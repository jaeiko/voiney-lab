"""What a fixed repetition's count means (lane EV2, decision 3).

Human decision 2026-10-10. Lane AQ (8) found the analysis writing
``repeat_count`` two ways: "Repeat ... once more" and "Repeat steps N and M" as
1 (the runs after the first), "a total of 4 washes", "three cycles" and "a
second time" as the total. Lane VT counts rounds as "n회 중 k회째", so the first
reading skips a wash: CDC's "22. Repeat preheated distilled water wash step
once more." was led as one round of one.

* ``FixedRangeRepetition.repeat_count`` is the total number of runs of the
  range, the first one included.
* The analysis says how the source states it (``repeat_count_kind``): the
  total, the runs after the first ("additional"), or two readings
  ("ambiguous", "Repeat steps 36-38 twice").
* The server makes an additional count a total (+1); an ambiguous one is no
  count, and the experimenter is asked through lane CB/CF's count question --
  before the start, or at the range's first step with "실험 중에 묻기".
* An analysis stored before the decision has no kind: its count is not
  changed, and the start screen says it needs checking ("확인 필요").
"""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from tests.lane_cb_support import Turns
from tests.protocol_vocabulary_support import build_fixture
from tests.test_protocol_catalog import write_text_pdf
from voiney_lab import experiment_protocol as domain
from voiney_lab.experiment_protocol_analysis import (
    ANALYSIS_RESPONSE_SCHEMA,
    ProtocolAnalysisResponseError,
    parse_protocol_analysis_response,
    validate_protocol_analysis_evidence,
)
from voiney_lab.experiment_protocol_config import ProtocolPersistenceSettings
from voiney_lab.experiment_protocol_pdf import (
    ProtocolPdfExtraction,
    ProtocolPdfMetadata,
    ProtocolPdfPage,
    clear_protocol_pdf_cache,
    extract_protocol_pdf,
)
from voiney_lab.experiment_protocol_store import (
    deserialize_analysis,
    initialize_protocol_store,
    serialize_analysis,
)
from voiney_lab.protocol_catalog import ProtocolCatalog

ROOT = Path(__file__).resolve().parents[1]
TITLE = "Plug wash protocol"
ONCE_MORE = "3. Repeat step 2 once more."
TWICE = "3. Repeat step 2 twice."
TOTAL = "3. Repeat step 2 for a total of 4 washes."


def source(repeat_line: str) -> ProtocolPdfExtraction:
    text = f"{TITLE}\n1. Add the plugs to the tube.\n2. Wash the plugs with water.\n{repeat_line}\n4. Store the plugs.\n"
    return ProtocolPdfExtraction(
        original_filename="repeat.pdf", byte_size=1, sha256="d" * 64,
        media_type="application/pdf", page_count=1, encrypted=False,
        metadata=ProtocolPdfMetadata(None, None, None, None, None, None, None),
        pages=(ProtocolPdfPage(1, text, False),),
    )


def evidence(excerpt: str) -> dict:
    return {"source_page_number": 1, "source_excerpt": excerpt}


def response(repeat_line: str, count: int | None, kind: str | None, *, with_kind: bool = True) -> dict:
    steps = [
        ("1", "Add the plugs to the tube."),
        ("2", "Wash the plugs with water."),
        ("3", repeat_line[3:]),
        ("4", "Store the plugs."),
    ]
    repetition = {
        "type": "fixed_range_repetition",
        "repetition_id": "repeat-2",
        "start_step_id": "step-2",
        "end_step_id": "step-2",
        "range_source_text": repeat_line[3:],
        "evidence": evidence(repeat_line),
        "repeat_count": count,
        "step_id": "step-3",
    }
    if with_kind:
        repetition["repeat_count_kind"] = kind
    return {
        "analysis_schema_version": 1,
        "pdf_sha256": "d" * 64,
        "capability_policy_id": "p1-conservative",
        "protocol": {
            "protocol_id": "protocol-repeat",
            "metadata": {"title": TITLE, "original_language": "en", "evidence": evidence(TITLE)},
            "before_start": [], "materials": [], "equipment": [],
            "sections": [{
                "section_id": "wash", "title_source_text": TITLE, "evidence": evidence(TITLE),
                "steps": [
                    {
                        "step_id": f"step-{label}", "source_label": label,
                        "instruction_source_text": text, "evidence": evidence(f"{label}. {text}"),
                    }
                    for label, text in steps
                ],
            }],
            "constructs": [repetition],
            "description": None,
        },
    }


