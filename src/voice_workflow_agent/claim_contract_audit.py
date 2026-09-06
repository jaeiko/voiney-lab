"""Every refusal the server can make, and where the provider was told about it.

Three times in four steps the server refused a response for failing a rule it
had never stated:

* STEP 26 -- ``request_handle_mismatch``. The response had to echo 24
  characters exactly and the schema described the field as ``"string"``.
* STEP 28 -- ``protocol_title_missing_or_conflicting``. Assembly needs exactly
  one title marker; the prompt mentioned ``protocol_title`` zero times.
* STEP 28 -- the chunk's ordinal, which assembly depends on, was not in the
  request at all.

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
    "declined_segment_states_a_value": _prompt(
        "none of them may be declined"
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
    "top_level_claim_scope_invalid": _prompt("target_claim_id"),
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
