"""An item that waits for a person never runs as it is (lane RV, decisions a and c).

The measure the lane was asked for: no path takes an item marked "확인 필요"
into what a session runs, says or writes until a person has decided it.
Every way a protocol reaches a run is tried with a made-up sentence the
source does not print: the executable fixture, the fixture translations are
made from, a session start, a recovery by revision id, the start screen, a
registration with the item undecided, and -- once decided -- the version's
facts, timers and texts, where the made-up words are no longer found.
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from tests.test_lane_rv_review_api import STEP_TWO
from tests.test_lane_rv_review_pending import MADE_UP, _CatalogCase
from voiney_lab import server
from voiney_lab.protocol_catalog import (
    ProtocolCatalogError,
    ProtocolCatalogUnavailableError,
    ProtocolReviewItemsPendingError,
)

LINES_OF_STEP_TWO = {"page": 1, "first_line": 4, "last_line": 4}


def everything_a_session_reads(fixture) -> str:
    """Every text a session speaks, shows or times from, joined."""

    texts = [fixture.title]
    for index, step in enumerate(fixture.steps):
        texts += [fact.text for fact in fixture.facts_for_step(index)]
        texts.append(step.instruction_source_text)
    if fixture.timer_table is not None:
        texts += [timer.excerpt for timer in fixture.timer_table.verified]
    texts += [json.dumps(server.fixture_safety_notices(fixture), ensure_ascii=False)]
    return "\n".join(texts)


class NoPathToRunTests(_CatalogCase):
    def setUp(self) -> None:
        super().setUp()
        self.analyze(self.answer(step_two=MADE_UP, pages=(1, 2, 3)))

    def test_nothing_that_runs_reads_the_analysis_waiting_for_review(self) -> None:
        entry = self.catalog.get_entry(self.protocol_id)
        self.assertFalse(entry.available_for_execution)
        with self.assertRaises(ProtocolCatalogUnavailableError):
            self.catalog.load_executable_fixture(self.protocol_id)
        with self.assertRaises(ProtocolCatalogUnavailableError):
            self.catalog.load_analysis_fixture(self.protocol_id)
        for revision_id in ("pdf-1", "pdf-1-analysis-1", "pdf-1-review-1"):
            with self.subTest(revision_id=revision_id), self.assertRaises(ProtocolCatalogError):
                self.catalog.load_fixture_for_revision(self.protocol_id, revision_id)
        # A new run: no recovery, so the session start reads the entry, which
        # does not run. A recovery naming the revision without an analysis is
        # refused the same way.
        self.assertIsNone(server._recovery_fixture(self.catalog, self.protocol_id, entry, None))
        with patch.object(server, "_recovery_revision_id", return_value="pdf-1-review-1"):
            self.assertEqual(
                server._recovery_fixture(self.catalog, self.protocol_id, entry, "experiment-1"),
                (None, None, "protocol_selection_unavailable"))
        start_screen = self.catalog.review(self.protocol_id)
        self.assertNotIn(MADE_UP, json.dumps(start_screen, ensure_ascii=False))

    def test_no_version_while_the_item_is_undecided(self) -> None:
        self.catalog.record_review_decision(
            self.protocol_id, {"item_id": "step:s1", "action": "confirm"})
        self.catalog.record_review_decision(
            self.protocol_id, {"item_id": "timer:s4", "action": "fix", "seconds": 360})
        with self.assertRaises(ProtocolReviewItemsPendingError):
            self.catalog.register_review(self.protocol_id, {"reviewer_name": "김재준"})
        self.assertEqual(self.catalog.get_entry(self.protocol_id).review_versions, ())
        with self.assertRaises(ProtocolCatalogUnavailableError):
            self.catalog.load_executable_fixture(self.protocol_id)

    def test_decided_the_made_up_words_are_nowhere_in_the_run(self) -> None:
        self.catalog.record_review_decision(self.protocol_id, {
            "item_id": "step:s2", "action": "fix", "text": STEP_TWO,
            "source_lines": LINES_OF_STEP_TWO})
        self.catalog.register_review(self.protocol_id, {"reviewer_name": "김재준"})
        fixture = self.catalog.load_executable_fixture(self.protocol_id)
        read = everything_a_session_reads(fixture)
        self.assertNotIn(MADE_UP, read)
        self.assertIn(STEP_TWO, read)
        # The made-up words stay only where they belong: the record of what
        # the analysis read and what the reviewer changed.
        (change,) = fixture.draft.review_version.changes
        self.assertEqual(change.original, MADE_UP)

    def test_a_confirmed_suggestion_runs_only_with_the_lines_a_person_pointed_at(self) -> None:
        self.catalog.record_review_decision(self.protocol_id, {
            "item_id": "step:s2", "action": "confirm", "source_lines": LINES_OF_STEP_TWO})
        self.catalog.register_review(self.protocol_id, {"reviewer_name": "김재준"})
        step = self.catalog.load_executable_fixture(self.protocol_id).steps[1]
        # The AI's reading, confirmed by a person, quoted by the page's own line.
        self.assertEqual(step.instruction_source_text, MADE_UP)
        self.assertEqual(step.evidence.source_excerpt, STEP_TWO)


class NoPathForAFailedPieceTests(_CatalogCase):
    def test_a_failed_piece_s_pages_must_be_confirmed_before_a_version(self) -> None:
        from unittest.mock import Mock
        from voiney_lab.experiment_protocol_analysis import AnalysisPiece

        pieces = (AnalysisPiece(1, 2, 1, 2), AnalysisPiece(2, 2, 2, 3))
        broken = self.answer(pages=(2, 3)).replace('"steps":', '"stepz":', 1)
        answers = {1: [self.answer(pages=(1, 2))], 2: [broken, broken]}

        def answer(*, system_prompt, input_json, response_schema):
            first = json.loads(input_json)["pages"][0]["source_page_number"]
            return answers[first].pop(0)

        model = Mock()
        model.analyze.side_effect = answer
        self.catalog.request_analysis(self.protocol_id, "analysis-" + "p" * 32)
        with patch("voiney_lab.protocol_catalog.analysis_pieces", return_value=pieces):
            self.catalog.analyze(self.protocol_id, model, analysis_id="analysis-" + "p" * 32)
        draft = self.catalog.review_draft(self.protocol_id)
        self.assertEqual(draft["needs_review"], ["pages:2-3"])
        pages = next(item for item in draft["items"] if item["item_id"] == "pages:2-3")
        self.assertEqual(pages["allowed_actions"], ["confirm", "memo", "reset"])
        with self.assertRaises(ProtocolReviewItemsPendingError):
            self.catalog.register_review(self.protocol_id, {"reviewer_name": "김재준"})
        self.catalog.record_review_decision(
            self.protocol_id, {"item_id": "pages:2-3", "action": "confirm"})
        version = self.catalog.register_review(
            self.protocol_id, {"reviewer_name": "김재준"})["review_version"]
        (change,) = version["changes"]
        self.assertEqual((change["item_id"], change["action"], change["kind_ko"]),
                         ("pages:2-3", "confirm", "분석 실패 쪽"))


if __name__ == "__main__":
    unittest.main()
