"""Evidence-first claim DTO, assembly, and production routing regressions."""

from __future__ import annotations

import hashlib
import re
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from scripts.diagnose_protocol_claim_latency import _privacy_safe_action_audit
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
    MAX_EVIDENCE_ITEM_REFS_PER_PAGE,
    MAX_CHUNK_CLAIM_RESPONSE_BYTES,
    MAX_PAGE_COVERAGE_RECORDS,
    ClaimCategory,
    claim_response_schema,
    claim_response_schema_metrics,
    generate_page_evidence_segments,
    prepare_chunk_claim_request_context,
    resolve_claim_source_evidence,
    validate_chunk_claim_analysis,
    _numbered_step_labels,
)


_FIXTURE_PAGE_WIDTH = 4000  # wide enough that one unwrapped fixture line
# is never clipped: a bounded extractor would otherwise drop the tail and
# disagree with an unbounded one on synthetic input only.


_FIXTURE_LINE_LEADING = 12  # points between successive baselines


def write_lined_pages(
    path: Path,
    pages: tuple[tuple[str, ...], ...],
    *,
    title: str = "Protocol Evidence",
) -> None:
    """Write each page as real, separate text lines.

    ``write_pages`` collapses newlines to spaces, so every page it produces has
    zero line breaks.  Boundary rules that key on end-of-line punctuation are
    therefore never exercised by those fixtures.  This writer emits one
    positioned show operation per line instead, so the extracted text contains
    actual line breaks.  Existing ``write_pages`` callers are unaffected.
    """

    writer = PdfWriter()
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_ref = writer._add_object(font)
    for lines in pages:
        page = writer.add_blank_page(width=_FIXTURE_PAGE_WIDTH, height=792)
        operations = ["BT /F1 9 Tf"]
        baseline = 740
        for line in lines:
            escaped = (
                line.replace("\\", "\\\\")
                .replace("(", "\\(")
                .replace(")", "\\)")
                .replace("\n", " ")
            )
            operations.append(f"1 0 0 1 36 {baseline} Tm ({escaped}) Tj")
            baseline -= _FIXTURE_LINE_LEADING
        operations.append("ET")
        stream = DecodedStreamObject()
        stream.set_data(" ".join(operations).encode("ascii"))
        page[NameObject("/Resources")] = DictionaryObject(
            {
                NameObject("/Font"): DictionaryObject(
                    {NameObject("/F1"): font_ref}
                )
            }
        )
        page[NameObject("/Contents")] = writer._add_object(stream)
    writer.add_metadata({"/Title": title})
    with path.open("wb") as target:
        writer.write(target)


def write_pages(path: Path, page_texts: tuple[str, ...]) -> None:
    """Write one page per string, honouring any newlines it contains.

    This used to replace every newline with a space, so a fixture written as
    ``"Preparation\n1. Add buffer."`` silently became one line and its
    numbered step ended up mid-sentence. Those fixtures only passed because
    the trigger also matched a number in the middle of a line, which no real
    document needs. A page with no newline is written exactly as before.
    """

    if any("\n" in text for text in page_texts):
        write_lined_pages(
            path, tuple(tuple(text.split("\n")) for text in page_texts)
        )
        return
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
        page = writer.add_blank_page(width=_FIXTURE_PAGE_WIDTH, height=792)
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


def declined_handles(
    page: dict[str, object],
    records: list[dict[str, object]],
) -> list[str]:
    """Handles for substantive segments on this page that no record cites.

    Every fixture model has to answer for the whole page now: cite a segment or
    decline it explicitly.  Punctuation-only fragments carry nothing to claim
    and are exempt.
    """

    page_number = page["source_page_number"]
    cited = {
        handle
        for record in records
        if record["evidence"]["source_page_number"] == page_number
        for handle in record["evidence"]["evidence_segment_ids"]
    }
    segments = page["segments"]
    assert isinstance(segments, list)
    return [
        str(segment[0])
        for segment in segments
        if str(segment[0]) not in cited
        and re.search(r"[A-Za-z0-9]", str(segment[1]))
    ]


def page_text(page: dict[str, object]) -> str:
    return "".join(segment[1] for segment in page["segments"])  # type: ignore[index]


def evidence_for_excerpt(
    page: dict[str, object], excerpt: str
) -> dict[str, object]:
    segments = page["segments"]
    assert isinstance(segments, list)
    text = page_text(page)
    start = text.index(excerpt)
    end = start + len(excerpt)
    selected: list[str] = []
    offset = 0
    for segment in segments:
        assert isinstance(segment, list)
        segment_id, segment_text = segment
        assert isinstance(segment_text, str)
        assert isinstance(segment_id, str)
        segment_end = offset + len(segment_text)
        if segment_end > start and offset < end:
            selected.append(segment_id)
        offset = segment_end
    assert selected
    return {
        "source_page_number": page["source_page_number"],
        "evidence_segment_ids": selected,
    }


def page_local_schema_handles(
    response_schema: dict[str, object],
) -> dict[int, tuple[str, ...]]:
    definitions = response_schema["$defs"]
    assert isinstance(definitions, dict)
    selector = definitions["page_local_core_evidence"]
    assert isinstance(selector, dict)
    branches = selector["oneOf"]
    assert isinstance(branches, list)
    result: dict[int, tuple[str, ...]] = {}
    for branch in branches:
        page_schema = branch["properties"]["source_page_number"]
        handle_schema = branch["properties"]["evidence_segment_ids"]["items"]
        result[page_schema["const"]] = tuple(handle_schema["enum"])
    return result


def page_local_schema_accepts_evidence(
    response_schema: dict[str, object],
    evidence: dict[str, object],
) -> bool:
    page_number = evidence.get("source_page_number")
    handles = evidence.get("evidence_segment_ids")
    if not isinstance(page_number, int) or isinstance(page_number, bool):
        return False
    if not isinstance(handles, list) or not 1 <= len(handles) <= 256:
        return False
    allowed = page_local_schema_handles(response_schema).get(page_number)
    return allowed is not None and all(
        isinstance(handle, str) and handle in allowed for handle in handles
    )


