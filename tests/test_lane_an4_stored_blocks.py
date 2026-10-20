"""A stored analysis's page blocks are read as text blocks again (lane AN4, found while measuring).

The protocol store keeps a ``PdfTextBlock`` (a NamedTuple) as a plain tuple,
so the pages of a stored analysis come back with blocks that have no
``.text``. The review draft of a passed analysis checks its step timers
against those pages (``verify_step_timers``); a timer whose sentence runs past
a page end reaches the page-end rule, which read ``block.text`` and raised --
``GET /api/protocols/<id>/review-draft`` answered 500 for nine of this lane's
live documents that had passed. The page-end rule now reads such a tuple as
the block it was; nothing about the rule changes.
"""

from __future__ import annotations

import unittest

from tests.test_lane_ev2_cross_page import NOTE, source
from voiney_lab.experiment_protocol_analysis import _page_end_span, _statement_across_page_end
from voiney_lab.experiment_protocol_store import _decode_domain, _encode_domain
from voiney_lab.pdf_text_engine import PdfTextBlock


class StoredBlocksTests(unittest.TestCase):
    def test_the_store_gives_blocks_back_as_plain_tuples(self) -> None:
        stored = _decode_domain(_encode_domain(source()))
        block = stored.pages[0].blocks[0]
        self.assertNotIsInstance(block, PdfTextBlock)
        self.assertEqual(tuple(block), tuple(source().pages[0].blocks[0]))

    def test_the_page_end_rule_reads_a_stored_page_as_the_extracted_one(self) -> None:
        extraction = source()
        stored = _decode_domain(_encode_domain(extraction))
        for page_index in range(extraction.page_count):
            self.assertEqual(
                _page_end_span(stored.pages[page_index], stored),
                _page_end_span(extraction.pages[page_index], extraction))

    def test_a_statement_across_the_page_end_is_found_on_stored_pages(self) -> None:
        extraction = source()
        stored = _decode_domain(_encode_domain(extraction))
        found = _statement_across_page_end(NOTE, extraction, 1)
        self.assertIsNotNone(found)
        self.assertEqual(_statement_across_page_end(NOTE, stored, 1), found)


if __name__ == "__main__":
    unittest.main()
