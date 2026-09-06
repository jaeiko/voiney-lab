"""A hazard is read out; it is not signed off.

STEP 13 established that a warning the model found does not stand in for a
person's review, and that stands. What changes here is narrower and is about
originals only: the system stops making a claim nobody could support.

The gate collected an acknowledgement meaning "safety review complete". On a
revision that is answerable -- somebody changed a procedure, and a person can
look at what they changed. On a document as registered there is nothing to
compare against, and the only thing a reviewer could confirm is that our
extraction found the hazards the PDF states, which is a claim about our own
reading offered by someone reading the same PDF. So the assertion goes, and
what replaces it is an obligation rather than a signature: the source's own
warning text is read out at the step it governs, on every run, and the step
does not begin until it has been.

The three things this file pins are the three ways it could go wrong:
originals must still surface the hazard, revisions must still be gated, and
no count of model-written warnings may open anything.
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

#: The hazard text is taken from the step's own evidence rather than invented.
#: A warning has to resolve against the source like any other statement -- that
#: is what makes the disclosure quotable -- so a made-up excerpt is refused by
#: validate_protocol before any of this is reached, which is correct.


def _draft():
    from tests.test_pdf_to_session_walkthrough import _pipeline

    return _pipeline()


class LineageDecidesTheGateTests(unittest.TestCase):
    """7-1 and 7-2: which Protocol is asked to pass a reviewer."""

    @classmethod
    def setUpClass(cls) -> None:
        if not IN_GEL.is_file():
            raise unittest.SkipTest(f"{IN_GEL} is not present.")
        cls.extraction, _plan, _merged, cls.draft = _draft()

    def _codes(self, lineage=None):
        arguments = {} if lineage is None else {"source_lineage": lineage}
        return domain.assess_readiness(self.draft.protocol, **arguments).reason_codes

    def test_a_revision_is_still_gated(self) -> None:
        self.assertIn(
            domain.ReadinessReasonCode.NO_DECLARED_SAFETY_WARNINGS.value,
            self._codes(domain.SourceLineage.REVISION),
        )

    def test_an_unstated_lineage_is_gated_exactly_like_a_revision(self) -> None:
        """Fail closed: saying nothing is not a way to skip a gate."""

        self.assertEqual(
            self._codes(),
            self._codes(domain.SourceLineage.REVISION),
        )
        self.assertEqual(
            self._codes(domain.SourceLineage.UNKNOWN),
            self._codes(domain.SourceLineage.REVISION),
        )

    def test_a_registered_document_is_not_asked_to_pass_it(self) -> None:
        original = self._codes(domain.SourceLineage.ORIGINAL_REGISTRATION)
        self.assertNotIn(
            domain.ReadinessReasonCode.NO_DECLARED_SAFETY_WARNINGS.value,
            original,
        )

    def test_nothing_else_about_readiness_moves(self) -> None:
        """Only that one gate. Everything else answers the same either way."""

        revision = list(self._codes(domain.SourceLineage.REVISION))
        original = list(self._codes(domain.SourceLineage.ORIGINAL_REGISTRATION))
        revision.remove(
            domain.ReadinessReasonCode.NO_DECLARED_SAFETY_WARNINGS.value
        )
        self.assertEqual(sorted(revision), sorted(original))
        # And it does not make the document ready. Four ambiguities and two
        # repeat-untils still stand in the way, which is the point: this
        # removes an unanswerable question, not a real one.
        self.assertEqual(
            domain.assess_readiness(
                self.draft.protocol,
                source_lineage=domain.SourceLineage.ORIGINAL_REGISTRATION,
            ).status,
            domain.ReadinessStatus.ANALYSIS_REQUIRED,
        )


class TheWarningIsReadOutTests(unittest.TestCase):
    """7-1: what the original owes instead, and that it binds."""

    @classmethod
    def setUpClass(cls) -> None:
        if not IN_GEL.is_file():
            raise unittest.SkipTest(f"{IN_GEL} is not present.")
        cls.extraction, _plan, _merged, cls.draft = _draft()

    def _session_with_a_warning_on_step(self, position=2):
        """Put a source-quoted hazard on one step and build a session on it."""

        sections = list(self.draft.protocol.sections)
        steps = list(sections[0].steps)
        target = steps[position]
        warning = domain.SourceStatement(
            statement_id=f"warning-{target.step_id}",
            source_text=target.evidence.source_excerpt,
            evidence=replace(target.evidence),
        )
        self.warning_text = target.evidence.source_excerpt
        steps[position] = replace(target, warnings=(warning,))
        sections[0] = replace(sections[0], steps=tuple(steps))
        protocol = replace(self.draft.protocol, sections=tuple(sections))
        draft = replace(self.draft, protocol=protocol)
        fixture = CuratedProtocolFixture(
            draft=draft,
            status="fictional_non_operational",
            ordered_step_labels=tuple(step.source_label for step in steps),
            fixture_sha256="0" * 64,
            revision_id="safety-disclosure-test",
            development_only=True,
            source_pdf_path=IN_GEL,
            source_filename=draft.extraction.original_filename,
        )
        session = CuratedProtocolSession(fixture)
        session.active = True
        return session, position, steps[position].step_id

    def test_the_step_will_not_begin_until_the_warning_has_been_read(self):
        session, index, step_id = self._session_with_a_warning_on_step()
        self.assertTrue(session.step_declares_safety_warnings(index))
        self.assertEqual(session.steps_awaiting_a_safety_disclosure(), (index,))
        self.assertFalse(session.may_begin_step(step_id))

        session.record_safety_warning_disclosure(
            index, actor_principal_id="operator-a", actor_role="researcher"
        )
        self.assertTrue(session.may_begin_step(step_id))
        self.assertEqual(session.steps_awaiting_a_safety_disclosure(), ())

    def test_the_disclosure_is_the_source_text_and_not_a_reading_of_it(self):
        session, index, _step_id = self._session_with_a_warning_on_step()
        disclosure = session.safety_warning_disclosure(index)
        self.assertIsNotNone(disclosure)
        self.assertEqual(len(disclosure["warnings"]), 1)
        self.assertEqual(
            disclosure["warnings"][0]["source_text"], self.warning_text
        )
        self.assertEqual(disclosure["warnings"][0]["source_page_number"], 4)
        self.assertIn("원문 그대로", disclosure["notice"])
        self.assertFalse(disclosure["disclosed"])

    def test_a_step_the_source_says_nothing_about_is_unaffected(self) -> None:
        session, index, _step_id = self._session_with_a_warning_on_step()
        other = 0 if index != 0 else 1
        self.assertFalse(session.step_declares_safety_warnings(other))
        self.assertIsNone(session.safety_warning_disclosure(other))
        self.assertTrue(
            session.may_begin_step(session.fixture.steps[other].step_id)
        )

    def test_a_new_run_owes_the_warning_again(self) -> None:
        """Per execution, not per protocol: different hands, same hazard."""

        session, index, step_id = self._session_with_a_warning_on_step()
        session.record_safety_warning_disclosure(
            index, actor_principal_id="operator-a", actor_role="researcher"
        )
        self.assertTrue(session.may_begin_step(step_id))
        session.reset()
        self.assertEqual(session.steps_awaiting_a_safety_disclosure(), (index,))
        self.assertFalse(session.may_begin_step(step_id))

    def test_a_disclosure_needs_a_step_that_has_one_and_a_person(self) -> None:
        session, index, _step_id = self._session_with_a_warning_on_step()
        other = 0 if index != 0 else 1
        with self.assertRaises(ValueError):
            session.record_safety_warning_disclosure(
                other, actor_principal_id="a", actor_role="researcher"
            )
        for actor, role in (("", "researcher"), ("a", ""), ("  ", "  ")):
            with self.subTest(actor=actor, role=role):
                with self.assertRaises(ValueError):
                    session.record_safety_warning_disclosure(
                        index, actor_principal_id=actor, actor_role=role
                    )

    def test_reading_a_warning_out_clears_no_gate_at_all(self) -> None:
        """7-3's third: it is a disclosure, not an approval."""

        session, index, _step_id = self._session_with_a_warning_on_step()
        before = domain.assess_readiness(
            session.fixture.draft.protocol,
            source_lineage=domain.SourceLineage.REVISION,
        )
        session.record_safety_warning_disclosure(
            index, actor_principal_id="operator-a", actor_role="researcher"
        )
        after = domain.assess_readiness(
            session.fixture.draft.protocol,
            source_lineage=domain.SourceLineage.REVISION,
        )
        self.assertEqual(before.reason_codes, after.reason_codes)
        self.assertIn(
            domain.ReadinessReasonCode.NO_DECLARED_SAFETY_WARNINGS.value,
            after.reason_codes,
        )
        from voice_workflow_agent.protocol_catalog import ProtocolCatalog

        self.assertNotIn(
            "safety_warning_disclosure",
            set(ProtocolCatalog._BLOCKER_RESOLUTION),
        )

    def test_a_count_of_model_written_warnings_opens_nothing(self) -> None:
        """7-3's third, from the other side: the count is still not authority."""

        session, index, _step_id = self._session_with_a_warning_on_step()
        protocol = session.fixture.draft.protocol
        self.assertEqual(domain.declared_safety_warning_count(protocol), 1)
        self.assertIn(
            domain.ReadinessReasonCode.NO_DECLARED_SAFETY_WARNINGS.value,
            domain.assess_readiness(
                protocol, source_lineage=domain.SourceLineage.REVISION
            ).reason_codes,
        )
        # And a zero count does not raise it on an original either -- the gate
        # is decided by lineage, never by how many warnings extraction found.
        self.assertEqual(
            domain.declared_safety_warning_count(self.draft.protocol), 0
        )
        self.assertNotIn(
            domain.ReadinessReasonCode.NO_DECLARED_SAFETY_WARNINGS.value,
            domain.assess_readiness(
                self.draft.protocol,
                source_lineage=domain.SourceLineage.ORIGINAL_REGISTRATION,
            ).reason_codes,
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
