"""A refusal diagnostic names the segments it is about.

What is left of the chunk-analysis cache tests: the cache, the claim request
and the claim schema were deleted on 2026-10-13 (lane AN, decision 1; the git
history keeps them). ``ProtocolEvidenceDiagnostic`` stays in
``experiment_protocol_analysis``, and its public form still carries the
offending segment ids when a refusal has them.
"""

from __future__ import annotations

import unittest


class EchoedIdentitiesArePinnedTests(unittest.TestCase):
    """The diagnostic half of the old class; its schema half went with the cache."""

    def test_a_refusal_names_the_segments_it_is_about(self) -> None:
        """STEP 25 had to re-derive by hand which segment a refusal meant."""

        from voiney_lab.experiment_protocol_analysis import (
            ProtocolEvidenceDiagnostic,
        )

        diagnostic = ProtocolEvidenceDiagnostic(
            validation_stage="chunk_page_coverage_validation",
            reason_code="declined_segment_states_a_value",
            mismatch_class="segment_accounting_mismatch",
            offending_segment_ids=("seg-b", "seg-a"),
        )
        self.assertEqual(
            diagnostic.public_dict()["offending_segment_ids"],
            ["seg-b", "seg-a"],
        )
        self.assertNotIn(
            "offending_segment_ids",
            ProtocolEvidenceDiagnostic(
                validation_stage="s", reason_code="r", mismatch_class="m"
            ).public_dict(),
        )


if __name__ == "__main__":
    unittest.main()
