"""An analysis response that does not close as JSON, read in two fixed shapes (lane EV3, decision 1).

Human decision 2026-10-12. Lane M3 (2026-10-11) found 9 of 30 Gemini 3.8 Flash
"low" and 6 of 28 "medium" analysis responses that were not one JSON object
although the model stopped by itself (never at the output limit). Two shapes
held most of them: (a) the text ends one closing brace short; (b) where the
protocol's sections array ends and "constructs" begins, the model closes one
"}]" too many or opens a new "{". Only those two are repaired, and only by
structural characters -- not one character of a value changes. The repaired
response then passes every check a whole one passes (schema, source evidence,
boundaries), and the repair is recorded beside the analysis and said on the
pipeline line ("응답 형식을 고쳐 읽음(괄호 n개)"). Any other broken response is
left as it was: an invalid response, retried once automatically as before.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from difflib import SequenceMatcher
from pathlib import Path

from tests.test_lane_ev2_cross_page import THAW, response, source, step
from tests.test_protocol_catalog import write_text_pdf
from voiney_lab.experiment_protocol_analysis import (
    ProtocolAnalysisEvidenceError,
    ProtocolAnalysisResponseError,
    _repaired_response_structure,
    _value_characters,
    parse_protocol_analysis_response,
)
from voiney_lab.experiment_protocol_config import ProtocolPersistenceSettings
from voiney_lab.experiment_protocol_pdf import clear_protocol_pdf_cache, extract_protocol_pdf
from voiney_lab.experiment_protocol_store import initialize_protocol_store
from voiney_lab.protocol_catalog import ProtocolCatalog

JUNCTION = '],"constructs":'


def whole() -> str:
    """One whole response, written the way the models write it (no spaces)."""

    return json.dumps(
        response(step("1", THAW, THAW)), ensure_ascii=False, separators=(",", ":"))


def extra_closers(text: str) -> str:
    """Sections closed, then "}]" once more before "constructs" (lane M3, shape b)."""

    assert text.count(JUNCTION) == 1
    return text.replace(JUNCTION, ']}],"constructs":', 1)


def new_opener(text: str) -> str:
    """Sections closed, then a new "{" opened before "constructs" (lane M3, shape b)."""

    assert text.count(JUNCTION) == 1
    return text.replace(JUNCTION, '],{"constructs":', 1)


def one_short(text: str) -> str:
    """The last closing brace left out (lane M3, shape a)."""

    assert text.endswith("}")
    return text[:-1]


class TheTwoShapesAreReadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.expected = parse_protocol_analysis_response(whole(), source())

    def assert_read(self, raw: str, shapes: list[str], brackets: int) -> None:
        with self.assertRaises(json.JSONDecodeError):
            json.loads(raw)
        draft = parse_protocol_analysis_response(raw, source())
        self.assertEqual(draft.protocol, self.expected.protocol)
        self.assertEqual(draft.verified_evidence_count, self.expected.verified_evidence_count)
        self.assertEqual(
            draft.response_repair.public_dict(), {"shapes": shapes, "bracket_count": brackets})
        self.assertEqual(draft.response_repair.summary_ko, f"응답 형식을 고쳐 읽음(괄호 {brackets}개)")

    def test_a_whole_response_records_no_repair(self) -> None:
        self.assertIsNone(self.expected.response_repair)

    def test_one_closing_brace_short_at_the_end(self) -> None:
        self.assert_read(one_short(whole()), ["closing_brace_added_at_end"], 1)

    def test_one_closing_brace_short_with_whitespace_after_the_text(self) -> None:
        self.assert_read(one_short(whole()) + "\n", ["closing_brace_added_at_end"], 1)

    def test_an_extra_closer_pair_where_the_sections_end(self) -> None:
        self.assert_read(
            extra_closers(whole()), ["extra_closers_removed_before_constructs"], 2)

    def test_a_new_object_opened_where_the_sections_end(self) -> None:
        self.assert_read(
            new_opener(whole()), ["extra_opener_removed_before_constructs"], 1)

    def test_both_shapes_in_one_response(self) -> None:
        self.assert_read(
            one_short(extra_closers(whole())),
            ["extra_closers_removed_before_constructs", "closing_brace_added_at_end"], 3)

    def test_the_repair_changes_structural_characters_only(self) -> None:
        for broken in (
            one_short(whole()), extra_closers(whole()), new_opener(whole()),
            one_short(extra_closers(whole())),
        ):
            with self.subTest(broken=broken[-40:]):
                repaired, repair = _repaired_response_structure(broken)
                self.assertEqual(_value_characters(repaired), _value_characters(broken))
                changed = "".join(
                    broken[i1:i2] + repaired[j1:j2]
                    for tag, i1, i2, j1, j2 in SequenceMatcher(
                        None, broken, repaired, autojunk=False).get_opcodes()
                    if tag != "equal")
                self.assertLessEqual(set(changed), set("{}[]"))
                self.assertEqual(len(changed), repair.bracket_count)
                self.assertEqual(json.loads(repaired), json.loads(whole()))

    def test_brackets_and_keys_inside_a_value_are_value_characters(self) -> None:
        value = 'Thaw ]}],{"constructs": [room] temperature}.'
        text = whole().replace(json.dumps(THAW), json.dumps(value))
        json.loads(text)
        self.assertIn(json.dumps(value), _value_characters(text))
        repaired, repair = _repaired_response_structure(one_short(text))
        self.assertEqual(repaired, text)
        self.assertEqual(repair.shapes, ("closing_brace_added_at_end",))


class OtherBrokenResponsesStayRefusedTests(unittest.TestCase):
    def assert_refused(self, raw: str) -> None:
        self.assertIsNone(_repaired_response_structure(raw))
        with self.assertRaises(ProtocolAnalysisResponseError) as caught:
            parse_protocol_analysis_response(raw, source())
        self.assertIsNone(getattr(caught.exception, "response_repair", None))

    def test_two_closing_braces_short(self) -> None:
        self.assert_refused(whole()[:-2])

    def test_a_dropped_quote(self) -> None:
        text = whole()
        self.assertIn('"notes":', text)
        self.assert_refused(text.replace('"notes":', 'notes":', 1))

    def test_a_response_cut_inside_a_string(self) -> None:
        text = whole()
        self.assert_refused(text[: text.index(THAW) + 10])

    def test_an_extra_closer_pair_anywhere_else(self) -> None:
        text = whole()
        self.assertIn('"materials":[]', text)
        self.assert_refused(text.replace('"materials":[]', '"materials":[]}]', 1))

    def test_an_extra_closer_pair_before_another_key(self) -> None:
        # The repair reads "constructs" right after the sections, nothing else.
        tree = response(step("1", THAW, THAW))
        tree["protocol"] = {k: tree["protocol"][k] for k in (
            "protocol_id", "metadata", "before_start", "materials", "equipment", "sections",
            "description", "constructs")}
        text = json.dumps(tree, ensure_ascii=False, separators=(",", ":"))
        self.assertIn('],"description":', text)
        self.assert_refused(text.replace('],"description":', ']}],"description":', 1))

    def test_a_closing_brace_short_inside_the_text(self) -> None:
        text = whole()
        evidence_end = '"source_page_number":1,"source_excerpt":"' + THAW + '"}'
        self.assertIn(evidence_end, text)
        self.assert_refused(text.replace(evidence_end, evidence_end[:-1], 1))

    def test_prose_after_the_object(self) -> None:
        self.assert_refused(one_short(whole()) + " Done.")

    def test_a_second_object_after_the_first(self) -> None:
        self.assert_refused(whole() + "{}")

    def test_a_repair_that_still_does_not_parse(self) -> None:
        self.assert_refused(extra_closers(whole()).replace('"notes":', 'notes":', 1))


class TheRepairedResponseIsCheckedLikeAnyOtherTests(unittest.TestCase):
    def test_a_quote_not_on_its_page_is_refused_and_the_repair_is_carried(self) -> None:
        raw = one_short(whole().replace(
            json.dumps(THAW, ensure_ascii=False) + "}",
            json.dumps(THAW.replace("frozen", "fresh"), ensure_ascii=False) + "}", 1))
        with self.assertRaises(ProtocolAnalysisEvidenceError) as caught:
            parse_protocol_analysis_response(raw, source())
        self.assertEqual(caught.exception.response_repair.bracket_count, 1)

    def test_a_repaired_envelope_must_still_be_the_envelope(self) -> None:
        text = json.dumps({"protocol": {}}, separators=(",", ":"))
        with self.assertRaises(ProtocolAnalysisResponseError) as caught:
            parse_protocol_analysis_response(text[:-1], source())
        self.assertEqual(caught.exception.response_repair.bracket_count, 1)


TEXT = "Protocol Alpha\nSection preparation\n1. Add solution.\nWear gloves."


class _RawModel:
    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.calls = 0

    def analyze(self, *, system_prompt: str, input_json: str, response_schema: dict) -> str:
        self.calls += 1
        return self.answers.pop(0)


class TheCatalogRecordsTheRepairTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        clear_protocol_pdf_cache()
        self.store = initialize_protocol_store(ProtocolPersistenceSettings(True, root / "catalog"))
        self.catalog = ProtocolCatalog(self.store)
        pdf = root / "alpha.pdf"
        write_text_pdf(pdf, TEXT, title="Protocol Alpha")
        self.protocol_id = self.catalog.register(
            pdf, source_filename="alpha.pdf", media_type="application/pdf",
        ).entry.protocol_id
        self.sha256 = extract_protocol_pdf(pdf).sha256

    def tearDown(self) -> None:
        self.store.close()
        clear_protocol_pdf_cache()
        self.temp.cleanup()

    def raw(self, *, instruction: str = "1. Add solution.") -> str:
        evidence = lambda excerpt: {"source_page_number": 1, "source_excerpt": excerpt}
        return json.dumps({
            "analysis_schema_version": 1,
            "pdf_sha256": self.sha256,
            "capability_policy_id": "p1-conservative",
            "protocol": {
                "protocol_id": "alpha",
                "metadata": {"title": "Protocol Alpha", "original_language": "en",
                             "evidence": evidence("Protocol Alpha")},
                "before_start": [], "materials": [], "equipment": [],
                "sections": [{
                    "section_id": "preparation",
                    "title_source_text": "Section preparation",
                    "evidence": evidence("Section preparation"),
                    "steps": [{
                        "step_id": "step-1", "source_label": "1",
                        "instruction_source_text": instruction,
                        "evidence": evidence(instruction),
                        "warnings": [{"statement_id": "gloves", "source_text": "Wear gloves.",
                                      "evidence": evidence("Wear gloves.")}],
                    }],
                }],
                "constructs": [],
                "description": None,
            },
        }, separators=(",", ":"))

    def analyze(self, *answers: str):
        analysis_id = "analysis-" + "e" * 32
        self.catalog.request_analysis(self.protocol_id, analysis_id)
        model = _RawModel(*answers)
        try:
            self.catalog.analyze(self.protocol_id, model, analysis_id=analysis_id)
        except Exception as exc:  # noqa: BLE001 - the caller looks at it
            return model, exc
        return model, None

    def events(self, event_type: str) -> list[dict]:
        return [event.payload for event in self.store.list_events(self.protocol_id)
                if event.event_type == event_type]

    def test_a_repaired_pass_is_recorded_and_said_on_the_pipeline_line(self) -> None:
        model, error = self.analyze(extra_closers(self.raw()))
        self.assertIsNone(error)
        # Read on the first answer: no automatic retry was needed.
        self.assertEqual(model.calls, 1)
        self.assertEqual(self.events("protocol_analysis_retry_started"), [])
        (ready,) = self.events("protocol_analysis_ready")
        self.assertEqual(ready["response_repaired"], {
            "shapes": ["extra_closers_removed_before_constructs"], "bracket_count": 2})
        for line in (self.catalog.review(self.protocol_id)["pipeline"],
                     self.catalog.pipeline_status(self.protocol_id)):
            self.assertEqual(line["stage"], "ready")
            self.assertIn("응답 형식을 고쳐 읽음(괄호 2개)", line["message"])
            self.assertEqual(line["response_repair"]["bracket_count"], 2)

    def test_a_whole_answer_says_nothing_about_a_repair(self) -> None:
        _, error = self.analyze(self.raw())
        self.assertIsNone(error)
        (ready,) = self.events("protocol_analysis_ready")
        self.assertNotIn("response_repaired", ready)
        line = self.catalog.pipeline_status(self.protocol_id)
        self.assertNotIn("고쳐 읽음", line["message"])
        self.assertNotIn("response_repair", line)

    def test_a_repaired_answer_that_fails_a_check_is_sent_again_as_the_broken_answer_was(self) -> None:
        # "고쳐도 안 되면 지금처럼 자동 재시도한다": read after a repair and then
        # refused, the answer gets the one automatic retry a broken answer gets
        # -- DS-2's stored answers lost three passes when it did not.
        model, error = self.analyze(
            one_short(self.raw(instruction="1. Add the solution.")), self.raw())
        self.assertIsNone(error)
        self.assertEqual(model.calls, 2)
        (failed,) = self.events("protocol_analysis_failed")
        self.assertEqual(failed["failure_code"], "protocol_analysis_invalid_evidence")
        self.assertEqual(failed["response_repaired"]["bracket_count"], 1)
        (retry,) = self.events("protocol_analysis_retry_started")
        self.assertEqual(retry["reason_code"], "protocol_analysis_invalid_evidence")
        self.assertEqual(retry["response_repaired"]["bracket_count"], 1)
        (ready,) = self.events("protocol_analysis_ready")
        self.assertNotIn("response_repaired", ready)
        line = self.catalog.pipeline_status(self.protocol_id)
        self.assertEqual(line["stage"], "ready")
        self.assertNotIn("고쳐 읽음", line["message"])

    def test_a_repaired_retry_that_fails_too_is_the_run_s_failure(self) -> None:
        model, error = self.analyze(
            one_short(self.raw(instruction="1. Add the solution.")),
            extra_closers(self.raw(instruction="1. Add the solution.")))
        self.assertIsInstance(error, ProtocolAnalysisEvidenceError)
        self.assertEqual(model.calls, 2)
        failed = self.events("protocol_analysis_failed")
        self.assertEqual([f["response_repaired"]["bracket_count"] for f in failed], [1, 2])
        line = self.catalog.pipeline_status(self.protocol_id)
        self.assertEqual(line["stage"], "evidence")
        self.assertIn("자동 재시도(1/1)도 실패했습니다", line["message"])
        self.assertIn("응답 형식을 고쳐 읽음(괄호 2개)", line["message"])

    def test_a_whole_answer_that_fails_its_evidence_is_still_not_sent_again(self) -> None:
        model, error = self.analyze(self.raw(instruction="1. Add the solution."), self.raw())
        self.assertIsInstance(error, ProtocolAnalysisEvidenceError)
        self.assertEqual(model.calls, 1)
        self.assertEqual(self.events("protocol_analysis_retry_started"), [])

    def test_a_person_s_second_request_is_still_one_request(self) -> None:
        broken = self.raw().replace('"warnings":', 'warnings":', 1)
        self.analyze(broken, broken)
        analysis_id = "analysis-" + "9" * 32
        self.catalog.request_analysis(self.protocol_id, analysis_id)
        model = _RawModel(one_short(self.raw(instruction="1. Add the solution.")), self.raw())
        with self.assertRaises(ProtocolAnalysisEvidenceError):
            self.catalog.analyze(self.protocol_id, model, analysis_id=analysis_id)
        self.assertEqual(model.calls, 1)

    def test_another_broken_answer_is_still_retried_once(self) -> None:
        broken = self.raw().replace('"warnings":', 'warnings":', 1)
        model, error = self.analyze(broken, one_short(self.raw()))
        self.assertIsNone(error)
        self.assertEqual(model.calls, 2)
        first, = self.events("protocol_analysis_failed")
        self.assertEqual(first["failure_code"], "protocol_analysis_invalid_response")
        self.assertNotIn("response_repaired", first)
        (ready,) = self.events("protocol_analysis_ready")
        self.assertEqual(ready["response_repaired"]["bracket_count"], 1)

    def test_a_repaired_re_analysis_that_fails_says_so_beside_the_pass(self) -> None:
        self.analyze(self.raw())
        analysis_id = "analysis-" + "f" * 32
        self.catalog.request_analysis(self.protocol_id, analysis_id, reanalysis=True)
        bad = self.raw(instruction="1. Add the solution.")
        with self.assertRaises(ProtocolAnalysisEvidenceError):
            self.catalog.analyze(
                self.protocol_id, _RawModel(one_short(bad), one_short(bad)),
                analysis_id=analysis_id)
        again = self.catalog.review(self.protocol_id)["reanalysis"]
        self.assertEqual(again["failure"]["response_repaired"]["bracket_count"], 1)
        self.assertEqual(again["automatic_retry"]["state"], "failed")
        line = self.catalog.pipeline_status(self.protocol_id)
        self.assertEqual(line["stage"], "ready")
        self.assertIn("다시 분석이 실패했습니다", line["message"])
        self.assertIn("응답 형식을 고쳐 읽음(괄호 1개)", line["message"])
        # The pass that still runs was read whole.
        self.assertNotIn("response_repair", line)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
