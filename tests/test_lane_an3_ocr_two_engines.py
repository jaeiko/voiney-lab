"""Lane AN3, decision A: numbers on a scanned page, both engines, place by place.

Human decision 2026-10-16 (권고안 A):

1. Both engines read a scanned or photographed page; each number used in
   execution is compared with what the other engine read in the same place
   on the page. The same number and unit there settles it without a person
   (lane RV2's ``ocr_two_engines_agree``), whatever other numbers on the page
   the engines read differently.
2. The values read differently, or read by one engine only, are one card per
   page: the page's piece, the values, [모두 맞아요], and per value
   [engine A's value] [engine B's value] [직접 입력].
3. Under the limit of 12, a page's OCR card counts once.
4. Only values used in execution (step, sub-action, timer, amount, condition)
   are asked -- not numbers in notes, material or equipment names, or a
   document's metadata.

One engine's reading is never taken as confirmation, and no number is read
differently by a single character. Every OCR engine and model here is fake;
nothing is "live-tested".
"""

from __future__ import annotations

import json
import tempfile
import unittest
import unittest.mock
from collections import Counter
from pathlib import Path

from tests.test_lane_rv2_auto_resolve import TEXT_PAGE
from tests.test_lane_rv_review_pending import _RawModel, write_pages_pdf
from tests.test_protocol_ocr_providers import PNG
from voiney_lab.experiment_protocol_analysis import AUTO_RESOLVED_OCR_TWO_ENGINES
from voiney_lab.experiment_protocol_config import ProtocolPersistenceSettings
from voiney_lab.experiment_protocol_pdf import clear_protocol_pdf_cache, extract_protocol_pdf
from voiney_lab.experiment_protocol_store import initialize_protocol_store
from voiney_lab.protocol_catalog import ProtocolCatalog
from voiney_lab.protocol_ocr import (
    OcrPage,
    OcrResult,
    OcrValueReading,
    ProtocolOcrResultError,
    ocr_result_payload,
    validate_ocr_result,
)
from voiney_lab.protocol_ocr_providers import (
    CLOVA,
    GOOGLE,
    TEXT_LAYER,
    DualEngineOcrProvider,
    OcrWord,
    PlacedNumber,
    RecognizedPage,
    numeric_tokens,
    placed_numbers,
    readings_by_place,
)

SPIN = "2. Spin the tube at 4000 rpm for 5 min."
KEEP = "3. Keep the tube at 4 C."
SCAN = ["Section spin", "Buffer B 25 mM", SPIN, "Note: see reference 12.", KEEP]


def laid_out(lines: list[str], *, top: float = 100, char: float = 18) -> tuple[OcrWord, ...]:
    """Words as an engine gives them: one line of the page per string, each
    word's box ``char`` pixels a letter wide, a letter's width between words."""

    words = []
    for row, line in enumerate(lines):
        x, y = 10.0, top + row * 60
        parts = line.split(" ")
        for index, part in enumerate(parts):
            words.append(OcrWord(part, x, y, x + char * len(part), y + 30,
                                 "\n" if index == len(parts) - 1 else " "))
            x += char * (len(part) + 1)
    return tuple(words)


def number(token: str, x0: float, y0: float, x1: float, y1: float) -> PlacedNumber:
    return PlacedNumber(token, token, x0, y0, x1, y1)


class _WordsEngine:
    """A fake engine that reads ``lines`` with word boxes."""

    def __init__(self, name: str, lines: list[str] | None) -> None:
        self.name = name
        self.version = f"{name}-fake-1"
        self.lines = lines

    def recognize_page(self, png: bytes, *, page_number: int) -> RecognizedPage:
        if self.lines is None:
            raise RuntimeError("engine down")
        words = laid_out(self.lines)
        return RecognizedPage("".join(w.text + w.after for w in words).rstrip(), 0.9, words)


def provider(google: list[str] | None, clova: list[str] | None = None, *,
             engines: tuple[str, ...] = (CLOVA, GOOGLE)) -> DualEngineOcrProvider:
    made = {GOOGLE: _WordsEngine(GOOGLE, google), CLOVA: _WordsEngine(CLOVA, clova)}
    return DualEngineOcrProvider(tuple(made[name] for name in engines),
                                 render=lambda *a, **k: PNG)