class RichClaimModel:
    """Strict provider fake that emits every supported claim category."""

    def __init__(self, mutation=None) -> None:
        self.mutation = mutation

    def analyze(self, *, system_prompt, input_json, response_schema) -> str:
        del system_prompt
        request = json.loads(input_json)
        core_pages = tuple(
            item for item in request["pages"] if item["role"] == "core"
        )
        expected_handles = {
            item["source_page_number"]: tuple(
                segment[0] for segment in item["segments"]
            )
            for item in core_pages
        }
        if page_local_schema_handles(response_schema) != expected_handles:
            raise AssertionError("The chunk path used the wrong claim schema.")
        page = core_pages[0]
        page_number = page["source_page_number"]

        def evidence(excerpt: str) -> dict[str, object]:
            return evidence_for_excerpt(page, excerpt)

        action = (
            "1. Add 10 mL buffer at 5% and incubate at 37 C for 15 min at "
            "800 rpm then hold for 20 min; WARNING hot; observe clear; repeat "
            "steps 1-1 until clear; volume is not specified."
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
            ("repeat-1", "repeat_condition", "repeat steps 1-1 until clear", "action-1"),
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
                    "section_id": None if top_level else "section-preparation",
                    "step_id": None if top_level else "step-1",
                    "source_label": "1" if is_action else None,
                    "target_claim_id": target,
                    "required_for_execution": (
                        category in {"action", "explicit_missing_ambiguous_value"}
                    ),
                    # A repeat must declare the range it repeats, and the
                    # cited excerpt must contain it.
                    "repeated_step_labels": (
                        ["1", "1"] if category == "repeat_condition" else None
                    ),
                    "evidence": evidence(excerpt),
                }
            )
        structure = [
            {
                "marker_id": "protocol-title",
                "kind": "protocol_title",
                "source_order": 0,
                "section_id": None,
                "evidence": evidence("Protocol Evidence"),
            },
            {
                "marker_id": "marker-preparation",
                "kind": "section",
                "source_order": 1,
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
            "request_handle": request["request_handle"],
            "page_coverage": [
                {
                    "source_page_number": page_number,
                    "analysis_incomplete": False,
                    "declined_evidence_segment_ids": declined_handles(
                        page, [*structure, *records]
                    ),
                }
            ],
            "structure": structure,
            "claims": records,
        }
        if self.mutation is not None:
            self.mutation(response)
        return json.dumps(response, separators=(",", ":"))


class RequestAwareRichClaimModel(RichClaimModel):
    """Expose only the fictional request structure to deterministic mutations."""

    def analyze(self, *, system_prompt, input_json, response_schema) -> str:
        self.request = json.loads(input_json)
        return super().analyze(
            system_prompt=system_prompt,
            input_json=input_json,
            response_schema=response_schema,
        )


class ProtocolClaimAnalysisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "rich.pdf"
        self.action = (
            "1. Add 10 mL buffer at 5% and incubate at 37 C for 15 min at "
            "800 rpm then hold for 20 min; WARNING hot; observe clear; repeat "
            "steps 1-1 until clear; volume is not specified."
        )
        write_pages(
            self.source,
            (
                "Protocol Evidence Preparation Before start: thaw sample. "
                "Material: buffer 10 mL 5%. Equipment: mixer 800 rpm.\n"
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

    def claim_request(self):
        chunk = self.plan.chunks[0]
        return prepare_chunk_claim_request_context(
            self.extraction,
            source_revision=chunk.candidate_revision_id,
            chunk_id=chunk.chunk_id,
            ordinal=chunk.ordinal,
            core_page_refs=chunk.core_page_refs,
            context_page_refs=chunk.overlap_page_refs,
        )

    def test_chunk_schema_is_small_and_every_claim_has_independent_provenance(self):
        rendered_schema = json.dumps(CLAIM_RESPONSE_SCHEMA, sort_keys=True)
        self.assertNotIn("ExperimentProtocol", rendered_schema)
        self.assertNotIn('"protocol"', rendered_schema)
        self.assertNotIn("source_excerpt", rendered_schema)
        self.assertNotIn("source_text", rendered_schema)
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
            self.assertEqual(claim.source_text, claim.evidence.source_excerpt)
        action = next(
            claim
            for claim in result.analysis.claims
            if claim.category is ClaimCategory.ACTION
        )
        for category in (ClaimCategory.QUANTITY, ClaimCategory.DURATION):
            parameter = next(
                claim
                for claim in result.analysis.claims
                if claim.category is category
            )
            self.assertEqual(parameter.target_claim_id, action.claim_id)
            self.assertEqual(parameter.step_id, action.step_id)

    def test_prompt_requires_one_action_claim_per_numbered_source_action(self):
        normalized_prompt = " ".join(CLAIM_ANALYSIS_SYSTEM_PROMPT.split())
        self.assertIn(
            "For each distinct explicit numbered source action",
            normalized_prompt,
        )
        self.assertIn(
            "Never omit or merge numbered source actions",
            normalized_prompt,
        )
        self.assertIn(
            "all other non-action claims may coexist with an action claim but "
            "never substitute for it",
            normalized_prompt,
        )
        self.assertIn(
            "mark that page analysis_incomplete",
            normalized_prompt,
        )

    def test_provider_cannot_author_or_override_canonical_source_text(self):
        result = self.analyze()
        self.assertTrue(
            all(
                item.source_text == item.evidence.source_excerpt
                for item in (*result.analysis.structure, *result.analysis.claims)
            )
        )

        def inject_paraphrase(response):
            response["claims"][0]["source_text"] = "provider paraphrase"

        with self.assertRaises(ProtocolAnalysisResponseError):
            self.analyze(RichClaimModel(inject_paraphrase))

        altered = replace(
            result.analysis,
            claims=(
                replace(result.analysis.claims[0], source_text="provider paraphrase"),
                *result.analysis.claims[1:],
            ),
        )
        with self.assertRaises(ProtocolAnalysisEvidenceError):
            validate_chunk_claim_analysis(
                altered,
                self.extraction,
                source_revision="pdf-1",
                chunk_id=self.plan.chunks[0].chunk_id,
                core_page_refs=self.plan.chunks[0].core_page_refs,
            )

    def test_semantic_relationships_remain_provider_selected_and_validated(self):
        model = RequestAwareRichClaimModel()

        def retarget_quantity_to_material(response):
            material = response["claims"][0]
            quantity = response["claims"][4]
            quantity["target_claim_id"] = material["claim_id"]
            quantity["section_id"] = None
            quantity["step_id"] = None
            quantity["evidence"] = dict(material["evidence"])

        model.mutation = retarget_quantity_to_material
        result = self.analyze(model)
        quantity = next(
            claim
            for claim in result.analysis.claims
            if claim.claim_id == "quantity-1"
        )
        self.assertEqual(quantity.target_claim_id, "material-buffer")
        self.assertEqual(quantity.source_text, quantity.evidence.source_excerpt)
        merged = merge_validated_chunk_results(
            self.extraction,
            self.plan,
            (result,),
        )
        draft = assemble_validated_protocol_claims(self.extraction, merged)
        self.assertEqual(len(draft.protocol.materials[0].quantities), 1)

    def test_claim_schema_makes_overlong_coverage_arrays_invalid(self):
        coverage_schema = claim_response_schema(self.claim_request())["properties"][
            "page_coverage"
        ]

        self.assertEqual(coverage_schema["minItems"], 1)
        self.assertEqual(coverage_schema["maxItems"], 1)
        self.assertEqual(
            CLAIM_RESPONSE_SCHEMA["properties"]["page_coverage"]["maxItems"],
            MAX_PAGE_COVERAGE_RECORDS,
        )
        self.assertNotIn(
            "evidence_item_ids",
            coverage_schema["items"]["properties"],
        )
        self.assertNotIn(
            "evidence_item_ids",
            coverage_schema["items"]["required"],
        )

        too_many_coverage_records = [
            {"source_page_number": 1, "status": "complete"}
            for _ in range(MAX_PAGE_COVERAGE_RECORDS + 1)
        ]
        self.assertGreater(
            len(too_many_coverage_records),
            coverage_schema["maxItems"],
        )

    def test_request_schema_binds_page_handle_pairs_and_exact_coverage(self):
        pages = tuple(
            replace(
                self.extraction.pages[0],
                source_page_number=page_number,
                text=text,
            )
            for page_number, text in enumerate(
                (
                    "Preparation\n1. Add buffer.\n2. Mix sample.",
                    "Processing\n3. Incubate sample.\n4. Stop mixing.",
                    "Context\n5. Prior-page action.",
                ),
                start=1,
            )
        )
        extraction = replace(self.extraction, page_count=3, pages=pages)
        request = prepare_chunk_claim_request_context(
            extraction,
            source_revision="pdf-page-local-schema",
            chunk_id="chunk-page-local-schema",
            ordinal=0,
            core_page_refs=(1, 3),
            context_page_refs=(2,),
        )
        core_pages = request.core_page_refs
        schema = claim_response_schema(request)
        coverage = schema["properties"]["page_coverage"]
        self.assertEqual(coverage["minItems"], len(core_pages))
        self.assertEqual(coverage["maxItems"], len(core_pages))
        self.assertEqual(
            coverage["items"]["properties"]["source_page_number"]["enum"],
            list(core_pages),
        )
        self.assertNotIn(
            2,
            coverage["items"]["properties"]["source_page_number"]["enum"],
        )
        expected_handles = {
            page.source_page_number: tuple(
                evidence.handle for evidence in page.evidence
            )
            for page in request.pages
            if page.role == "core"
        }
        context_handles = {
            evidence.handle
            for page in request.pages
            if page.role == "context"
            for evidence in page.evidence
        }
        self.assertEqual(page_local_schema_handles(schema), expected_handles)
        self.assertTrue(
            context_handles.isdisjoint(
                handle
                for handles in page_local_schema_handles(schema).values()
                for handle in handles
            )
        )
        for section_name in ("structure", "claims"):
            evidence_schema = schema["properties"][section_name]["items"][
                "properties"
            ]["evidence"]
            self.assertEqual(
                evidence_schema,
                {"$ref": "#/$defs/page_local_core_evidence"},
            )

        first_page_handles = list(expected_handles[1])
        second_page_handles = list(expected_handles[3])
        context_handle = next(iter(context_handles))
        cases = (
            (
                "matching page and handle",
                {
                    "source_page_number": 1,
                    "evidence_segment_ids": first_page_handles[:1],
                },
                True,
            ),
            (
                "same-page adjacent handles",
                {
                    "source_page_number": 1,
                    "evidence_segment_ids": first_page_handles[:2],
                },
                True,
            ),
            (
                "wrong core-page handle",
                {
                    "source_page_number": 1,
                    "evidence_segment_ids": second_page_handles[:1],
                },
                False,
            ),
            (
                "context-only handle",
                {"source_page_number": 1, "evidence_segment_ids": [context_handle]},
                False,
            ),
            (
                "fabricated handle",
                {"source_page_number": 1, "evidence_segment_ids": ["s-fabricated"]},
                False,
            ),
            (
                "mixed-page handle list",
                {
                    "source_page_number": 1,
                    "evidence_segment_ids": [
                        first_page_handles[0],
                        second_page_handles[0],
                    ],
                },
                False,
            ),
            (
                "declared context page",
                {"source_page_number": 2, "evidence_segment_ids": [context_handle]},
                False,
            ),
        )
        for label, evidence, expected in cases:
            with self.subTest(label=label):
                self.assertIs(
                    page_local_schema_accepts_evidence(schema, evidence),
                    expected,
                )
        for page_number, handles in expected_handles.items():
            for handle in handles:
                with self.subTest(
                    label="every issued core handle",
                    page_number=page_number,
                ):
                    self.assertTrue(
                        page_local_schema_accepts_evidence(
                            schema,
                            {
                                "source_page_number": page_number,
                                "evidence_segment_ids": [handle],
                            },
                        )
                    )

        metrics = claim_response_schema_metrics(request)
        self.assertEqual(
            metrics.handle_enum_entry_count,
            sum(map(len, expected_handles.values())),
        )
        self.assertEqual(
            metrics.core_handle_count,
            metrics.handle_enum_entry_count,
        )
        self.assertEqual(metrics.context_handle_count, len(context_handles))
        self.assertEqual(
            metrics.largest_page_handle_enum,
            max(map(len, expected_handles.values())),
        )
        self.assertGreater(metrics.schema_after_bytes, metrics.schema_before_bytes)

    def test_coverage_cardinality_is_derived_and_provider_refs_are_rejected(self):
        def too_many_coverage_records(response):
            record = response["page_coverage"][0]
            response["page_coverage"] = [
                dict(record) for _ in range(MAX_PAGE_COVERAGE_RECORDS + 1)
            ]

        with self.assertRaises(ProtocolAnalysisResponseError):
            self.analyze(RichClaimModel(too_many_coverage_records))

        def provider_authored_reference(response):
            response["page_coverage"][0]["evidence_item_ids"] = ["fabricated"]

        with self.assertRaises(ProtocolAnalysisResponseError):
            self.analyze(RichClaimModel(provider_authored_reference))

        def too_many_emitted_items(response):
            template = next(
                claim
                for claim in response["claims"]
                if claim["category"] == "material"
            )
            response["claims"] = []
            for index in range(MAX_EVIDENCE_ITEM_REFS_PER_PAGE + 1):
                claim = dict(template)
                claim["claim_id"] = f"material-{index}"
                response["claims"].append(claim)

        with self.assertRaises(ProtocolAnalysisEvidenceError) as cardinality_failure:
            self.analyze(RichClaimModel(too_many_emitted_items))
        self.assertEqual(
            cardinality_failure.exception.diagnostic.mismatch_class,
            "coverage_reference_cardinality_exceeded",
        )
        self.assertEqual(
            cardinality_failure.exception.diagnostic.actual_count,
            MAX_EVIDENCE_ITEM_REFS_PER_PAGE + 3,
        )

        valid = self.analyze()
        self.assertEqual(len(valid.analysis.page_coverage), 1)
        self.assertEqual(
            valid.analysis.page_coverage[0].evidence_item_ids,
            tuple(
                sorted(
                    [item.marker_id for item in valid.analysis.structure]
                    + [item.claim_id for item in valid.analysis.claims]
                )
            ),
        )

    def test_nine_emitted_items_are_all_derived_into_page_coverage(self):
        def retain_nine_items(response):
            response["claims"] = response["claims"][:7]

        result = self.analyze(RichClaimModel(retain_nine_items))
        expected = {
            item.marker_id for item in result.analysis.structure
        } | {
            item.claim_id for item in result.analysis.claims
        }

        self.assertEqual(len(expected), 9)
        self.assertEqual(
            set(result.analysis.page_coverage[0].evidence_item_ids),
            expected,
        )
        self.assertTrue(
            {item.marker_id for item in result.analysis.structure}
            <= set(result.analysis.page_coverage[0].evidence_item_ids)
        )
        self.assertTrue(
            {item.claim_id for item in result.analysis.claims}
            <= set(result.analysis.page_coverage[0].evidence_item_ids)
        )

    def test_canonical_coverage_backstop_rejects_missing_fabricated_and_duplicate_ids(self):
        result = self.analyze().analysis
        coverage = result.page_coverage[0]
        claim_id = result.claims[0].claim_id
        marker_id = result.structure[0].marker_id
        cases = {
            "missing claim": tuple(
                item_id
                for item_id in coverage.evidence_item_ids
                if item_id != claim_id
            ),
            "missing marker": tuple(
                item_id
                for item_id in coverage.evidence_item_ids
                if item_id != marker_id
            ),
            "fabricated": (*coverage.evidence_item_ids, "fabricated-id"),
            "duplicate": (
                *coverage.evidence_item_ids,
                coverage.evidence_item_ids[0],
            ),
        }
        for label, evidence_item_ids in cases.items():
            with self.subTest(label=label):
                altered = replace(
                    result,
                    page_coverage=(
                        replace(
                            coverage,
                            evidence_item_ids=evidence_item_ids,
                        ),
                    ),
                )
                with self.assertRaises(ProtocolAnalysisEvidenceError):
                    validate_chunk_claim_analysis(
                        altered,
                        self.extraction,
                        source_revision="pdf-1",
                        chunk_id=self.plan.chunks[0].chunk_id,
                        core_page_refs=self.plan.chunks[0].core_page_refs,
                    )

    def test_zero_item_core_page_requires_no_relevant_claims_status(self):
        source = self.root / "zero-items.pdf"
        write_pages(source, ("Context page without relevant claims.",))
        extraction = extract_protocol_pdf(source)
        plan = plan_protocol_chunks(
            extraction,
            f"protocol-{extraction.sha256[:32]}",
            "pdf-1",
        )

        class ZeroItemModel:
            def __init__(self, status: str) -> None:
                self.status = status

            def analyze(self, *, system_prompt, input_json, response_schema) -> str:
                del system_prompt, response_schema
                request = json.loads(input_json)
                page = next(
                    item for item in request["pages"] if item["role"] == "core"
                )
                return json.dumps(
                    {
                        "claim_schema_version": CLAIM_SCHEMA_VERSION,
                        "capability_policy_id": "p1-conservative",
                        "request_handle": request["request_handle"],
                        "page_coverage": [
                            {
                                "source_page_number": page["source_page_number"],
                                "analysis_incomplete": self.status
                                == "analysis_incomplete",
                                # Claiming nothing now means declining every
                                # substantive segment, on the record.
                                "declined_evidence_segment_ids": (
                                    declined_handles(page, [])
                                ),
                            }
                        ],
                        "structure": [],
                        "claims": [],
                    }
                )

        result = analyze_protocol_chunk(
            extraction,
            plan.chunks[0],
            ZeroItemModel("no_relevant_claims"),
        )
        self.assertEqual(result.page_coverage[0].evidence_item_ids, ())
        self.assertEqual(
            result.page_coverage[0].status.value,
            "no_relevant_claims",
        )

        # Superseded: a provider used to be able to declare "complete" for a
        # page holding nothing, and this asserted the refusal. The status is
        # now derived from the item count and the dispositions, so that
        # sentence is not sayable. Declaring the opposite self-report cannot
        # manufacture a complete page either.
        result = analyze_protocol_chunk(
            extraction,
            plan.chunks[0],
            ZeroItemModel("complete"),
        )
        self.assertEqual(
            result.page_coverage[0].status.value, "no_relevant_claims"
        )

    def test_every_core_page_still_requires_exactly_one_coverage_record(self):
        two_page_source = self.root / "exact-coverage.pdf"
        page_text = (
            "Protocol Evidence Preparation Before start: thaw sample. "
            "Material: buffer 10 mL 5%. Equipment: mixer 800 rpm.\n"
            + self.action
        )
        write_pages(two_page_source, (page_text, page_text))
        extraction = extract_protocol_pdf(two_page_source)
        plan = plan_protocol_chunks(
            extraction,
            f"protocol-{extraction.sha256[:32]}",
            "pdf-1",
        )
        self.assertEqual(plan.chunks[0].core_page_refs, (1, 2))

        with self.assertRaises(ProtocolAnalysisResponseError) as failure:
            analyze_protocol_chunk(
                extraction,
                plan.chunks[0],
                RichClaimModel(),
            )
        safe = failure.exception.diagnostic.privacy_safe_dict()
        self.assertEqual(safe["reason_code"], "coverage_mismatch")
        self.assertEqual(
            safe["mismatch_class"],
            "incomplete_or_duplicate_page_coverage",
        )
        self.assertEqual(safe["expected_count"], 2)
        self.assertEqual(safe["actual_count"], 1)

    def test_evidence_segments_are_deterministic_and_source_identity_bound(self):
        multiline = replace(
            self.extraction,
            pages=(
                replace(
                    self.extraction.pages[0],
                    text=(
                        "Preparation notes\n"
                        "1. Add 10 mL buffer.\nKeep the vessel covered.\n"
                        "2. Incubate for 15 min.\nWARNING: hot surface."
                    ),
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
        # Five segments, not three: an end-of-line sentence now closes a block,
        # so the unnumbered lines are no longer absorbed into the numbered step
        # above them.
        self.assertEqual(len(first), 5)
        texts = [segment.text.strip() for segment in first]
        self.assertIn("Keep the vessel covered.", texts)
        self.assertIn("WARNING: hot surface.", texts)
        for step_text in (s for s in texts if s.startswith(("1.", "2."))):
            self.assertNotIn("Keep the vessel covered", step_text)
            self.assertNotIn("WARNING: hot surface", step_text)
        self.assertNotEqual(
            [segment.segment_id for segment in first],
            [segment.segment_id for segment in other_revision],
        )
        self.assertTrue(all(segment.segment_id.startswith("seg-") for segment in first))

    def test_action_blocks_are_bounded_and_preserve_multiline_execution_text(self):
        action = (
            "1. Before start, add 10 mL buffer and incubate for 15 min.\n"
            "WARNING: hot surface. Repeat until the sample is clear.\n"
            + ("Continue mixing without changing the source text.\n" * 120)
        )
        extraction = replace(
            self.extraction,
            pages=(replace(self.extraction.pages[0], text=action + "2. Stop mixing."),),
        )
        segments = generate_page_evidence_segments(
            extraction,
            source_revision="pdf-long-action",
            page_number=1,
        )

        self.assertEqual("".join(item.text for item in segments), extraction.pages[0].text)
        self.assertTrue(all(len(item.text) <= 4096 for item in segments))
        reconstructed = "".join(item.text for item in segments)
        for phrase in (
            "Before start",
            "10 mL",
            "15 min",
            "WARNING: hot surface",
            "Repeat until the sample is clear",
        ):
            self.assertIn(phrase, reconstructed)

    def test_single_and_adjacent_multi_segment_evidence_resolve_exactly(self):
        multiline = replace(
            self.extraction,
            pages=(
                replace(
                    self.extraction.pages[0],
                    text="Preparation\n1. Add buffer.\nContinue mixing.\n2. Stop mixing.",
                ),
            ),
        )
        request = prepare_chunk_claim_request_context(
            multiline,
            source_revision="pdf-segments",
            chunk_id="chunk-segments",
            ordinal=0,
            core_page_refs=(1,),
            context_page_refs=(),
        )
        segments = request.pages[0].evidence
        single = resolve_claim_source_evidence(
            {"source_page_number": 1, "evidence_segment_ids": [segments[1].handle]},
            request=request,
        )
        adjacent = resolve_claim_source_evidence(
            {
                "source_page_number": 1,
                "evidence_segment_ids": [
                    segments[0].handle,
                    segments[1].handle,
                ],
            },
            request=request,
        )

        self.assertEqual(single.source_excerpt, segments[1].segment.text)
        self.assertEqual(
            adjacent.source_excerpt,
            segments[0].segment.text + segments[1].segment.text,
        )
        self.assertEqual(
            adjacent.evidence_segment_ids,
            (segments[0].segment.segment_id, segments[1].segment.segment_id),
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
                    text="Preparation\n1. Add buffer.\n2. Mix.\n3. Stop.",
                ),
                second_page,
            ),
        )
        request = prepare_chunk_claim_request_context(
            multiline,
            source_revision="pdf-segments",
            chunk_id="chunk-segments",
            ordinal=0,
            core_page_refs=(1,),
            context_page_refs=(2,),
        )
        wrong_revision = prepare_chunk_claim_request_context(
            multiline,
            source_revision="pdf-other",
            chunk_id="chunk-segments",
            ordinal=0,
            core_page_refs=(1,),
            context_page_refs=(2,),
        )
        core_page = next(page for page in request.pages if page.source_page_number == 1)
        context_page = next(page for page in request.pages if page.source_page_number == 2)
        segments = core_page.evidence
        cases = (
            {
                "source_page_number": 1,
                "evidence_segment_ids": ["s-" + "0" * 16],
            },
            {
                "source_page_number": 1,
                "evidence_segment_ids": [context_page.evidence[0].handle],
            },
            {
                "source_page_number": 1,
                "evidence_segment_ids": [
                    segments[0].handle,
                    segments[2].handle,
                ],
            },
            {
                "source_page_number": 1,
                "evidence_segment_ids": [
                    segments[1].handle,
                    segments[0].handle,
                ],
            },
            {
                "source_page_number": 1,
                "evidence_segment_ids": [
                    wrong_revision.pages[1].evidence[0].handle
                ],
            },
        )
        for raw in cases:
            with self.subTest(raw=tuple(raw["evidence_segment_ids"])):
                with self.assertRaises(ProtocolAnalysisEvidenceError):
                    resolve_claim_source_evidence(
                        raw,
                        request=request,
                    )
        with self.assertRaises(ProtocolAnalysisEvidenceError):
            resolve_claim_source_evidence(
                {
                    "source_page_number": 1,
                    "evidence_segment_ids": [segments[0].handle],
                },
                request=wrong_revision,
            )
        with self.assertRaises(ProtocolAnalysisEvidenceError):
            resolve_claim_source_evidence(
                {
                    "source_page_number": 2,
                    "evidence_segment_ids": [context_page.evidence[0].handle],
                },
                request=request,
            )

    def test_request_is_minimal_exact_and_handles_are_request_scoped(self):
        second_source = self.root / "provider-pages.pdf"
        write_pages(
            second_source,
            (
                "Protocol Evidence Context page.",
                "Protocol Evidence Core page.",
            ),
        )
        extraction = extract_protocol_pdf(second_source)
        context = prepare_chunk_claim_request_context(
            extraction,
            source_revision="pdf-provider-pages",
            chunk_id="chunk-provider-pages",
            ordinal=1,
            core_page_refs=(2,),
            context_page_refs=(1,),
        )
        request = json.loads(context.input_json())

        self.assertEqual(
            set(request),
            {"claim_schema_version", "capability_policy_id", "request_handle", "pages"},
        )
        self.assertEqual(len(request["pages"]), 2)
        for page in request["pages"]:
            page_number = page["source_page_number"]
            self.assertEqual(set(page), {"source_page_number", "role", "segments"})
            self.assertEqual(
                page_text(page),
                extraction.pages[page_number - 1].text,
            )
            self.assertTrue(page["segments"])
            self.assertTrue(all(len(segment[0]) == 18 for segment in page["segments"]))
        other = prepare_chunk_claim_request_context(
            extraction,
            source_revision="pdf-provider-pages",
            chunk_id="chunk-provider-pages-other",
            ordinal=1,
            core_page_refs=(2,),
            context_page_refs=(1,),
        )
        self.assertNotEqual(context.request_handle, other.request_handle)
        self.assertNotEqual(
            context.pages[0].evidence[0].handle,
            other.pages[0].evidence[0].handle,
        )
        with self.assertRaises(ProtocolAnalysisEvidenceError):
            resolve_claim_source_evidence(
                {
                    "source_page_number": 2,
                    "evidence_segment_ids": [other.pages[1].evidence[0].handle],
                },
                request=context,
            )
        self.assertIn("opaque, request-scoped server identities", CLAIM_ANALYSIS_SYSTEM_PROMPT)
        self.assertIn("Never return source_excerpt text", CLAIM_ANALYSIS_SYSTEM_PROMPT)

    def test_compact_request_is_smaller_than_full_canonical_segment_contract(self):
        context = prepare_chunk_claim_request_context(
            self.extraction,
            source_revision="pdf-1",
            chunk_id="chunk-size-regression",
            ordinal=0,
            core_page_refs=(1,),
            context_page_refs=(),
        )
        legacy = {
            "claim_schema_version": 2,
            "capability_policy_id": "p1-conservative",
            "source": {
                "source_revision": context.source_revision,
                "source_sha256": context.source_sha256,
                "page_count": self.extraction.page_count,
            },
            "chunk": {
                "chunk_id": context.chunk_id,
                "ordinal": context.ordinal,
                "core_page_refs": list(context.core_page_refs),
                "context_page_refs": list(context.context_page_refs),
            },
            "pages": [
                {
                    "source_page_number": page.source_page_number,
                    "role": page.role,
                    "page_text_sha256": page.evidence[0].segment.page_text_sha256,
                    "evidence_segments": [
                        {
                            "segment_id": item.segment.segment_id,
                            "text": item.segment.text,
                        }
                        for item in page.evidence
                    ],
                }
                for page in context.pages
            ],
        }
        legacy_bytes = len(
            json.dumps(
                legacy,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        self.assertLess(len(context.input_json().encode("utf-8")), legacy_bytes)

    def test_server_constructs_canonical_page_hash_after_validation(self):
        result = self.analyze()
        self.assertEqual(
            result.analysis.page_coverage[0].page_text_sha256,
            hashlib.sha256(self.extraction.pages[0].text.encode("utf-8")).hexdigest(),
        )

    def test_altered_or_missing_request_handle_fails_closed(self):
        def altered(response):
            response["request_handle"] = "r-" + "0" * 22

        with self.assertRaises(ProtocolAnalysisEvidenceError) as altered_failure:
            self.analyze(RichClaimModel(altered))
        self.assertEqual(
            altered_failure.exception.diagnostic.reason_code, "chunk_identity_mismatch"
        )
        self.assertEqual(
            altered_failure.exception.diagnostic.mismatch_class,
            "request_handle_mismatch",
        )

        def missing(response):
            del response["request_handle"]

        with self.assertRaises(ProtocolAnalysisResponseError):
            self.analyze(RichClaimModel(missing))

    def test_handle_from_another_analysis_run_fails_closed(self):
        two_page_source = self.root / "wrong-page-hash.pdf"
        page_text = (
            "Protocol Evidence Preparation Before start: thaw sample. "
            "Material: buffer 10 mL 5%. Equipment: mixer 800 rpm.\n"
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
        def other_run(response):
            response["request_handle"] = "r-" + "x" * 22

        with self.assertRaises(ProtocolAnalysisEvidenceError) as failure:
            analyze_protocol_chunk(
                extraction,
                plan.chunks[1],
                RichClaimModel(other_run),
            )
        self.assertEqual(
            failure.exception.diagnostic.reason_code, "chunk_identity_mismatch"
        )

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
        self.assertEqual(action.process_timer.duration.source_text, self.action)
        self.assertEqual(action.required_observations[0].source_text, self.action)
        self.assertEqual(action.instruction_source_text, action.evidence.source_excerpt)
        expected_locator = f"source_revision=pdf-1;source_sha256={self.extraction.sha256}"
        self.assertEqual(action.evidence.location_detail, expected_locator)
        self.assertEqual(action.warnings[0].evidence.location_detail, expected_locator)

    def test_multiple_action_claims_share_one_executable_step_identity(self):
        def second_action_claim(response):
            original = next(
                claim
                for claim in response["claims"]
                if claim["category"] == "action"
            )
            additional = dict(original)
            additional["claim_id"] = "action-1-additional"
            additional["source_order"] = original["source_order"] + 1
            additional["evidence"] = dict(original["evidence"])
            response["claims"].append(additional)

        result = self.analyze(RichClaimModel(second_action_claim))
        merged = merge_validated_chunk_results(
            self.extraction,
            self.plan,
            (result,),
        )
        draft = assemble_validated_protocol_claims(self.extraction, merged)

        self.assertEqual(len(draft.protocol.sections[0].steps), 1)
        self.assertEqual(
            len(draft.protocol.sections[0].steps[0].sub_actions),
            2,
        )
        self.assertEqual(
            {
                claim.step_id
                for claim in result.analysis.claims
                if claim.category is ClaimCategory.ACTION
            },
            {"step-1"},
        )

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
                "Material: buffer 10 mL 5%. Equipment: mixer 800 rpm.\n"
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
            "chunk_identity_mismatch",
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
            record["evidence"] = dict(record["evidence"])
            response["claims"].append(record)

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
        self.assertTrue(any(item.source_text == self.action for item in ambiguities))
        self.assertIn(
            domain.ReadinessReasonCode.UNRESOLVED_AMBIGUITY.value,
            draft.readiness.reason_codes,
        )
        conditions = draft.protocol.sections[0].steps[0].sub_actions[0].conditions
        self.assertEqual(
            [
                item.source_text
                for item in conditions
                if item.statement_id
                in {"parameter-duration-1", "parameter-duration-2"}
            ],
            [self.action, self.action],
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
            response["page_coverage"][0]["analysis_incomplete"] = True

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
            self.assertTrue(
                any(
                    claim["category"] in {"quantity", "duration", "prerequisite"}
                    for claim in response["claims"]
                )
            )

        with self.assertRaises(ProtocolAnalysisEvidenceError) as failure:
            self.analyze(RichClaimModel(omit_action))
        self.assertEqual(
            failure.exception.diagnostic.reason_code,
            "numbered_action_missing",
        )
        self.assertEqual(
            failure.exception.diagnostic.missing_numbered_action_count,
            1,
        )

    def test_two_numbered_actions_cannot_collapse_into_one_action_claim(self):
        source = self.root / "two-actions.pdf"
        write_pages(
            source,
            (
                "Protocol Evidence Preparation Before start: thaw sample. "
                "Material: buffer 10 mL 5%. Equipment: mixer 800 rpm.\n"
                + self.action
                + "\n2. Finish processing.",
            ),
        )
        extraction = extract_protocol_pdf(source)
        plan = plan_protocol_chunks(
            extraction,
            f"protocol-{extraction.sha256[:32]}",
            "pdf-1",
        )

        with self.assertRaises(ProtocolAnalysisEvidenceError) as failure:
            analyze_protocol_chunk(
                extraction,
                plan.chunks[0],
                RichClaimModel(),
            )

        self.assertEqual(
            failure.exception.diagnostic.reason_code,
            "numbered_action_missing",
        )
        self.assertEqual(
            failure.exception.diagnostic.missing_numbered_action_count,
            1,
        )

    def test_diagnostic_action_audit_is_structural_and_content_free(self):
        request = self.claim_request()
        valid_raw = RichClaimModel().analyze(
            system_prompt=CLAIM_ANALYSIS_SYSTEM_PROMPT,
            input_json=request.input_json(),
            response_schema=claim_response_schema(request),
        )
        valid = _privacy_safe_action_audit(
            valid_raw,
            self.extraction,
            request,
        )
        self.assertEqual(valid["required_action_count"], 1)
        self.assertEqual(valid["provider_action_count"], 1)
        self.assertEqual(valid["missing_action_count"], 0)

        def omit_action(response):
            response["claims"] = [
                claim
                for claim in response["claims"]
                if claim["category"] != "action"
            ]

        omitted_raw = RichClaimModel(omit_action).analyze(
            system_prompt=CLAIM_ANALYSIS_SYSTEM_PROMPT,
            input_json=request.input_json(),
            response_schema=claim_response_schema(request),
        )
        omitted = _privacy_safe_action_audit(
            omitted_raw,
            self.extraction,
            request,
        )
        rendered = json.dumps(omitted, sort_keys=True)
        self.assertEqual(omitted["provider_action_count"], 0)
        self.assertEqual(omitted["missing_action_count"], 1)
        self.assertEqual(omitted["missing_action_identities"], ["p1-n1"])
        self.assertIn(
            "duration",
            omitted["missing_action_other_categories"]["p1-n1"],
        )
        self.assertNotIn(self.action, rendered)
        self.assertNotIn(self.extraction.sha256, rendered)

    def test_common_false_positive_numbered_structures_are_excluded(self):
        source_text = "\n".join(
            (
                "1.1 Scope",
                "1 200 mg",
                "[1] Reference entry",
                "Figure 1A",
                "Page 25",
                "Note 1: context only",
                "1) 10 mL",
                "7. Mix the sample.",
            )
        )

        self.assertEqual(_numbered_step_labels(source_text), ("7",))

    def test_privacy_safe_diagnostics_cover_completed_response_failures(self):
        private_claim_id = "private-claim-id"
        private_handle = "s-private-handle"
        private_source_text = "PRIVATE MODEL NORMALIZATION"

        def unknown_handle(response):
            response["claims"][0]["evidence"]["evidence_segment_ids"] = [
                private_handle
            ]

        def provider_source_text_injection(response):
            response["claims"][0]["source_text"] = private_source_text

        def incomplete_coverage(response):
            response["page_coverage"] = []

        def duplicate_record(response):
            response["claims"][1]["claim_id"] = private_claim_id
            response["claims"][0]["claim_id"] = private_claim_id

        cases = (
            (
                unknown_handle,
                ProtocolAnalysisEvidenceError,
                "evidence_segment_unknown",
                "source_identity_mismatch",
            ),
            (
                provider_source_text_injection,
                ProtocolAnalysisResponseError,
                "invalid_response",
                "response_contract_violation",
            ),
            (
                incomplete_coverage,
                ProtocolAnalysisResponseError,
                "coverage_mismatch",
                "incomplete_or_duplicate_page_coverage",
            ),
            (
                duplicate_record,
                ProtocolAnalysisResponseError,
                "duplicate_evidence_item_identifier",
                "duplicate_or_conflicting_record",
            ),
        )
        for mutation, error_type, reason_code, mismatch_class in cases:
            with self.subTest(reason_code=reason_code):
                with self.assertRaises(error_type) as failure:
                    self.analyze(RichClaimModel(mutation))
                safe = failure.exception.diagnostic.privacy_safe_dict()
                rendered = json.dumps(safe, sort_keys=True)
                self.assertEqual(safe["reason_code"], reason_code)
                self.assertEqual(safe["mismatch_class"], mismatch_class)
                for private_value in (
                    private_claim_id,
                    private_handle,
                    private_source_text,
                    self.extraction.sha256,
                ):
                    self.assertNotIn(private_value, rendered)
                self.assertFalse(
                    {"source_hash", "quote_sha256", "chunk_id", "source_revision"}
                    & set(safe)
                )

    def test_selector_diagnostics_distinguish_wrong_page_context_and_range(self):
        two_page_source = self.root / "selector-diagnostics.pdf"
        page_text = (
            "Protocol Evidence Preparation Before start: thaw sample. "
            "Material: buffer 10 mL 5%. Equipment: mixer 800 rpm.\n"
            + self.action
            + "\n2. Finish processing."
        )
        write_pages(two_page_source, (page_text, page_text))
        extraction = extract_protocol_pdf(two_page_source)
        plan = plan_protocol_chunks(
            extraction,
            f"protocol-{extraction.sha256[:32]}",
            "pdf-1",
            limits=ChunkAnalysisLimits(max_core_pages_per_chunk=1),
        )
        chunk = plan.chunks[1]
        self.assertEqual(chunk.overlap_page_refs, (1,))

        model = RequestAwareRichClaimModel()

        def wrong_page_handle(response):
            context_page = next(
                page for page in model.request["pages"] if page["role"] == "context"
            )
            response["claims"][0]["evidence"]["evidence_segment_ids"] = [
                context_page["segments"][0][0]
            ]

        def context_page_evidence(response):
            context_page = next(
                page for page in model.request["pages"] if page["role"] == "context"
            )
            response["claims"][0]["evidence"] = {
                "source_page_number": context_page["source_page_number"],
                "evidence_segment_ids": [context_page["segments"][0][0]],
            }

        def non_contiguous_handles(response):
            core_page = next(
                page for page in model.request["pages"] if page["role"] == "core"
            )
            self.assertGreaterEqual(len(core_page["segments"]), 3)
            response["claims"][0]["evidence"]["evidence_segment_ids"] = [
                core_page["segments"][0][0],
                core_page["segments"][2][0],
            ]

        cases = (
            (
                wrong_page_handle,
                "chunk_identity_mismatch",
                "provider_handle_page_mismatch",
            ),
            (
                context_page_evidence,
                "chunk_identity_mismatch",
                "context_evidence_for_core_item",
            ),
            (
                non_contiguous_handles,
                "evidence_segment_range_invalid",
                "non_contiguous_source_evidence",
            ),
        )
        for mutation, reason_code, mismatch_class in cases:
            model.mutation = mutation
            with self.subTest(mismatch_class=mismatch_class):
                with self.assertRaises(ProtocolAnalysisEvidenceError) as failure:
                    analyze_protocol_chunk(extraction, chunk, model)
                safe = failure.exception.diagnostic.privacy_safe_dict()
                self.assertEqual(safe["reason_code"], reason_code)
                self.assertEqual(safe["mismatch_class"], mismatch_class)
                self.assertEqual(safe["item_type"], "claim")
                self.assertEqual(safe["item_index"], 0)
                self.assertEqual(safe["category"], "material")
                self.assertGreaterEqual(safe["provider_handle_count"], 1)

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
        self.assertEqual(limits.max_core_source_bytes_per_chunk, 4 * 1024)
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
                f"Protocol Large Section {number}\n{number}. Do action {number}. "
                + "x" * 700
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
                self.assertEqual(status.total_chunks, 3)
                self.assertEqual(status.completed_chunks, 3)
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
