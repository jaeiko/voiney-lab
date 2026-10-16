"""What the server settles without asking a person (lane RV2, decision b).

Human decision 2026-10-16: fewer questions, not looser checks. The server
settles an item itself only on deterministic grounds, and records why:

* A number OCR read runs only when both engines read its page and their
  numbers and units were the same (the comparison lanes P2/P3 made), and the
  item's quote passed the source check. Where the engines' numbers differed,
  or only one engine read the page, each value an executed item states there
  is a card -- the OCR keeps only the chosen engine's text and a per-page
  mark, so a page that differs anywhere asks every value on it.
* A refused quote whose source lines narrow to one place gets the server's
  proposal; a person only says [맞아요].
* A step two pieces of a long document read in the same words is kept once.

Every OCR engine and model here is fake.
"""

from __future__ import annotations

import json
import tempfile
from collections import Counter
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tests.test_lane_rv_review_pending import (
    MADE_UP,
    _CatalogCase,
    _RawModel,
    write_pages_pdf,
)
from tests.test_protocol_ocr_providers import PNG, _FakeEngine
from voiney_lab.experiment_protocol_analysis import (
    AUTO_RESOLVED_OCR_TWO_ENGINES,
    AUTO_RESOLVED_SAME_STEP,
    AnalysisPiece,
)
from voiney_lab.experiment_protocol_config import ProtocolPersistenceSettings
from voiney_lab.experiment_protocol_pdf import clear_protocol_pdf_cache, extract_protocol_pdf
from voiney_lab.experiment_protocol_store import initialize_protocol_store
from voiney_lab.protocol_catalog import ProtocolCatalog, ocr_value_tokens, pages_read_by_both
from voiney_lab.protocol_ocr import OcrPage, OcrResult, OcrValueBox
from voiney_lab.protocol_ocr_providers import (
    CLOVA,
    GOOGLE,
    TEXT_LAYER,
    DualEngineOcrProvider,
    RecognizedPage,
    numeric_tokens,
)

TEXT_PAGE = ["Protocol Delta", "Section mix", "1. Add 2 mL of buffer A."]
SCAN_TEXT = "Section spin\n2. Spin the tube at 4000 rpm for 5 min.\n3. Keep the tube at 4 C."


class _TwoEngineOcr:
    """A provider that says it called both engines, with the page-2 reading given."""

    def __init__(self, *, differ: bool = False, engines=(CLOVA, GOOGLE),
                 warnings: tuple[str, ...] = (), text: str = SCAN_TEXT) -> None:
        self.engine_names = engines
        self.differ = differ
        self.warnings = warnings
        self.text = text

    def recognize(self, source_pdf, *, source_sha256: str, page_count: int) -> OcrResult:
        layer = extract_protocol_pdf(Path(source_pdf)).pages[0].text
        return OcrResult(
            source_sha256=source_sha256, provider="google", provider_version="fake-1",
            pages=(
                OcrPage(1, layer, provider=TEXT_LAYER),
                OcrPage(2, self.text, confidence=0.93, provider=GOOGLE,
                        numeric_review_required=self.differ, render_dpi=300,
                        value_boxes=(OcrValueBox("4000 rpm", 400, 300, 560, 340),
                                     OcrValueBox("5 min", 600, 300, 700, 340),
                                     OcrValueBox("4 C", 500, 360, 560, 400))),
            ),
            warnings=self.warnings,
        )


def delta_answer(sha256: str, *, step_two: str = "2. Spin the tube at 4000 rpm for 5 min.",
                 step_three_quote: str = "3. Keep the tube at 4 C.") -> str:
    ev = lambda excerpt, page: {"source_page_number": page, "source_excerpt": excerpt}
    return json.dumps({
        "analysis_schema_version": 1, "pdf_sha256": sha256,
        "capability_policy_id": "p1-conservative",
        "protocol": {
            "protocol_id": "delta",
            "metadata": {"title": "Protocol Delta", "original_language": "en",
                         "evidence": ev("Protocol Delta", 1)},
            "before_start": [], "materials": [], "equipment": [],
            "sections": [
                {"section_id": "mix", "title_source_text": "Section mix",
                 "evidence": ev("Section mix", 1),
                 "steps": [{"step_id": "s1", "source_label": "1",
                            "instruction_source_text": "1. Add 2 mL of buffer A.",
                            "evidence": ev("1. Add 2 mL of buffer A.", 1)}]},
                {"section_id": "spin", "title_source_text": "Section spin",
                 "evidence": ev("Section spin", 2),
                 "steps": [
                     {"step_id": "s2", "source_label": "2", "instruction_source_text": step_two,
                      "evidence": ev(step_two, 2)},
                     {"step_id": "s3", "source_label": "3",
                      "instruction_source_text": "3. Keep the tube at 4 C.",
                      "evidence": ev(step_three_quote, 2)},
                 ]},
            ],
            "constructs": [], "description": None,
        },
    })


