"""Provider-free bounded large-PDF planning, analysis, merge, and recovery."""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from voice_workflow_agent.experiment_protocol_analysis import (
    ProtocolAnalysisEvidenceError,
)
from voice_workflow_agent.experiment_protocol_config import (
    ProtocolPersistenceSettings,
)
from tests.test_protocol_claim_analysis import declined_handles
from voice_workflow_agent.experiment_protocol_pdf import extract_protocol_pdf
from voice_workflow_agent.experiment_protocol_store import (
    initialize_protocol_store,
)
from voice_workflow_agent.protocol_catalog import (
    CLAIM_CHUNK_ANALYSIS_ENABLED_ENV,
    ProtocolCatalog,
    ProtocolChunkAnalysisFailedError,
    ProtocolChunkMergeConflictError,
    ProtocolCatalogUnavailableError,
)
from voice_workflow_agent.protocol_chunk_analysis import (
    ChunkAnalysisLimits,
    ProtocolChunkAdmissionError,
    ProtocolChunkMergeError,
    ProtocolChunkResultError,
    ValidatedChunkResult,
    analyze_protocol_chunk,
    assemble_validated_protocol_claims,
    extraction_for_chunk,
    merge_validated_chunk_results,
    plan_protocol_chunks,
    validate_chunk_result,
)
from voice_workflow_agent.protocol_claim_analysis import (
    CLAIM_SCHEMA_VERSION,
)


_FIXTURE_PAGE_WIDTH = 4000  # wide enough that one unwrapped fixture line
# is never clipped: a bounded extractor would otherwise drop the tail and
# disagree with an unbounded one on synthetic input only.


def write_pages(path: Path, page_texts: tuple[str | None, ...]) -> None:
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
        if raw_text is None:
            continue
        # One positioned show operation per line, so a fixture that writes a
        # numbered step on its own line really gets one.
        lines = raw_text.split("\n")
        operations = []
        for offset, line in enumerate(lines):
            escaped = (
                line.replace("\\", "\\\\")
                .replace("(", "\\(")
                .replace(")", "\\)")
            )
            operations.append(
                f"BT /F1 9 Tf 36 {740 - offset * 12} Td ({escaped}) Tj ET"
            )
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
    writer.add_metadata({"/Title": "Protocol Large"})
    with path.open("wb") as target:
        writer.write(target)


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


def page_local_schema_handles(response_schema) -> dict[int, tuple[str, ...]]:
    branches = response_schema["$defs"]["page_local_core_evidence"]["oneOf"]
    return {
        branch["properties"]["source_page_number"]["const"]: tuple(
            branch["properties"]["evidence_segment_ids"]["items"]["enum"]
        )
        for branch in branches
    }


