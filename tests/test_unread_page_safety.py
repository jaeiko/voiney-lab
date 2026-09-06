"""A page the machine did not finish reading never passes for a read one.

STEP 28 stopped discarding a document because a page was incompletely read,
which was right: refusing the merge threw away twenty-five correctly read
instructions to punish eighteen unaccounted segments. It also opened a way for
a Protocol to reach an experimenter with a page the system had not finished,
and STEP 28 did not close it. This is that closure.

The rule is narrow and it is about who says what. The system may not announce
that such a step is complete, because completion is the one claim it must
never make on its own authority about text it did not read. The experimenter
still can -- they are standing in front of the bench with the page in hand --
and saying so out loud is enough, because the hands this is written for are in
gloves. The acknowledgement is an experiment-session fact and is deliberately
kept out of the approval ledger: it clears no readiness gate and settles no
reviewer finding.
"""

from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from pathlib import Path

from voice_workflow_agent import experiment_protocol as domain
from voice_workflow_agent.curated_protocol import (
    CuratedProtocolFixture,
    CuratedProtocolSession,
)

ROOT = Path(__file__).resolve().parents[1]
IN_GEL = ROOT / "data" / "runtime" / "candidate-a-source" / "in-gel-digestion.pdf"
sys.path.insert(0, str(ROOT / "scripts"))


def _draft():
    from tests.test_pdf_to_session_walkthrough import _pipeline

    return _pipeline()


class AssemblyStillReachesTheEndTests(unittest.TestCase):
    """Task 3: find every remaining gate without spending a call.

    A contract-satisfying response is built offline and driven all the way to
    an assembled Protocol and then to the accuracy scorer. The point is not the
    score -- the input is synthetic, so the number means nothing -- but that
    the path has no gate left on it.
    """

    @classmethod
    def setUpClass(cls) -> None:
        if not IN_GEL.is_file():
            raise unittest.SkipTest(f"{IN_GEL} is not present.")
        cls.extraction, cls.plan, cls.merged, cls.draft = _draft()

    def test_a_contract_satisfying_response_assembles(self) -> None:
        steps = [
            step
            for section in self.draft.protocol.sections
            for step in section.steps
        ]
        self.assertEqual(len(steps), 25)
        self.assertEqual(
            len(self.merged.page_coverage), self.extraction.page_count
        )

    def test_the_scorer_can_read_an_assembled_protocol(self) -> None:
        """Path only. The candidate is synthetic, so the number is not a score."""

        from voice_workflow_agent.protocol_extraction_accuracy import (
            audit_reference,
            score_extraction,
        )

        report = score_extraction(
            self.draft.protocol,
            self.draft.protocol,
            reference_notes=audit_reference(self.draft.protocol, self.extraction),
        )
        self.assertEqual(report.reference_steps, 25)
        self.assertEqual(report.candidate_steps, 25)
        self.assertTrue(report.order_matches)
        published = report.public_dict()
        self.assertIn("steps_unscorable_on_values", published)

    def test_incomplete_pages_survive_the_merge_addressably(self) -> None:
        """Task 3-4: the marking has to reach the assembled record."""

        statuses = {
            item.source_page_number: item.status.value
            for item in self.merged.page_coverage
        }
        self.assertEqual(set(statuses), set(range(1, self.extraction.page_count + 1)))
        for item in self.merged.page_coverage:
            if item.status.value == "analysis_incomplete":
                # Marked *and* addressable: the ids are how the text is found.
                self.assertTrue(item.unaccounted_segment_ids)


