"""The lane R router's two tools and the server's validation of a proposal.

Behind the front rules (``CuratedProtocolSession.front_plan``) a model reads
the turn and answers it, or *proposes* one change by calling a tool. A
proposal is evidence, never authorization. This module holds the two tool
schemas, parses a tool call, and rules on it against server-owned facts the
session supplies; ``CuratedProtocolSession.apply_tool_proposal`` carries an
accepted proposal out through the same plan() branches the rules use, so
there is no second state machine. Nothing here calls a model, holds state,
or imports the workflow machine.

The rules follow the lane R design (``~/reports/lane_r_design.md`` §2) as the
people running the pilot decided them on 2026-10-02:

* ``next`` never moves on by itself (D2): it opens the completion question,
  or at a repeat-until step the endpoint question, and only the researcher's
  answer on the next turn moves on. Said with "not done yet" it is refused
  and the step's source completion criterion is said instead (D7).
* ``stop`` needs "종료" in the words and then asks once more (D5).
* ``start_timer`` takes its duration from the source; a different duration
  said aloud starts nothing and asks "원문은 15분입니다. 15분으로 시작할까요?"
  (D6).
* ``pause`` runs at once; ``resume`` only lifts a pause; ``start`` only
  starts a protocol that has never started.

Not wired: server.py calls none of this yet (lane R part 2-b).
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from voiney_lab.semantic_intent import (
    evidence_fence_rejection,
    has_completion_evidence,
    normalize_semantic_utterance,
    targets_current_step,
)

# The recorded-value check the tool loop already applies to a model's
# observation argument: numbers and identifiers must match on token
# boundaries (A-17 is not A-170).
from voiney_lab.tools import _observation_matches_transcript

CHANGE_STATE = "change_state"
RECORD_LOG = "record_log"
CHANGE_STATE_ACTIONS = ("start", "next", "stop", "pause", "resume", "start_timer")
RECORD_LOG_TYPES = ("observation", "anomaly")
EVIDENCE_MAX_CHARS = 200
VALUE_MAX_CHARS = 500

CHANGE_STATE_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": CHANGE_STATE,
        "description": (
            "Propose ONE workflow change that the researcher explicitly asked "
            "for in this turn. This is a proposal: the server validates it "
            "against its own state and may refuse it or ask the researcher "
            "first. 'next' never moves on by itself: the server asks the "
            "researcher to confirm the step is complete, or to report the "
            "observed endpoint. 'stop' needs the researcher to have said "
            "'종료', and the server asks once more before ending. "
            "'start_timer' runs the protocol's own duration; the server asks "
            "when the researcher names a different one. Never call it for a "
            "question about a step, for something only hoped for or planned, "
            "to skip steps, or to move on when the researcher says the step "
            "is not done. Do not describe the change as done; the server "
            "writes the reply for state-changing turns."
        ),
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "action": {"type": "string", "enum": list(CHANGE_STATE_ACTIONS)},
                "target_step": {
                    "type": "string",
                    "description": (
                        "Only the current step label from the server "
                        "snapshot, or omit. Any other step is refused."
                    ),
                },
                "evidence": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": EVIDENCE_MAX_CHARS,
                    "description": (
                        "A verbatim span copied from the researcher's "
                        "utterance in THIS turn that shows the command."
                    ),
                },
            },
            "required": ["action", "evidence"],
        },
    },
}

RECORD_LOG_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": RECORD_LOG,
        "description": (
            "Propose recording what the researcher reported in this turn: an "
            "observation or an anomaly. The value must be words the "
            "researcher actually said; never translate, shorten, correct or "
            "add units. Recording never completes a step. Do not say it is "
            "recorded; the server confirms after the record is stored."
        ),
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "type": {"type": "string", "enum": list(RECORD_LOG_TYPES)},
                "value": {"type": "string", "minLength": 1, "maxLength": VALUE_MAX_CHARS},
                "evidence": {
                    "type": "string", "minLength": 1, "maxLength": EVIDENCE_MAX_CHARS,
                },
            },
            "required": ["type", "value", "evidence"],
        },
    },
}

#: The tools offered to the model, in the order they are advertised.
ROUTER_TOOLS: tuple[dict[str, Any], ...] = (CHANGE_STATE_TOOL, RECORD_LOG_TOOL)

#: Every reason a proposal can be refused, with what it means. A verdict
#: carries one of these, or "accepted".
REFUSAL_REASONS: Mapping[str, str] = {
    "no_proposal": "no tool call was made",
    "tool_unknown": "a tool other than change_state or record_log",
    "arguments_not_json": "the arguments are not a JSON object",
    "arguments_invalid": "missing, extra or mistyped arguments, or a value outside its enum",
    "more_than_one_proposal": "more than one tool call in one turn: all are refused",
    "stale_proposal": "made for another turn, generation, revision or step",
    "transcript_unreliable": "the transcript could not be trusted; the front rules own the turn",
    "pending_gate_owns_turn": "a server question is open this turn; the front rules own the answer",
    "workflow_not_active": "the protocol is not running",
    "already_started": "start while the protocol is already running",
    "session_ended": "start after the experiment ended: no restart from step 1",
    "workflow_paused": "only resume or stop is taken during a pause",
    "evidence_not_verbatim": "the evidence is not in this turn's words",
    "interrogative_not_authorized": "a question is not a command",
    "hypothetical_not_authorized": "a hypothetical is not a command",
    "target_not_current_step": "a step other than the current one: no skipping",
    "no_completion_word": "next without a word of finishing or moving on",
    "end_word_missing": "stop without 종료 in the words (D5)",
    "no_start_word": "start without a start word, or about a timer",
    "no_pause_word": "pause without a pause word",
    "no_resume_word": "resume without a resume word",
    "no_timer_word": "start_timer without a timer word",
    "completion_not_reached": "next said with the step not done: the criterion is said instead (D7)",
    "already_paused": "pause while already paused",
    "not_paused": "resume while not paused",
    "no_step_timer": "the current step has no source timer",
    "value_not_in_utterance": "the recorded value is not the researcher's words",
}


@dataclass(frozen=True)
class ToolProposal:
    """One parsed tool call. Data, never an instruction."""

    tool: str
    evidence: str
    action: str | None = None
    target_step: str | None = None
    log_type: str | None = None
    value: str | None = None


@dataclass(frozen=True)
class ProposalBasis:
    """What the model was shown when it made the proposal (the fences)."""

    turn_id: int
    generation: int | None
    workflow_revision: int
    step_id: str | None


@dataclass(frozen=True)
class RouterTurnFacts:
    """Server-owned facts one proposal is ruled on. Built by the session."""

    utterance: str
    language: str
    turn_id: int
    generation: int | None
    workflow_revision: int
    step_id: str | None
    current_step_label: str | None
    workflow_active: bool
    workflow_status: str
    paused: bool
    experiment_started: bool
    experiment_running: bool
    #: The one-turn server question this turn could answer, or None.
    open_question: str | None
    #: The current step repeats until an endpoint the operator observes.
    observation_step: bool
    #: The current step's source timer, in seconds; 0 when it has none.
    step_timer_seconds: int
    timer_running: bool
    #: "멈춰도 돼?", "중지해야 돼", "종료할까": a question about a command.
    control_question: bool
    transcript_unreliable: bool


@dataclass(frozen=True)
class ProposalVerdict:
    """The server's ruling: carry it out, ask first, or refuse."""

    effect: str  # "execute" | "ask" | "refuse"
    reason_code: str
    proposal: ToolProposal | None = None
    #: For "ask": the question opened -- completion, observation, stop, timer.
    question: str | None = None
    #: For a duration said aloud that differs from the source (D6).
    stated_duration_seconds: int | None = None

    @property
    def accepted(self) -> bool:
        return self.effect != "refuse"


