"""Only a person may say a numbered line is not an execution step.

On the first real use of the provider-facing version of this judgement, a model
disposed of six numbered lines on headspace pages 6-8 -- labels 22, 30, 31, 32,
33, 34 -- every one an imperative carrying a temperature, a time or a piece of
equipment. Approved, the protocol would have been missing six steps and nothing
in the extraction would have said so.

The field is gone from the provider contract, so a model has no means to make
that judgement and therefore cannot make it wrongly. Every numbered line is an
execution step as far as extraction is concerned; treating a description as a
step only costs an operator hearing a description read out.

A reviewer may still record the finding, with the same provenance as any other
and revocable -- and it clears nothing, so it cannot become a route around the
obligation.
"""

from __future__ import annotations

import unittest

from voiney_lab import experiment_protocol as domain

_PAGE = (
    "Protocol Labels\nSection preparation\n1. Wash the pellet.\n"
    "2 Buffer contains sodium chloride.\n"
)
_GATE = domain.ReadinessReasonCode.NO_DECLARED_SAFETY_WARNINGS.value


class TheModelCannotMakeThisJudgementTests(unittest.TestCase):
    def test_the_response_schema_has_no_field_for_it(self) -> None:
        from voiney_lab.protocol_claim_analysis import (
            CLAIM_RESPONSE_SCHEMA,
        )

        coverage = CLAIM_RESPONSE_SCHEMA["properties"]["page_coverage"][
            "items"
        ]
        self.assertNotIn("non_step_labels", coverage["properties"])
        self.assertNotIn("non_step_labels", coverage["required"])

    def test_the_prompt_no_longer_asks_for_it(self) -> None:
        from voiney_lab.protocol_claim_analysis import (
            CLAIM_ANALYSIS_SYSTEM_PROMPT,
        )

        prompt = " ".join(CLAIM_ANALYSIS_SYSTEM_PROMPT.split())
        self.assertNotIn("non_step_labels", prompt)
        self.assertIn(
            "Every numbered label on a core page is an execution step", prompt
        )

    def test_the_protocol_carries_no_disposition_collection(self) -> None:
        from dataclasses import fields

        self.assertNotIn(
            "label_dispositions",
            [field.name for field in fields(domain.ExperimentProtocol)],
        )

    def test_no_readiness_gate_stands_over_an_empty_collection(self) -> None:
        self.assertFalse(
            hasattr(
                domain.ReadinessReasonCode, "UNCONFIRMED_LABEL_DISPOSITION"
            )
        )


if __name__ == "__main__":
    unittest.main()
