"""An omitted value keeps a Protocol out of execution.

Two ways to lose a value the source states, and until now only one of them
cost anything. Declining a segment that states a value inside a numbered step
is refused outright and the whole chunk goes -- ``declined_segment_states_a_value``.
Saying nothing about the same segment raised nothing at all.

The comment beside that refusal claimed the silence was caught later, that an
incomplete page "blocks the whole-document merge just as firmly". STEP 28
removed that veto and left the sentence behind, so for six steps the cheapest
way to lose a value was to not mention it. STEP 28's replacement -- telling the
experimenter which page the machine had not finished -- was built and never
fed: the only assignment to ``unread_pages`` in the tree was in a test.

This is the other half, and it is deliberately not symmetric in severity. An
omission is a silence, not a false statement, so the chunk survives and so do
the claims that were right. What it costs is execution: the Protocol does not
run until a person has read the pages where a value went unaccounted.

The derivation from the merge's page coverage, and the catalog wiring that fed
it, were deleted with the evidence-first claim-chunk path on 2026-10-13 (lane
AN, decision 1; the git history keeps them). What is left here are the
readiness reasons themselves, given to a Protocol directly.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from voiney_lab import experiment_protocol as domain


class TheGateItselfTests(unittest.TestCase):
    """The readiness reason, in isolation from any document."""

    def _protocol(self):
        return _synthetic_protocol(self)

    def test_no_unaccounted_value_raises_nothing(self) -> None:
        codes = domain.assess_readiness(self._protocol()).reason_codes
        self.assertNotIn(
            domain.ReadinessReasonCode.SOURCE_PAGE_NOT_FULLY_READ.value, codes
        )

    def test_an_unaccounted_value_blocks_and_names_the_page_count(self) -> None:
        assessment = domain.assess_readiness(
            self._protocol(), pages_stating_unaccounted_values=(8,)
        )
        self.assertIn(
            domain.ReadinessReasonCode.SOURCE_PAGE_NOT_FULLY_READ.value,
            assessment.reason_codes,
        )
        self.assertEqual(
            assessment.status, domain.ReadinessStatus.ANALYSIS_REQUIRED
        )
        reason = next(
            item
            for item in assessment.reasons
            if item.code
            is domain.ReadinessReasonCode.SOURCE_PAGE_NOT_FULLY_READ
        )
        self.assertIn("1 source page(s)", reason.message)

    def test_the_page_list_is_deduplicated_and_junk_is_ignored(self) -> None:
        """Fail closed on shape: a bad entry must not become a bad page."""

        assessment = domain.assess_readiness(
            self._protocol(),
            pages_stating_unaccounted_values=(8, 8, True, "9", None, 3),
        )
        reason = next(
            item
            for item in assessment.reasons
            if item.code
            is domain.ReadinessReasonCode.SOURCE_PAGE_NOT_FULLY_READ
        )
        self.assertIn("2 source page(s)", reason.message)

    def test_it_is_a_notice_the_experimenter_reads_before_the_start(self) -> None:
        # Lane DI (2026-10-08): not an execution blocker. The page's own text
        # is shown at the step (the session's unread-page disclosure).
        code = domain.ReadinessReasonCode.SOURCE_PAGE_NOT_FULLY_READ
        self.assertNotIn(code, domain.EXECUTION_BLOCKING_REASON_CODES)


class ADeclinedValueBlocksWithoutDiscardingTests(unittest.TestCase):
    """STEP 35: being right about a heading no longer costs a provider call.

    ``declined_segment_states_a_value`` discarded the whole chunk. Measured on
    in-gel that cost two of five chunks over one segment each -- "Reduction and
    alkylation of cysteines 1h", a section heading with its own time estimate,
    and a column of four durations -- and took with them four correct claims
    including a repetition over steps 2-7 that had been derived correctly.

    Safety is "not executed before a person has looked". Both designs achieve
    it. Only one of them also throws away the evidence.
    """

    def test_a_declined_value_inside_a_step_blocks_execution(self) -> None:
        assessment = domain.assess_readiness(
            _synthetic_protocol(self), pages_declining_stated_values=(5,)
        )
        self.assertIn(
            domain.ReadinessReasonCode.DECLINED_VALUE_NOT_RESOLVED.value,
            assessment.reason_codes,
        )
        self.assertEqual(
            assessment.status, domain.ReadinessStatus.ANALYSIS_REQUIRED
        )

    def test_it_is_a_separate_reason_from_an_omission(self) -> None:
        """A silence and a judgement are different faults and read differently."""

        both = domain.assess_readiness(
            _synthetic_protocol(self),
            pages_stating_unaccounted_values=(8,),
            pages_declining_stated_values=(5,),
        )
        self.assertIn(
            domain.ReadinessReasonCode.SOURCE_PAGE_NOT_FULLY_READ.value,
            both.reason_codes,
        )
        self.assertIn(
            domain.ReadinessReasonCode.DECLINED_VALUE_NOT_RESOLVED.value,
            both.reason_codes,
        )

    def test_exceeding_the_allowance_signals_and_does_not_destroy(self) -> None:
        crowded = domain.assess_readiness(
            _synthetic_protocol(self), pages_declining_excessive_values=(7,)
        )
        self.assertIn(
            domain.ReadinessReasonCode.EXCESSIVE_DECLINED_VALUES.value,
            crowded.reason_codes,
        )
        self.assertEqual(
            crowded.status, domain.ReadinessStatus.ANALYSIS_REQUIRED
        )

    def test_both_new_reasons_are_notices_under_the_mvp_rule(self) -> None:
        for code in (
            domain.ReadinessReasonCode.DECLINED_VALUE_NOT_RESOLVED,
            domain.ReadinessReasonCode.EXCESSIVE_DECLINED_VALUES,
        ):
            with self.subTest(code=code.value):
                self.assertNotIn(code, domain.EXECUTION_BLOCKING_REASON_CODES)


def _synthetic_protocol(case):
    """A valid one-step Protocol; the readiness reasons are given to it."""

    import tempfile

    from tests.test_protocol_catalog import analysis_draft, write_text_pdf

    temp = tempfile.TemporaryDirectory()
    case.addCleanup(temp.cleanup)
    pdf = Path(temp.name) / "alpha.pdf"
    write_text_pdf(
        pdf,
        "Protocol Alpha\nSection preparation\n1. Add solution.\nWear gloves.",
        title="Protocol Alpha",
    )
    return analysis_draft(pdf, "protocol-alpha", "Protocol Alpha").protocol


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