def _refuse(reason_code: str, proposal: ToolProposal | None = None) -> ProposalVerdict:
    return ProposalVerdict("refuse", reason_code, proposal)


# --- Parsing ---------------------------------------------------------------------

def parse_tool_call(name: object, arguments: object) -> ToolProposal | str:
    """One tool call as a ToolProposal, or the refusal code that rejects it.

    ``arguments`` is the JSON string a chat completion returns, or an
    already-decoded mapping. Keys must be exactly the schema's: a missing
    required key, an extra key, a wrong type or a value outside its enum
    fails closed.
    """

    if name not in (CHANGE_STATE, RECORD_LOG):
        return "tool_unknown"
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            return "arguments_not_json"
    if not isinstance(arguments, Mapping):
        return "arguments_not_json"
    keys = set(arguments)
    if name == CHANGE_STATE:
        if not {"action", "evidence"} <= keys <= {"action", "target_step", "evidence"}:
            return "arguments_invalid"
        action, target, evidence = (
            arguments.get("action"), arguments.get("target_step"), arguments.get("evidence"),
        )
        if (
            action not in CHANGE_STATE_ACTIONS
            or (target is not None and not isinstance(target, str))
            or not _bounded_text(evidence, EVIDENCE_MAX_CHARS)
        ):
            return "arguments_invalid"
        return ToolProposal(
            tool=CHANGE_STATE,
            action=action,
            target_step=(target.strip() or None) if isinstance(target, str) else None,
            evidence=evidence.strip(),
        )
    if keys != {"type", "value", "evidence"}:
        return "arguments_invalid"
    log_type, value, evidence = arguments["type"], arguments["value"], arguments["evidence"]
    if (
        log_type not in RECORD_LOG_TYPES
        or not _bounded_text(value, VALUE_MAX_CHARS)
        or not _bounded_text(evidence, EVIDENCE_MAX_CHARS)
    ):
        return "arguments_invalid"
    return ToolProposal(
        tool=RECORD_LOG, log_type=log_type, value=value.strip(), evidence=evidence.strip(),
    )


