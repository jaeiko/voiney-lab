"""Policy contracts for the bounded semantic intent fallback.

These tests need no protocol fixture and no credentials: they pin the rules that
decide whether a *proposal* is ever allowed to influence a turn.
"""

from __future__ import annotations

import asyncio
import time
import unittest

from voice_workflow_agent.semantic_intent import (
    PROPOSABLE_INTENTS,
    SEMANTIC_INTENT_TIERS,
    SemanticIntent,
    SemanticIntentContext,
    SemanticIntentDecision,
    SemanticIntentProposal,
    SemanticIntentSettings,
    SemanticIntentTier,
    evaluate_semantic_proposal,
    outcome_for_decision,
    parse_semantic_proposal,
    propose_semantic_intent,
    resolve_semantic_intent,
    semantic_fallback_reason,
)


def context(
    utterance: str,
    *,
    workflow_active: bool = True,
    timer_state: str = "running",
    timer_configured: bool = True,
    remaining: int | None = 300,
    pending: str | None = None,
    reason: str = "deterministic_off_topic",
) -> SemanticIntentContext:
    return SemanticIntentContext(
        utterance=utterance,
        normalized_utterance=utterance.casefold(),
        language="ko",
        session_phase="active" if workflow_active else "preview",
        workflow_active=workflow_active,
        current_step_label="3",
        step_timer_state=timer_state,
        step_timer_configured=timer_configured,
        step_timer_remaining_seconds=remaining,
        pending_interaction=pending,
        deterministic_reason=reason,
    )


def proposal(
    intent: SemanticIntent,
    *,
    mutation: bool = False,
    confidence: float = 0.95,
    evidence: str = "",
    target: str | None = None,
) -> SemanticIntentProposal:
    return SemanticIntentProposal(
        intent=intent,
        target=target,
        mutation_requested=mutation,
        confidence=confidence,
        explicit_action_evidence=evidence,
        reason="unit test",
    )


ENABLED = SemanticIntentSettings(enabled=True)


class SemanticIntentVocabularyTests(unittest.TestCase):
    def test_every_proposable_intent_has_exactly_one_tier(self) -> None:
        proposable = set(PROPOSABLE_INTENTS) - {SemanticIntent.UNKNOWN.value}
        tiered = {intent.value for intent in SEMANTIC_INTENT_TIERS}
        self.assertEqual(proposable, tiered)

    def test_checkpoint_tier_is_limited_to_completion_and_stop(self) -> None:
        checkpoint = {
            intent
            for intent, tier in SEMANTIC_INTENT_TIERS.items()
            if tier is SemanticIntentTier.CHECKPOINT
        }
        self.assertEqual(
            checkpoint,
            {SemanticIntent.COMPLETE_CURRENT_STEP, SemanticIntent.STOP},
        )

    def test_a_decision_never_claims_mutation_authority(self) -> None:
        decision = evaluate_semantic_proposal(
            proposal(SemanticIntent.TIMER_STATUS),
            context("얼마나 남았어?"),
            ENABLED,
        )
        self.assertTrue(decision.accepted)
        self.assertFalse(decision.state_mutation)


