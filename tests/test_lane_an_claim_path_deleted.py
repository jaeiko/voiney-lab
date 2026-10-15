"""The old claim-chunk path is gone (lane AN, decision 1).

Human decision 2026-10-13. The evidence-first claim-chunk path behind the
switch ``PROTOCOL_CLAIM_CHUNKS_ENABLED`` (default off; a deleted setting is
named here without its prefix, which the setting-name check keeps for the
table) never ran in the pilot: in lane AQ Gemini refused its claim schema
with HTTP 400, and a 17-page document took 16 sequential calls, past its
120 s bound. It was deleted with its settings and its tests; the git history
keeps it. What stays of ``protocol_claim_analysis`` is the page segment
generator that the in-gel fixture's timer manifest and the unread-page
disclosure still cite, with unchanged segment ids.
"""

from __future__ import annotations

import importlib.util
import unittest
from unittest import mock

from voiney_lab.experiment_protocol_pdf import (
    ProtocolPdfExtraction,
    ProtocolPdfMetadata,
    ProtocolPdfPage,
)
from voiney_lab.setting_names import BY_NAME
from voiney_lab.setting_renames import NEW_PREFIX, RENAMED

DELETED_MODULES = (
    "protocol_chunk_analysis",
    "chunk_analysis_cache",
    "claim_contract_audit",
    "protocol_claim_semantic_audit",
    "protocol_claim_stream_telemetry",
    "protocol_provider_diagnostics",
)
DELETED_SETTINGS = (
    NEW_PREFIX + "PROTOCOL_CLAIM_CHUNKS_ENABLED",
    NEW_PREFIX + "CHUNK_CACHE_DIR",
)
OLD_SWITCH = DELETED_SETTINGS[0]


def extraction(page_count: int) -> ProtocolPdfExtraction:
    return ProtocolPdfExtraction(
        original_filename="ten.pdf", byte_size=1, sha256="b" * 64,
        media_type="application/pdf", page_count=page_count, encrypted=False,
        metadata=ProtocolPdfMetadata(None, None, None, None, None, None, None),
        pages=tuple(
            ProtocolPdfPage(n, f"Page {n}\n{n}. Add {n} mL of buffer to the tube.", False)
            for n in range(1, page_count + 1)
        ),
    )


class TheClaimChunkPathIsGoneTests(unittest.TestCase):
    def test_its_modules_are_gone(self):
        for name in DELETED_MODULES:
            with self.subTest(module=name):
                self.assertIsNone(importlib.util.find_spec(f"voiney_lab.{name}"))

    def test_only_the_page_segment_generator_is_left_of_the_claim_module(self):
        from voiney_lab import protocol_claim_analysis as module

        public = {
            name for name, value in vars(module).items()
            if not name.startswith("_")
            and getattr(value, "__module__", module.__name__) == module.__name__
            and not isinstance(value, type(module))
            and name != "annotations"
        }
        self.assertEqual(
            public,
            {"EVIDENCE_SEGMENT_VERSION", "ProtocolEvidenceSegment",
             "generate_page_evidence_segments"},
        )
        self.assertEqual(module.EVIDENCE_SEGMENT_VERSION, 6)

    def test_its_settings_are_gone_from_the_table_and_the_rename_map(self):
        for name in DELETED_SETTINGS:
            with self.subTest(setting=name):
                self.assertNotIn(name, BY_NAME)
                self.assertNotIn(name, set(RENAMED.values()))

    def test_the_old_switch_no_longer_sends_a_document_elsewhere(self):
        from voiney_lab import protocol_catalog

        self.assertFalse(hasattr(protocol_catalog, "CLAIM_CHUNK_ANALYSIS_ENABLED_ENV"))
        # Ten small pages: with the switch on, more than eight pages used to
        # go to the claim-chunk path ("chunked_analysis_required").
        with mock.patch.dict("os.environ", {OLD_SWITCH: "true"}):
            self.assertEqual(
                protocol_catalog._analysis_state(extraction(10)),
                "structured_analysis_ready",
            )
        # A value the old switch refused is no longer read either.
        with mock.patch.dict("os.environ", {OLD_SWITCH: "maybe"}):
            self.assertEqual(
                protocol_catalog._analysis_state(extraction(10)),
                "structured_analysis_ready",
            )

    def test_the_catalog_has_no_claim_run(self):
        from voiney_lab.protocol_catalog import ProtocolCatalog

        for name in ("_analyze_chunked", "cancel_analysis_run", "_append_chunk_event"):
            with self.subTest(name=name):
                self.assertFalse(hasattr(ProtocolCatalog, name))

if __name__ == "__main__":
    unittest.main()
