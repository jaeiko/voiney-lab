"""Offline tests for the development-only curated Protocol voice slice."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from voice_workflow_agent import experiment_protocol as domain
from voice_workflow_agent.audio import FRAME_BYTES
from voice_workflow_agent.brain import (
    BrainResult,
    SentenceSegment,
    select_curated_protocol_answer,
)
from voice_workflow_agent.curated_protocol import (
    DEVELOPMENT_FIXTURE_STATUS,
    CuratedProtocolAction,
    CuratedProtocolFixtureError,
    CuratedProtocolSession,
    CuratedProtocolSpeechMode,
    load_curated_protocol_fixture,
)
from voice_workflow_agent.experiment_protocol_analysis import (
    ANALYSIS_RESPONSE_SCHEMA,
    ProtocolAnalysisEvidenceError,
    ProtocolAnalysisResponseError,
    parse_protocol_analysis_response,
)
from voice_workflow_agent.language import Transcription
from voice_workflow_agent.server import (
    ListenerSession,
    ServerConfig,
    cancel_cascade_generation,
    run_turn,
    run_turn_safely,
    voice_socket,
)
from voice_workflow_agent.tools import ToolContext
from voice_workflow_agent.vad import EndpointDetector, TurnState


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "data/development_protocols/candidate_a_curated_analysis.json"
PROVENANCE = ROOT / "data/development_protocols/candidate_a_curated_analysis.provenance.json"
SOURCE_PDF = Path("/home/student/protocol-test-files/in-gel-digestion.pdf")
EXPECTED_FIXTURE_SHA256 = "c2779c24924dbeb3c83d2f905f81af472b1b51f1adfd0c7e3fe423270d7a76d7"
EXPECTED_SCHEMA_SHA256 = "3d7970faf5f55cd7ad11abbccffa01cd4f8989bb5932a436740e77bac7f23923"


class RecordingCompletions:
    def __init__(self, fact_id: str = "current_step", error: Exception | None = None):
        self.fact_id = fact_id
        self.error = error
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(
                content=json.dumps({"fact_id": self.fact_id})
            )
        )])


class RecordingClient:
    def __init__(self, fact_id: str = "current_step", error: Exception | None = None):
        self.model = "offline-model"
        self.chat = SimpleNamespace(
            completions=RecordingCompletions(fact_id, error)
        )


class Socket:
    def __init__(self):
        self.text: list[dict] = []
        self.binary: list[bytes] = []

    async def send_text(self, value: str) -> None:
        self.text.append(json.loads(value))

    async def send_bytes(self, value: bytes) -> None:
        self.binary.append(value)


def assert_schema_shape(
    testcase: unittest.TestCase,
    value: object,
    schema: dict,
    root: dict,
) -> None:
    if "$ref" in schema:
        name = schema["$ref"].removeprefix("#/$defs/")
        assert_schema_shape(testcase, value, root["$defs"][name], root)
        return
    if "anyOf" in schema:
        errors = []
        for branch in schema["anyOf"]:
            try:
                assert_schema_shape(testcase, value, branch, root)
                return
            except AssertionError as exc:
                errors.append(exc)
        testcase.fail(f"no anyOf branch matched: {len(errors)}")
    if "oneOf" in schema:
        matches = 0
        for branch in schema["oneOf"]:
            try:
                assert_schema_shape(testcase, value, branch, root)
            except AssertionError:
                continue
            matches += 1
        testcase.assertEqual(matches, 1)
        return
    if "const" in schema:
        testcase.assertEqual(value, schema["const"])
    if "enum" in schema:
        testcase.assertIn(value, schema["enum"])
    expected = schema.get("type")
    if expected == "object":
        testcase.assertIsInstance(value, dict)
        properties = schema.get("properties", {})
        testcase.assertTrue(set(schema.get("required", ())).issubset(value))
        if schema.get("additionalProperties") is False:
            testcase.assertEqual(set(value) - set(properties), set())
        for name, item in value.items():
            assert_schema_shape(testcase, item, properties[name], root)
    elif expected == "array":
        testcase.assertIsInstance(value, list)
        for item in value:
            assert_schema_shape(testcase, item, schema["items"], root)
    elif expected == "string":
        testcase.assertIsInstance(value, str)
    elif expected == "integer":
        testcase.assertIs(type(value), int)
    elif expected == "number":
        testcase.assertIn(type(value), (int, float))
    elif expected == "boolean":
        testcase.assertIs(type(value), bool)
    elif expected == "null":
        testcase.assertIsNone(value)


class CuratedProtocolFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = load_curated_protocol_fixture(
            FIXTURE,
            PROVENANCE,
            SOURCE_PDF,
        )
        cls.payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cls.provenance = json.loads(PROVENANCE.read_text(encoding="utf-8"))

    def parse_mutation(self, payload: dict):
        return parse_protocol_analysis_response(
            json.dumps(payload, ensure_ascii=False),
            self.fixture.draft.extraction,
        )

    def test_fixture_identity_schema_decoder_evidence_and_development_status(self):
        raw = FIXTURE.read_bytes()
        schema_bytes = json.dumps(
            ANALYSIS_RESPONSE_SCHEMA,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        self.assertEqual(hashlib.sha256(raw).hexdigest(), EXPECTED_FIXTURE_SHA256)
        self.assertEqual(hashlib.sha256(schema_bytes).hexdigest(), EXPECTED_SCHEMA_SHA256)
        assert_schema_shape(self, self.payload, ANALYSIS_RESPONSE_SCHEMA, ANALYSIS_RESPONSE_SCHEMA)
        self.assertEqual(self.fixture.status, DEVELOPMENT_FIXTURE_STATUS)
        self.assertEqual(
            self.provenance["status"],
            "development_only_not_final_acceptance",
        )
        self.assertEqual(
            self.provenance["fixture_creation_mode"],
            "offline_curated_development_fixture",
        )
        self.assertNotIn("acceptance", self.payload)
        self.assertNotIn("persistence", self.payload)
        self.assertGreaterEqual(self.fixture.draft.verified_evidence_count, 25)

    def test_fixture_has_exact_ordered_inventory_and_locked_source_facts(self):
        protocol = self.fixture.draft.protocol
        steps = self.fixture.steps
        labels = tuple(step.source_label for step in steps)
        self.assertEqual(labels, tuple(str(number) for number in range(1, 26)))
        self.assertEqual(len(labels), len(set(labels)))
        self.assertEqual(
            tuple(tuple(step.source_label for step in section.steps)
                  for section in protocol.sections),
            (("1",), ("2", "3", "4", "5", "6", "7"),
             tuple(str(number) for number in range(8, 21)),
             ("21", "22", "23"), ("24", "25")),
        )
        self.assertTrue(protocol.materials)
        self.assertTrue(protocol.equipment)
        self.assertTrue(protocol.before_start)
        self.assertTrue(any(step.warnings for step in steps))
        self.assertTrue(any(step.notes for step in steps))
        self.assertTrue(any(step.expected_results for step in steps))

        by_label = {step.source_label: step for step in steps}
        step_three = "\n".join((
            by_label["3"].instruction_source_text,
            *(item.source_text for item in by_label["3"].notes),
        ))
        self.assertIn("500 µL", step_three)
        self.assertIn("solution a", step_three.casefold())
        self.assertIn("37°C", step_three)
        self.assertIn("800 rpm", step_three)
        self.assertIn("7", by_label["7"].instruction_source_text)
        self.assertIn("60min", by_label["12"].instruction_source_text)
        self.assertIn("800 rpm", by_label["12"].instruction_source_text)
        self.assertIn("60°C", by_label["12"].instruction_source_text)
        self.assertIn("room temperature", by_label["16"].instruction_source_text.casefold())
        self.assertIn("dark", by_label["16"].instruction_source_text.casefold())
        self.assertIn("45min", by_label["16"].instruction_source_text)
        self.assertIn("25uL", by_label["22"].instruction_source_text)
        self.assertIn("fridge", by_label["22"].instruction_source_text.casefold())
        self.assertIn("10min", by_label["22"].instruction_source_text)
        self.assertIn("37°C", by_label["23"].instruction_source_text)
        self.assertIn("800 rpm", by_label["23"].instruction_source_text)
        self.assertIn("16", by_label["23"].instruction_source_text)
        self.assertGreaterEqual(len(by_label["24"].sub_actions), 2)
        timed_actions = tuple(
            action
            for action in by_label["24"].sub_actions
            if action.estimated_duration is not None
            or action.process_timer is not None
        )
        self.assertEqual(len(timed_actions), 1)
        self.assertIn("extract", timed_actions[0].instruction_source_text.casefold())
        self.assertTrue(all(
            action.estimated_duration is None and action.process_timer is None
            for action in by_label["25"].sub_actions
        ))
        self.assertTrue(any(
            isinstance(item, domain.RepeatUntil)
            and {
                "candidate-a-step-02",
                "candidate-a-step-03",
                "candidate-a-step-04",
                "candidate-a-step-05",
                "candidate-a-step-06",
                "candidate-a-step-07",
            }.issubset(item.repeated_step_ids)
            and item.step_id == "candidate-a-step-07"
            for item in protocol.constructs
        ))
        self.assertTrue(any(
            isinstance(item, domain.RepeatUntil)
            and {"candidate-a-step-08", "candidate-a-step-09"}
            .issubset(item.repeated_step_ids)
            for item in protocol.constructs
        ))
        self.assertTrue(any(
            isinstance(item, (domain.SourceAmbiguity, domain.ProtocolConflict))
            and item.step_id == "candidate-a-step-20"
            and not item.resolved
            for item in protocol.constructs
        ))

    def test_fixture_rejects_unknown_field_and_altered_page(self):
        unknown = copy.deepcopy(self.payload)
        unknown["protocol"]["materials"][0]["supplier"] = "not allowed"
        with self.assertRaises(ProtocolAnalysisResponseError):
            self.parse_mutation(unknown)

        wrong_page = copy.deepcopy(self.payload)
        wrong_page["protocol"]["materials"][0]["evidence"]["source_page_number"] = 3
        with self.assertRaises(ProtocolAnalysisEvidenceError):
            self.parse_mutation(wrong_page)

    def test_fixture_rejects_paraphrase_changed_unit_and_stitched_excerpt(self):
        paraphrase = copy.deepcopy(self.payload)
        paraphrase["protocol"]["materials"][0]["evidence"]["source_excerpt"] += " altered"
        with self.assertRaises(ProtocolAnalysisEvidenceError):
            self.parse_mutation(paraphrase)

        changed_unit = copy.deepcopy(self.payload)
        step = changed_unit["protocol"]["sections"][1]["steps"][1]
        step["instruction_source_text"] = step["instruction_source_text"].replace(
            "500 µL", "501 µL", 1
        )
        with self.assertRaises(ProtocolAnalysisEvidenceError):
            self.parse_mutation(changed_unit)

        stitched = copy.deepcopy(self.payload)
        evidence = stitched["protocol"]["sections"][1]["steps"][1]["evidence"]
        original = evidence["source_excerpt"]
        evidence["source_excerpt"] = original[:20] + original[-20:]
        with self.assertRaises(ProtocolAnalysisEvidenceError):
            self.parse_mutation(stitched)

    def test_loader_rejects_fixture_or_provenance_tampering(self):
        with self.subTest("fixture hash"):
            with patch("pathlib.Path.read_bytes", side_effect=OSError("offline")):
                with self.assertRaises(CuratedProtocolFixtureError):
                    load_curated_protocol_fixture(FIXTURE, PROVENANCE, SOURCE_PDF)


class CuratedProtocolSessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = load_curated_protocol_fixture(FIXTURE, PROVENANCE, SOURCE_PDF)

    def test_server_owned_start_current_repeat_next_replay_stop(self):
        session = CuratedProtocolSession(self.fixture)
        start = session.plan("프로토콜 시작", turn_id=1, language="ko")
        self.assertEqual(start.action, CuratedProtocolAction.START)
        self.assertEqual(session.state()["current_step_label"], "1")
        current = session.plan("현재 단계", turn_id=2, language="ko")
        repeat = session.plan("다시 말해 줘", turn_id=3, language="ko")
        self.assertEqual(current.action, CuratedProtocolAction.CURRENT)
        self.assertEqual(repeat.action, CuratedProtocolAction.REPEAT)
        self.assertFalse(current.state_changed)
        self.assertFalse(repeat.state_changed)
        advanced = session.plan("다음", turn_id=4, language="ko")
        replay = session.plan("다음", turn_id=4, language="ko")
        self.assertEqual(advanced, replay)
        self.assertEqual(session.state()["current_step_label"], "2")
        stopped = session.plan("종료", turn_id=5, language="ko")
        self.assertEqual(stopped.action, CuratedProtocolAction.STOP)
        self.assertFalse(session.state()["active"])

    def test_explicit_korean_workflow_forms_and_projection_revisions(self):
        session = CuratedProtocolSession(self.fixture)
        opening = session.state()
        self.assertEqual(opening["revision"], 0)
        self.assertEqual(opening["readiness_status"], "analysis_required")
        self.assertTrue(opening["development_only"])

        start = session.plan("프로토콜을 시작해 줘", turn_id=1, language="ko")
        started = session.state()
        self.assertEqual(start.action, CuratedProtocolAction.START)
        self.assertEqual(started["current_step_label"], "1")
        self.assertEqual(started["revision"], 1)

        for turn_id, transcript, action in (
            (2, "현재 단계를 알려줘", CuratedProtocolAction.CURRENT),
            (3, "현재 단계 알려줘", CuratedProtocolAction.CURRENT),
            (4, "다시 말해 줘", CuratedProtocolAction.REPEAT),
            (5, "다시 말해줘", CuratedProtocolAction.REPEAT),
        ):
            plan = session.plan(transcript, turn_id=turn_id, language="ko")
            self.assertEqual(plan.action, action)
            self.assertEqual(session.state()["revision"], 1)
            self.assertEqual(session.state()["current_step_label"], "1")

        advanced = session.plan("단계로 넘어가죠", turn_id=6, language="ko")
        self.assertEqual(advanced.action, CuratedProtocolAction.NEXT)
        self.assertEqual(session.state()["current_step_label"], "2")
        self.assertEqual(session.state()["revision"], 2)
        self.assertEqual(session.plan(
            "단계로 넘어가죠", turn_id=6, language="ko"
        ), advanced)
        self.assertEqual(session.state()["current_step_label"], "2")

        stopped = session.plan(
            "프로토콜을 종료해 줘", turn_id=7, language="ko"
        )
        self.assertEqual(stopped.action, CuratedProtocolAction.STOP)
        self.assertFalse(session.state()["active"])
        self.assertEqual(session.state()["revision"], 3)

    def test_configured_protocol_is_immediately_usable_for_reviewed_proceed_forms(self):
        transcripts = (
            "실험을 진행해 줘.",
            "실험을 진행해줘.",
            "실험을 진행해주세요",
            "실험을 진행해 주세요",
            "프로토콜을 진행해 줘",
            "프로토콜을 진행해줘",
            "프로토콜 진행해 줘",
            "프로토콜 진행해줘",
            "절차를 진행해 줘",
            "절차를 진행해줘",
            "프로토콜을 시작해 줘",
            "프로토콜을 시작해줘",
            "프로토콜 시작해 줘",
            "프로토콜 시작해줘",
        )
        canonical = self.fixture.steps[0].instruction_source_text

        with patch(
            "voice_workflow_agent.curated_protocol.extract_protocol_pdf",
            side_effect=AssertionError("runtime PDF verification must not run"),
        ), patch(
            "voice_workflow_agent.curated_protocol._load_json_object",
            side_effect=AssertionError("runtime provenance lookup must not run"),
        ), patch(
            "voice_workflow_agent.curated_protocol._sha256",
            side_effect=AssertionError("runtime hash verification must not run"),
        ), patch.object(
            domain,
            "assess_readiness",
            side_effect=AssertionError("readiness must not gate a configured turn"),
        ):
            for turn_id, transcript in enumerate(transcripts, 1):
                with self.subTest(transcript=transcript):
                    session = CuratedProtocolSession(self.fixture)
                    session.activate_configured()
                    opening = session.state()

                    plan = session.plan(
                        transcript,
                        turn_id=turn_id,
                        language="ko",
                    )

                    self.assertEqual(plan.action, CuratedProtocolAction.START)
                    self.assertEqual(plan.step_label, "1")
                    self.assertIn(canonical, plan.display_text)
                    self.assertNotIn("검증된 개발용 픽스처", plan.display_text)
                    self.assertEqual(
                        plan.speech_text,
                        "개발용 픽스처 1단계 안내를 화면에 표시했습니다.",
                    )
                    self.assertEqual(session.current_index, 0)
                    self.assertEqual(session.state(), opening)

        session = CuratedProtocolSession(self.fixture)
        session.activate_configured()
        for turn_id, question in enumerate((
            "프로토콜 시작 조건이 뭐야?",
            "이 프로토콜을 시작하면 위험해?",
            "프로토콜 진행 상황을 설명해줘",
            "7단계에서 왜 진행할 수 없어?",
        ), 100):
            with self.subTest(question=question):
                self.assertEqual(
                    session.plan(
                        question,
                        turn_id=turn_id,
                        language="ko",
                    ).action,
                    CuratedProtocolAction.UNSUPPORTED,
                )

    def test_stopped_protocol_requires_an_explicit_restart(self):
        session = CuratedProtocolSession(self.fixture)
        session.activate_configured()
        session.plan("중지해 줘.", turn_id=1, language="ko")

        unrelated = session.plan("아무 관련 없는 말", turn_id=2, language="ko")
        self.assertEqual(unrelated.action, CuratedProtocolAction.INACTIVE)
        self.assertFalse(session.active)
        self.assertNotIn("검증된 개발용 픽스처", unrelated.display_text)

        restarted = session.plan(
            "프로토콜을 시작해 줘",
            turn_id=3,
            language="ko",
        )
        self.assertEqual(restarted.action, CuratedProtocolAction.START)
        self.assertTrue(session.active)
        self.assertEqual(session.current_index, 0)

    def test_display_and_speech_are_explicit_and_full_detail_is_reviewed(self):
        session = CuratedProtocolSession(self.fixture)
        canonical = self.fixture.steps[0].instruction_source_text
        ordinary = (
            (
                "프로토콜을 시작해 줘",
                CuratedProtocolAction.START,
                "개발용 픽스처 1단계 안내를 화면에 표시했습니다.",
            ),
            (
                "현재 단계 알려줘",
                CuratedProtocolAction.CURRENT,
                "현재 1단계입니다. 안내를 화면에 표시했습니다.",
            ),
            (
                "다시 말해줘",
                CuratedProtocolAction.REPEAT,
                "현재 1단계 안내를 다시 표시했습니다.",
            ),
        )
        opening = None
        for turn_id, (transcript, action, speech) in enumerate(ordinary, 1):
            plan = session.plan(transcript, turn_id=turn_id, language="ko")
            if opening is None:
                opening = session.state()
            self.assertEqual(plan.action, action)
            self.assertIn(canonical, plan.display_text)
            self.assertEqual(plan.speech_text, speech)
            self.assertEqual(
                plan.speech_mode,
                CuratedProtocolSpeechMode.CONTROL,
            )
            self.assertNotIn(canonical, plan.speech_text)
            self.assertIsNone(plan.critical_warning_text)
        self.assertEqual(session.state(), opening)

        resume = session.plan("프로토콜 시작", turn_id=4, language="ko")
        self.assertEqual(resume.action, CuratedProtocolAction.START)
        self.assertEqual(
            resume.speech_text,
            "개발용 픽스처 1단계 안내를 화면에 다시 표시했습니다.",
        )
        self.assertFalse(resume.state_changed)
        self.assertEqual(session.state(), opening)

        for offset, transcript in enumerate((
            "전체 내용을 읽어줘",
            "현재 단계 전체를 읽어줘",
            "상세 내용을 읽어줘",
            "현재 단계 상세 내용을 읽어줘",
        ), 10):
            with self.subTest(transcript=transcript):
                state = session.state()
                detail = session.plan(
                    transcript,
                    turn_id=offset,
                    language="ko",
                )
                self.assertEqual(
                    detail.action,
                    CuratedProtocolAction.FULL_DETAIL,
                )
                self.assertEqual(detail.display_text, canonical)
                self.assertEqual(detail.speech_text, canonical)
                self.assertEqual(
                    detail.speech_mode,
                    CuratedProtocolSpeechMode.FULL_DETAIL,
                )
                self.assertEqual(session.state(), state)

        inactive_detail = CuratedProtocolSession(self.fixture).plan(
            "현재 단계 전체를 읽어줘",
            turn_id=99,
            language="ko",
        )
        self.assertEqual(
            inactive_detail.action,
            CuratedProtocolAction.INACTIVE,
        )
        self.assertEqual(
            inactive_detail.speech_mode,
            CuratedProtocolSpeechMode.BLOCKED,
        )

    def test_generic_fixture_warning_is_not_inferred_as_critical(self):
        warning = self.fixture.steps[0].warnings[0]
        self.assertEqual(
            set(warning.__dataclass_fields__),
            {"statement_id", "source_text", "evidence"},
        )
        session = CuratedProtocolSession(self.fixture)
        start = session.plan(
            "프로토콜을 시작해 줘",
            turn_id=1,
            language="ko",
        )
        self.assertIsNone(start.critical_warning_text)
        self.assertNotIn(warning.source_text, start.speech_text)

    def test_all_reviewed_korean_command_forms_are_deterministic(self):
        cases = (
            (
                CuratedProtocolAction.START,
                ("프로토콜을 시작해 줘",),
            ),
            (
                CuratedProtocolAction.CURRENT,
                ("현재 단계를 알려줘", "현재 단계 알려줘"),
            ),
            (
                CuratedProtocolAction.REPEAT,
                ("다시 말해 줘", "다시 말해줘"),
            ),
            (
                CuratedProtocolAction.NEXT,
                (
                    "다음 단계로 넘어가 줘",
                    "다음 단계로 넘어가죠",
                    "단계로 넘어가죠",
                    "다음 단계를 진행해 줘",
                    "다음 단계를 진행해줘",
                    "다음 단계를 진행해 주세요",
                    "다음 단계를 진행해주세요",
                    "다음 단계로 진행해 줘",
                    "다음 단계로 진행해줘",
                    "다음 단계로 진행해 주세요",
                    "다음 단계로 진행해주세요",
                    "다음 단계 진행해 줘",
                    "다음 단계 진행해줘",
                ),
            ),
            (
                CuratedProtocolAction.STOP,
                (
                    "프로토콜을 종료해 줘", "프로토콜 종료해줘",
                    "중지해 줘", "중지해줘", "중지해 주세요", "중지해주세요",
                    "프로토콜을 중지해 줘", "프로토콜을 중지해줘",
                    "절차를 중지해 줘", "절차를 중지해줘",
                ),
            ),
        )
        turn_id = 0
        for action, transcripts in cases:
            for transcript in transcripts:
                with self.subTest(action=action, transcript=transcript):
                    turn_id += 1
                    session = CuratedProtocolSession(self.fixture)
                    if action is not CuratedProtocolAction.START:
                        session.active = True
                    plan = session.plan(
                        transcript,
                        turn_id=turn_id,
                        language="ko",
                    )
                    self.assertEqual(plan.action, action)
                    if action is CuratedProtocolAction.NEXT:
                        self.assertEqual(session.current_index, 1)
                        self.assertEqual(
                            session.plan(
                                transcript,
                                turn_id=turn_id,
                                language="ko",
                            ),
                            plan,
                        )
                        self.assertEqual(session.current_index, 1)

    def test_next_and_stop_commands_remain_anchored_against_questions(self):
        next_questions = (
            "다음 단계 내용이 뭐야?",
            "다음 단계로 가면 위험해?",
            "다음 단계 진행 조건을 설명해줘",
            "다음 단계를 완료했다고 기록해 줘",
            "7단계에서 왜 진행할 수 없어?",
            "다음 단계가 승인됐어?",
        )
        stop_questions = (
            "중지 조건을 알려줘",
            "왜 중지해야 해?",
            "중지하지 말아 줘",
            "중지된 단계가 뭐야?",
            "프로토콜 중지 기능을 설명해줘",
        )
        for turn_id, transcript in enumerate(
            next_questions + stop_questions, 1
        ):
            with self.subTest(transcript=transcript):
                session = CuratedProtocolSession(self.fixture)
                session.active = True
                opening = session.state()
                plan = session.plan(
                    transcript, turn_id=turn_id, language="ko"
                )
                self.assertEqual(plan.action, CuratedProtocolAction.UNSUPPORTED)
                self.assertEqual(session.state(), opening)

    def test_real_fixture_binding_does_not_share_listener_state(self):
        first = ListenerSession()
        second = ListenerSession()
        first.set_curated_protocol_fixture(self.fixture)
        second.set_curated_protocol_fixture(self.fixture)
        self.assertIsNot(
            first.curated_protocol_session,
            second.curated_protocol_session,
        )
        first.curated_protocol_session.plan(
            "프로토콜을 시작해 줘", turn_id=1, language="ko"
        )
        first.curated_protocol_session.plan(
            "다음 단계로 넘어가 줘", turn_id=2, language="ko"
        )
        self.assertEqual(first.curated_protocol_session.current_index, 1)
        self.assertEqual(second.curated_protocol_session.current_index, 0)
        self.assertFalse(second.curated_protocol_session.active)

    def test_final_step_never_advances_and_empty_state_never_passes(self):
        session = CuratedProtocolSession(self.fixture)
        session.active = True
        session.current_index = 24
        final = session.plan("다음", turn_id=1, language="ko")
        self.assertEqual(session.state()["current_step_label"], "25")
        self.assertTrue(final.final_step)
        self.assertFalse(final.state_changed)
        inactive = CuratedProtocolSession(self.fixture)
        result = inactive.plan("현재 단계", turn_id=1, language="ko")
        self.assertEqual(result.action, CuratedProtocolAction.INACTIVE)
        self.assertFalse(inactive.state()["active"])

    def test_readiness_blockers_prevent_server_owned_advance(self):
        by_label = {
            step.source_label: index
            for index, step in enumerate(self.fixture.steps)
        }
        for turn_id, label in enumerate(("7", "9", "20"), 1):
            with self.subTest(label=label):
                session = CuratedProtocolSession(self.fixture)
                session.active = True
                session.current_index = by_label[label]

                blocked = session.plan("next", turn_id=turn_id, language="en")

                self.assertEqual(
                    session.state()["current_step_label"],
                    label,
                )
                self.assertFalse(blocked.state_changed)
                self.assertIn("has not been marked complete", blocked.response_text)
                self.assertIn(
                    session.state()["block_reason"],
                    ("unsupported_repeat_until", "unresolved_ambiguity"),
                )

    def test_supported_question_has_only_current_context_and_unsupported_is_bounded(self):
        session = CuratedProtocolSession(self.fixture)
        session.active = True
        session.current_index = 2
        supported = session.plan("현재 온도는?", turn_id=1, language="ko")
        self.assertEqual(supported.action, CuratedProtocolAction.QUESTION)
        self.assertEqual(supported.fact_id, "current_step")
        self.assertEqual(len(supported.facts), 1)
        self.assertEqual(supported.display_text, supported.facts[0].text)
        self.assertEqual(supported.speech_text, supported.facts[0].text)
        self.assertEqual(
            supported.speech_mode,
            CuratedProtocolSpeechMode.VERIFIED_FACT,
        )
        unrelated = self.fixture.steps[3].instruction_source_text
        self.assertNotIn(unrelated, "\n".join(fact.text for fact in supported.facts))
        unsupported = session.plan("달의 질량은?", turn_id=2, language="ko")
        self.assertEqual(unsupported.action, CuratedProtocolAction.UNSUPPORTED)
        self.assertIn("허용된 답변이 없습니다", unsupported.response_text)
        self.assertEqual(unsupported.display_text, unsupported.speech_text)
        self.assertEqual(
            unsupported.speech_mode,
            CuratedProtocolSpeechMode.BLOCKED,
        )
        self.assertIsNone(unsupported.fact_id)

    def test_supported_question_routing_uses_server_authored_fact_kinds(self):
        cases = (
            (0, "주의 사항은?", "warning"),
            (0, "준비 사항은?", "prerequisite"),
            (4, "필요한 재료는?", "material"),
            (2, "사용할 장비는?", "equipment"),
            (6, "예상 결과는?", "expected_result"),
        )
        for turn_id, (index, transcript, expected_kind) in enumerate(cases, 1):
            with self.subTest(expected_kind=expected_kind):
                session = CuratedProtocolSession(self.fixture)
                session.active = True
                session.current_index = index

                plan = session.plan(
                    transcript,
                    turn_id=turn_id,
                    language="ko",
                )

                self.assertEqual(plan.action, CuratedProtocolAction.QUESTION)
                self.assertEqual(len(plan.facts), 1)
                self.assertEqual(plan.facts[0].kind, expected_kind)
                self.assertEqual(plan.fact_id, plan.facts[0].fact_id)

    def test_commands_precede_fact_selection_and_solution_a_is_step_scoped(self):
        session = CuratedProtocolSession(self.fixture)
        session.active = True
        reviewed_commands = (
            "프로토콜을 시작해 줘",
            "현재 단계를 알려줘",
            "현재 단계 알려줘",
            "다시 말해 줘",
            "다시 말해줘",
            "다음 단계로 넘어가 줘",
            "다음 단계로 넘어가죠",
            "단계로 넘어가죠",
            "프로토콜을 종료해 줘",
            "프로토콜 종료해줘",
        )
        with patch(
            "voice_workflow_agent.curated_protocol._select_verified_fact",
            side_effect=AssertionError("commands must precede fact selection"),
        ):
            for turn_id, transcript in enumerate(reviewed_commands, 1):
                with self.subTest(transcript=transcript):
                    candidate = CuratedProtocolSession(self.fixture)
                    candidate.active = True
                    plan = candidate.plan(
                        transcript,
                        turn_id=turn_id,
                        language="ko",
                    )
                    self.assertIsNot(
                        plan.action,
                        CuratedProtocolAction.QUESTION,
                    )

        session.current_index = 1
        solution = session.plan(
            "용액 A는 어떻게 준비해?",
            turn_id=20,
            language="ko",
        )
        self.assertEqual(solution.action, CuratedProtocolAction.QUESTION)
        self.assertEqual(solution.fact_id, "current_step")
        self.assertEqual(solution.facts, (
            next(
                fact for fact in self.fixture.facts_for_step(1)
                if fact.fact_id == "current_step"
            ),
        ))
        self.assertIn(solution.facts[0].text, solution.response_text)

        session.current_index = 0
        unavailable = session.plan(
            "용액 A는 어떻게 준비해?",
            turn_id=21,
            language="ko",
        )
        self.assertEqual(unavailable.action, CuratedProtocolAction.UNSUPPORTED)
        self.assertIn("현재 단계", unavailable.response_text)
        self.assertNotIn(solution.facts[0].text, unavailable.response_text)

    def test_ambiguous_or_future_step_facts_fail_closed_without_mutation(self):
        session = CuratedProtocolSession(self.fixture)
        session.active = True
        session.current_index = 1
        opening = session.state()

        ambiguous = session.plan(
            "필요한 재료는?",
            turn_id=1,
            language="ko",
        )
        future = session.plan(
            "사용할 장비는?",
            turn_id=2,
            language="ko",
        )

        self.assertEqual(ambiguous.action, CuratedProtocolAction.UNSUPPORTED)
        self.assertEqual(future.action, CuratedProtocolAction.UNSUPPORTED)
        self.assertEqual(session.state(), opening)
        inactive = CuratedProtocolSession(self.fixture).plan(
            "현재 온도는?",
            turn_id=3,
            language="ko",
        )
        self.assertEqual(inactive.action, CuratedProtocolAction.INACTIVE)
        self.assertIn("시작해 주세요", inactive.response_text)


class CuratedProtocolBrainTests(unittest.TestCase):
    def test_fact_selector_is_strict_tool_free_and_returns_only_supplied_text(self):
        client = RecordingClient("temperature")
        answer = asyncio.run(select_curated_protocol_answer(
            client,
            "온도는?",
            language="ko",
            protocol_id="fixture",
            protocol_title="Development fixture",
            step_label="3",
            facts=(("temperature", "step", "37°C"),),
        ))
        self.assertEqual((answer.fact_id, answer.text), ("temperature", "37°C"))
        call = client.chat.completions.calls[0]
        self.assertEqual(call["temperature"], 0)
        self.assertEqual(call["response_format"]["type"], "json_schema")
        self.assertTrue(call["response_format"]["json_schema"]["strict"])
        self.assertNotIn("tools", call)
        rendered = json.dumps(call["messages"], ensure_ascii=False)
        self.assertNotIn("source_excerpt", rendered)
        self.assertNotIn("pdf_sha256", rendered)

    def test_fact_selector_rejects_invalid_and_preserves_unsupported_selection(self):
        with self.assertRaises(RuntimeError):
            asyncio.run(select_curated_protocol_answer(
                RecordingClient("unknown"),
                "온도는?",
                language="ko",
                protocol_id="fixture",
                protocol_title="Development fixture",
                step_label="3",
                facts=(("temperature", "step", "37°C"),),
            ))
        unsupported = asyncio.run(select_curated_protocol_answer(
            RecordingClient("unsupported"),
            "온도는?",
            language="ko",
            protocol_id="fixture",
            protocol_title="Development fixture",
            step_label="3",
            facts=(("temperature", "step", "37°C"),),
        ))
        self.assertEqual((unsupported.fact_id, unsupported.text), ("unsupported", ""))


class CuratedProtocolServerCascadeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = load_curated_protocol_fixture(FIXTURE, PROVENANCE, SOURCE_PDF)

    def make_session(self, index: int = 2) -> ListenerSession:
        context = ToolContext(Path("/unused/offline-catalog"), None, "ko", "test_only")
        workflow = CuratedProtocolSession(self.fixture)
        workflow.active = True
        workflow.current_index = index
        session = ListenerSession(
            tool_context=context,
            curated_protocol_session=workflow,
        )
        session.active = True
        session.active_turn_id = 1
        session.next_turn_id = 2
        session.turn_generations[1] = session.generation
        session.accept_configuration(
            41,"cascade","ko",self.fixture.protocol_id)
        session.detector.state = TurnState.PROCESSING
        return session

    def accept_barge_in(self, session: ListenerSession):
        decisions = iter(
            [False, True, True, True, True, False]
            + [True] * 8
            + [False] * session.detector.config.endpoint_silence_frames
        )
        session._interrupt_detector = EndpointDetector(
            session.detector.config,
            classifier=lambda _: next(decisions),
        )
        frame_count = 14 + session.detector.config.endpoint_silence_frames
        return session.accept_chunk(b"\0" * FRAME_BYTES * frame_count)

    def assert_no_accepted_curated_events(self, socket: Socket) -> None:
        event_types = {item["type"] for item in socket.text}
        self.assertNotIn("protocol.fixture.state", event_types)
        self.assertNotIn("reply.delta", event_types)
        self.assertNotIn("reply.complete", event_types)
        self.assertNotIn("turn.done", event_types)

    def run_question(
        self,
        *,
        client: RecordingClient | None = None,
        tts_side_effect=None,
        transcript: str = "이 작업의 온도는?",
        index: int = 2,
    ):
        session = self.make_session(index=index)
        socket = Socket()
        client = client or RecordingClient("current_step")
        async def immediate(function, *args, **kwargs):
            return function(*args, **kwargs)
        with patch(
            "voice_workflow_agent.server.transcribe",
            return_value=Transcription(transcript, "ko"),
        ) as stt, patch(
            "voice_workflow_agent.server.synthesize",
            return_value=b"\0\0",
            side_effect=tts_side_effect,
        ) as tts, patch(
            "voice_workflow_agent.server.AsyncOpenAI",
            return_value=client,
        ) as llm, patch(
            "voice_workflow_agent.server.require_env",
            side_effect=AssertionError("Provider configuration must not be read"),
        ), patch(
            "voice_workflow_agent.server.asyncio.to_thread",
            side_effect=immediate,
        ), patch(
            "voice_workflow_agent.experiment_protocol_analysis.save_protocol_analysis",
            side_effect=AssertionError("persistence is forbidden"),
        ):
            asyncio.run(run_turn(socket, session, b"\0\0", 1, 1))
        return session, socket, client, stt, tts, llm

    def test_progressive_curated_turn_completes_only_after_playback_ack(self):
        session=self.make_session(index=0)
        socket=Socket()
        listening=session.advance_turn_progress(
            1,session.generation,"listening")
        socket.text.append({"type":"turn.state",**listening})

        async def immediate(function,*args,**kwargs):
            return function(*args,**kwargs)

        with patch(
            "voice_workflow_agent.server.transcribe",
            return_value=Transcription("현재 단계 알려줘","ko"),
        ), patch(
            "voice_workflow_agent.server.synthesize",return_value=b"\0\0",
        ) as tts, patch(
            "voice_workflow_agent.server.asyncio.to_thread",
            side_effect=immediate,
        ), patch(
            "voice_workflow_agent.server.AsyncOpenAI",
            side_effect=AssertionError("LLM must not run"),
        ):
            asyncio.run(run_turn(
                socket,session,b"\0\0",1,1,clock=lambda:100.0))

        states=[item["state"] for item in socket.text
                if item["type"]=="turn.state"]
        self.assertEqual(states,[
            "listening","transcribing","routing","checking_protocol",
            "synthesizing","playing",
        ])
        revisions=[item["revision"] for item in socket.text
                   if item["type"]=="turn.state"]
        self.assertEqual(revisions,list(range(1,len(revisions)+1)))
        self.assertNotIn("complete",states)
        self.assertIn("reply.complete",[item["type"] for item in socket.text])
        self.assertIn("turn.done",[item["type"] for item in socket.text])
        self.assertEqual(
            tts.call_args.args[0],
            "현재 1단계입니다. 안내를 화면에 표시했습니다.")
        display=next(item["text"] for item in socket.text
                     if item["type"]=="reply.delta")
        self.assertIn(self.fixture.steps[0].instruction_source_text,display)
        self.assertNotEqual(display,tts.call_args.args[0])

        self.assertTrue(session.playback_ended(1))
        terminal=session.advance_turn_progress(
            1,session.generation,
            session.turn_terminal_outcome(1,session.generation),
            timings_ms={"playback_completion":0},
        )
        self.assertEqual((terminal["state"],terminal["revision"]),("complete",7))
        self.assertIsNone(session.advance_turn_progress(
            1,session.generation,"error"))

    def test_fresh_configured_proceed_uses_full_turn_ledger_and_step_one(self):
        session = self.make_session(index=0)
        session.curated_protocol_session = CuratedProtocolSession(self.fixture)
        session.curated_protocol_session.activate_configured()
        socket = Socket()
        listening = session.advance_turn_progress(
            1, session.generation, "listening"
        )
        socket.text.append({"type": "turn.state", **listening})

        async def immediate(function, *args, **kwargs):
            return function(*args, **kwargs)

        with patch(
            "voice_workflow_agent.server.transcribe",
            return_value=Transcription("실험을 진행해 줘.", "ko"),
        ), patch(
            "voice_workflow_agent.server.synthesize",
            return_value=b"\0\0",
        ) as tts, patch(
            "voice_workflow_agent.server.AsyncOpenAI",
            side_effect=AssertionError("LLM must not run"),
        ), patch(
            "voice_workflow_agent.server.require_env",
            side_effect=AssertionError("Provider configuration must not be read"),
        ), patch(
            "voice_workflow_agent.server.asyncio.to_thread",
            side_effect=immediate,
        ):
            asyncio.run(run_turn(socket, session, b"\0\0", 1, 1))

        self.assertEqual(
            [
                item["state"]
                for item in socket.text
                if item["type"] == "turn.state"
            ],
            [
                "listening",
                "transcribing",
                "routing",
                "checking_protocol",
                "synthesizing",
                "playing",
            ],
        )
        self.assertEqual(session.curated_protocol_session.current_index, 0)
        self.assertTrue(session.curated_protocol_session.active)
        done = next(item for item in socket.text if item["type"] == "turn.done")
        self.assertEqual((done["route"], done["result_kind"]), (
            "curated_protocol",
            "start",
        ))
        display = next(
            item["text"] for item in socket.text if item["type"] == "reply.delta"
        )
        self.assertIn(self.fixture.steps[0].instruction_source_text, display)
        self.assertEqual(
            tts.call_args.args[0],
            "개발용 픽스처 1단계 안내를 화면에 표시했습니다.",
        )
        self.assertNotIn("검증된 개발용 픽스처", display)
        self.assertTrue(session.playback_ended(1))
        terminal = session.advance_turn_progress(
            1,
            session.generation,
            session.turn_terminal_outcome(1, session.generation),
        )
        self.assertEqual(terminal["state"], "complete")

    def test_live_unspaced_start_transcripts_use_complete_curated_route(self):
        transcripts = (
            "실험을 진행해줘.",
            "프로토콜을 진행해줘",
            "프로토콜을 시작해줘",
            "프로토콜을 시작해줘.",
        )

        async def immediate(function, *args, **kwargs):
            return function(*args, **kwargs)

        for transcript in transcripts:
            with self.subTest(transcript=transcript):
                session = self.make_session(index=0)
                session.curated_protocol_session = CuratedProtocolSession(
                    self.fixture
                )
                session.curated_protocol_session.activate_configured()
                socket = Socket()
                listening = session.advance_turn_progress(
                    1, session.generation, "listening"
                )
                socket.text.append({"type": "turn.state", **listening})
                with patch(
                    "voice_workflow_agent.server.transcribe",
                    return_value=Transcription(transcript, "ko"),
                ), patch(
                    "voice_workflow_agent.server.synthesize",
                    return_value=b"\0\0",
                ), patch(
                    "voice_workflow_agent.server.AsyncOpenAI",
                    side_effect=AssertionError("LLM must not run"),
                ), patch(
                    "voice_workflow_agent.server.asyncio.to_thread",
                    side_effect=immediate,
                ):
                    asyncio.run(run_turn(socket, session, b"\0\0", 1, 1))
                done = next(
                    item for item in socket.text if item["type"] == "turn.done"
                )
                self.assertEqual(
                    (done["route"], done["result_kind"]),
                    ("curated_protocol", "start"),
                )
                self.assertEqual(session.curated_protocol_session.current_index, 0)
                self.assertNotIn(
                    "현재 단계에는 이 질문에 대해 허용된 답변이 없습니다.",
                    next(
                        item["text"] for item in socket.text
                        if item["type"] == "reply.delta"
                    ),
                )

    def test_live_next_and_stop_transcripts_use_deterministic_curated_route(self):
        next_session, next_socket, next_client, _, next_tts, _ = (
            self.run_question(
                transcript="다음 단계를 진행해 줘.", index=0
            )
        )
        next_done = next(
            item for item in next_socket.text if item["type"] == "turn.done"
        )
        next_reply = next(
            item["text"] for item in next_socket.text
            if item["type"] == "reply.delta"
        )
        self.assertEqual(
            (next_done["route"], next_done["result_kind"]),
            ("curated_protocol", "next"),
        )
        self.assertEqual(next_session.curated_protocol_session.current_index, 1)
        self.assertTrue(next_session.curated_protocol_session.active)
        self.assertNotIn("허용된 답변이 없습니다", next_reply)
        self.assertIn(self.fixture.steps[1].instruction_source_text, next_reply)
        self.assertEqual(
            next_tts.call_args.args[0],
            "2단계로 이동했습니다. 안내를 화면에 표시했습니다.",
        )
        self.assertEqual(next_client.chat.completions.calls, [])
        self.assertTrue(next_session.playback_ended(1))
        next_terminal = next_session.advance_turn_progress(
            1, next_session.generation,
            next_session.turn_terminal_outcome(1, next_session.generation),
        )
        self.assertEqual(next_terminal["state"], "complete")

        stop_session, stop_socket, stop_client, _, _, _ = self.run_question(
            transcript="중지해 줘."
        )
        stop_done = next(
            item for item in stop_socket.text if item["type"] == "turn.done"
        )
        stop_reply = next(
            item["text"] for item in stop_socket.text
            if item["type"] == "reply.delta"
        )
        self.assertEqual(
            (stop_done["route"], stop_done["result_kind"]),
            ("curated_protocol", "stop"),
        )
        self.assertFalse(stop_session.curated_protocol_session.active)
        self.assertTrue(stop_session.active)
        self.assertNotIn("허용된 답변이 없습니다", stop_reply)
        self.assertNotIn("completed", stop_session.curated_protocol_session.state())
        self.assertEqual(stop_client.chat.completions.calls, [])
        self.assertTrue(stop_session.playback_ended(1))
        stop_terminal = stop_session.advance_turn_progress(
            1, stop_session.generation,
            stop_session.turn_terminal_outcome(1, stop_session.generation),
        )
        self.assertEqual(stop_terminal["state"], "complete")

    def test_subthreshold_playback_candidate_preserves_curated_checkpoint(self):
        session = self.make_session(index=0)
        workflow = session.curated_protocol_session
        opening_state = workflow.state()
        opening_generation = session.generation
        opening_turn = session.active_turn_id
        self.assertTrue(session.start_playback(1))
        decisions = iter([True] * 11 + [False] * 4)
        playback_config = session._new_interrupt_detector(
            playback=True
        ).config
        session._interrupt_detector = EndpointDetector(
            playback_config, classifier=lambda _: next(decisions)
        )

        events = session.accept_chunk(b"\0" * FRAME_BYTES * 15)

        self.assertEqual(events, [])
        self.assertEqual(workflow.state(), opening_state)
        self.assertEqual(session.generation, opening_generation)
        self.assertEqual(session.active_turn_id, opening_turn)
        self.assertEqual(session.next_turn_id, 2)
        self.assertTrue(session.playback_ended(1))
        self.assertEqual(workflow.state(), opening_state)

    def test_blocked_curated_turn_delivers_then_terminates_blocked(self):
        session=self.make_session(index=6)
        socket=Socket()

        async def immediate(function,*args,**kwargs):
            return function(*args,**kwargs)

        with patch(
            "voice_workflow_agent.server.transcribe",
            return_value=Transcription("다음 단계로 넘어가 줘","ko"),
        ), patch(
            "voice_workflow_agent.server.synthesize",return_value=b"\0\0",
        ), patch(
            "voice_workflow_agent.server.asyncio.to_thread",
            side_effect=immediate,
        ), patch(
            "voice_workflow_agent.server.AsyncOpenAI",
            side_effect=AssertionError("LLM must not run"),
        ):
            asyncio.run(run_turn(socket,session,b"\0\0",1,1))

        self.assertEqual(session.curated_protocol_session.current_index,6)
        self.assertEqual(
            session.turn_terminal_outcome(1,session.generation),"blocked")
        self.assertEqual(
            [item["state"] for item in socket.text
             if item["type"]=="turn.state"][-1],"playing")
        self.assertTrue(session.playback_ended(1))
        terminal=session.advance_turn_progress(
            1,session.generation,"blocked")
        self.assertEqual(terminal["state"],"blocked")
        self.assertIsNone(session.advance_turn_progress(
            1,session.generation,"complete"))

    def test_turn_progress_is_generation_scoped_and_fail_closed(self):
        first=self.make_session(index=0)
        second=self.make_session(index=0)
        self.assertEqual(
            first.advance_turn_progress(1,0,"listening")["revision"],1)
        self.assertIsNone(first.advance_turn_progress(1,0,"listening"))
        self.assertIsNone(first.advance_turn_progress(1,1,"transcribing"))
        self.assertEqual(
            second.advance_turn_progress(1,0,"transcribing")["revision"],1)
        self.assertEqual(first.turn_progress[(1,0)].state,"listening")
        self.assertEqual(second.turn_progress[(1,0)].state,"transcribing")
        self.assertIsNone(first.advance_turn_progress(
            1,0,"transcribing",route="raw_internal_function"))
        self.assertIsNone(first.advance_turn_progress(
            1,0,"transcribing",timings_ms={"elapsed":float("nan")}))

    def test_real_turn_failure_is_sanitized_terminal_error(self):
        session=self.make_session(index=0)
        socket=Socket()

        async def immediate(function,*args,**kwargs):
            return function(*args,**kwargs)

        with patch(
            "voice_workflow_agent.server.transcribe",
            return_value=Transcription("다음","ko"),
        ), patch(
            "voice_workflow_agent.server.synthesize",
            side_effect=RuntimeError("private synthetic detail"),
        ), patch(
            "voice_workflow_agent.server.asyncio.to_thread",
            side_effect=immediate,
        ), patch(
            "voice_workflow_agent.server.log.exception",
        ):
            asyncio.run(run_turn_safely(socket,session,b"\0\0",1,1))
        self.assertEqual(session.curated_protocol_session.current_index,0)
        terminal=[item for item in socket.text
                  if item["type"]=="turn.state"][-1]
        self.assertEqual(terminal["state"],"error")
        public_error=next(item for item in socket.text
                          if item["type"]=="error")
        self.assertEqual(public_error["message"],"voice turn processing failed")
        self.assertNotIn("private synthetic detail",json.dumps(
            socket.text,ensure_ascii=False))

    def test_complete_in_process_question_is_deterministic_and_current_step_only(self):
        session, socket, client, stt, tts, llm = self.run_question()
        stt.assert_called_once()
        llm.assert_not_called()
        self.assertEqual(client.chat.completions.calls, [])
        tts.assert_called_once()
        self.assertEqual(
            tts.call_args.args[0],
            self.fixture.steps[2].instruction_source_text,
        )
        self.assertTrue(socket.binary)
        display = next(
            item for item in socket.text if item["type"] == "reply.delta"
        )
        self.assertEqual(display["text"], self.fixture.steps[2].instruction_source_text)
        done = next(item for item in socket.text if item["type"] == "turn.done")
        self.assertEqual(done["route"], "curated_protocol")
        self.assertEqual(done["result_kind"], "question")
        self.assertEqual(done["fact_id"], "current_step")
        self.assertEqual(done["speech_mode"], "verified_fact")
        self.assertFalse(done["critical_warning_present"])
        self.assertEqual(done["tools_used"], [])
        self.assertEqual(session.curated_protocol_session.current_index, 2)

    def test_unsupported_question_returns_only_bounded_server_response(self):
        session, socket, client, _, tts, llm = self.run_question(
            client=RecordingClient(error=AssertionError("LLM must not run")),
            transcript="달의 질량은?",
        )
        spoken = tts.call_args.args[0]
        llm.assert_not_called()
        self.assertEqual(client.chat.completions.calls, [])
        self.assertIn("허용된 답변이 없습니다", spoken)
        self.assertNotIn(self.fixture.steps[2].instruction_source_text, spoken)
        done = next(item for item in socket.text if item["type"] == "turn.done")
        self.assertEqual(done["result_kind"], "unsupported")
        self.assertIsNone(done["fact_id"])
        self.assertEqual(session.curated_protocol_session.current_index, 2)

    def test_stt_failure_or_empty_transcript_prevents_llm_tts_and_state_change(self):
        for mode in ("failure", "empty"):
            with self.subTest(mode=mode):
                session = self.make_session()
                socket = Socket()
                client = RecordingClient()
                transcription = (
                    RuntimeError("offline STT failure")
                    if mode == "failure"
                    else Transcription("", "ko")
                )
                async def immediate(function, *args, **kwargs):
                    return function(*args, **kwargs)
                with patch(
                    "voice_workflow_agent.server.transcribe",
                    side_effect=transcription if isinstance(transcription, Exception) else None,
                    return_value=transcription if not isinstance(transcription, Exception) else None,
                ), patch(
                    "voice_workflow_agent.server.synthesize",
                ) as tts, patch(
                    "voice_workflow_agent.server.AsyncOpenAI",
                    return_value=client,
                ), patch(
                    "voice_workflow_agent.server.asyncio.to_thread",
                    side_effect=immediate,
                ):
                    if mode == "failure":
                        with self.assertRaises(RuntimeError):
                            asyncio.run(run_turn(socket, session, b"\0\0", 1, 1))
                    else:
                        asyncio.run(run_turn(socket, session, b"\0\0", 1, 1))
                self.assertEqual(client.chat.completions.calls, [])
                tts.assert_not_called()
                self.assertEqual(session.curated_protocol_session.current_index, 2)

    def test_verified_fact_question_never_constructs_or_calls_an_llm(self):
        client = RecordingClient(error=AssertionError("LLM must not run"))
        session, _, _, _, tts, llm = self.run_question(client=client)
        llm.assert_not_called()
        self.assertEqual(client.chat.completions.calls, [])
        tts.assert_called_once()
        self.assertEqual(session.curated_protocol_session.current_index, 2)

    def test_tts_failure_is_reported_without_corrupting_workflow_state(self):
        session = self.make_session()
        socket = Socket()
        client = RecordingClient("current_step")
        async def immediate(function, *args, **kwargs):
            return function(*args, **kwargs)
        with self.assertRaises(RuntimeError):
            with patch(
                "voice_workflow_agent.server.transcribe",
                return_value=Transcription("이 작업의 온도는?", "ko"),
            ), patch(
                "voice_workflow_agent.server.synthesize",
                side_effect=RuntimeError("offline TTS failure"),
            ), patch(
                "voice_workflow_agent.server.AsyncOpenAI",
                return_value=client,
            ), patch(
                "voice_workflow_agent.server.require_env",
                return_value="offline-test",
            ), patch(
                "voice_workflow_agent.server.asyncio.to_thread",
                side_effect=immediate,
            ):
                asyncio.run(run_turn(socket, session, b"\0\0", 1, 1))
        self.assertEqual(session.curated_protocol_session.current_index, 2)
        self.assert_no_accepted_curated_events(socket)

    def test_tts_failure_rolls_back_deterministic_next(self):
        session = self.make_session(index=0)
        socket = Socket()
        client = RecordingClient(error=AssertionError("LLM must not run"))
        async def immediate(function, *args, **kwargs):
            return function(*args, **kwargs)
        with self.assertRaises(RuntimeError):
            with patch(
                "voice_workflow_agent.server.transcribe",
                return_value=Transcription("다음 단계를 진행해 줘.", "ko"),
            ), patch(
                "voice_workflow_agent.server.synthesize",
                side_effect=RuntimeError("offline TTS failure"),
            ), patch(
                "voice_workflow_agent.server.AsyncOpenAI",
                return_value=client,
            ), patch(
                "voice_workflow_agent.server.asyncio.to_thread",
                side_effect=immediate,
            ):
                asyncio.run(run_turn(socket, session, b"\0\0", 1, 1))
        self.assertEqual(client.chat.completions.calls, [])
        self.assertEqual(session.curated_protocol_session.current_index, 0)
        self.assert_no_accepted_curated_events(socket)

    def test_barge_in_before_playable_acceptance_restores_once(self):
        session = self.make_session(index=0)
        socket = Socket()
        synthesis_started = asyncio.Event()

        async def controlled_thread(function, *args, **kwargs):
            if args and isinstance(args[0], str):
                synthesis_started.set()
                await asyncio.Future()
            return function(*args, **kwargs)

        async def scenario():
            workflow = session.curated_protocol_session
            with patch(
                "voice_workflow_agent.server.transcribe",
                return_value=Transcription("다음 단계를 진행해 줘.", "ko"),
            ), patch(
                "voice_workflow_agent.server.synthesize",
                return_value=b"\0\0",
            ), patch(
                "voice_workflow_agent.server.asyncio.to_thread",
                side_effect=controlled_thread,
            ), patch.object(
                workflow, "_restore", wraps=workflow._restore,
            ) as restore:
                task = asyncio.create_task(
                    run_turn(socket, session, b"\0\0", 1, 1))
                await synthesis_started.wait()
                self.assertEqual(workflow.current_index, 1)
                events = self.accept_barge_in(session)
                self.assertEqual(
                    len([item for item in events
                         if item.kind == "assistant.interrupted"]),
                    1,
                )
                await cancel_cascade_generation(
                    socket,session,task,
                    next(item for item in events
                         if item.kind == "assistant.interrupted"))
                await asyncio.sleep(0)
                restore.assert_called_once()
                self.assertEqual(workflow.current_index, 0)
                self.assertFalse(session.start_playback(1))
                self.assertFalse(session.playback_ended(1))
                self.assertNotIn("completed", workflow.state())
                self.assert_no_accepted_curated_events(socket)
                clear=next(item for item in socket.text
                           if item["type"]=="cascade.playback.clear")
                self.assertEqual(clear["state"],"cancelled")
                self.assertGreater(clear["revision"],0)
                for late_state in ("playing","complete","blocked","error"):
                    self.assertIsNone(session.advance_turn_progress(
                        1,clear["generation"],late_state))

        asyncio.run(scenario())

    def test_barge_in_after_playable_acceptance_keeps_navigation_and_new_turn(self):
        session = self.make_session(index=0)
        socket = Socket()

        async def immediate(function, *args, **kwargs):
            return function(*args, **kwargs)

        with patch(
            "voice_workflow_agent.server.transcribe",
            side_effect=[
                Transcription("다음 단계를 진행해 줘.", "ko"),
                Transcription("현재 단계 알려줘", "ko"),
            ],
        ), patch(
            "voice_workflow_agent.server.synthesize",
            return_value=b"\0\0",
        ) as tts, patch(
            "voice_workflow_agent.server.asyncio.to_thread",
            side_effect=immediate,
        ), patch(
            "voice_workflow_agent.server.AsyncOpenAI",
            side_effect=AssertionError("LLM must not run"),
        ), patch(
            "voice_workflow_agent.experiment_protocol_analysis.save_protocol_analysis",
            side_effect=AssertionError("persistence is forbidden"),
        ):
            asyncio.run(run_turn(socket, session, b"\0\0", 1, 1))
            self.assertEqual(session.curated_protocol_session.current_index, 1)
            self.assertEqual(
                tts.call_args_list[0].args[0],
                "2단계로 이동했습니다. 안내를 화면에 표시했습니다.",
            )
            self.assertIn(
                self.fixture.steps[1].instruction_source_text,
                next(item["text"] for item in socket.text
                     if item["type"] == "reply.delta"),
            )
            accepted_state = session.curated_protocol_session.state()
            events = self.accept_barge_in(session)
            interruption = next(
                item for item in events
                if item.kind == "assistant.interrupted"
            )
            end = next(item for item in events if item.kind == "speech.end")
            asyncio.run(cancel_cascade_generation(
                socket,session,None,interruption))
            clear=next(item for item in socket.text
                       if item["type"]=="cascade.playback.clear")
            self.assertEqual(
                (clear["turn_id"],clear["generation"],clear["state"]),
                (interruption.turn_id,interruption.generation,"cancelled"))
            self.assertEqual(
                session.curated_protocol_session.state(), accepted_state)
            self.assertFalse(session.playback_ended(interruption.turn_id))
            asyncio.run(run_turn(
                socket, session, end.result.utterance or b"", end.turn_id,
                end.result.total_frames, end.result.voiced_frames,
            ))
            self.assertEqual(session.curated_protocol_session.current_index, 1)
            self.assertEqual(
                [item["result_kind"] for item in socket.text
                 if item["type"] == "turn.done"],
                ["next", "current"],
            )
            self.assertTrue(session.playback_ended(end.turn_id))
            self.assertIsNone(session.advance_turn_progress(
                interruption.turn_id,interruption.generation,"complete"))
            self.assertNotIn(
                "completed", session.curated_protocol_session.state())

    def test_barge_in_preserves_full_detail_fact_and_stop_semantics(self):
        cases = (
            (2, "현재 단계 전체를 읽어줘", "full_detail", None, True),
            (2, "이 작업의 온도는?", "question", "current_step", True),
            (6, "다음", "next", None, True),
            (2, "프로토콜 종료해줘", "stop", None, False),
        )

        async def immediate(function, *args, **kwargs):
            return function(*args, **kwargs)

        for index, transcript, result_kind, fact_id, protocol_active in cases:
            with self.subTest(result_kind=result_kind):
                session = self.make_session(index=index)
                socket = Socket()
                with patch(
                    "voice_workflow_agent.server.transcribe",
                    return_value=Transcription(transcript, "ko"),
                ), patch(
                    "voice_workflow_agent.server.synthesize",
                    return_value=b"\0\0",
                ), patch(
                    "voice_workflow_agent.server.asyncio.to_thread",
                    side_effect=immediate,
                ), patch(
                    "voice_workflow_agent.server.AsyncOpenAI",
                    side_effect=AssertionError("LLM must not run"),
                ), patch(
                    "voice_workflow_agent.experiment_protocol_analysis.save_protocol_analysis",
                    side_effect=AssertionError("persistence is forbidden"),
                ):
                    asyncio.run(run_turn(
                        socket, session, b"\0\0", 1, 1))
                display = next(
                    item["text"] for item in socket.text
                    if item["type"] == "reply.delta")
                done = next(
                    item for item in socket.text
                    if item["type"] == "turn.done")
                opening_index = session.curated_protocol_session.current_index
                self.accept_barge_in(session)
                self.assertEqual(done["result_kind"], result_kind)
                self.assertEqual(done["fact_id"], fact_id)
                self.assertEqual(
                    next(item["text"] for item in socket.text
                         if item["type"] == "reply.delta"),
                    display,
                )
                self.assertEqual(
                    session.curated_protocol_session.current_index,
                    opening_index,
                )
                self.assertEqual(
                    session.curated_protocol_session.active,
                    protocol_active,
                )
                self.assertTrue(session.active)
                self.assertNotIn(
                    "completed", session.curated_protocol_session.state())

    def test_tts_failure_rolls_back_start_without_visible_active_state(self):
        session = self.make_session(index=0)
        session.curated_protocol_session.active = False
        opening_state = session.curated_protocol_session.state()
        socket = Socket()
        client = RecordingClient(error=AssertionError("LLM must not run"))
        async def immediate(function, *args, **kwargs):
            return function(*args, **kwargs)
        with self.assertRaises(RuntimeError):
            with patch(
                "voice_workflow_agent.server.transcribe",
                return_value=Transcription("프로토콜 시작", "ko"),
            ), patch(
                "voice_workflow_agent.server.synthesize",
                side_effect=RuntimeError("offline TTS failure"),
            ), patch(
                "voice_workflow_agent.server.AsyncOpenAI",
                return_value=client,
            ), patch(
                "voice_workflow_agent.server.asyncio.to_thread",
                side_effect=immediate,
            ):
                asyncio.run(run_turn(socket, session, b"\0\0", 1, 1))
        self.assertEqual(client.chat.completions.calls, [])
        self.assertEqual(session.curated_protocol_session.state(), opening_state)
        self.assert_no_accepted_curated_events(socket)

    def test_tts_failure_rolls_back_stop_without_visible_inactive_state(self):
        session = self.make_session(index=2)
        opening_state = session.curated_protocol_session.state()
        socket = Socket()
        client = RecordingClient(error=AssertionError("LLM must not run"))
        async def immediate(function, *args, **kwargs):
            return function(*args, **kwargs)
        with self.assertRaises(RuntimeError):
            with patch(
                "voice_workflow_agent.server.transcribe",
                return_value=Transcription("중지해 줘.", "ko"),
            ), patch(
                "voice_workflow_agent.server.synthesize",
                side_effect=RuntimeError("offline TTS failure"),
            ), patch(
                "voice_workflow_agent.server.AsyncOpenAI",
                return_value=client,
            ), patch(
                "voice_workflow_agent.server.asyncio.to_thread",
                side_effect=immediate,
            ):
                asyncio.run(run_turn(socket, session, b"\0\0", 1, 1))
        self.assertEqual(client.chat.completions.calls, [])
        self.assertEqual(session.curated_protocol_session.state(), opening_state)
        self.assert_no_accepted_curated_events(socket)

    def test_deterministic_routes_do_not_call_llm_and_next_advances_once(self):
        session = self.make_session(index=0)
        socket = Socket()
        client = RecordingClient(error=AssertionError("LLM must not run"))
        async def immediate(function, *args, **kwargs):
            return function(*args, **kwargs)
        with patch(
            "voice_workflow_agent.server.transcribe",
            return_value=Transcription("다음 단계를 진행해 줘.", "ko"),
        ), patch(
            "voice_workflow_agent.server.synthesize",
            return_value=b"\0\0",
        ) as tts, patch(
            "voice_workflow_agent.server.AsyncOpenAI",
            return_value=client,
        ), patch(
            "voice_workflow_agent.server.asyncio.to_thread",
            side_effect=immediate,
        ):
            asyncio.run(run_turn(socket, session, b"\0\0", 1, 1))
        self.assertEqual(client.chat.completions.calls, [])
        self.assertEqual(session.curated_protocol_session.current_index, 1)
        self.assertEqual(
            tts.call_args.args[0],
            "2단계로 이동했습니다. 안내를 화면에 표시했습니다.",
        )
        closing_state = session.curated_protocol_session.state()
        state_events = [
            item for item in socket.text
            if item["type"] == "protocol.fixture.state"
        ]
        self.assertEqual(len(state_events), 1)
        self.assertEqual(state_events[0]["state"], closing_state)
        self.assertEqual(state_events[0]["action"], "next")
        self.assertEqual(
            len([item for item in socket.text if item["type"] == "reply.delta"]),
            1,
        )
        self.assertIn(
            self.fixture.steps[1].instruction_source_text,
            next(
                item for item in socket.text
                if item["type"] == "reply.delta"
            )["text"],
        )
        self.assertTrue(socket.binary)
        done = next(item for item in socket.text if item["type"] == "turn.done")
        self.assertEqual(done["route"], "curated_protocol")
        self.assertEqual(done["segment_count"], 1)
        self.assertEqual(done["speech_mode"], "control")

    def test_text_driven_curated_sequence_has_exact_server_owned_states(self):
        session = self.make_session(index=0)
        session.curated_protocol_session.active = False
        session.accept_configuration(
            44,
            "cascade",
            "ko",
            self.fixture.protocol_id,
        )
        socket = Socket()
        transcripts = (
            "프로토콜을 시작해 줘",
            "현재 단계 알려줘",
            "주의 사항은?",
            "달의 질량은?",
            "용액 A는 어떻게 준비해?",
            "단계로 넘어가죠",
            "프로토콜 종료해줘",
        )
        expected = (
            ("start", True, "1", 1, None),
            ("current", True, "1", 1, None),
            ("question", True, "1", 1, "warning_1"),
            ("unsupported", True, "1", 1, None),
            ("unsupported", True, "1", 1, None),
            ("next", True, "2", 2, None),
            ("stop", False, None, 3, None),
        )

        async def immediate(function, *args, **kwargs):
            return function(*args, **kwargs)

        with patch(
            "voice_workflow_agent.server.transcribe",
            side_effect=[Transcription(value, "ko") for value in transcripts],
        ), patch(
            "voice_workflow_agent.server.synthesize",
            return_value=b"\0\0",
        ) as tts, patch(
            "voice_workflow_agent.server.AsyncOpenAI",
            side_effect=AssertionError("LLM must not run"),
        ), patch(
            "voice_workflow_agent.server.stream_brain_turn",
            side_effect=AssertionError("brain must not run"),
        ) as brain, patch(
            "voice_workflow_agent.server.require_env",
            side_effect=AssertionError("Provider configuration must not be read"),
        ), patch(
            "voice_workflow_agent.server.asyncio.to_thread",
            side_effect=immediate,
        ), patch(
            "voice_workflow_agent.experiment_protocol_analysis.save_protocol_analysis",
            side_effect=AssertionError("persistence is forbidden"),
        ):
            for turn_id in range(1, len(transcripts) + 1):
                session.active_turn_id = turn_id
                session.detector.state = TurnState.PROCESSING
                asyncio.run(run_turn(socket, session, b"\0\0", turn_id, 1))
                self.assertTrue(session.playback_ended(turn_id))

        brain.assert_not_called()
        states = [
            item for item in socket.text
            if item["type"] == "protocol.fixture.state"
        ]
        self.assertEqual(len(states), len(expected))
        for item, (action, active, label, revision, _) in zip(states, expected):
            with self.subTest(action=action, revision=revision):
                self.assertEqual(item["configuration_id"], 44)
                self.assertEqual(item["action"], action)
                self.assertEqual(item["state"]["active"], active)
                self.assertEqual(item["state"]["current_step_label"], label)
                self.assertEqual(item["state"]["revision"], revision)
                self.assertTrue(item["state"]["development_only"])
                self.assertEqual(
                    item["state"]["readiness_status"],
                    "analysis_required",
                )
        done = [item for item in socket.text if item["type"] == "turn.done"]
        self.assertEqual(len(done), len(expected))
        self.assertEqual({item["route"] for item in done}, {"curated_protocol"})
        self.assertEqual(
            [(item["result_kind"], item["fact_id"]) for item in done],
            [(action, fact_id) for action, _, _, _, fact_id in expected],
        )

    def test_acknowledged_real_fixture_sequence_stays_inside_curated_boundary(self):
        configuration_id = 71
        config = ServerConfig(
            Path("/unused/offline-catalog.sqlite"),
            None,
            "test_only",
            frozenset({"ko"}),
            "ko",
            None,
            None,
            FIXTURE,
            PROVENANCE,
            SOURCE_PDF,
        )
        captured: list[ListenerSession] = []
        original_listener = ListenerSession

        def listener_factory(*args, **kwargs):
            listener = original_listener(*args, **kwargs)
            captured.append(listener)
            return listener

        class HandshakeSocket(Socket):
            def __init__(self):
                super().__init__()
                self.ready = asyncio.Event()
                self.disconnect = asyncio.Event()
                self.receive_count = 0

            async def accept(self):
                return None

            async def receive(self):
                self.receive_count += 1
                if self.receive_count == 1:
                    return {"text": json.dumps({
                        "type": "session.start",
                        "mode": "cascade",
                        "language": "ko",
                        "protocol_id": self_protocol_id,
                        "configuration_id": configuration_id,
                    })}
                await self.disconnect.wait()
                return {"type": "websocket.disconnect", "code": 1000}

            async def send_text(self, value: str) -> None:
                await super().send_text(value)
                if self.text[-1]["type"] == "session.ready":
                    self.ready.set()

        self_protocol_id = self.fixture.protocol_id
        socket = HandshakeSocket()
        transcripts = (
            "프로토콜을 시작해 줘",
            "현재 단계 알려줘",
            "다시 말해줘",
            "단계로 넘어가죠",
            "용액 A는 어떻게 준비해?",
            "현재 단계 전체를 읽어줘",
            "필요한 재료는?",
            "프로토콜 종료해줘",
        )

        async def immediate(function, *args, **kwargs):
            return function(*args, **kwargs)

        async def scenario():
            server_task = asyncio.create_task(voice_socket(socket))
            await asyncio.wait_for(socket.ready.wait(), timeout=5)
            await asyncio.sleep(0)
            session = captured[0]
            for turn_id in range(1, len(transcripts) + 1):
                session.active_turn_id = turn_id
                session.detector.state = TurnState.PROCESSING
                await run_turn(socket, session, b"\0\0", turn_id, 1)
                self.assertTrue(session.playback_ended(turn_id))
            self.assertTrue(session.active)
            self.assertFalse(session.curated_protocol_session.active)
            socket.disconnect.set()
            await server_task

        with patch(
            "voice_workflow_agent.server.server_config",
            return_value=config,
        ), patch(
            "voice_workflow_agent.server.ListenerSession",
            side_effect=listener_factory,
        ), patch(
            "voice_workflow_agent.server.ProcedureStore",
            side_effect=AssertionError("ProcedureStore must not be constructed"),
        ) as procedure_store, patch(
            "voice_workflow_agent.server.load_procedure_definitions",
            side_effect=AssertionError("procedure catalog must not be loaded"),
        ), patch(
            "voice_workflow_agent.server.transcribe",
            side_effect=[Transcription(value, "ko") for value in transcripts],
        ), patch(
            "voice_workflow_agent.server.synthesize",
            return_value=b"\0\0",
        ) as tts, patch(
            "voice_workflow_agent.server.AsyncOpenAI",
            side_effect=AssertionError("LLM must not be constructed"),
        ), patch(
            "voice_workflow_agent.server.stream_brain_turn",
            side_effect=AssertionError("generic brain must not run"),
        ), patch(
            "voice_workflow_agent.server.require_env",
            side_effect=AssertionError("Provider configuration must not be read"),
        ), patch(
            "voice_workflow_agent.server.asyncio.to_thread",
            side_effect=immediate,
        ), patch(
            "voice_workflow_agent.experiment_protocol_analysis.save_protocol_analysis",
            side_effect=AssertionError("persistence is forbidden"),
        ):
            asyncio.run(scenario())

        procedure_store.assert_not_called()
        ready = [item for item in socket.text if item["type"] == "session.ready"]
        self.assertEqual(len(ready), 1)
        self.assertEqual(ready[0]["configuration_id"], configuration_id)
        self.assertEqual(ready[0]["protocol_id"], self.fixture.protocol_id)
        done = [item for item in socket.text if item["type"] == "turn.done"]
        self.assertEqual(
            [(item["result_kind"], item["fact_id"]) for item in done],
            [
                ("start", None),
                ("current", None),
                ("repeat", None),
                ("next", None),
                ("question", "current_step"),
                ("full_detail", None),
                ("unsupported", None),
                ("stop", None),
            ],
        )
        self.assertEqual(
            [item["speech_mode"] for item in done],
            [
                "control",
                "control",
                "control",
                "control",
                "verified_fact",
                "full_detail",
                "blocked",
                "stop",
            ],
        )
        step_one = self.fixture.steps[0].instruction_source_text
        step_two = self.fixture.steps[1].instruction_source_text
        spoken = [call.args[0] for call in tts.call_args_list]
        self.assertEqual(spoken, [
            "개발용 픽스처 1단계 안내를 화면에 표시했습니다.",
            "현재 1단계입니다. 안내를 화면에 표시했습니다.",
            "현재 1단계 안내를 다시 표시했습니다.",
            "2단계로 이동했습니다. 안내를 화면에 표시했습니다.",
            step_two,
            step_two,
            "검증된 개발용 픽스처의 현재 단계에는 이 질문에 대해 허용된 답변이 없습니다.",
            "개발용 픽스처 프로토콜 세션을 종료했습니다.",
        ])
        self.assertNotIn(step_one, spoken[:4])
        self.assertNotIn(step_two, spoken[:4])
        default_control_speech = (*spoken[:4], spoken[-1])
        fixture_facts = (
            *self.fixture.facts_for_step(0),
            *self.fixture.facts_for_step(1),
        )
        for control_text in default_control_speech:
            with self.subTest(control_text=control_text):
                self.assertTrue(all(
                    fact.text not in control_text for fact in fixture_facts
                ))
                lowered = control_text.casefold()
                for forbidden in (
                    "candidate_a_curated_analysis",
                    "source_excerpt",
                    "evidence",
                    "provenance",
                    "/home/",
                    ".json",
                ):
                    self.assertNotIn(forbidden, lowered)
        replies = [
            item["text"] for item in socket.text
            if item["type"] == "reply.delta"
        ]
        for index in (0, 1, 2):
            self.assertIn(step_one, replies[index])
        self.assertIn(step_two, replies[3])
        self.assertEqual(replies[4], step_two)
        self.assertEqual(replies[5], step_two)
        self.assertNotIn(step_two, replies[6])
        self.assertEqual(
            replies[7],
            "개발용 픽스처 프로토콜 세션을 종료했습니다.",
        )
        states = [
            item for item in socket.text
            if item["type"] == "protocol.fixture.state"
        ]
        self.assertEqual(states[0]["action"], "attached")
        self.assertEqual(
            [
                (
                    item["action"],
                    item["state"]["active"],
                    item["state"]["current_step_label"],
                    item["state"]["revision"],
                    item["state"]["block_reason"],
                )
                for item in states[1:]
            ],
            [
                ("start", True, "1", 1, None),
                ("current", True, "1", 1, None),
                ("repeat", True, "1", 1, None),
                ("next", True, "2", 2, None),
                ("question", True, "2", 2, None),
                ("full_detail", True, "2", 2, None),
                ("unsupported", True, "2", 2, None),
                ("stop", False, None, 3, None),
            ],
        )
        for item in states:
            self.assertNotIn("completed", item["state"])
            self.assertNotIn("cycle", item["state"])
        self.assertEqual(states[-1]["state"]["active"], False)
        self.assertEqual(states[-2]["state"]["current_step_label"], "2")

    def test_readiness_blocked_next_is_spoken_without_llm_or_state_change(self):
        session = self.make_session(index=6)
        socket = Socket()
        client = RecordingClient(error=AssertionError("LLM must not run"))
        async def immediate(function, *args, **kwargs):
            return function(*args, **kwargs)
        with patch(
            "voice_workflow_agent.server.transcribe",
            return_value=Transcription("다음", "ko"),
        ), patch(
            "voice_workflow_agent.server.synthesize",
            return_value=b"\0\0",
        ) as tts, patch(
            "voice_workflow_agent.server.AsyncOpenAI",
            return_value=client,
        ), patch(
            "voice_workflow_agent.server.asyncio.to_thread",
            side_effect=immediate,
        ):
            asyncio.run(run_turn(socket, session, b"\0\0", 1, 1))
        self.assertEqual(client.chat.completions.calls, [])
        self.assertEqual(session.curated_protocol_session.current_index, 6)
        self.assertIn("완료 처리되지 않았습니다", tts.call_args.args[0])
        state_events = [
            item for item in socket.text
            if item["type"] == "protocol.fixture.state"
        ]
        self.assertEqual(len(state_events), 1)
        self.assertEqual(
            state_events[0]["state"],
            session.curated_protocol_session.state(),
        )
        self.assertEqual(state_events[0]["action"], "next")
