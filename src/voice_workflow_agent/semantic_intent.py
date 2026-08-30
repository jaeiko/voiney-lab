"""Bounded semantic intent fallback for language deterministic routing cannot resolve.

Real researchers code-switch and paraphrase ("Time 얼마나 남았어?"), and a regex
per utterance is not an architecture.  This module adds one narrow, read-only
semantic *proposal* stage behind the deterministic fast path:

    STT
    -> deterministic intent fast path (``classify_curated_control_intent``)
    -> semantic intent proposal, only when the deterministic result is a
       catch-all (this module)
    -> server-owned policy validation (this module)
    -> deterministic workflow state machine (``CuratedProtocolSession``)
    -> persistence
    -> acknowledgement

Three properties are structural rather than advisory:

* The model never mutates anything.  It returns one structured proposal; every
  transition is still decided by the curated state machine after the proposal
  has been re-validated against server-owned context.
* A proposal is evidence, never authorization.  Confidence alone never
  authorizes a mutation: bounded control needs verbatim, non-interrogative
  action evidence, and checkpoint intents are downgraded to an explicit
  researcher confirmation - the semantic path can never advance, complete, or
  end a protocol on its own.
* Every failure mode (disabled, unavailable, timeout, malformed output,
  unsupported intent, weak confidence) fails closed to the deterministic
  outcome the turn already had.

The vocabulary below is a projection of existing
``CuratedProtocolAction`` members; this module deliberately holds no import of
the workflow machine so it cannot grow into a second router.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import time
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

log = logging.getLogger(__name__)


class SemanticIntent(str, Enum):
    """The only meanings a semantic proposal may express.

    Each member projects onto an action the curated runtime already supports.
    The resolver may not invent a workflow action outside this set.
    """

    CURRENT_STEP = "current_step"
    NEXT_STEP_INFORMATION = "next_step_information"
    COMPLETE_CURRENT_STEP = "complete_current_step"
    NOT_DONE = "not_done"
    START_TIMER = "start_timer"
    TIMER_STATUS = "timer_status"
    TIMER_INFORMATION = "timer_information"
    PAUSE = "pause"
    RESUME = "resume"
    STOP = "stop"
    REPEAT = "repeat"
    RELATED_QUESTION = "related_question"
    UNKNOWN = "unknown"


class SemanticIntentTier(str, Enum):
    """How much evidence one proposed meaning needs before it may be used."""

    #: No workflow state changes at all.
    INFORMATIONAL = "informational"
    #: Bounded, reversible, protocol-defined control (timer, pause, resume).
    BOUNDED_CONTROL = "bounded_control"
    #: Advances or ends the workflow; never executed from a proposal.
    CHECKPOINT = "checkpoint"


SEMANTIC_INTENT_TIERS: Mapping[SemanticIntent, SemanticIntentTier] = {
    SemanticIntent.CURRENT_STEP: SemanticIntentTier.INFORMATIONAL,
    SemanticIntent.NEXT_STEP_INFORMATION: SemanticIntentTier.INFORMATIONAL,
    SemanticIntent.NOT_DONE: SemanticIntentTier.INFORMATIONAL,
    SemanticIntent.TIMER_STATUS: SemanticIntentTier.INFORMATIONAL,
    SemanticIntent.TIMER_INFORMATION: SemanticIntentTier.INFORMATIONAL,
    SemanticIntent.REPEAT: SemanticIntentTier.INFORMATIONAL,
    SemanticIntent.RELATED_QUESTION: SemanticIntentTier.INFORMATIONAL,
    SemanticIntent.START_TIMER: SemanticIntentTier.BOUNDED_CONTROL,
    SemanticIntent.PAUSE: SemanticIntentTier.BOUNDED_CONTROL,
    SemanticIntent.RESUME: SemanticIntentTier.BOUNDED_CONTROL,
    SemanticIntent.COMPLETE_CURRENT_STEP: SemanticIntentTier.CHECKPOINT,
    SemanticIntent.STOP: SemanticIntentTier.CHECKPOINT,
}

#: Proposable intents, in the order they are advertised to the resolver.
PROPOSABLE_INTENTS: tuple[str, ...] = tuple(
    member.value for member in SemanticIntent
)

#: Deterministic outcomes that count as "the fast path did not resolve this".
#: Anything else is already a specialized decision and is never second-guessed.
_UNRESOLVED_ACTIONS: Mapping[str, str] = {
    "off_topic": "deterministic_off_topic",
    "unsupported": "deterministic_unsupported",
}

#: ``related_question`` is the curated classifier's generic catch-all for
#: "mentions something lab-shaped"; its specialized siblings (safety, parameter,
#: follow-up) are real decisions and stay authoritative.
_UNRESOLVED_RELATED_QUESTION_KINDS = frozenset({"related_question"})

#: Deterministic non-mutating completion guards.  These are real decisions -
#: the classifier recognized a hypothetical, quoted, negated, or future
#: completion and deliberately refused to mutate.  Never re-open them.
_PROTECTED_INTENT_KINDS = frozenset({
    "completion_criteria_question",
    "future_completion",
    "negated_completion",
    "quoted_completion",
    "hypothetical_completion",
    "ambiguous_completion",
    "underspecified_result_request",
    "operational_parameter_ambiguous",
    "operational_value_clarification_required",
})

#: Shared-arbiter intents that already own their own specialized route.
_PROTECTED_ARBITRATION_INTENTS = frozenset({
    "learning",
    "protocol_audit",
    "history_resume",
    "uncertainty",
    "combined_learning_next",
    "visual",
})

# --- Bounded evidence guards -------------------------------------------------
# These are *policy* checks on observable evidence, not an intent lexicon: they
# never decide what an utterance means, they only decide whether a meaning the
# resolver already proposed is allowed to be acted on.

_REMAINING_TIME_EVIDENCE = re.compile(
    r"남았|남은|남아|얼마나\s*더|remaining|time\s+left|left\s+on|"
    r"how\s+(?:much\s+time|long)",
    re.IGNORECASE,
)
_CLOCK_TIME_QUESTION = re.compile(
    r"지금\s*몇\s*시|현재\s*시각|몇\s*시\s*(?:야|예요|입니까|인가)|"
    r"what\s+time\s+is\s+it|current\s+time",
    re.IGNORECASE,
)
_COMPLETION_EVIDENCE = re.compile(
    r"완료|끝|다\s*했|마쳤|마무리|넘어가|넘어갈|진행하자|done|finish|complete|"
    r"move\s+on|next\s+step",
    re.IGNORECASE,
)
_INTERROGATIVE_EVIDENCE = re.compile(
    r"[?？]|뭐|무엇|무슨|뭔지|어떻게|어떤|어디|언제|누가|왜|"
    r"\bwhat\b|\bwhy\b|\bhow\b|\bwhen\b|\bwhere\b|\bwhich\b|\bwho\b",
    re.IGNORECASE,
)
_HYPOTHETICAL_EVIDENCE = re.compile(
    r"하면|한다면|치면|가정|라고\s*하|라는\s*게|무슨\s*뜻|의미(?:가|는)|"
    r"\bif\b|assuming|suppose|what\s+happens",
    re.IGNORECASE,
)
_POLITE_ACTION_REQUEST = re.compile(
    r"(?:해\s*)?(?:줘|줄래|주세요|주시겠|부탁)|"
    r"\b(?:please|could\s+you|would\s+you)\b",
    re.IGNORECASE,
)

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


def _flag(environment: Mapping[str, str], name: str, default: bool) -> bool:
    raw = environment.get(name, "true" if default else "false").strip().casefold()
    return raw in _TRUE_VALUES


@dataclass(frozen=True)
class SemanticIntentSettings:
    """Bounded, non-secret configuration for the semantic fallback stage.

    Disabled by default: the live-validated deterministic voice path must never
    acquire a dependency on a model being reachable.
    """

    enabled: bool = False
    model: str = "grok-4.20-0309-non-reasoning"
    timeout_seconds: float = 2.5
    minimum_confidence: float = 0.6
    mutation_minimum_confidence: float = 0.85

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> "SemanticIntentSettings":
        env = os.environ if environment is None else environment
        enabled = _flag(
            env, "VOICE_WORKFLOW_AGENT_SEMANTIC_INTENT_ENABLED", False
        )
        model = env.get(
            "VOICE_WORKFLOW_AGENT_SEMANTIC_INTENT_MODEL",
            "grok-4.20-0309-non-reasoning",
        ).strip() or "grok-4.20-0309-non-reasoning"
        timeout = _bounded_float(
            env, "VOICE_WORKFLOW_AGENT_SEMANTIC_INTENT_TIMEOUT_SECONDS",
            2.5, 0.2, 8.0,
        )
        minimum = _bounded_float(
            env, "VOICE_WORKFLOW_AGENT_SEMANTIC_INTENT_MIN_CONFIDENCE",
            0.6, 0.0, 1.0,
        )
        mutation_minimum = _bounded_float(
            env, "VOICE_WORKFLOW_AGENT_SEMANTIC_INTENT_MUTATION_MIN_CONFIDENCE",
            0.85, 0.0, 1.0,
        )
        if mutation_minimum < minimum:
            raise ValueError(
                "VOICE_WORKFLOW_AGENT_SEMANTIC_INTENT_MUTATION_MIN_CONFIDENCE "
                "may not be weaker than the read-only confidence floor"
            )
        return cls(enabled, model, timeout, minimum, mutation_minimum)

    def public_capability(self) -> dict[str, object]:
        """Non-secret capability projection for the browser cockpit."""

        return {
            "status": "enabled" if self.enabled else "disabled",
            "model": self.model if self.enabled else None,
            "timeout_seconds": self.timeout_seconds if self.enabled else None,
            "minimum_confidence": (
                self.minimum_confidence if self.enabled else None
            ),
            "mutation_minimum_confidence": (
                self.mutation_minimum_confidence if self.enabled else None
            ),
            "mutation_authority": "none",
        }


def _bounded_float(
    environment: Mapping[str, str],
    name: str,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    raw = environment.get(name, str(default)).strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def normalize_semantic_utterance(value: str) -> str:
    """Normalize width/spacing only; punctuation and case-bearing text survive.

    The policy guards below need the interrogative punctuation the curated
    utterance key strips, so this is deliberately a *lighter* normalization.
    """

    normalized = unicodedata.normalize("NFKC", value)
    normalized = normalized.replace("’", "'").replace("`", "'")
    return re.sub(r"\s+", " ", normalized).strip()


@dataclass(frozen=True)
class SemanticIntentContext:
    """Server-owned facts the resolver may reason over.  No protocol prose."""

    utterance: str
    normalized_utterance: str
    language: str
    session_phase: str
    workflow_active: bool
    current_step_label: str | None
    step_timer_state: str
    step_timer_configured: bool
    step_timer_remaining_seconds: int | None
    pending_interaction: str | None
    deterministic_reason: str

    @property
    def timer_available(self) -> bool:
        """True when a protocol-defined step timer exists to talk about."""

        return self.step_timer_configured or self.step_timer_state in {
            "running", "expired"
        }

    def model_payload(self) -> dict[str, object]:
        """The exact, bounded context handed to the resolver."""

        return {
            "language": self.language,
            "session_phase": self.session_phase,
            "workflow_active": self.workflow_active,
            "current_step_label": self.current_step_label,
            "step_timer_state": self.step_timer_state,
            "step_timer_configured": self.step_timer_configured,
            "step_timer_remaining_seconds": self.step_timer_remaining_seconds,
            "pending_interaction": self.pending_interaction,
            "deterministic_result": self.deterministic_reason,
        }


@dataclass(frozen=True)
class SemanticIntentProposal:
    """One structured proposal.  Data, never an instruction."""

    intent: SemanticIntent
    target: str | None
    mutation_requested: bool
    confidence: float
    explicit_action_evidence: str
    reason: str


@dataclass(frozen=True)
class SemanticIntentDecision:
    """The server's ruling on one proposal."""

    accepted: bool
    reason_code: str
    intent: SemanticIntent | None = None
    confidence: float | None = None
    requires_confirmation: bool = False

    @property
    def state_mutation(self) -> bool:
        """A decision is evidence only; the state machine still owns transitions."""

        return False


