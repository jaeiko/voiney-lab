"""Every refusal the server can make, and where the provider was told about it.

Three times in four steps the server refused a response for failing a rule it
had never stated:

* STEP 26 -- ``request_handle_mismatch``. The response had to echo 24
  characters exactly and the schema described the field as ``"string"``.
* STEP 28 -- ``protocol_title_missing_or_conflicting``. Assembly needs exactly
  one title marker; the prompt mentioned ``protocol_title`` zero times.
* STEP 28 -- the chunk's ordinal, which assembly depends on, was not in the
  request at all.

``declined_segment_states_a_value`` was here until STEP 35, when declining a
stated value stopped ending the chunk and became a blocker a reviewer clears.
Its entry is gone because the refusal is gone; the prompt still tells a model
not to decline such a segment, which is now stricter than the server, and that
direction is safe -- a model that obeys gives up a move it did not need.

Each cost provider calls to discover, and together they are why no document
merged for four steps. None of them was a model failure: a model cannot obey
a rule nobody wrote down.

This module collects the refusal codes from the source tree -- across file
boundaries, chunk validation and whole-document assembly alike -- and each one
must be answered here with *where* the requirement is stated. A code with no
entry fails the audit, so adding a refusal without stating its requirement
breaks the build rather than a provider run.

The audit is deliberately not a keyword search over the prompt. A code named
``repeat_range_inverted`` would "find" the word ``repeat`` and prove nothing.
Each entry names the evidence explicitly and says which kind it is:

``PROMPT``      the requirement is written in the system prompt, and the
                recorded phrase must appear there verbatim.
``SCHEMA``      the response schema makes the violation unsayable, and the
                named schema path must exist.
``SERVER_ONLY`` the refusal is about the server's own state or the transport,
                not about anything the provider chooses, so there is nothing
                for a provider to have been told. Each of these carries the
                reason it cannot be a provider's fault.
"""

from __future__ import annotations

import ast
import pathlib
from dataclasses import dataclass

PACKAGE_ROOT = pathlib.Path(__file__).resolve().parent

PROMPT = "PROMPT"
SCHEMA = "SCHEMA"
SERVER_ONLY = "SERVER_ONLY"


@dataclass(frozen=True)
class ContractEvidence:
    """Where a provider was told about one refusal, or why it was not."""

    kind: str
    detail: str

    def __post_init__(self) -> None:
        if self.kind not in (PROMPT, SCHEMA, SERVER_ONLY):
            raise ValueError(f"Unknown contract evidence kind: {self.kind}")
        if not self.detail.strip():
            raise ValueError("Contract evidence needs a detail.")


def _prompt(phrase: str) -> ContractEvidence:
    return ContractEvidence(PROMPT, phrase)


def _schema(path: str) -> ContractEvidence:
    return ContractEvidence(SCHEMA, path)


def _server(reason: str) -> ContractEvidence:
    return ContractEvidence(SERVER_ONLY, reason)