class SemanticFallbackGateTests(unittest.TestCase):
    def test_resolved_deterministic_actions_never_reach_a_model(self) -> None:
        for action in (
            "timer_status", "start_timer", "next", "current", "next_information",
            "question", "clarify_completion", "record_observation", "stop",
            "pause", "resume", "visual_request", "protocol_query", "step_range",
        ):
            with self.subTest(action=action):
                self.assertIsNone(
                    semantic_fallback_reason(
                        deterministic_action=action,
                        deterministic_intent_kind="workflow_command",
                    )
                )

    def test_catch_all_outcomes_warrant_a_proposal(self) -> None:
        cases = (
            ("off_topic", "off_topic", "deterministic_off_topic"),
            ("unsupported", "unsupported", "deterministic_unsupported"),
            (
                "related_question",
                "related_question",
                "deterministic_generic_related_question",
            ),
        )
        for action, kind, expected in cases:
            with self.subTest(action=action):
                self.assertEqual(
                    semantic_fallback_reason(
                        deterministic_action=action,
                        deterministic_intent_kind=kind,
                    ),
                    expected,
                )

    def test_specialized_related_questions_stay_authoritative(self) -> None:
        self.assertIsNone(
            semantic_fallback_reason(
                deterministic_action="related_question",
                deterministic_intent_kind="related_safety_question",
            )
        )

    def test_deliberate_non_mutating_completion_guards_are_never_reopened(self) -> None:
        for kind in (
            "hypothetical_completion", "future_completion", "negated_completion",
            "quoted_completion", "completion_criteria_question",
            "ambiguous_completion",
        ):
            with self.subTest(kind=kind):
                self.assertIsNone(
                    semantic_fallback_reason(
                        deterministic_action="off_topic",
                        deterministic_intent_kind=kind,
                    )
                )

    def test_specialized_arbitration_intents_keep_their_own_route(self) -> None:
        for intent in (
            "learning", "protocol_audit", "history_resume", "uncertainty",
            "combined_learning_next", "visual",
        ):
            with self.subTest(intent=intent):
                self.assertIsNone(
                    semantic_fallback_reason(
                        deterministic_action="off_topic",
                        deterministic_intent_kind="off_topic",
                        arbitration_intent=intent,
                    )
                )


class SemanticProposalParsingTests(unittest.TestCase):
    def payload(self, **overrides: object) -> dict[str, object]:
        base: dict[str, object] = {
            "intent": "timer_status",
            "target": None,
            "mutation_requested": False,
            "confidence": 0.9,
            "explicit_action_evidence": "남았",
            "reason": "asks remaining time",
        }
        base.update(overrides)
        return base

    def test_a_well_formed_payload_parses(self) -> None:
        parsed = parse_semantic_proposal(self.payload())
        assert parsed is not None
        self.assertIs(parsed.intent, SemanticIntent.TIMER_STATUS)
        self.assertEqual(parsed.confidence, 0.9)

    def test_malformed_payloads_fail_closed(self) -> None:
        cases = {
            "not a mapping": "timer_status",
            "unsupported intent": self.payload(intent="delete_protocol"),
            "missing intent": {k: v for k, v in self.payload().items() if k != "intent"},
            "confidence out of range": self.payload(confidence=1.4),
            "confidence not numeric": self.payload(confidence="high"),
            "confidence boolean": self.payload(confidence=True),
            "mutation not boolean": self.payload(mutation_requested="yes"),
            "evidence not a string": self.payload(explicit_action_evidence=7),
            "reason missing": {
                k: v for k, v in self.payload().items() if k != "reason"
            },
        }
        for name, payload in cases.items():
            with self.subTest(case=name):
                self.assertIsNone(parse_semantic_proposal(payload))