@dataclass(frozen=True)
class SemanticIntentOutcome:
    """Privacy-safe telemetry for one semantic fallback attempt."""

    status: str
    reason_code: str
    proposed_intent: str | None = None
    accepted: bool = False
    confidence: float | None = None
    latency_ms: int | None = None
    requires_confirmation: bool = False

    def public_payload(self) -> dict[str, object]:
        """Reason codes and enum values only - never utterance or model prose."""

        return {
            "status": self.status,
            "reason_code": self.reason_code,
            "proposed_intent": self.proposed_intent,
            "accepted": self.accepted,
            "confidence": self.confidence,
            "latency_ms": self.latency_ms,
            "requires_confirmation": self.requires_confirmation,
        }


def semantic_fallback_reason(
    *,
    deterministic_action: str,
    deterministic_intent_kind: str,
    arbitration_intent: str | None = None,
) -> str | None:
    """Return why a semantic proposal is warranted, or ``None`` for the fast path.

    Pure and cheap: this is the gate that keeps the deterministic path the fast
    path.  Anything the deterministic stack actually classified is returned as
    ``None`` and never reaches a model.
    """

    if deterministic_intent_kind in _PROTECTED_INTENT_KINDS:
        return None
    if arbitration_intent in _PROTECTED_ARBITRATION_INTENTS:
        return None
    reason = _UNRESOLVED_ACTIONS.get(deterministic_action)
    if reason is not None:
        return reason
    if (
        deterministic_action == "related_question"
        and deterministic_intent_kind in _UNRESOLVED_RELATED_QUESTION_KINDS
    ):
        return "deterministic_generic_related_question"
    return None