def _bounded_text(value: object, limit: int) -> bool:
    return isinstance(value, str) and 0 < len(value.strip()) <= limit


# --- The words that carry each change ---------------------------------------------
# Policy checks on observable evidence, like semantic_intent's fences: they
# decide whether a change a model proposed is visible in what the researcher
# said, never what the researcher meant.

_NEXT_WORD = re.compile(r"다음|\bnext\b", re.I)
_END_WORD = re.compile(r"종료|\bend\s+(?:the\s+)?(?:session|experiment|protocol)\b", re.I)
_START_WORD = re.compile(r"시작|개시|\bstart\b|\bbegin\b", re.I)
_PAUSE_WORD = re.compile(
    r"잠깐|잠시|멈|정지|중지|중단|스톱|기다려|\bstop\b|\bpause\b|\bhold\s+on\b|\bwait\b",
    re.I,
)
_RESUME_WORD = re.compile(
    r"재개|계속|이어서|다시\s*(?:시작|진행|하자|할게)|\bresume\b|\bcontinue\b", re.I,
)
_TIMER_WORD = re.compile(
    r"타이머|시간\s*(?:을\s*)?재|재\s*(?:줘|주세요|줄래|주이소|줄래요)|재줘|"
    r"\btimer\b|\btime\s+(?:it|this|the\s+step)\b",
    re.I,
)
#: The step is said not to be done: "덜 됐는데", "아직", "안 끝났어", "not yet".
_NOT_DONE = re.compile(
    r"덜|아직|안\s*(?:됐|되었|돼|끝났|끝냈|했)|못\s*(?:했|끝냈|끝났)|미완|"
    r"\bnot\s+(?:yet|done|finished|complete)\b|\bisn(?:'|’)?t\s+(?:done|finished|complete)\b|"
    r"\bincomplete\b",
    re.I,
)
#: A number and a unit. Korean numerals are read only with 십 or 백 in them
#: ("십오 분", "삼십분"): a lone "이" or "오" is as often "this" or a name, and
#: "두 분" is two people, so the native counts are read only before "시간".
_DURATION_PART = re.compile(
    r"(\d+(?:\.\d+)?|[일이삼사오육칠팔구]?[십백][일이삼사오육칠팔구]?|한|두|세|네)\s*"
    r"(시간|분|초|hours?|hrs?|minutes?|mins?|seconds?|secs?)(?![A-Za-z])",
    re.I,
)
_SINO_DIGITS = {"일": 1, "이": 2, "삼": 3, "사": 4, "오": 5, "육": 6, "칠": 7, "팔": 8, "구": 9}
_NATIVE_COUNTS = {"한": 1, "두": 2, "세": 3, "네": 4}
_UNIT_SECONDS = (
    (("시간", "hour", "hours", "hr", "hrs"), 3600),
    (("분", "minute", "minutes", "min", "mins"), 60),
    (("초", "second", "seconds", "sec", "secs"), 1),
)


