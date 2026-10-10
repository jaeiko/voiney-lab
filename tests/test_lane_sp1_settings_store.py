"""Lane SP1, decision 3 (2026-10-10): the settings table at workspace schema 10.

Schema 9's ``experimenter_settings`` CHECK took four names. Decision 3's
"주변 소리" (``ambient_mode``: quiet / noisy) and decision 1's "민감도"
(``speaker_sensitivity``: high / normal / low) are kept like the other
settings, so schema 10 builds the table again with the six names and keeps
everything else of it: every row as it was, its sequence ids, the
AUTOINCREMENT counter, the index and both append-only triggers. Opening a
schema-10 store again changes nothing. A store migrated from 9 holds no row
of the new names, so they start at their defaults (조용함, 보통).
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.identity_support import development_principal
from voiney_lab import server as server_module
from voiney_lab import workspace_store as ws
from voiney_lab.workspace_store import (
    EXPERIMENTER_SETTING_VALUES,
    WORKSPACE_SCHEMA_VERSION,
    WorkspaceError,
    WorkspaceSettings,
    initialize_workspace_store,
)

#: Rows a schema-9 store may hold: two experimenters of one tenant, every
#: name schema 9 knew, by voice and on the screen, in the order made.
OLD_ROWS = (
    (1, "tenant-a", "principal-a", "confirm_mode", "quiet", "voice", "2026-10-08T01:00:00+00:00"),
    (2, "tenant-a", "principal-b", "question_timing", "during", "screen", "2026-10-08T02:00:00+00:00"),
    (3, "tenant-a", "principal-a", "web_lookup", "off", "screen", "2026-10-09T03:00:00+00:00"),
    (4, "tenant-a", "principal-a", "proactive_mode", "needed", "voice", "2026-10-09T04:00:00+00:00"),
    (5, "tenant-a", "principal-a", "confirm_mode", "confirm", "screen", "2026-10-09T05:00:00+00:00"),
)


def _objects(connection: sqlite3.Connection, kind: str) -> dict[str, str]:
    return {
        str(name): str(sql)
        for name, sql in connection.execute(
            "SELECT name,sql FROM sqlite_master WHERE type=? AND tbl_name='experimenter_settings'",
            (kind,),
        )
    }


def build_schema_9_store(directory: Path, *, sequence: int | None = None) -> dict[str, dict[str, str]]:
    """A workspace as schema 9 left it, with ``OLD_ROWS``; its index and triggers."""

    directory.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(directory / ws.WORKSPACE_DATABASE_FILENAME)
    connection.executescript(ws.SCHEMA)
    for script in (ws.MIGRATION_1_TO_2, ws.MIGRATION_2_TO_3, ws.MIGRATION_3_TO_4,
                   ws.MIGRATION_4_TO_5, ws.MIGRATION_5_TO_6, ws.MIGRATION_6_TO_7,
                   ws.MIGRATION_7_TO_8, ws.MIGRATION_8_TO_9):
        connection.executescript(script)
    connection.execute("INSERT INTO organizations VALUES('tenant-a','Tenant A','2026-10-01T00:00:00+00:00')")
    for principal in ("principal-a", "principal-b"):
        connection.execute(
            "INSERT INTO principals VALUES(?,?,?,?)",
            (principal, f"subject-{principal}", principal, "2026-10-01T00:00:00+00:00"))
    connection.executemany("INSERT INTO experimenter_settings VALUES(?,?,?,?,?,?,?)", OLD_ROWS)
    if sequence is not None:
        connection.execute("UPDATE sqlite_sequence SET seq=? WHERE name='experimenter_settings'", (sequence,))
    connection.commit()
    assert connection.execute("SELECT schema_version FROM schema_metadata").fetchone()[0] == 9
    objects = {"index": _objects(connection, "index"), "trigger": _objects(connection, "trigger")}
    connection.close()
    return objects


class SchemaTenTests(unittest.TestCase):

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.directory = Path(tmp.name) / "workspace"

    def open(self):
        store = initialize_workspace_store(WorkspaceSettings(True, self.directory))
        self.addCleanup(store.close)
        return store

    def test_the_version_is_ten(self) -> None:
        self.assertEqual(WORKSPACE_SCHEMA_VERSION, 10)
        store = self.open()
        self.assertEqual(store._connection.execute(
            "SELECT schema_version FROM schema_metadata").fetchone()[0], 10)

    def test_a_schema_9_store_keeps_every_row_and_its_sequence(self) -> None:
        before = build_schema_9_store(self.directory)
        store = self.open()
        connection = store._connection
        self.assertEqual(connection.execute("SELECT schema_version FROM schema_metadata").fetchone()[0], 10)
        self.assertEqual(
            [tuple(row) for row in connection.execute(
                "SELECT * FROM experimenter_settings ORDER BY sequence_id")],
            list(OLD_ROWS))
        self.assertEqual(connection.execute(
            "SELECT seq FROM sqlite_sequence WHERE name='experimenter_settings'").fetchone()[0], 5)
        self.assertEqual(_objects(connection, "index"), before["index"])
        self.assertEqual(_objects(connection, "trigger"), before["trigger"])
        self.assertEqual(connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE name LIKE 'experimenter_settings_v9%'").fetchone()[0], 0)

    def test_each_experimenters_values_are_as_they_were_and_the_new_names_default(self) -> None:
        build_schema_9_store(self.directory)
        store = self.open()
        rows = store._connection.execute(
            "SELECT principal_id,name,value FROM experimenter_settings ORDER BY sequence_id").fetchall()
        latest: dict[str, dict[str, str]] = {}
        for principal, name, value in rows:
            latest.setdefault(principal, {})[name] = value
        self.assertEqual(latest, {
            "principal-a": {"confirm_mode": "confirm", "web_lookup": "off", "proactive_mode": "needed"},
            "principal-b": {"question_timing": "during"},
        })
        self.assertEqual(server_module.EXPERIMENTER_SETTING_DEFAULTS["ambient_mode"], "quiet")
        self.assertEqual(server_module.EXPERIMENTER_SETTING_DEFAULTS["speaker_sensitivity"], "normal")
        self.assertEqual(set(EXPERIMENTER_SETTING_VALUES), set(server_module.EXPERIMENTER_SETTING_DEFAULTS))

    def test_the_counter_is_kept_when_it_is_past_the_last_row(self) -> None:
        build_schema_9_store(self.directory, sequence=9)
        store = self.open()
        connection = store._connection
        self.assertEqual(connection.execute(
            "SELECT seq FROM sqlite_sequence WHERE name='experimenter_settings'").fetchone()[0], 9)
        connection.execute(
            "INSERT INTO experimenter_settings(organization_id,principal_id,name,value,source,created_at) "
            "VALUES('tenant-a','principal-a','ambient_mode','noisy','voice','2026-10-10T00:00:00+00:00')")
        self.assertEqual(connection.execute(
            "SELECT max(sequence_id) FROM experimenter_settings").fetchone()[0], 10)

    def test_it_stays_append_only_and_takes_the_two_new_names(self) -> None:
        build_schema_9_store(self.directory)
        store = self.open()
        connection = store._connection
        for statement in ("DELETE FROM experimenter_settings",
                          "UPDATE experimenter_settings SET value='readback'"):
            with self.subTest(statement=statement), self.assertRaises(sqlite3.DatabaseError):
                connection.execute(statement)
        for name, value in (("ambient_mode", "noisy"), ("speaker_sensitivity", "low"), ("web_lookup", "on")):
            connection.execute(
                "INSERT INTO experimenter_settings(organization_id,principal_id,name,value,source,created_at) "
                "VALUES('tenant-a','principal-a',?,?,'screen','2026-10-10T00:00:00+00:00')", (name, value))
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO experimenter_settings(organization_id,principal_id,name,value,source,created_at) "
                "VALUES('tenant-a','principal-a','volume','loud','screen','2026-10-10T00:00:00+00:00')")

    def test_opening_it_again_changes_nothing(self) -> None:
        build_schema_9_store(self.directory)
        store = initialize_workspace_store(WorkspaceSettings(True, self.directory))
        first = list(store._connection.iterdump())
        store.close()
        store = initialize_workspace_store(WorkspaceSettings(True, self.directory))
        try:
            self.assertEqual(list(store._connection.iterdump()), first)
        finally:
            store.close()

    def test_a_new_store_takes_the_two_names_and_refuses_other_values(self) -> None:
        store = self.open()
        principal = development_principal()
        store.bootstrap_principal(principal)
        for name, value in (("ambient_mode", "noisy"), ("speaker_sensitivity", "high"),
                            ("speaker_sensitivity", "low"), ("ambient_mode", "quiet")):
            store.record_experimenter_setting(principal, name=name, value=value, source="voice")
        self.assertEqual(store.experimenter_settings(principal),
                         {"ambient_mode": "quiet", "speaker_sensitivity": "low"})
        for name, value in (("ambient_mode", "loud"), ("speaker_sensitivity", "max"), ("volume", "on")):
            with self.subTest(name=name), self.assertRaises(WorkspaceError):
                store.record_experimenter_setting(principal, name=name, value=value, source="screen")


class SettingsApiTests(unittest.TestCase):
    """The screen's settings carry the two names, kept in the workspace."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.workspace_dir = Path(tmp.name) / "workspace"
        self.principal = development_principal()
        store = initialize_workspace_store(WorkspaceSettings(True, self.workspace_dir))
        store.bootstrap_principal(self.principal)
        store.close()

    def call(self, function, *args):
        token = server_module._REQUEST_PRINCIPAL.set(self.principal)
        try:
            with patch.dict(os.environ, {
                "VOINEY_LAB_WORKSPACE_ENABLED": "true",
                "VOINEY_LAB_WORKSPACE_DATA_DIR": str(self.workspace_dir),
            }):
                return function(*args)
        finally:
            server_module._REQUEST_PRINCIPAL.reset(token)

    def test_defaults_then_screen_changes_outlive_the_servers_memory(self) -> None:
        with patch.dict(server_module._EXPERIMENTER_SETTINGS_MEMORY, {}, clear=True):
            settings = self.call(server_module.get_experimenter_settings)["settings"]
            self.assertEqual((settings["ambient_mode"], settings["speaker_sensitivity"]), ("quiet", "normal"))
            changed = self.call(server_module.put_experimenter_settings, {"ambient_mode": "noisy"})
            self.assertEqual(changed["settings"]["ambient_mode"], "noisy")
            changed = self.call(server_module.put_experimenter_settings, {"speaker_sensitivity": "high"})
            self.assertEqual(changed["settings"]["speaker_sensitivity"], "high")
        with patch.dict(server_module._EXPERIMENTER_SETTINGS_MEMORY, {}, clear=True):
            settings = self.call(server_module.get_experimenter_settings)["settings"]
            self.assertEqual((settings["ambient_mode"], settings["speaker_sensitivity"]), ("noisy", "high"))
        with self.assertRaises(Exception):
            self.call(server_module.put_experimenter_settings, {"ambient_mode": "loud"})


if __name__ == "__main__":
    unittest.main()