class SemanticPolicyTests(unittest.TestCase):
    def assert_rejected(self, decision: SemanticIntentDecision, reason: str) -> None:
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason_code, reason)

    def test_unknown_is_always_refused(self) -> None:
        self.assert_rejected(
            evaluate_semantic_proposal(
                proposal(SemanticIntent.UNKNOWN), context("오늘 날씨 어때"), ENABLED
            ),
            "model_unknown",
        )

    def test_absent_proposal_is_refused(self) -> None:
        self.assert_rejected(
            evaluate_semantic_proposal(None, context("아무말"), ENABLED),
            "no_proposal",
        )

    def test_read_only_and_mutating_confidence_floors_differ(self) -> None:
        self.assert_rejected(
            evaluate_semantic_proposal(
                proposal(SemanticIntent.TIMER_STATUS, confidence=0.4),
                context("얼마나 남았어"),
                ENABLED,
            ),
            "low_confidence",
        )
        self.assert_rejected(
            evaluate_semantic_proposal(
                proposal(
                    SemanticIntent.COMPLETE_CURRENT_STEP,
                    mutation=True, confidence=0.7, evidence="완료됐어",
                ),
                context("완료됐어"),
                ENABLED,
            ),
            "low_confidence_for_mutation",
        )

    def test_confidence_alone_never_authorizes_a_mutation(self) -> None:
        cases = {
            "mutation_not_requested": proposal(
                SemanticIntent.COMPLETE_CURRENT_STEP,
                mutation=False, confidence=1.0, evidence="완료됐어",
            ),
            "evidence_not_verbatim": proposal(
                SemanticIntent.COMPLETE_CURRENT_STEP,
                mutation=True, confidence=1.0, evidence="the step is finished",
            ),
        }
        for reason, candidate in cases.items():
            with self.subTest(reason=reason):
                self.assert_rejected(
                    evaluate_semantic_proposal(candidate, context("완료됐어"), ENABLED),
                    reason,
                )

    def test_question_and_hypothetical_shapes_cannot_mutate(self) -> None:
        self.assert_rejected(
            evaluate_semantic_proposal(
                proposal(
                    SemanticIntent.COMPLETE_CURRENT_STEP,
                    mutation=True, confidence=1.0, evidence="끝났다고",
                ),
                context("끝났다고 하면 어떻게 돼?"),
                ENABLED,
            ),
            "interrogative_not_authorized",
        )
        self.assert_rejected(
            evaluate_semantic_proposal(
                proposal(
                    SemanticIntent.START_TIMER,
                    mutation=True, confidence=1.0, evidence="시작하면",
                ),
                context("타이머 시작하면 돼"),
                ENABLED,
            ),
            "hypothetical_not_authorized",
        )

    def test_a_polite_timer_request_may_reach_the_authoritative_timer_gate(self) -> None:
        decision = evaluate_semantic_proposal(
            proposal(
                SemanticIntent.START_TIMER,
                mutation=True,
                confidence=1.0,
                evidence="재줄래",
            ),
            context("이제 시간 좀 재줄래?"),
            ENABLED,
        )
        self.assertTrue(decision.accepted, decision.reason_code)

    def test_current_step_timer_targets_preserve_the_step_fence(self) -> None:
        for target in (
            "timer", "step_timer", "current_step_timer", "step 3 timer",
            "3단계 타이머", "현재 단계 타이머",
        ):
            with self.subTest(target=target):
                decision = evaluate_semantic_proposal(
                    proposal(
                        SemanticIntent.START_TIMER,
                        mutation=True,
                        confidence=0.99,
                        evidence="시작해줘",
                        target=target,
                    ),
                    context(
                        "Time을 시작해줘.",
                        timer_state="not_started",
                    ),
                    ENABLED,
                )
                self.assertTrue(decision.accepted, decision.reason_code)
                self.assertIs(decision.intent, SemanticIntent.START_TIMER)

        refused = evaluate_semantic_proposal(
            proposal(
                SemanticIntent.START_TIMER,
                mutation=True,
                confidence=0.99,
                evidence="시작해줘",
                target="step 4 timer",
            ),
            context("Time을 시작해줘.", timer_state="not_started"),
            ENABLED,
        )
        self.assert_rejected(refused, "target_not_current_step")

    def test_running_timer_start_downgrades_at_the_informational_floor(self) -> None:
        decision = evaluate_semantic_proposal(
            proposal(
                SemanticIntent.START_TIMER,
                mutation=True,
                confidence=0.8,
                evidence="시간 좀 재줄래",
                target="timer",
            ),
            context("이제 이제 시간 좀 재줄래?"),
            ENABLED,
        )
        self.assertTrue(decision.accepted, decision.reason_code)
        self.assertEqual(decision.reason_code, "semantic_running_timer_read_only")
        self.assertIs(decision.intent, SemanticIntent.TIMER_INFORMATION)
        self.assertFalse(decision.state_mutation)

    def test_low_confidence_start_cannot_start_a_not_started_timer(self) -> None:
        decision = evaluate_semantic_proposal(
            proposal(
                SemanticIntent.START_TIMER,
                mutation=True,
                confidence=0.8,
                evidence="시간 좀 재줄래",
                target="timer",
            ),
            context(
                "이제 이제 시간 좀 재줄래?",
                timer_state="not_started",
                remaining=0,
            ),
            ENABLED,
        )
        self.assert_rejected(decision, "low_confidence_for_mutation")

    def test_below_informational_confidence_running_timer_proposal_is_refused(self) -> None:
        decision = evaluate_semantic_proposal(
            proposal(
                SemanticIntent.START_TIMER,
                mutation=True,
                confidence=0.5,
                evidence="시간 좀 재줄래",
                target="timer",
            ),
            context("이제 이제 시간 좀 재줄래?"),
            ENABLED,
        )
        self.assert_rejected(decision, "low_confidence")

    def test_a_mutation_may_not_be_redirected_onto_another_step(self) -> None:
        self.assert_rejected(
            evaluate_semantic_proposal(
                proposal(
                    SemanticIntent.COMPLETE_CURRENT_STEP,
                    mutation=True, confidence=1.0, evidence="완료됐어",
                    target="7",
                ),
                context("완료됐어"),
                ENABLED,
            ),
            "target_not_current_step",
        )

    def test_naming_the_authoritative_current_step_is_allowed(self) -> None:
        for target in (None, "", "current", "authoritative_current_step", "이 단계", "3"):
            with self.subTest(target=target):
                decision = evaluate_semantic_proposal(
                    proposal(
                        SemanticIntent.COMPLETE_CURRENT_STEP,
                        mutation=True, confidence=0.99, evidence="완료됐어",
                        target=target,
                    ),
                    context("완료됐어"),
                    ENABLED,
                )
                self.assertTrue(decision.accepted, decision.reason_code)
                self.assertTrue(decision.requires_confirmation)

    def test_a_server_owned_gate_keeps_ownership_of_the_turn(self) -> None:
        self.assert_rejected(
            evaluate_semantic_proposal(
                proposal(
                    SemanticIntent.START_TIMER,
                    mutation=True, confidence=1.0, evidence="재줘",
                ),
                context("시간 재줘", pending="completion_gate"),
                ENABLED,
            ),
            "pending_gate_owns_turn",
        )

    def test_stop_is_never_authorized_semantically(self) -> None:
        self.assert_rejected(
            evaluate_semantic_proposal(
                proposal(
                    SemanticIntent.STOP,
                    mutation=True, confidence=1.0, evidence="정리하자",
                ),
                context("이만 정리하자"),
                ENABLED,
            ),
            "checkpoint_requires_explicit_command",
        )

    def test_completion_is_only_ever_staged_as_a_confirmation(self) -> None:
        decision = evaluate_semantic_proposal(
            proposal(
                SemanticIntent.COMPLETE_CURRENT_STEP,
                mutation=True, confidence=0.99, evidence="완료됐어",
            ),
            context("완료됐어"),
            ENABLED,
        )
        self.assertTrue(decision.accepted)
        self.assertTrue(decision.requires_confirmation)
        self.assertFalse(decision.state_mutation)

    def test_completion_without_completion_evidence_is_refused(self) -> None:
        self.assert_rejected(
            evaluate_semantic_proposal(
                proposal(
                    SemanticIntent.COMPLETE_CURRENT_STEP,
                    mutation=True, confidence=0.99, evidence="세척했어",
                ),
                context("세척했어"),
                ENABLED,
            ),
            "no_completion_evidence",
        )

    def test_a_clock_time_question_is_not_a_timer_question(self) -> None:
        for utterance in ("what time is it?", "지금 몇 시야?"):
            with self.subTest(utterance=utterance):
                self.assert_rejected(
                    evaluate_semantic_proposal(
                        proposal(SemanticIntent.TIMER_STATUS, confidence=1.0),
                        context(utterance),
                        ENABLED,
                    ),
                    "clock_time_question",
                )

    def test_timer_status_needs_remaining_time_evidence(self) -> None:
        self.assert_rejected(
            evaluate_semantic_proposal(
                proposal(SemanticIntent.TIMER_STATUS, confidence=1.0),
                context("time이라는 단어가 무슨 뜻이야?"),
                ENABLED,
            ),
            "no_remaining_time_evidence",
        )

    def test_timer_status_is_refused_when_no_step_timer_exists(self) -> None:
        self.assert_rejected(
            evaluate_semantic_proposal(
                proposal(SemanticIntent.TIMER_STATUS, confidence=1.0),
                context(
                    "얼마나 남았어?",
                    timer_state="not_started",
                    timer_configured=False,
                    remaining=0,
                ),
                ENABLED,
            ),
            "no_step_timer_available",
        )

    def test_a_started_step_timer_answers_a_paraphrased_question(self) -> None:
        for utterance in (
            "타이머 얼마나 남았어?", "타임 얼마나 남았어?", "Time 얼마나 남았어?",
            "몇 분 남았어?", "남은 시간 알려줘", "얼마나 남았지?",
        ):
            with self.subTest(utterance=utterance):
                decision = evaluate_semantic_proposal(
                    proposal(SemanticIntent.TIMER_STATUS, confidence=0.9),
                    context(utterance),
                    ENABLED,
                )
                self.assertTrue(decision.accepted, decision.reason_code)
                self.assertIs(decision.intent, SemanticIntent.TIMER_STATUS)

    def test_step_scoped_reads_require_an_active_workflow(self) -> None:
        for intent in (
            SemanticIntent.CURRENT_STEP,
            SemanticIntent.NEXT_STEP_INFORMATION,
            SemanticIntent.NOT_DONE,
            SemanticIntent.REPEAT,
        ):
            with self.subTest(intent=intent):
                self.assert_rejected(
                    evaluate_semantic_proposal(
                        proposal(intent, confidence=1.0),
                        context("지금 어디까지 했어?", workflow_active=False),
                        ENABLED,
                    ),
                    "workflow_not_active",
                )


