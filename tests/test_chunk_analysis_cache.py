"""A cached chunk saves a call; it never saves a claim from being checked.

Merging needs every chunk at once and a chunk costs a call, so a document with
more chunks than remaining budget could not be closed at all -- arithmetic, not
model quality, was the obstacle. The cache removes that. What it must not
remove is any part of the validation a live response goes through, so these
tests spend most of their effort trying to get something past it: an entry
edited after it was written, an entry filed under a contract that has since
changed, an entry whose evidence no longer resolves.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from voice_workflow_agent.chunk_analysis_cache import (
    CACHE_DIRECTORY_ENV,
    ChunkAnalysisCache,
    ChunkCacheError,
    ChunkCacheKey,
    canonical_claim_payload,
    key_for_chunk,
    prompt_sha256,
)
from voice_workflow_agent.experiment_protocol_pdf import extract_protocol_pdf
from voice_workflow_agent.protocol_chunk_analysis import (
    ChunkAnalysisLimits,
    extraction_for_chunk,
    plan_protocol_chunks,
)
from voice_workflow_agent.protocol_claim_analysis import (
    CLAIM_ANALYSIS_SYSTEM_PROMPT,
    CLAIM_SCHEMA_VERSION,
    EVIDENCE_SEGMENT_VERSION,
    claim_response_schema,
    parse_chunk_claim_response,
    prepare_chunk_claim_request_context,
)

from voice_workflow_agent.protocol_catalog import _ACKNOWLEDGEABLE_GATES

from tests.test_protocol_chunk_analysis import FakeChunkModel
from tests.test_protocol_claim_analysis import RichClaimModel, write_pages


class _Fixture:
    """One planned chunk plus the raw payload a strict provider fake emits."""

    def __init__(self, root: Path) -> None:
        self.source = root / "source.pdf"
        action = (
            "1. Add 10 mL buffer at 5% and incubate at 37 C for 15 min at "
            "800 rpm then hold for 20 min; WARNING hot; observe clear; repeat "
            "steps 1-1 until clear; volume is not specified."
        )
        write_pages(
            self.source,
            (
                "Protocol Evidence Preparation Before start: thaw sample. "
                "Material: buffer 10 mL 5%. Equipment: mixer 800 rpm.\n"
                + action,
            ),
        )
        self.extraction = extract_protocol_pdf(self.source)
        self.plan = plan_protocol_chunks(
            self.extraction,
            f"protocol-{self.extraction.sha256[:32]}",
            "pdf-1",
            limits=ChunkAnalysisLimits(max_concurrency=1, max_retries=0),
        )
        self.chunk = self.plan.chunks[0]
        self.scoped = extraction_for_chunk(self.extraction, self.chunk)
        self.request = prepare_chunk_claim_request_context(
            self.scoped,
            source_revision=self.chunk.candidate_revision_id,
            chunk_id=self.chunk.chunk_id,
            ordinal=self.chunk.ordinal,
            core_page_refs=self.chunk.core_page_refs,
            context_page_refs=self.chunk.overlap_page_refs,
        )
        self.raw = RichClaimModel().analyze(
            system_prompt="",
            input_json=self.request.input_json(),
            response_schema=claim_response_schema(self.request),
        )

    def validate(self, raw: str):
        return parse_chunk_claim_response(
            raw,
            extraction=self.scoped,
            source_revision=self.chunk.candidate_revision_id,
            chunk_id=self.chunk.chunk_id,
            core_page_refs=self.chunk.core_page_refs,
            request=self.request,
        )

    def key(self) -> ChunkCacheKey:
        return key_for_chunk(self.extraction, self.chunk)

    def load(self, cache: ChunkAnalysisCache, key: ChunkCacheKey | None = None):
        return cache.load(
            key or self.key(),
            extraction=self.scoped,
            request=self.request,
        )


class ChunkAnalysisCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.fixture = _Fixture(self.root)
        self.cache = ChunkAnalysisCache(self.root / "cache")
        # The payload really does pass the live path before it is stored.
        self.analysis = self.fixture.validate(self.fixture.raw)

    def tearDown(self) -> None:
        self.temp.cleanup()

    # --- 2-1: the answer survives the invocation ------------------------

    def test_a_stored_chunk_is_returned_on_a_later_read(self) -> None:
        self.assertIsNone(self.fixture.load(self.cache))
        self.cache.store(self.fixture.key(), self.fixture.raw)

        # A separate cache object, as a later invocation would build.
        later = ChunkAnalysisCache(self.root / "cache")
        hit = self.fixture.load(later)
        self.assertIsNotNone(hit)
        self.assertEqual(hit.analysis, self.analysis)

    # --- 2-2: the key names everything that could change the answer -----

    def test_the_key_covers_source_chunk_schema_prompt_and_segmentation(self):
        key = self.fixture.key()
        identity = key.identity()
        self.assertEqual(identity["source_sha256"], self.fixture.extraction.sha256)
        self.assertEqual(identity["chunk_id"], self.fixture.chunk.chunk_id)
        self.assertEqual(
            identity["core_page_refs"], list(self.fixture.chunk.core_page_refs)
        )
        self.assertEqual(identity["claim_schema_version"], CLAIM_SCHEMA_VERSION)
        self.assertEqual(
            identity["evidence_segment_version"], EVIDENCE_SEGMENT_VERSION
        )
        self.assertEqual(identity["prompt_sha256"], prompt_sha256())

        self.cache.store(key, self.fixture.raw)
        from dataclasses import replace

        for field, value in (
            ("source_sha256", "0" * 64),
            ("chunk_id", "chunk-something-else"),
            ("ordinal", key.ordinal + 1),
            ("core_page_refs", (99,)),
            ("context_page_refs", (98,)),
            ("source_revision", "pdf-2"),
            ("claim_schema_version", CLAIM_SCHEMA_VERSION + 1),
            ("evidence_segment_version", EVIDENCE_SEGMENT_VERSION + 1),
            ("prompt_sha256", "1" * 64),
        ):
            with self.subTest(changed=field):
                moved = replace(key, **{field: value})
                self.assertNotEqual(moved.digest(), key.digest())
                self.assertIsNone(
                    self.fixture.load(self.cache, moved),
                    f"{field} changed and a stale entry was still served",
                )

    def test_a_changed_segmentation_version_invalidates_every_entry(self) -> None:
        """Task 1's split, if it changes, must cost calls rather than lie.

        Segment boundaries decide evidence handles, so every cited id in a
        stored payload means something different afterwards. The entry is not
        migrated; it is simply not found.
        """

        from dataclasses import replace

        self.cache.store(self.fixture.key(), self.fixture.raw)
        self.assertIsNotNone(self.fixture.load(self.cache))
        future = replace(
            self.fixture.key(),
            evidence_segment_version=EVIDENCE_SEGMENT_VERSION + 1,
        )
        self.assertIsNone(self.fixture.load(self.cache, future))

    # --- 2-3: what comes out is checked again ---------------------------

    def test_a_cached_count_is_trusted_exactly_as_little_as_a_live_one(self):
        """Revalidation is the live path, so it is no stronger and no weaker.

        Editing a declared repetition count is *not* caught here, and that is
        the design rather than a hole: a count is the model's reading of the
        source, and STEP 18 decided it never executes on the model's word. The
        bound is held by ``unconfirmed_fixed_repetition``, which a reviewer
        must clear against the source. A cached count arrives under the same
        unconfirmed gate a live one does, so the cache changes what a call
        costs and nothing about what a claim is allowed to do.

        What revalidation does catch is anything the parser checks -- a range
        the cited evidence does not state, a handle that does not resolve --
        and those are asserted in the tests either side of this one.
        """

        path = self.cache.store(self.fixture.key(), self.fixture.raw)
        entry = json.loads(path.read_text())
        payload = json.loads(entry["claim_payload"])
        for claim in payload["claims"]:
            if claim.get("category") == "fixed_range_repetition":
                claim["repetition_count"] = 7
        entry["claim_payload"] = json.dumps(payload, sort_keys=True)
        path.write_text(json.dumps(entry))

        hit = self.fixture.load(self.cache)
        self.assertIsNotNone(hit)
        counts = {
            claim.repetition_count
            for claim in hit.analysis.claims
            if claim.category.value == "fixed_range_repetition"
        }
        self.assertEqual(counts, {7})

        # And that is safe because the readiness gate does not take its word.
        from voice_workflow_agent import experiment_protocol as domain
        self.assertIn(
            domain.ReadinessReasonCode.UNCONFIRMED_FIXED_REPETITION.value,
            {code for code in _ACKNOWLEDGEABLE_GATES} | {"unconfirmed_fixed_repetition"},
        )

    def test_an_edited_evidence_handle_is_refused(self) -> None:
        path = self.cache.store(self.fixture.key(), self.fixture.raw)
        entry = json.loads(path.read_text())
        payload = json.loads(entry["claim_payload"])
        forged = False
        for claim in payload["claims"]:
            handles = claim.get("evidence", {}).get("evidence_segment_ids")
            if handles:
                handles[0] = "s-" + "Z" * (len(handles[0]) - 2)
                forged = True
                break
        self.assertTrue(forged, "the fixture cited no evidence to forge")
        entry["claim_payload"] = json.dumps(payload, sort_keys=True)
        path.write_text(json.dumps(entry))

        self.assertIsNone(self.fixture.load(self.cache))
        self.assertFalse(path.exists())

    def test_an_edited_repeated_range_is_refused(self) -> None:
        path = self.cache.store(self.fixture.key(), self.fixture.raw)
        entry = json.loads(path.read_text())
        payload = json.loads(entry["claim_payload"])
        for claim in payload["claims"]:
            if claim.get("repeated_step_labels"):
                claim["repeated_step_labels"] = ["1", "9"]
        entry["claim_payload"] = json.dumps(payload, sort_keys=True)
        path.write_text(json.dumps(entry))

        self.assertIsNone(self.fixture.load(self.cache))
        self.assertFalse(path.exists())

    def test_an_entry_filed_under_a_different_identity_is_refused(self) -> None:
        path = self.cache.store(self.fixture.key(), self.fixture.raw)
        entry = json.loads(path.read_text())
        entry["identity"]["claim_schema_version"] = CLAIM_SCHEMA_VERSION + 1
        path.write_text(json.dumps(entry))

        self.assertIsNone(self.fixture.load(self.cache))
        self.assertFalse(path.exists())

    def test_a_malformed_or_foreign_file_is_a_miss(self) -> None:
        path = self.cache.path_for(self.fixture.key())
        path.parent.mkdir(parents=True, exist_ok=True)
        for body in ("not json", json.dumps({"claim_payload": "{}"}), "[]"):
            with self.subTest(body=body[:12]):
                path.write_text(body)
                self.assertIsNone(self.fixture.load(self.cache))

    # --- 2-4: what is written down --------------------------------------

    def test_only_the_canonical_claim_structure_is_written(self) -> None:
        spaced = json.dumps(json.loads(self.fixture.raw), indent=4)
        self.assertNotEqual(spaced, self.fixture.raw)
        path = self.cache.store(self.fixture.key(), spaced)
        entry = json.loads(path.read_text())

        self.assertEqual(set(entry), {"identity", "claim_payload"})
        stored = entry["claim_payload"]
        self.assertEqual(stored, canonical_claim_payload(self.fixture.raw))
        self.assertNotIn("\n", stored)
        # The provider's own formatting did not survive; the claims did.
        self.assertEqual(json.loads(stored), json.loads(self.fixture.raw))

        # The payload names handles and numbers and nothing else: the source
        # text belongs to the server, so caching a payload cannot put protocol
        # prose on disk even by accident.
        page = self.fixture.request.pages[0]
        for item in page.evidence:
            self.assertNotIn(item.segment.text.strip()[:40], stored)

    def test_a_completion_that_is_not_a_claim_object_is_never_stored(self) -> None:
        for body in ('"a string"', "[1, 2]", "null"):
            with self.subTest(body=body):
                with self.assertRaises(ChunkCacheError):
                    self.cache.store(self.fixture.key(), body)

    # --- 2-5: where it lives ---------------------------------------------

    def test_the_cache_root_is_configurable_and_defaults_outside_runtime(self):
        from unittest.mock import patch

        with patch.dict(
            {}, {}, clear=False
        ):  # placeholder to keep the import local
            pass
        import os

        with patch.dict(
            os.environ, {CACHE_DIRECTORY_ENV: str(self.root / "elsewhere")}
        ):
            cache = ChunkAnalysisCache()
        self.assertEqual(cache.root, self.root / "elsewhere")

        with patch.dict(os.environ, {CACHE_DIRECTORY_ENV: ""}):
            default = ChunkAnalysisCache()
        self.assertNotIn("runtime", str(default.root))
        self.assertIn("development_cache", str(default.root))

    def test_storing_writes_one_file_and_nothing_else(self) -> None:
        self.cache.store(self.fixture.key(), self.fixture.raw)
        self.assertEqual(len(self.cache.entries()), 1)
        # Storing the same key twice replaces rather than accumulates.
        self.cache.store(self.fixture.key(), self.fixture.raw)
        self.assertEqual(len(self.cache.entries()), 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


class CrossInvocationChunkByChunkTests(unittest.TestCase):
    """2-6 and 2-7: pay for one chunk at a time and still merge at the end.

    This is the arithmetic STEP 24 exists to change. Merge needs every chunk
    at once, so a three-chunk document could never be closed on a two-call
    budget, however many calls had already been paid for. Here each invocation
    spends at most one call, the cache alone carries results across, and the
    third invocation merges a set that is entirely cache.

    Nothing contacts a provider: the model is the strict offline fake the
    chunk tests use, which refuses a request whose schema or prompt is wrong.
    """

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "multi.pdf"
        page_texts = tuple(
            f"Protocol Large Section {number}\n{number}. Do action {number}. "
            + ("x" * 48)
            for number in range(1, 4)
        )
        write_pages(self.source, page_texts)
        self.extraction = extract_protocol_pdf(self.source)
        largest = max(
            len(page.text.encode("utf-8")) for page in self.extraction.pages
        )
        self.plan = plan_protocol_chunks(
            self.extraction,
            f"protocol-{self.extraction.sha256[:32]}",
            "pdf-1",
            limits=ChunkAnalysisLimits(
                max_pages=16,
                max_extracted_text_bytes=64 * 1024,
                max_chunks=16,
                max_chunk_text_bytes=largest + 4,
                max_chunk_result_bytes=512 * 1024,
                max_concurrency=1,
                timeout_seconds=2,
                max_retries=0,
                overlap_pages=0,
            ),
        )
        self.assertEqual(len(self.plan.chunks), 3)
        self.cache = ChunkAnalysisCache(self.root / "cache")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _context(self, chunk):
        scoped = extraction_for_chunk(self.extraction, chunk)
        request = prepare_chunk_claim_request_context(
            scoped,
            source_revision=chunk.candidate_revision_id,
            chunk_id=chunk.chunk_id,
            ordinal=chunk.ordinal,
            core_page_refs=chunk.core_page_refs,
            context_page_refs=chunk.overlap_page_refs,
        )
        return scoped, request

    def _one_invocation(self, ordinal, calls):
        """Spend at most one call; take anything already cached for free."""

        validated = {}
        for chunk in self.plan.chunks:
            scoped, request = self._context(chunk)
            hit = self.cache.load(
                key_for_chunk(self.extraction, chunk),
                extraction=scoped,
                request=request,
            )
            if hit is not None:
                validated[chunk.ordinal] = ("cache", hit.analysis)
                continue
            if chunk.ordinal != ordinal:
                continue
            raw = FakeChunkModel().analyze(
                system_prompt=CLAIM_ANALYSIS_SYSTEM_PROMPT,
                input_json=request.input_json(),
                response_schema=claim_response_schema(request),
            )
            calls.append(chunk.ordinal)
            analysis = parse_chunk_claim_response(
                raw,
                extraction=scoped,
                source_revision=chunk.candidate_revision_id,
                chunk_id=chunk.chunk_id,
                core_page_refs=chunk.core_page_refs,
                request=request,
            )
            self.cache.store(key_for_chunk(self.extraction, chunk), raw)
            validated[chunk.ordinal] = ("provider", analysis)
        return validated

    def test_one_chunk_per_invocation_still_reaches_every_chunk(self) -> None:
        calls: list[int] = []

        first = self._one_invocation(0, calls)
        self.assertEqual(calls, [0])
        self.assertEqual(sorted(first), [0])

        second = self._one_invocation(1, calls)
        self.assertEqual(calls, [0, 1])
        self.assertEqual(sorted(second), [0, 1])
        self.assertEqual(second[0][0], "cache")
        self.assertEqual(second[1][0], "provider")

        third = self._one_invocation(2, calls)
        self.assertEqual(calls, [0, 1, 2])
        self.assertEqual(sorted(third), [0, 1, 2])

        # A fourth invocation spends nothing: every chunk is already known.
        fourth = self._one_invocation(0, calls)
        self.assertEqual(calls, [0, 1, 2], "a cached chunk was paid for again")
        self.assertEqual(
            [source for source, _ in fourth.values()], ["cache"] * 3
        )

    def test_merge_accepts_a_set_that_is_part_cache_and_part_fresh(self) -> None:
        from voice_workflow_agent.protocol_chunk_analysis import (
            ValidatedChunkResult,
            merge_validated_chunk_results,
        )

        calls: list[int] = []
        self._one_invocation(0, calls)
        self._one_invocation(1, calls)
        results = self._one_invocation(2, calls)
        self.assertEqual(len(calls), 3)
        sources = {source for source, _ in results.values()}
        self.assertEqual(sources, {"cache", "provider"})

        merged = merge_validated_chunk_results(
            self.extraction,
            self.plan,
            tuple(
                ValidatedChunkResult(chunk, results[chunk.ordinal][1])
                for chunk in self.plan.chunks
            ),
        )
        self.assertTrue(merged.claims)
        self.assertEqual(
            len(merged.page_coverage), self.extraction.page_count
        )