def parse_semantic_proposal(payload: object) -> SemanticIntentProposal | None:
    """Validate structured resolver output; malformed output fails closed."""

    if not isinstance(payload, Mapping):
        return None
    raw_intent = payload.get("intent")
    if not isinstance(raw_intent, str):
        return None
    try:
        intent = SemanticIntent(raw_intent.strip().casefold())
    except ValueError:
        return None
    confidence = payload.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        return None
    confidence = float(confidence)
    if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
        return None
    mutation_requested = payload.get("mutation_requested")
    if not isinstance(mutation_requested, bool):
        return None
    target = payload.get("target")
    if target is not None and not isinstance(target, str):
        return None
    evidence = payload.get("explicit_action_evidence")
    reason = payload.get("reason")
    if not isinstance(evidence, str) or not isinstance(reason, str):
        return None
    return SemanticIntentProposal(
        intent=intent,
        target=(target.strip() or None) if isinstance(target, str) else None,
        mutation_requested=mutation_requested,
        confidence=confidence,
        explicit_action_evidence=evidence.strip()[:200],
        reason=reason.strip()[:200],
    )


def evaluate_semantic_proposal(
    proposal: SemanticIntentProposal | None,
    context: SemanticIntentContext,
    settings: SemanticIntentSettings,
) -> SemanticIntentDecision:
    """Apply server-owned policy.  This, not the model, authorizes anything."""

    if proposal is None:
        return SemanticIntentDecision(False, "no_proposal")
    if proposal.intent is SemanticIntent.UNKNOWN:
        return SemanticIntentDecision(False, "model_unknown")
    tier = SEMANTIC_INTENT_TIERS.get(proposal.intent)
    if tier is None:
        return SemanticIntentDecision(False, "unsupported_intent")

    floor = (
        settings.minimum_confidence
        if tier is SemanticIntentTier.INFORMATIONAL
        else settings.mutation_minimum_confidence
    )
    if proposal.confidence < floor:
        return _reject(
            "low_confidence"
            if tier is SemanticIntentTier.INFORMATIONAL
            else "low_confidence_for_mutation",
            proposal,
        )

    utterance = context.utterance
    if tier is not SemanticIntentTier.INFORMATIONAL:
        # Confidence is never authorization: a state-changing meaning has to be
        # visible in what the researcher actually said.
        if not context.workflow_active:
            return _reject("workflow_not_active", proposal)
        if context.pending_interaction is not None:
            return _reject("pending_gate_owns_turn", proposal)
        if not proposal.mutation_requested:
            return _reject("mutation_not_requested", proposal)
        evidence = proposal.explicit_action_evidence
        if not evidence or evidence.casefold() not in utterance.casefold():
            return _reject("evidence_not_verbatim", proposal)
        if (
            _INTERROGATIVE_EVIDENCE.search(utterance)
            and not (
                tier is SemanticIntentTier.BOUNDED_CONTROL
                and _POLITE_ACTION_REQUEST.search(utterance)
            )
        ):
            return _reject("interrogative_not_authorized", proposal)
        if _HYPOTHETICAL_EVIDENCE.search(utterance):
            return _reject("hypothetical_not_authorized", proposal)
        if not _targets_authoritative_current_step(proposal.target, context):
            # A proposal may never redirect a mutation onto another step; the
            # authoritative current step is the only thing it can speak about.
            return _reject("target_not_current_step", proposal)

    if proposal.intent is SemanticIntent.STOP:
        # Ending a run stays a deterministic, explicitly worded command.
        return _reject("checkpoint_requires_explicit_command", proposal)

    if proposal.intent is SemanticIntent.TIMER_STATUS:
        if _CLOCK_TIME_QUESTION.search(utterance):
            return _reject("clock_time_question", proposal)
        if not _REMAINING_TIME_EVIDENCE.search(utterance):
            return _reject("no_remaining_time_evidence", proposal)
        if not context.timer_available:
            # Never fabricate a timer that the approved step does not define.
            return _reject("no_step_timer_available", proposal)

    if proposal.intent is SemanticIntent.START_TIMER and not context.timer_available:
        return _reject("no_step_timer_available", proposal)

    if proposal.intent is SemanticIntent.COMPLETE_CURRENT_STEP:
        if not _COMPLETION_EVIDENCE.search(utterance):
            return _reject("no_completion_evidence", proposal)

    if (
        proposal.intent
        in {
            SemanticIntent.CURRENT_STEP,
            SemanticIntent.NEXT_STEP_INFORMATION,
            SemanticIntent.NOT_DONE,
            SemanticIntent.REPEAT,
        }
        and not context.workflow_active
    ):
        return _reject("workflow_not_active", proposal)

    return SemanticIntentDecision(
        accepted=True,
        reason_code=f"semantic_{proposal.intent.value}",
        intent=proposal.intent,
        confidence=proposal.confidence,
        # A checkpoint meaning is only ever staged as an explicit confirmation.
        requires_confirmation=tier is SemanticIntentTier.CHECKPOINT,
    )


