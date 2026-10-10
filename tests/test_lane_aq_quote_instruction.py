"""The analysis prompt tells the model where a quote may end (lane AQ, decision 5).

Human decision of 2026-10-10. Lane EB (2026-10-09) found 12 source-evidence
refusals where the model had cut its excerpt at a line-end hyphen ("re-" /
"suspend") or inside a word; the word-boundary check then rightly refused
the quote. The prompt now says, in one sentence beside the "shortest exact
contiguous passage" rule, to quote through to where the word ends, taking in
the next line when the word continues there. The evidence checks themselves
are unchanged: a quote that still ends inside a word is still refused.
"""

from __future__ import annotations

import unittest

from voiney_lab.experiment_protocol_analysis import ANALYSIS_SYSTEM_PROMPT


class QuotingInstructionTests(unittest.TestCase):
    def test_the_prompt_says_not_to_cut_an_excerpt_at_a_line_end_hyphen_or_inside_a_word(self) -> None:
        normalized = " ".join(ANALYSIS_SYSTEM_PROMPT.split())
        self.assertIn("line-end hyphen", normalized)
        self.assertIn("inside a word", normalized)
        self.assertIn("where the word ends", normalized)
        self.assertIn("next line", normalized)

    def test_it_is_one_sentence_beside_the_shortest_passage_rule(self) -> None:
        normalized = " ".join(ANALYSIS_SYSTEM_PROMPT.split())
        sentence = next(s for s in normalized.split(". ") if "line-end hyphen" in s)
        self.assertLess(len(sentence), 240)
        self.assertLess(
            normalized.index("shortest exact contiguous passage"),
            normalized.index("line-end hyphen"))
        # The rule it sits beside still reads as the earlier tests pin it.
        self.assertIn(
            "only source-layout whitespace that the downstream validator normalizes may differ",
            normalized.casefold())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