class PlacedNumbersTests(unittest.TestCase):
    def test_a_number_with_its_unit_takes_the_box_of_both_words(self) -> None:
        found = placed_numbers(laid_out(["Spin at 4000 rpm for 5 min"]))
        self.assertEqual([item.token for item in found], ["4000rpm", "5min"])
        spin = found[0]
        self.assertEqual(spin.text, "4000 rpm")
        self.assertEqual((spin.x0, spin.x1), (10 + 18 * 8, 10 + 18 * 8 + 18 * 8))

    def test_a_page_s_placed_numbers_are_its_numeric_tokens(self) -> None:
        for lines in (SCAN, ["Mix 1:10, then 12,000 xg at 4 °C", "Add 500 uL (2 x) H2SO4 1.5 mL"]):
            with self.subTest(lines=lines):
                words = laid_out(lines)
                text = "".join(word.text + word.after for word in words)
                self.assertEqual(Counter(item.token for item in placed_numbers(words)),
                                 numeric_tokens(text))

    def test_a_unit_an_engine_runs_into_the_number_is_part_of_it(self) -> None:
        words = (OcrWord("at", 0, 0, 20, 30, " "), OcrWord("65", 30, 0, 50, 30, ""),
                 OcrWord("°C", 50, 0, 70, 30, "\n"))
        (found,) = placed_numbers(words)
        self.assertEqual((found.token, found.text, found.x0, found.x1), ("65°c", "65°C", 30, 70))


class ReadingsByPlaceTests(unittest.TestCase):
    def test_the_same_number_in_the_same_place_is_read_alike(self) -> None:
        (reading,) = readings_by_place((number("4000rpm", 100, 10, 260, 40),),
                                       (number("4000rpm", 102, 11, 258, 41),))
        self.assertEqual((reading.token, reading.other_token), ("4000rpm", "4000rpm"))

    def test_4000_and_400_in_the_same_place_differ(self) -> None:
        (reading,) = readings_by_place((number("4000rpm", 100, 10, 260, 40),),
                                       (number("400rpm", 100, 10, 240, 40),))
        self.assertEqual((reading.other_token, reading.other_text), ("400rpm", "400rpm"))

    def test_nothing_read_in_that_place_is_one_engine_s_number(self) -> None:
        (reading,) = readings_by_place((number("5min", 100, 10, 160, 40),),
                                       (number("5min", 100, 300, 160, 330),))
        self.assertIsNone(reading.other_token)
        self.assertIsNone(reading.other_text)

    def test_a_unit_read_on_one_side_only_differs(self) -> None:
        (reading,) = readings_by_place((number("37°c", 100, 10, 180, 40),),
                                       (number("37", 100, 10, 140, 40),))
        self.assertEqual(reading.other_token, "37")

    def test_a_decimal_point_and_a_thousands_comma_differ(self) -> None:
        (reading,) = readings_by_place((number("12,000×g", 100, 10, 220, 40),),
                                       (number("12.000×g", 100, 10, 220, 40),))
        self.assertEqual(reading.other_token, "12.000×g")

    def test_boxes_overlapping_half_the_smaller_box_are_one_place(self) -> None:
        (inside,) = readings_by_place((number("5", 100, 10, 140, 40),),
                                      (number("5", 120, 10, 160, 40),))  # 50%
        self.assertEqual(inside.other_token, "5")
        (apart,) = readings_by_place((number("5", 100, 10, 140, 40),),
                                     (number("5", 125, 10, 165, 40),))  # 37.5%
        self.assertIsNone(apart.other_token)

    def test_each_number_pairs_once_the_largest_overlap_first(self) -> None:
        first, second = readings_by_place(
            (number("1", 100, 10, 140, 40), number("2", 130, 10, 170, 40)),
            (number("2", 128, 10, 168, 40),))
        self.assertIsNone(first.other_token)
        self.assertEqual(second.other_token, "2")