#: Server-recognized ways of naming "the step the session is actually on".
_CURRENT_STEP_TARGETS = frozenset({
    "current", "current_step", "authoritative_current_step", "this", "this_step",
    "현재", "현재 단계", "이 단계", "지금",
})


def _targets_authoritative_current_step(
    target: str | None, context: SemanticIntentContext
) -> bool:
    """True when a proposal names the current step, or names nothing at all."""

    if not target:
        return True
    normalized = " ".join(target.strip().casefold().split())
    if normalized in _CURRENT_STEP_TARGETS:
        return True
    label = (context.current_step_label or "").strip().casefold()
    return bool(label) and normalized in {label, f"{label}단계", f"step {label}"}


def _reject(
    reason_code: str, proposal: SemanticIntentProposal
) -> SemanticIntentDecision:
    return SemanticIntentDecision(
        accepted=False,
        reason_code=reason_code,
        intent=proposal.intent,
        confidence=proposal.confidence,
    )


SEMANTIC_INTENT_PROMPT = (
    "You classify one laboratory voice utterance for a hands-free protocol "
    "assistant. You have no authority: you never advance, complete, pause, "
    "stop, or record anything. A deterministic server state machine decides "
    "every transition and will re-validate your answer.\n"
    "Return one intent from the supplied enumeration and nothing else. Use "
    "\"unknown\" whenever you are not confident, whenever the utterance is "
    "small talk, a definition question about a word, or an unsupported "
    "hypothetical, and "
    "whenever the requested meaning is not in the enumeration.\n"
    "Rules:\n"
    "- Korean, English, and mixed-script speech are equally valid input.\n"
    "- \"timer_status\" means asking how much of a running step timer is left. "
    "Asking for the wall-clock time is not \"timer_status\".\n"
    "- \"timer_information\" means asking what starting or running the step "
    "timer would do. It is informational even when phrased hypothetically.\n"
    "- Set \"mutation_requested\" true only when the researcher is commanding a "
    "change of workflow or timer state, never when they are asking about one.\n"
    "- \"explicit_action_evidence\" must be a verbatim span copied from the "
    "utterance that shows the action. Copy characters exactly. Use an empty "
    "string when there is no such span.\n"
    "- \"reason\" is one short clause of justification. Never write an "
    "instruction, a protocol step, a quantity, or a safety claim."
)