class SemanticResolverFailureTests(unittest.TestCase):
    def resolve(self, resolver, settings=ENABLED):
        return asyncio.run(
            resolve_semantic_intent(
                resolver, context("얼마나 남았어?"), settings=settings
            )
        )

    def test_a_disabled_fallback_never_calls_the_resolver(self) -> None:
        async def resolver(_context):  # pragma: no cover - must not run
            raise AssertionError("disabled fallback must not call a model")

        found, outcome = self.resolve(resolver, SemanticIntentSettings(enabled=False))
        self.assertIsNone(found)
        self.assertEqual(outcome.status, "skipped")

    def test_a_raising_resolver_fails_closed(self) -> None:
        async def resolver(_context):
            raise RuntimeError("provider unavailable")

        found, outcome = self.resolve(resolver)
        self.assertIsNone(found)
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.reason_code, "resolver_error")

    def test_an_empty_resolver_result_fails_closed(self) -> None:
        async def resolver(_context):
            return None

        found, outcome = self.resolve(resolver)
        self.assertIsNone(found)
        self.assertEqual(outcome.status, "unavailable")

    def test_a_slow_provider_is_bounded_by_the_configured_timeout(self) -> None:
        class SlowClient:
            model = "fake-model"

            class chat:
                class completions:
                    @staticmethod
                    async def create(**_kwargs):
                        await asyncio.sleep(5)

        started = time.perf_counter()
        result = asyncio.run(
            propose_semantic_intent(
                SlowClient(),
                context("얼마나 남았어?"),
                settings=SemanticIntentSettings(
                    enabled=True, timeout_seconds=0.2
                ),
            )
        )
        self.assertIsNone(result)
        self.assertLess(time.perf_counter() - started, 0.6)

    def test_invalid_structured_output_fails_closed(self) -> None:
        for content in (
            "not json",
            '{"intent":"delete_protocol","target":null,"mutation_requested":true,'
            '"confidence":0.99,"explicit_action_evidence":"x","reason":"y"}',
            '{"intent":"timer_status"}',
        ):
            with self.subTest(content=content[:24]):
                class Client:
                    model = "fake-model"

                    class chat:
                        class completions:
                            @staticmethod
                            async def create(**_kwargs):
                                message = type("M", (), {"content": content})
                                choice = type("C", (), {"message": message()})
                                return type("R", (), {"choices": [choice()]})()

                self.assertIsNone(
                    asyncio.run(
                        propose_semantic_intent(
                            Client(), context("얼마나 남았어?"), settings=ENABLED
                        )
                    )
                )

    def test_a_transport_error_fails_closed(self) -> None:
        class BrokenClient:
            model = "fake-model"

            class chat:
                class completions:
                    @staticmethod
                    async def create(**_kwargs):
                        raise ConnectionError("no route to provider")

        self.assertIsNone(
            asyncio.run(
                propose_semantic_intent(
                    BrokenClient(), context("얼마나 남았어?"), settings=ENABLED
                )
            )
        )


