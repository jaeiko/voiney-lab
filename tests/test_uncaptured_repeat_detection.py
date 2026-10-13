"""A repeat the source states and the analysis missed is told before the start.

in-gel prints three repeat instructions and the claim analysis carried one.
Nothing knew the other two were gone: the assembled Protocol had twenty-five
steps in order and looked finished, so an agent would have walked them once
and said so.

The detector that read those instructions out of the document and compared
them with the claim analysis was deleted with the evidence-first claim-chunk
path on 2026-10-13 (lane AN, decision 1; the git history keeps it). What
stays is the readiness reason it fed: ``assess_readiness`` still records
``source_states_an_uncaptured_repetition`` for the ranges it is given, and
under the MVP rule the reason is a notice the experimenter reads before
starting.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.test_protocol_catalog import analysis_draft, write_text_pdf
from voiney_lab import experiment_protocol as domain

#: The two in-gel ranges the claim analysis did not carry (pages 6 and 8).
_MISSED = (("8", "9"), ("17", "18"))


class WhatTheAnalysisMissedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        with tempfile.TemporaryDirectory() as temp:
            pdf = Path(temp) / "alpha.pdf"
            write_text_pdf(
                pdf,
                "Protocol Alpha\nSection preparation\n1. Add solution.\n"
                "Wear gloves.",
                title="Protocol Alpha",
            )
            cls.protocol = analysis_draft(
                pdf, "protocol-alpha", "Protocol Alpha"
            ).protocol

    def test_it_is_recorded_and_told_to_the_experimenter_before_the_start(self) -> None:
        code = domain.ReadinessReasonCode.SOURCE_STATES_AN_UNCAPTURED_REPETITION
        readiness = domain.assess_readiness(
            self.protocol, uncaptured_repeat_instructions=_MISSED
        )
        self.assertIn(code.value, readiness.reason_codes)
        self.assertIs(readiness.status, domain.ReadinessStatus.ANALYSIS_REQUIRED)
        # Lane DI (2026-10-08): a notice, not an execution blocker. The
        # experimenter reads it before starting and the step's source is
        # read out as it stands.
        self.assertNotIn(code, domain.EXECUTION_BLOCKING_REASON_CODES)
        self.assertIn(
            code, {r.code for r in domain.execution_notice_reasons(readiness)}
        )

    def test_a_complete_analysis_raises_nothing(self) -> None:
        """The gate must be silent when every stated repeat is carried."""

        assessment = domain.assess_readiness(
            self.protocol, uncaptured_repeat_instructions=()
        )
        self.assertNotIn(
            domain.ReadinessReasonCode.SOURCE_STATES_AN_UNCAPTURED_REPETITION.value,
            assessment.reason_codes,
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
