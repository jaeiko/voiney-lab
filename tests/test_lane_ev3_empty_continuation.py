"""A second page named with an empty second piece is no second piece (lane EV3, decision 3).

Human decision 2026-10-12. At "low" reasoning Gemini set
``continued_on_page_number`` and left ``continued_excerpt`` an empty string,
putting the whole quote -- the next page's part too -- in ``source_excerpt``
(lane M3: PMC8250384 p.7->8, fda_eam_4_7 p.10->11). The server read the empty
string as a recorded second piece and refused it. Now a second page whose
piece is empty or only whitespace is read as "no second piece": both fields
are left out and the quote is judged by lane EV2's decision-1 rule for one
quote that runs across the page end (or as a quote on its own page). The
source-evidence and boundary rules are unchanged. The prompt and the schema
the provider is sent say to leave both fields empty when there is no second
piece.
"""

from __future__ import annotations

import hashlib
import json
import unittest

from tests.test_lane_ev2_cross_page import (
    NOTE,
    NOTE_PAGE_1,
    NOTE_PAGE_2,
    THAW,
    first_note,
    note,
    parse,
    step,
)
from voiney_lab import experiment_protocol_analysis as analysis_module
from voiney_lab.curated_protocol import _CANONICAL_SCHEMA_SHA256, _canonical_json_bytes
from voiney_lab.experiment_protocol_analysis import (
    ANALYSIS_PROVIDER_RESPONSE_SCHEMA,
    ANALYSIS_RESPONSE_SCHEMA,
    ANALYSIS_SYSTEM_PROMPT,
    ProtocolAnalysisEvidenceError,
)

#: The SHA-256 the curated development fixture pins for the validated
#: contract: unchanged by this lane.
PINNED_SCHEMA_SHA256 = "2533d564c54332d84ef3b7fdadb0d0010110fb0130a6772d907738a7482e07c9"


def split_note(quote: str, page: int | None, second: str | None) -> dict:
    payload = step("1", THAW, THAW, notes=[note(NOTE, quote)])
    evidence = payload["notes"][0]["evidence"]
    evidence["continued_on_page_number"] = page
    evidence["continued_excerpt"] = second
    return payload


class AnEmptySecondPieceIsNoSecondPieceTests(unittest.TestCase):
    def test_the_whole_quote_with_an_empty_second_piece_is_split_onto_its_two_pages(self):
        for empty in ("", " ", " \n\t"):
            with self.subTest(empty=repr(empty)):
                found = first_note(parse(split_note(f"{NOTE_PAGE_1} {NOTE_PAGE_2}", 2, empty)))
                self.assertEqual(found.evidence.source_page_number, 1)
                self.assertEqual(found.evidence.source_excerpt, NOTE_PAGE_1)
                self.assertEqual(found.evidence.continued_on_page_number, 2)
                self.assertEqual(found.evidence.continued_excerpt, NOTE_PAGE_2)

    def test_a_quote_on_its_own_page_with_an_empty_second_piece_keeps_no_second_page(self):
        payload = step("1", THAW, THAW)
        payload["evidence"].update(continued_on_page_number=2, continued_excerpt="")
        found = parse(payload).protocol.sections[0].steps[0].evidence
        self.assertEqual(found.source_excerpt, THAW)
        self.assertIsNone(found.continued_on_page_number)
        self.assertIsNone(found.continued_excerpt)

    def test_an_empty_second_piece_does_not_excuse_a_quote_that_is_not_on_the_page(self):
        for quote in (
            f"{NOTE_PAGE_1} Do not use whole cells.",       # skips text of page 2
            "1. Thaw a frozen aliquot of nuclei at room temperature. "
            f"{NOTE_PAGE_2}",                                # not the end of page 1
            f"{NOTE_PAGE_1} prepared as previously desc",    # stops inside a word
        ):
            with self.subTest(quote=quote[-30:]):
                with self.assertRaises(ProtocolAnalysisEvidenceError):
                    parse(split_note(quote, 2, ""))

    def test_a_named_second_piece_is_still_checked_as_before(self):
        for first, page, second in (
            (NOTE_PAGE_1, 2, "Do not use whole cells."),     # not the next page's start
            (NOTE_PAGE_1, None, NOTE_PAGE_2),                 # a piece without its page
            (NOTE_PAGE_1, 2, None),                           # a page without its piece
            (NOTE_PAGE_1, 3, NOTE_PAGE_2),                    # not the next page
        ):
            with self.subTest(page=page, second=(second or "")[:20]):
                with self.assertRaises(ProtocolAnalysisEvidenceError):
                    parse(split_note(first, page, second))


def prompt() -> str:
    return " ".join(ANALYSIS_SYSTEM_PROMPT.split())


class ThePromptAndTheSchemaSayItTests(unittest.TestCase):
    def test_the_prompt_says_to_leave_both_fields_empty(self):
        self.assertIn(
            "When the quote ends on its own page, leave both continued_on_page_number "
            "and continued_excerpt null; never name a next page without its part.",
            prompt(),
        )

    def test_the_schema_sent_to_the_provider_describes_both_fields(self):
        properties = ANALYSIS_PROVIDER_RESPONSE_SCHEMA["$defs"]["SourceEvidence"]["properties"]
        for name in ("continued_on_page_number", "continued_excerpt"):
            with self.subTest(name=name):
                self.assertIn("null", properties[name]["description"])
                self.assertIn("no second", properties[name]["description"])
        # Only the descriptions differ: the shape the provider is asked for
        # is the validated contract.
        stripped = json.loads(json.dumps(ANALYSIS_PROVIDER_RESPONSE_SCHEMA))
        for name in ("continued_on_page_number", "continued_excerpt"):
            del stripped["$defs"]["SourceEvidence"]["properties"][name]["description"]
        self.assertEqual(stripped, ANALYSIS_RESPONSE_SCHEMA)

    def test_the_validated_contract_and_its_pinned_identity_are_unchanged(self):
        definition = ANALYSIS_RESPONSE_SCHEMA["$defs"]["SourceEvidence"]["properties"]
        self.assertNotIn("description", definition["continued_excerpt"])
        canonical = _canonical_json_bytes(ANALYSIS_RESPONSE_SCHEMA)[:-1]
        self.assertEqual(hashlib.sha256(canonical).hexdigest(), PINNED_SCHEMA_SHA256)
        self.assertEqual(_CANONICAL_SCHEMA_SHA256, PINNED_SCHEMA_SHA256)

    def test_an_analysis_sends_the_described_schema(self):
        sent = {}

        class Model:
            def analyze(self, *, system_prompt, input_json, response_schema):
                sent["schema"] = response_schema
                raise analysis_module.ProtocolAnalysisModelError("stop here")

        from tests.test_lane_ev2_cross_page import source
        with self.assertRaises(analysis_module.ProtocolAnalysisModelError):
            analysis_module.analyze_protocol_extraction(source(), Model())
        self.assertIs(sent["schema"], ANALYSIS_PROVIDER_RESPONSE_SCHEMA)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