SEMANTIC_INTENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "intent": {"type": "string", "enum": list(PROPOSABLE_INTENTS)},
        "target": {"type": "string"},
        "mutation_requested": {"type": "boolean"},
        "confidence": {"type": "number"},
        "explicit_action_evidence": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": [
        "intent",
        "target",
        "mutation_requested",
        "confidence",
        "explicit_action_evidence",
        "reason",
    ],
}


def _response_content(response: Any) -> str:
    choices = getattr(response, "choices", None) or []
    if not choices:
        return ""
    message = getattr(choices[0], "message", None)
    content = getattr(message, "content", None)
    return content if isinstance(content, str) else ""


def _consume_task_result(task: asyncio.Task[Any]) -> None:
    """Retrieve a detached provider task's terminal exception without logging it."""

    if task.cancelled():
        return
    try:
        task.exception()
    except asyncio.CancelledError:
        return


async def propose_semantic_intent(
    client: Any,
    context: SemanticIntentContext,
    *,
    settings: SemanticIntentSettings,
) -> SemanticIntentProposal | None:
    """Ask the existing model/provider boundary for one bounded proposal.

    Every failure - timeout, transport error, malformed JSON, unsupported
    intent - returns ``None`` so the turn keeps its deterministic outcome.
    """

    messages = [
        {"role": "system", "content": SEMANTIC_INTENT_PROMPT},
        {
            "role": "system",
            "content": json.dumps(
                context.model_payload(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        },
        {"role": "user", "content": context.utterance},
    ]
    model = getattr(client, "model", None) or settings.model
    started = time.perf_counter()
    provider_task = asyncio.create_task(
        client.chat.completions.create(
                model=getattr(client, "model", None) or settings.model,
                messages=messages,
                temperature=0,
                max_tokens=160,
                timeout=settings.timeout_seconds,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "semantic_intent_proposal_v1",
                        "strict": True,
                        "schema": SEMANTIC_INTENT_SCHEMA,
                    },
                },
            )
    )
    try:
        done, _ = await asyncio.wait(
            {provider_task}, timeout=settings.timeout_seconds
        )
        if not done:
            provider_task.cancel()
            provider_task.add_done_callback(_consume_task_result)
            log.warning(
                "semantic_intent.provider status=timeout model=%s "
                "elapsed_ms=%s reason=provider_timeout exception=TimeoutError",
                model,
                int(round((time.perf_counter() - started) * 1000)),
            )
            return None
        response = provider_task.result()
    except asyncio.CancelledError:
        provider_task.cancel()
        provider_task.add_done_callback(_consume_task_result)
        raise
    except Exception as exc:
        log.warning(
            "semantic_intent.provider status=failed model=%s elapsed_ms=%s "
            "reason=provider_error exception=%s",
            model,
            int(round((time.perf_counter() - started) * 1000)),
            type(exc).__name__,
        )
        return None
    try:
        payload = json.loads(_response_content(response))
    except (TypeError, ValueError):
        log.warning(
            "semantic_intent.provider status=invalid model=%s elapsed_ms=%s "
            "reason=invalid_json exception=JSONDecodeError",
            model,
            int(round((time.perf_counter() - started) * 1000)),
        )
        return None
    proposal = parse_semantic_proposal(payload)
    if proposal is None:
        log.warning(
            "semantic_intent.provider status=invalid model=%s elapsed_ms=%s "
            "reason=invalid_proposal exception=ValidationError",
            model,
            int(round((time.perf_counter() - started) * 1000)),
        )
        return None
    log.info(
        "semantic_intent.provider status=success model=%s elapsed_ms=%s "
        "reason=proposal_received intent=%s",
        model,
        int(round((time.perf_counter() - started) * 1000)),
        proposal.intent.value,
    )
    return proposal