class _OcrCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        clear_protocol_pdf_cache()
        self.settings = ProtocolPersistenceSettings(True, root / "catalog")
        self.store = initialize_protocol_store(self.settings)
        self.catalog = ProtocolCatalog(self.store)
        self.pdf = root / "delta.pdf"
        write_pages_pdf(self.pdf, [TEXT_PAGE, []], title="Protocol Delta")
        self.sha256 = extract_protocol_pdf(self.pdf).sha256
        self.protocol_id = self.catalog.register(
            self.pdf, source_filename="delta.pdf", media_type="application/pdf",
        ).entry.protocol_id

    def tearDown(self) -> None:
        self.store.close()
        clear_protocol_pdf_cache()
        self.temp.cleanup()

    def analyze_after_ocr(self, provider, answer: str | None = None):
        self.catalog.run_ocr(self.protocol_id, provider, ocr_id="ocr-" + "a" * 32)
        self.catalog.request_analysis(self.protocol_id, "analysis-" + "a" * 32)
        model = _RawModel(answer or delta_answer(self.sha256))
        try:
            return self.catalog.analyze(self.protocol_id, model, analysis_id="analysis-" + "a" * 32)
        except Exception as exc:  # noqa: BLE001 - the caller looks at it
            return exc

    def events(self, event_type: str) -> list[dict]:
        return [event.payload for event in self.store.list_events(self.protocol_id)
                if event.event_type == event_type]


