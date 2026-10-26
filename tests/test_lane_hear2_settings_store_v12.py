"""Lane HEAR2, decision 6 (2026-10-26): the settings table at workspace schema 12.

The person's test of 2026-10-26: "목소리 맞추기" failed every one of ten
times. The server answered HTTP 500 to every ``POST /api/voice/calibration``.
Lane HEAR had added the two experimenter settings the measurement writes --
``speaker_filter`` (the person's choice) and ``voice_reference`` (the
measured level, when, and the microphone) -- to ``EXPERIMENTER_SETTING_VALUES``
but wrote no migration: a workspace store's ``experimenter_settings`` table
still carried schema 11's ``CHECK(name IN (...))`` without them, so the
INSERT failed with an IntegrityError the route turned into a 500. Lane HEAR's
tests ran with the workspace off (the server's memory) and never met it.

Schema 12 builds the table again with the ten names and keeps everything
else of it, as schema 11 did: every row as it was, its sequence ids, the
AUTOINCREMENT counter, the index and both append-only triggers.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from tests.identity_support import development_principal
from voiney_lab import workspace_store as ws
from voiney_lab.workspace_store import (
    EXPERIMENTER_SETTING_VALUES,
    WORKSPACE_SCHEMA_VERSION,
    WorkspaceError,
    WorkspaceSettings,
    initialize_workspace_store,
)

OLD_ROWS = (
    (1, "tenant-a", "principal-a", "confirm_mode", "quiet", "voice", "2026-10-08T01:00:00+00:00"),
    (2, "tenant-a", "principal-b", "agent_voice", "ko-KR-Chirp3-HD-Kore", "screen", "2026-10-18T02:00:00+00:00"),
    (5, "tenant-a", "principal-a", "voice_calibration", "measured", "voice", "2026-10-25T03:00:00+00:00"),
)
SETTINGS_V11 = (
    "confirm_mode", "question_timing", "web_lookup", "proactive_mode",
    "ambient_mode", "speaker_sensitivity", "agent_voice", "voice_calibration",
)


def _objects(connection: sqlite3.Connection, kind: str) -> dict[str, str]:
    return {
        str(name): str(sql)
        for name, sql in connection.execute(
            "SELECT name,sql FROM sqlite_master WHERE type=? AND tbl_name='experimenter_settings'",
            (kind,),
        )
    }


def _schema_11_store(path: Path) -> None:
    """A store exactly as schema 11 left it (what the pilot server ran on 2026-10-26)."""

    connection = sqlite3.connect(path)
    connection.executescript(ws.SCHEMA)
    for migration in (
        ws.MIGRATION_1_TO_2, ws.MIGRATION_2_TO_3, ws.MIGRATION_3_TO_4, ws.MIGRATION_4_TO_5,
        ws.MIGRATION_5_TO_6, ws.MIGRATION_6_TO_7, ws.MIGRATION_7_TO_8, ws.MIGRATION_8_TO_9,
        ws.MIGRATION_9_TO_10, ws.MIGRATION_10_TO_11,
    ):
        connection.executescript("BEGIN IMMEDIATE;\n" + migration + "\nCOMMIT;")
    connection.execute("INSERT INTO organizations VALUES('tenant-a','Lab A','2026-10-08T00:00:00+00:00')")
    for principal in ("principal-a", "principal-b"):
        connection.execute(
            "INSERT INTO principals VALUES(?,?,?,?)",
            (principal, f"dev:{principal}", principal, "2026-10-08T00:00:00+00:00"))
    connection.executemany(
        "INSERT INTO experimenter_settings(sequence_id,organization_id,principal_id,name,value,source,created_at)"
        " VALUES(?,?,?,?,?,?,?)", OLD_ROWS)
    connection.commit()
    assert connection.execute("SELECT schema_version FROM schema_metadata").fetchone()[0] == 11
    # The failure the person met: schema 11 refuses the measurement's row.
    with_check = connection.execute(
        "SELECT sql FROM sqlite_master WHERE name='experimenter_settings'").fetchone()[0]
    assert "'voice_reference'" not in with_check
    connection.close()


class SchemaTwelveTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.folder = Path(self._temp.name)
        self.path = self.folder / ws.WORKSPACE_DATABASE_FILENAME

    def test_the_version_is_twelve_and_the_values_table_knows_the_two_names(self) -> None:
        # Lane KO2 (2026-10-26) raised the schema to 13 (translation style
        # versions); the settings table is schema 12's.
        self.assertEqual(WORKSPACE_SCHEMA_VERSION, 13)
        self.assertEqual(EXPERIMENTER_SETTING_VALUES["speaker_filter"], ("off", "on"))
        self.assertIsNone(EXPERIMENTER_SETTING_VALUES["voice_reference"], "the server checks the reference's form")

    def test_a_schema_11_store_refused_the_measurement_before_this_lane(self) -> None:
        _schema_11_store(self.path)
        connection = sqlite3.connect(self.path)
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO experimenter_settings(organization_id,principal_id,name,value,source,created_at)"
                " VALUES('tenant-a','principal-a','voice_reference','v1:-23.4:1792991234:mic-1','screen','2026-10-26T03:05:00+00:00')")
        connection.close()

    def test_a_schema_11_store_migrates_keeping_rows_ids_counter_index_and_triggers(self) -> None:
        _schema_11_store(self.path)
        before = sqlite3.connect(self.path)
        old_triggers = _objects(before, "trigger")
        old_index = _objects(before, "index")
        before.close()
        store = initialize_workspace_store(WorkspaceSettings(enabled=True, data_dir=self.folder))
        try:
            connection = store._connection  # noqa: SLF001
            self.assertEqual(connection.execute("SELECT schema_version FROM schema_metadata").fetchone()[0], 13)  # lane KO2: 13
            rows = connection.execute(
                "SELECT sequence_id,organization_id,principal_id,name,value,source,created_at"
                " FROM experimenter_settings ORDER BY sequence_id").fetchall()
            self.assertEqual([tuple(row) for row in rows], list(OLD_ROWS))
            self.assertEqual(connection.execute(
                "SELECT seq FROM sqlite_sequence WHERE name='experimenter_settings'").fetchone()[0], 5)
            self.assertEqual(set(_objects(connection, "trigger")), set(old_triggers))
            self.assertEqual(set(_objects(connection, "index")), set(old_index))
            check = _objects(connection, "table")["experimenter_settings"]
            for name in (*SETTINGS_V11, "speaker_filter", "voice_reference"):
                self.assertIn(f"'{name}'", check)
            principal = development_principal()
            store.bootstrap_principal(principal)
            # The measurement's two rows, as POST /api/voice/calibration writes them.
            after = store.record_experimenter_setting(
                principal, name="voice_reference", value="v1:-23.4:1792991234:mic-3f9a1c2b", source="screen")
            self.assertEqual(after["voice_reference"], "v1:-23.4:1792991234:mic-3f9a1c2b")
            after = store.record_experimenter_setting(
                principal, name="speaker_filter", value="on", source="screen")
            self.assertEqual(after["speaker_filter"], "on")
            with self.assertRaises(WorkspaceError):
                store.record_experimenter_setting(principal, name="speaker_filter", value="maybe", source="screen")
            with self.assertRaises(WorkspaceError):
                store.record_experimenter_setting(principal, name="voice_reference", value="v1:-23.4:1792991234:bad id!", source="screen")
            with self.assertRaises(sqlite3.DatabaseError):
                connection.execute("UPDATE experimenter_settings SET value='off' WHERE name='speaker_filter'")
            with self.assertRaises(sqlite3.DatabaseError):
                connection.execute("DELETE FROM experimenter_settings WHERE name='speaker_filter'")
        finally:
            store.close()

    def test_opening_a_schema_12_store_again_changes_nothing(self) -> None:
        _schema_11_store(self.path)
        store = initialize_workspace_store(WorkspaceSettings(enabled=True, data_dir=self.folder))
        store.close()
        first = sqlite3.connect(self.path)
        objects = {kind: _objects(first, kind) for kind in ("table", "index", "trigger")}
        seq = first.execute("SELECT seq FROM sqlite_sequence WHERE name='experimenter_settings'").fetchone()[0]
        first.close()
        store = initialize_workspace_store(WorkspaceSettings(enabled=True, data_dir=self.folder))
        try:
            connection = store._connection  # noqa: SLF001
            self.assertEqual(connection.execute("SELECT schema_version FROM schema_metadata").fetchone()[0], 13)  # lane KO2: 13
            for kind, expected in objects.items():
                self.assertEqual(_objects(connection, kind), expected)
            self.assertEqual(connection.execute(
                "SELECT seq FROM sqlite_sequence WHERE name='experimenter_settings'").fetchone()[0], seq)
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
