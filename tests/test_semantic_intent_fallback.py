"""Paraphrase corpus and production-boundary contracts for the semantic fallback.

Every model interaction here is fake-backed; nothing needs live credentials.

The corpus is one table with an explicit ``stage`` column, because the property
under test is not only *what* an utterance resolves to but *which stage resolved
it*.  ``DETERMINISTIC`` rows run against a resolver that raises if it is called
at all, so the live-validated fast path is proved to stay model-free; ``SEMANTIC``
rows exercise an injected proposal; ``REFUSED`` rows inject a deliberately wrong
proposal and pin the policy reason that discards it.

Every row asserts the resolved action, whether mutation was allowed, and the
canonical workflow state before and after the turn.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any
from unittest.mock import patch

from voice_workflow_agent.curated_protocol import (
    _SEMANTIC_INTENT_PROJECTION,
    CuratedProtocolAction,
    CuratedProtocolSession,
    curated_intent_from_semantic_decision,
    load_curated_protocol_fixture,
)
from voice_workflow_agent.language import Transcription
from voice_workflow_agent.runtime_routing import (
    probe_curated_semantic_fallback,
    route_curated_runtime_turn,
    route_curated_runtime_turn_with_semantics,
)
from voice_workflow_agent.semantic_intent import (
    SEMANTIC_INTENT_TIERS,
    SemanticIntent,
    SemanticIntentDecision,
    SemanticIntentProposal,
    SemanticIntentSettings,
    SemanticIntentTier,
    propose_semantic_intent,
)
from voice_workflow_agent.server import ListenerSession, run_turn
from voice_workflow_agent.tools import ToolContext
from voice_workflow_agent.vad import TurnState


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "data/development_protocols/candidate_a_curated_analysis.json"
PROVENANCE = (
    ROOT / "data/development_protocols/candidate_a_curated_analysis.provenance.json"
)
SOURCE_PDF = ROOT / "data" / "runtime" / "candidate-a-source" / "in-gel-digestion.pdf"

ENABLED = SemanticIntentSettings(enabled=True)

#: Step index 2 ("Step 3") is a 15-minute protocol-defined incubation, so it is
#: the honest place to exercise timer language.
TIMED_STEP_INDEX = 2


class Stage(str, Enum):
    """Which routing stage is expected to resolve a corpus row."""

    DETERMINISTIC = "deterministic"
    SEMANTIC = "semantic"
    REFUSED = "refused"


@dataclass(frozen=True)
class Case:
    """One corpus row."""

    utterance: str
    stage: Stage
    action: CuratedProtocolAction
    mutates: bool
    reason: str
    #: Injected proposal for SEMANTIC/REFUSED rows.
    intent: SemanticIntent | None = None
    mutation_requested: bool = False
    evidence: str = ""
    #: Start the protocol-defined step timer before the turn.
    running_timer: bool = False
    #: Route against a not-yet-started session.
    inactive: bool = False


class RecordingSocket:
    def __init__(self) -> None:
        self.text: list[dict[str, Any]] = []
        self.binary: list[bytes] = []

    async def send_text(self, value: str) -> None:
        self.text.append(json.loads(value))

    async def send_bytes(self, value: bytes) -> None:
        self.binary.append(value)


def fake_resolver(case: Case):
    """One injected model output; a resolver is just a coroutine over context."""

    async def resolve(_context):
        assert case.intent is not None
        return SemanticIntentProposal(
            intent=case.intent,
            target=None,
            mutation_requested=case.mutation_requested,
            confidence=0.95,
            explicit_action_evidence=case.evidence,
            reason="injected test proposal",
        )

    return resolve


async def refusing_resolver(_context):  # pragma: no cover - must never run
    raise AssertionError(
        "the deterministic fast path must not consult a semantic model"
    )


def canonical_state(workflow: CuratedProtocolSession) -> dict[str, Any]:
    """Exactly the workflow authority a turn may not change without permission."""

    state = workflow.state()
    return {
        "active": state["active"],
        "current_step_id": state["current_step_id"],
        "current_step_label": state["current_step_label"],
        "revision": state["revision"],
        "workflow_status": state["workflow_status"],
        "at_final_step": state["at_final_step"],
        "block_reason": state["block_reason"],
        "timer_state": workflow.timer_status()["state"],
        "timer_step_index": workflow._timer_step_index,
        "timer_started_at": workflow._timer_started_at,
    }


class SemanticFallbackTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = load_curated_protocol_fixture(FIXTURE, PROVENANCE, SOURCE_PDF)

    def workflow(
        self, step_index: int = TIMED_STEP_INDEX, *, active: bool = True
    ) -> CuratedProtocolSession:
        workflow = CuratedProtocolSession(self.fixture)
        workflow.active = active
        workflow.current_index = step_index if active else 0
        workflow._workflow_status = "active" if active else "preview"
        return workflow

    def route(
        self, workflow, transcript, resolver, *,
        turn_id=1, quality=None, settings=ENABLED,
    ):
        return asyncio.run(
            route_curated_runtime_turn_with_semantics(
                workflow,
                transcript,
                turn_id=turn_id,
                language="ko",
                transcript_quality=quality,
                configuration_id=41,
                generation=0,
                resolver=resolver,
                semantic_settings=settings,
            )
        )

    def check(self, case: Case) -> None:
        """Route one corpus row and assert stage, action, mutation, and state."""

        workflow = self.workflow(active=not case.inactive)
        if case.running_timer:
            workflow.start_timer()
        before = canonical_state(workflow)
        resolver = (
            refusing_resolver
            if case.stage is Stage.DETERMINISTIC
            else fake_resolver(case)
        )

        routed = self.route(workflow, case.utterance, resolver)

        self.assertEqual(routed.plan.action, case.action)
        self.assertEqual(routed.plan.state_changed, case.mutates)
        assert routed.semantic is not None
        self.assertEqual(routed.semantic.reason_code, case.reason)
        self.assertEqual(
            routed.semantic.accepted, case.stage is Stage.SEMANTIC
        )
        if case.stage is Stage.DETERMINISTIC:
            self.assertEqual(routed.semantic.status, "skipped")
        after = canonical_state(workflow)
        if case.mutates:
            self.assertNotEqual(after, before)
        else:
            self.assertEqual(after, before)


class DeterministicFastPathTests(SemanticFallbackTestCase):
    """Live-validated deterministic behavior must never reach a model."""

    CORPUS = (
        # Protocol start (live-validated with a real microphone).
        Case("시작해줘", Stage.DETERMINISTIC, CuratedProtocolAction.START,
             True, "deterministic_route_resolved", inactive=True),
        # Timer status.
        Case("타이머 얼마나 남았어?", Stage.DETERMINISTIC,
             CuratedProtocolAction.TIMER_STATUS, False,
             "deterministic_route_resolved", running_timer=True),
        Case("타임 얼마나 남았어?", Stage.DETERMINISTIC,
             CuratedProtocolAction.TIMER_STATUS, False,
             "deterministic_route_resolved", running_timer=True),
        Case("몇 분 남았어?", Stage.DETERMINISTIC,
             CuratedProtocolAction.TIMER_STATUS, False,
             "deterministic_route_resolved", running_timer=True),
        # Timer start, including the mixed-script form.
        Case("타이머 시작해줘", Stage.DETERMINISTIC,
             CuratedProtocolAction.START_TIMER, True,
             "deterministic_route_resolved"),
        Case("Timer를 시작해줘", Stage.DETERMINISTIC,
             CuratedProtocolAction.START_TIMER, True,
             "deterministic_route_resolved"),
        # Current step and next-step preview.
        Case("현재 단계 알려줘", Stage.DETERMINISTIC,
             CuratedProtocolAction.CURRENT, False,
             "deterministic_route_resolved"),
        Case("다음 단계 알려줘", Stage.DETERMINISTIC,
             CuratedProtocolAction.NEXT_INFORMATION, False,
             "deterministic_route_resolved"),
        # Completion.
        Case("이 단계 다 했어", Stage.DETERMINISTIC, CuratedProtocolAction.NEXT,
             True, "deterministic_route_resolved"),
        Case("다시 말해줘", Stage.DETERMINISTIC, CuratedProtocolAction.REPEAT,
             False, "deterministic_route_resolved"),
        # Deliberate deterministic guards: already-made decisions, never re-opened.
        Case("끝났다고 하면 어떻게 돼?", Stage.DETERMINISTIC,
             CuratedProtocolAction.OFF_TOPIC, False,
             "deterministic_route_resolved"),
        Case("타이밍이 중요한 이유가 뭐야?", Stage.DETERMINISTIC,
             CuratedProtocolAction.QUESTION, False,
             "deterministic_route_resolved"),
    )

    def test_deterministic_utterances_never_consult_the_model(self) -> None:
        for case in self.CORPUS:
            with self.subTest(utterance=case.utterance):
                self.check(case)

    def test_the_probe_is_pure_and_reports_why_it_declined(self) -> None:
        workflow = self.workflow()
        before = canonical_state(workflow)
        probe = probe_curated_semantic_fallback(
            workflow, "타이머 얼마나 남았어?", language="ko"
        )
        self.assertFalse(probe.needed)
        self.assertEqual(probe.reason_code, "deterministic_route_resolved")
        self.assertIsNone(probe.context)
        self.assertEqual(canonical_state(workflow), before)

    def test_the_probe_fires_only_for_unresolved_language(self) -> None:
        workflow = self.workflow()
        workflow.start_timer()
        probe = probe_curated_semantic_fallback(
            workflow, "Time 얼마나 남았어?", language="ko"
        )
        self.assertTrue(probe.needed)
        self.assertEqual(probe.reason_code, "deterministic_off_topic")
        assert probe.context is not None
        self.assertEqual(probe.context.current_step_label, "3")
        self.assertEqual(probe.context.step_timer_state, "running")
        self.assertTrue(probe.context.step_timer_configured)

    def test_a_degraded_transcript_never_reaches_the_model(self) -> None:
        workflow = self.workflow()
        probe = probe_curated_semantic_fallback(
            workflow, "Time 얼마나 남았어?", language="ko",
            transcript_quality="low_confidence",
        )
        self.assertFalse(probe.needed)
        self.assertEqual(probe.reason_code, "degraded_transcript")

    def test_synchronous_routing_stays_free_of_any_semantic_stage(self) -> None:
        workflow = self.workflow()
        routed = route_curated_runtime_turn(
            workflow, "타이머 얼마나 남았어?", turn_id=1, language="ko"
        )
        self.assertEqual(routed.plan.action, CuratedProtocolAction.TIMER_STATUS)
        self.assertIsNone(routed.semantic)

    def test_duplicate_timer_start_does_not_reset_the_timer(self) -> None:
        workflow = self.workflow()
        first = self.route(workflow, "타이머 시작해줘", refusing_resolver, turn_id=1)
        self.assertEqual(first.plan.action, CuratedProtocolAction.START_TIMER)
        started = canonical_state(workflow)
        second = self.route(workflow, "타이머 시작해줘", refusing_resolver, turn_id=2)
        self.assertEqual(second.plan.action, CuratedProtocolAction.START_TIMER)
        self.assertEqual(canonical_state(workflow), started)

    def test_current_step_completion_advances_exactly_one_step(self) -> None:
        workflow = self.workflow()
        before = workflow.current_index
        routed = self.route(workflow, "이 단계 다 했어", refusing_resolver)
        self.assertEqual(routed.plan.action, CuratedProtocolAction.NEXT)
        self.assertTrue(routed.plan.state_changed)
        self.assertEqual(workflow.current_index, before + 1)


class SemanticParaphraseCorpusTests(SemanticFallbackTestCase):
    """Table-driven paraphrase corpus: meaning, not surface form."""

    CORPUS = (
        # --- timer status -------------------------------------------------
        Case("Time 얼마나 남았어?", Stage.SEMANTIC,
             CuratedProtocolAction.TIMER_STATUS, False, "semantic_timer_status",
             intent=SemanticIntent.TIMER_STATUS, evidence="얼마나 남았",
             running_timer=True),
        Case("Timer, 얼마나 남았어?", Stage.SEMANTIC,
             CuratedProtocolAction.TIMER_STATUS, False, "semantic_timer_status",
             intent=SemanticIntent.TIMER_STATUS, evidence="얼마나 남았",
             running_timer=True),
        Case("얼마나 남았지?", Stage.SEMANTIC,
             CuratedProtocolAction.TIMER_STATUS, False, "semantic_timer_status",
             intent=SemanticIntent.TIMER_STATUS, evidence="얼마나 남았",
             running_timer=True),
        Case("남은 시간 알려줘", Stage.SEMANTIC,
             CuratedProtocolAction.TIMER_STATUS, False, "semantic_timer_status",
             intent=SemanticIntent.TIMER_STATUS, evidence="남은 시간",
             running_timer=True),
        # --- timer start --------------------------------------------------
        Case("시간 재줘", Stage.SEMANTIC, CuratedProtocolAction.START_TIMER,
             True, "semantic_start_timer", intent=SemanticIntent.START_TIMER,
             mutation_requested=True, evidence="재줘"),
        Case("타이머 돌려줘", Stage.SEMANTIC, CuratedProtocolAction.START_TIMER,
             True, "semantic_start_timer", intent=SemanticIntent.START_TIMER,
             mutation_requested=True, evidence="돌려줘"),
        Case("이제 시간 좀 재줄래?", Stage.SEMANTIC,
             CuratedProtocolAction.START_TIMER, False, "semantic_start_timer",
             intent=SemanticIntent.START_TIMER, mutation_requested=True,
             evidence="재줄래", running_timer=True),
        Case("시간 재면 어떻게 돼?", Stage.SEMANTIC,
             CuratedProtocolAction.TIMER_STATUS, False,
             "semantic_timer_information",
             intent=SemanticIntent.TIMER_INFORMATION, running_timer=True),
        # --- current step -------------------------------------------------
        Case("지금 뭐 하는 단계야?", Stage.SEMANTIC, CuratedProtocolAction.CURRENT,
             False, "semantic_current_step", intent=SemanticIntent.CURRENT_STEP),
        Case("지금 어디까지 했어?", Stage.SEMANTIC, CuratedProtocolAction.CURRENT,
             False, "semantic_current_step", intent=SemanticIntent.CURRENT_STEP),
        # --- next preview (read-only, never completion) --------------------
        Case("그 다음엔 뭐해?", Stage.SEMANTIC,
             CuratedProtocolAction.NEXT_INFORMATION, False,
             "semantic_next_step_information",
             intent=SemanticIntent.NEXT_STEP_INFORMATION),
        Case("다음에 뭘 해야 돼?", Stage.SEMANTIC,
             CuratedProtocolAction.NEXT_INFORMATION, False,
             "semantic_next_step_information",
             intent=SemanticIntent.NEXT_STEP_INFORMATION),
        Case("다음 단계가 뭔지 설명해 줘.", Stage.SEMANTIC,
             CuratedProtocolAction.NEXT_INFORMATION, False,
             "semantic_next_step_information",
             intent=SemanticIntent.NEXT_STEP_INFORMATION),
        # --- not done (stays on the current step) --------------------------
        Case("아직 안 됐어", Stage.SEMANTIC,
             CuratedProtocolAction.DECLINE_COMPLETION, False,
             "semantic_not_done", intent=SemanticIntent.NOT_DONE),
        Case("아직 덜 됐어", Stage.SEMANTIC,
             CuratedProtocolAction.DECLINE_COMPLETION, False,
             "semantic_not_done", intent=SemanticIntent.NOT_DONE),
        Case("조금 더 해야 돼", Stage.SEMANTIC,
             CuratedProtocolAction.DECLINE_COMPLETION, False,
             "semantic_not_done", intent=SemanticIntent.NOT_DONE),
        # --- completion: staged as a confirmation, never a transition ------
        Case("완료됐어", Stage.SEMANTIC,
             CuratedProtocolAction.CLARIFY_COMPLETION, False,
             "semantic_complete_current_step",
             intent=SemanticIntent.COMPLETE_CURRENT_STEP,
             mutation_requested=True, evidence="완료됐어"),
        Case("끝났어", Stage.SEMANTIC,
             CuratedProtocolAction.CLARIFY_COMPLETION, False,
             "semantic_complete_current_step",
             intent=SemanticIntent.COMPLETE_CURRENT_STEP,
             mutation_requested=True, evidence="끝났어"),
    )

    def test_paraphrases_resolve_to_the_bounded_action_they_mean(self) -> None:
        for case in self.CORPUS:
            with self.subTest(utterance=case.utterance):
                self.check(case)

    def test_no_read_only_paraphrase_advances_the_protocol(self) -> None:
        for case in self.CORPUS:
            if case.mutates:
                continue
            with self.subTest(utterance=case.utterance):
                workflow = self.workflow()
                if case.running_timer:
                    workflow.start_timer()
                before = workflow.current_index
                self.route(workflow, case.utterance, fake_resolver(case))
                self.assertEqual(workflow.current_index, before)

    def test_an_accepted_timer_question_reports_real_server_owned_time(self) -> None:
        case = self.CORPUS[0]
        workflow = self.workflow()
        workflow.start_timer()
        routed = self.route(workflow, case.utterance, fake_resolver(case))
        self.assertEqual(routed.plan.action, CuratedProtocolAction.TIMER_STATUS)
        self.assertIn("남았", routed.plan.speech_text)
        self.assertEqual(workflow.timer_status()["state"], "running")

    def test_a_semantic_timer_start_cannot_reset_an_active_deadline(self) -> None:
        case = next(
            item for item in self.CORPUS
            if item.utterance == "이제 시간 좀 재줄래?"
        )
        workflow = self.workflow()
        workflow.start_timer()
        deadline_before = workflow._timer_started_at + workflow._timer_duration_seconds
        routed = self.route(workflow, case.utterance, fake_resolver(case))
        deadline_after = workflow._timer_started_at + workflow._timer_duration_seconds
        self.assertEqual(routed.plan.action, CuratedProtocolAction.START_TIMER)
        self.assertFalse(routed.plan.state_changed)
        self.assertEqual(deadline_after, deadline_before)
        self.assertIn("이미 진행 중", routed.plan.speech_text)

    def test_a_timer_hypothetical_explains_without_starting_or_resetting(self) -> None:
        case = next(
            item for item in self.CORPUS
            if item.utterance == "시간 재면 어떻게 돼?"
        )
        workflow = self.workflow()
        workflow.start_timer()
        before = canonical_state(workflow)
        routed = self.route(workflow, case.utterance, fake_resolver(case))
        self.assertEqual(routed.plan.action, CuratedProtocolAction.TIMER_STATUS)
        self.assertFalse(routed.plan.state_changed)
        self.assertEqual(canonical_state(workflow), before)
        self.assertIn("초기화하지 않습니다", routed.plan.speech_text)

    def test_a_timer_question_without_a_started_timer_never_fabricates_one(self) -> None:
        workflow = self.workflow()
        self.assertEqual(workflow.timer_status()["state"], "not_started")
        before = canonical_state(workflow)
        routed = self.route(
            workflow,
            "Time 얼마나 남았어?",
            fake_resolver(
                Case("Time 얼마나 남았어?", Stage.SEMANTIC,
                     CuratedProtocolAction.TIMER_STATUS, False,
                     "semantic_timer_status",
                     intent=SemanticIntent.TIMER_STATUS, evidence="얼마나 남았")
            ),
        )
        self.assertEqual(routed.plan.action, CuratedProtocolAction.TIMER_STATUS)
        self.assertIn("아직 타이머가 시작되지 않았습니다", routed.plan.speech_text)
        self.assertEqual(canonical_state(workflow), before)

    def test_a_step_without_a_protocol_timer_refuses_the_timer_reading(self) -> None:
        workflow = self.workflow(step_index=0)
        self.assertEqual(workflow.timer_status()["duration_seconds"], 0)
        before = canonical_state(workflow)
        routed = self.route(
            workflow,
            "얼마나 남았지?",
            fake_resolver(
                Case("얼마나 남았지?", Stage.REFUSED,
                     CuratedProtocolAction.OFF_TOPIC, False,
                     "no_step_timer_available",
                     intent=SemanticIntent.TIMER_STATUS, evidence="얼마나 남았")
            ),
        )
        self.assertEqual(routed.plan.action, CuratedProtocolAction.OFF_TOPIC)
        assert routed.semantic is not None
        self.assertEqual(routed.semantic.reason_code, "no_step_timer_available")
        self.assertEqual(canonical_state(workflow), before)

    def test_a_staged_completion_is_committed_only_by_the_researcher(self) -> None:
        workflow = self.workflow()
        before = workflow.current_index
        case = Case("완료됐어", Stage.SEMANTIC,
                    CuratedProtocolAction.CLARIFY_COMPLETION, False,
                    "semantic_complete_current_step",
                    intent=SemanticIntent.COMPLETE_CURRENT_STEP,
                    mutation_requested=True, evidence="완료됐어")
        staged = self.route(workflow, case.utterance, fake_resolver(case), turn_id=1)
        self.assertEqual(staged.plan.action, CuratedProtocolAction.CLARIFY_COMPLETION)
        self.assertFalse(staged.plan.state_changed)
        self.assertEqual(workflow.current_index, before)

        confirmed = self.route(workflow, "응 맞아", refusing_resolver, turn_id=2)
        self.assertEqual(confirmed.plan.action, CuratedProtocolAction.NEXT)
        self.assertTrue(confirmed.plan.state_changed)
        self.assertEqual(workflow.current_index, before + 1)

    def test_a_staged_completion_declined_by_the_researcher_stays_put(self) -> None:
        workflow = self.workflow()
        before = canonical_state(workflow)
        case = Case("끝났어", Stage.SEMANTIC,
                    CuratedProtocolAction.CLARIFY_COMPLETION, False,
                    "semantic_complete_current_step",
                    intent=SemanticIntent.COMPLETE_CURRENT_STEP,
                    mutation_requested=True, evidence="끝났어")
        self.route(workflow, case.utterance, fake_resolver(case), turn_id=1)
        declined = self.route(workflow, "아직 안 됐어", refusing_resolver, turn_id=2)
        self.assertEqual(
            declined.plan.action, CuratedProtocolAction.DECLINE_COMPLETION
        )
        self.assertFalse(declined.plan.state_changed)
        self.assertEqual(canonical_state(workflow), before)


class SemanticAmbiguityAndFailClosedTests(SemanticFallbackTestCase):
    """Ambiguous, hypothetical, and mislabeled utterances must not mutate."""

    CORPUS = (
        # "time" alone is never a timer question.
        Case("what time is it?", Stage.REFUSED, CuratedProtocolAction.OFF_TOPIC,
             False, "clock_time_question", intent=SemanticIntent.TIMER_STATUS,
             evidence="time", running_timer=True),
        Case("Time이라는 단어가 무슨 뜻이야?", Stage.REFUSED,
             CuratedProtocolAction.OFF_TOPIC, False,
             "no_remaining_time_evidence", intent=SemanticIntent.TIMER_STATUS,
             evidence="Time", running_timer=True),
        # A read-only preview request may never be re-read as completion.
        Case("다음 단계가 뭔지 설명만 해줘", Stage.REFUSED,
             CuratedProtocolAction.RELATED_QUESTION, False,
             "interrogative_not_authorized",
             intent=SemanticIntent.COMPLETE_CURRENT_STEP,
             mutation_requested=True, evidence="다음 단계"),
        # Ending a run stays a deterministically worded command.
        Case("이만 정리하자", Stage.REFUSED, CuratedProtocolAction.OFF_TOPIC,
             False, "checkpoint_requires_explicit_command",
             intent=SemanticIntent.STOP, mutation_requested=True,
             evidence="정리하자"),
        # A confident but unevidenced mutation is still refused.
        Case("아직 안 됐어", Stage.REFUSED, CuratedProtocolAction.OFF_TOPIC,
             False, "evidence_not_verbatim",
             intent=SemanticIntent.COMPLETE_CURRENT_STEP,
             mutation_requested=True, evidence="I am finished"),
        Case("조금 더 해야 돼", Stage.REFUSED, CuratedProtocolAction.OFF_TOPIC,
             False, "mutation_not_requested",
             intent=SemanticIntent.COMPLETE_CURRENT_STEP,
             mutation_requested=False, evidence="더 해야 돼"),
        # The model saying "I don't know" is honored, not overridden.
        Case("오늘 날씨 어때", Stage.REFUSED, CuratedProtocolAction.OFF_TOPIC,
             False, "model_unknown", intent=SemanticIntent.UNKNOWN),
    )

    def test_ambiguous_and_mislabeled_utterances_never_mutate_state(self) -> None:
        for case in self.CORPUS:
            with self.subTest(utterance=case.utterance):
                self.check(case)

    def test_completion_hypothetical_explains_the_gate_without_completing(self) -> None:
        workflow = self.workflow()
        before = canonical_state(workflow)
        routed = self.route(
            workflow, "끝났다고 하면 어떻게 돼?", refusing_resolver
        )
        self.assertEqual(routed.plan.action, CuratedProtocolAction.OFF_TOPIC)
        self.assertFalse(routed.plan.state_changed)
        self.assertEqual(canonical_state(workflow), before)
        self.assertIn("관찰 게이트", routed.plan.speech_text)
        self.assertIn("상태를 변경하지 않았습니다", routed.plan.speech_text)

    def test_an_unavailable_model_leaves_the_deterministic_outcome_intact(self) -> None:
        async def unavailable(_context):
            raise TimeoutError("provider unreachable")

        workflow = self.workflow()
        before = canonical_state(workflow)
        routed = self.route(workflow, "Time 얼마나 남았어?", unavailable)
        self.assertEqual(routed.plan.action, CuratedProtocolAction.OFF_TOPIC)
        self.assertFalse(routed.plan.state_changed)
        self.assertEqual(canonical_state(workflow), before)
        assert routed.semantic is not None
        self.assertEqual(routed.semantic.status, "failed")

    def test_a_silent_model_leaves_the_deterministic_outcome_intact(self) -> None:
        async def silent(_context):
            return None

        workflow = self.workflow()
        before = canonical_state(workflow)
        routed = self.route(workflow, "완료됐어", silent)
        self.assertEqual(routed.plan.action, CuratedProtocolAction.OFF_TOPIC)
        self.assertEqual(canonical_state(workflow), before)
        assert routed.semantic is not None
        self.assertEqual(routed.semantic.status, "unavailable")

    def test_a_pending_confirmation_gate_keeps_ownership_of_the_turn(self) -> None:
        workflow = self.workflow()
        case = Case("완료됐어", Stage.SEMANTIC,
                    CuratedProtocolAction.CLARIFY_COMPLETION, False,
                    "semantic_complete_current_step",
                    intent=SemanticIntent.COMPLETE_CURRENT_STEP,
                    mutation_requested=True, evidence="완료됐어")
        staged = self.route(workflow, case.utterance, fake_resolver(case), turn_id=1)
        self.assertEqual(staged.plan.action, CuratedProtocolAction.CLARIFY_COMPLETION)
        probe = probe_curated_semantic_fallback(
            workflow, "아직 안 됐어", language="ko"
        )
        self.assertFalse(probe.needed)
        self.assertEqual(probe.reason_code, "pending_gate_owns_turn")

    def test_a_proposal_for_an_already_resolved_turn_is_ignored(self) -> None:
        workflow = self.workflow()
        before = canonical_state(workflow)
        routed = route_curated_runtime_turn(
            workflow,
            "타이머 얼마나 남았어?",
            turn_id=1,
            language="ko",
            semantic_proposal=SemanticIntentProposal(
                intent=SemanticIntent.COMPLETE_CURRENT_STEP,
                target=None,
                mutation_requested=True,
                confidence=1.0,
                explicit_action_evidence="타이머",
                reason="injected out of band",
            ),
            semantic_settings=ENABLED,
        )
        self.assertEqual(routed.plan.action, CuratedProtocolAction.TIMER_STATUS)
        self.assertEqual(canonical_state(workflow), before)
        assert routed.semantic is not None
        self.assertEqual(
            routed.semantic.reason_code, "deterministic_route_owns_turn"
        )

    def test_source_defined_observation_checkpoints_keep_their_gate(self) -> None:
        index = next(
            position
            for position, step in enumerate(self.fixture.steps)
            if step.source_label == "7"
        )
        workflow = self.workflow(step_index=index)
        before = canonical_state(workflow)
        case = Case("완료됐어", Stage.SEMANTIC,
                    CuratedProtocolAction.CLARIFY_COMPLETION, False,
                    "semantic_complete_current_step",
                    intent=SemanticIntent.COMPLETE_CURRENT_STEP,
                    mutation_requested=True, evidence="완료됐어")
        routed = self.route(workflow, case.utterance, fake_resolver(case))
        self.assertFalse(routed.plan.state_changed)
        self.assertEqual(canonical_state(workflow), before)

    def test_a_reviewed_checkpoint_answer_overrides_any_proposal(self) -> None:
        """Only source-defined checkpoint semantics may pick the reviewed branch.

        Step 7's endpoint is "is the gel destained and transparent". Both
        answers below start from a deterministic catch-all, so the resolver *is*
        consulted - and the source-defined observation predicate still decides
        the branch, whatever the model proposed.
        """

        index = next(
            position
            for position, step in enumerate(self.fixture.steps)
            if step.source_label == "7"
        )
        rogue = Case("", Stage.REFUSED, CuratedProtocolAction.NEXT, False, "",
                     intent=SemanticIntent.COMPLETE_CURRENT_STEP,
                     mutation_requested=True, evidence="")

        negative = self.workflow(step_index=index)
        routed = self.route(negative, "아직 투명하지 않아", fake_resolver(rogue))
        self.assertEqual(routed.plan.intent_kind, "direct_negative_observation")
        self.assertFalse(routed.plan.state_changed)
        self.assertEqual(negative.current_index, index)

        positive = self.workflow(step_index=index)
        routed = self.route(positive, "젤이 투명해졌어", fake_resolver(rogue))
        self.assertEqual(routed.plan.intent_kind, "direct_positive_observation")
        self.assertTrue(routed.plan.state_changed)
        self.assertEqual(positive.current_index, index + 1)

    def test_an_ordinary_not_done_answer_never_advances(self) -> None:
        """`아직 안 됐어` has no reviewed branch here, so it must simply hold."""

        index = next(
            position
            for position, step in enumerate(self.fixture.steps)
            if step.source_label == "7"
        )
        workflow = self.workflow(step_index=index)
        before = canonical_state(workflow)
        case = Case("아직 안 됐어", Stage.SEMANTIC,
                    CuratedProtocolAction.DECLINE_COMPLETION, False,
                    "semantic_not_done", intent=SemanticIntent.NOT_DONE)
        routed = self.route(workflow, case.utterance, fake_resolver(case))
        self.assertFalse(routed.plan.state_changed)
        self.assertEqual(canonical_state(workflow), before)


class SemanticMutationAuthorityTests(SemanticFallbackTestCase):
    """Structural proof that a proposal can never authorize a transition."""

    def test_the_projection_table_is_the_complete_allowlist(self) -> None:
        projected = set(_SEMANTIC_INTENT_PROJECTION)
        # `stop` and `unknown` deliberately have no projection at all.
        self.assertEqual(
            set(SemanticIntent) - projected,
            {SemanticIntent.STOP, SemanticIntent.UNKNOWN},
        )
        for intent, projection in _SEMANTIC_INTENT_PROJECTION.items():
            with self.subTest(intent=intent):
                self.assertIsInstance(
                    projection["action"], CuratedProtocolAction
                )
                if SEMANTIC_INTENT_TIERS[intent] is SemanticIntentTier.CHECKPOINT:
                    self.assertFalse(projection["allows_state_mutation"])
                    self.assertTrue(projection["requires_confirmation"])

    def test_a_refused_decision_projects_to_nothing(self) -> None:
        refused = SemanticIntentDecision(
            False, "low_confidence_for_mutation",
            intent=SemanticIntent.COMPLETE_CURRENT_STEP,
        )
        self.assertIsNone(
            curated_intent_from_semantic_decision(
                refused, language="ko", normalized_transcript="완료됐어"
            )
        )

    def test_no_proposal_of_any_kind_can_advance_the_protocol_step(self) -> None:
        """Every vocabulary member, at maximum confidence, demanding mutation."""

        for intent in SemanticIntent:
            with self.subTest(intent=intent):
                workflow = self.workflow()
                before = workflow.current_index

                async def resolver(_context, intent=intent):
                    return SemanticIntentProposal(
                        intent=intent,
                        target="current",
                        mutation_requested=True,
                        confidence=1.0,
                        explicit_action_evidence="완료됐어",
                        reason="maximally confident rogue proposal",
                    )

                routed = self.route(workflow, "완료됐어", resolver)
                self.assertEqual(workflow.current_index, before)
                self.assertNotEqual(routed.plan.action, CuratedProtocolAction.NEXT)

    def test_an_accepted_checkpoint_proposal_changes_nothing_by_itself(self) -> None:
        for intent, tier in SEMANTIC_INTENT_TIERS.items():
            if tier is not SemanticIntentTier.CHECKPOINT:
                continue
            with self.subTest(intent=intent):
                workflow = self.workflow()
                before = canonical_state(workflow)
                case = Case("완료됐어", Stage.REFUSED,
                            CuratedProtocolAction.OFF_TOPIC, False, "",
                            intent=intent, mutation_requested=True,
                            evidence="완료됐어")
                routed = self.route(workflow, case.utterance, fake_resolver(case))
                self.assertFalse(routed.plan.state_changed)
                self.assertEqual(canonical_state(workflow), before)


class SemanticProviderFailureTests(SemanticFallbackTestCase):
    """Timeout, transport error, and malformed output all fail closed."""

    @staticmethod
    def provider_resolver(settings, *, content=None, error=None, delay=0.0):
        """A resolver built on the real provider call, with a fake client."""

        class Client:
            model = "fake-model"

            class chat:
                class completions:
                    @staticmethod
                    async def create(**_kwargs):
                        if delay:
                            await asyncio.sleep(delay)
                        if error is not None:
                            raise error
                        message = type("M", (), {"content": content})
                        choice = type("C", (), {"message": message()})
                        return type("R", (), {"choices": [choice()]})()

        async def resolve(context):
            return await propose_semantic_intent(
                Client(), context, settings=settings
            )

        return resolve

    def assert_unchanged(self, resolver, *, settings=ENABLED) -> None:
        workflow = self.workflow()
        workflow.start_timer()
        before = canonical_state(workflow)
        routed = self.route(
            workflow, "Time 얼마나 남았어?", resolver, settings=settings
        )
        self.assertEqual(routed.plan.action, CuratedProtocolAction.OFF_TOPIC)
        self.assertFalse(routed.plan.state_changed)
        self.assertEqual(canonical_state(workflow), before)
        assert routed.semantic is not None
        self.assertFalse(routed.semantic.accepted)
        self.assertEqual(routed.semantic.reason_code, "no_proposal")

    def test_a_provider_timeout_leaves_canonical_state_unchanged(self) -> None:
        settings = SemanticIntentSettings(enabled=True, timeout_seconds=0.2)
        self.assert_unchanged(
            self.provider_resolver(settings, delay=2.0), settings=settings
        )

    def test_a_provider_transport_error_leaves_canonical_state_unchanged(self) -> None:
        self.assert_unchanged(
            self.provider_resolver(
                ENABLED, error=ConnectionError("no route to provider")
            )
        )

    def test_invalid_provider_output_leaves_canonical_state_unchanged(self) -> None:
        cases = {
            "not json": "not json at all",
            "invented action": json.dumps({
                "intent": "advance_two_steps", "target": None,
                "mutation_requested": True, "confidence": 1.0,
                "explicit_action_evidence": "남았", "reason": "x",
            }),
            "missing fields": json.dumps({"intent": "timer_status"}),
            "confidence out of range": json.dumps({
                "intent": "timer_status", "target": None,
                "mutation_requested": False, "confidence": 7,
                "explicit_action_evidence": "남았", "reason": "x",
            }),
        }
        for name, content in cases.items():
            with self.subTest(case=name):
                self.assert_unchanged(
                    self.provider_resolver(ENABLED, content=content)
                )


class SemanticProductionBoundaryTests(SemanticFallbackTestCase):
    """The WebSocket turn must route through the same arbitration boundary."""

    def listener(self, workflow, *, enabled: bool) -> ListenerSession:
        session = ListenerSession(
            tool_context=ToolContext(
                Path("/unused/offline-catalog"), None, "ko", "test_only"
            ),
            curated_protocol_session=workflow,
            semantic_intent_settings=SemanticIntentSettings(enabled=enabled),
        )
        session.active = True
        session.active_turn_id = 1
        session.next_turn_id = 2
        session.turn_generations[1] = session.generation
        session.accept_configuration(41, "cascade", "ko", self.fixture.protocol_id)
        session.detector.state = TurnState.PROCESSING
        return session

    def run_socket_turn(self, session, transcript, *, client_factory):
        socket = RecordingSocket()

        async def immediate(function, *args, **kwargs):
            return function(*args, **kwargs)

        with patch.dict(
            "os.environ", {"XAI_API_KEY": "test-only-key"}, clear=False
        ), patch(
            "voice_workflow_agent.server.transcribe",
            return_value=Transcription(transcript, "ko"),
        ), patch(
            "voice_workflow_agent.server.synthesize", return_value=b"\0\0",
        ), patch(
            "voice_workflow_agent.server.asyncio.to_thread", side_effect=immediate,
        ), patch(
            "voice_workflow_agent.server.AsyncOpenAI", side_effect=client_factory,
        ), patch(
            "voice_workflow_agent.server.stream_brain_turn",
            side_effect=AssertionError("curated production routing must win"),
        ):
            asyncio.run(run_turn(socket, session, b"\0\0", 1, 1))
        return socket

    @staticmethod
    def route_decision(socket) -> dict[str, Any]:
        return next(
            item for item in socket.text if item["type"] == "turn.route_decision"
        )

    @staticmethod
    def proposal_client(payload: dict[str, object] | None = None, *, error=None):
        """Fake the existing xAI chat boundary; no credentials are ever used."""

        def factory(*_args, **_kwargs):
            class Client:
                model = "fake-model"

                class chat:
                    class completions:
                        @staticmethod
                        async def create(**_create_kwargs):
                            if error is not None:
                                raise error
                            content = json.dumps(payload, ensure_ascii=False)
                            message = type("M", (), {"content": content})
                            choice = type("C", (), {"message": message()})
                            return type("R", (), {"choices": [choice()]})()

            return Client()

        return factory

    def test_the_websocket_turn_publishes_the_semantic_ruling(self) -> None:
        workflow = self.workflow()
        workflow.start_timer()
        session = self.listener(workflow, enabled=True)
        before = canonical_state(workflow)
        socket = self.run_socket_turn(
            session,
            "Time 얼마나 남았어?",
            client_factory=self.proposal_client({
                "intent": "timer_status",
                "target": None,
                "mutation_requested": False,
                "confidence": 0.93,
                "explicit_action_evidence": "얼마나 남았",
                "reason": "asks how much timer is left",
            }),
        )
        decision = self.route_decision(socket)
        self.assertEqual(decision["runtime_router"], "curated_protocol")
        self.assertEqual(decision["action"], "timer_status")
        self.assertFalse(decision["state_mutation"])
        fallback = decision["semantic_fallback"]
        assert isinstance(fallback, dict)
        self.assertEqual(fallback["status"], "accepted")
        self.assertEqual(fallback["proposed_intent"], "timer_status")
        self.assertTrue(fallback["accepted"])
        self.assertNotIn("Time", json.dumps(fallback, ensure_ascii=False))
        self.assertEqual(canonical_state(workflow), before)

    def test_the_websocket_turn_records_workspace_allowlisted_dimensions(self) -> None:
        workflow = self.workflow()
        workflow.start_timer()
        session = self.listener(workflow, enabled=True)
        with patch(
            "voice_workflow_agent.server._record_workspace_metric"
        ) as record_metric:
            self.run_socket_turn(
                session,
                "Time 얼마나 남았어?",
                client_factory=self.proposal_client({
                    "intent": "timer_status",
                    "target": "current",
                    "mutation_requested": False,
                    "confidence": 0.93,
                    "explicit_action_evidence": "얼마나 남았",
                    "reason": "asks how much timer is left",
                }),
            )
        semantic_call = next(
            call for call in record_metric.call_args_list
            if call.kwargs.get("metric_name") == "semantic_intent_fallback"
        )
        self.assertEqual(
            set(semantic_call.kwargs["dimensions"]),
            {"route", "status", "reason_code", "intent", "event_kind"},
        )

    def test_the_websocket_turn_never_calls_a_model_for_resolved_language(self) -> None:
        workflow = self.workflow()
        session = self.listener(workflow, enabled=True)
        socket = self.run_socket_turn(
            session,
            "타이머 얼마나 남았어?",
            client_factory=AssertionError(
                "a deterministic turn must not build a provider client"
            ),
        )
        decision = self.route_decision(socket)
        self.assertEqual(decision["action"], "timer_status")
        self.assertEqual(
            decision["semantic_fallback"]["reason_code"],
            "deterministic_route_resolved",
        )

    def test_a_disabled_fallback_leaves_the_turn_exactly_as_it_was(self) -> None:
        workflow = self.workflow()
        session = self.listener(workflow, enabled=False)
        socket = self.run_socket_turn(
            session,
            "Time 얼마나 남았어?",
            client_factory=AssertionError(
                "a disabled fallback must not build a provider client"
            ),
        )
        decision = self.route_decision(socket)
        self.assertEqual(decision["action"], "off_topic")
        self.assertFalse(decision["state_mutation"])
        self.assertIsNone(decision["semantic_fallback"])

    def test_a_provider_failure_leaves_workflow_state_unchanged(self) -> None:
        workflow = self.workflow()
        session = self.listener(workflow, enabled=True)
        before = canonical_state(workflow)
        socket = self.run_socket_turn(
            session,
            "완료됐어",
            client_factory=self.proposal_client(
                error=ConnectionError("no route to provider")
            ),
        )
        decision = self.route_decision(socket)
        self.assertEqual(decision["action"], "off_topic")
        self.assertFalse(decision["state_mutation"])
        self.assertEqual(
            decision["semantic_fallback"]["reason_code"], "no_proposal"
        )
        self.assertEqual(canonical_state(workflow), before)

    def test_malformed_provider_output_leaves_workflow_state_unchanged(self) -> None:
        workflow = self.workflow()
        session = self.listener(workflow, enabled=True)
        before = canonical_state(workflow)
        socket = self.run_socket_turn(
            session,
            "완료됐어",
            client_factory=self.proposal_client({
                "intent": "advance_two_steps",
                "target": "9",
                "mutation_requested": True,
                "confidence": 1.0,
                "explicit_action_evidence": "완료됐어",
                "reason": "invented action",
            }),
        )
        decision = self.route_decision(socket)
        self.assertFalse(decision["state_mutation"])
        self.assertEqual(
            decision["semantic_fallback"]["reason_code"], "no_proposal"
        )
        self.assertEqual(canonical_state(workflow), before)

    def test_a_rogue_completion_proposal_cannot_advance_the_protocol(self) -> None:
        workflow = self.workflow()
        session = self.listener(workflow, enabled=True)
        before = canonical_state(workflow)
        socket = self.run_socket_turn(
            session,
            "다음 단계가 뭔지 설명만 해줘",
            client_factory=self.proposal_client({
                "intent": "complete_current_step",
                "target": "current",
                "mutation_requested": True,
                "confidence": 1.0,
                "explicit_action_evidence": "다음 단계",
                "reason": "researcher is moving on",
            }),
        )
        decision = self.route_decision(socket)
        self.assertFalse(decision["state_mutation"])
        self.assertEqual(
            decision["semantic_fallback"]["reason_code"],
            "interrogative_not_authorized",
        )
        self.assertEqual(canonical_state(workflow), before)

    def test_a_proposal_may_not_redirect_a_mutation_onto_another_step(self) -> None:
        workflow = self.workflow()
        session = self.listener(workflow, enabled=True)
        before = canonical_state(workflow)
        socket = self.run_socket_turn(
            session,
            "완료됐어",
            client_factory=self.proposal_client({
                "intent": "complete_current_step",
                "target": "9",
                "mutation_requested": True,
                "confidence": 1.0,
                "explicit_action_evidence": "완료됐어",
                "reason": "step nine is done",
            }),
        )
        decision = self.route_decision(socket)
        self.assertFalse(decision["state_mutation"])
        self.assertEqual(
            decision["semantic_fallback"]["reason_code"], "target_not_current_step"
        )
        self.assertEqual(canonical_state(workflow), before)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
