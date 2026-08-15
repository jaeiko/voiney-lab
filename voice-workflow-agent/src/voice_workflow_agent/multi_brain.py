"""Typed, read-only LLM roles for Candidate A semantic questions.

CLASS-EXPLICIT: prompts request behavior; schemas and server gates enforce the
allowed data surface. PROJECT-ENGINEERING: Answer, Source, and Visual are this
application's bounded roles, not instructor-mandated names or an agent framework.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable


ALLOWED_SOURCE_SCOPES = (
    "ACTIVE_PROTOCOL",
    "ORIGINAL_SOURCE_PDF",
    "SOURCE_APPROVED_ALTERNATIVE",
    "APPROVED_REFERENCE",
    "AUTHORITATIVE_EXTERNAL_REFERENCE",
    "SUPPLEMENTAL_MODEL_KNOWLEDGE",
    "UNSUPPORTED_OPERATIONAL",
    "NOT_FOUND",
)
ALLOWED_VISUAL_CLASSES = (
    "original_source_visual",
    "approved_visual",
    "authoritative_external_reference",
    "generated_instructional_illustration",
    "no_visual",
)


def _field(value: Any, name: str, default: Any = None) -> Any:
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def _content(response: Any) -> str:
    choices = _field(response, "choices", []) or []
    if not choices:
        raise RuntimeError("brain response has no choice")
    content = _field(_field(choices[0], "message", {}), "content")
    if not isinstance(content, str):
        raise RuntimeError("brain response has no text")
    return content


_NUMERIC = re.compile(
    r"(?:\d{2}[:]\d{2}[:]\d{2}|\d+(?:\.\d+)?\s*"
    r"(?:mg/mL|ng/uL|mm3|mm³|µL|uL|mL|ml|mM|°C|rpm|min|v/v|C|h|%))",
    re.I,
)


def _numbers(value: str) -> frozenset[str]:
    return frozenset(item.casefold().replace(" ", "").replace("μ", "µ") for item in _NUMERIC.findall(value))


@dataclass(frozen=True)
class MultiBrainSettings:
    enabled: bool = False
    model: str = "grok-4.6"
    answer_timeout_seconds: float = 8.0
    planner_timeout_seconds: float = 6.0
    primary_answer_budget_seconds: float = 1.25

    @classmethod
    def from_environment(cls) -> "MultiBrainSettings":
        enabled = os.environ.get("VOICE_WORKFLOW_AGENT_MULTI_BRAIN_ENABLED", "false").strip().casefold() in {"1", "true", "yes", "on"}
        model = os.environ.get("VOICE_WORKFLOW_AGENT_MULTI_BRAIN_MODEL", "grok-4.6").strip()
        answer = float(os.environ.get("VOICE_WORKFLOW_AGENT_ANSWER_BRAIN_TIMEOUT_SECONDS", "8"))
        planner = float(os.environ.get("VOICE_WORKFLOW_AGENT_PLANNER_BRAIN_TIMEOUT_SECONDS", "6"))
        primary = float(os.environ.get("VOICE_WORKFLOW_AGENT_ANSWER_BRAIN_PRIMARY_BUDGET_SECONDS", "1.25"))
        if enabled and not model:
            raise ValueError("multi-brain model is required when enabled")
        if not 1 <= answer <= 15 or not 1 <= planner <= 12:
            raise ValueError("multi-brain timeouts are outside bounded limits")
        if not 0.1 <= primary < answer:
            raise ValueError("primary answer budget must be shorter than the answer timeout")
        return cls(enabled, model or "grok-4.6", answer, planner, primary)

    def public_capability(self) -> dict[str, object]:
        return {
            "status": "enabled" if self.enabled else "disabled",
            "model": self.model if self.enabled else None,
            "answer_timeout_seconds": self.answer_timeout_seconds if self.enabled else None,
            "planner_timeout_seconds": self.planner_timeout_seconds if self.enabled else None,
            "primary_answer_budget_seconds": self.primary_answer_budget_seconds if self.enabled else None,
        }


@dataclass(frozen=True)
class BrainFact:
    evidence_id: str
    kind: str
    text: str
    source_page: int


@dataclass(frozen=True)
class BrainClaim:
    claim_id: str
    target_type: str
    target_id: str
    dimension: str
    required_authority: str
    evidence_ids: tuple[str, ...]
    admission_status: str
    unresolved_reason: str | None = None


@dataclass(frozen=True)
class BrainSnapshot:
    configuration_id: int | None
    session_id: str
    turn_id: int
    generation_id: int
    workflow_revision: int
    protocol_id: str
    document_sha256: str
    step_id: str
    step_index: int
    language: str
    transcript: str
    intent_kind: str
    question_kind: str | None
    requested_entities: tuple[str, ...]
    question_dimensions: tuple[str, ...]
    facts: tuple[BrainFact, ...]
    claims: tuple[BrainClaim, ...] = ()

    def public_context(self, *, role: str | None = None) -> dict[str, object]:
        claims = self.claims
        dimensions = self.question_dimensions
        if role == "source" and claims:
            claims = tuple(
                claim for claim in claims
                if claim.admission_status == "research_required"
            )
            dimensions = tuple(dict.fromkeys(
                claim.dimension for claim in claims
            ))
        return {
            "configuration_id": self.configuration_id,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "generation_id": self.generation_id,
            "workflow_revision": self.workflow_revision,
            "protocol_id": self.protocol_id,
            "document_sha256": self.document_sha256,
            "step_id": self.step_id,
            "step_index": self.step_index,
            "language": self.language,
            "intent_kind": self.intent_kind,
            "question_kind": self.question_kind,
            "requested_entities": list(self.requested_entities),
            "question_dimensions": list(dimensions),
            "facts": [fact.__dict__ for fact in self.facts],
            "claims": [claim.__dict__ for claim in claims],
        }


@dataclass(frozen=True)
class BrainActivation:
    answer: bool
    source: bool
    visual: bool

    @property
    def roles(self) -> tuple[str, ...]:
        return tuple(name for name, enabled in (("answer", self.answer), ("source", self.source), ("visual", self.visual)) if enabled)


@dataclass(frozen=True)
class AnswerBrainOutput:
    spoken_answer: str
    display_answer: str
    evidence_ids: tuple[str, ...]
    limitations: tuple[str, ...]
    claim_sections: tuple[tuple[str, str, tuple[str, ...]], ...] = ()


@dataclass(frozen=True)
class SourceBrainOutput:
    entities: tuple[str, ...]
    dimensions: tuple[str, ...]
    scopes: tuple[str, ...]
    query: str
    needs_research: bool
    claim_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class VisualBrainOutput:
    helps: bool
    entity: str | None
    preferred_class: str
    reason_code: str


@dataclass(frozen=True)
class BrainTerminal:
    role: str
    status: str
    elapsed_ms: int
    output: AnswerBrainOutput | SourceBrainOutput | VisualBrainOutput | None = None


def activation_for(*, intent_kind: str, visual_requested: bool, unresolved_dimensions: tuple[str, ...]) -> BrainActivation:
    semantic = intent_kind in {
        "protocol_entity_question", "related_question", "related_followup",
        "related_safety_question", "step_elaboration", "expected_result_explanation",
        "operational_deviation", "visual_request",
    }
    if not semantic:
        return BrainActivation(False, False, False)
    return BrainActivation(
        answer=True,
        source=bool(unresolved_dimensions) or intent_kind in {"related_safety_question", "related_followup"},
        visual=visual_requested,
    )


ANSWER_SYSTEM = (
    "You are the read-only Answer Brain. Answer the actual question first in the requested language. "
    "Use only supplied facts. Every factual sentence must cite supplied evidence IDs in the JSON field. "
    "Never mutate workflow/report state, claim persistence, authorize a deviation, invent criteria, or add a number. "
    "Attach each independently supported section to one supplied claim ID and only that claim's evidence IDs. "
    "Omit unresolved claims from answer sections; the server will preserve their limitation. "
    "Return concise speech plus richer display text as strict JSON."
)
SOURCE_SYSTEM = (
    "You are the read-only Source Brain. Plan only supplied unresolved claim IDs. Select only supplied entities, dimensions, and allowed source scopes; "
    "construct one bounded research query. You cannot retrieve, admit authority, answer, cite, or mutate state. Return strict JSON."
)
VISUAL_SYSTEM = (
    "You are the read-only Visual Brain. Decide whether a visual helps and choose one allowed visual class. "
    "Select only a supplied entity. You cannot generate/fetch an image, claim provenance, speak, or mutate state. Return strict JSON."
)


class HybridMultiBrain:
    """Start independently enabled roles concurrently; the server owns admission."""

    def __init__(self, client: Any, settings: MultiBrainSettings, *, clock: Callable[[], float] = time.perf_counter) -> None:
        self.client = client
        self.settings = settings
        self.clock = clock

    def start(self, snapshot: BrainSnapshot, activation: BrainActivation) -> "BrainRun":
        tasks: dict[str, asyncio.Task[BrainTerminal]] = {}
        if activation.answer:
            tasks["answer"] = asyncio.create_task(self._timed("answer", self._answer(snapshot), self.settings.answer_timeout_seconds))
        if activation.source:
            tasks["source"] = asyncio.create_task(self._timed("source", self._source(snapshot), self.settings.planner_timeout_seconds))
        if activation.visual:
            tasks["visual"] = asyncio.create_task(self._timed("visual", self._visual(snapshot), self.settings.planner_timeout_seconds))
        return BrainRun(snapshot, activation, tasks)

    async def _timed(self, role: str, operation: Awaitable[Any], timeout: float) -> BrainTerminal:
        started = self.clock()
        provider_task = asyncio.create_task(operation)
        try:
            done, _ = await asyncio.wait({provider_task}, timeout=timeout)
            if not done:
                # asyncio.wait_for waits for cancellation cleanup and can exceed
                # its public deadline when a transport is slow to unwind.  The
                # server terminal must remain bounded even in that failure mode.
                provider_task.cancel()
                provider_task.add_done_callback(_consume_task_result)
                return BrainTerminal(
                    role, "timeout", round((self.clock() - started) * 1000)
                )
            output = provider_task.result()
        except asyncio.CancelledError:
            provider_task.cancel()
            provider_task.add_done_callback(_consume_task_result)
            raise
        except Exception:
            return BrainTerminal(role, "rejected", round((self.clock() - started) * 1000))
        return BrainTerminal(role, "success", round((self.clock() - started) * 1000), output)

    async def _create(self, role: str, system: str, snapshot: BrainSnapshot, schema: dict[str, object]) -> dict[str, Any]:
        request_timeout = (
            self.settings.answer_timeout_seconds
            if role == "answer" else self.settings.planner_timeout_seconds
        )
        response = await self.client.chat.completions.create(
            model=self.settings.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "system", "content": json.dumps(snapshot.public_context(role=role), ensure_ascii=False, sort_keys=True, separators=(",", ":"))},
                {"role": "user", "content": snapshot.transcript[:800]},
            ],
            response_format={"type": "json_schema", "json_schema": {"name": f"candidate_a_{role}_brain_v1", "strict": True, "schema": schema}},
            temperature=0,
            timeout=request_timeout,
        )
        try:
            value = json.loads(_content(response))
        except json.JSONDecodeError as exc:
            raise RuntimeError("brain output is not JSON") from exc
        if not isinstance(value, dict):
            raise RuntimeError("brain output is not an object")
        return value

    async def _answer(self, snapshot: BrainSnapshot) -> AnswerBrainOutput:
        evidence = tuple(fact.evidence_id for fact in snapshot.facts)
        claim_ids = tuple(claim.claim_id for claim in snapshot.claims)
        properties: dict[str, object] = {
            "spoken_answer": {"type": "string"}, "display_answer": {"type": "string"},
            "evidence_ids": {"type": "array", "items": {"type": "string", "enum": list(evidence)}, "uniqueItems": True},
            "limitations": {"type": "array", "items": {"type": "string"}},
        }
        if claim_ids:
            properties["claim_sections"] = {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "claim_id": {"type": "string", "enum": list(claim_ids)},
                    "text": {"type": "string"},
                    "evidence_ids": {"type": "array", "items": {
                        "type": "string", "enum": list(evidence)},
                        "uniqueItems": True},
                },
                "required": ["claim_id", "text", "evidence_ids"],
            }}
        schema = {
            "type": "object", "additionalProperties": False,
            "properties": properties,
            "required": [
                "spoken_answer", "display_answer", "evidence_ids", "limitations",
                *(("claim_sections",) if claim_ids else ()),
            ],
        }
        value = await self._create("answer", ANSWER_SYSTEM, snapshot, schema)
        spoken, display, ids, limitations, raw_sections = value.get("spoken_answer"), value.get("display_answer"), value.get("evidence_ids"), value.get("limitations"), value.get("claim_sections", [])
        fact_map = {fact.evidence_id: fact.text for fact in snapshot.facts}
        claim_map = {claim.claim_id: claim for claim in snapshot.claims}
        if not isinstance(spoken, str) or not spoken.strip() or not isinstance(display, str) or not display.strip() or not isinstance(ids, list) or not ids or any(item not in fact_map for item in ids) or not isinstance(limitations, list) or any(not isinstance(item, str) for item in limitations) or not isinstance(raw_sections, list):
            raise RuntimeError("answer brain output failed shape/evidence gate")
        sections: list[tuple[str, str, tuple[str, ...]]] = []
        for item in raw_sections:
            if not isinstance(item, dict):
                raise RuntimeError("answer brain claim section is invalid")
            claim_id, text, section_ids = (
                item.get("claim_id"), item.get("text"), item.get("evidence_ids")
            )
            claim = claim_map.get(claim_id)
            if (
                claim is None
                or claim.admission_status != "local_supported"
                or not isinstance(text, str) or not text.strip()
                or not isinstance(section_ids, list) or not section_ids
                or any(evidence_id not in claim.evidence_ids
                       for evidence_id in section_ids)
            ):
                raise RuntimeError("answer brain claim section failed admission")
            section_evidence = "\n".join(fact_map[item] for item in section_ids)
            if not _numbers(text).issubset(_numbers(section_evidence)):
                raise RuntimeError("answer brain claim section introduced a number")
            sections.append((claim_id, text.strip(), tuple(section_ids)))
        admitted = "\n".join(fact_map[item] for item in ids)
        if not _numbers(spoken + "\n" + display).issubset(_numbers(admitted)):
            raise RuntimeError("answer brain introduced a number or unit")
        forbidden = re.compile(
            r"(?:I|제가|내가).{0,40}(?:saved|recorded|persisted|저장|기록)"
            r"|(?:저장|기록)(?:했|됐|되었|했습니다)"
            r"|(?:step|단계).*(?:completed|완료 처리)",
            re.I,
        )
        if forbidden.search(spoken) or forbidden.search(display):
            raise RuntimeError("answer brain claimed workflow/report mutation")
        return AnswerBrainOutput(
            spoken.strip(), display.strip(), tuple(ids), tuple(limitations),
            tuple(sections),
        )

    async def _source(self, snapshot: BrainSnapshot) -> SourceBrainOutput:
        entities = snapshot.requested_entities or ("current_step",)
        unresolved_claim_ids = tuple(
            claim.claim_id for claim in snapshot.claims
            if claim.admission_status == "research_required"
        )
        dimensions = tuple(dict.fromkeys(
            claim.dimension for claim in snapshot.claims
            if claim.claim_id in unresolved_claim_ids
        )) or snapshot.question_dimensions or ("related_knowledge",)
        properties: dict[str, object] = {
            "entities": {"type": "array", "items": {"type": "string", "enum": list(entities)}, "uniqueItems": True},
            "dimensions": {"type": "array", "items": {"type": "string", "enum": list(dimensions)}, "uniqueItems": True},
            "scopes": {"type": "array", "items": {"type": "string", "enum": list(ALLOWED_SOURCE_SCOPES)}, "uniqueItems": True},
            "query": {"type": "string"}, "needs_research": {"type": "boolean"},
        }
        if unresolved_claim_ids:
            properties["claim_ids"] = {
                "type": "array", "items": {
                    "type": "string", "enum": list(unresolved_claim_ids)},
                "uniqueItems": True,
            }
        schema = {
            "type": "object", "additionalProperties": False,
            "properties": properties,
            "required": [
                "entities", "dimensions", "scopes", "query", "needs_research",
                *(("claim_ids",) if unresolved_claim_ids else ()),
            ],
        }
        value = await self._create("source", SOURCE_SYSTEM, snapshot, schema)
        selected_entities, selected_dimensions, scopes, query, needs, selected_claim_ids = value.get("entities"), value.get("dimensions"), value.get("scopes"), value.get("query"), value.get("needs_research"), value.get("claim_ids", list(unresolved_claim_ids))
        if not isinstance(selected_entities, list) or any(item not in entities for item in selected_entities) or not isinstance(selected_dimensions, list) or any(item not in dimensions for item in selected_dimensions) or not isinstance(scopes, list) or any(item not in ALLOWED_SOURCE_SCOPES for item in scopes) or not isinstance(query, str) or len(query.strip()) > 600 or not isinstance(needs, bool) or not isinstance(selected_claim_ids, list) or any(item not in unresolved_claim_ids for item in selected_claim_ids):
            raise RuntimeError("source brain output failed policy gate")
        if "UNSUPPORTED_OPERATIONAL" in scopes and any(scope not in {"ACTIVE_PROTOCOL", "UNSUPPORTED_OPERATIONAL", "SUPPLEMENTAL_MODEL_KNOWLEDGE", "NOT_FOUND"} for scope in scopes):
            raise RuntimeError("source brain broadened operational authority")
        return SourceBrainOutput(tuple(selected_entities), tuple(selected_dimensions), tuple(scopes), query.strip(), needs, tuple(selected_claim_ids))

    async def _visual(self, snapshot: BrainSnapshot) -> VisualBrainOutput:
        entities = snapshot.requested_entities or ("current_step",)
        schema = {"type": "object", "additionalProperties": False, "properties": {
            "helps": {"type": "boolean"}, "entity": {"type": ["string", "null"], "enum": [*entities, None]},
            "preferred_class": {"type": "string", "enum": list(ALLOWED_VISUAL_CLASSES)},
            "reason_code": {"type": "string", "enum": ["explicit_request", "original_available", "reference_preferred", "illustration_helpful", "not_helpful", "insufficient_evidence"]},
        }, "required": ["helps", "entity", "preferred_class", "reason_code"]}
        value = await self._create("visual", VISUAL_SYSTEM, snapshot, schema)
        helps, entity, preferred, reason = value.get("helps"), value.get("entity"), value.get("preferred_class"), value.get("reason_code")
        if not isinstance(helps, bool) or entity not in (*entities, None) or preferred not in ALLOWED_VISUAL_CLASSES or not isinstance(reason, str):
            raise RuntimeError("visual brain output failed policy gate")
        if not helps and preferred != "no_visual":
            raise RuntimeError("visual brain returned an inconsistent plan")
        return VisualBrainOutput(helps, entity, preferred, reason)


class BrainRun:
    def __init__(self, snapshot: BrainSnapshot, activation: BrainActivation, tasks: dict[str, asyncio.Task[BrainTerminal]]) -> None:
        self.snapshot = snapshot
        self.activation = activation
        self.tasks = tasks

    async def terminal(self, role: str, *, timeout: float | None = None) -> BrainTerminal | None:
        task = self.tasks.get(role)
        if task is None:
            return None
        if timeout is None:
            return await task
        try:
            # The provider task remains bounded by its own hard timeout.  This
            # shorter wait is only the user-visible primary-answer budget.
            return await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    def cancel(self) -> None:
        for task in self.tasks.values():
            if not task.done():
                task.cancel()


def _consume_task_result(task: asyncio.Task[Any]) -> None:
    """Collect detached cancellation outcomes without logging provider content."""

    try:
        task.result()
    except (asyncio.CancelledError, Exception):
        pass
