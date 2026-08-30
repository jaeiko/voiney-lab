"""Regression contracts for the production curated-runtime routing boundary."""

from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from voice_workflow_agent.curated_protocol import (
    CuratedProtocolAction,
    CuratedProtocolSession,
    classify_curated_control_intent,
    load_curated_protocol_fixture,
)
from voice_workflow_agent.intent_arbitration import RequestIntent
from voice_workflow_agent.language import Transcription
from voice_workflow_agent.runtime_routing import route_curated_runtime_turn
from voice_workflow_agent.server import ListenerSession, run_turn
from voice_workflow_agent.tools import ToolContext
from voice_workflow_agent.vad import TurnState


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "data/development_protocols/candidate_a_curated_analysis.json"
PROVENANCE = ROOT / "data/development_protocols/candidate_a_curated_analysis.provenance.json"
SOURCE_PDF = (Path(__file__).resolve().parents[1] / "data" / "runtime" / "candidate-a-source" / "in-gel-digestion.pdf")


class RecordingSocket:
    def __init__(self) -> None:
        self.text: list[dict[str, object]] = []
        self.binary: list[bytes] = []

    async def send_text(self, value: str) -> None:
        self.text.append(json.loads(value))

    async def send_bytes(self, value: bytes) -> None:
        self.binary.append(value)


class RuntimeIntentRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = load_curated_protocol_fixture(FIXTURE, PROVENANCE, SOURCE_PDF)

    def active_workflow(self) -> CuratedProtocolSession:
        workflow = CuratedProtocolSession(self.fixture)
        workflow.active = True
        workflow.current_index = 0
        return workflow

    def route(
        self,
        workflow: CuratedProtocolSession,
        transcript: str,
        turn_id: int = 1,
    ):
        return route_curated_runtime_turn(
            workflow,
            transcript,
            turn_id=turn_id,
            language="ko",
            configuration_id=41,
            generation=0,
        )

    def test_scenarios_a_to_f_use_specialized_read_only_runtime_routes(self) -> None:
        cases = (
            (
                "왜 해야 돼?",
                RequestIntent.LEARNING,
                "current_step_learning",
                "approved_step_metadata",
            ),
            (
                "현재 프로토콜 버전 알려줘.",
                RequestIntent.PROTOCOL_AUDIT,
                "protocol_audit",
                "protocol_metadata",
            ),
            (
                "어제 실험 이어줘.",
                RequestIntent.HISTORY_RESUME,
                "previous_experiment_resume",
                "session_history",
            ),
            (
                "이 실험 결과가 성공할까?",
                RequestIntent.UNCERTAINTY,
                "bounded_outcome_uncertainty",
                "bounded_uncertainty",
            ),
            (
                "주의사항 알려줘.",
                RequestIntent.LEARNING,
                "current_step_warning",
                "approved_step_metadata",
            ),
        )
        for turn_id, (transcript, expected_intent, expected_kind, origin) in enumerate(cases, 1):
            with self.subTest(transcript=transcript):
                workflow = self.active_workflow()
                before = workflow.state()
                routed = self.route(workflow, transcript, turn_id)
                self.assertEqual(routed.arbitration.intent, expected_intent)
                self.assertEqual(routed.plan.intent_kind, expected_kind)
                self.assertEqual(routed.plan.answer_origin, origin)
                self.assertFalse(routed.state_mutation)
                self.assertEqual(workflow.state(), before)
                self.assertNotIn(
                    "염색된 단백질 밴드를 준비해 작은 조각",
                    routed.plan.speech_text or "",
                )

    def test_combined_learning_and_next_previews_then_requires_explicit_confirmation(self) -> None:
        workflow = self.active_workflow()
        before = workflow.state()
        routed = self.route(
            workflow,
            "이 단계 왜 하는지 알려주고 다음 단계도 알려줘.",
        )
        self.assertEqual(
            routed.arbitration.intent,
            RequestIntent.COMBINED_LEARNING_NEXT,
        )
        self.assertEqual(routed.plan.intent_kind, "learning_and_next_preview")
        self.assertEqual(routed.plan.action, CuratedProtocolAction.CLARIFY_COMPLETION)
        self.assertIn("다음 단계는 2단계", routed.plan.speech_text or "")
        self.assertIn("실제로 완료", routed.plan.speech_text or "")
        self.assertFalse(routed.state_mutation)
        self.assertEqual(workflow.state()["current_step_label"], before["current_step_label"])

        confirmed = self.route(workflow, "네", turn_id=2)
        self.assertTrue(confirmed.state_mutation)
        self.assertEqual(workflow.state()["current_step_label"], "2")

    def test_visual_request_is_recognized_without_mutation(self) -> None:
        workflow = self.active_workflow()
        routed = self.route(workflow, "이 장비 사진을 찾아줘.")
        self.assertEqual(routed.arbitration.intent, RequestIntent.VISUAL)
        self.assertEqual(routed.plan.action, CuratedProtocolAction.VISUAL_REQUEST)
        self.assertEqual(routed.plan.visual_intent, "lab_equipment_image")
        self.assertFalse(routed.state_mutation)

    def test_timer_start_classifier_accepts_korean_english_and_mixed_script(self) -> None:
        for transcript in (
            "타이머 시작해줘",
            "타이머를 시작해줘",
            "Timer 시작해줘",
            "Timer를 시작해줘",
            "timer를 시작해 줘",
            "start timer",
            "timer start",
        ):
            with self.subTest(transcript=transcript):
                intent = classify_curated_control_intent(transcript, language="ko")
                self.assertEqual(intent.action, CuratedProtocolAction.START_TIMER)

    def test_timer_word_in_unrelated_sentences_does_not_start_timer(self) -> None:
        for transcript in (
            "timer 기능이 무엇인지 설명해줘",
            "timer 앱을 시작해줘",
            "timer가 언제 시작하는지 알려줘",
            "timer를 시작해도 돼?",
        ):
            with self.subTest(transcript=transcript):
                intent = classify_curated_control_intent(transcript, language="ko")
                self.assertNotEqual(intent.action, CuratedProtocolAction.START_TIMER)

    def test_timer_status_questions_keep_timer_status_priority(self) -> None:
        for transcript in (
            "타이머 얼마나 남았어?",
            "몇 분 남았어?",
            "타임 얼마나 남았어?",
            "타임 몇 분 남았어?",
            "타임 남은 시간 알려줘",
            "timer status",
            "how much time is left",
        ):
            with self.subTest(transcript=transcript):
                intent = classify_curated_control_intent(transcript, language="ko")
                self.assertEqual(intent.action, CuratedProtocolAction.TIMER_STATUS)

    def test_taim_variant_is_not_broadly_treated_as_timer_control(self) -> None:
        for transcript in (
            "점심 타임 얼마나 남았어?",
            "time 얼마나 남았어?",
            "타임이라는 단어 뜻 알려줘",
        ):
            with self.subTest(transcript=transcript):
                intent = classify_curated_control_intent(transcript, language="ko")
                self.assertNotIn(intent.action, {
                    CuratedProtocolAction.START_TIMER,
                    CuratedProtocolAction.TIMER_STATUS,
                })
                self.assertFalse(intent.allows_state_mutation)

    def test_mixed_script_timer_start_uses_production_runtime_boundary(self) -> None:
        workflow = self.active_workflow()
        workflow.current_index = 2
        self.assertEqual(workflow.timer_status()["state"], "not_started")

        routed = self.route(workflow, "Timer를 시작해줘.")

        self.assertEqual(routed.plan.action, CuratedProtocolAction.START_TIMER)
        self.assertTrue(routed.state_mutation)
        self.assertEqual(workflow.timer_status()["state"], "running")

    def test_websocket_cascade_emits_sanitized_route_decision_from_same_boundary(self) -> None:
        workflow = self.active_workflow()
        session = ListenerSession(
            tool_context=ToolContext(
                Path("/unused/offline-catalog"), None, "ko", "test_only"
            ),
            curated_protocol_session=workflow,
        )
        session.active = True
        session.active_turn_id = 1
        session.next_turn_id = 2
        session.turn_generations[1] = session.generation
        session.accept_configuration(41, "cascade", "ko", self.fixture.protocol_id)
        session.detector.state = TurnState.PROCESSING
        socket = RecordingSocket()

        async def immediate(function, *args, **kwargs):
            return function(*args, **kwargs)

        with patch(
            "voice_workflow_agent.server.transcribe",
            return_value=Transcription("왜 해야 돼?", "ko"),
        ), patch(
            "voice_workflow_agent.server.synthesize",
            return_value=b"\0\0",
        ), patch(
            "voice_workflow_agent.server.asyncio.to_thread",
            side_effect=immediate,
        ), patch(
            "voice_workflow_agent.server.stream_brain_turn",
            side_effect=AssertionError("curated production routing must win"),
        ):
            asyncio.run(run_turn(socket, session, b"\0\0", 1, 1))

        decision = next(
            item for item in socket.text if item["type"] == "turn.route_decision"
        )
        self.assertEqual(decision["intent"], "learning")
        self.assertEqual(decision["runtime_router"], "curated_protocol")
        self.assertEqual(decision["answer_origin"], "approved_step_metadata")
        self.assertFalse(decision["state_mutation"])
        self.assertNotIn("raw_transcript", decision)

    def test_korean_language_mismatch_cannot_complete_a_step(self) -> None:
        workflow = self.active_workflow()
        before = workflow.state()
        session = ListenerSession(
            tool_context=ToolContext(
                Path("/unused/offline-catalog"), None, "ko", "test_only"
            ),
            curated_protocol_session=workflow,
        )
        session.active = True
        session.active_turn_id = 1
        session.next_turn_id = 2
        session.turn_generations[1] = session.generation
        session.accept_configuration(41, "cascade", "ko", self.fixture.protocol_id)
        session.detector.state = TurnState.PROCESSING
        socket = RecordingSocket()

        async def immediate(function, *args, **kwargs):
            return function(*args, **kwargs)

        with patch(
            "voice_workflow_agent.server.transcribe",
            return_value=Transcription("Current step complete.", "en"),
        ), patch(
            "voice_workflow_agent.server.synthesize",
            return_value=b"\0\0",
        ), patch(
            "voice_workflow_agent.server.asyncio.to_thread",
            side_effect=immediate,
        ), patch(
            "voice_workflow_agent.server.route_curated_runtime_turn_with_semantics",
            side_effect=AssertionError("language mismatch must stop before routing"),
        ):
            asyncio.run(run_turn(socket, session, b"\0\0", 1, 1))

        mismatch = next(
            item for item in socket.text if item["type"] == "stt.language_mismatch"
        )
        self.assertEqual(mismatch["configured_language"], "ko")
        self.assertEqual(mismatch["detected_language"], "en")
        self.assertFalse(mismatch["mutation_authorized"])
        self.assertFalse(any(item["type"] == "transcript" for item in socket.text))
        reply = next(item for item in socket.text if item["type"] == "reply.complete")
        self.assertEqual(
            reply["text"],
            "음성 인식 언어가 불확실합니다. 다시 한 번 말씀해 주세요.",
        )
        self.assertEqual(workflow.state(), before)


if __name__ == "__main__":
    unittest.main()