#: refusal code -> where the requirement is stated.
CONTRACT_EVIDENCE: dict[str, ContractEvidence] = {
    # --- the response envelope -------------------------------------------
    "chunk_identity_mismatch": _schema("properties.request_handle.const"),
    "invalid_response": _server(
        "the completion was not the JSON object the schema describes"
    ),
    "invalid_source_hash": _server("the server's own source identity"),
    "invalid_source_revision": _server("the server's own revision identity"),
    "whole_source_identity_mismatch": _server(
        "chunk provenance the server assigned, not anything the model chose"
    ),
    "duplicate_chunk_conflict": _server("the server's own chunk plan"),
    "missing_chunk_result": _server("the server's own chunk plan"),
    "merge_metadata_limit_exceeded": _server("a server-side size bound"),
    # --- evidence citation ------------------------------------------------
    "unknown_evidence_handle": _prompt(
        "select one or more directly adjacent evidence_segment_ids in source"
        " order"
    ),
    "evidence_segment_unknown": _prompt(
        "select one or more directly adjacent evidence_segment_ids in source"
        " order"
    ),
    "evidence_segment_range_invalid": _prompt("directly adjacent"),
    "missing_evidence": _prompt(
        "For every claim and structural marker, cite a one-based core source"
        " page"
    ),
    "invalid_evidence": _prompt("cite a one-based core source page"),
    "schema_evidence_missing": _schema("$defs.page_local_core_evidence"),
    "quote_not_found": _server(
        "the server reconstructs the excerpt from its own bytes; the provider"
        " never sends text"
    ),
    "quote_normalization_mismatch": _server(
        "server-side reconstruction of text the provider did not send"
    ),
    "ambiguous_source_match": _server(
        "the server reconstructs the excerpt from its own bytes and found more than one match"
    ),
    "page_not_found": _server(
        "server-side page identity, computed from the source file"
    ),
    "claim_not_found": _server(
        "server-side resolution of an identifier the server itself assigned"
    ),
    "invalid_location_detail": _server(
        "server-side resolution of a location the server itself computed"
    ),
    "source_label_not_found": _server(
        "server-side label resolution against text the provider never sent"
    ),
    # --- page accounting ---------------------------------------------------
    "coverage_mismatch": _prompt(
        "Each segment is either cited by at least one claim or marker, or"
        " listed in that page's declined_evidence_segment_ids"
    ),
    "declination_malformed": _prompt("declined_evidence_segment_ids"),
    "duplicate_declined_segment": _prompt("declined_evidence_segment_ids"),
    "declined_segment_not_on_page": _prompt(
        "Each segment is either cited by at least one claim or marker, or"
        " listed in that page's declined_evidence_segment_ids"
    ),
    "unsupported_coverage_status": _schema(
        "properties.page_coverage.items.properties.analysis_incomplete"
    ),
    "incomplete_source_coverage": _server(
        "the merged record must hold every page of the source; a page missing"
        " from the record is a hole in the record, not in the reading"
    ),
    # The provider cannot say this at all under a strict schema: the coverage
    # record forbids extra properties, and the field STEP 21 removed was one.
    # The server keeps checking anyway, for a provider that is not strict.
    "label_disposition_not_accepted": _schema(
        "properties.page_coverage.items.additionalProperties"
    ),
    # --- structure ---------------------------------------------------------
    "protocol_title_missing_or_conflicting": _prompt(
        "exactly one protocol_title marker across the whole source"
    ),
    "section_conflict": _prompt(
        "a section marker for every section_id any claim refers to"
    ),
    "unsupported_structure_marker": _schema(
        "properties.structure.items.properties.kind.enum"
    ),
    "action_structure_invalid": _prompt(
        "preserves that action's source step label, step identity, section"
        " identity, and direct evidence"
    ),
    # --- claims ------------------------------------------------------------
    "unsupported_claim_category": _schema(
        "properties.claims.items.properties.category.enum"
    ),
    "numbered_action_missing": _prompt(
        "For each distinct explicit numbered source action on every core page,"
        " emit a distinct action claim"
    ),
    "claim_identity_conflict": _prompt(
        "Every identifier you return - marker_id, claim_id, section_id,"
        " step_id and target_claim_id"
    ),
    "duplicate_evidence_item_identifier": _prompt(
        "Every identifier you return - marker_id, claim_id, section_id,"
        " step_id and target_claim_id"
    ),
    "claim_target_invalid": _prompt("target_claim_id"),
    "step_identity_conflict": _prompt("step identity"),
    "source_label_conflict": _prompt("source step label"),
    "orphan_execution_claim": _prompt("target_claim_id"),
    "action_claim_scope_conflict": _prompt("section identity"),
    "resource_claim_scope_conflict": _prompt("section identity"),
    # Measured in STEP 37: the prompt says "top-level" zero times and never
    # states that a material, a piece of equipment or a prerequisite may carry
    # no step. The recorded phrase below is about target_claim_id in general
    # and does not state this rule, so this entry is SERVER_ONLY rather than
    # PROMPT -- the honest label for a rule the provider was never told.
    #
    # It is not stated in the prompt on purpose. Saying it would move
    # prompt_sha256 and cost every cached chunk, and the model's observation is
    # no longer discarded anyway: the claim is scoped as the domain requires
    # and what the model said is kept in
    # MergedProtocolClaims.unverified_step_attributions, marked unverified.
    "top_level_claim_scope_invalid": _server(
        "a domain scoping rule the prompt has never stated; the model's own"
        " attribution is preserved rather than refused"
    ),
    "document_level_claim_scope_invalid": _prompt("target_claim_id"),
    "missing_value_scope_invalid": _prompt("target_claim_id"),
    "warning_must_attach_to_enclosing_step": _prompt("step identity"),
    # --- repetition --------------------------------------------------------
    "repeat_range_missing": _prompt("repeated_step_labels"),
    "repeat_range_malformed": _prompt("repeated_step_labels"),
    "repeat_range_inverted": _prompt("repeated_step_labels"),
    "repeat_range_not_applicable": _prompt("repeated_step_labels"),
    "repeat_range_not_in_evidence": _prompt(
        "The cited evidence must contain those two labels written as a range"
    ),
    "repeat_range_step_unknown": _prompt("repeated_step_labels"),
    "repetition_count_missing": _prompt("repetition_count"),
    "repetition_count_malformed": _prompt("repetition_count"),
    "repetition_count_not_applicable": _prompt("repetition_count"),
    # --- not about a provider response at all ------------------------------
    # --- one passage, two incompatible readings -----------------------------
    "contradictory_repetition_claims": _prompt(
        "A repetition claim must set repeated_step_labels to the first and last"
        " step label the source says to repeat"
    ),
    "contradictory_execution_requirement": _prompt(
        "Set required_for_execution true when the claim states something an"
        " operator must have or do to run the step"
    ),
    # --- not about a provider response at all --------------------------------
    "semantic_running_timer_read_only": _server(
        "a runtime intent guard in the voice path, not a claim contract"
    ),
}


