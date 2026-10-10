"""The analysis prompt for a quote across a page end and for a repeat count (lane EV2, decision 5).

Human decision 2026-10-10: the prompt says to split a quote that runs onto the
next page into the continued_* fields, and to state how the source puts a
repeat count, together with decisions 1 and 3. For the first the provider is
now asked for ``continued_on_page_number`` and ``continued_excerpt``; what it
writes there is kept only when the two pieces are found joined across the page
end (lane PA's check), exactly as when the server split a joined quote itself.
Whether the sentences change what a model writes cannot be measured by replay:
that needs the next live measurement.
"""

from __future__ import annotations

import json
import unittest

from tests.test_lane_ev2_cross_page import (
    NOTE,
    NOTE_PAGE_1,
    NOTE_PAGE_2,
    THAW,
    note,
    parse,
    source,
    step,
)
from voiney_lab.experiment_protocol_analysis import (
    ANALYSIS_RESPONSE_SCHEMA,
    ANALYSIS_SYSTEM_PROMPT,
    ProtocolAnalysisEvidenceError,
    parse_protocol_analysis_response,
)
from tests.test_lane_ev2_cross_page import response


def prompt() -> str:
    return " ".join(ANALYSIS_SYSTEM_PROMPT.split())


class PromptSentencesTests(unittest.TestCase):
    def test_a_quote_across_a_page_end_is_split_into_the_continued_fields(self):
        self.assertIn(
            "When a passage you quote runs past the end of its page onto the next "
            "page, put the part on the cited page in source_excerpt and the part "
            "that opens the next page in continued_excerpt, with "
            "continued_on_page_number set to that next page's number.",
            prompt(),
        )
        self.assertIn(
            "Leave the running header, footer and page number out of both parts, "
            "and never continue onto a third page.",
            prompt(),
        )

    def test_every_fixed_repetition_states_how_the_source_puts_its_count(self):
        self.assertIn(
            "For every fixed_range_repetition set repeat_count_kind to how the "
            "source states the count:",
            prompt(),
        )
        for kind in ('"total"', '"additional"', '"ambiguous"'):
            self.assertIn(kind, prompt())
        self.assertIn("once more", prompt())
        self.assertIn("Repeat steps 36-38 twice", prompt())

    def test_the_earlier_sentences_stand(self):
        # Lane AQ's line-end sentence and the shortest-passage rule.
        self.assertIn("Never cut an excerpt at a line-end hyphen or inside a word", prompt())
        self.assertIn("Use the shortest exact contiguous passage", prompt())


class TheProviderMaySplitTests(unittest.TestCase):
    def test_the_schema_asks_for_the_continuation_and_still_not_for_handles(self):
        definition = ANALYSIS_RESPONSE_SCHEMA["$defs"]["SourceEvidence"]
        self.assertIn("continued_on_page_number", definition["properties"])
        self.assertIn("continued_excerpt", definition["properties"])
        self.assertNotIn("continued_excerpt", definition["required"])
        self.assertNotIn("evidence_segment_ids", definition["properties"])

    def split(self, first: str, page: int | None, second: str | None) -> dict:
        payload = step("1", THAW, THAW, notes=[note(NOTE, first)])
        evidence = payload["notes"][0]["evidence"]
        evidence["continued_on_page_number"] = page
        evidence["continued_excerpt"] = second
        return payload

    def test_a_split_the_provider_wrote_is_kept_when_it_continues_the_page(self):
        found = parse(self.split(NOTE_PAGE_1, 2, NOTE_PAGE_2)).protocol.sections[0].steps[0].notes[0]
        self.assertEqual(found.evidence.source_excerpt, NOTE_PAGE_1)
        self.assertEqual(found.evidence.continued_on_page_number, 2)
        self.assertEqual(found.evidence.continued_excerpt, NOTE_PAGE_2)

    def test_a_split_that_does_not_continue_the_page_is_refused(self):
        for first, page, second in (
            (NOTE_PAGE_1, 2, "Do not use whole cells."),          # not the next page's start
            (NOTE_PAGE_1, 3, NOTE_PAGE_2),                         # not the next page
            ("1. Thaw a frozen aliquot of nuclei at room temperature.", 2, NOTE_PAGE_2),  # not the page end
            (NOTE_PAGE_1, None, NOTE_PAGE_2),                     # half a continuation
            (NOTE_PAGE_1, 2, None),
        ):
            with self.subTest(first=first[:20], page=page, second=(second or "")[:20]):
                with self.assertRaises(ProtocolAnalysisEvidenceError):
                    parse(self.split(first, page, second))

    def test_a_split_must_stay_within_the_document(self):
        payload = response(step(
            "4", "Add 50 µl of antibody buffer.", "4. Add 50 µl of antibody buffer.", page=3))
        payload["protocol"]["sections"][0]["steps"][0]["evidence"].update(
            continued_on_page_number=4, continued_excerpt="more")
        with self.assertRaises(ProtocolAnalysisEvidenceError):
            parse_protocol_analysis_response(json.dumps(payload), source())


if __name__ == "__main__":
    unittest.main()