class SemanticIntentSettingsTests(unittest.TestCase):
    def test_the_fallback_is_disabled_unless_explicitly_enabled(self) -> None:
        self.assertFalse(SemanticIntentSettings.from_environment({}).enabled)
        self.assertFalse(SemanticIntentSettings().enabled)

    def test_enabling_uses_bounded_defaults(self) -> None:
        settings = SemanticIntentSettings.from_environment({
            "VOICE_WORKFLOW_AGENT_SEMANTIC_INTENT_ENABLED": "true",
            "CHAT_MODEL": "grok-4.6",
        })
        self.assertTrue(settings.enabled)
        self.assertEqual(settings.model, "grok-4.20-0309-non-reasoning")
        self.assertEqual(settings.timeout_seconds, 2.5)
        self.assertLess(settings.minimum_confidence, settings.mutation_minimum_confidence)

    def test_out_of_range_configuration_is_refused(self) -> None:
        cases = (
            {"VOICE_WORKFLOW_AGENT_SEMANTIC_INTENT_TIMEOUT_SECONDS": "60"},
            {"VOICE_WORKFLOW_AGENT_SEMANTIC_INTENT_MIN_CONFIDENCE": "2"},
            {"VOICE_WORKFLOW_AGENT_SEMANTIC_INTENT_TIMEOUT_SECONDS": "fast"},
        )
        for environment in cases:
            with self.subTest(environment=environment):
                with self.assertRaises(ValueError):
                    SemanticIntentSettings.from_environment(environment)

    def test_a_mutation_floor_may_not_be_weaker_than_the_read_only_floor(self) -> None:
        with self.assertRaises(ValueError):
            SemanticIntentSettings.from_environment({
                "VOICE_WORKFLOW_AGENT_SEMANTIC_INTENT_MIN_CONFIDENCE": "0.9",
                "VOICE_WORKFLOW_AGENT_SEMANTIC_INTENT_MUTATION_MIN_CONFIDENCE": "0.5",
            })

    def test_the_public_capability_declares_no_mutation_authority(self) -> None:
        capability = SemanticIntentSettings(enabled=True).public_capability()
        self.assertEqual(capability["status"], "enabled")
        self.assertEqual(capability["mutation_authority"], "none")


class SemanticOutcomeTests(unittest.TestCase):
    def test_telemetry_carries_reason_codes_and_no_utterance_text(self) -> None:
        decision = evaluate_semantic_proposal(
            proposal(SemanticIntent.TIMER_STATUS, confidence=0.9),
            context("Time 얼마나 남았어?"),
            ENABLED,
        )
        payload = outcome_for_decision(decision, latency_ms=42).public_payload()
        self.assertEqual(payload["status"], "accepted")
        self.assertEqual(payload["proposed_intent"], "timer_status")
        self.assertEqual(payload["latency_ms"], 42)
        self.assertNotIn("Time", str(payload))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
