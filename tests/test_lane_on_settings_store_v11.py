"""Lane ON, decision ② (2026-10-18): the settings table at workspace schema 11.

Schema 10's ``experimenter_settings`` CHECK took six names. The agent's
voice (``agent_voice``: the provider's voice id, or ``default``) and the
calibration outcome the wizard skips the step by (``voice_calibration``:
none / measured / skipped) are kept like the other settings, so schema 11
builds the table again with the eight names and keeps everything else of
it: every row as it was, its sequence ids, the AUTOINCREMENT counter, the
index and both append-only triggers. Opening a schema-11 store again
changes nothing. A store migrated from 10 holds no row of the new names.
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
    (2, "tenant-a", "principal-b", "question_timing", "during", "screen", "2026-10-08T02:00:00+00:00"),
    (3, "tenant-a", "principal-a", "ambient_mode", "noisy", "screen", "2026-10-10T03:00:00+00:00"),
    (7, "tenant-a", "principal-a", "speaker_sensitivity", "high", "voice", "2026-10-10T04:00:00+00:00"),
)


def _objects(connection: sqlite3.Connection, kind: str) -> dict[str, str]:
    return {
        str(name): str(sql)
        for name, sql in connection.execute(
            "SELECT name,sql FROM sqlite_master WHERE type=? AND tbl_name='experimenter_settings'",
            (kind,),
        )
    }


def _schema_10_store(path: Path) -> None:
    """A store exactly as schema 10 left it, with rows of two experimenters."""

    connection = sqlite3.connect(path)
    connection.executescript(ws.SCHEMA)
    for migration in (
        ws.MIGRATION_1_TO_2, ws.MIGRATION_2_TO_3, ws.MIGRATION_3_TO_4, ws.MIGRATION_4_TO_5,
        ws.MIGRATION_5_TO_6, ws.MIGRATION_6_TO_7, ws.MIGRATION_7_TO_8, ws.MIGRATION_8_TO_9,
        ws.MIGRATION_9_TO_10,
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
    assert connection.execute("SELECT schema_version FROM schema_metadata").fetchone()[0] == 10
    connection.close()


class SchemaElevenTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.folder = Path(self._temp.name)
        self.path = self.folder / ws.WORKSPACE_DATABASE_FILENAME

    def test_the_version_is_eleven_and_the_values_table_knows_the_two_names(self) -> None:
        self.assertEqual(WORKSPACE_SCHEMA_VERSION, 12)  # lane HEAR2: schema 12 adds speaker_filter and voice_reference
        self.assertEqual(EXPERIMENTER_SETTING_VALUES["voice_calibration"], ("none", "measured", "skipped"))
        self.assertIsNone(EXPERIMENTER_SETTING_VALUES["agent_voice"], "the voice ids are the provider's; the server checks them")

    def test_a_schema_10_store_migrates_keeping_rows_ids_counter_index_and_triggers(self) -> None:
        _schema_10_store(self.path)
        before = sqlite3.connect(self.path)
        old_triggers = _objects(before, "trigger")
        old_index = _objects(before, "index")
        before.close()
        store = initialize_workspace_store(WorkspaceSettings(enabled=True, data_dir=self.folder))
        try:
            connection = store._connection  # noqa: SLF001
            self.assertEqual(connection.execute("SELECT schema_version FROM schema_metadata").fetchone()[0], 12)  # lane HEAR2: the store opens at 12 now
            rows = connection.execute(
                "SELECT sequence_id,organization_id,principal_id,name,value,source,created_at"
                " FROM experimenter_settings ORDER BY sequence_id").fetchall()
            self.assertEqual([tuple(row) for row in rows], list(OLD_ROWS))
            self.assertEqual(connection.execute(
                "SELECT seq FROM sqlite_sequence WHERE name='experimenter_settings'").fetchone()[0], 7)
            self.assertEqual(set(_objects(connection, "trigger")), set(old_triggers))
            self.assertEqual(set(_objects(connection, "index")), set(old_index))
            check = _objects(connection, "table")["experimenter_settings"]
            for name in ("confirm_mode", "question_timing", "web_lookup", "proactive_mode",
                         "ambient_mode", "speaker_sensitivity", "agent_voice", "voice_calibration"):
                self.assertIn(f"'{name}'", check)
            principal = development_principal()
            store.bootstrap_principal(principal)
            after = store.record_experimenter_setting(
                principal, name="agent_voice", value="ko-KR-Chirp3-HD-Kore", source="screen")
            self.assertEqual(after["agent_voice"], "ko-KR-Chirp3-HD-Kore")
            after = store.record_experimenter_setting(
                principal, name="voice_calibration", value="measured", source="voice")
            self.assertEqual(after["voice_calibration"], "measured")
            with self.assertRaises(WorkspaceError):
                store.record_experimenter_setting(principal, name="voice_calibration", value="maybe", source="voice")
            with self.assertRaises(WorkspaceError):
                store.record_experimenter_setting(principal, name="agent_voice", value="bad voice id!", source="screen")
            with self.assertRaises(WorkspaceError):
                store.record_experimenter_setting(principal, name="agent_voice", value="x" * 81, source="screen")
            self.assertEqual(connection.execute(
                "SELECT seq FROM sqlite_sequence WHERE name='experimenter_settings'").fetchone()[0], 9)
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("DELETE FROM experimenter_settings WHERE sequence_id=1")
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("UPDATE experimenter_settings SET value='x' WHERE sequence_id=1")
        finally:
            store.close()
        # Opening it again changes nothing.
        again = initialize_workspace_store(WorkspaceSettings(enabled=True, data_dir=self.folder))
        try:
            self.assertEqual(again._connection.execute(  # noqa: SLF001
                "SELECT COUNT(*) FROM experimenter_settings").fetchone()[0], 6)
        finally:
            again.close()

    def test_a_fresh_store_is_schema_11_with_the_names(self) -> None:
        store = initialize_workspace_store(WorkspaceSettings(enabled=True, data_dir=self.folder))
        try:
            connection = store._connection  # noqa: SLF001
            self.assertEqual(connection.execute("SELECT schema_version FROM schema_metadata").fetchone()[0], 12)  # lane HEAR2: the store opens at 12 now
            self.assertIn("'agent_voice'", _objects(connection, "table")["experimenter_settings"])
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