class DualEngineReadingsTests(unittest.TestCase):
    def recognize(self, ocr: DualEngineOcrProvider, pdf: Path) -> OcrResult:
        extraction = extract_protocol_pdf(pdf)
        return ocr.recognize(pdf, source_sha256=extraction.sha256, page_count=extraction.page_count)

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.pdf = Path(self.temp.name) / "scan.pdf"
        write_pages_pdf(self.pdf, [TEXT_PAGE, []], title="Protocol Delta")
        clear_protocol_pdf_cache()

    def tearDown(self) -> None:
        clear_protocol_pdf_cache()
        self.temp.cleanup()

    def test_the_page_keeps_the_other_engine_and_its_number_in_each_place(self) -> None:
        other = [line.replace("4000 rpm", "400 rpm") for line in SCAN]
        result = self.recognize(provider(SCAN, other), self.pdf)
        page = result.pages[1]
        self.assertEqual((page.provider, page.other_provider), (GOOGLE, CLOVA))
        self.assertEqual(page.other_provider_version, "clova-fake-1")
        self.assertIn("400 rpm", page.other_text)
        readings = {reading.token: reading.other_token for reading in page.value_readings}
        self.assertEqual(readings, {"25mm": "25mm", "2": "2", "4000rpm": "400rpm", "5min": "5min",
                                    "12": "12", "3": "3", "4": "4"})
        self.assertTrue(page.numeric_review_required)
        self.assertIsNone(result.pages[0].value_readings)  # the text-layer page
        payload = ocr_result_payload(validate_ocr_result(
            result, expected_sha256=result.source_sha256, expected_page_count=2))
        stored = payload["pages"][1]
        self.assertEqual(stored["other_provider"], CLOVA)
        self.assertEqual(stored["value_readings"][2]["token"], "4000rpm")
        self.assertEqual(stored["value_readings"][2]["other_text"], "400 rpm")
        self.assertEqual(len(stored["value_readings"][2]["box"]), 4)

    def test_engines_without_word_boxes_are_not_compared_by_place(self) -> None:
        engine = _WordsEngine(GOOGLE, SCAN)
        engine.recognize_page = lambda png, *, page_number: RecognizedPage("\n".join(SCAN), 0.9)
        clova = _WordsEngine(CLOVA, SCAN)
        result = self.recognize(DualEngineOcrProvider((clova, engine), render=lambda *a, **k: PNG),
                                self.pdf)
        self.assertIsNone(result.pages[1].value_readings)
        self.assertEqual(result.pages[1].other_provider, CLOVA)

    def test_one_engine_reading_the_page_keeps_no_other_reading(self) -> None:
        for ocr in (provider(SCAN, None), provider(SCAN, engines=(GOOGLE,))):
            with self.subTest(engines=ocr.engine_names):
                page = self.recognize(ocr, self.pdf).pages[1]
                self.assertIsNone(page.other_provider)
                self.assertIsNone(page.other_text)
                self.assertIsNone(page.value_readings)

    def test_unsound_readings_are_refused(self) -> None:
        good = OcrValueReading("5min", "5 min", 1, 2, 3, 4, "5min", "5 min")
        for reading in (
            OcrValueReading("5min", "5 min", 1, 2, 3, 4, "5min", None),
            OcrValueReading("x" * 65, "5 min", 1, 2, 3, 4),
            OcrValueReading("5min", "5 min", -1, 2, 3, 4),
            OcrValueReading("5min", "5 min", 3, 2, 1, 4),
        ):
            with self.subTest(reading=reading):
                result = OcrResult("0" * 64, "google", "v1", (
                    OcrPage(1, "a", provider=GOOGLE, value_readings=(good, reading)),))
                with self.assertRaises(ProtocolOcrResultError):
                    validate_ocr_result(result, expected_sha256="0" * 64, expected_page_count=1)
        result = OcrResult("0" * 64, "google", "v1", (
            OcrPage(1, "a", provider=GOOGLE, value_readings=(good,)),))
        self.assertEqual(validate_ocr_result(
            result, expected_sha256="0" * 64, expected_page_count=1).pages[0].value_readings, (good,))


