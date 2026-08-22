"""Offline tests for the development-only curated Protocol voice slice."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from voice_workflow_agent import experiment_protocol as domain
from voice_workflow_agent.audio import FRAME_BYTES, pcm_to_wav
from voice_workflow_agent.brain import (
    BrainResult,
    SentenceSegment,
    answer_curated_protocol_question,
    select_curated_protocol_answer,
)
from voice_workflow_agent.curated_protocol import (
    DEVELOPMENT_FIXTURE_STATUS,
    CuratedProtocolAction,
    CuratedProtocolFixtureError,
    CuratedProtocolSession,
    CuratedProtocolSpeechMode,
    classify_curated_control_intent,
    load_curated_protocol_fixture,
    normalize_scientific_query,
)
from voice_workflow_agent.experiment_protocol_analysis import (
    ANALYSIS_RESPONSE_SCHEMA,
    ProtocolAnalysisEvidenceError,
    ProtocolAnalysisResponseError,
    parse_protocol_analysis_response,
)
from voice_workflow_agent.experiment_reports import ExperimentReportStore
from voice_workflow_agent.external_references import (
    ExternalReferenceSettings,
    SupplementalKnowledgeSettings,
)
from voice_workflow_agent.language import InputLanguagePreference, Transcription
from voice_workflow_agent.multi_brain import MultiBrainSettings
from voice_workflow_agent.server import (
    ListenerSession,
    LockedSender,
    ServerConfig,
    _send_session_greeting,
    _record_experiment_report_plan,
    cancel_cascade_generation,
    get_protocol_source_page,
    get_protocol_visual_asset,
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


class GroundedCompletions:
    def __init__(self, payload: dict, error: Exception | None = None):
        self.payload = payload
        self.error = error
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(
                content=json.dumps(self.payload, ensure_ascii=False)
            )
        )])


class GroundedClient:
    def __init__(self, payload: dict, error: Exception | None = None):
        self.model = "offline-model"
        self.chat = SimpleNamespace(
            completions=GroundedCompletions(payload, error)
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

    def test_persisted_progress_restore_requires_contiguous_exact_revision_steps(self):
        session = CuratedProtocolSession(self.fixture)
        session.restore_experiment_progress(
            current_step_id=self.fixture.steps[2].step_id,
            completed_step_ids=tuple(
                step.step_id for step in self.fixture.steps[:2]
            ),
        )
        self.assertTrue(session.active)
        self.assertEqual(session.current_index, 2)
        self.assertEqual(session.state()["current_step_id"], self.fixture.steps[2].step_id)
        self.assertEqual(session.timer_status()["state"], "not_started")

        with self.assertRaises(CuratedProtocolFixtureError):
            CuratedProtocolSession(self.fixture).restore_experiment_progress(
                current_step_id=self.fixture.steps[2].step_id,
                completed_step_ids=(self.fixture.steps[0].step_id,),
            )
        with self.assertRaises(CuratedProtocolFixtureError):
            CuratedProtocolSession(self.fixture).restore_experiment_progress(
                current_step_id="unknown-step",
                completed_step_ids=(),
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

    def test_verified_visual_manifest_selects_only_source_crops(self):
        step_one = self.fixture.visual_for_step(0)
        step_seven = self.fixture.visual_for_step(6)
        step_nine = self.fixture.visual_for_step(8)
        self.assertIsNone(step_one)
        self.assertEqual((step_seven.kind, step_seven.mime_type), (
            "source_crop", "image/png",
        ))
        self.assertEqual((step_nine.kind, step_nine.mime_type), (
            "source_crop", "image/jpeg",
        ))
        self.assertEqual(step_seven.source_page, 5)
        self.assertEqual(step_nine.source_page, 6)
        self.assertNotIn("candidate-a-step-01", self.fixture.visual_manifest)
        self.assertIn("candidate-a-step-07", self.fixture.visual_manifest)
        for index in (6, 8):
            asset, content = self.fixture.visual_content(index)
            self.assertTrue(content)
            self.assertEqual(hashlib.sha256(content).hexdigest(), asset.sha256)

    def test_candidate_visual_endpoints_serve_exact_assets_and_secondary_pages(self):
        with patch(
            "voice_workflow_agent.server.server_config",
            return_value=SimpleNamespace(),
        ), patch(
            "voice_workflow_agent.server._configured_candidate_fixture",
            return_value=self.fixture,
        ):
            for index in (6, 8):
                asset, content = self.fixture.visual_content(index)
                response = get_protocol_visual_asset(
                    self.fixture.protocol_id,
                    self.fixture.revision_id,
                    asset.asset_id,
                )
                self.assertEqual(response.body, content)
                self.assertEqual(
                    response.headers["x-protocol-visual-kind"], asset.kind
                )
                self.assertEqual(
                    response.headers["x-protocol-asset-sha256"], asset.sha256
                )
            page = get_protocol_source_page(
                self.fixture.protocol_id, self.fixture.revision_id, 5
            )
            self.assertIn(b"Source page 5", page.body)
            with self.assertRaises(Exception) as unknown_page:
                get_protocol_source_page(
                    self.fixture.protocol_id, self.fixture.revision_id, 99
                )
            self.assertEqual(
                getattr(unknown_page.exception, "status_code", None), 404
            )
            with self.assertRaises(Exception) as traversal:
                get_protocol_visual_asset(
                    self.fixture.protocol_id,
                    self.fixture.revision_id,
                    "../source-crop-5-125",
                )
            self.assertEqual(getattr(traversal.exception, "status_code", None), 404)

    def test_localization_is_identity_bound_and_preserves_numeric_units(self):
        self.assertEqual(len(self.fixture.localizations), 36)
        translated = self.fixture.localized_fact(
            "candidate-a-step-03", "current_step"
        )
        for marker in ("500 µL", "Solution A", "37°C", "800 rpm"):
            self.assertIn(marker, translated)
        self.assertEqual(self.fixture.source_pdf_sha256, self.provenance["candidate_sha256"])

    def test_localization_rejects_added_numeric_value_or_wrong_source_identity(self):
        original = json.loads(
            FIXTURE.with_name(
                "candidate_a_curated_analysis.localization.ko.json"
            ).read_text(encoding="utf-8")
        )
        for mutation in ("numeric", "identity"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                fixture = root / FIXTURE.name
                provenance = root / PROVENANCE.name
                localization = root / (
                    "candidate_a_curated_analysis.localization.ko.json"
                )
                fixture.write_bytes(FIXTURE.read_bytes())
                provenance.write_bytes(PROVENANCE.read_bytes())
                payload = copy.deepcopy(original)
                if mutation == "numeric":
                    payload["translations"][
                        "candidate-a-step-03/current_step"
                    ] += " 999 µL"
                else:
                    payload["document_sha256"] = "0" * 64
                localization.write_text(
                    json.dumps(
                        payload,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ) + "\n",
                    encoding="utf-8",
                )
                with self.assertRaises(CuratedProtocolFixtureError):
                    load_curated_protocol_fixture(
                        fixture, provenance, SOURCE_PDF
                    )


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
        confirmation = session.plan("다음", turn_id=4, language="ko")
        self.assertEqual(
            confirmation.action, CuratedProtocolAction.CLARIFY_COMPLETION
        )
        self.assertFalse(confirmation.state_changed)
        self.assertEqual(session.state()["current_step_label"], "1")
        advanced = session.plan("네.", turn_id=5, language="ko")
        replay = session.plan("네.", turn_id=5, language="ko")
        self.assertEqual(advanced, replay)
        self.assertEqual(session.state()["current_step_label"], "2")
        stopped = session.plan("종료", turn_id=6, language="ko")
        self.assertEqual(stopped.action, CuratedProtocolAction.STOP)
        self.assertFalse(session.state()["active"])

    def test_compound_completion_and_next_is_structured_and_advances_once(self):
        phrases = (
            "현재 현재 단계를 완료했어 다음 단계로 안내해 줘",
            "현재 단계를 완료했어. 다음 단계로 안내해 줘.",
            "이 단계 끝냈어요. 다음으로 넘어가요.",
            "이 단계 끝났어 다음 단계로 알려줘",
            "다 했어. 다음 단계 알려줘.",
            "현재 작업 완료. 다음 단계 진행해 줘.",
            "여기까지 했고 이제 다음으로 가자.",
            "I finished this step. Take me to the next one.",
            "This step is complete; what comes next?",
        )
        for turn_id, phrase in enumerate(phrases, 500):
            with self.subTest(phrase=phrase):
                session = CuratedProtocolSession(self.fixture)
                session.activate_configured()
                session.active = True
                intent = classify_curated_control_intent(
                    phrase,
                    language="en" if phrase.startswith(("I ", "This ")) else "ko",
                )
                self.assertEqual(intent.intent_kind, "completion_and_next")
                self.assertTrue(intent.reported_completion)
                self.assertEqual(intent.requested_transition, "next")
                plan = session.plan(
                    phrase,
                    turn_id=turn_id,
                    language=intent.language,
                )
                self.assertEqual(plan.action, CuratedProtocolAction.NEXT)
                self.assertTrue(plan.reported_completion)
                self.assertTrue(plan.state_changed)
                self.assertEqual(session.current_index, 1)
                self.assertEqual(plan.step_label, "2")
                self.assertEqual(session.plan(
                    phrase,
                    turn_id=turn_id,
                    language=intent.language,
                ), plan)
                self.assertEqual(session.current_index, 1)

    def test_real_voice_scientific_normalization_and_navigation_are_bounded(self):
        inventory=("ambic","hplc water","solution a","solution b","acetonitrile")
        cases=(
            ("여기서 A M B I C가 뭐야?","ambic",("definition",)),
            ("여기서 A M P I C가 뭐야?","ambic",("definition",)),
            ("A M B I C에 대해서 알려줘.","ambic",("definition",)),
            ("에이엠빅이 뭐야?","ambic",("definition",)),
            ("여기서 솔루션 A는 뭐야?","solution_a",("definition",)),
            ("솔루션 B 구성은 뭐야?","solution_b",("definition","composition")),
            ("PLC water is what?","hplc_water",("definition",)),
            ("H PLC water가 뭐야?","hplc_water",("definition",)),
            ("HPLC 워터가 뭐야?","hplc_water",("definition",)),
            ("겨야 할 안전 수칙은 뭐야?",None,("definition","safety")),
            ("지켜야 할 안전 수칙은 뭐야?",None,("definition","safety")),
        )
        for transcript,entity,dimensions in cases:
            with self.subTest(transcript=transcript):
                intent=classify_curated_control_intent(
                    transcript,language="ko",entity_inventory=inventory,
                )
                self.assertEqual(intent.action,CuratedProtocolAction.RELATED_QUESTION)
                self.assertEqual(intent.requested_entity,entity)
                self.assertEqual(intent.question_dimensions,dimensions)
                self.assertFalse(intent.allows_state_mutation)
        for transcript in (
            "다음 단계에 대해서 진행해 줘.", "다음 단계로 진행해 줘."
        ):
            intent=classify_curated_control_intent(
                transcript,language="ko",entity_inventory=inventory,
            )
            self.assertEqual(
                intent.action,CuratedProtocolAction.CLARIFY_COMPLETION
            )
            self.assertFalse(intent.allows_state_mutation)
        normalized,entity,_=normalize_scientific_query(
            "25 mM Solution B를 250 mM로 바꿔?",entity_inventory=inventory,
        )
        self.assertIn("25 mm",normalized)
        self.assertIn("250 mm",normalized)
        self.assertEqual(entity,"solution_b")
        untouched,unresolved,note=normalize_scientific_query(
            "AMPIC가 뭐야?",entity_inventory=("ambic","ampic"),
        )
        self.assertIn("ampic",untouched)
        self.assertIsNone(unresolved)
        self.assertIsNone(note)
        labels,_,_=normalize_scientific_query(
            "Step 3의 Solution A와 Step 8의 Solution B",
            entity_inventory=inventory,
        )
        self.assertIn("step 3",labels)
        self.assertIn("step 8",labels)
        self.assertIn("solution a",labels)
        self.assertIn("solution b",labels)

    def test_bounded_related_followups_reuse_context_or_clarify(self):
        session=CuratedProtocolSession(self.fixture)
        session.activate_configured()
        first=session.plan("HPLC 워터가 뭐야?",turn_id=910,language="ko")
        self.assertEqual(first.action,CuratedProtocolAction.RELATED_QUESTION)
        follow=session.plan("되는 거 아니야?",turn_id=911,language="ko")
        self.assertEqual(follow.action,CuratedProtocolAction.RELATED_QUESTION)
        self.assertIn("HPLC",session.reference_query_for("되는 거 아니야?",follow).upper())
        external=session.plan("외부 검색은 어떻게?",turn_id=912,language="ko")
        self.assertEqual(external.requested_followup,"search_external_reference")
        web=session.plan("웹에서 확인해 줘.",turn_id=913,language="ko")
        self.assertEqual(web.requested_followup,"search_external_reference")

    def test_ambiguous_completion_and_off_topic_are_non_mutating(self):
        session = CuratedProtocolSession(self.fixture)
        session.activate_configured()
        session.active = True
        session.current_index = 1
        opening = session._checkpoint()

        ambiguous = session.plan(
            "이 단계가 완료된 것 같은데 다음으로 가도 될까?",
            turn_id=600,
            language="ko",
        )
        self.assertEqual(
            ambiguous.action,
            CuratedProtocolAction.CLARIFY_COMPLETION,
        )
        self.assertIn("상태는 변경하지 않았습니다", ambiguous.display_text)
        self.assertEqual(session.current_index, 1)

        for turn_id, phrase in enumerate((
            "혹시 융프라우 다녀오셨나요?",
            "오늘 여행 가기 좋은 날인가요?",
            '문서에서 "stop"이라는 영어 단어는 무슨 뜻이야?',
        ), 601):
            with self.subTest(phrase=phrase):
                plan = session.plan(phrase, turn_id=turn_id, language="ko")
                self.assertEqual(plan.action, CuratedProtocolAction.OFF_TOPIC)
                self.assertFalse(plan.state_changed)
                self.assertIn("진행 중인 실험 절차", plan.display_text)
                self.assertNotIn("픽스처", plan.display_text)
                self.assertEqual(session.current_index, 1)
                self.assertTrue(session.active)

        self.assertEqual(session.active, opening[0])
        self.assertEqual(session.current_index, opening[1])

    def test_repeat_paraphrases_are_deterministic_and_non_mutating(self):
        for turn_id, phrase in enumerate((
            "다시 한 번 말해줘",
            "방금 설명 반복해줘",
            "아까 안내 다시 말해줘",
            "Please explain that again",
        ), 700):
            with self.subTest(phrase=phrase):
                session = CuratedProtocolSession(self.fixture)
                session.active = True
                session.current_index = 2
                opening = session.state()
                plan = session.plan(
                    phrase,
                    turn_id=turn_id,
                    language="en" if phrase.startswith("Please") else "ko",
                )
                self.assertEqual(plan.action, CuratedProtocolAction.REPEAT)
                self.assertFalse(plan.state_changed)
                self.assertEqual(session.state(), opening)

    def test_named_step_elaboration_uses_tier_zero_without_mutation(self):
        session = CuratedProtocolSession(self.fixture)
        session.active = True
        session.current_index = 2
        opening = session.state()
        plan = session.plan(
            "3단계에 대해 조금만 더 자세하게 설명해줄 수 있어?",
            turn_id=710,
            language="ko",
        )
        self.assertEqual(plan.action, CuratedProtocolAction.FULL_DETAIL)
        self.assertEqual(plan.intent_kind, "step_elaboration")
        self.assertEqual(plan.target_step, "3")
        self.assertEqual(
            plan.speech_text,
            self.fixture.localized_fact("candidate-a-step-03", "current_step"),
        )
        self.assertIn(self.fixture.steps[2].instruction_source_text, plan.display_text)
        self.assertEqual(session.state(), opening)

    def test_step_three_contextual_solution_a_aliases_use_adjacent_verified_fact(self):
        for turn_id, phrase in enumerate((
            "A 용액은 어떻게 만들어?",
            "그 용액은 어떻게 준비해?",
            "AMBIC은 어떻게 준비해?",
        ), 720):
            with self.subTest(phrase=phrase):
                session = CuratedProtocolSession(self.fixture)
                session.active = True
                session.current_index = 2
                opening = session.state()
                plan = session.plan(phrase, turn_id=turn_id, language="ko")
                self.assertEqual(plan.action, CuratedProtocolAction.QUESTION)
                self.assertEqual(plan.intent_kind, "contextual_protocol_entity")
                self.assertEqual(
                    plan.fact_id,
                    "candidate-a-step-02/current_step",
                )
                self.assertIn("Solution A", plan.source_texts[0])
                self.assertIn("AMBIC", plan.speech_text)
                self.assertEqual(session.state(), opening)

    def test_current_step_question_variants_precede_grounded_qa_without_mutation(self):
        phrases = (
            "현재 단계가 뭐야?",
            "현재 단계 다시 알려줘.",
            "현재 단계를 다시 알려줘.",
            "지금 무슨 단계야?",
        )
        for turn_id, phrase in enumerate(phrases, 100):
            with self.subTest(phrase=phrase):
                session = CuratedProtocolSession(self.fixture)
                session.active = True
                session.current_index = 2
                plan = session.plan(phrase, turn_id=turn_id, language="ko")
                self.assertEqual(plan.action, CuratedProtocolAction.CURRENT)
                self.assertEqual(session.current_index, 2)
                self.assertEqual(plan.step_label, "3")

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

        requested = session.plan("단계로 넘어가죠", turn_id=6, language="ko")
        self.assertEqual(
            requested.action, CuratedProtocolAction.CLARIFY_COMPLETION
        )
        self.assertEqual(session.state()["current_step_label"], "1")
        self.assertEqual(session.state()["revision"], 1)
        advanced = session.plan("네.", turn_id=7, language="ko")
        self.assertEqual(advanced.action, CuratedProtocolAction.NEXT)
        self.assertEqual(session.state()["current_step_label"], "2")
        self.assertEqual(session.state()["revision"], 2)
        self.assertEqual(session.plan(
            "네.", turn_id=7, language="ko"
        ), advanced)
        self.assertEqual(session.state()["current_step_label"], "2")

        stopped = session.plan(
            "프로토콜을 종료해 줘", turn_id=8, language="ko"
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
                        "실험을 시작합니다. 현재 1단계입니다. "
                        "염색된 단백질 밴드를 준비해 작은 조각으로 나누고 "
                        "지정된 AMBIC 용액이 담긴 튜브에 넣어 주세요.",
                    )
                    self.assertEqual(session.current_index, 0)
                    started = session.state()
                    self.assertTrue(started["active"])
                    self.assertFalse(opening["active"])
                    self.assertEqual(started["workflow_status"], "active")
                    self.assertEqual(opening["workflow_status"], "ready")
                    self.assertEqual(started["timers"]["experiment"]["state"], "running")
                    self.assertIsNotNone(started["timers"]["experiment"]["started_at"])
                    self.assertEqual(started["timers"]["step"]["state"], "not_started")
                    self.assertEqual(started["timer"]["state"], "not_started")
                    self.assertTrue(plan.state_changed)

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
                    CuratedProtocolAction.RELATED_QUESTION,
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
                "실험을 시작합니다. 현재 1단계입니다. "
                "염색된 단백질 밴드를 준비해 작은 조각으로 나누고 "
                "지정된 AMBIC 용액이 담긴 튜브에 넣어 주세요.",
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
            "1단계 안내를 화면에 다시 표시했습니다.",
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
                self.assertIn(canonical, detail.display_text)
                self.assertIn("답변 · 한국어 참고 번역", detail.display_text)
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
            CuratedProtocolAction.FULL_DETAIL,
        )
        self.assertFalse(inactive_detail.state_changed)
        self.assertFalse(
            CuratedProtocolSession(self.fixture).state()["active"]
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
                CuratedProtocolAction.CLARIFY_COMPLETION,
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
                self.assertIn(plan.action, {
                    CuratedProtocolAction.RELATED_QUESTION,
                    CuratedProtocolAction.OFF_TOPIC,
                })
                self.assertNotIn(plan.action, {
                    CuratedProtocolAction.NEXT,
                    CuratedProtocolAction.STOP,
                })
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
            "현재 단계를 완료했어. 다음 단계로 넘어가 줘",
            turn_id=2,
            language="ko",
        )
        self.assertEqual(first.curated_protocol_session.current_index, 1)
        self.assertEqual(second.curated_protocol_session.current_index, 0)
        self.assertFalse(second.curated_protocol_session.active)

    def test_final_step_never_advances_and_empty_state_never_passes(self):
        session = CuratedProtocolSession(self.fixture)
        session.active = True
        session.current_index = 24
        final = session.plan(
            "현재 단계를 완료했어. 다음 단계로 안내해 줘",
            turn_id=1,
            language="ko",
        )
        self.assertEqual(session.current_index, 24)
        self.assertTrue(final.final_step)
        self.assertTrue(final.state_changed)
        self.assertFalse(session.active)
        self.assertEqual(session.workflow_status, "completed")
        self.assertIsNone(session.state()["current_step_label"])
        inactive = CuratedProtocolSession(self.fixture)
        result = inactive.plan("현재 단계", turn_id=1, language="ko")
        self.assertEqual(result.action, CuratedProtocolAction.CURRENT)
        self.assertFalse(inactive.state()["active"])
        self.assertEqual(inactive.state()["current_step_label"], "1")
        self.assertIn(inactive.state()["workflow_status"], {"preview", "ready"})

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

                blocked = session.plan(
                    "I completed this step. Guide me to the next step.",
                    turn_id=turn_id,
                    language="en",
                )

                self.assertEqual(
                    session.state()["current_step_label"],
                    label,
                )
                self.assertFalse(blocked.state_changed)
                self.assertEqual(
                    blocked.action, CuratedProtocolAction.CLARIFY_COMPLETION
                )
                self.assertIn("I will not record completion", blocked.response_text)
                self.assertIsNotNone(session.pending_observation_confirmation)
                self.assertIsNone(session.state()["block_reason"])

    def test_supported_question_has_only_current_context_and_unsupported_is_bounded(self):
        session = CuratedProtocolSession(self.fixture)
        session.active = True
        session.current_index = 2
        supported = session.plan("현재 온도는?", turn_id=1, language="ko")
        self.assertEqual(supported.action, CuratedProtocolAction.QUESTION)
        self.assertEqual(supported.fact_id, "current_step")
        self.assertEqual(len(supported.facts), 1)
        self.assertIn(supported.facts[0].text, supported.display_text)
        self.assertIn("답변 · 한국어 참고 번역", supported.display_text)
        self.assertEqual(
            supported.speech_text,
            self.fixture.localized_fact(
                self.fixture.steps[2].step_id, "current_step"
            ),
        )
        self.assertEqual(
            supported.speech_mode,
            CuratedProtocolSpeechMode.VERIFIED_FACT,
        )
        unrelated = self.fixture.steps[3].instruction_source_text
        self.assertNotIn(unrelated, "\n".join(fact.text for fact in supported.facts))
        unsupported = session.plan("달의 질량은?", turn_id=2, language="ko")
        self.assertEqual(unsupported.action, CuratedProtocolAction.OFF_TOPIC)
        self.assertIn("관련 실험실 자료", unsupported.response_text)
        self.assertNotIn("픽스처", unsupported.response_text)
        self.assertEqual(unsupported.display_text, unsupported.speech_text)
        self.assertEqual(
            unsupported.speech_mode,
            CuratedProtocolSpeechMode.CONTROL,
        )
        self.assertIsNone(unsupported.fact_id)

    def test_solution_a_question_separates_exact_source_display_from_korean_speech(self):
        session=CuratedProtocolSession(self.fixture)
        session.active=True
        session.current_index=1
        opening=session.state()

        plan=session.plan(
            "용액 A는 어떻게 준비해?",turn_id=91,language="ko")

        source=self.fixture.steps[1].instruction_source_text
        self.assertEqual(plan.action,CuratedProtocolAction.QUESTION)
        self.assertEqual(plan.fact_id,"current_step")
        self.assertIn(source,plan.display_text)
        self.assertIn("한국어 참고 번역",plan.display_text)
        self.assertIn("답변 · 한국어 참고 번역",plan.display_text)
        self.assertIn("2 parts",plan.display_text)
        self.assertIn("Solution A",plan.speech_text)
        self.assertNotIn(source,plan.speech_text)
        self.assertIn("Solution B",plan.speech_text)
        self.assertEqual(session.state(),opening)

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
        self.assertEqual(unavailable.action, CuratedProtocolAction.RELATED_QUESTION)
        self.assertIn("Solution A", unavailable.response_text)
        self.assertIn(solution.facts[0].text, unavailable.source_texts)
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

        self.assertEqual(ambiguous.action, CuratedProtocolAction.RELATED_QUESTION)
        self.assertEqual(future.action, CuratedProtocolAction.RELATED_QUESTION)
        self.assertEqual(session.state(), opening)
        inactive = CuratedProtocolSession(self.fixture).plan(
            "현재 온도는?",
            turn_id=3,
            language="ko",
        )
        self.assertEqual(inactive.action, CuratedProtocolAction.INACTIVE)
        self.assertIn("시작해 주세요", inactive.response_text)

    def test_real_voice_completion_only_forms_advance_atomically_once(self):
        phrases = (
            "현재 단계를 완료했어요.",
            "이 단계 완료했어.",
            "여기까지 다 했어요.",
            "지금 단계는 끝났습니다.",
            "방금 작업 마쳤어.",
            "I completed the current step.",
            "This step is finished.",
        )
        for turn_id, phrase in enumerate(phrases, 900):
            with self.subTest(phrase=phrase):
                session = CuratedProtocolSession(self.fixture)
                session.active = True
                session.current_index = 1
                plan = session.plan(phrase, turn_id=turn_id, language="ko")
                self.assertEqual(plan.action, CuratedProtocolAction.NEXT)
                self.assertTrue(plan.reported_completion)
                self.assertEqual(plan.intent_kind, "report_completion")
                self.assertEqual(session.current_index, 2)
                self.assertEqual(
                    session.plan(phrase, turn_id=turn_id, language="ko"), plan
                )
                self.assertEqual(session.current_index, 2)

    def test_completion_only_preserves_ambiguous_and_readiness_boundaries(self):
        ambiguous = CuratedProtocolSession(self.fixture)
        ambiguous.active = True
        opening = ambiguous.state()
        plan = ambiguous.plan(
            "거의 끝난 것 같아.", turn_id=920, language="ko"
        )
        self.assertEqual(plan.action, CuratedProtocolAction.CLARIFY_COMPLETION)
        self.assertEqual(ambiguous.state(), opening)
        for turn_id, index in enumerate((6, 8, 19), 921):
            with self.subTest(step=index + 1):
                session = CuratedProtocolSession(self.fixture)
                session.active = True
                session.current_index = index
                plan = session.plan(
                    "현재 단계를 완료했어요.", turn_id=turn_id, language="ko"
                )
                self.assertEqual(
                    plan.action, CuratedProtocolAction.CLARIFY_COMPLETION
                )
                self.assertEqual(plan.intent_kind, "observation_confirmation_required")
                self.assertIsNotNone(session.pending_observation_confirmation)
                self.assertEqual(session.current_index, index)

    def test_detail_planner_uses_admitted_facts_without_invented_method(self):
        session = CuratedProtocolSession(self.fixture)
        session.active = True
        session.current_index = 3
        opening = session.state()
        plan = session.plan(
            "지금 단계에서 뭘 해야 하는지 자세히 알려줘.",
            turn_id=930,
            language="ko",
        )
        self.assertEqual(plan.action, CuratedProtocolAction.FULL_DETAIL)
        self.assertIn("무엇을 제거하나요", plan.display_text)
        self.assertIn("젤 밴드는 튜브에 남습니다", plan.display_text)
        self.assertIn("제거 도구", plan.display_text)
        self.assertNotIn("피펫", plan.display_text)
        self.assertNotEqual(
            " ".join(plan.primary_text.split()),
            " ".join(self.fixture.localized_fact(
                self.fixture.steps[3].step_id, "current_step"
            ).split()),
        )
        self.assertEqual(session.state(), opening)

    def test_fully_destained_explanation_uses_all_page_five_evidence(self):
        session = CuratedProtocolSession(self.fixture)
        session.active = True
        session.current_index = 6
        opening = session.state()
        plan = session.plan(
            "젤 밴드가 완전히 탈색된다는 게 무슨 의미야?",
            turn_id=931,
            language="ko",
        )
        self.assertEqual(plan.action, CuratedProtocolAction.FULL_DETAIL)
        self.assertIn("투명", plan.display_text)
        self.assertIn("두 번의 사이클", plan.display_text)
        self.assertIn("고정 반복 횟수", plan.display_text)
        self.assertIn("expected_result_1", plan.evidence_ids)
        self.assertIn(5, plan.source_pages)
        self.assertEqual(session.state(), opening)

    def test_context_audio_safety_and_unreliable_transcript_routes_are_read_only(self):
        session = CuratedProtocolSession(self.fixture)
        session.active = True
        session.current_index = 2
        opening = session.state()
        contextual = session.plan(
            "그거는 어떻게 준비해?", turn_id=940, language="ko"
        )
        self.assertEqual(contextual.action, CuratedProtocolAction.QUESTION)
        self.assertIn("resolved_entity:solution_a", contextual.limitations)
        safety = session.plan(
            "여기서 안전하게 실험하려면 어떻게 해야 돼?",
            turn_id=941,
            language="ko",
        )
        self.assertEqual(safety.action, CuratedProtocolAction.RELATED_QUESTION)
        replay = session.plan("There's no sound.", turn_id=942, language="ko")
        self.assertEqual(replay.action, CuratedProtocolAction.AUDIO_RECOVERY)
        unreliable = session.plan("わんねーちょ", turn_id=943, language="ko")
        self.assertEqual(
            unreliable.action, CuratedProtocolAction.TRANSCRIPT_UNRELIABLE
        )
        self.assertEqual(session.state(), opening)

        ambiguous = CuratedProtocolSession(self.fixture)
        ambiguous.active = True
        ambiguous.current_index = 1
        choice = ambiguous.plan(
            "그 용액은 어떻게 준비해?", turn_id=944, language="ko"
        )
        self.assertEqual(choice.action, CuratedProtocolAction.CLARIFY_REFERENCE)
        self.assertIn("Solution A와 Solution B", choice.display_text)

    def test_source_web_followup_and_search_cancel_are_bounded_read_only_intents(self):
        session = CuratedProtocolSession(self.fixture)
        session.active = True
        session.current_index = 2
        opening = session.state()

        sources = session.plan("출처 보여줘", turn_id=945, language="ko")
        self.assertEqual(sources.action, CuratedProtocolAction.FULL_DETAIL)
        self.assertEqual(sources.intent_kind, "show_sources")
        self.assertTrue(sources.evidence_ids)

        no_context = CuratedProtocolSession(self.fixture)
        no_context.active = True
        no_context_opening = no_context.state()
        clarify = no_context.plan(
            "웹에서 더 찾아봐", turn_id=946, language="ko"
        )
        self.assertEqual(clarify.action, CuratedProtocolAction.CLARIFY_REFERENCE)
        self.assertEqual(no_context.state(), no_context_opening)

        question = "여기서 진짜 안전 수칙 있어?"
        related = session.plan(question, turn_id=947, language="ko")
        self.assertEqual(related.action, CuratedProtocolAction.RELATED_QUESTION)
        followup = session.plan("웹에서 더 찾아봐", turn_id=948, language="ko")
        self.assertEqual(followup.action, CuratedProtocolAction.RELATED_QUESTION)
        self.assertEqual(followup.requested_followup, "search_external_reference")
        self.assertEqual(session.reference_query_for("웹에서 더 찾아봐", followup), question)

        cancelled = session.plan("방금 검색 취소해", turn_id=949, language="ko")
        self.assertEqual(cancelled.action, CuratedProtocolAction.CANCEL_READONLY)
        self.assertEqual(cancelled.intent_kind, "cancel_readonly_operation")
        self.assertEqual(session.state(), opening)

        session.reset()
        session.activate_configured()
        after_reset = session.plan("웹에서 더 찾아봐", turn_id=950, language="ko")
        self.assertEqual(after_reset.action, CuratedProtocolAction.CLARIFY_REFERENCE)

    def test_provider_quality_metadata_cannot_override_clear_stop(self):
        session = CuratedProtocolSession(self.fixture)
        session.active = True
        unreliable = session.plan(
            "일반 발화", turn_id=950, language="ko",
            transcript_quality="provider_low_confidence",
        )
        self.assertEqual(
            unreliable.action, CuratedProtocolAction.TRANSCRIPT_UNRELIABLE
        )
        session = CuratedProtocolSession(self.fixture)
        session.active = True
        stopped = session.plan(
            "프로토콜 종료해줘", turn_id=951, language="ko",
            transcript_quality="provider_low_confidence",
        )
        self.assertEqual(stopped.action, CuratedProtocolAction.STOP)
        self.assertFalse(session.active)


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

    def test_grounded_qa_is_current_step_cited_numeric_safe_and_tool_free(self):
        client = GroundedClient({
            "intent": "grounded_explanation",
            "target_step_id": "candidate-a-step-03",
            "primary_text": "Solution A 500 µL를 사용하고 37°C에서 진행합니다.",
            "claims": [{
                "text": "검증된 원문에는 Solution A 500 µL와 37°C가 명시되어 있습니다.",
                "evidence_ids": ["current_step"],
                "inference_label": "direct_source_fact",
            }],
            "unsupported_parts": [],
        })
        answer = asyncio.run(answer_curated_protocol_question(
            client,
            "이 단계에서 용액은 얼마나 넣어?",
            language="ko",
            protocol_id="fixture",
            protocol_title="Development fixture",
            step_id="candidate-a-step-03",
            step_label="3",
            facts=((
                "current_step", "step",
                "Wash with Solution A 500 µL at 37°C.", 4,
            ),),
        ))
        self.assertEqual(answer.evidence_ids, ("current_step",))
        self.assertEqual(answer.target_step_id, "candidate-a-step-03")
        call = client.chat.completions.calls[0]
        self.assertEqual(call["temperature"], 0)
        self.assertTrue(call["response_format"]["json_schema"]["strict"])
        self.assertNotIn("tools", call)

    def test_grounded_qa_rejects_wrong_evidence_step_and_invented_quantity(self):
        base = {
            "intent": "grounded_explanation",
            "target_step_id": "candidate-a-step-03",
            "primary_text": "Solution A 700 µL를 사용합니다.",
            "claims": [{
                "text": "Solution A 700 µL",
                "evidence_ids": ["current_step"],
                "inference_label": "direct_source_fact",
            }],
            "unsupported_parts": [],
        }
        with self.assertRaises(RuntimeError):
            asyncio.run(answer_curated_protocol_question(
                GroundedClient(base), "얼마나?", language="ko",
                protocol_id="fixture", protocol_title="Development fixture",
                step_id="candidate-a-step-03", step_label="3",
                facts=(("current_step", "step", "Solution A 500 µL", 4),),
            ))
        invalid = copy.deepcopy(base)
        invalid["target_step_id"] = "candidate-a-step-04"
        invalid["primary_text"] = "Solution A 500 µL"
        invalid["claims"][0]["text"] = "Solution A 500 µL"
        with self.assertRaises(RuntimeError):
            asyncio.run(answer_curated_protocol_question(
                GroundedClient(invalid), "얼마나?", language="ko",
                protocol_id="fixture", protocol_title="Development fixture",
                step_id="candidate-a-step-03", step_label="3",
                facts=(("current_step", "step", "Solution A 500 µL", 4),),
            ))


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

    def test_stt_diagnostics_bind_exact_wav_prefix_and_documented_fields(self):
        session = self.make_session(index=1)
        socket = Socket()
        pcm = b"\0\0"

        async def immediate(function, *args, **kwargs):
            return function(*args, **kwargs)

        with patch(
            "voice_workflow_agent.server.transcribe",
            return_value=Transcription(
                "현재 단계 알려줘", "ko", duration_seconds=0.02,
                words=({"word": "현재", "start": 0.0, "end": 0.02},),
                response_status=200,
            ),
        ), patch(
            "voice_workflow_agent.server.synthesize", return_value=b"\0\0",
        ), patch(
            "voice_workflow_agent.server.asyncio.to_thread",
            side_effect=immediate,
        ):
            asyncio.run(run_turn(
                socket, session, pcm, 1, 1, 1, 1,
            ))
        diagnostic = next(
            item for item in socket.text if item["type"] == "stt.diagnostics"
        )
        wav = pcm_to_wav(pcm)
        self.assertEqual(
            diagnostic["wav_sha256"], hashlib.sha256(wav).hexdigest()
        )
        self.assertEqual(diagnostic["wav_byte_count"], len(wav))
        self.assertEqual(diagnostic["retained_prefix_frames"], 1)
        self.assertEqual(diagnostic["request_field_order"][0], "format")
        self.assertEqual(diagnostic["request_field_order"][-1], "file")
        self.assertIn("AMBIC", diagnostic["keyterms"])
        self.assertEqual(diagnostic["response_status"], 200)
        self.assertEqual(diagnostic["word_count"], 1)

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
        candidates=session.accept_chunk(b"\0" * FRAME_BYTES * frame_count)
        ready=next(
            item for item in candidates
            if item.kind=="barge_in_audio_ready")
        return [*candidates,*session.commit_interrupt_candidate(ready)]

    def assert_no_accepted_curated_events(self, socket: Socket) -> None:
        event_types = {item["type"] for item in socket.text}
        self.assertNotIn("protocol.fixture.state", event_types)
        self.assertNotIn("reply.delta", event_types)
        self.assertNotIn("reply.complete", event_types)
        self.assertNotIn("turn.done", event_types)

    def test_completion_only_bypasses_every_knowledge_provider_and_advances_once(self):
        session = self.make_session(index=1)
        session.multi_brain_settings = MultiBrainSettings(
            True, "offline-model", 2, 2, .2,
        )
        socket = Socket()

        async def immediate(function, *args, **kwargs):
            return function(*args, **kwargs)

        with patch(
            "voice_workflow_agent.server.transcribe",
            return_value=Transcription("현재 단계를 완료했어요.", "ko"),
        ), patch(
            "voice_workflow_agent.server.synthesize", return_value=b"\0\0"
        ) as tts, patch(
            "voice_workflow_agent.server.AsyncOpenAI",
            side_effect=AssertionError("provider must not be constructed"),
        ), patch(
            "voice_workflow_agent.server.search_approved_lab_references",
            side_effect=AssertionError("retrieval must not run"),
        ), patch(
            "voice_workflow_agent.server.XaiAuthoritativeWebSearch",
            side_effect=AssertionError("web search must not run"),
        ), patch(
            "voice_workflow_agent.server.asyncio.to_thread",
            side_effect=immediate,
        ):
            asyncio.run(run_turn(socket, session, b"\0\0", 1, 1))

        self.assertEqual(session.curated_protocol_session.current_index, 2)
        self.assertEqual(tts.call_count, 1)
        done = next(item for item in socket.text if item["type"] == "turn.done")
        self.assertEqual(done["intent_kind"], "report_completion")
        self.assertTrue(done["reported_completion"])
        self.assertEqual(done["tools_used"], [])
        operation = next(
            item for item in socket.text if item["type"] == "server.operation"
        )
        self.assertEqual(operation["operation"], "completion_and_next_transition")

    def test_session_greeting_is_once_per_logical_session_and_interruptible(self):
        context = ToolContext(Path("/unused/offline-catalog"), None, "ko", "test_only")
        session = ListenerSession(tool_context=context)
        session.start()
        session.accept_configuration(41, "cascade", "ko", self.fixture.protocol_id)
        session.greeting_audio_ready = True
        socket = Socket()

        async def immediate(function, *args, **kwargs):
            return function(*args, **kwargs)

        async def scenario():
            sender = LockedSender(socket)
            await _send_session_greeting(sender, session, language="ko")
            await _send_session_greeting(sender, session, language="ko")

        with patch(
            "voice_workflow_agent.server.synthesize", return_value=b"\0\0",
        ) as tts, patch(
            "voice_workflow_agent.server.asyncio.to_thread", side_effect=immediate,
        ):
            asyncio.run(scenario())

        greetings = [item for item in socket.text if item["type"] == "session.greeting"]
        self.assertEqual(len(greetings), 1)
        self.assertEqual(tts.call_count, 1)
        self.assertEqual(greetings[0]["turn_id"], 2_000_000_000)
        self.assertEqual(session.next_turn_id, 1)
        self.assertEqual(session.state, TurnState.AGENT_SPEAKING)
        events = self.accept_barge_in(session)
        self.assertTrue(any(item.kind == "barge_in_audio_ready" for item in events))

    def test_read_only_three_brain_server_path_is_concurrent_and_state_fenced(self):
        session = self.make_session(index=1)
        session.multi_brain_settings = MultiBrainSettings(
            True, "offline-model", 2, 2, .5,
        )
        socket = Socket()
        opening = session.curated_protocol_session.state()

        class Completions:
            def __init__(self):
                self.entered = 0
                self.active = 0
                self.maximum_active = 0
                self.release = asyncio.Event()

            async def create(self, **kwargs):
                self.entered += 1
                self.active += 1
                self.maximum_active = max(self.maximum_active, self.active)
                if self.entered == 3:
                    self.release.set()
                await asyncio.wait_for(self.release.wait(), timeout=1)
                self.active -= 1
                schema = kwargs["response_format"]["json_schema"]
                name = schema["name"]
                properties = schema["schema"]["properties"]
                if "answer" in name:
                    ids = properties["evidence_ids"]["items"]["enum"]
                    payload = {
                        "spoken_answer": "두 물의 차이는 활성 프로토콜 밖의 설명이 필요합니다.",
                        "display_answer": "활성 프로토콜은 HPLC water의 용액 준비 역할만 확인합니다.",
                        "evidence_ids": ids[:1],
                        "limitations": ["일반 물과의 품질 차이는 별도 근거가 필요합니다."],
                    }
                elif "source" in name:
                    payload = {
                        "entities": properties["entities"]["items"]["enum"][:1],
                        "dimensions": properties["dimensions"]["items"]["enum"][:1],
                        "scopes": ["ACTIVE_PROTOCOL"],
                        "query": "HPLC water comparison",
                        "needs_research": False,
                    }
                else:
                    payload = {
                        "helps": False,
                        "entity": None,
                        "preferred_class": "no_visual",
                        "reason_code": "insufficient_evidence",
                    }
                return SimpleNamespace(choices=[SimpleNamespace(
                    message=SimpleNamespace(content=json.dumps(payload)),
                )])

        completions = Completions()
        client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

        async def immediate(function, *args, **kwargs):
            return function(*args, **kwargs)

        with patch(
            "voice_workflow_agent.server.transcribe",
            return_value=Transcription(
                "HPLC water와 일반 물의 차이를 설명하고 관련 그림도 보여줘.",
                "ko",
            ),
        ), patch(
            "voice_workflow_agent.server.synthesize", return_value=b"\0\0",
        ) as tts, patch(
            "voice_workflow_agent.server.AsyncOpenAI", return_value=client,
        ), patch(
            "voice_workflow_agent.server.require_env", return_value="offline",
        ), patch(
            "voice_workflow_agent.server.search_approved_lab_references",
            return_value={
                "status": "no_admissible_evidence", "answerable": False,
                "matches": [], "retrieval": {"backend": "sqlite"},
            },
        ), patch(
            "voice_workflow_agent.server.asyncio.to_thread",
            side_effect=immediate,
        ):
            asyncio.run(run_turn(socket, session, b"\0\0", 1, 1))

        self.assertGreaterEqual(completions.maximum_active, 3)
        self.assertEqual(session.curated_protocol_session.state(), opening)
        self.assertEqual(tts.call_count, 1)
        states = [item for item in socket.text if item["type"] == "brain.state"]
        self.assertEqual(states[0]["roles"], ["answer", "source", "visual"])
        self.assertEqual(states[-1]["status"], "complete")
        rejected = [
            item for item in socket.text
            if item["type"] == "brain.output.rejected"
        ]
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["reason"], "unresolved_claim_dimensions")
        reply = next(item for item in socket.text if item["type"] == "reply.delta")
        self.assertNotEqual(
            reply["primary_text"],
            "활성 프로토콜은 HPLC water의 용액 준비 역할만 확인합니다.",
        )
        done = next(item for item in socket.text if item["type"] == "turn.done")
        self.assertEqual(done["brains_enabled"], ["answer", "source", "visual"])
        self.assertEqual(done["tools_used"], [])

    def test_bare_next_asks_confirmation_without_report_or_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            session = self.make_session(index=1)
            session.experiment_report_store = ExperimentReportStore(
                Path(directory) / "reports.sqlite"
            )
            socket = Socket()

            async def immediate(function, *args, **kwargs):
                return function(*args, **kwargs)

            with patch(
                "voice_workflow_agent.server.transcribe",
                return_value=Transcription("다음 단계로 안내해 줘.", "ko"),
            ), patch(
                "voice_workflow_agent.server.synthesize", return_value=b"\0\0"
            ) as tts, patch(
                "voice_workflow_agent.server.AsyncOpenAI",
                side_effect=AssertionError("provider must not be constructed"),
            ), patch(
                "voice_workflow_agent.server.asyncio.to_thread",
                side_effect=immediate,
            ):
                asyncio.run(run_turn(socket, session, b"\0\0", 1, 1))

            self.assertEqual(session.curated_protocol_session.current_index, 1)
            self.assertIsNotNone(
                session.curated_protocol_session.pending_completion_confirmation
            )
            self.assertEqual(session.experiment_report_store.list_reports(), [])
            self.assertFalse(any(
                item["type"] == "experiment.report.state"
                for item in socket.text
            ))
            done = next(item for item in socket.text if item["type"] == "turn.done")
            self.assertEqual(done["result_kind"], "clarify_completion")
            self.assertIn("완료하셨나요", tts.call_args.args[0])
            self.assertNotIn("실험 기록", tts.call_args.args[0])

    def test_completion_report_acknowledgement_follows_persistence(self):
        with tempfile.TemporaryDirectory() as directory:
            session = self.make_session(index=1)
            session.experiment_report_store = ExperimentReportStore(
                Path(directory) / "reports.sqlite"
            )
            order = []

            class OrderedSocket(Socket):
                async def send_text(self, value: str) -> None:
                    parsed = json.loads(value)
                    if parsed["type"] == "reply.delta":
                        order.append("reply")
                    await super().send_text(value)

            socket = OrderedSocket()

            async def immediate(function, *args, **kwargs):
                return function(*args, **kwargs)

            def persist(*args, **kwargs):
                order.append("persist")
                return _record_experiment_report_plan(*args, **kwargs)

            def tts(text, language):
                order.append("tts")
                self.assertIn("실험 기록에 반영", text)
                return b"\0\0"

            with patch(
                "voice_workflow_agent.server.transcribe",
                return_value=Transcription("현재 단계를 완료했어요.", "ko"),
            ), patch(
                "voice_workflow_agent.server.synthesize", side_effect=tts,
            ), patch(
                "voice_workflow_agent.server._record_experiment_report_plan",
                side_effect=persist,
            ), patch(
                "voice_workflow_agent.server.asyncio.to_thread",
                side_effect=immediate,
            ):
                asyncio.run(run_turn(socket, session, b"\0\0", 1, 1))

            self.assertEqual(order.count("persist"), 1)
            self.assertLess(order.index("persist"), order.index("tts"))
            self.assertLess(order.index("persist"), order.index("reply"))
            reply = next(
                item["text"] for item in socket.text
                if item["type"] == "reply.delta"
            )
            self.assertIn("실험 기록에 반영", reply)
            report = session.experiment_report_store.get_report(
                session.experiment_report_id
            )
            self.assertEqual(
                len([event for event in report["events"]
                     if event["event_type"] == "step_completed"]),
                1,
            )

    def test_completion_persistence_failure_rolls_back_without_success_language(self):
        with tempfile.TemporaryDirectory() as directory:
            session = self.make_session(index=1)
            session.experiment_report_store = ExperimentReportStore(
                Path(directory) / "reports.sqlite"
            )
            socket = Socket()

            async def immediate(function, *args, **kwargs):
                return function(*args, **kwargs)

            with patch(
                "voice_workflow_agent.server.transcribe",
                return_value=Transcription("현재 단계를 완료했어요.", "ko"),
            ), patch(
                "voice_workflow_agent.server.synthesize", return_value=b"\0\0"
            ) as tts, patch(
                "voice_workflow_agent.server._record_experiment_report_plan",
                side_effect=RuntimeError("synthetic persistence failure"),
            ), patch(
                "voice_workflow_agent.server.asyncio.to_thread",
                side_effect=immediate,
            ):
                asyncio.run(run_turn(socket, session, b"\0\0", 1, 1))

            self.assertEqual(session.curated_protocol_session.current_index, 1)
            self.assertIn("저장하지 못해", tts.call_args.args[0])
            self.assertNotIn("기록에 반영", tts.call_args.args[0])
            reply = next(
                item["text"] for item in socket.text
                if item["type"] == "reply.delta"
            )
            self.assertIn("현재 단계를 유지", reply)
            self.assertNotIn("기록에 반영", reply)
            self.assertEqual(
                session.turn_terminal_outcome(1, session.generation), "blocked"
            )

    def test_completion_report_replay_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            session = self.make_session(index=1)
            session.experiment_report_store = ExperimentReportStore(
                Path(directory) / "reports.sqlite"
            )
            curated = session.curated_protocol_session
            pre_transition_index = curated.current_index
            plan = curated.plan(
                "현재 단계를 완료했어요.",
                turn_id=1,
                language="ko",
                configuration_id=41,
                generation=session.generation,
            )
            first = _record_experiment_report_plan(
                session, curated, plan,
                turn_id=1, generation=session.generation,
                pre_transition_index=pre_transition_index,
            )
            replay = _record_experiment_report_plan(
                session, curated, plan,
                turn_id=1, generation=session.generation,
                pre_transition_index=pre_transition_index,
            )
            for report in (first, replay):
                self.assertEqual(
                    len([event for event in report["events"]
                         if event["event_type"] == "step_completed"]),
                    1,
                )
            self.assertEqual(curated.current_index, 2)

    def test_step_seven_block_records_no_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            session = self.make_session(index=6)
            session.experiment_report_store = ExperimentReportStore(
                Path(directory) / "reports.sqlite"
            )
            curated = session.curated_protocol_session
            plan = curated.plan(
                "현재 단계를 완료했어. 다음 단계로 안내해 줘",
                turn_id=1,
                language="ko",
                configuration_id=41,
                generation=session.generation,
            )
            report = _record_experiment_report_plan(
                session, curated, plan,
                turn_id=1, generation=session.generation,
                pre_transition_index=6,
            )
            self.assertFalse(plan.state_changed)
            self.assertEqual(curated.current_index, 6)
            self.assertEqual(plan.action, CuratedProtocolAction.CLARIFY_COMPLETION)
            self.assertIsNotNone(curated.pending_observation_confirmation)
            self.assertNotIn("step_completed", [
                event["event_type"] for event in report["events"]
            ])

    def test_source_observation_persists_once_before_steps_7_9_20_advance(self):
        cases = (
            (6, "젤이 완전히 탈색되어 투명해요"),
            (8, "젤이 흰색으로 변했고 탈수됐어요"),
            (19, "젤이 흰색으로 변했고 탈수됐어요"),
        )
        for index, observation in cases:
            with self.subTest(step=self.fixture.steps[index].source_label), tempfile.TemporaryDirectory() as directory:
                session = self.make_session(index=index)
                session.experiment_report_store = ExperimentReportStore(
                    Path(directory) / "reports.sqlite"
                )
                curated = session.curated_protocol_session
                curated.plan(
                    "현재 단계를 완료했어요", turn_id=1, language="ko",
                    configuration_id=41, generation=session.generation,
                )
                plan = curated.plan(
                    observation, turn_id=2, language="ko",
                    configuration_id=41, generation=session.generation,
                )
                report = _record_experiment_report_plan(
                    session, curated, plan, turn_id=2,
                    generation=session.generation,
                    pre_transition_index=index,
                )
                completed = [
                    event for event in report["events"]
                    if event["event_type"] == "step_completed"
                ]
                self.assertEqual(len(completed), 1)
                self.assertEqual(completed[0]["user_wording"], observation)
                self.assertEqual(
                    completed[0]["payload"]["observation_predicate"],
                    "positive",
                )
                self.assertEqual(curated.current_index, index + 1)

    def test_observation_persistence_failure_restores_steps_7_9_20(self):
        async def immediate(function, *args, **kwargs):
            return function(*args, **kwargs)

        cases = (
            (6, "젤이 완전히 탈색되어 투명해요"),
            (8, "젤이 흰색으로 변했고 탈수됐어요"),
            (19, "젤이 흰색으로 변했고 탈수됐어요"),
        )
        for index, observation in cases:
            with self.subTest(step=self.fixture.steps[index].source_label), tempfile.TemporaryDirectory() as directory:
                session = self.make_session(index=index)
                session.experiment_report_store = ExperimentReportStore(
                    Path(directory) / "reports.sqlite"
                )
                curated = session.curated_protocol_session
                curated.plan(
                    "현재 단계를 완료했어요", turn_id=1, language="ko",
                    configuration_id=41, generation=session.generation,
                )
                session.active_turn_id = 2
                session.turn_generations[2] = session.generation
                session.detector.state = TurnState.PROCESSING
                socket = Socket()
                with patch(
                    "voice_workflow_agent.server.transcribe",
                    return_value=Transcription(observation, "ko"),
                ), patch(
                    "voice_workflow_agent.server.synthesize",
                    return_value=b"\0\0",
                ) as tts, patch(
                    "voice_workflow_agent.server._record_experiment_report_plan",
                    side_effect=RuntimeError("synthetic persistence failure"),
                ), patch(
                    "voice_workflow_agent.server.asyncio.to_thread",
                    side_effect=immediate,
                ):
                    asyncio.run(run_turn(socket, session, b"\0\0", 2, 1))
                self.assertEqual(curated.current_index, index)
                self.assertIn("저장하지 못해", tts.call_args.args[0])
                self.assertNotIn("실험 기록에 반영", tts.call_args.args[0])

    def test_anomaly_acknowledgement_requires_report_persistence(self):
        async def immediate(function, *args, **kwargs):
            return function(*args, **kwargs)

        for failure in (False, True):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                session = self.make_session(index=3)
                session.experiment_report_store = ExperimentReportStore(
                    Path(directory) / "reports.sqlite"
                )
                socket = Socket()
                patches = {
                    "side_effect": RuntimeError("synthetic persistence failure")
                } if failure else {
                    "side_effect": _record_experiment_report_plan
                }
                with patch(
                    "voice_workflow_agent.server.transcribe",
                    return_value=Transcription(
                        "예상과 다르게 색이 남아 있어.", "ko"
                    ),
                ), patch(
                    "voice_workflow_agent.server.synthesize",
                    return_value=b"\0\0",
                ) as tts, patch(
                    "voice_workflow_agent.server._record_experiment_report_plan",
                    **patches,
                ), patch(
                    "voice_workflow_agent.server.asyncio.to_thread",
                    side_effect=immediate,
                ):
                    asyncio.run(run_turn(socket, session, b"\0\0", 1, 1))
                if failure:
                    self.assertIn("저장하지 못해", tts.call_args.args[0])
                    self.assertNotIn("기록에 남겼습니다", tts.call_args.args[0])
                    self.assertEqual(
                        session.experiment_report_store.list_reports(), []
                    )
                else:
                    self.assertIn("기록에 남겼습니다", tts.call_args.args[0])
                    report = session.experiment_report_store.get_report(
                        session.experiment_report_id
                    )
                    self.assertEqual(report["anomaly_count"], 1)

    def test_report_persists_completed_pre_transition_step_before_tts_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            session = self.make_session(index=1)
            session.experiment_report_store = ExperimentReportStore(
                Path(directory) / "reports.sqlite"
            )
            socket = Socket()
            async def immediate(function, *args, **kwargs):
                return function(*args, **kwargs)
            with patch(
                "voice_workflow_agent.server.transcribe",
                return_value=Transcription("현재 단계를 완료했어요.", "ko"),
            ), patch(
                "voice_workflow_agent.server.synthesize",
                side_effect=RuntimeError("synthetic TTS failure"),
            ), patch(
                "voice_workflow_agent.server.asyncio.to_thread",
                side_effect=immediate,
            ):
                with self.assertRaisesRegex(RuntimeError, "synthetic TTS"):
                    asyncio.run(run_turn(socket, session, b"\0\0", 1, 1))

            self.assertEqual(session.curated_protocol_session.current_index, 2)
            self.assertIsNotNone(session.experiment_report_id)
            report = session.experiment_report_store.get_report(
                session.experiment_report_id
            )
            completed = next(
                item for item in report["events"]
                if item["event_type"] == "step_completed"
            )
            self.assertEqual(completed["step_id"], self.fixture.steps[1].step_id)
            self.assertEqual(completed["step_label"], "2")
            self.assertEqual(
                completed["payload"]["post_transition_step_id"],
                self.fixture.steps[2].step_id,
            )
            self.assertEqual(
                completed["payload"]["completion_source"], "user_command"
            )
            event = next(
                item for item in socket.text
                if item["type"] == "experiment.report.state"
            )
            self.assertEqual(event["configuration_id"], 41)
            self.assertEqual(event["session_id"], session.session_id)
            self.assertEqual(event["generation"], session.generation)

    def test_cough_label_is_rejected_before_turn_routing_and_reporting(self):
        with tempfile.TemporaryDirectory() as directory:
            session = self.make_session(index=1)
            session.experiment_report_store = ExperimentReportStore(
                Path(directory) / "reports.sqlite"
            )
            socket = Socket()
            opening = session.curated_protocol_session.current_index
            async def immediate(function, *args, **kwargs):
                return function(*args, **kwargs)
            with patch(
                "voice_workflow_agent.server.transcribe",
                return_value=Transcription("[Coughing]", "en"),
            ), patch(
                "voice_workflow_agent.server.synthesize",
                side_effect=AssertionError("noise must not reach TTS"),
            ), patch(
                "voice_workflow_agent.server.search_approved_lab_references",
                side_effect=AssertionError("noise must not reach retrieval"),
            ), patch(
                "voice_workflow_agent.server.asyncio.to_thread",
                side_effect=immediate,
            ):
                asyncio.run(run_turn(socket, session, b"\0\0", 1, 1))
            types = [item["type"] for item in socket.text]
            self.assertIn("speech.rejected", types)
            self.assertNotIn("transcript", types)
            self.assertNotIn("reply.delta", types)
            self.assertNotIn("turn.done", types)
            self.assertEqual(session.curated_protocol_session.current_index, opening)
            self.assertIsNone(session.experiment_report_id)
            self.assertEqual(session.experiment_report_store.list_reports(), [])

    def test_repeated_natural_stop_finalizes_report_exactly_once(self):
        with tempfile.TemporaryDirectory() as directory:
            session = self.make_session(index=1)
            session.experiment_report_store = ExperimentReportStore(
                Path(directory) / "reports.sqlite"
            )
            curated = session.curated_protocol_session
            opening_index = curated.current_index
            first = curated.plan(
                "프로토콜을 종료할게", turn_id=1, language="ko"
            )
            first_report = _record_experiment_report_plan(
                session, curated, first, turn_id=1,
                generation=session.generation,
                pre_transition_index=opening_index,
            )
            second = curated.plan(
                "프로토콜 종료할게요", turn_id=2, language="ko"
            )
            second_report = _record_experiment_report_plan(
                session, curated, second, turn_id=2,
                generation=session.generation,
                pre_transition_index=opening_index,
            )
            self.assertTrue(first.state_changed)
            self.assertFalse(second.state_changed)
            self.assertEqual(first_report["status"], "stopped")
            self.assertEqual(second_report["finalization_version"], 1)
            self.assertEqual(
                [event["event_type"] for event in second_report["events"]],
                ["session_stopped", "report_finalized"],
            )
            self.assertEqual(
                second_report["events"][0]["payload"]["stop_reason"],
                "stopped_by_user",
            )

    def test_internal_miss_escalates_once_to_authoritative_web_without_mutation(self):
        session=self.make_session(index=1)
        session.external_reference_settings=ExternalReferenceSettings(
            True,("pubchem.ncbi.nlm.nih.gov",),"offline-web",2.0,3,
            "candidate_a",
        )
        socket=Socket();opening=session.curated_protocol_session.current_index
        async def immediate(function,*args,**kwargs):
            return function(*args,**kwargs)
        async def unsupported(*args,**kwargs):
            return SimpleNamespace(
                intent="unsupported",primary_text="",evidence_ids=(),
                inference_labels=(),unsupported_parts=("definition",),
            )
        class Web:
            calls=0
            def __init__(self,*args): pass
            async def search(self,query,*,language):
                Web.calls+=1
                return {
                    "status":"success","answer":"HPLC water는 분석용 고순도 물입니다.",
                    "matches":[{
                        "title":"PubChem","canonical_url":"https://pubchem.ncbi.nlm.nih.gov/",
                        "domain":"pubchem.ncbi.nlm.nih.gov","retrieved_at":"2026-08-11T00:00:00+00:00",
                        "source_kind":"external_authoritative_reference",
                        "relevant_excerpt":"HPLC grade water reference",
                    }],"backend":"xai_responses_web_search",
                }
        with patch(
            "voice_workflow_agent.server.transcribe",
            return_value=Transcription("H PLC water가 뭐야?","ko"),
        ),patch(
            "voice_workflow_agent.server.synthesize",return_value=b"\0\0",
        ),patch(
            "voice_workflow_agent.server.answer_curated_protocol_question",
            side_effect=unsupported,
        ),patch(
            "voice_workflow_agent.server.search_approved_lab_references",
            return_value={
                "status":"no_admissible_evidence","answerable":False,
                "matches":[],"retrieval":{"backend":"sqlite"},
            },
        ),patch(
            "voice_workflow_agent.server.XaiAuthoritativeWebSearch",Web,
        ),patch(
            "voice_workflow_agent.server.AsyncOpenAI",return_value=SimpleNamespace(),
        ),patch(
            "voice_workflow_agent.server.require_env",return_value="offline",
        ),patch(
            "voice_workflow_agent.server.asyncio.to_thread",side_effect=immediate,
        ):
            asyncio.run(run_turn(socket,session,b"\0\0",1,1))
        self.assertEqual(Web.calls,1)
        self.assertEqual(session.curated_protocol_session.current_index,opening)
        tools=[item for item in socket.text if item["type"]=="tool.call"]
        self.assertEqual(
            [item["tool"] for item in tools],
            ["search_approved_lab_references","search_authoritative_web"],
        )
        reply=next(item for item in socket.text if item["type"]=="reply.delta")
        self.assertEqual(reply["answer_origin"],"current_protocol")
        self.assertEqual(reply["question_dimensions"],["definition"])
        self.assertIn("HPLC",reply["transcript_correction_note"])
        supplement=next(
            item for item in socket.text if item["type"]=="research.result")
        self.assertEqual(
            supplement["answer_origin"],"external_authoritative_reference")
        self.assertEqual(supplement["terminal_status"], "success")
        self.assertEqual(
            len([item for item in socket.text
                 if item["type"] == "research.result"]),
            1,
        )

    def test_web_failure_preserves_local_facts_and_returns_specific_limitation(self):
        session=self.make_session(index=1)
        session.external_reference_settings=ExternalReferenceSettings(
            True,("pubchem.ncbi.nlm.nih.gov",),"offline-web",2.0,3,
            "candidate_a",
        )
        socket=Socket();opening=session.curated_protocol_session.current_index
        async def immediate(function,*args,**kwargs):
            return function(*args,**kwargs)
        async def unsupported(*args,**kwargs):
            return SimpleNamespace(
                intent="unsupported",primary_text="",evidence_ids=(),
                inference_labels=(),unsupported_parts=("definition",),
            )
        class Web:
            calls=0
            def __init__(self,*args): pass
            async def search(self,query,*,language):
                Web.calls+=1
                return {"status":"error","matches":[]}
        with patch(
            "voice_workflow_agent.server.transcribe",
            return_value=Transcription("HPLC water가 뭐야?","ko"),
        ),patch(
            "voice_workflow_agent.server.synthesize",return_value=b"\0\0",
        ),patch(
            "voice_workflow_agent.server.answer_curated_protocol_question",
            side_effect=unsupported,
        ),patch(
            "voice_workflow_agent.server.search_approved_lab_references",
            return_value={
                "status":"no_admissible_evidence","answerable":False,
                "matches":[],"retrieval":{"backend":"sqlite"},
            },
        ),patch(
            "voice_workflow_agent.server.XaiAuthoritativeWebSearch",Web,
        ),patch(
            "voice_workflow_agent.server.AsyncOpenAI",return_value=SimpleNamespace(),
        ),patch(
            "voice_workflow_agent.server.require_env",return_value="offline",
        ),patch(
            "voice_workflow_agent.server.asyncio.to_thread",side_effect=immediate,
        ):
            asyncio.run(run_turn(socket,session,b"\0\0",1,1))
        self.assertEqual(Web.calls,1)
        self.assertEqual(session.curated_protocol_session.current_index,opening)
        result=next(
            item for item in socket.text
            if item["type"]=="tool.result"
            and item["tool"]=="search_authoritative_web"
        )
        self.assertEqual(result["status"],"error")
        reply=next(item for item in socket.text if item["type"]=="reply.delta")
        self.assertIn("직접 답변",reply["text"])
        self.assertIn("HPLC water",reply["primary_text"])
        self.assertNotIn("Catalog #",reply["text"])
        supplement=next(
            item for item in socket.text if item["type"]=="research.result")
        self.assertEqual(supplement["status"],"error")
        self.assertEqual(supplement["terminal_status"], "failed")
        self.assertEqual(
            len([item for item in socket.text
                 if item["type"] == "research.result"]),
            1,
        )
        self.assertIn("추가 차원",supplement["limitation"])
        self.assertNotIn("fixture",reply["text"].casefold())

    def test_model_only_supplement_is_labelled_citation_free_and_read_only(self):
        session=self.make_session(index=1)
        session.external_reference_settings=ExternalReferenceSettings(False)
        session.supplemental_knowledge_settings=SupplementalKnowledgeSettings(
            True,"offline-supplement",2.0,
        )
        socket=Socket();opening=session.curated_protocol_session.current_index

        async def immediate(function,*args,**kwargs):
            return function(*args,**kwargs)

        async def unsupported(*args,**kwargs):
            return SimpleNamespace(
                intent="unsupported",primary_text="",evidence_ids=(),
                inference_labels=(),unsupported_parts=("role",),
            )

        class Supplement:
            calls=0
            def __init__(self,*args): pass
            async def explain(self,query,*,language):
                Supplement.calls+=1
                return {
                    "status":"success",
                    "answer":"중탄산 이온은 완충 계열의 일반 화학 개념입니다.",
                    "backend":"xai_responses_supplemental_model_knowledge",
                }

        with patch(
            "voice_workflow_agent.server.transcribe",
            return_value=Transcription(
                "AMBIC에서 bicarbonate는 왜 중요한 거야?","ko"),
        ),patch(
            "voice_workflow_agent.server.synthesize",return_value=b"\0\0",
        ),patch(
            "voice_workflow_agent.server.answer_curated_protocol_question",
            side_effect=unsupported,
        ),patch(
            "voice_workflow_agent.server.search_approved_lab_references",
            return_value={
                "status":"no_admissible_evidence","answerable":False,
                "matches":[],"retrieval":{"backend":"sqlite"},
            },
        ),patch(
            "voice_workflow_agent.server.XaiSupplementalKnowledge",Supplement,
        ),patch(
            "voice_workflow_agent.server.AsyncOpenAI",return_value=SimpleNamespace(),
        ),patch(
            "voice_workflow_agent.server.require_env",return_value="offline",
        ),patch(
            "voice_workflow_agent.server.asyncio.to_thread",side_effect=immediate,
        ):
            asyncio.run(run_turn(socket,session,b"\0\0",1,1))

        self.assertEqual(Supplement.calls,1)
        self.assertEqual(session.curated_protocol_session.current_index,opening)
        result=next(
            item for item in socket.text if item["type"]=="research.result")
        self.assertEqual(result["status"],"success")
        self.assertEqual(result["terminal_status"],"success")
        self.assertEqual(result["answer_origin"],"supplemental_model_knowledge")
        self.assertEqual(result["citations"],[])
        self.assertNotIn("권위",result["primary_text"])
        self.assertFalse(any(
            item.get("tool") == "search_authoritative_web"
            for item in socket.text
        ))

    def test_operational_substitution_never_uses_model_only_supplement(self):
        session=self.make_session(index=1)
        session.external_reference_settings=ExternalReferenceSettings(False)
        session.supplemental_knowledge_settings=SupplementalKnowledgeSettings(
            True,"offline-supplement",2.0,
        )
        socket=Socket()

        async def immediate(function,*args,**kwargs):
            return function(*args,**kwargs)

        async def unsupported(*args,**kwargs):
            return SimpleNamespace(
                intent="unsupported",primary_text="",evidence_ids=(),
                inference_labels=(),unsupported_parts=("substitution",),
            )

        with patch(
            "voice_workflow_agent.server.transcribe",
            return_value=Transcription(
                "HPLC water 대신 일반 증류수를 써도 돼?","ko"),
        ),patch(
            "voice_workflow_agent.server.synthesize",return_value=b"\0\0",
        ),patch(
            "voice_workflow_agent.server.answer_curated_protocol_question",
            side_effect=unsupported,
        ),patch(
            "voice_workflow_agent.server.search_approved_lab_references",
            return_value={
                "status":"no_admissible_evidence","answerable":False,
                "matches":[],"retrieval":{"backend":"sqlite"},
            },
        ),patch(
            "voice_workflow_agent.server.XaiSupplementalKnowledge",
            side_effect=AssertionError("operational supplement must not run"),
        ),patch(
            "voice_workflow_agent.server.AsyncOpenAI",return_value=SimpleNamespace(),
        ),patch(
            "voice_workflow_agent.server.asyncio.to_thread",side_effect=immediate,
        ):
            asyncio.run(run_turn(socket,session,b"\0\0",1,1))
        # Operational deviations terminate at the deterministic authority gate:
        # there is no evidence search to misrepresent as a provider operation.
        self.assertFalse(any(
            item["type"] in {"research.state", "research.result"}
            for item in socket.text
        ))
        response=next(
            item for item in socket.text if item["type"]=="reply.delta")
        self.assertIn("승인할 수 없습니다",response["text"])
        self.assertIn("HPLC water",response["text"])
        self.assertEqual(session.curated_protocol_session.current_index,1)

    def test_audio_help_and_unreliable_transcript_are_read_only_and_tool_free(self):
        for turn_id, transcription, expected in (
            (1, Transcription("There's no sound.", "en"), "audio_recovery"),
            (1, Transcription("わんねーちょ", "ja"), "transcript_unreliable"),
            (
                1,
                Transcription("불분명한 발화", "ko", confidence=0.1),
                "transcript_unreliable",
            ),
        ):
            with self.subTest(expected=expected, text=transcription.text):
                session = self.make_session(index=2)
                if expected == "audio_recovery":
                    session.accepted_input_language = InputLanguagePreference.AUTO
                socket = Socket()

                async def immediate(function, *args, **kwargs):
                    return function(*args, **kwargs)

                with patch(
                    "voice_workflow_agent.server.transcribe",
                    return_value=transcription,
                ), patch(
                    "voice_workflow_agent.server.synthesize", return_value=b"\0\0"
                ), patch(
                    "voice_workflow_agent.server.AsyncOpenAI",
                    side_effect=AssertionError("provider must not be constructed"),
                ), patch(
                    "voice_workflow_agent.server.search_approved_lab_references",
                    side_effect=AssertionError("retrieval must not run"),
                ), patch(
                    "voice_workflow_agent.server.asyncio.to_thread",
                    side_effect=immediate,
                ):
                    asyncio.run(run_turn(socket, session, b"\0\0", turn_id, 1))
                self.assertEqual(session.curated_protocol_session.current_index, 2)
                done = next(
                    item for item in socket.text if item["type"] == "turn.done"
                )
                self.assertEqual(done["result_kind"], expected)
                self.assertEqual(done["tools_used"], [])

    def run_question(
        self,
        *,
        client: RecordingClient | GroundedClient | None = None,
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
            return_value="offline-test-value",
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

    def test_generated_visual_is_queued_after_answer_audio_and_patches_same_turn(self):
        from voice_workflow_agent.curated_protocol import _png_rgb
        from voice_workflow_agent.generated_visuals import (
            GeneratedVisualAsset, GeneratedVisualSettings,
        )

        session=self.make_session(index=0)
        socket=Socket()
        listening=session.advance_turn_progress(1,session.generation,"listening")
        socket.text.append({"type":"turn.state",**listening})

        async def immediate(function,*args,**kwargs):
            return function(*args,**kwargs)

        async def fake_image_generate(self,specification):
            self.client.called_specification=specification
            return _png_rgb(64,64,b"\xff\xff\xff"*64*64)

        async def fake_obtain(specification,settings,generate):
            content=await generate(specification)
            content_hash=hashlib.sha256(content).hexdigest()
            return GeneratedVisualAsset(
                asset_id=content_hash,cache_key=specification.cache_key(settings.model),
                protocol_id=specification.protocol_id,
                revision_id=specification.revision_id,
                step_id=specification.step_id,step_label=specification.step_label,
                source_document_hash=specification.document_sha256,
                source_page=specification.source_page,
                source_evidence_ids=specification.source_evidence_ids,
                mime_type="image/png",content_sha256=content_hash,
                byte_size=len(content),width=64,height=64,content=content,
            ),False

        async def scenario():
            await run_turn(socket,session,b"\0\0",1,1)
            for _ in range(10):
                await asyncio.sleep(0)
                if any(
                    item.get("type")=="protocol.visual.state"
                    and item.get("status")=="visual_ready"
                    for item in socket.text
                ):
                    break

        fake_client=SimpleNamespace(images=SimpleNamespace())
        with patch.dict(os.environ,{
            "VOICE_WORKFLOW_AGENT_GENERATED_VISUALS_ENABLED":"true",
            "VOICE_WORKFLOW_AGENT_GENERATED_VISUAL_MODEL":"offline-test-model",
        },clear=False),patch(
            "voice_workflow_agent.server.transcribe",
            return_value=Transcription(
                "이 단계를 이해하기 쉽게 그림으로 보여줘.", "ko"
            ),
        ),patch(
            "voice_workflow_agent.server.synthesize",return_value=b"\0\0",
        ),patch(
            "voice_workflow_agent.server.AsyncOpenAI",return_value=fake_client,
        ),patch(
            "voice_workflow_agent.server.require_env",
            return_value="offline-test-value",
        ),patch(
            "voice_workflow_agent.server.XaiImageGenerator.generate",
            new=fake_image_generate,
        ),patch(
            "voice_workflow_agent.server.GENERATED_VISUALS.obtain",
            side_effect=fake_obtain,
        ),patch(
            "voice_workflow_agent.server.asyncio.to_thread",
            side_effect=immediate,
        ):
            session.generated_visual_settings=GeneratedVisualSettings(
                True,"offline-test-model"
            )
            asyncio.run(scenario())

        kinds=[item["type"] for item in socket.text]
        pending=next(
            index for index,item in enumerate(socket.text)
            if item["type"]=="protocol.visual.state"
            and item["status"]=="visual_pending")
        ready=next((
            index for index,item in enumerate(socket.text)
            if item["type"]=="protocol.visual.state"
            and item["status"]=="visual_ready"),None)
        self.assertIsNotNone(ready,socket.text)
        self.assertLess(kinds.index("reply.delta"),pending)
        self.assertLess(kinds.index("audio.segment.start"),pending)
        self.assertLess(pending,ready)
        visual=socket.text[ready]
        self.assertEqual(
            (visual["turn_id"],visual["generation"],visual["step_id"]),
            (1,session.generation,"candidate-a-step-01"),
        )
        self.assertEqual(
            visual["asset"]["url"],
            f"/api/generated-visuals/{visual['asset']['asset_id']}",
        )
        self.assertEqual(kinds.count("tool.call"),1)
        self.assertEqual(kinds.count("tool.result"),1)

    def test_entity_visual_is_queued_before_slow_explanatory_research(self):
        from voice_workflow_agent.generated_visuals import GeneratedVisualSettings

        session=self.make_session(index=0)
        session.tool_context=None
        session.generated_visual_settings=GeneratedVisualSettings(
            True,"offline-test-model")
        session.external_reference_settings=ExternalReferenceSettings(
            True,("pubchem.ncbi.nlm.nih.gov",),"offline-web",2.0,3,
            "candidate_a",user_visible_enrichment_budget_seconds=.005,
        )
        socket=Socket();order=[]

        async def immediate(function,*args,**kwargs):
            return function(*args,**kwargs)

        async def queued_visual(**kwargs):
            order.append(("visual",kwargs["turn_id"],kwargs["generation"]))

        class Web:
            def __init__(self,*args): pass
            async def search(self,query,*,language):
                await asyncio.sleep(.02)
                order.append(("research",language))
                return {"status":"not_found","matches":[]}

        with patch(
            "voice_workflow_agent.server.transcribe",
            return_value=Transcription(
                "염색된 단백질 밴드가 어떤 걸 의미해? 그림도 보여줘.","ko"
            ),
        ),patch(
            "voice_workflow_agent.server.synthesize",return_value=b"\0\0",
        ),patch(
            "voice_workflow_agent.server._queue_curated_generated_visual",
            side_effect=queued_visual,
        ),patch(
            "voice_workflow_agent.server.XaiAuthoritativeWebSearch",Web,
        ),patch(
            "voice_workflow_agent.server.AsyncOpenAI",
            return_value=SimpleNamespace(),
        ),patch(
            "voice_workflow_agent.server.require_env",return_value="offline",
        ),patch(
            "voice_workflow_agent.server.asyncio.to_thread",
            side_effect=immediate,
        ):
            asyncio.run(run_turn(socket,session,b"\0\0",1,1))

        self.assertEqual(order[0],("visual",1,session.generation))
        self.assertEqual(order[1],("research","ko"))
        reply=next(item for item in socket.text if item["type"]=="reply.delta")
        self.assertIn("염색된 단백질 밴드",reply["primary_text"])
        self.assertEqual(reply["requested_entities"],["stained_protein_band"])
        self.assertFalse(reply.get("state_changed",False))
        bounded=next(
            item for item in socket.text
            if item["type"]=="research.state"
            and item.get("status")=="background_bounded"
        )
        self.assertEqual(bounded["phase"],"optional_enrichment")
        self.assertEqual(bounded["user_visible_budget_ms"],5)
        self.assertEqual(
            len([item for item in socket.text if item["type"]=="research.result"]),
            1,
        )

    def test_entity_visual_disabled_returns_same_turn_unavailable_state(self):
        session=self.make_session(index=0);session.tool_context=None
        socket=Socket()
        async def immediate(function,*args,**kwargs):
            return function(*args,**kwargs)
        with patch(
            "voice_workflow_agent.server.transcribe",
            return_value=Transcription("젤 플러그 이미지를 보여줘.","ko"),
        ),patch(
            "voice_workflow_agent.server.synthesize",return_value=b"\0\0",
        ),patch(
            "voice_workflow_agent.server.asyncio.to_thread",
            side_effect=immediate,
        ):
            asyncio.run(run_turn(socket,session,b"\0\0",1,1))
        unavailable=next(
            item for item in socket.text
            if item["type"]=="protocol.visual.state"
        )
        self.assertEqual(unavailable["status"],"visual_failed")
        self.assertEqual(unavailable["fallback"],"feature_disabled")
        self.assertEqual(unavailable["turn_id"],1)
        self.assertEqual(unavailable["generation"],session.generation)
        self.assertFalse(any(item["type"]=="tool.call" for item in socket.text))

    def test_verified_source_crop_suppresses_generated_visual_specification(self):
        from voice_workflow_agent.server import _curated_visual_specification

        source_session=CuratedProtocolSession(self.fixture)
        source_session.active=True
        source_session.current_index=6
        self.assertEqual(
            self.fixture.visual_for_step(6).kind,"source_crop")
        self.assertIsNone(_curated_visual_specification(source_session))
        source_session.current_index=0
        self.assertIsNotNone(_curated_visual_specification(source_session))

    def test_related_question_uses_approved_reference_without_state_mutation(self):
        session=self.make_session(index=1)
        socket=Socket()
        citation={
            "chunk_id":"a"*64,"document_id":"approved-reference-1",
            "document_sha256":"b"*64,"document_title":"Approved Lab Guide",
            "document_version":"1","page_number":4,"section":"handling",
            "source_language":"en","approval_status":"approved",
            "original_excerpt":"Keep the fictional container closed.",
        }
        match={
            **citation,"language":"en","original_text":citation["original_excerpt"],
            "score":2.0,"version":"1","source_checksum":"b"*64,
            "section_code":"handling",
        }
        grounded=SimpleNamespace(intent="unsupported")
        approved=SimpleNamespace(
            primary_text="추가 승인 참고자료에 따르면 용기를 닫아 두세요.",
            citations=(citation,),limitations=("활성 프로토콜의 일부가 아닙니다.",),
        )

        async def immediate(function,*args,**kwargs):
            return function(*args,**kwargs)

        with patch.dict(os.environ,{},clear=True),patch(
            "voice_workflow_agent.server.transcribe",
            return_value=Transcription("2단계 할 때 주의사항 같은 거 있어?","ko"),
        ),patch(
            "voice_workflow_agent.server.synthesize",return_value=b"\0\0",
        ) as tts,patch(
            "voice_workflow_agent.server.AsyncOpenAI",
            return_value=SimpleNamespace(model="offline"),
        ),patch(
            "voice_workflow_agent.server.require_env",
            return_value="offline-test-value",
        ),patch(
            "voice_workflow_agent.server.answer_curated_protocol_question",
            return_value=grounded,
        ),patch(
            "voice_workflow_agent.server.search_approved_lab_references",
            return_value={
                "status":"success","answerable":True,"matches":[match],
                "retrieval":{"backend":"sqlite"},
            },
        ) as retrieval,patch(
            "voice_workflow_agent.server.answer_approved_reference_question",
            return_value=approved,
        ),patch(
            "voice_workflow_agent.server.asyncio.to_thread",
            side_effect=immediate,
        ):
            asyncio.run(run_turn(socket,session,b"\0\0",1,1))

        self.assertEqual(session.curated_protocol_session.current_index,1)
        self.assertTrue(session.curated_protocol_session.active)
        retrieval.assert_called_once()
        calls=[item for item in socket.text if item["type"]=="tool.call"]
        results=[item for item in socket.text if item["type"]=="tool.result"]
        self.assertEqual([item["tool"] for item in calls],[
            "search_approved_lab_references",
        ])
        self.assertEqual(results[0]["retrieval_backend"],"sqlite")
        reply=next(item for item in socket.text if item["type"]=="reply.delta")
        self.assertEqual(reply["answer_origin"],"current_protocol")
        supplement=next(
            item for item in socket.text if item["type"]=="research.result")
        self.assertEqual(supplement["answer_origin"],"approved_lab_corpus")
        self.assertEqual(supplement["citations"],[citation])
        self.assertIn("활성 프로토콜",tts.call_args.args[0])
        operation=next(
            item for item in socket.text if item["type"]=="server.operation")
        self.assertEqual(operation["operation"],"related_question_unresolved")

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
            return_value="offline-test-value",
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
            "실험을 시작합니다. 현재 1단계입니다. "
            "염색된 단백질 밴드를 준비해 작은 조각으로 나누고 "
            "지정된 AMBIC 용액이 담긴 튜브에 넣어 주세요.",
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
                transcript="현재 단계를 완료했어. 다음 단계를 진행해 줘.", index=0
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

    def test_live_compound_next_and_off_topic_bypass_llm_and_preserve_state_safety(self):
        compound_session, compound_socket, compound_client, _, _, _ = self.run_question(
            transcript="현재 현재 단계를 완료했어 다음 단계로 안내해 줘",
            index=0,
        )
        compound_done = next(
            item for item in compound_socket.text if item["type"] == "turn.done"
        )
        compound_operation = next(
            item for item in compound_socket.text
            if item["type"] == "server.operation"
        )
        self.assertEqual(compound_session.curated_protocol_session.current_index, 1)
        self.assertEqual(compound_done["intent_kind"], "completion_and_next")
        self.assertTrue(compound_done["reported_completion"])
        self.assertEqual(compound_done["requested_transition"], "next")
        self.assertEqual(
            compound_operation["operation"],
            "completion_and_next_transition",
        )
        self.assertEqual(compound_client.chat.completions.calls, [])

        off_session, off_socket, off_client, _, _, _ = self.run_question(
            transcript="혹시 융프라우 다녀오셨나요?",
            index=1,
        )
        off_reply = next(
            item["text"] for item in off_socket.text
            if item["type"] == "reply.delta"
        )
        off_operation = next(
            item for item in off_socket.text
            if item["type"] == "server.operation"
        )
        self.assertTrue(off_session.curated_protocol_session.active)
        self.assertEqual(off_session.curated_protocol_session.current_index, 1)
        self.assertIn("관련 실험실 자료", off_reply)
        self.assertNotIn("픽스처", off_reply)
        self.assertEqual(off_operation["operation"], "scope_reminder")
        self.assertEqual(off_client.chat.completions.calls, [])

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

    def test_live_paraphrase_and_context_routes_bypass_llm_and_retrieval(self):
        cases = (
            ("다시 한 번 말해줘", 2, "repeat", 2, None),
            ("이 단계 끝났어 다음 단계로 알려줘", 0, "next", 1, None),
            (
                "3단계에 대해 조금만 더 자세하게 설명해줄 수 있어?",
                2,
                "full_detail",
                2,
                None,
            ),
            (
                "그 용액은 어떻게 준비해?",
                2,
                "question",
                2,
                "candidate-a-step-02/current_step",
            ),
        )
        with patch(
            "voice_workflow_agent.server.search_approved_lab_references",
            side_effect=AssertionError("deterministic Tier 0 route must not retrieve"),
        ):
            for transcript, index, result_kind, closing_index, fact_id in cases:
                with self.subTest(transcript=transcript):
                    session, socket, client, _, _, _ = self.run_question(
                        transcript=transcript,
                        index=index,
                    )
                    done = next(
                        item for item in socket.text if item["type"] == "turn.done"
                    )
                    self.assertEqual(done["result_kind"], result_kind)
                    self.assertEqual(done["fact_id"], fact_id)
                    self.assertEqual(
                        session.curated_protocol_session.current_index,
                        closing_index,
                    )
                    self.assertEqual(client.chat.completions.calls, [])
                    self.assertEqual(
                        [item for item in socket.text if item["type"] == "tool.call"],
                        [],
                    )

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
            return_value=Transcription(
                "현재 단계를 완료했어. 다음 단계로 넘어가 줘", "ko"
            ),
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
            self.fixture.localized_fact(
                self.fixture.steps[2].step_id, "current_step"
            ),
        )
        self.assertTrue(socket.binary)
        display = next(
            item for item in socket.text if item["type"] == "reply.delta"
        )
        self.assertIn(self.fixture.steps[2].instruction_source_text, display["text"])
        self.assertEqual(
            display["primary_text"],
            self.fixture.localized_fact(
                self.fixture.steps[2].step_id, "current_step"
            ),
        )
        done = next(item for item in socket.text if item["type"] == "turn.done")
        self.assertEqual(done["route"], "curated_protocol")
        self.assertEqual(done["result_kind"], "question")
        self.assertEqual(done["fact_id"], "current_step")
        self.assertEqual(done["speech_mode"], "verified_fact")
        self.assertFalse(done["critical_warning_present"])
        self.assertEqual(done["tools_used"], [])
        self.assertEqual(session.curated_protocol_session.current_index, 2)

    def test_unsupported_question_returns_only_bounded_server_response(self):
        client = GroundedClient({
            "intent": "unsupported",
            "target_step_id": "candidate-a-step-03",
            "primary_text": "",
            "claims": [],
            "unsupported_parts": ["현재 단계 근거에 없음"],
        })
        session, socket, client, _, tts, llm = self.run_question(
            client=client,
            transcript="달의 질량은?",
        )
        spoken = tts.call_args.args[0]
        llm.assert_not_called()
        self.assertEqual(len(client.chat.completions.calls), 0)
        self.assertIn("관련 실험실 자료", spoken)
        self.assertNotIn("픽스처", spoken)
        self.assertNotIn(self.fixture.steps[2].instruction_source_text, spoken)
        done = next(item for item in socket.text if item["type"] == "turn.done")
        self.assertEqual(done["result_kind"], "off_topic")
        self.assertIsNone(done["fact_id"])
        self.assertEqual(session.curated_protocol_session.current_index, 2)

    def test_grounded_question_uses_current_step_only_and_never_mutates_state(self):
        client = GroundedClient({
            "intent": "grounded_explanation",
            "target_step_id": "candidate-a-step-03",
            "primary_text": "현재 단계에서는 Solution A 500 µL를 사용합니다.",
            "claims": [{
                "text": "원문에 Solution A 500 µL가 명시되어 있습니다.",
                "evidence_ids": ["current_step"],
                "inference_label": "direct_source_fact",
            }],
            "unsupported_parts": [],
        })
        session, socket, client, _, tts, llm = self.run_question(
            client=client,
            transcript="이 단계에서 용액은 얼마나 넣어?",
        )
        self.assertEqual(session.curated_protocol_session.current_index, 2)
        self.assertEqual(len(client.chat.completions.calls), 0)
        llm.assert_not_called()
        self.assertIn("활성 프로토콜", tts.call_args.args[0])
        reply = next(item for item in socket.text if item["type"] == "reply.delta")
        self.assertEqual(reply["answer_origin"], "current_protocol")
        self.assertIn(4, reply["source_pages"])
        self.assertEqual(
            reply["translation_status"], "deterministic_protocol_structure")
        operation = next(item for item in socket.text
                         if item["type"] == "server.operation")
        self.assertEqual(operation["operation"], "related_question_unresolved")

    def test_whole_protocol_metadata_is_provider_free_at_position_six(self):
        session, socket, client, _, tts, llm = self.run_question(
            transcript="지금은 총 몇 단계로 이루어져 있어?", index=5,
        )
        self.assertEqual(session.curated_protocol_session.current_index, 5)
        llm.assert_not_called()
        self.assertEqual(len(client.chat.completions.calls), 0)
        self.assertIn("총 25단계", tts.call_args.args[0])
        reply = next(item for item in socket.text if item["type"] == "reply.delta")
        self.assertIn("현재: 6/25", reply["text"])
        self.assertIn("남은 단계: 19", reply["text"])
        self.assertEqual(reply["answer_origin"], "current_protocol")
        self.assertEqual(
            next(item for item in socket.text
                 if item["type"] == "server.operation")["operation"],
            "protocol_structure_read",
        )
        self.assertFalse(any(
            item["type"] in {"tool.call", "research.state", "research.result"}
            for item in socket.text
        ))

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
                return_value=Transcription(
                    "현재 단계를 완료했어. 다음 단계를 진행해 줘.", "ko"
                ),
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
                return_value=Transcription(
                    "현재 단계를 완료했어. 다음 단계를 진행해 줘.", "ko"
                ),
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
                Transcription(
                    "현재 단계를 완료했어. 다음 단계를 진행해 줘.", "ko"
                ),
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
            (
                6,
                "현재 단계를 완료했어. 다음 단계로 안내해 줘",
                "clarify_completion",
                None,
                True,
            ),
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
            return_value=Transcription(
                "현재 단계를 완료했어. 다음 단계를 진행해 줘.", "ko"
            ),
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
        closing_state = session.curated_protocol_session.state(
            spoken_summary="2단계로 이동했습니다. 안내를 화면에 표시했습니다."
        )
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
            "현재 단계를 완료했어. 단계로 넘어가죠",
            "프로토콜 종료해줘",
        )
        expected = (
            ("start", True, "1", 1, None),
            ("current", True, "1", 1, None),
            ("question", True, "1", 1, "warning_1"),
            ("off_topic", True, "1", 1, None),
            ("related_question", True, "1", 1, None),
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
                self.server_generation = 0

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
                if self.receive_count == 2:
                    return {"text": json.dumps({
                        "type": "client.audio_ready",
                        "configuration_id": configuration_id,
                        "generation": self.server_generation,
                        "audio_context_state": "running",
                        "sample_rate": 48000,
                    })}
                await self.disconnect.wait()
                return {"type": "websocket.disconnect", "code": 1000}

            async def send_text(self, value: str) -> None:
                await super().send_text(value)
                if self.text[-1]["type"] == "session.ready":
                    self.server_generation = self.text[-1]["generation"]
                    self.ready.set()

        self_protocol_id = self.fixture.protocol_id
        socket = HandshakeSocket()
        transcripts = (
            "프로토콜을 시작해 줘",
            "현재 단계 알려줘",
            "다시 말해줘",
            "현재 단계를 완료했어. 단계로 넘어가죠",
            "용액 A는 어떻게 준비해?",
            "현재 단계 전체를 읽어줘",
            "프로토콜 전체 재료 목록을 알려줘",
            "프로토콜 종료해줘",
        )

        async def immediate(function, *args, **kwargs):
            return function(*args, **kwargs)

        async def scenario():
            server_task = asyncio.create_task(voice_socket(socket))
            await asyncio.wait_for(socket.ready.wait(), timeout=5)
            for _ in range(20):
                if captured and captured[0].greeting_emitted:
                    break
                await asyncio.sleep(0)
            session = captured[0]
            self.assertTrue(session.greeting_emitted)
            self.assertTrue(session.playback_ended(2_000_000_000))
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
                ("greeting", None),
                ("start", None),
                ("current", None),
                ("repeat", None),
                ("next", None),
                ("question", "current_step"),
                ("full_detail", None),
                ("protocol_query", None),
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
                "control",
                "verified_fact",
                "full_detail",
                "verified_fact",
                "stop",
            ],
        )
        step_one = self.fixture.steps[0].instruction_source_text
        step_two = self.fixture.steps[1].instruction_source_text
        spoken = [call.args[0] for call in tts.call_args_list]
        self.assertEqual(spoken, [
            f"Voice Workflow Agent입니다. 선택한 {self.fixture.title} "
            "프로토콜이 준비되었습니다. 시작할까요, 아니면 먼저 질문하시겠어요?",
            "실험을 시작합니다. 현재 1단계입니다. "
            "염색된 단백질 밴드를 준비해 작은 조각으로 나누고 "
            "지정된 AMBIC 용액이 담긴 튜브에 넣어 주세요.",
            "현재 1단계입니다. 안내를 화면에 표시했습니다.",
            "현재 1단계 안내를 다시 표시했습니다.",
            "2단계로 이동했습니다. 안내를 화면에 표시했습니다.",
            self.fixture.localized_fact(
                self.fixture.steps[1].step_id, "current_step"
            ),
            step_two,
            "시작 전에는 깨끗한 작업면과 도구를 준비하고, 화면의 검증된 재료와 장비 목록을 확인해 주세요.",
            "완료로 처리하지 않고 프로토콜 세션을 종료했습니다.",
        ])
        self.assertNotIn(step_one, spoken[:5])
        self.assertNotIn(step_two, spoken[:5])
        default_control_speech = (*spoken[:5], spoken[-1])
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
        self.assertEqual(replies[0], spoken[0])
        replies = replies[1:]
        for index in (0, 1, 2):
            self.assertIn(step_one, replies[index])
        self.assertIn(step_two, replies[3])
        self.assertIn(step_two, replies[4])
        self.assertIn("한국어 참고 번역", replies[4])
        self.assertIn("답변 · 한국어 참고 번역", replies[4])
        self.assertIn("Solution A", replies[4])
        self.assertIn(step_two, replies[5])
        # Protocol-wide inventory is a deterministic PDF-wide view; it does not
        # trigger optional retrieval or alter the active step.
        self.assertIn("검증된 재료", replies[6])
        self.assertIn("Acetonitrile", replies[6])
        related_reply = [
            item for item in socket.text if item["type"] == "reply.delta"
        ][6]
        self.assertIn(step_two, related_reply["source_texts"])
        self.assertEqual(
            replies[7],
            "완료로 처리하지 않고 프로토콜 세션을 종료했습니다.",
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
                ("start", True, "1", 2, None),
                ("current", True, "1", 2, None),
                ("repeat", True, "1", 2, None),
                ("next", True, "2", 3, None),
                ("question", True, "2", 3, None),
                ("full_detail", True, "2", 3, None),
                ("protocol_query", True, "2", 3, None),
                ("stop", False, None, 4, None),
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
            return_value=Transcription(
                "현재 단계를 완료했어. 다음 단계로 안내해 줘", "ko"
            ),
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
        self.assertIn("관찰 결과", tts.call_args.args[0])
        state_events = [
            item for item in socket.text
            if item["type"] == "protocol.fixture.state"
        ]
        self.assertEqual(len(state_events), 1)
        self.assertEqual(
            state_events[0]["state"],
            session.curated_protocol_session.state(
                spoken_summary=tts.call_args.args[0]
            ),
        )
        self.assertEqual(state_events[0]["action"], "clarify_completion")

    def test_polite_transition_requests_require_confirmation_while_preview_is_read_only(self):
        transition_prompts = (
            "다음 단계로 안내해줘.",
            "다음 단계로 안내해줄 수 있어?",
            "다음으로 넘어가자.",
            "다음 단계로 가줘.",
            "이제 다음 단계 진행해줘.",
            "다음으로 넘어갈 수 있어?",
            "다음 단계로 넘어가도 돼?",
            "다음 단계 진행할까?",
            "다음 단계로 이동해 주세요",
            "다음으로 갈래?",
        )
        for prompt in transition_prompts:
            with self.subTest(prompt=prompt):
                intent = classify_curated_control_intent(prompt, language="ko")
                self.assertEqual(intent.action, CuratedProtocolAction.CLARIFY_COMPLETION)
                self.assertTrue(intent.requires_confirmation)
                self.assertFalse(intent.allows_state_mutation)

        preview_prompts = (
            "다음 단계가 뭐야?",
            "다음 단계 내용만 미리 알려줘.",
            "다음 단계는 뭘 하는 단계야?",
            "다음 단계 미리보기 해줘.",
            "다음 스텝 미리 알려줘",
            "next step",
            "what is the next step",
        )
        for prompt in preview_prompts:
            with self.subTest(preview_prompt=prompt):
                intent = classify_curated_control_intent(prompt, language="ko")
                self.assertEqual(intent.action, CuratedProtocolAction.NEXT_INFORMATION)
                self.assertFalse(intent.allows_state_mutation)

    def test_multi_entity_claim_requests_and_spoken_summary(self):
        curated_session = CuratedProtocolSession(self.fixture)
        curated_session.active = True
        curated_session.current_index = 0
        prompt = "염색된 단백질 밴드와, 어, AMBIC가 무엇인지 설명해줘."
        intent = classify_curated_control_intent(prompt, language="ko")
        self.assertEqual(intent.action, CuratedProtocolAction.RELATED_QUESTION)
        self.assertIn("stained_protein_band", intent.requested_entities)
        self.assertIn("ambic", intent.requested_entities)
        plan = curated_session.plan(prompt, turn_id=1, language="ko")
        envelope = curated_session.protocol_answer_envelope(plan, language="ko")
        self.assertIn("단백질 밴드", envelope.speech_summary)
        self.assertIn("AMBIC", envelope.speech_summary)
        self.assertGreaterEqual(len(envelope.admitted_claim_ids), 2)

    def test_lab_domain_qa_tube_definition_does_not_repeat_step_instruction(self):
        curated_session = CuratedProtocolSession(self.fixture)
        curated_session.active = True
        curated_session.current_index = 3  # Step 4: Solution A discard
        prompt = "여기서 젤 밴드가 들어있는 튜브가 무엇이야?"
        intent = classify_curated_control_intent(prompt, language="ko")
        self.assertIn(intent.action, {CuratedProtocolAction.LAB_DOMAIN_QA, CuratedProtocolAction.RELATED_QUESTION})
        plan = curated_session.plan(prompt, turn_id=1, language="ko")
        envelope = curated_session.protocol_answer_envelope(plan, language="ko")
        self.assertNotIn("Solution A를 제거해 폐기합니다", envelope.speech_summary)
        self.assertIn("튜브", envelope.speech_summary)

    def test_timer_aware_spoken_prompt_appends_hint_on_timed_steps(self):
        curated_session = CuratedProtocolSession(self.fixture)
        curated_session.active = True
        curated_session.current_index = 2  # Step 3: has 15 min timer
        prompt = "현재 단계 알려줘"
        plan = curated_session.plan(prompt, turn_id=1, language="ko")
        self.assertIn("타이머를 시작하려면 말씀해주세요", plan.speech_text)

        # After starting timer, the prompt should not append the hint again
        curated_session.start_timer()
        plan_after = curated_session.plan(prompt, turn_id=2, language="ko")
        self.assertNotIn("타이머를 시작하려면 말씀해주세요", plan_after.speech_text)

    def test_voice_observation_capture_is_read_only_protocol_context(self):
        curated_session = CuratedProtocolSession(self.fixture)
        curated_session.active = True
        original_step = curated_session.current_index

        plan = curated_session.plan(
            "메모 추가해: 시료가 평소보다 탁해 보인다",
            turn_id=1,
            language="ko",
        )
        self.assertEqual(plan.action, CuratedProtocolAction.RECORD_OBSERVATION)
        self.assertTrue(plan.reported_observation)
        self.assertEqual(plan.observation_predicate, "note")
        self.assertIn("탁해", plan.observation_outcome)
        self.assertFalse(plan.state_changed)
        self.assertEqual(curated_session.current_index, original_step)

    def test_voice_observation_prompt_captures_exact_next_descriptive_turn(self):
        curated_session = CuratedProtocolSession(self.fixture)
        curated_session.active = True

        prompt = curated_session.plan(
            "record observation", turn_id=1, language="en"
        )
        self.assertEqual(prompt.action, CuratedProtocolAction.RECORD_OBSERVATION)
        self.assertFalse(prompt.reported_observation)
        self.assertEqual(
            curated_session.context_capsule().pending_interaction,
            "observation_note",
        )

        recorded = curated_session.plan(
            "The sample is slightly cloudy", turn_id=2, language="en"
        )
        self.assertEqual(recorded.action, CuratedProtocolAction.RECORD_OBSERVATION)
        self.assertTrue(recorded.reported_observation)
        self.assertEqual(
            recorded.observation_outcome, "The sample is slightly cloudy"
        )
        self.assertFalse(recorded.state_changed)
        self.assertEqual(curated_session.current_index, 0)

    def test_appearance_observation_does_not_become_anomaly_or_completion(self):
        curated_session = CuratedProtocolSession(self.fixture)
        curated_session.active = True

        plan = curated_session.plan(
            "The sample looks different", turn_id=1, language="en"
        )
        self.assertEqual(plan.action, CuratedProtocolAction.RECORD_OBSERVATION)
        self.assertEqual(plan.observation_predicate, "appearance")
        self.assertFalse(plan.reported_anomaly)
        self.assertFalse(plan.reported_completion)
        self.assertFalse(plan.state_changed)
        self.assertEqual(curated_session.current_index, 0)

        anomaly = curated_session.plan(
            "There is a spill", turn_id=2, language="en"
        )
        self.assertEqual(anomaly.action, CuratedProtocolAction.REPORT_ANOMALY)