def _korean_number(token: str, unit: str) -> int | None:
    if token in _NATIVE_COUNTS:
        return _NATIVE_COUNTS[token] if unit == "시간" else None
    scale = 10 if "십" in token else 100
    leading, _, ones = token.partition("십" if scale == 10 else "백")
    return _SINO_DIGITS.get(leading, 1) * scale + _SINO_DIGITS.get(ones, 0)


def stated_duration_seconds(utterance: str) -> int | None:
    """A duration said aloud ("10분", "1시간 30분", "십오 분"), in seconds, or None."""

    total = 0
    found = False
    for number, unit in _DURATION_PART.findall(utterance):
        value: float | None
        if number[0].isdigit():
            value = float(number)
        else:
            value = _korean_number(number, unit)
        if value is None:
            continue
        unit = unit.casefold()
        seconds = next(scale for names, scale in _UNIT_SECONDS if unit in names)
        total += int(round(value * seconds))
        found = True
    return total if found else None


# --- Validation ---------------------------------------------------------------------

_BOUNDED_CONTROL = frozenset({"pause", "resume", "start", "start_timer"})


def validate_tool_proposals(
    proposals: Sequence[ToolProposal | str],
    facts: RouterTurnFacts,
    basis: ProposalBasis,
) -> ProposalVerdict:
    """Rule on one turn's tool calls: at most one, and that one must pass.

    A string in ``proposals`` is a call ``parse_tool_call`` already rejected,
    with its refusal code. Two or more calls in one turn are all refused: a
    turn changes at most one thing.
    """

    if not proposals:
        return _refuse("no_proposal")
    if len(proposals) > 1:
        return _refuse("more_than_one_proposal")
    proposal = proposals[0]
    if isinstance(proposal, str):
        return _refuse(proposal if proposal in REFUSAL_REASONS else "arguments_invalid")
    return validate_tool_proposal(proposal, facts, basis)