def repetition_of(repeat_line: str, count: int | None, kind: str | None, **options):
    draft = parse_protocol_analysis_response(
        json.dumps(response(repeat_line, count, kind, **options)), source(repeat_line))
    return draft, draft.protocol.constructs[0]


class TheCountIsTheTotalTests(unittest.TestCase):
    def test_the_domain_says_what_the_count_means(self):
        doc = " ".join((domain.FixedRangeRepetition.__doc__ or "").split())
        self.assertIn("total number of runs of the range, the first run included", doc)

    def test_the_analysis_is_asked_how_the_source_states_it(self):
        definition = ANALYSIS_RESPONSE_SCHEMA["$defs"]["FixedRangeRepetition"]
        self.assertIn("repeat_count_kind", definition["required"])
        kinds = definition["properties"]["repeat_count_kind"]["anyOf"][0]["enum"]
        self.assertEqual(kinds, ["total", "additional", "ambiguous"])

    def test_once_more_is_one_more_than_the_first_run(self):
        _, repetition = repetition_of(ONCE_MORE, 1, "additional")
        self.assertEqual(repetition.repeat_count, 2)
        self.assertEqual(repetition.repeat_count_kind, "additional")

    def test_a_stated_total_is_kept(self):
        _, repetition = repetition_of(TOTAL, 4, "total")
        self.assertEqual(repetition.repeat_count, 4)
        self.assertEqual(repetition.repeat_count_kind, "total")

    def test_an_ambiguous_count_is_no_count(self):
        _, repetition = repetition_of(TWICE, 2, "ambiguous")
        self.assertIsNone(repetition.repeat_count)
        self.assertEqual(repetition.repeat_count_kind, "ambiguous")

    def test_a_count_without_its_kind_is_not_changed(self):
        _, repetition = repetition_of(ONCE_MORE, 1, None, with_kind=False)
        self.assertEqual(repetition.repeat_count, 1)
        self.assertIsNone(repetition.repeat_count_kind)

    def test_revalidating_a_stored_analysis_does_not_add_one_again(self):
        draft, _ = repetition_of(ONCE_MORE, 1, "additional")
        verified, _ = validate_protocol_analysis_evidence(draft.protocol, source(ONCE_MORE))
        self.assertEqual(verified.constructs[0].repeat_count, 2)

    def test_the_kind_survives_storage_and_old_records_read_without_one(self):
        draft, _ = repetition_of(ONCE_MORE, 1, "additional")
        stored, _ = serialize_analysis(draft.protocol, draft.readiness, draft.capability_policy_id)
        self.assertEqual(deserialize_analysis(stored)[0].constructs[0].repeat_count_kind, "additional")
        old = json.loads(stored)
        construct = old["protocol"]["fields"]["constructs"]["$tuple"][0]
        del construct["fields"]["repeat_count_kind"]
        restored = deserialize_analysis(json.dumps(old))[0].constructs[0]
        self.assertIsNone(restored.repeat_count_kind)
        self.assertEqual(restored.repeat_count, 2)

    def test_the_domain_refuses_an_unknown_kind_and_a_count_for_an_ambiguous_one(self):
        draft, repetition = repetition_of(TOTAL, 4, "total")
        for changed in (
            replace(repetition, repeat_count_kind="twice"),
            replace(repetition, repeat_count_kind="ambiguous", repeat_count=2),
        ):
            with self.subTest(changed=changed.repeat_count_kind), self.assertRaises(domain.ProtocolValidationError):
                domain.validate_protocol(replace(draft.protocol, constructs=(changed,)))

    def test_a_response_with_an_unknown_kind_is_refused(self):
        with self.assertRaises(ProtocolAnalysisResponseError):
            repetition_of(TOTAL, 4, "twice")


STEPS = (
    "1 Add the plugs to the tube.",
    "2 Wash the plugs with water.",
    "3 Repeat step 2 twice.",
    "4 Store the plugs.",
)