def scan_answer(sha256: str, *, scan: list[str] = SCAN, steps: list[tuple[str, str]] | None = None,
                page: int = 2) -> str:
    """The analysis of the delta document whose page ``page`` is ``scan``:
    its material and note are on that page beside the steps."""

    ev = lambda excerpt, number: {"source_page_number": number, "source_excerpt": excerpt}
    steps = steps if steps is not None else [("s2", SPIN), ("s3", KEEP)]
    spin_steps = []
    for step_id, words in steps:
        step = {"step_id": step_id, "source_label": words.split(".", 1)[0],
                "instruction_source_text": words, "evidence": ev(words, page)}
        if words == SPIN:
            step["notes"] = [{"statement_id": "ref", "source_text": "Note: see reference 12.",
                              "evidence": ev("Note: see reference 12.", page)}]
        spin_steps.append(step)
    materials = ([{"material_id": "bufb", "name_source_text": "Buffer B 25 mM",
                   "evidence": ev("Buffer B 25 mM", page)}] if "Buffer B 25 mM" in scan else [])
    return json.dumps({
        "analysis_schema_version": 1, "pdf_sha256": sha256,
        "capability_policy_id": "p1-conservative",
        "protocol": {
            "protocol_id": "delta",
            "metadata": {"title": "Protocol Delta", "original_language": "en",
                         "evidence": ev("Protocol Delta", 1)},
            "before_start": [], "materials": materials, "equipment": [],
            "sections": [
                {"section_id": "mix", "title_source_text": "Section mix",
                 "evidence": ev("Section mix", 1),
                 "steps": [{"step_id": "s1", "source_label": "1",
                            "instruction_source_text": "1. Add 2 mL of buffer A.",
                            "evidence": ev("1. Add 2 mL of buffer A.", 1)}]},
                {"section_id": "spin", "title_source_text": "Section spin",
                 "evidence": ev("Section spin", page), "steps": spin_steps},
            ],
            "constructs": [], "description": None,
        },
    })


class _ScanCase(unittest.TestCase):
    PAGES: list[list[str]] = [TEXT_PAGE, []]

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        clear_protocol_pdf_cache()
        self.store = initialize_protocol_store(ProtocolPersistenceSettings(True, root / "catalog"))
        self.catalog = ProtocolCatalog(self.store)
        self.pdf = root / "delta.pdf"
        write_pages_pdf(self.pdf, self.PAGES, title="Protocol Delta")
        self.sha256 = extract_protocol_pdf(self.pdf).sha256
        self.protocol_id = self.catalog.register(
            self.pdf, source_filename="delta.pdf", media_type="application/pdf",
        ).entry.protocol_id

    def tearDown(self) -> None:
        self.store.close()
        clear_protocol_pdf_cache()
        self.temp.cleanup()

    def analyze(self, ocr, answer: str | None = None):
        self.catalog.run_ocr(self.protocol_id, ocr, ocr_id="ocr-" + "a" * 32)
        self.catalog.request_analysis(self.protocol_id, "analysis-" + "a" * 32)
        model = _RawModel(answer or scan_answer(self.sha256))
        try:
            return self.catalog.analyze(self.protocol_id, model, analysis_id="analysis-" + "a" * 32)
        except Exception as exc:  # noqa: BLE001 - the caller looks at it
            return exc

    def events(self, event_type: str) -> list[dict]:
        return [event.payload for event in self.store.list_events(self.protocol_id)
                if event.event_type == event_type]

    def cards(self) -> list[dict]:
        return self.catalog.review_draft(self.protocol_id)["cards"]


class SettledByPlaceTests(_ScanCase):
    def test_values_both_read_alike_run_though_other_numbers_on_the_page_differ(self) -> None:
        # CLOVA reads the note's reference number differently; the page is
        # marked, but every value the steps use was read alike in its place.
        other = [line.replace("reference 12", "reference 13") for line in SCAN]
        entry = self.analyze(provider(SCAN, other))
        self.assertTrue(entry.available_for_execution)
        (completed,) = self.events("protocol_ocr_completed")
        self.assertTrue(completed["pages"][1]["numeric_review_required"])
        (ready,) = self.events("protocol_analysis_ready")
        settled = [item for item in ready["auto_resolved"] if item["kind"] == "ocr_value"]
        self.assertEqual([(item["page"], item["token"]) for item in settled],
                         [(2, "4000rpm"), (2, "5min"), (2, "4")])
        self.assertEqual({item["reason"] for item in settled}, {AUTO_RESOLVED_OCR_TWO_ENGINES})
        self.assertEqual(self.cards(), [])


