"""Evidence-first claim DTO, assembly, and production routing regressions."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from voice_workflow_agent import experiment_protocol as domain
from voice_workflow_agent.experiment_protocol_analysis import (
    ProtocolAnalysisEvidenceError,
    ProtocolAnalysisResponseError,
)
from voice_workflow_agent.experiment_protocol_config import (
    ProtocolPersistenceSettings,
)
from voice_workflow_agent.experiment_protocol_pdf import extract_protocol_pdf
from voice_workflow_agent.experiment_protocol_store import (
    initialize_protocol_store,
)
from voice_workflow_agent.protocol_catalog import (
    CLAIM_CHUNK_ANALYSIS_ENABLED_ENV,
    ProtocolApprovalError,
    ProtocolCatalog,
    ProtocolCatalogUnavailableError,
    SharedSecretApprovalPolicy,
    _analysis_state,
)
from voice_workflow_agent.protocol_chunk_analysis import (
    ChunkAnalysisLimits,
    ProtocolChunkMergeError,
    ValidatedChunkResult,
    analyze_protocol_chunk,
    assemble_validated_protocol_claims,
    merge_validated_chunk_results,
    plan_protocol_chunks,
)
from voice_workflow_agent.protocol_claim_analysis import (
    CLAIM_ANALYSIS_SYSTEM_PROMPT,
    CLAIM_RESPONSE_SCHEMA,
    CLAIM_SCHEMA_VERSION,
    MAX_CHUNK_CLAIM_RESPONSE_BYTES,
    ClaimCategory,
    generate_page_evidence_segments,
    prepare_chunk_claim_request,
    resolve_claim_source_evidence,
)


def write_pages(path: Path, page_texts: tuple[str, ...]) -> None:
    writer = PdfWriter()
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_ref = writer._add_object(font)
    for raw_text in page_texts:
        page = writer.add_blank_page(width=612, height=792)
        escaped = (
            raw_text.replace("\\", "\\\\")
            .replace("(", "\\(")
            .replace(")", "\\)")
            .replace("\n", " ")
        )
        stream = DecodedStreamObject()
        stream.set_data(
            f"BT /F1 9 Tf 36 740 Td ({escaped}) Tj ET".encode("ascii")
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {
                NameObject("/Font"): DictionaryObject(
                    {NameObject("/F1"): font_ref}
                )
            }
        )
        page[NameObject("/Contents")] = writer._add_object(stream)
    writer.add_metadata({"/Title": "Protocol Evidence"})
    with path.open("wb") as target:
        writer.write(target)


def page_text(page: dict[str, object]) -> str:
    return "".join(
        segment["text"] for segment in page["evidence_segments"]  # type: ignore[index]
    )


def evidence_for_excerpt(
    page: dict[str, object], excerpt: str
) -> dict[str, object]:
    segments = page["evidence_segments"]
    assert isinstance(segments, list)
    text = page_text(page)
    start = text.index(excerpt)
    end = start + len(excerpt)
    selected: list[str] = []
    offset = 0
    for segment in segments:
        assert isinstance(segment, dict)
        segment_text = segment["text"]
        segment_id = segment["segment_id"]
        assert isinstance(segment_text, str)
        assert isinstance(segment_id, str)
        segment_end = offset + len(segment_text)
        if segment_end > start and offset < end:
            selected.append(segment_id)
        offset = segment_end
    assert selected
    return {
        "source_page_number": page["source_page_number"],
        "page_text_sha256": page["page_text_sha256"],
        "evidence_segment_ids": selected,
    }


class RichClaimModel:
    """Strict provider fake that emits every supported claim category."""

    def __init__(self, mutation=None) -> None:
        self.mutation = mutation

    def analyze(self, *, system_prompt, input_json, response_schema) -> str:
        del system_prompt
        if response_schema != CLAIM_RESPONSE_SCHEMA:
            raise AssertionError("The chunk path reused the full Protocol schema.")
        request = json.loads(input_json)
        source = request["source"]
        chunk = request["chunk"]
        page = next(item for item in request["pages"] if item["role"] == "core")
        page_number = page["source_page_number"]

        def evidence(excerpt: str) -> dict[str, object]:
            return evidence_for_excerpt(page, excerpt)

        action = (
            "1. Add 10 mL buffer at 5% and incubate at 37 C for 15 min at "
            "800 rpm then hold for 20 min; WARNING hot; observe clear; repeat "
            "until clear; volume is not specified."
        )
        claims = [
            ("material-buffer", "material", "Material: buffer 10 mL 5%.", None),
            ("equipment-mixer", "equipment", "Equipment: mixer 800 rpm.", None),
            ("prerequisite-thaw", "prerequisite", "Before start: thaw sample.", None),
            ("action-1", "action", action, None),
            ("quantity-1", "quantity", "10 mL", "action-1"),
            ("concentration-1", "concentration", "5%", "action-1"),
            ("temperature-1", "temperature", "37 C", "action-1"),
            ("duration-1", "duration", "15 min", "action-1"),
            ("speed-1", "agitation_speed", "800 rpm", "action-1"),
            ("warning-1", "warning_hazard", "WARNING hot", "action-1"),
            ("observation-1", "observation_checkpoint", "observe clear", "action-1"),
            ("repeat-1", "repeat_condition", "repeat until clear", "action-1"),
            (
                "missing-1",
                "explicit_missing_ambiguous_value",
                "volume is not specified",
                "action-1",
            ),
        ]
        records = []
        for order, (claim_id, category, text, target) in enumerate(claims, start=2):
            top_level = category in {"material", "equipment", "prerequisite"}
            is_action = category == "action"
            excerpt = text if top_level else action
            records.append(
                {
                    "claim_id": claim_id,
                    "category": category,
                    "source_order": order,
                    "source_text": text,
                    "section_id": None if top_level else "section-preparation",
                    "step_id": None if top_level else "step-1",
                    "source_label": "1" if is_action else None,
                    "target_claim_id": target,
                    "required_for_execution": (
                        category in {"action", "explicit_missing_ambiguous_value"}
                    ),
                    "evidence": evidence(excerpt),
                }
            )
        structure = [
            {
                "marker_id": "protocol-title",
                "kind": "protocol_title",
                "source_order": 0,
                "source_text": "Protocol Evidence",
                "section_id": None,
                "evidence": evidence("Protocol Evidence"),
            },
            {
                "marker_id": "marker-preparation",
                "kind": "section",
                "source_order": 1,
                "source_text": "Preparation",
                "section_id": "section-preparation",
                "evidence": evidence("Preparation"),
            },
        ]
        item_ids = [item["marker_id"] for item in structure] + [
            item["claim_id"] for item in records
        ]
        response = {
            "claim_schema_version": CLAIM_SCHEMA_VERSION,
            "capability_policy_id": "p1-conservative",
            "source_revision": source["source_revision"],
            "source_sha256": source["source_sha256"],
            "chunk_id": chunk["chunk_id"],
            "page_coverage": [
                {
                    "source_revision": source["source_revision"],
                    "source_sha256": source["source_sha256"],
                    "source_page_number": page_number,
                    "page_text_sha256": page["page_text_sha256"],
                    "status": "complete",
                    "evidence_item_ids": item_ids,
                }
            ],
            "structure": structure,
            "claims": records,
        }
        if self.mutation is not None:
            self.mutation(response)
        return json.dumps(response, separators=(",", ":"))


class ProtocolClaimAnalysisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "rich.pdf"
        self.action = (
            "1. Add 10 mL buffer at 5% and incubate at 37 C for 15 min at "
            "800 rpm then hold for 20 min; WARNING hot; observe clear; repeat "
            "until clear; volume is not specified."
        )
        write_pages(
            self.source,
            (
                "Protocol Evidence Preparation Before start: thaw sample. "
                "Material: buffer 10 mL 5%. Equipment: mixer 800 rpm. "
                + self.action,
            ),
        )
        self.extraction = extract_protocol_pdf(self.source)
        self.protocol_id = f"protocol-{self.extraction.sha256[:32]}"
        self.plan = plan_protocol_chunks(
            self.extraction,
            self.protocol_id,
            "pdf-1",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def analyze(self, model: RichClaimModel | None = None):
        chunk = self.plan.chunks[0]
        return ValidatedChunkResult(
            chunk,
            analyze_protocol_chunk(
                self.extraction,
                chunk,
                model or RichClaimModel(),
            ),
        )

    def test_chunk_schema_is_small_and_every_claim_has_independent_provenance(self):
        rendered_schema = json.dumps(CLAIM_RESPONSE_SCHEMA, sort_keys=True)
        self.assertNotIn("ExperimentProtocol", rendered_schema)
        self.assertNotIn('"protocol"', rendered_schema)
        self.assertNotIn("source_excerpt", rendered_schema)
        result = self.analyze()
        self.assertEqual(
            {claim.category for claim in result.analysis.claims},
            set(ClaimCategory),
        )
        for claim in result.analysis.claims:
            self.assertEqual(claim.evidence.source_revision, "pdf-1")
            self.assertEqual(claim.evidence.source_sha256, self.extraction.sha256)
            self.assertEqual(claim.evidence.source_page_number, 1)
            self.assertTrue(claim.evidence.evidence_segment_ids)
            self.assertIn(claim.evidence.source_excerpt, self.extraction.pages[0].text)
            self.assertIn(claim.source_text, claim.evidence.source_excerpt)

    def test_evidence_segments_are_deterministic_and_source_identity_bound(self):
        multiline = replace(
            self.extraction,
            pages=(
                replace(
                    self.extraction.pages[0],
                    text="first extracted line\nsecond extracted line\nthird extracted line",
                ),
            ),
        )
        first = generate_page_evidence_segments(
            multiline,
            source_revision="pdf-segments",
            page_number=1,
        )
        duplicate = generate_page_evidence_segments(
            multiline,
            source_revision="pdf-segments",
            page_number=1,
        )
        other_revision = generate_page_evidence_segments(
            multiline,
            source_revision="pdf-other",
            page_number=1,
        )

        self.assertEqual(first, duplicate)
        self.assertEqual("".join(segment.text for segment in first), multiline.pages[0].text)
        self.assertEqual(len(first), 3)
        self.assertNotEqual(
            [segment.segment_id for segment in first],
            [segment.segment_id for segment in other_revision],
        )
        self.assertTrue(all(segment.segment_id.startswith("seg-") for segment in first))

    def test_single_and_adjacent_multi_segment_evidence_resolve_exactly(self):
        multiline = replace(
            self.extraction,
            pages=(
                replace(
                    self.extraction.pages[0],
                    text="first extracted line\nsecond extracted line\nthird extracted line",
                ),
            ),
        )
        segments = generate_page_evidence_segments(
            multiline,
            source_revision="pdf-segments",
            page_number=1,
        )
        identity = {
            "source_page_number": 1,
            "page_text_sha256": segments[0].page_text_sha256,
        }
        single = resolve_claim_source_evidence(
            {**identity, "evidence_segment_ids": [segments[1].segment_id]},
            multiline,
            source_revision="pdf-segments",
            core_pages=frozenset({1}),
            chunk_id="chunk-segments",
        )
        adjacent = resolve_claim_source_evidence(
            {
                **identity,
                "evidence_segment_ids": [
                    segments[0].segment_id,
                    segments[1].segment_id,
                ],
            },
            multiline,
            source_revision="pdf-segments",
            core_pages=frozenset({1}),
            chunk_id="chunk-segments",
        )

        self.assertEqual(single.source_excerpt, segments[1].text)
        self.assertEqual(
            adjacent.source_excerpt,
            segments[0].text + segments[1].text,
        )

    def test_fabricated_wrong_page_revision_hash_and_invalid_ranges_fail(self):
        second_page = replace(
            self.extraction.pages[0],
            source_page_number=2,
            text="different page line",
        )
        multiline = replace(
            self.extraction,
            page_count=2,
            pages=(
                replace(
                    self.extraction.pages[0],
                    text="first extracted line\nsecond extracted line\nthird extracted line",
                ),
                second_page,
            ),
        )
        segments = generate_page_evidence_segments(
            multiline,
            source_revision="pdf-segments",
            page_number=1,
        )
        page_two = generate_page_evidence_segments(
            multiline,
            source_revision="pdf-segments",
            page_number=2,
        )
        identity = {
            "source_page_number": 1,
            "page_text_sha256": segments[0].page_text_sha256,
        }
        cases = (
            {**identity, "evidence_segment_ids": ["seg-" + "0" * 64]},
            {
                **identity,
                "evidence_segment_ids": [page_two[0].segment_id],
            },
            {
                **identity,
                "evidence_segment_ids": [
                    segments[0].segment_id,
                    segments[2].segment_id,
                ],
            },
            {
                **identity,
                "evidence_segment_ids": [
                    segments[1].segment_id,
                    segments[0].segment_id,
                ],
            },
            {
                **identity,
                "page_text_sha256": "0" * 64,
                "evidence_segment_ids": [segments[0].segment_id],
            },
        )
        for raw in cases:
            with self.subTest(raw=tuple(raw["evidence_segment_ids"])):
                with self.assertRaises(ProtocolAnalysisEvidenceError):
                    resolve_claim_source_evidence(
                        raw,
                        multiline,
                        source_revision="pdf-segments",
                        core_pages=frozenset({1}),
                        chunk_id="chunk-segments",
                    )
        with self.assertRaises(ProtocolAnalysisEvidenceError):
            resolve_claim_source_evidence(
                {
                    **identity,
                    "evidence_segment_ids": [segments[0].segment_id],
                },
                multiline,
                source_revision="pdf-other",
                core_pages=frozenset({1}),
                chunk_id="chunk-segments",
            )
        with self.assertRaises(ProtocolAnalysisEvidenceError):
            resolve_claim_source_evidence(
                {
                    "source_page_number": 2,
                    "page_text_sha256": page_two[0].page_text_sha256,
                    "evidence_segment_ids": [page_two[0].segment_id],
                },
                multiline,
                source_revision="pdf-segments",
                core_pages=frozenset({1}),
                chunk_id="chunk-segments",
            )

    def test_request_supplies_exact_server_hash_for_every_provider_page(self):
        second_source = self.root / "provider-pages.pdf"
        write_pages(
            second_source,
            (
                "Protocol Evidence Context page.",
                "Protocol Evidence Core page.",
            ),
        )
        extraction = extract_protocol_pdf(second_source)
        request = json.loads(
            prepare_chunk_claim_request(
                extraction,
                source_revision="pdf-provider-pages",
                chunk_id="chunk-provider-pages",
                ordinal=1,
                core_page_refs=(2,),
                context_page_refs=(1,),
            )
        )

        self.assertEqual(len(request["pages"]), 2)
        for page in request["pages"]:
            page_number = page["source_page_number"]
            self.assertEqual(
                page["page_text_sha256"],
                hashlib.sha256(
                    extraction.pages[page_number - 1].text.encode("utf-8")
                ).hexdigest(),
            )
            self.assertEqual(
                page_text(page),
                extraction.pages[page_number - 1].text,
            )
            self.assertTrue(page["evidence_segments"])
        self.assertIn("opaque, server-owned page identity", CLAIM_ANALYSIS_SYSTEM_PROMPT)
        self.assertIn("Never\ncalculate, derive", CLAIM_ANALYSIS_SYSTEM_PROMPT)
        self.assertIn("Never return source_excerpt text", CLAIM_ANALYSIS_SYSTEM_PROMPT)

    def test_correct_echoed_page_hash_passes_validation(self):
        result = self.analyze()
        self.assertEqual(
            result.analysis.page_coverage[0].page_text_sha256,
            hashlib.sha256(self.extraction.pages[0].text.encode("utf-8")).hexdigest(),
        )

    def test_altered_or_missing_page_hash_fails_closed(self):
        def altered(response):
            response["page_coverage"][0]["page_text_sha256"] = "0" * 64

        with self.assertRaises(ProtocolAnalysisEvidenceError) as altered_failure:
            self.analyze(RichClaimModel(altered))
        self.assertEqual(
            altered_failure.exception.diagnostic.reason_code,
            "invalid_source_hash",
        )

        def missing(response):
            del response["page_coverage"][0]["page_text_sha256"]

        with self.assertRaises(ProtocolAnalysisResponseError):
            self.analyze(RichClaimModel(missing))

    def test_hash_from_another_supplied_page_fails_closed(self):
        two_page_source = self.root / "wrong-page-hash.pdf"
        page_text = (
            "Protocol Evidence Preparation Before start: thaw sample. "
            "Material: buffer 10 mL 5%. Equipment: mixer 800 rpm. "
            + self.action
        )
        write_pages(
            two_page_source,
            ("First page identity. " + page_text, "Second page identity. " + page_text),
        )
        extraction = extract_protocol_pdf(two_page_source)
        plan = plan_protocol_chunks(
            extraction,
            f"protocol-{extraction.sha256[:32]}",
            "pdf-1",
            limits=ChunkAnalysisLimits(max_core_pages_per_chunk=1),
        )
        self.assertEqual(plan.chunks[1].overlap_page_refs, (1,))
        first_page_hash = hashlib.sha256(
            extraction.pages[0].text.encode("utf-8")
        ).hexdigest()

        def other_page(response):
            response["page_coverage"][0]["page_text_sha256"] = first_page_hash

        with self.assertRaises(ProtocolAnalysisEvidenceError) as failure:
            analyze_protocol_chunk(
                extraction,
                plan.chunks[1],
                RichClaimModel(other_page),
            )
        self.assertEqual(failure.exception.diagnostic.reason_code, "invalid_source_hash")

    def test_claims_merge_then_assemble_with_exact_final_provenance_and_blockers(self):
        merged_claims = merge_validated_chunk_results(
            self.extraction,
            self.plan,
            (self.analyze(),),
        )
        draft = assemble_validated_protocol_claims(
            self.extraction,
            merged_claims,
        )
        self.assertEqual(draft.protocol.protocol_id, self.protocol_id)
        self.assertEqual(draft.readiness.status, domain.ReadinessStatus.ANALYSIS_REQUIRED)
        self.assertIn(
            domain.ReadinessReasonCode.MISSING_EXECUTION_CRITICAL_VALUE.value,
            draft.readiness.reason_codes,
        )
        self.assertIn(
            domain.ReadinessReasonCode.UNSUPPORTED_REPEAT_UNTIL.value,
            draft.readiness.reason_codes,
        )
        action = draft.protocol.sections[0].steps[0].sub_actions[0]
        self.assertEqual(action.process_timer.duration.source_text, "15 min")
        self.assertEqual(action.required_observations[0].source_text, "observe clear")
        expected_locator = f"source_revision=pdf-1;source_sha256={self.extraction.sha256}"
        self.assertEqual(action.evidence.location_detail, expected_locator)
        self.assertEqual(action.warnings[0].evidence.location_detail, expected_locator)

    def test_provider_cannot_introduce_source_excerpt(self):
        def fabricated(response):
            response["claims"][4]["evidence"]["source_excerpt"] = "fabricated"

        with self.assertRaises(ProtocolAnalysisResponseError):
            self.analyze(RichClaimModel(fabricated))

    def test_wrong_source_page_fails_even_when_excerpt_exists_elsewhere(self):
        two_page_source = self.root / "wrong-page.pdf"
        write_pages(
            two_page_source,
            (
                "Protocol Evidence Preparation Before start: thaw sample. "
                "Material: buffer 10 mL 5%. Equipment: mixer 800 rpm. "
                + self.action,
                "Control page without the claimed quantity.",
            ),
        )
        extraction = extract_protocol_pdf(two_page_source)
        plan = plan_protocol_chunks(
            extraction,
            f"protocol-{extraction.sha256[:32]}",
            "pdf-1",
        )

        def wrong_page(response):
            response["claims"][4]["evidence"]["source_page_number"] = 2

        with self.assertRaises(ProtocolAnalysisEvidenceError) as failure:
            analyze_protocol_chunk(
                extraction,
                plan.chunks[0],
                RichClaimModel(wrong_page),
            )
        self.assertEqual(
            failure.exception.diagnostic.reason_code,
            "invalid_source_hash",
        )

    def test_untrusted_chunk_response_is_bounded_before_json_decoding(self):
        def oversized(response):
            response["unexpected"] = "x" * (MAX_CHUNK_CLAIM_RESPONSE_BYTES + 1)

        with self.assertRaises(ProtocolAnalysisResponseError):
            self.analyze(RichClaimModel(oversized))

    def test_repeated_duration_claims_are_retained_and_block_execution(self):
        def second_duration(response):
            record = dict(response["claims"][7])
            record["claim_id"] = "duration-2"
            record["source_order"] = 11
            record["source_text"] = "20 min"
            record["evidence"] = dict(record["evidence"])
            response["claims"].append(record)
            response["page_coverage"][0]["evidence_item_ids"].append("duration-2")

        result = self.analyze(RichClaimModel(second_duration))
        merged = merge_validated_chunk_results(
            self.extraction,
            self.plan,
            (result,),
        )
        draft = assemble_validated_protocol_claims(self.extraction, merged)
        ambiguities = tuple(
            item
            for item in draft.protocol.constructs
            if isinstance(item, domain.SourceAmbiguity)
        )
        self.assertTrue(any(item.source_text == "20 min" for item in ambiguities))
        self.assertIn(
            domain.ReadinessReasonCode.UNRESOLVED_AMBIGUITY.value,
            draft.readiness.reason_codes,
        )
        conditions = draft.protocol.sections[0].steps[0].sub_actions[0].conditions
        self.assertEqual(
            [item.source_text for item in conditions if "min" in item.source_text],
            ["15 min", "20 min"],
        )

    def test_analysis_required_claim_result_cannot_be_approved_or_executed(self):
        merged = merge_validated_chunk_results(
            self.extraction,
            self.plan,
            (self.analyze(),),
        )
        draft = assemble_validated_protocol_claims(self.extraction, merged)
        store = initialize_protocol_store(
            ProtocolPersistenceSettings(True, self.root / "blocked-catalog")
        )
        try:
            catalog = ProtocolCatalog(store)
            entry = catalog.register(
                self.source,
                source_filename="blocked.pdf",
                media_type="application/pdf",
            ).entry
            analysis = store.append_analysis_revision(
                entry.protocol_id,
                1,
                "analysis-claim-blocked",
                draft.protocol,
                draft.readiness,
                draft.capability_policy_id,
            )
            with self.assertRaises(ProtocolApprovalError):
                catalog.approve(
                    entry.protocol_id,
                    f"pdf-1-analysis-{analysis.analysis_revision_number}",
                    policy=SharedSecretApprovalPolicy("secret"),
                    presented_secret="secret",
                )
            with self.assertRaises(ProtocolCatalogUnavailableError):
                catalog.load_executable_fixture(entry.protocol_id)
        finally:
            store.close()

    def test_analysis_incomplete_coverage_cannot_form_a_partial_protocol(self):
        def incomplete(response):
            response["page_coverage"][0]["status"] = "analysis_incomplete"

        result = self.analyze(RichClaimModel(incomplete))
        with self.assertRaises(ProtocolChunkMergeError) as failure:
            merge_validated_chunk_results(
                self.extraction,
                self.plan,
                (result,),
            )
        self.assertEqual(failure.exception.reason_code, "incomplete_source_coverage")

    def test_numbered_action_omission_is_not_accepted_as_complete_coverage(self):
        def omit_action(response):
            response["claims"] = [
                claim
                for claim in response["claims"]
                if claim["category"] != "action"
            ]
            response["page_coverage"][0]["evidence_item_ids"].remove("action-1")

        with self.assertRaises(ProtocolAnalysisEvidenceError) as failure:
            self.analyze(RichClaimModel(omit_action))
        self.assertEqual(
            failure.exception.diagnostic.reason_code,
            "numbered_action_missing",
        )

    def test_page_count_claim_routing_is_default_off_and_explicitly_enabled(self):
        many = self.root / "many.pdf"
        write_pages(
            many,
            tuple(
                f"Protocol Evidence Preparation {number}. Do action {number}."
                for number in range(1, 41)
            ),
        )
        extraction = extract_protocol_pdf(many)
        limits = ChunkAnalysisLimits()
        plan = plan_protocol_chunks(
            extraction,
            f"protocol-{extraction.sha256[:32]}",
            "pdf-1",
            limits=limits,
        )
        self.assertEqual(limits.max_chunk_text_bytes, 192 * 1024)
        self.assertEqual(len(plan.chunks), 5)
        self.assertTrue(all(len(chunk.core_page_refs) <= 8 for chunk in plan.chunks))
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(_analysis_state(extraction), "structured_analysis_ready")
        with patch.dict(
            "os.environ",
            {CLAIM_CHUNK_ANALYSIS_ENABLED_ENV: "false"},
        ):
            self.assertEqual(_analysis_state(extraction), "structured_analysis_ready")
        with patch.dict(
            "os.environ",
            {CLAIM_CHUNK_ANALYSIS_ENABLED_ENV: "true"},
        ):
            self.assertEqual(_analysis_state(extraction), "chunked_analysis_required")

    def test_production_catalog_uses_claim_chunks_for_small_nine_page_protocol(self):
        from tests.test_protocol_chunk_analysis import FakeChunkModel

        source = self.root / "nine-pages.pdf"
        write_pages(
            source,
            tuple(
                f"Protocol Large Section {number} {number}. Do action {number}."
                for number in range(1, 10)
            ),
        )
        store = initialize_protocol_store(
            ProtocolPersistenceSettings(True, self.root / "catalog")
        )
        try:
            with patch.dict(
                "os.environ",
                {CLAIM_CHUNK_ANALYSIS_ENABLED_ENV: "true"},
            ):
                catalog = ProtocolCatalog(store)
                entry = catalog.register(
                    source,
                    source_filename="nine-pages.pdf",
                    media_type="application/pdf",
                ).entry
                self.assertEqual(entry.analysis_status, "chunked_analysis_required")
                analyzed = catalog.analyze(
                    entry.protocol_id,
                    FakeChunkModel(),
                    analysis_id="claim-production-boundary",
                    chunk_limits=ChunkAnalysisLimits(max_retries=0),
                )
                self.assertEqual(analyzed.analysis_status, "review_required")
                self.assertFalse(analyzed.available_for_execution)
                status = catalog.analysis_run_status(entry.protocol_id)
                self.assertEqual(status.total_chunks, 2)
                self.assertEqual(status.completed_chunks, 2)
        finally:
            store.close()

    def test_production_catalog_keeps_page_count_route_monolithic_by_default(self):
        source = self.root / "nine-pages-default-off.pdf"
        write_pages(
            source,
            tuple(
                f"Protocol Large Section {number} {number}. Do action {number}."
                for number in range(1, 10)
            ),
        )
        store = initialize_protocol_store(
            ProtocolPersistenceSettings(True, self.root / "default-off-catalog")
        )
        try:
            with patch.dict(
                "os.environ",
                {CLAIM_CHUNK_ANALYSIS_ENABLED_ENV: "false"},
            ):
                catalog = ProtocolCatalog(store)
                entry = catalog.register(
                    source,
                    source_filename="nine-pages-default-off.pdf",
                    media_type="application/pdf",
                ).entry
                self.assertEqual(
                    entry.analysis_status,
                    "structured_analysis_ready",
                )
                self.assertEqual(
                    catalog.analysis_run_status(entry.protocol_id).total_chunks,
                    0,
                )
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