def wash_fixture(count: int | None, kind: str | None, text: str = "Repeat step 2 twice."):
    steps = (*STEPS[:2], f"3 {text}", STEPS[3])
    base = build_fixture(protocol_id="ev2-wash", title=TITLE, steps=steps)
    protocol = base.draft.protocol
    repetition = domain.FixedRangeRepetition(
        repetition_id="repeat-2", start_step_id="step-2", end_step_id="step-2",
        range_source_text=text, evidence=domain.SourceEvidence(1, f"3 {text}"),
        repeat_count=count, repeat_count_kind=kind, step_id="step-3",
    )
    protocol = replace(protocol, constructs=(repetition,))
    domain.validate_protocol(protocol)
    draft = replace(base.draft, protocol=protocol, readiness=domain.assess_readiness(protocol))
    return replace(base, draft=draft)


class Session(Turns):
    def open_with(self, fixture, *, question_timing: str):
        from voiney_lab.curated_protocol import CuratedProtocolSession

        self.session = CuratedProtocolSession(fixture)
        self.session.apply_experimenter_settings(
            {"confirm_mode": "readback", "question_timing": question_timing})
        self.session.activate_configured()
        self.turn_id = 0
        return self.session


AMBIGUOUS_QUESTION = (
    "(1/1) 2~2단계 반복 횟수가 원문에서 두 가지로 읽혀요: “Repeat step 2 twice.”. "
    "처음 한 번을 포함해 모두 몇 번 하시나요? "
    "아직 모르면 '아직 몰라', 지금 정하지 않으려면 '나중에'라고 해 주세요."
)


class AnAmbiguousCountIsAskedTests(Session, unittest.TestCase):
    def test_it_is_asked_before_the_start(self):
        self.open_with(wash_fixture(None, "ambiguous"), question_timing="before_start")
        plan = self.say("프로토콜 시작해줘")
        self.assertFalse(self.session.active)
        self.assertIn(AMBIGUOUS_QUESTION, plan.display_text)
        plan = self.say("세 번")
        self.assertTrue(self.session.active)
        self.assertEqual([item["count"] for item in plan.step_record["registered"]], [3])
        self.session.current_index = 1
        status = self.session.repeat_round_status()
        self.assertEqual((status["round"], status["required"]), (1, 3))

    def test_it_is_asked_at_the_ranges_first_step_while_the_run_goes(self):
        self.open_with(wash_fixture(None, "ambiguous"), question_timing="during")
        self.say("프로토콜 시작해줘")
        self.assertEqual(self.label(), "1")
        self.say("1단계 완료했어")
        self.assertEqual(self.label(), "2")
        question = self.session.open_server_question()
        self.assertEqual(question["kind"], "repeat_count")
        self.assertIn("두 가지로 읽혀요", question["text"])
        self.assertIn("처음 한 번을 포함해 모두 몇 번", question["text"])

    def test_a_count_the_source_states_is_not_asked(self):
        for kind, count in (("total", 2), ("additional", 2), (None, 1)):
            with self.subTest(kind=kind):
                self.open_with(wash_fixture(count, kind, "Repeat step 2 once more."), question_timing="before_start")
                self.say("프로토콜 시작해줘")
                self.assertTrue(self.session.active)
                self.assertIsNone(self.session.open_server_question())


class OnceMoreIsLedAsTwoRoundsTests(Session, unittest.TestCase):
    def test_the_range_end_asks_for_the_second_round(self):
        # "Repeat step 2 once more." read as 1 additional run: 2 in total.
        self.open_with(wash_fixture(2, "additional", "Repeat step 2 once more."), question_timing="during")
        self.say("프로토콜 시작해줘")
        self.session.current_index = 1
        self.say("2단계 완료했어")
        self.assertEqual(self.label(), "3")
        # A round runs through the step that states the repeat (lane CB).
        plan = self.say("3단계 완료했어")
        self.assertEqual(self.label(), "3")
        self.assertEqual(
            plan.display_text,
            "2회 중 1회째 끝났어요. 2~2단계를 한 번 더 해야 해요(2/2회차). 2단계로 돌아갈까요?",
        )
        self.assertIn("2회 중 2회차", plan.speech_text)


