"""Production turn-routing boundary shared by WebSocket and transcript replay."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from voice_workflow_agent.curated_protocol import (
    CuratedProtocolSession,
    CuratedProtocolTurnPlan,
)
from voice_workflow_agent.intent_arbitration import (
    RequestArbitration,
    arbitrate_request,
)
from voice_workflow_agent.semantic_intent import (
    SemanticIntentContext,
    SemanticIntentOutcome,
    SemanticIntentProposal,
    SemanticIntentSettings,
    outcome_for_decision,
    resolve_semantic_intent,
    semantic_fallback_reason,
)

SemanticIntentResolver = Callable[
    [SemanticIntentContext], Awaitable[SemanticIntentProposal | None]
]


@dataclass(frozen=True)
class CuratedRuntimeRoute:
    """Observable result of routing one transcript through production policy."""

    arbitration: RequestArbitration
    runtime_router: str
    plan: CuratedProtocolTurnPlan
    semantic: SemanticIntentOutcome | None = None

    @property
    def state_mutation(self) -> bool:
        return bool(self.plan.state_changed)

    @property
    def answer_origin(self) -> str:
        return self.plan.answer_origin


@dataclass(frozen=True)
class SemanticFallbackProbe:
    """Whether this turn is allowed to spend a semantic proposal, and why."""

    needed: bool
    reason_code: str
    context: SemanticIntentContext | None = None


def probe_curated_semantic_fallback(
    session: CuratedProtocolSession,
    transcript: str,
    *,
    language: str,
    arbitration: RequestArbitration | None = None,
    transcript_quality: str | None = None,
) -> SemanticFallbackProbe:
    """Decide, deterministically and without I/O, whether to ask for a proposal.

    This is what keeps the deterministic path the fast path: it runs the same
    classifier ``plan`` will run (~0.4 ms) and returns ``needed=False`` for every
    utterance the deterministic stack already classifies, so those turns never
    touch a model.
    """

    if transcript_quality is not None:
        return SemanticFallbackProbe(False, "degraded_transcript")
    if session.awaiting_server_confirmation:
        # A server-owned confirmation gate owns this turn's interpretation.
        return SemanticFallbackProbe(False, "pending_gate_owns_turn")
    decision = arbitration or arbitrate_request(transcript)
    if not decision.normalized_text:
        return SemanticFallbackProbe(False, "empty_transcript")
    intent = session.deterministic_control_intent(
        transcript, language=language, arbitration=decision
    )
    reason = semantic_fallback_reason(
        deterministic_action=intent.action.value,
        deterministic_intent_kind=intent.intent_kind,
        arbitration_intent=decision.intent.value,
    )
    if reason is None:
        return SemanticFallbackProbe(False, "deterministic_route_resolved")
    return SemanticFallbackProbe(
        True,
        reason,
        session.semantic_intent_context(
            transcript, language=language, deterministic_reason=reason
        ),
    )


def route_curated_runtime_turn(
    session: CuratedProtocolSession,
    transcript: str,
    *,
    turn_id: int,
    language: str,
    transcript_quality: str | None = None,
    configuration_id: int | None = None,
    generation: int | None = None,
    arbitration: RequestArbitration | None = None,
    semantic_proposal: SemanticIntentProposal | None = None,
    semantic_settings: SemanticIntentSettings | None = None,
    semantic_outcome: SemanticIntentOutcome | None = None,
) -> CuratedRuntimeRoute:
    """Use the exact arbitration/planning boundary called by Cascade runtime."""

    arbitration = arbitration or arbitrate_request(transcript)
    plan = session.plan(
        transcript,
        turn_id=turn_id,
        language=language,
        transcript_quality=transcript_quality,
        configuration_id=configuration_id,
        generation=generation,
        arbitration=arbitration,
        semantic_proposal=semantic_proposal,
        semantic_settings=semantic_settings,
    )
    ruling = session.last_semantic_decision
    if ruling is not None:
        semantic_outcome = outcome_for_decision(
            ruling,
            latency_ms=(
                semantic_outcome.latency_ms if semantic_outcome else None
            ),
        )
    return CuratedRuntimeRoute(
        arbitration=arbitration,
        runtime_router="curated_protocol",
        plan=plan,
        semantic=semantic_outcome,
    )


async def route_curated_runtime_turn_with_semantics(
    session: CuratedProtocolSession,
    transcript: str,
    *,
    turn_id: int,
    language: str,
    transcript_quality: str | None = None,
    configuration_id: int | None = None,
    generation: int | None = None,
    arbitration: RequestArbitration | None = None,
    resolver: SemanticIntentResolver | None = None,
    semantic_settings: SemanticIntentSettings | None = None,
) -> CuratedRuntimeRoute:
    """Route one turn, consulting the semantic fallback only when warranted.

    The model call happens here, outside the state machine, and produces data
    only.  ``session.plan`` stays synchronous and model-free, so a slow, absent,
    or malformed resolver can delay nothing but this one optional proposal and
    can never change what the workflow does with it.
    """

    arbitration = arbitration or arbitrate_request(transcript)
    settings = semantic_settings or SemanticIntentSettings()
    proposal: SemanticIntentProposal | None = None
    outcome: SemanticIntentOutcome | None = None
    if resolver is not None and settings.enabled:
        probe = probe_curated_semantic_fallback(
            session,
            transcript,
            language=language,
            arbitration=arbitration,
            transcript_quality=transcript_quality,
        )
        if not probe.needed or probe.context is None:
            outcome = SemanticIntentOutcome("skipped", probe.reason_code)
        else:
            proposal, outcome = await resolve_semantic_intent(
                resolver, probe.context, settings=settings
            )
    return route_curated_runtime_turn(
        session,
        transcript,
        turn_id=turn_id,
        language=language,
        transcript_quality=transcript_quality,
        configuration_id=configuration_id,
        generation=generation,
        arbitration=arbitration,
        semantic_proposal=proposal,
        semantic_settings=settings,
        semantic_outcome=outcome,
    )