def collect_refusal_codes() -> dict[str, tuple[str, ...]]:
    """Every refusal code in the package, and the modules that raise it.

    Read from the syntax tree rather than by grepping, so a code that moves
    between files, or is raised positionally rather than by keyword, is still
    counted. Three call shapes cover every site in this package: a
    ``reason_code=`` keyword, a consistency or merge error raised with the code
    as its only argument, and the local ``fail("code", ...)`` helpers.
    """

    found: dict[str, set[str]] = {}
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            code: str | None = None
            if isinstance(node, ast.keyword) and node.arg == "reason_code":
                if isinstance(node.value, ast.Constant) and isinstance(
                    node.value.value, str
                ):
                    code = node.value.value
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in {
                    "ProtocolClaimConsistencyError",
                    "ProtocolChunkMergeError",
                } or node.func.id == "fail":
                    first = node.args[0] if node.args else None
                    if isinstance(first, ast.Constant) and isinstance(
                        first.value, str
                    ):
                        code = first.value
            if code:
                found.setdefault(code, set()).add(path.name)
    return {code: tuple(sorted(paths)) for code, paths in sorted(found.items())}


# ---------------------------------------------------------------------------
# Rules that forbid a move and name another one
# ---------------------------------------------------------------------------
#
# The table above answers "was this rule stated at all". It cannot see the
# defect STEP 30 spent five provider calls on, where every rule was stated and
# two of them disagreed:
#
#   the value-honesty escape hatch  "claim it as a document-level claim ..."
#   the positional attachment rule  "Only a claim whose evidence lies outside
#                                    every numbered step is document-level"
#
# For a value-bearing segment inside a numbered step both applied, the first
# named the one move the second forbade, and there was no third. The model was
# refused for doing what it was told and refused for not doing it. No amount of
# reading the prompt for missing rules finds that; it is found by asking, for
# a real segment, whether anything the prompt permits is something the server
# accepts.
#
# So each rule of the form "you may not do X, do Y instead" is recorded here
# with the verbatim phrase that states it and the moves it leaves open. The
# moves a segment has are the intersection across every rule that applies to
# it, and that intersection must contain something the server will take.