class _Model:
    def __init__(self, reply: str) -> None:
        self.reply = reply

    def analyze(self, *, system_prompt: str, input_json: str, response_schema: dict) -> str:
        return self.reply


PDF_TEXT = "Plug wash protocol 1. Add the plugs. 2. Wash the plugs. 3. Repeat step 2 twice. 4. Store the plugs."


class StartScreenSaysWhatNeedsCheckingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        clear_protocol_pdf_cache()
        self.store = initialize_protocol_store(ProtocolPersistenceSettings(True, self.root / "catalog"))
        self.catalog = ProtocolCatalog(self.store)
        self.pdf = self.root / "wash.pdf"
        write_text_pdf(self.pdf, PDF_TEXT, title="Plug wash")
        self.protocol_id = self.catalog.register(
            self.pdf, source_filename="wash.pdf", media_type="application/pdf",
        ).entry.protocol_id

    def tearDown(self) -> None:
        self.store.close()
        clear_protocol_pdf_cache()
        self.temp.cleanup()

    def analyze(self, count: int | None, kind: str | None, *, with_kind: bool = True) -> dict:
        extraction = extract_protocol_pdf(self.pdf)
        repetition = {
            "type": "fixed_range_repetition", "repetition_id": "repeat-2",
            "start_step_id": "step-2", "end_step_id": "step-2",
            "range_source_text": "Repeat step 2 twice.",
            "evidence": evidence("3. Repeat step 2 twice."), "repeat_count": count, "step_id": "step-3",
        }
        if with_kind:
            repetition["repeat_count_kind"] = kind
        reply = {
            "analysis_schema_version": 1, "pdf_sha256": extraction.sha256,
            "capability_policy_id": "p1-conservative",
            "protocol": {
                "protocol_id": self.protocol_id,
                "metadata": {"title": "Plug wash protocol", "original_language": "en",
                             "evidence": evidence("Plug wash protocol")},
                "before_start": [], "materials": [], "equipment": [],
                "sections": [{
                    "section_id": "wash", "title_source_text": "Plug wash protocol",
                    "evidence": evidence("Plug wash protocol"),
                    "steps": [
                        {"step_id": f"step-{n}", "source_label": str(n), "instruction_source_text": text,
                         "evidence": evidence(f"{n}. {text}")}
                        for n, text in ((1, "Add the plugs."), (2, "Wash the plugs."),
                                        (3, "Repeat step 2 twice."), (4, "Store the plugs."))
                    ],
                }],
                "constructs": [repetition], "description": None,
            },
        }
        analysis_id = "analysis-" + "f" * 32
        self.catalog.request_analysis(self.protocol_id, analysis_id)
        self.catalog.analyze(self.protocol_id, _Model(json.dumps(reply)), analysis_id=analysis_id)
        return self.catalog.review(self.protocol_id)

    def test_a_count_with_no_kind_needs_checking(self):
        review = self.analyze(2, None, with_kind=False)
        self.assertEqual(
            review["repeat_count_checks_ko"],
            ["반복 횟수 확인 필요 · 2~2단계 · 원문 “Repeat step 2 twice.” · 분석이 2회로 읽음"
             "(처음을 포함한 총 횟수인지 원문을 확인하세요)"],
        )

    def test_an_ambiguous_count_is_said_to_be_asked(self):
        review = self.analyze(2, "ambiguous")
        self.assertEqual(
            review["repeat_count_checks_ko"],
            ["반복 횟수 확인 필요 · 2~2단계 · 원문 “Repeat step 2 twice.” · 처음을 포함한 총 횟수인지 "
             "더 하는 횟수인지 원문이 두 가지로 읽혀 시작할 때 여쭤봅니다"],
        )

    def test_a_stated_count_needs_no_line(self):
        review = self.analyze(2, "total")
        self.assertEqual(review["repeat_count_checks_ko"], [])

    def test_the_start_summary_renders_the_lines_as_text(self):
        html = (ROOT / "src" / "voiney_lab" / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="protocol-start-repeat-checks"', html)
        start = html.index("function renderStartSummary(")
        body = html[start: html.index("\n}\n", start)]
        self.assertIn("repeat_count_checks_ko", body)
        self.assertNotIn("innerHTML", body)


if __name__ == "__main__":
    unittest.main()