def validate_tool_proposal(
    proposal: ToolProposal,
    facts: RouterTurnFacts,
    basis: ProposalBasis,
) -> ProposalVerdict:
    """The server's ruling on one parsed proposal (design §2-3, §2-4)."""

    action = proposal.action if proposal.tool == CHANGE_STATE else None
    refuse = lambda code: _refuse(code, proposal)  # noqa: E731

    # Fences: a proposal made for another moment is dropped, and a turn the
    # front rules own is not the model's.
    if (
        basis.turn_id != facts.turn_id
        or basis.workflow_revision != facts.workflow_revision
        or basis.step_id != facts.step_id
        or (
            basis.generation is not None
            and facts.generation is not None
            and basis.generation != facts.generation
        )
    ):
        return refuse("stale_proposal")
    if facts.transcript_unreliable:
        return refuse("transcript_unreliable")
    if facts.open_question is not None:
        return refuse("pending_gate_owns_turn")

    # Whether there is a running protocol for the change to act on.
    if action == "start":
        if facts.workflow_active:
            return refuse("already_started")
        if facts.experiment_started or facts.workflow_status in {"stopped", "completed"}:
            return refuse("session_ended")
        if facts.paused:
            return refuse("workflow_paused")
    elif action == "stop":
        if not facts.workflow_active and not facts.experiment_running:
            return refuse("workflow_not_active")
    elif not facts.workflow_active:
        return refuse("workflow_not_active")

    # The change must be visible in what was said this turn.
    utterance = normalize_semantic_utterance(facts.utterance)
    evidence = normalize_semantic_utterance(proposal.evidence)
    fence = evidence_fence_rejection(
        utterance=utterance,
        evidence=evidence,
        bounded_control=proposal.tool == RECORD_LOG or action in _BOUNDED_CONTROL,
    )
    if fence is not None:
        return refuse(fence)
    if proposal.tool == CHANGE_STATE and facts.control_question:
        return refuse("interrogative_not_authorized")
    word_missing = _missing_action_word(action, evidence)
    if word_missing is not None:
        return refuse(word_missing)
    if proposal.tool == CHANGE_STATE and not targets_current_step(
        proposal.target_step,
        current_step_label=facts.current_step_label,
        timer_available=facts.step_timer_seconds > 0 or facts.timer_running,
        timer_target=action == "start_timer",
    ):
        return refuse("target_not_current_step")

    if proposal.tool == RECORD_LOG:
        if facts.paused:
            return refuse("workflow_paused")
        if not _observation_matches_transcript(proposal.value, utterance):
            return refuse("value_not_in_utterance")
        return ProposalVerdict("execute", "accepted", proposal)
    if action == "next":
        if facts.paused:
            return refuse("workflow_paused")
        if _NOT_DONE.search(utterance):
            return refuse("completion_not_reached")
        return ProposalVerdict(
            "ask", "accepted", proposal,
            question="observation" if facts.observation_step else "completion",
        )
    if action == "stop":
        return ProposalVerdict("ask", "accepted", proposal, question="stop")
    if action == "pause":
        if facts.paused:
            return refuse("already_paused")
        return ProposalVerdict("execute", "accepted", proposal)
    if action == "resume":
        if not facts.paused:
            return refuse("not_paused")
        return ProposalVerdict("execute", "accepted", proposal)
    if action == "start":
        return ProposalVerdict("execute", "accepted", proposal)
    # start_timer
    if facts.paused:
        return refuse("workflow_paused")
    if facts.step_timer_seconds <= 0:
        return refuse("no_step_timer")
    if facts.timer_running:
        # The step's branch reports the time left and starts nothing.
        return ProposalVerdict("execute", "timer_already_running", proposal)
    stated = stated_duration_seconds(utterance)
    if stated is not None and stated != facts.step_timer_seconds:
        return ProposalVerdict(
            "ask", "duration_differs_from_source", proposal,
            question="timer", stated_duration_seconds=stated,
        )
    return ProposalVerdict("execute", "accepted", proposal)


def _missing_action_word(action: str | None, evidence: str) -> str | None:
    if action is None:
        return None
    if action == "next":
        if has_completion_evidence(evidence) or _NEXT_WORD.search(evidence):
            return None
        return "no_completion_word"
    if action == "stop":
        return None if _END_WORD.search(evidence) else "end_word_missing"
    if action == "start":
        if _START_WORD.search(evidence) and not _TIMER_WORD.search(evidence):
            return None
        return "no_start_word"
    if action == "pause":
        return None if _PAUSE_WORD.search(evidence) else "no_pause_word"
    if action == "resume":
        return None if _RESUME_WORD.search(evidence) else "no_resume_word"
    return None if _TIMER_WORD.search(evidence) else "no_timer_word"
