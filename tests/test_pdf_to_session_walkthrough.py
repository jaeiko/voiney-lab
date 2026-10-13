"""A stored in-gel analysis may run, and the repeats still hold at the bench.

What is left of the one-PDF walkthrough: the offline claim-chunk pipeline it
drove -- planning, the claim model, merge and assembly -- was deleted on
2026-10-13 (lane AN, decision 1; the git history keeps it). The later stages
(store, catalog verdict, executable fixture, session) are driven with the
curated in-gel draft instead.

The operational store is never opened: the catalog lives in a temporary
directory and the source PDF is only read.
"""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from tests.protocol_vocabulary_support import in_gel_fixture
from voiney_lab import experiment_protocol as domain
from voiney_lab.curated_protocol import CuratedProtocolSession
from voiney_lab.experiment_protocol_store import (
    ProtocolPersistenceSettings,
    initialize_protocol_store,
)
from voiney_lab.protocol_catalog import ProtocolCatalog

IN_GEL = Path("data/runtime/candidate-a-source/in-gel-digestion.pdf")


class TheLoopStopsAtExecutionReadinessTests(unittest.TestCase):
    """Stages 7 to 10, in an isolated store."""

    def setUp(self) -> None:
        if not IN_GEL.is_file():
            self.skipTest(f"{IN_GEL} is not present.")
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        root = Path(self._temp.name)
        self.draft = in_gel_fixture().draft
        self.store = initialize_protocol_store(
            ProtocolPersistenceSettings(True, root / "catalog")
        )
        self.addCleanup(self.store.close)
        self.catalog = ProtocolCatalog(self.store)
        registration = self.catalog.register(
            IN_GEL,
            source_filename=IN_GEL.name,
            media_type="application/pdf",
        )
        self.protocol_id = registration.entry.protocol_id
        protocol = domain.validate_protocol(
            replace(self.draft.protocol, protocol_id=self.protocol_id)
        )
        self.store.append_analysis_revision(
            self.protocol_id,
            1,
            "analysis-walkthrough",
            protocol,
            domain.assess_readiness(protocol),
            self.draft.capability_policy.profile_id,
        )
        self.revision_id = "pdf-1-analysis-1"

    def test_the_assembled_analysis_may_run_under_the_mvp_rule(self) -> None:
        """Stages 7 to 9 under the rule of 2026-10-08 (lane DI).

        The assembled analysis carries the safety reason and the ambiguities
        the hand-built fixture carries too. Neither is an execution blocker
        any more: both are notices the experimenter reads before pressing
        start, so the catalog loads the executable fixture without any
        approval, finding or activation. What the rule does *not* do is
        release the repeat steps: 7, 9 and 20 are still refused at the bench
        until the experimenter reports the endpoint the document states.
        """

        entry = self.catalog.get_entry(self.protocol_id)
        review = self.catalog.review(self.protocol_id)
        self.assertTrue(entry.available_for_execution)
        self.assertEqual(entry.execution_blocker_codes, ())
        self.assertEqual(review["execution_blockers"], [])
        self.assertEqual(
            sorted({item["code"] for item in review["execution_notices"]}),
            [
                domain.ReadinessReasonCode.NO_DECLARED_SAFETY_WARNINGS.value,
                domain.ReadinessReasonCode.UNRESOLVED_AMBIGUITY.value,
            ],
        )
        self.assertEqual(
            {event.event_type for event in self.store.list_events(self.protocol_id)},
            {"protocol_registered"},
        )
        fixture = self.catalog.load_executable_fixture(self.protocol_id)
        self.assertEqual(fixture.status, "analysis_passed")

        # Selectable, and still gated where the source states a repeat.
        session = CuratedProtocolSession(fixture)
        session.active = True
        session._workflow_status = "active"
        anchors = [
            index
            for index, step in enumerate(fixture.steps)
            if session.endpoint_observation_outstanding(index)
        ]
        self.assertTrue(anchors)
        for index in anchors:
            session.current_index = index
            self.assertTrue(session.endpoint_observation_outstanding(index))


if __name__ == "__main__":
    unittest.main()
