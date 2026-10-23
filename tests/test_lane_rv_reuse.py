"""A registered review version is reused, not uploaded again (lane RV, decision d).

Human decision 2026-10-12: the protocol list shows the review versions (v1,
v2, date, reviewer); uploading the same PDF again suggests the existing
review version; analysing again makes a new analysis -- a new item for review
when it waits for one -- while the existing review version keeps running.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from tests.test_lane_rv_review_api import STEP_TWO
from tests.test_lane_rv_review_pending import MADE_UP, MISSED_PAGES_2_3, _CatalogCase
from voiney_lab import server
from voiney_lab.protocol_catalog import review_version_suggestion

LINES_OF_STEP_TWO = {"page": 1, "first_line": 4, "last_line": 4}


class ReuseTests(_CatalogCase):
    def setUp(self) -> None:
        super().setUp()
        self.analyze(self.answer(step_two=MADE_UP))
        self.catalog.record_review_decision(self.protocol_id, {
            "item_id": "step:s2", "action": "confirm", "source_lines": LINES_OF_STEP_TWO})
        self.confirm_possibly_missed()  # lane MISS: its other cards
        self.catalog.register_review(self.protocol_id, {"reviewer_name": "김재준"})

    def test_the_list_shows_the_versions_with_date_and_reviewer(self) -> None:
        (row,) = [entry.public_dict() for entry in self.catalog.list_entries()]
        (version,) = row["review_versions"]
        self.assertEqual(
            (version["version"], version["version_ko"], version["reviewer_name"],
             version["revision_id"]),
            (1, "검토본 v1", "김재준", "pdf-1-review-1"))
        self.assertRegex(version["registered_on"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertEqual((version["change_count"], version["confirmed_count"]), (0, 1))
        self.assertEqual(row["current_review_version"], version)

    def test_uploading_the_same_pdf_again_suggests_the_review_version(self) -> None:
        registration = self.catalog.register(
            self.pdf, source_filename="gamma-again.pdf", media_type="application/pdf")
        self.assertTrue(registration.deduplicated)
        response = server._registration_response(registration, {"state": "not_required"})
        suggestion = response["review_suggestion"]
        self.assertEqual(suggestion["revision_id"], "pdf-1-review-1")
        self.assertIn("다시 올리지 않고 검토본 v1(", suggestion["message_ko"])
        self.assertIn(" · 김재준)로 바로 시작할 수 있습니다.", suggestion["message_ko"])
        # A new file suggests nothing.
        fresh = SimpleNamespace(entry=registration.entry, deduplicated=False)
        self.assertNotIn("review_suggestion", server._registration_response(fresh, {}))

    def test_without_a_version_an_upload_suggests_nothing(self) -> None:
        entry = SimpleNamespace(review_versions=())
        self.assertIsNone(review_version_suggestion(entry))

    def test_analysing_again_makes_a_new_review_while_the_version_runs(self) -> None:
        self.analyze(self.answer(step_two="2. Incubate for 1O min at 37 C."),
                     analysis_id="analysis-" + "b" * 32, reanalysis=True)
        entry = self.catalog.get_entry(self.protocol_id)
        self.assertTrue(entry.available_for_execution)
        self.assertEqual(entry.revision_id, "pdf-1-review-1")
        # Lane MISS: and pages 2-3's steps, which this answer leaves out.
        self.assertEqual(entry.review_pending["needs_review_count"], 3)
        self.assertEqual(
            self.catalog.load_executable_fixture(self.protocol_id).revision_id, "pdf-1-review-1")
        draft = self.catalog.review_draft(self.protocol_id)
        self.assertEqual(draft["target"]["kind"], "review_pending")
        self.assertEqual(draft["needs_review"], ["step:s2", *MISSED_PAGES_2_3])
        self.catalog.record_review_decision(self.protocol_id, {
            "item_id": "step:s2", "action": "fix", "text": STEP_TWO,
            "source_lines": LINES_OF_STEP_TWO})
        self.confirm_possibly_missed()
        registered = self.catalog.register_review(self.protocol_id, {"reviewer_name": "최수진"})
        self.assertEqual(registered["review_version"]["revision_id"], "pdf-1-review-2")
        self.assertEqual(self.catalog.get_entry(self.protocol_id).revision_id, "pdf-1-review-2")

    def test_an_analysis_that_passes_after_the_version_does_not_replace_it(self) -> None:
        # The version holds a person's decisions; a newer analysis runs only
        # once it is reviewed and registered as the next version.
        self.analyze(self.answer(), analysis_id="analysis-" + "c" * 32, reanalysis=True)
        entry = self.catalog.get_entry(self.protocol_id)
        self.assertEqual(entry.revision_id, "pdf-1-review-1")
        self.assertEqual(self.catalog.review_draft(self.protocol_id)["target"]["kind"], "analysis")
        self.catalog.register_review(self.protocol_id, {"reviewer_name": "최수진"})
        self.assertEqual(self.catalog.get_entry(self.protocol_id).revision_id, "pdf-1-review-2")


if __name__ == "__main__":
    unittest.main()