class OnePageOneCardTests(_ScanCase):
    def test_values_read_differently_are_one_card_for_their_page(self) -> None:
        other = [line.replace("4000 rpm", "400 rpm").replace("4 C", "8 C") for line in SCAN]
        entry = self.analyze(provider(SCAN, other))
        self.assertFalse(entry.available_for_execution)
        self.assertEqual(entry.review_pending["needs_review_count"], 1)
        self.assertEqual(entry.review_pending["by_reason"], {"ocr_value": 1})
        self.assertEqual(entry.review_pending["message_ko"], "검토 대기 · 확인 필요 1건(OCR 값 확인 1쪽)")
        (card,) = self.cards()
        self.assertEqual((card["card_id"], card["type"], card["page"]), ("ocr_page:2", "ocr_page", 2))
        self.assertEqual(card["progress_ko"], "1 / 1")
        self.assertIn("2쪽", card["question_ko"])
        self.assertEqual([choice["label_ko"] for choice in card["choices"]], ["모두 맞아요"])
        self.assertEqual(card["choices"][0]["request"], {"item_id": "ocr_page:2", "action": "confirm"})
        # Round 2, decision ②: 2 of the page's 7 numbers differ (29%), so the
        # value both engines read alike is asked too.
        spin, rest, keep = card["values"]
        self.assertEqual((rest["item_id"], rest["reason_code"]),
                         ("ocr_value:2:5min", "ocr_page_differs_widely"))
        self.assertEqual((spin["item_id"], spin["shown"]), ("ocr_value:2:4000rpm", "4000 rpm"))
        self.assertEqual([(reading["engine"], reading["text"]) for reading in spin["readings"]],
                         [("Google", "4000 rpm"), ("CLOVA", "400 rpm")])
        self.assertEqual(spin["reason_ko"], "두 OCR 엔진이 이 값을 다르게 읽음")
        self.assertEqual([(choice["label_ko"], choice.get("engine"), choice["needs"])
                          for choice in spin["choices"]],
                         [("4000 rpm", "Google", None), ("400 rpm", "CLOVA", None),
                          ("직접 입력", None, "text")])
        self.assertEqual([choice["request"] for choice in spin["choices"]], [
            {"item_id": "ocr_value:2:4000rpm", "action": "confirm"},
            {"item_id": "ocr_value:2:4000rpm", "action": "fix", "text": "400 rpm"},
            {"item_id": "ocr_value:2:4000rpm", "action": "fix"},
        ])
        self.assertEqual([reading["text"] for reading in keep["readings"]], ["4", "8"])
        self.assertEqual(spin["linked_items"], ["step:s2"])

    def test_all_correct_settles_every_value_of_the_page_and_the_version_runs(self) -> None:
        other = [line.replace("4000 rpm", "400 rpm").replace("4 C", "8 C") for line in SCAN]
        self.analyze(provider(SCAN, other))
        (card,) = self.cards()
        result = self.catalog.record_review_decision(self.protocol_id, card["choices"][0]["request"])
        self.assertTrue(result["registrable"])
        self.assertTrue(result["cards"][0]["decided"])
        decisions = self.events("protocol_review_decision_recorded")
        self.assertEqual([(d["item_id"], d["action"], d["page_card"]) for d in decisions], [
            ("ocr_value:2:4000rpm", "confirm", "ocr_page:2"),
            ("ocr_value:2:5min", "confirm", "ocr_page:2"),  # round 2, decision ②
            ("ocr_value:2:4", "confirm", "ocr_page:2")])
        self.catalog.register_review(self.protocol_id, {"reviewer_name": "김재준"})
        self.assertEqual(self.catalog.load_executable_fixture(self.protocol_id).revision_id,
                         "pdf-1-review-1")

    def test_all_correct_leaves_a_value_already_decided_as_it_was(self) -> None:
        other = [line.replace("4000 rpm", "400 rpm").replace("4 C", "8 C") for line in SCAN]
        self.analyze(provider(SCAN, other))
        (card,) = self.cards()
        keep = card["values"][2]  # round 2, decision ②: 5 min is asked too, in between
        self.catalog.record_review_decision(self.protocol_id, keep["choices"][1]["request"])
        self.catalog.record_review_decision(self.protocol_id, card["choices"][0]["request"])
        decisions = self.events("protocol_review_decision_recorded")
        self.assertEqual([(d["item_id"], d["action"]) for d in decisions], [
            ("ocr_value:2:4", "fix"), ("ocr_value:2:4000rpm", "confirm"),
            ("ocr_value:2:5min", "confirm")])

    def test_the_other_engine_s_value_asks_the_step_sentence_again(self) -> None:
        other = [line.replace("4000 rpm", "400 rpm") for line in SCAN]
        self.analyze(provider(SCAN, other))
        (card,) = self.cards()
        (spin,) = card["values"]
        result = self.catalog.record_review_decision(self.protocol_id, spin["choices"][1]["request"])
        self.assertFalse(result["registrable"])
        changed = [item for item in result["cards"] if item["type"] == "value_changed"]
        self.assertEqual([item["item_id"] for item in changed], ["step:s2"])
        self.assertEqual(changed[0]["proposal"], "2. Spin the tube at 400 rpm for 5 min.")

    def test_a_value_one_engine_read_shows_that_engine_s_reading_only(self) -> None:
        other = [line.replace(" for 5 min.", "") for line in SCAN]
        self.analyze(provider(SCAN, other))
        (card,) = self.cards()
        (value,) = card["values"]
        self.assertEqual(value["item_id"], "ocr_value:2:5min")
        self.assertEqual(value["reason_ko"], "OCR 엔진 하나만 이 값을 읽음")
        self.assertEqual([(reading["engine"], reading["text"]) for reading in value["readings"]],
                         [("Google", "5 min")])
        self.assertEqual([choice["label_ko"] for choice in value["choices"]], ["5 min", "직접 입력"])

    def test_a_page_one_engine_read_asks_its_values_on_one_card(self) -> None:
        entry = self.analyze(provider(SCAN, engines=(GOOGLE,)))
        self.assertFalse(entry.available_for_execution)
        (card,) = self.cards()
        self.assertEqual([value["item_id"] for value in card["values"]],
                         ["ocr_value:2:4000rpm", "ocr_value:2:5min", "ocr_value:2:4"])
        self.assertEqual({value["reason_ko"] for value in card["values"]}, {"OCR 엔진 하나만 읽은 쪽"})
        self.assertEqual([choice["label_ko"] for choice in card["values"][0]["choices"]],
                         ["4000 rpm 맞아요", "직접 입력"])
        self.catalog.record_review_decision(self.protocol_id, card["choices"][0]["request"])
        self.assertTrue(self.catalog.review_draft(self.protocol_id)["registrable"])