async def resolve_semantic_intent(
    resolver: Any,
    context: SemanticIntentContext,
    *,
    settings: SemanticIntentSettings,
) -> tuple[SemanticIntentProposal | None, SemanticIntentOutcome]:
    """Run one resolver attempt and report bounded, content-free telemetry."""

    if resolver is None or not settings.enabled:
        return None, SemanticIntentOutcome("skipped", "resolver_unavailable")
    started = time.perf_counter()
    try:
        proposal = await resolver(context)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.warning("semantic intent resolver raised; failing closed", exc_info=True)
        return None, SemanticIntentOutcome(
            "failed", "resolver_error",
            latency_ms=int(round((time.perf_counter() - started) * 1000)),
        )
    latency_ms = int(round((time.perf_counter() - started) * 1000))
    if proposal is None:
        return None, SemanticIntentOutcome(
            "unavailable", "no_proposal", latency_ms=latency_ms
        )
    return proposal, SemanticIntentOutcome(
        "proposed",
        "awaiting_policy",
        proposed_intent=proposal.intent.value,
        confidence=proposal.confidence,
        latency_ms=latency_ms,
    )


def outcome_for_decision(
    decision: SemanticIntentDecision,
    *,
    latency_ms: int | None = None,
) -> SemanticIntentOutcome:
    """Project one policy ruling into privacy-safe turn telemetry."""

    return SemanticIntentOutcome(
        status="accepted" if decision.accepted else "rejected",
        reason_code=decision.reason_code,
        proposed_intent=decision.intent.value if decision.intent else None,
        accepted=decision.accepted,
        confidence=decision.confidence,
        latency_ms=latency_ms,
        requires_confirmation=decision.requires_confirmation,
    )