class FakeChunkModel:
    def __init__(self, *, conflict_on_page: int | None = None) -> None:
        self.calls = 0
        self.conflict_on_page = conflict_on_page

    def analyze(self, *, system_prompt, input_json, response_schema) -> str:
        normalized_prompt = " ".join(system_prompt.split())
        assert (
            "For each distinct explicit numbered source action"
            in normalized_prompt
        )
        assert "all other non-action claims" in normalized_prompt
        assert "never substitute for it" in normalized_prompt
        assert "ExperimentProtocol" not in json.dumps(response_schema)
        assert "evidence_item_ids" not in json.dumps(response_schema)
        self.calls += 1
        request = json.loads(input_json)
        pages = [page for page in request["pages"] if page["role"] == "core"]
        assert page_local_schema_handles(response_schema) == {
            page["source_page_number"]: tuple(
                segment[0] for segment in page["segments"]
            )
            for page in pages
        }
        structure = []
        claims = []
        coverage = []
        for page in pages:
            number = page["source_page_number"]
            instruction = f"{number}. Do action {number}."
            title = f"Section {number}"
            if number == self.conflict_on_page:
                instruction = f"{number}. Do conflicting action {number}."
            item_ids = []
            evidence = lambda excerpt: evidence_for_excerpt(page, excerpt)
            if number == 1:
                structure.append(
                    {
                        "marker_id": "protocol-title",
                        "kind": "protocol_title",
                        "source_order": 0,
                        "section_id": None,
                        "evidence": evidence("Protocol Large"),
                    }
                )
                item_ids.append("protocol-title")
            if title in page_text(page):
                marker_id = f"marker-section-{number}"
                structure.append(
                    {
                        "marker_id": marker_id,
                        "kind": "section",
                        "source_order": 1,
                        "section_id": f"section-{number}",
                        "evidence": evidence(title),
                    }
                )
                item_ids.append(marker_id)
            if instruction in page_text(page):
                claim_id = f"action-{number}"
                claims.append(
                    {
                        "claim_id": claim_id,
                        "category": "action",
                        "source_order": 2,
                        "section_id": f"section-{number}",
                        "step_id": f"step-{number}",
                        "source_label": str(number),
                        "target_claim_id": None,
                        "required_for_execution": True,
                        "repeated_step_labels": None,
                        "repetition_count": None,
                        "evidence": evidence(instruction),
                    }
                )
                item_ids.append(claim_id)
            coverage.append(
                {
                    "source_page_number": number,
                    "analysis_incomplete": False,
                    "non_step_labels": [],
                    "declined_evidence_segment_ids": declined_handles(
                        page, [*structure, *claims]
                    ),
                }
            )
        response = {
            "claim_schema_version": CLAIM_SCHEMA_VERSION,
            "capability_policy_id": "p1-conservative",
            "request_handle": request["request_handle"],
            "page_coverage": coverage,
            "structure": structure,
            "claims": claims,
        }
        return json.dumps(response, separators=(",", ":"))


class RetryOnceChunkModel(FakeChunkModel):
    def __init__(self) -> None:
        super().__init__()
        self._seen: set[int] = set()
        self._lock = threading.Lock()

    def analyze(self, **kwargs) -> str:
        request = json.loads(kwargs["input_json"])
        first_page = next(
            page["source_page_number"]
            for page in request["pages"]
            if page["role"] == "core"
        )
        with self._lock:
            if first_page not in self._seen:
                self._seen.add(first_page)
                self.calls += 1
                raise RuntimeError("private retry detail")
        return super().analyze(**kwargs)


class ProtocolChunkAnalysisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.claim_chunk_gate = patch.dict(
            "os.environ",
            {CLAIM_CHUNK_ANALYSIS_ENABLED_ENV: "true"},
        )
        self.claim_chunk_gate.start()
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.pdf = self.root / "large.pdf"
        self.page_texts = tuple(
            f"Protocol Large Section {number}\n{number}. Do action {number}. "
            + ("x" * 48)
            for number in range(1, 7)
        )
        write_pages(self.pdf, self.page_texts)
        self.extraction = extract_protocol_pdf(self.pdf)
        largest = max(
            len(page.text.encode("utf-8")) for page in self.extraction.pages
        )
        self.limits = ChunkAnalysisLimits(
            max_pages=16,
            max_extracted_text_bytes=64 * 1024,
            max_chunks=16,
            max_chunk_text_bytes=largest + 4,
            max_chunk_result_bytes=512 * 1024,
            max_concurrency=2,
            timeout_seconds=2,
            max_retries=1,
            overlap_pages=0,
        )
        self.protocol_id = "protocol-" + self.extraction.sha256[:32]
        self.plan = plan_protocol_chunks(
            self.extraction,
            self.protocol_id,
            "pdf-1",
            limits=self.limits,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()
        self.claim_chunk_gate.stop()

    def results(self, model: FakeChunkModel | None = None):
        model = model or FakeChunkModel()
        return tuple(
            ValidatedChunkResult(
                chunk,
                analyze_protocol_chunk(self.extraction, chunk, model),
            )
            for chunk in self.plan.chunks
        )

    def test_planner_is_deterministic_ordered_bounded_and_page_aligned(self):
        duplicate = plan_protocol_chunks(
            self.extraction,
            self.protocol_id,
            "pdf-1",
            limits=self.limits,
        )
        self.assertEqual(self.plan, duplicate)
        self.assertGreater(len(self.plan.chunks), 1)
        self.assertEqual(
            [chunk.ordinal for chunk in self.plan.chunks],
            list(range(len(self.plan.chunks))),
        )
        core = tuple(
            page
            for chunk in self.plan.chunks
            for page in chunk.core_page_refs
        )
        self.assertEqual(core, tuple(range(1, self.extraction.page_count + 1)))
        for chunk in self.plan.chunks:
            self.assertLessEqual(
                chunk.extracted_text_bytes,
                self.limits.max_chunk_text_bytes,
            )
            self.assertEqual(
                chunk.source_page_refs,
                tuple(range(chunk.source_page_start, chunk.source_page_end + 1)),
            )

    def test_planner_admission_limits_and_empty_pdf_fail_closed(self):
        with self.assertRaises(ProtocolChunkAdmissionError):
            plan_protocol_chunks(
                self.extraction,
                self.protocol_id,
                "pdf-1",
                limits=replace(self.limits, max_pages=2),
            )
        scanned = self.root / "scanned.pdf"
        write_pages(scanned, (None, None))
        with self.assertRaises(ProtocolChunkAdmissionError):
            plan_protocol_chunks(
                extract_protocol_pdf(scanned),
                "protocol-" + "a" * 32,
                "pdf-1",
                limits=self.limits,
            )
        with self.assertRaises(ProtocolChunkAdmissionError):
            replace(self.limits, max_concurrency=3)
        with self.assertRaises(ProtocolChunkAdmissionError):
            replace(self.limits, max_core_source_bytes_per_chunk=0)
        with self.assertRaises(ProtocolChunkAdmissionError):
            replace(
                self.limits,
                max_core_source_bytes_per_chunk=192 * 1024 + 1,
            )

    def test_core_source_budget_subdivides_legacy_page_windows(self):
        sizes = tuple(
            len(page.text.encode("utf-8"))
            for page in self.extraction.pages
        )
        budget = sizes[0] + sizes[1] + 1
        limits = replace(
            self.limits,
            max_chunks=8,
            max_chunk_text_bytes=64 * 1024,
            max_core_pages_per_chunk=4,
            max_core_source_bytes_per_chunk=budget,
        )

        plan = plan_protocol_chunks(
            self.extraction,
            self.protocol_id,
            "pdf-1",
            limits=limits,
        )

        self.assertEqual(
            tuple(chunk.core_page_refs for chunk in plan.chunks),
            ((1, 2), (3, 4), (5, 6)),
        )
        self.assertEqual(
            tuple(
                page
                for chunk in plan.chunks
                for page in chunk.core_page_refs
            ),
            tuple(range(1, 7)),
        )
        self.assertNotEqual(
            plan.planner_configuration_sha256,
            plan_protocol_chunks(
                self.extraction,
                self.protocol_id,
                "pdf-1",
                limits=replace(
                    limits,
                    max_core_source_bytes_per_chunk=budget + 1,
                ),
            ).planner_configuration_sha256,
        )
        with self.assertRaises(ProtocolChunkAdmissionError):
            plan_protocol_chunks(
                self.extraction,
                self.protocol_id,
                "pdf-1",
                limits=replace(limits, max_chunks=2),
            )

    def test_planner_overlap_is_page_local_bounded_and_identity_bound(self):
        overlap_pdf = self.root / "overlap.pdf"
        write_pages(
            overlap_pdf,
            (
                "Protocol Large Section 1\n1. Do action 1. " + "a" * 40,
                "Protocol Large Section 2\n2. Do action 2. " + "b" * 40,
                "Protocol Large Section 3\n3. Do action 3. " + "c" * 130,
                "Protocol Large Section 4\n4. Do action 4. " + "d" * 80,
            ),
        )
        extraction = extract_protocol_pdf(overlap_pdf)
        sizes = [len(page.text.encode("utf-8")) for page in extraction.pages]
        overlap_limits = replace(
            self.limits,
            max_chunk_text_bytes=sizes[1] + sizes[2],
            overlap_pages=1,
        )
        plan = plan_protocol_chunks(
            extraction,
            "protocol-" + extraction.sha256[:32],
            "pdf-1",
            limits=overlap_limits,
        )
        self.assertEqual(plan.chunks[1].overlap_page_refs, (2,))
        self.assertEqual(plan.chunks[1].source_page_refs, (2, 3))
        self.assertLessEqual(
            plan.chunks[1].extracted_text_bytes,
            overlap_limits.max_chunk_text_bytes,
        )
        changed = replace(
            plan.chunks[1],
            extracted_text_sha256="0" * 64,
        )
        with self.assertRaises(ProtocolChunkResultError):
            extraction_for_chunk(extraction, changed)

    def test_chunk_scope_blanks_other_pages_and_rejects_external_evidence(self):
        chunk = self.plan.chunks[0]
        scoped = extraction_for_chunk(self.extraction, chunk)
        outside = next(
            page for page in scoped.pages if page.source_page_number not in chunk.source_page_refs
        )
        self.assertTrue(outside.text_empty)
        bad = FakeChunkModel()
        original = bad.analyze

        def outside_response(**kwargs):
            response = json.loads(original(**kwargs))
            response["claims"][0]["evidence"] = {
                "source_page_number": outside.source_page_number,
                "evidence_segment_ids": ["s-" + "0" * 16],
            }
            return json.dumps(response)

        bad.analyze = outside_response
        with self.assertRaises(ProtocolAnalysisEvidenceError):
            analyze_protocol_chunk(self.extraction, chunk, bad)

    def test_complete_valid_results_merge_in_source_order_and_require_review(self):
        claims = merge_validated_chunk_results(
            self.extraction,
            self.plan,
            self.results(),
        )
        merged = assemble_validated_protocol_claims(self.extraction, claims)
        labels = tuple(
            step.source_label
            for section in merged.protocol.sections
            for step in section.steps
        )
        self.assertEqual(labels, tuple(str(value) for value in range(1, 7)))
        self.assertEqual(merged.protocol.metadata.pdf, self.extraction)
        self.assertEqual(merged.protocol.protocol_id, self.protocol_id)

    def test_missing_cross_document_revision_run_and_planner_results_fail(self):
        results = self.results()
        with self.assertRaises(ProtocolChunkMergeError) as missing:
            merge_validated_chunk_results(
                self.extraction,
                self.plan,
                results[:-1],
            )
        self.assertEqual(missing.exception.reason_code, "missing_chunk_result")
        changed = replace(
            results[0],
            chunk=replace(results[0].chunk, candidate_revision_id="pdf-2"),
        )
        with self.assertRaises(ProtocolChunkResultError):
            validate_chunk_result(self.plan, changed, self.extraction)
        changed = replace(
            results[0],
            chunk=replace(results[0].chunk, analysis_run_id="run-stale"),
        )
        with self.assertRaises(ProtocolChunkResultError):
            validate_chunk_result(self.plan, changed, self.extraction)
        changed = replace(
            results[0],
            chunk=replace(results[0].chunk, planner_version="stale-planner"),
        )
        with self.assertRaises(ProtocolChunkResultError):
            validate_chunk_result(self.plan, changed, self.extraction)
        changed = replace(
            results[0],
            chunk=replace(results[0].chunk, document_id="0" * 64),
        )
        with self.assertRaises(ProtocolChunkResultError):
            validate_chunk_result(self.plan, changed, self.extraction)

    def test_duplicate_exact_chunk_deduplicates_but_conflict_fails(self):
        results = self.results()
        claims = merge_validated_chunk_results(
            self.extraction,
            self.plan,
            results + (results[0],),
        )
        merged = assemble_validated_protocol_claims(self.extraction, claims)
        self.assertEqual(
            sum(len(section.steps) for section in merged.protocol.sections),
            6,
        )
        with self.assertRaises(ProtocolChunkMergeError) as conflict:
            merge_validated_chunk_results(
                self.extraction,
                self.plan,
                results + (replace(results[0], attempts=2),),
            )
        self.assertEqual(conflict.exception.reason_code, "duplicate_chunk_conflict")

    def test_valid_but_conflicting_section_identity_fails_closed(self):
        results = list(self.results())
        second = results[1]
        changed_marker = replace(
            second.analysis.structure[0],
            marker_id=results[0].analysis.structure[-1].marker_id,
        )
        results[1] = replace(
            second,
            analysis=replace(
                second.analysis,
                structure=(changed_marker,),
                page_coverage=(
                    replace(
                        second.analysis.page_coverage[0],
                        evidence_item_ids=(
                            changed_marker.marker_id,
                            second.analysis.claims[0].claim_id,
                        ),
                    ),
                ),
            ),
        )
        with self.assertRaises(ProtocolChunkMergeError) as conflict:
            merge_validated_chunk_results(
                self.extraction,
                self.plan,
                results,
            )
        self.assertEqual(
            conflict.exception.reason_code,
            "structure_marker_conflict",
        )

    def test_catalog_large_run_is_explicit_idempotent_and_review_required(self):
        settings = ProtocolPersistenceSettings(True, self.root / "catalog")
        store = initialize_protocol_store(settings)
        try:
            catalog = ProtocolCatalog(store)
            with patch(
                "voice_workflow_agent.protocol_catalog._analysis_state",
                return_value="chunked_analysis_required",
            ):
                entry = catalog.register(
                    self.pdf,
                    source_filename="large.pdf",
                    media_type="application/pdf",
                ).entry
                model = FakeChunkModel()
                result = catalog.analyze(
                    entry.protocol_id,
                    model,
                    analysis_id="analysis-large-review",
                    chunk_limits=self.limits,
                )
                calls = model.calls
                replay = catalog.analyze(
                    entry.protocol_id,
                    model,
                    analysis_id="analysis-large-replay",
                    chunk_limits=self.limits,
                )
            self.assertEqual(result.analysis_status, "review_required")
            self.assertEqual(replay.revision_id, result.revision_id)
            self.assertEqual(model.calls, calls)
            self.assertFalse(result.available_for_execution)
            with self.assertRaises(ProtocolCatalogUnavailableError):
                catalog.load_executable_fixture(entry.protocol_id)
            status = catalog.analysis_run_status(entry.protocol_id)
            self.assertEqual(status.state, "review_required")
            self.assertEqual(status.completed_chunks, len(self.plan.chunks))
            self.assertEqual(status.failed_chunks, 0)
            completed = [
                event
                for event in store.list_events(entry.protocol_id)
                if event.event_type == "protocol_chunk_analysis_completed"
            ]
            self.assertTrue(completed)
            self.assertNotIn(
                "analysis_payload_json",
                json.dumps(status.public_dict()),
            )
            self.assertNotIn(
                "claim_payload_json",
                json.dumps(status.public_dict()),
            )
        finally:
            store.close()

        reopened = initialize_protocol_store(settings)
        try:
            status = ProtocolCatalog(reopened).analysis_run_status(entry.protocol_id)
            self.assertEqual(status.state, "review_required")
            self.assertEqual(status.restart_behavior, "terminal_review_required")
        finally:
            reopened.close()

    def test_valid_chunk_conflict_persists_merge_conflict_and_no_candidate(self):
        store = initialize_protocol_store(
            ProtocolPersistenceSettings(True, self.root / "conflict-catalog")
        )
        try:
            catalog = ProtocolCatalog(store)
            with patch(
                "voice_workflow_agent.protocol_catalog._analysis_state",
                return_value="chunked_analysis_required",
            ):
                entry = catalog.register(
                    self.pdf,
                    source_filename="large.pdf",
                    media_type="application/pdf",
                ).entry

                class ConflictingSectionModel(FakeChunkModel):
                    def analyze(self, **kwargs):
                        response = json.loads(super().analyze(**kwargs))
                        for marker in response["structure"]:
                            if marker["kind"] == "section":
                                marker["marker_id"] = "shared-section-marker"
                        return json.dumps(response, separators=(",", ":"))

                with self.assertRaises(ProtocolChunkMergeConflictError):
                    catalog.analyze(
                        entry.protocol_id,
                        ConflictingSectionModel(),
                        analysis_id="analysis-conflicting-merge",
                        chunk_limits=self.limits,
                    )
            status = catalog.analysis_run_status(entry.protocol_id)
            self.assertEqual(status.state, "merge_conflict")
            self.assertEqual(status.failure_code, "merge_conflict")
            self.assertEqual(status.merge_status, "conflict")
            self.assertFalse(store.list_analysis_revisions(entry.protocol_id, 1))
        finally:
            store.close()

    def test_listing_and_status_do_not_invoke_model_or_promote_analysis(self):
        store = initialize_protocol_store(
            ProtocolPersistenceSettings(True, self.root / "read-only-catalog")
        )
        try:
            catalog = ProtocolCatalog(store)
            with patch(
                "voice_workflow_agent.protocol_catalog._analysis_state",
                return_value="chunked_analysis_required",
            ):
                entry = catalog.register(
                    self.pdf,
                    source_filename="large.pdf",
                    media_type="application/pdf",
                ).entry
                model = Mock()
                catalog.list_entries()
                status = catalog.analysis_run_status(entry.protocol_id)
            model.analyze.assert_not_called()
            self.assertEqual(status.state, "chunked_analysis_required")
            self.assertFalse(entry.available_for_execution)
        finally:
            store.close()

    def test_failed_chunks_persist_only_safe_codes_and_never_merge(self):
        store = initialize_protocol_store(
            ProtocolPersistenceSettings(True, self.root / "failed-catalog")
        )
        try:
            catalog = ProtocolCatalog(store)
            with patch(
                "voice_workflow_agent.protocol_catalog._analysis_state",
                return_value="chunked_analysis_required",
            ):
                entry = catalog.register(
                    self.pdf,
                    source_filename="large.pdf",
                    media_type="application/pdf",
                ).entry
                model = Mock()
                model.analyze.side_effect = RuntimeError(
                    "secret raw provider body"
                )
                with self.assertRaises(ProtocolChunkAnalysisFailedError):
                    catalog.analyze(
                        entry.protocol_id,
                        model,
                        analysis_id="analysis-failed-chunks",
                        chunk_limits=replace(self.limits, max_retries=0),
                    )
            status = catalog.analysis_run_status(entry.protocol_id)
            self.assertEqual(status.state, "chunk_analysis_failed")
            self.assertGreater(status.failed_chunks, 0)
            rendered = json.dumps(
                [event.payload for event in store.list_events(entry.protocol_id)]
            )
            self.assertNotIn("secret raw provider body", rendered)
            self.assertFalse(store.list_analysis_revisions(entry.protocol_id, 1))
        finally:
            store.close()

    def test_timeout_is_terminal_and_late_worker_result_cannot_merge(self):
        store = initialize_protocol_store(
            ProtocolPersistenceSettings(True, self.root / "timeout-catalog")
        )
        try:
            catalog = ProtocolCatalog(store)
            with patch(
                "voice_workflow_agent.protocol_catalog._analysis_state",
                return_value="chunked_analysis_required",
            ):
                entry = catalog.register(
                    self.pdf,
                    source_filename="large.pdf",
                    media_type="application/pdf",
                ).entry

                class SlowModel(FakeChunkModel):
                    def analyze(self, **kwargs):
                        time.sleep(0.05)
                        return super().analyze(**kwargs)

                with self.assertRaises(ProtocolChunkAnalysisFailedError):
                    catalog.analyze(
                        entry.protocol_id,
                        SlowModel(),
                        analysis_id="analysis-timeout",
                        chunk_limits=replace(
                            self.limits,
                            timeout_seconds=0.005,
                            max_retries=0,
                        ),
                    )
            time.sleep(0.07)
            status = catalog.analysis_run_status(entry.protocol_id)
            self.assertEqual(status.state, "chunk_analysis_failed")
            self.assertGreater(status.failed_chunks, 0)
            self.assertFalse(store.list_analysis_revisions(entry.protocol_id, 1))
        finally:
            store.close()

    def test_bounded_retry_succeeds_without_duplicate_chunk_results(self):
        store = initialize_protocol_store(
            ProtocolPersistenceSettings(True, self.root / "retry-catalog")
        )
        try:
            catalog = ProtocolCatalog(store)
            with patch(
                "voice_workflow_agent.protocol_catalog._analysis_state",
                return_value="chunked_analysis_required",
            ):
                entry = catalog.register(
                    self.pdf,
                    source_filename="large.pdf",
                    media_type="application/pdf",
                ).entry
                model = RetryOnceChunkModel()
                result = catalog.analyze(
                    entry.protocol_id,
                    model,
                    analysis_id="analysis-retried",
                    chunk_limits=self.limits,
                )
            self.assertEqual(result.analysis_status, "review_required")
            status = catalog.analysis_run_status(entry.protocol_id)
            self.assertEqual(status.completed_chunks, status.total_chunks)
            completed = [
                event
                for event in store.list_events(entry.protocol_id)
                if event.event_type == "protocol_chunk_analysis_completed"
            ]
            self.assertEqual(len(completed), status.total_chunks)
            self.assertTrue(all(event.payload["attempts"] == 2 for event in completed))
        finally:
            store.close()

    def test_concurrency_is_serial_by_default_and_two_is_explicitly_bounded(self):
        self.assertEqual(ChunkAnalysisLimits().max_concurrency, 1)
        store = initialize_protocol_store(
            ProtocolPersistenceSettings(True, self.root / "concurrency-catalog")
        )
        active = 0
        maximum_active = 0
        lock = threading.Lock()

        class ObservedConcurrencyModel(FakeChunkModel):
            def analyze(inner_self, **kwargs):
                nonlocal active, maximum_active
                with lock:
                    active += 1
                    maximum_active = max(maximum_active, active)
                try:
                    time.sleep(0.02)
                    return super().analyze(**kwargs)
                finally:
                    with lock:
                        active -= 1

        try:
            catalog = ProtocolCatalog(store)
            with patch(
                "voice_workflow_agent.protocol_catalog._analysis_state",
                return_value="chunked_analysis_required",
            ):
                entry = catalog.register(
                    self.pdf,
                    source_filename="large.pdf",
                    media_type="application/pdf",
                ).entry
                catalog.analyze(
                    entry.protocol_id,
                    ObservedConcurrencyModel(),
                    analysis_id="analysis-concurrency-two",
                    chunk_limits=replace(
                        self.limits,
                        max_concurrency=2,
                        max_retries=0,
                    ),
                )
            self.assertEqual(maximum_active, 2)
        finally:
            store.close()

    def test_timeout_is_one_total_run_deadline_not_one_timeout_per_batch(self):
        store = initialize_protocol_store(
            ProtocolPersistenceSettings(True, self.root / "deadline-catalog")
        )

        class PerChunkDelayModel(FakeChunkModel):
            def analyze(self, **kwargs):
                time.sleep(0.03)
                return super().analyze(**kwargs)

        try:
            catalog = ProtocolCatalog(store)
            with patch(
                "voice_workflow_agent.protocol_catalog._analysis_state",
                return_value="chunked_analysis_required",
            ):
                entry = catalog.register(
                    self.pdf,
                    source_filename="large.pdf",
                    media_type="application/pdf",
                ).entry
                started = time.monotonic()
                with self.assertRaises(ProtocolChunkAnalysisFailedError):
                    catalog.analyze(
                        entry.protocol_id,
                        PerChunkDelayModel(),
                        analysis_id="analysis-total-deadline",
                        chunk_limits=replace(
                            self.limits,
                            max_concurrency=1,
                            timeout_seconds=0.05,
                            max_retries=0,
                        ),
                    )
                elapsed = time.monotonic() - started
            self.assertLess(elapsed, 0.12)
            self.assertFalse(store.list_analysis_revisions(entry.protocol_id, 1))
        finally:
            store.close()

    def test_interrupted_restart_is_honest_and_explicit_cancel_is_terminal(self):
        settings = ProtocolPersistenceSettings(True, self.root / "restart-catalog")
        store = initialize_protocol_store(settings)
        catalog = ProtocolCatalog(store)
        try:
            with patch(
                "voice_workflow_agent.protocol_catalog._analysis_state",
                return_value="chunked_analysis_required",
            ):
                entry = catalog.register(
                    self.pdf,
                    source_filename="large.pdf",
                    media_type="application/pdf",
                ).entry
            revision = catalog._latest_protocol_revision(entry.protocol_id)
            plan = plan_protocol_chunks(
                self.extraction,
                entry.protocol_id,
                "pdf-1",
                limits=self.limits,
            )
            catalog._append_chunk_event(
                revision,
                plan,
                "protocol_chunk_plan_created",
                plan.public_dict(),
                "plan",
            )
        finally:
            store.close()

        reopened = initialize_protocol_store(settings)
        try:
            recovered = ProtocolCatalog(reopened)
            status = recovered.analysis_run_status(entry.protocol_id)
            self.assertEqual(status.state, "chunk_planned")
            self.assertEqual(
                status.restart_behavior,
                "explicit_new_run_required_after_interruption",
            )
            model = Mock()
            with patch(
                "voice_workflow_agent.protocol_catalog._analysis_state",
                return_value="chunked_analysis_required",
            ):
                recovered.analyze(
                    entry.protocol_id,
                    model,
                    analysis_id="must-not-resume",
                    chunk_limits=self.limits,
                )
            model.analyze.assert_not_called()
            cancelled = recovered.cancel_analysis_run(
                entry.protocol_id,
                plan.analysis_run_id,
            )
            self.assertEqual(cancelled.state, "chunk_analysis_cancelled")
            self.assertFalse(reopened.list_analysis_revisions(entry.protocol_id, 1))
        finally:
            reopened.close()

    def test_cancelled_run_rejects_late_chunk_completions_and_never_merges(self):
        settings = ProtocolPersistenceSettings(True, self.root / "cancel-catalog")
        initial = initialize_protocol_store(settings)
        try:
            catalog = ProtocolCatalog(initial)
            with patch(
                "voice_workflow_agent.protocol_catalog._analysis_state",
                return_value="chunked_analysis_required",
            ):
                entry = catalog.register(
                    self.pdf,
                    source_filename="large.pdf",
                    media_type="application/pdf",
                ).entry
            plan = plan_protocol_chunks(
                self.extraction,
                entry.protocol_id,
                "pdf-1",
                limits=self.limits,
            )
        finally:
            initial.close()

        started = threading.Event()
        release = threading.Event()
        outcome: list[str] = []

        class BlockingModel(FakeChunkModel):
            def analyze(self, **kwargs):
                started.set()
                release.wait(1)
                return super().analyze(**kwargs)

        def analyze_in_worker():
            worker_store = initialize_protocol_store(settings)
            try:
                with patch(
                    "voice_workflow_agent.protocol_catalog._analysis_state",
                    return_value="chunked_analysis_required",
                ):
                    ProtocolCatalog(worker_store).analyze(
                        entry.protocol_id,
                        BlockingModel(),
                        analysis_id="analysis-cancelled-late",
                        chunk_limits=self.limits,
                    )
            except ProtocolChunkAnalysisFailedError as exc:
                outcome.append(exc.code)
            finally:
                worker_store.close()

        worker = threading.Thread(target=analyze_in_worker)
        worker.start()
        self.assertTrue(started.wait(1))
        cancelling_store = initialize_protocol_store(settings)
        try:
            status = ProtocolCatalog(cancelling_store).cancel_analysis_run(
                entry.protocol_id,
                plan.analysis_run_id,
            )
            self.assertEqual(status.state, "chunk_analysis_cancelled")
        finally:
            cancelling_store.close()
        release.set()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(outcome, ["chunk_analysis_failed"])

        closing_store = initialize_protocol_store(settings)
        try:
            catalog = ProtocolCatalog(closing_store)
            self.assertEqual(
                catalog.analysis_run_status(entry.protocol_id).state,
                "chunk_analysis_cancelled",
            )
            self.assertFalse(
                closing_store.list_analysis_revisions(entry.protocol_id, 1)
            )
        finally:
            closing_store.close()


if __name__ == "__main__":
    unittest.main()