from collections.abc import Callable  # noqa: E402
from typing import TYPE_CHECKING  # noqa: E402

if TYPE_CHECKING:  # pragma: no cover - import cycle at runtime, types only
    from .experiment_protocol_pdf import ProtocolPdfExtraction

#: The three things that can be done with a segment. There is no fourth: the
#: coverage rule is "cited or declined, never both and never neither", and a
#: citation either targets the step it sits in or is document-level.
DECLINE = "decline"
CITE_AT_STEP = "cite_targeting_the_enclosing_step"
CITE_DOCUMENT_LEVEL = "cite_as_document_level"
EVERY_MOVE = frozenset({DECLINE, CITE_AT_STEP, CITE_DOCUMENT_LEVEL})


@dataclass(frozen=True)
class SegmentPosition:
    """What the server knows about one segment without reading a word of it.

    Every field is structural. The value test is the shape test the prompt
    states -- a digit followed by a unit -- not a judgement about whether the
    number instructs anyone.
    """

    page_number: int
    segment_index: int
    substantive: bool
    carries_value: bool
    inside_a_step: bool
    outside_every_step: bool
    enclosing_step_label_on_this_page: bool

    @property
    def straddles_a_step_boundary(self) -> bool:
        """Neither wholly inside a step nor clear of every step.

        Such a segment could be declined but never cited: it has no enclosing
        block to target and is not outside every block either. Segment
        boundaries are cut at every step label, so this should be impossible;
        it is computed rather than assumed.
        """

        return self.substantive and not (
            self.inside_a_step or self.outside_every_step
        )


@dataclass(frozen=True)
class StatedAlternative:
    """One "you may not do X, do Y instead" rule, and the moves it leaves."""

    name: str
    #: Verbatim from the system prompt. If the prompt stops saying this, the
    #: rule recorded here is no longer the rule in force and the audit fails.
    phrase: str
    applies: Callable[[SegmentPosition], bool]
    permits: frozenset[str]
    why: str


ALTERNATIVE_RULES: tuple[StatedAlternative, ...] = (
    StatedAlternative(
        name="every_segment_is_cited_or_declined",
        phrase=(
            "Each segment is either cited by at least one claim or marker, or"
            " listed in that page's declined_evidence_segment_ids"
        ),
        applies=lambda position: position.substantive,
        permits=EVERY_MOVE,
        why="the coverage rule opens all three moves and closes none",
    ),
    StatedAlternative(
        name="a_value_bearing_segment_may_not_be_declined",
        phrase="none of them may be declined",
        applies=lambda position: position.substantive and position.carries_value,
        permits=frozenset({CITE_AT_STEP, CITE_DOCUMENT_LEVEL}),
        why=(
            "declining is withdrawn for a segment whose shape says it states a"
            " value, so a citation is the only thing left"
        ),
    ),
    StatedAlternative(
        name="evidence_inside_a_step_belongs_to_that_step",
        phrase=(
            "evidence inside a numbered step's span targets that step's action"
            " claim, and only evidence outside every numbered step is"
            " document-level"
        ),
        applies=lambda position: position.substantive and position.inside_a_step,
        permits=frozenset({DECLINE, CITE_AT_STEP}),
        why=(
            "this is the sentence STEP 30 had to write: the escape hatch used"
            " to name document-level here, which the attachment rule forbids"
        ),
    ),
    StatedAlternative(
        name="only_evidence_outside_every_step_is_document_level",
        phrase=(
            "Only a claim whose evidence lies outside every numbered step is"
            " document-level"
        ),
        applies=lambda position: (
            position.substantive and not position.outside_every_step
        ),
        permits=frozenset({DECLINE, CITE_AT_STEP}),
        why="the same boundary stated once more where the claim rules are set out",
    ),
)