class ExecutionValuesOnlyTests(_ScanCase):
    def test_numbers_in_a_material_name_or_a_note_are_never_asked(self) -> None:
        self.analyze(provider(SCAN, engines=(GOOGLE,)))
        _, _, flags, _ = self.catalog.review_pending_analysis(self.protocol_id)
        self.assertEqual(sorted(flag.item_id for flag in flags),
                         ["ocr_value:2:4", "ocr_value:2:4000rpm", "ocr_value:2:5min"])
        draft = self.catalog.review_draft(self.protocol_id)
        self.assertNotIn("material:bufb", draft["needs_review"])


MANY = ["Section spin", "2. Mix 1 mL 2 mL 3 mL 4 mL 5 mL 6 mL 7 mL.",
        "3. Add 8 mL 9 mL 10 mL 11 mL 12 mL 13 mL."]


class AnOcrPageCountsOnceTests(_ScanCase):
    PAGES = [TEXT_PAGE, [], []]

    def answer(self) -> str:
        payload = json.loads(scan_answer(self.sha256, scan=MANY, steps=[
            ("s2", MANY[1]), ("s3", MANY[2])]))
        payload["protocol"]["sections"].append({
            "section_id": "more", "title_source_text": "Section spin",
            "evidence": {"source_page_number": 3, "source_excerpt": "Section spin"},
            "steps": [{"step_id": "s4", "source_label": "4",
                       "instruction_source_text": "4. Mix 1 mL 2 mL 3 mL 4 mL 5 mL 6 mL 7 mL.",
                       "evidence": {"source_page_number": 3,
                                    "source_excerpt": "4. Mix 1 mL 2 mL 3 mL 4 mL 5 mL 6 mL 7 mL."}}],
        })
        return json.dumps(payload)

    def test_thirteen_values_on_one_page_and_seven_on_another_are_two_cards(self) -> None:
        third = ["Section spin", "4. Mix 1 mL 2 mL 3 mL 4 mL 5 mL 6 mL 7 mL."]

        class _Pages(_WordsEngine):
            def recognize_page(self, png: bytes, *, page_number: int) -> RecognizedPage:
                self.lines = MANY if page_number == 2 else third
                return super().recognize_page(png, page_number=page_number)

        ocr = DualEngineOcrProvider((_Pages(GOOGLE, MANY),), render=lambda *a, **k: PNG)
        entry = self.analyze(ocr, self.answer())
        self.assertEqual(entry.analysis_status, "review_pending")
        self.assertEqual(self.events("protocol_analysis_auto_reanalysis"), [])
        self.assertEqual(entry.review_pending["needs_review_count"], 2)
        self.assertEqual(entry.review_pending["message_ko"], "검토 대기 · 확인 필요 2건(OCR 값 확인 2쪽)")
        cards = self.cards()
        self.assertEqual([(card["card_id"], len(card["values"])) for card in cards],
                         [("ocr_page:2", 13), ("ocr_page:3", 7)])
        self.assertEqual([card["progress_ko"] for card in cards], ["1 / 2", "2 / 2"])

    def test_the_guidance_counts_ocr_pages_when_they_are_most_of_the_items(self) -> None:
        third = ["Section spin", "4. Mix 1 mL 2 mL 3 mL 4 mL 5 mL 6 mL 7 mL."]

        class _Pages(_WordsEngine):
            def recognize_page(self, png: bytes, *, page_number: int) -> RecognizedPage:
                self.lines = MANY if page_number == 2 else third
                return super().recognize_page(png, page_number=page_number)

        ocr = DualEngineOcrProvider((_Pages(GOOGLE, MANY),), render=lambda *a, **k: PNG)
        self.catalog.run_ocr(self.protocol_id, ocr, ocr_id="ocr-" + "a" * 32)
        self.catalog.request_analysis(self.protocol_id, "analysis-" + "a" * 32)
        with unittest.mock.patch.dict("os.environ", {"VOINEY_LAB_REVIEW_MAX_ITEMS": "1"}):
            with self.assertRaises(Exception) as caught:
                self.catalog.analyze(self.protocol_id, _RawModel(self.answer(), self.answer()),
                                     analysis_id="analysis-" + "a" * 32)
        self.assertEqual(getattr(caught.exception, "code", None),
                         "protocol_analysis_too_many_review_items")
        (failed,) = self.events("protocol_analysis_failed")
        self.assertEqual(failed["review_items"]["by_reason"], {"ocr_value": 2})
        message = self.catalog.pipeline_status(self.protocol_id)["message"]
        self.assertIn("확인할 항목이 2건이라(카드는 1건까지)", message)
        self.assertIn("그중 2건은 OCR 엔진 하나만 읽었거나 두 엔진이 다르게 읽은 숫자가 있는 쪽이에요"
                      "(쪽마다 한 건)", message)
        self.assertIn("VOINEY_LAB_OCR_PROVIDERS=clova,google", message)


