"""A rule the server enforces must be a rule the provider was told.

Three refusals in four steps were for rules nobody had written down, and each
was discovered by spending provider calls. This is the check that turns that
class of defect into a failing test instead of a failed run.
"""

from __future__ import annotations

import unittest

from voice_workflow_agent.claim_contract_audit import (
    CONTRACT_EVIDENCE,
    PROMPT,
    SCHEMA,
    SERVER_ONLY,
    collect_refusal_codes,
)
from voice_workflow_agent.experiment_protocol_pdf import extract_protocol_pdf
from voice_workflow_agent.protocol_chunk_analysis import (
    ChunkAnalysisLimits,
    extraction_for_chunk,
    plan_protocol_chunks,
)
from voice_workflow_agent.protocol_claim_analysis import (
    CLAIM_ANALYSIS_SYSTEM_PROMPT,
    claim_response_schema,
    prepare_chunk_claim_request_context,
)

import tempfile
from pathlib import Path

from tests.test_protocol_catalog import write_text_pdf


def _flat_prompt() -> str:
    return " ".join(CLAIM_ANALYSIS_SYSTEM_PROMPT.split())


def _example_schema():
    temp = tempfile.TemporaryDirectory()
    source = Path(temp.name) / "source.pdf"
    write_text_pdf(
        source,
        "Protocol Test\nSection preparation\n1. Add 10 mL of buffer.",
        title="Protocol Test",
    )
    extraction = extract_protocol_pdf(source)
    plan = plan_protocol_chunks(
        extraction,
        f"protocol-{extraction.sha256[:32]}",
        "pdf-1",
        limits=ChunkAnalysisLimits(max_concurrency=1, max_retries=0),
    )
    chunk = plan.chunks[0]
    request = prepare_chunk_claim_request_context(
        extraction_for_chunk(extraction, chunk),
        source_revision=chunk.candidate_revision_id,
        chunk_id=chunk.chunk_id,
        ordinal=chunk.ordinal,
        core_page_refs=chunk.core_page_refs,
        context_page_refs=chunk.overlap_page_refs,
    )
    return claim_response_schema(request), temp


def _resolve(schema, dotted: str):
    node = schema
    for part in dotted.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
            continue
        if isinstance(node, dict) and "$defs" in node and part in node["$defs"]:
            node = node["$defs"][part]
            continue
        return None
    return node


class ContractAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema, cls._temp = _example_schema()
        cls.codes = collect_refusal_codes()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temp.cleanup()

    def test_every_refusal_code_says_where_its_rule_was_stated(self) -> None:
        """A refusal with no entry here is a rule the provider never saw."""

        unexplained = sorted(set(self.codes) - set(CONTRACT_EVIDENCE))
        self.assertEqual(
            unexplained,
            [],
            "these refusals enforce a rule that is not recorded as stated "
            "anywhere; add the evidence to CONTRACT_EVIDENCE, and if there is "
            "none, state the rule in the prompt or the schema first",
        )

    def test_the_table_has_no_entries_for_refusals_that_do_not_exist(self):
        """A stale entry would let a real gap hide behind it."""

        stale = sorted(set(CONTRACT_EVIDENCE) - set(self.codes))
        self.assertEqual(stale, [])

    def test_every_prompt_claim_is_actually_in_the_prompt(self) -> None:
        """The recorded phrase must be there verbatim, not merely plausible."""

        prompt = _flat_prompt()
        missing = sorted(
            code
            for code, evidence in CONTRACT_EVIDENCE.items()
            if evidence.kind == PROMPT and evidence.detail not in prompt
        )
        self.assertEqual(missing, [])

    def test_every_schema_claim_resolves_to_a_real_schema_node(self) -> None:
        missing = sorted(
            code
            for code, evidence in CONTRACT_EVIDENCE.items()
            if evidence.kind == SCHEMA
            and _resolve(self.schema, evidence.detail) is None
        )
        self.assertEqual(missing, [])

    def test_server_only_refusals_say_why_a_provider_is_not_at_fault(self):
        for code, evidence in sorted(CONTRACT_EVIDENCE.items()):
            if evidence.kind != SERVER_ONLY:
                continue
            with self.subTest(code=code):
                self.assertGreater(len(evidence.detail.split()), 3)

    def test_the_three_defects_this_audit_exists_for_are_covered(self) -> None:
        """The regressions that motivated it, named."""

        for code, kind in (
            ("chunk_identity_mismatch", SCHEMA),
            ("protocol_title_missing_or_conflicting", PROMPT),
            ("declined_segment_states_a_value", PROMPT),
        ):
            with self.subTest(code=code):
                self.assertIn(code, CONTRACT_EVIDENCE)
                self.assertEqual(CONTRACT_EVIDENCE[code].kind, kind)

    def test_the_audit_reads_across_file_boundaries(self) -> None:
        """The title refusal lives in a different module from most of them."""

        self.assertGreaterEqual(len(self.codes), 50)
        modules = {module for paths in self.codes.values() for module in paths}
        self.assertGreaterEqual(len(modules), 3)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