def server_accepts(position: SegmentPosition) -> frozenset[str]:
    """The moves the server's own validators would let through.

    Deliberately built from what those validators check rather than by calling
    them: this has to answer for a segment nobody has claimed yet, which is
    before there is a response to validate. The three conditions mirror
    ``declined_segment_states_a_value``, ``_outside_every_step_block`` and
    ``action_claim_scope_conflict`` respectively.
    """

    if not position.substantive:
        return EVERY_MOVE
    moves = set()
    # Since STEP 35 the server accepts a declination of a stated value inside a
    # step: it registers a blocker instead of ending the chunk. The prompt
    # still forbids it, which is stricter than the server and therefore safe --
    # see the note above. The condition is kept rather than deleted because it
    # is what the *prompt* narrows on, and prompt_permits is what it feeds.
    moves.add(DECLINE)
    if position.outside_every_step:
        moves.add(CITE_DOCUMENT_LEVEL)
    if position.inside_a_step and position.enclosing_step_label_on_this_page:
        # A parameter claim must sit on the same page as the action it targets,
        # so the enclosing step's label being on this page is what makes the
        # target exist.
        moves.add(CITE_AT_STEP)
    return frozenset(moves)


def prompt_permits(
    position: SegmentPosition,
    rules: tuple[StatedAlternative, ...] = ALTERNATIVE_RULES,
) -> frozenset[str]:
    """The moves left open once every rule that applies has had its say."""

    moves = EVERY_MOVE
    for rule in rules:
        if rule.applies(position):
            moves &= rule.permits
    return moves


def segment_positions(
    extraction: "ProtocolPdfExtraction", *, source_revision: str = "pdf-1"
) -> tuple[SegmentPosition, ...]:
    """Where every segment of every page sits, structurally."""

    from .protocol_claim_analysis import (
        generate_page_evidence_segments,
        segment_carries_unit_bearing_value,
        step_block_ranges,
    )

    positions: list[SegmentPosition] = []
    for page_number in range(1, extraction.page_count + 1):
        page = extraction.pages[page_number - 1]
        ranges = step_block_ranges(page.text, page.bottom_band_offset)
        offset = 0
        for segment in generate_page_evidence_segments(
            extraction, source_revision=source_revision, page_number=page_number
        ):
            start, end = offset, offset + len(segment.text)
            offset = end
            inside = any(low <= start and end <= high for low, high in ranges)
            positions.append(
                SegmentPosition(
                    page_number=page_number,
                    segment_index=segment.segment_index,
                    substantive=any(ch.isalnum() for ch in segment.text),
                    carries_value=segment_carries_unit_bearing_value(segment.text),
                    inside_a_step=inside,
                    outside_every_step=all(
                        end <= low or high <= start for low, high in ranges
                    ),
                    # A step block begins at a label match on this page's own
                    # text, so an enclosing block implies the label is here.
                    enclosing_step_label_on_this_page=inside,
                )
            )
    return tuple(positions)


def cornered_segments(
    positions: tuple[SegmentPosition, ...],
    rules: tuple[StatedAlternative, ...] = ALTERNATIVE_RULES,
) -> tuple[tuple[SegmentPosition, frozenset[str], frozenset[str]], ...]:
    """Segments with nothing to do: obeying the prompt means being refused.

    Returns the position together with what the prompt left it and what the
    server would have taken, because a bare count of contradictions is not
    something anyone can act on.
    """

    cornered = []
    for position in positions:
        if not position.substantive:
            continue
        permitted = prompt_permits(position, rules)
        accepted = server_accepts(position)
        if not (permitted & accepted):
            cornered.append((position, permitted, accepted))
    return tuple(cornered)