class ResultsStoredBeforeTests(_ScanCase):
    class _Stored:
        """An OCR result stored before lane AN3: one engine's text and the page mark."""

        engine_names = (CLOVA, GOOGLE)

        def __init__(self, differ: bool) -> None:
            self.differ = differ

        def recognize(self, source_pdf, *, source_sha256: str, page_count: int) -> OcrResult:
            layer = extract_protocol_pdf(Path(source_pdf)).pages[0].text
            return OcrResult(source_sha256=source_sha256, provider="google",
                             provider_version="fake-1", pages=(
                                 OcrPage(1, layer, provider=TEXT_LAYER),
                                 OcrPage(2, "\n".join(SCAN), provider=GOOGLE,
                                         numeric_review_required=self.differ)))

    def test_a_page_both_read_alike_still_settles_its_values(self) -> None:
        self.assertTrue(self.analyze(self._Stored(differ=False)).available_for_execution)

    def test_a_page_marked_different_is_one_card_naming_the_page(self) -> None:
        self.analyze(self._Stored(differ=True))
        (card,) = self.cards()
        self.assertEqual(card["type"], "ocr_page")
        self.assertEqual({value["reason_ko"] for value in card["values"]},
                         {"두 OCR 엔진이 이 쪽의 숫자를 다르게 읽음"})
        self.assertEqual([value["readings"] for value in card["values"]], [[], [], []])


if __name__ == "__main__":
    unittest.main()
