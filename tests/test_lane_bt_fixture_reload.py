"""The development fixture loads again over a store an older version wrote (lane BT).

From 2026-10-08 the development server did not start. ``scripts/run_dev.sh``
loads the curated fixture before it serves, and the load refused:
``DuplicateProtocolIdentifierError("Protocol event identifier already has
different content.")``, after which ``set -euo pipefail`` never reached
uvicorn.

The store was not corrupt. Its ``development_fixture_materialized`` row for
this fixture and this analysis was written on 2026-10-06 by code that still
put ``"final_approval": false`` in the event payload. Lane DI (2026-10-08)
removed approval, and with it that key. The event id named the fixture and
the analysis but not the event's own content, so the next load computed the
same id for a payload that was no longer the same, and the store, correctly,
refused to treat the two as one event. Every other column of the row matched.

The rows below are written the way that older code wrote them -- the old id
format and the old payload -- on synthetic PDFs, never the licensed source or
anything from ``data/runtime``. A load over them must leave them exactly as
they are and add what the current code says, as a new event.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from voiney_lab import experiment_protocol as domain
from voiney_lab.curated_protocol import CuratedProtocolFixture
from voiney_lab.experiment_protocol_config import ProtocolPersistenceSettings
from voiney_lab.experiment_protocol_store import initialize_protocol_store
from voiney_lab.protocol_catalog import ProtocolCatalog

from tests.aged_store import curated_fixture_for, draft_for, edited_fixture
from tests.test_protocol_catalog import write_text_pdf

EVENT_TYPE = "development_fixture_materialized"


def _payload_as_written_before_lane_di(
    fixture: CuratedProtocolFixture,
) -> dict[str, object]:
    """The event payload as the code before 2026-10-08 wrote it.

    Spelled out key by key rather than derived from the current payload, so
    that it stays the shape the stored rows actually have whatever the current
    payload becomes.
    """

    return {
        "protocol_id": fixture.protocol_id,
        "revision_id": fixture.revision_id,
        "fixture_sha256": fixture.fixture_sha256,
        "source_sha256": fixture.source_pdf_sha256,
        "status": fixture.status,
        "development_only": True,
        "final_approval": False,
    }


def _analysis_changed(
    fixture: CuratedProtocolFixture, profile_id: str
) -> CuratedProtocolFixture:
    """The same fixture bytes whose analysis now says something else.

    Readiness and the capability policy are decided when the fixture is
    loaded, not by its bytes, so the analysis can change under an unchanged
    fixture hash; the policy's id is the smallest such change.
    """

    return replace(
        fixture,
        draft=replace(
            fixture.draft,
            capability_policy=domain.CapabilityPolicy(
                profile_id, fixture.draft.capability_policy.supported_features
            ),
        ),
    )


class _FixtureStoreCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.settings = ProtocolPersistenceSettings(True, self.root / "catalog")
        self.store = initialize_protocol_store(self.settings)
        self.catalog = ProtocolCatalog(self.store)
        source_pdf = self.root / "source.pdf"
        write_text_pdf(
            source_pdf,
            "Protocol Test\nSection preparation\n1. Add solution.\nWear gloves.",
            title="Protocol Test",
        )
        self.protocol_id = "lane-bt-development-fixture-v1"
        self.fixture = curated_fixture_for(
            draft_for(source_pdf, self.protocol_id),
            marker=b"lane-bt-fixture",
            source_pdf=source_pdf,
        )

    def tearDown(self) -> None:
        self.store.close()
        self._temp.cleanup()

    def _write_as_before_lane_di(self, fixture: CuratedProtocolFixture) -> str:
        """Materialize ``fixture`` exactly as the code of 2026-10-06 did."""

        analysis_id, payload_sha256 = (
            ProtocolCatalog._development_analysis_identity(fixture)
        )
        analysis = self.store.create_experiment_with_analysis(
            fixture.protocol_id,
            fixture.source_pdf_path,
            analysis_id,
            fixture.draft.protocol,
            fixture.draft.readiness,
            fixture.draft.capability_policy_id,
        )
        event_id = (
            f"development-fixture-{fixture.fixture_sha256[:48]}"
            f"-{payload_sha256[:16]}"
        )
        self.store.append_event(
            event_id,
            fixture.protocol_id,
            1,
            EVENT_TYPE,
            _payload_as_written_before_lane_di(fixture),
            analysis_revision_number=analysis.analysis_revision_number,
        )
        return event_id

    def _rows(self) -> dict[str, list[tuple]]:
        """Every stored row of the three tables a load writes, as stored."""

        connection = sqlite3.connect(
            self.settings.data_dir / "protocol_workspace.sqlite"
        )
        try:
            return {
                table: connection.execute(
                    f"SELECT * FROM {table} ORDER BY rowid"
                ).fetchall()
                for table in (
                    "protocol_revisions",
                    "analysis_revisions",
                    "protocol_events",
                )
            }
        finally:
            connection.close()

    def _fixture_events(self):
        return [
            event
            for event in self.store.list_events(self.protocol_id)
            if event.event_type == EVENT_TYPE
        ]

    def assertKeptAndAppended(self, before: dict, after: dict, *, events: int):
        """Every earlier row is still there, unchanged; only events grew."""

        for table, rows in before.items():
            self.assertEqual(after[table][: len(rows)], rows, table)
        self.assertEqual(after["protocol_revisions"], before["protocol_revisions"])
        self.assertEqual(
            len(after["protocol_events"]),
            len(before["protocol_events"]) + events,
        )


class LoadOverRowsAnOlderVersionWroteTests(_FixtureStoreCase):
    def test_a_the_store_as_it_stood_loads_and_keeps_its_rows(self) -> None:
        """The reproduction: older fixtures, then this one, all pre-lane-DI."""

        older = edited_fixture(self.fixture, marker=b"lane-bt-older-fixture")
        self._write_as_before_lane_di(older)
        old_event_id = self._write_as_before_lane_di(self.fixture)
        before = self._rows()

        # Until lane BT: DuplicateProtocolIdentifierError.
        loaded = self.catalog.bootstrap_development_fixture(self.fixture)

        self.assertTrue(loaded.deduplicated)
        self.assertKeptAndAppended(before, self._rows(), events=1)
        self.assertEqual(
            len(self.store.list_analysis_revisions(self.protocol_id, 1)), 2
        )
        old, new = [
            event
            for event in self._fixture_events()
            if event.analysis_revision_number == 2
        ]
        self.assertEqual(old.event_id, old_event_id)
        self.assertFalse(old.payload["final_approval"])
        self.assertNotEqual(new.event_id, old_event_id)
        self.assertEqual(
            new.payload, ProtocolCatalog._development_fixture_payload(self.fixture)
        )
        self.assertTrue(
            self.catalog.development_fixture_is_materialized(self.fixture)
        )
        self.assertFalse(self.catalog.development_fixture_is_materialized(older))

    def test_b_a_changed_analysis_after_the_old_rows_is_appended(self) -> None:
        self._write_as_before_lane_di(self.fixture)
        changed = _analysis_changed(self.fixture, "p1-lane-bt-changed")
        self.assertEqual(changed.fixture_sha256, self.fixture.fixture_sha256)
        before = self._rows()

        self.catalog.bootstrap_development_fixture(changed)

        after = self._rows()
        self.assertKeptAndAppended(before, after, events=1)
        self.assertEqual(
            len(after["analysis_revisions"]), len(before["analysis_revisions"]) + 1
        )
        self.assertTrue(self.catalog.development_fixture_is_materialized(changed))
        self.assertFalse(
            self.catalog.development_fixture_is_materialized(self.fixture),
            "the analysis the catalog no longer serves must not claim to be it",
        )

    def test_c_an_analysis_that_goes_a_b_a_never_collides(self) -> None:
        """Back to A: no conflict, nothing rewritten, and A fails closed.

        Analysis A already has its revision, so going back to it appends no
        analysis; the catalog keeps serving B, the latest. A fixture that
        disagrees with what the catalog serves is not runnable -- that
        fixture only, and the load itself does not fail.
        """

        self._write_as_before_lane_di(self.fixture)
        changed = _analysis_changed(self.fixture, "p1-lane-bt-changed")
        self.catalog.bootstrap_development_fixture(changed)
        before = self._rows()

        again = self.catalog.bootstrap_development_fixture(self.fixture)
        self.catalog.bootstrap_development_fixture(self.fixture)

        self.assertTrue(again.deduplicated)
        self.assertKeptAndAppended(before, self._rows(), events=1)
        self.assertEqual(
            len(self.store.list_analysis_revisions(self.protocol_id, 1)), 2
        )
        back_to_a = self._fixture_events()[-1]
        self.assertEqual(back_to_a.analysis_revision_number, 1)
        self.assertEqual(
            back_to_a.payload,
            ProtocolCatalog._development_fixture_payload(self.fixture),
        )
        self.assertFalse(
            self.catalog.development_fixture_is_materialized(self.fixture)
        )
        self.assertTrue(self.catalog.development_fixture_is_materialized(changed))

        # And forward to B again, as after going back to a branch.
        self.catalog.bootstrap_development_fixture(changed)
        self.assertTrue(self.catalog.development_fixture_is_materialized(changed))

    def test_d_the_same_fixture_twice_writes_once(self) -> None:
        with self.subTest(store="fresh"):
            first = self.catalog.bootstrap_development_fixture(self.fixture)
            rows = self._rows()
            second = self.catalog.bootstrap_development_fixture(self.fixture)
            self.assertFalse(first.deduplicated)
            self.assertTrue(second.deduplicated)
            self.assertEqual(self._rows(), rows)
            self.assertEqual(len(self._fixture_events()), 1)

        with self.subTest(store="written before lane DI"):
            older = edited_fixture(self.fixture, marker=b"lane-bt-older-again")
            self._write_as_before_lane_di(older)
            self.catalog.bootstrap_development_fixture(older)
            rows = self._rows()
            self.catalog.bootstrap_development_fixture(older)
            self.catalog.bootstrap_development_fixture(older)
            self.assertEqual(self._rows(), rows)
            self.assertTrue(self.catalog.development_fixture_is_materialized(older))


if __name__ == "__main__":
    unittest.main()