class OcrValuesTests(_OcrCase):
    def test_both_engines_alike_settles_the_values_and_the_analysis_runs(self) -> None:
        entry = self.analyze_after_ocr(_TwoEngineOcr())
        self.assertTrue(entry.available_for_execution)
        self.assertIsNone(entry.review_pending)
        (completed,) = self.events("protocol_ocr_completed")
        self.assertEqual((completed["engines"], completed["pages_read_by_both"]),
                         ([CLOVA, GOOGLE], [2]))
        (ready,) = self.events("protocol_analysis_ready")
        settled = ready["auto_resolved"]
        self.assertEqual([(entry["page"], entry["token"]) for entry in settled],
                         [(2, "4000rpm"), (2, "5min"), (2, "4")])
        self.assertEqual({entry["reason"] for entry in settled}, {AUTO_RESOLVED_OCR_TWO_ENGINES})
        self.assertEqual(settled[0]["items"], ["step:s2"])
        self.assertEqual(self.catalog.review_draft(self.protocol_id)["cards"], [])

    def test_engines_that_read_the_page_differently_ask_each_value_on_it(self) -> None:
        entry = self.analyze_after_ocr(_TwoEngineOcr(differ=True))
        self.assertFalse(entry.available_for_execution)
        self.assertEqual(entry.review_pending["needs_review_count"], 3)
        self.assertEqual(entry.review_pending["by_reason"], {"ocr_value": 3})
        draft = self.catalog.review_draft(self.protocol_id)
        cards = draft["cards"]
        self.assertEqual([card["item_id"] for card in cards],
                         ["ocr_value:2:4000rpm", "ocr_value:2:5min", "ocr_value:2:4"])
        card = cards[0]
        self.assertEqual((card["type"], card["shown"]), ("ocr_value", "4000 rpm"))
        self.assertEqual(card["reasons_ko"], ["두 OCR 엔진이 이 쪽의 숫자를 다르게 읽음"])
        self.assertEqual([(choice["label_ko"], choice["needs"]) for choice in card["choices"]],
                         [("4000 rpm 맞아요", None), ("직접 입력", "text")])
        self.assertEqual(card["source"]["value_boxes"], [0])
        self.assertEqual(card["source"]["lines"], {"page": 2, "first_line": 2, "last_line": 2})
        item = next(item for item in draft["items"] if item["item_id"] == "ocr_value:2:4000rpm")
        self.assertEqual(item["linked_items"], ["step:s2"])
        # The steps themselves passed and are not cards of their own.
        self.assertNotIn("step:s2", draft["needs_review"])

    def test_one_engine_reading_the_page_is_not_confirmation(self) -> None:
        for provider in (
            _TwoEngineOcr(engines=(GOOGLE,)),
            _TwoEngineOcr(warnings=("Page 2: CLOVA failed; the other engine's text was used.",)),
        ):
            with self.subTest(engines=provider.engine_names, warnings=provider.warnings):
                self.tearDown()
                self.setUp()
                entry = self.analyze_after_ocr(provider)
                self.assertFalse(entry.available_for_execution)
                _, _, flags, _ = self.catalog.review_pending_analysis(self.protocol_id)
                self.assertEqual({flag.reason_code for flag in flags}, {"ocr_one_engine"})
                self.assertEqual(flags[0].reason_ko, "OCR 엔진 하나만 읽은 쪽")

    def test_a_provider_that_names_no_engines_reads_as_one_engine(self) -> None:
        provider = _TwoEngineOcr()
        del provider.engine_names
        entry = self.analyze_after_ocr(provider)
        self.assertEqual(entry.review_pending["by_reason"], {"ocr_value": 3})

    def test_numbers_on_a_text_layer_page_are_never_ocr_items(self) -> None:
        self.analyze_after_ocr(_TwoEngineOcr(differ=True))
        _, _, flags, _ = self.catalog.review_pending_analysis(self.protocol_id)
        self.assertFalse(any("2ml" in flag.item_id for flag in flags))

    def test_a_refused_quote_on_an_ocr_page_keeps_its_own_card_only(self) -> None:
        answer = delta_answer(self.sha256, step_three_quote="3. Keep the tube at 8 C.")
        self.analyze_after_ocr(_TwoEngineOcr(), answer)
        draft = self.catalog.review_draft(self.protocol_id)
        self.assertEqual(draft["needs_review"], ["step:s3"])
        self.assertEqual(draft["cards"][0]["reasons_ko"], ["OCR 흐림"])

    def test_confirming_the_values_registers_a_version_that_runs(self) -> None:
        self.analyze_after_ocr(_TwoEngineOcr(differ=True))
        for card in self.catalog.review_draft(self.protocol_id)["cards"]:
            self.catalog.record_review_decision(self.protocol_id, card["choices"][0]["request"])
        result = self.catalog.register_review(self.protocol_id, {"reviewer_name": "김재준"})
        self.assertEqual(result["review_version"]["confirmed_count"], 3)
        fixture = self.catalog.load_executable_fixture(self.protocol_id)
        self.assertEqual(fixture.revision_id, "pdf-1-review-1")

    def test_the_engines_failed_on_a_page_are_read_from_the_provider_s_warnings(self) -> None:
        result = OcrResult(
            source_sha256="0" * 64, provider="clova-google", provider_version="x",
            pages=(OcrPage(1, "a", provider=TEXT_LAYER), OcrPage(2, "b", provider=CLOVA),
                   OcrPage(3, "c", provider=GOOGLE)),
            warnings=("Page 3: Google Vision failed; the other engine's text was used.",))
        self.assertEqual(pages_read_by_both(result, (CLOVA, GOOGLE)), [2])
        self.assertEqual(pages_read_by_both(result, (GOOGLE,)), [])

    def test_value_tokens_are_the_ocr_comparison_s_tokens(self) -> None:
        self.assertEqual(ocr_value_tokens("2. Spin at 4000 rpm for 5 min."),
                         [("4000rpm", "4000 rpm"), ("5min", "5 min")])
        self.assertEqual(ocr_value_tokens("3 Wash with 500 uL", label="3"), [("500µl", "500 uL")])
        for text in ("Add H2SO4 and 1.5 mL", "Spin at 12,000 ×g, 4 °C", "Mix 1:10 for 30 s"):
            with self.subTest(text=text):
                self.assertEqual(Counter(token for token, _ in ocr_value_tokens(text)),
                                 numeric_tokens(text))


class RealDualEngineProviderTests(_OcrCase):
    """Production boundary: the provider the server builds, two fake engines."""

    def provider(self, clova_text: str, google_text: str) -> DualEngineOcrProvider:
        clova, google = _FakeEngine(CLOVA, clova_text), _FakeEngine(GOOGLE, google_text)
        clova.recognize_page = lambda png, *, page_number: RecognizedPage(clova_text, 0.9)
        google.recognize_page = lambda png, *, page_number: RecognizedPage(google_text, 0.9)
        return DualEngineOcrProvider((clova, google), render=lambda *a, **k: PNG)

    def test_two_engines_that_read_the_same_numbers_settle_them(self) -> None:
        entry = self.analyze_after_ocr(self.provider(SCAN_TEXT, SCAN_TEXT))
        self.assertTrue(entry.available_for_execution)

    def test_two_engines_that_read_4000_and_400_ask_a_person(self) -> None:
        # Google's text is the one used for an English page; CLOVA read 400.
        other = SCAN_TEXT.replace("4000 rpm", "400 rpm")
        entry = self.analyze_after_ocr(self.provider(other, SCAN_TEXT))
        self.assertFalse(entry.available_for_execution)
        _, _, flags, _ = self.catalog.review_pending_analysis(self.protocol_id)
        self.assertEqual({flag.reason_code for flag in flags}, {"ocr_engines_differ"})
        self.assertIn("ocr_value:2:4000rpm", [flag.item_id for flag in flags])