class UnreadPageAtExecutionTests(unittest.TestCase):
    """Task 1: what the agent does when it reaches such a step."""

    @classmethod
    def setUpClass(cls) -> None:
        if not IN_GEL.is_file():
            raise unittest.SkipTest(f"{IN_GEL} is not present.")
        cls.extraction, _plan, _merged, cls.draft = _draft()

    def _fixture(self, unread=None):
        labels = tuple(
            step.source_label
            for section in self.draft.protocol.sections
            for step in section.steps
        )
        return CuratedProtocolFixture(
            draft=self.draft,
            status="fictional_non_operational",
            ordered_step_labels=labels,
            fixture_sha256="0" * 64,
            revision_id="unread-page-test",
            development_only=True,
            source_pdf_path=IN_GEL,
            source_filename=self.draft.extraction.original_filename,
            unread_pages=unread,
        )

    def _session(self, unread=None):
        session = CuratedProtocolSession(self._fixture(unread))
        session.active = True
        return session

    def _first_step_on(self, session, page):
        for index, step in enumerate(session.fixture.steps):
            if step.evidence.source_page_number == page:
                return index
        raise AssertionError(f"no step cites page {page}")

    # --- 1-4: a fully read page behaves exactly as before -----------------

    def test_a_fully_read_document_is_unchanged(self) -> None:
        session = self._session(None)
        self.assertEqual(session.unread_pages_awaiting_acknowledgement(), ())
        for index, step in enumerate(session.fixture.steps):
            self.assertFalse(session.step_is_on_an_unread_page(index))
            self.assertIsNone(session.unread_page_disclosure(index))
            self.assertTrue(session.may_report_step_complete(index))
            self.assertTrue(session.may_begin_step(step.step_id))

    def test_a_page_that_was_read_stays_unaffected_by_one_that_was_not(self):
        session = self._session({5: ()})
        clean = self._first_step_on(session, 4)
        self.assertFalse(session.step_is_on_an_unread_page(clean))
        self.assertTrue(session.may_report_step_complete(clean))
        self.assertTrue(
            session.may_begin_step(session.fixture.steps[clean].step_id)
        )

    # --- 1-1: what the experimenter is told -------------------------------

    def test_the_disclosure_names_the_page_and_quotes_it_verbatim(self) -> None:
        session = self._session({5: ()})
        index = self._first_step_on(session, 5)
        self.assertTrue(session.step_is_on_an_unread_page(index))
        disclosure = session.unread_page_disclosure(index)
        self.assertEqual(disclosure["source_page_number"], 5)
        self.assertEqual(disclosure["reading_status"], "incomplete")
        self.assertIn("완전히 읽지 못했습니다", disclosure["notice"])
        # The page's own words, not a reading of them.
        self.assertEqual(
            disclosure["source_page_text"], self.extraction.pages[4].text
        )
        self.assertFalse(disclosure["acknowledged"])

    def test_the_omitted_segments_are_quoted_too(self) -> None:
        from voice_workflow_agent.protocol_claim_analysis import (
            generate_page_evidence_segments,
        )

        segments = generate_page_evidence_segments(
            self.extraction, source_revision="unread-page-test", page_number=5
        )
        chosen = (segments[1].segment_id, segments[3].segment_id)
        session = self._session({5: chosen})
        index = self._first_step_on(session, 5)
        omitted = session.unread_page_disclosure(index)["unaccounted_segments"]
        self.assertEqual(len(omitted), 2)
        self.assertEqual(
            [item["source_text"] for item in omitted],
            [segments[1].text, segments[3].text],
        )

    # --- 1-1 and 1-2: completion, and clearing it by voice ----------------

    def test_the_agent_will_not_call_such_a_step_complete(self) -> None:
        session = self._session({5: ()})
        index = self._first_step_on(session, 5)
        self.assertFalse(session.may_report_step_complete(index))
        self.assertFalse(
            session.may_begin_step(session.fixture.steps[index].step_id)
        )
        self.assertEqual(session.unread_pages_awaiting_acknowledgement(), (5,))

    def test_an_acknowledgement_releases_only_that_page(self) -> None:
        session = self._session({5: (), 6: ()})
        five = self._first_step_on(session, 5)
        six = self._first_step_on(session, 6)
        session.acknowledge_unread_page(
            5, actor_principal_id="operator-a", actor_role="researcher"
        )
        self.assertTrue(session.may_report_step_complete(five))
        self.assertFalse(session.may_report_step_complete(six))
        self.assertEqual(session.unread_pages_awaiting_acknowledgement(), (6,))
        self.assertTrue(
            session.unread_page_disclosure(five)["acknowledged"]
        )

    def test_an_acknowledgement_needs_a_page_and_a_person(self) -> None:
        session = self._session({5: ()})
        for page in (4, "5", None, True):
            with self.subTest(page=page):
                with self.assertRaises(ValueError):
                    session.acknowledge_unread_page(
                        page, actor_principal_id="a", actor_role="researcher"
                    )
        for actor, role in (("", "researcher"), ("a", ""), ("  ", "  ")):
            with self.subTest(actor=actor, role=role):
                with self.assertRaises(ValueError):
                    session.acknowledge_unread_page(
                        5, actor_principal_id=actor, actor_role=role
                    )

    # --- 1-3: it makes no safety state true -------------------------------

    def test_acknowledging_a_page_clears_no_readiness_gate(self) -> None:
        session = self._session({5: ()})
        before = domain.assess_readiness(session.fixture.draft.protocol)
        session.acknowledge_unread_page(
            5, actor_principal_id="operator-a", actor_role="researcher"
        )
        after = domain.assess_readiness(session.fixture.draft.protocol)
        self.assertEqual(before.status, after.status)
        self.assertEqual(before.reason_codes, after.reason_codes)
        self.assertIn(
            domain.ReadinessReasonCode.NO_DECLARED_SAFETY_WARNINGS.value,
            after.reason_codes,
        )

    def test_an_unread_page_is_not_a_readiness_reason_at_all(self) -> None:
        """Reading status is the pipeline's business, not the reviewer's."""

        codes = {code.value for code in domain.ReadinessReasonCode}
        self.assertEqual(
            [c for c in codes if "unread" in c or "coverage" in c], []
        )

    def test_the_acknowledgement_is_not_a_reviewer_finding(self) -> None:
        from voice_workflow_agent.protocol_catalog import ProtocolCatalog

        self.assertNotIn(
            "unread_page", set(ProtocolCatalog._BLOCKER_RESOLUTION)
        )
        session = self._session({5: ()})
        session.acknowledge_unread_page(
            5, actor_principal_id="operator-a", actor_role="researcher"
        )
        # It lives on the session and nowhere else.
        self.assertTrue(session._acknowledged_unread_pages)
        session.reset()
        self.assertEqual(session.unread_pages_awaiting_acknowledgement(), (5,))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