class SuggestedLineTests(_CatalogCase):
    def test_lines_that_narrow_to_one_place_are_one_press(self) -> None:
        near = "2. Incubate it for 10 min at 37 C."
        self.analyze(self.answer(step_two=near))
        (card,) = self.catalog.review_draft(self.protocol_id)["cards"]
        self.assertEqual(card["type"], "evidence_suggested")
        self.assertEqual(card["question_ko"], "이 단계의 근거는 원문 1쪽 4번째 줄인가요?")
        self.assertEqual([choice["label_ko"] for choice in card["choices"]],
                         ["맞아요", "다른 줄이에요", "다시 분석"])
        press = card["choices"][0]["request"]
        self.assertEqual(press["source_lines"], {"page": 1, "first_line": 4, "last_line": 4})
        result = self.catalog.record_review_decision(self.protocol_id, press)
        self.assertTrue(result["registrable"])
        (decision,) = [event.payload for event in self.store.list_events(self.protocol_id)
                       if event.event_type == "protocol_review_decision_recorded"]
        self.assertTrue(decision["source_lines"]["suggested_by_server"])

    def test_lines_a_person_picks_elsewhere_are_not_marked_as_the_server_s(self) -> None:
        self.analyze(self.answer(step_two="2. Incubate it for 10 min at 37 C."))
        self.catalog.record_review_decision(self.protocol_id, {
            "item_id": "step:s2", "action": "confirm",
            "source_lines": {"page": 1, "first_line": 3, "last_line": 4}})
        (decision,) = [event.payload for event in self.store.list_events(self.protocol_id)
                       if event.event_type == "protocol_review_decision_recorded"]
        self.assertNotIn("suggested_by_server", decision["source_lines"])

    def test_words_like_no_line_or_like_two_places_get_no_one_press_card(self) -> None:
        self.analyze(self.answer(step_two=MADE_UP))
        (card,) = self.catalog.review_draft(self.protocol_id)["cards"]
        self.assertEqual(card["type"], "evidence")
        self.assertNotIn("confirm_suggested", [choice["choice_id"] for choice in card["choices"]])


class SameStepTwoPiecesTests(_CatalogCase):
    def test_a_step_two_pieces_read_in_the_same_words_is_kept_once_and_recorded(self) -> None:
        pieces = (AnalysisPiece(1, 2, 1, 2), AnalysisPiece(2, 2, 2, 3))
        answers = {1: [self.answer(pages=(1, 2))], 2: [self.answer(pages=(2, 3))]}

        def model_answer(*, system_prompt: str, input_json: str, response_schema: dict) -> str:
            return answers[json.loads(input_json)["pages"][0]["source_page_number"]].pop(0)

        model = Mock()
        model.analyze.side_effect = model_answer
        self.catalog.request_analysis(self.protocol_id, "analysis-" + "e" * 32)
        with patch("voiney_lab.protocol_catalog.analysis_pieces", return_value=pieces):
            entry = self.catalog.analyze(self.protocol_id, model, analysis_id="analysis-" + "e" * 32)
        self.assertTrue(entry.available_for_execution)
        fixture = self.catalog.load_executable_fixture(self.protocol_id)
        self.assertEqual([step.source_label for step in fixture.steps],
                         ["1", "2", "3", "4", "5", "6"])
        (ready,) = [event.payload for event in self.store.list_events(self.protocol_id)
                    if event.event_type == "protocol_analysis_ready"]
        merged = [entry for entry in ready["auto_resolved"] if entry["kind"] == "same_step"]
        self.assertEqual([(entry["kept"], entry["joined"], entry["same_words"]) for entry in merged],
                         [("p1.s3", "p2.s3", True), ("p1.s4", "p2.s4", True)])
        self.assertEqual({entry["reason"] for entry in merged}, {AUTO_RESOLVED_SAME_STEP})


if __name__ == "__main__":
    unittest.main()
