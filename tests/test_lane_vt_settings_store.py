"""Lane VT, decision 7 (2026-10-09): the settings table at workspace schema 9.

Lane CF's ``experimenter_settings`` table (schema 8) took two names by a
CHECK, ``confirm_mode`` and ``question_timing``, so lane WV's "웹 찾아보기"
was held in the server's memory and lost when it stopped. Schema 9 builds the
table again with two more names -- ``web_lookup`` and lane VT's
``proactive_mode`` -- and keeps everything else of it: every row as it was,
its sequence ids, the AUTOINCREMENT counter, the index and both append-only
triggers. Opening a schema-9 store again changes nothing. "웹 찾아보기" is now
kept with the other settings.
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
    WORKSPACE_SCHEMA_VERSION,
    WorkspaceError,
    WorkspaceSettings,
    initialize_workspace_store,
)

#: Rows a schema-8 store may hold: two experimenters of one tenant, changes
#: by voice and on the screen, in the order they were made.
OLD_ROWS = (
    (1, "tenant-a", "principal-a", "confirm_mode", "quiet", "voice", "2026-10-08T01:00:00+00:00"),
    (2, "tenant-a", "principal-b", "question_timing", "during", "screen", "2026-10-08T02:00:00+00:00"),
    (3, "tenant-a", "principal-a", "confirm_mode", "confirm", "screen", "2026-10-08T03:00:00+00:00"),
    (4, "tenant-a", "principal-a", "question_timing", "before_start", "voice", "2026-10-08T04:00:00+00:00"),
)


def _objects(connection: sqlite3.Connection, kind: str) -> dict[str, str]:
    return {
        str(name): str(sql)
        for name, sql in connection.execute(
            "SELECT name,sql FROM sqlite_master WHERE type=? AND tbl_name='experimenter_settings'",
            (kind,),
        )
    }


def build_schema_8_store(directory: Path, *, sequence: int | None = None) -> dict[str, dict[str, str]]:
    """A workspace as schema 8 left it, with ``OLD_ROWS``; its index and triggers.

    ``sequence`` sets the AUTOINCREMENT counter above the last row, as a store
    whose later insert was refused would hold it.
    """

    directory.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(directory / ws.WORKSPACE_DATABASE_FILENAME)
    connection.executescript(ws.SCHEMA)
    for script in (ws.MIGRATION_1_TO_2, ws.MIGRATION_2_TO_3, ws.MIGRATION_3_TO_4,
                   ws.MIGRATION_4_TO_5, ws.MIGRATION_5_TO_6, ws.MIGRATION_6_TO_7,
                   ws.MIGRATION_7_TO_8):
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
    assert connection.execute("SELECT schema_version FROM schema_metadata").fetchone()[0] == 8
    objects = {"index": _objects(connection, "index"), "trigger": _objects(connection, "trigger")}
    connection.close()
    return objects


class SchemaNineTests(unittest.TestCase):

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.directory = Path(tmp.name) / "workspace"

    def open(self):
        store = initialize_workspace_store(WorkspaceSettings(True, self.directory))
        self.addCleanup(store.close)
        return store

    def test_the_version_is_nine(self) -> None:
        self.assertEqual(WORKSPACE_SCHEMA_VERSION, 9)
        store = self.open()
        self.assertEqual(store._connection.execute(
            "SELECT schema_version FROM schema_metadata").fetchone()[0], 9)

    def test_a_schema_8_store_keeps_every_row_and_its_sequence(self) -> None:
        before = build_schema_8_store(self.directory)
        store = self.open()
        connection = store._connection
        self.assertEqual(connection.execute("SELECT schema_version FROM schema_metadata").fetchone()[0], 9)
        self.assertEqual(
            [tuple(row) for row in connection.execute(
                "SELECT * FROM experimenter_settings ORDER BY sequence_id")],
            list(OLD_ROWS))
        self.assertEqual(connection.execute(
            "SELECT seq FROM sqlite_sequence WHERE name='experimenter_settings'").fetchone()[0], 4)
        # The index and both triggers are made as schema 8 made them.
        self.assertEqual(_objects(connection, "index"), before["index"])
        self.assertEqual(_objects(connection, "trigger"), before["trigger"])
        # The copy the migration worked from is gone.
        self.assertEqual(connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE name LIKE 'experimenter_settings_v8%'").fetchone()[0], 0)

    def test_each_experimenters_values_are_as_they_were(self) -> None:
        build_schema_8_store(self.directory)
        store = self.open()
        rows = store._connection.execute(
            """SELECT principal_id,name,value FROM experimenter_settings ORDER BY sequence_id""").fetchall()
        latest: dict[str, dict[str, str]] = {}
        for principal, name, value in rows:
            latest.setdefault(principal, {})[name] = value
        self.assertEqual(latest, {
            "principal-a": {"confirm_mode": "confirm", "question_timing": "before_start"},
            "principal-b": {"question_timing": "during"},
        })

    def test_the_counter_is_kept_when_it_is_past_the_last_row(self) -> None:
        build_schema_8_store(self.directory, sequence=9)
        store = self.open()
        connection = store._connection
        self.assertEqual(connection.execute(
            "SELECT seq FROM sqlite_sequence WHERE name='experimenter_settings'").fetchone()[0], 9)
        connection.execute(
            "INSERT INTO experimenter_settings(organization_id,principal_id,name,value,source,created_at) "
            "VALUES('tenant-a','principal-a','web_lookup','off','voice','2026-10-09T00:00:00+00:00')")
        self.assertEqual(connection.execute(
            "SELECT max(sequence_id) FROM experimenter_settings").fetchone()[0], 10)

    def test_it_stays_append_only_and_takes_the_two_new_names(self) -> None:
        build_schema_8_store(self.directory)
        store = self.open()
        connection = store._connection
        for statement in ("DELETE FROM experimenter_settings",
                          "UPDATE experimenter_settings SET value='readback'"):
            with self.subTest(statement=statement), self.assertRaises(sqlite3.DatabaseError):
                connection.execute(statement)
        for name, value in (("web_lookup", "off"), ("proactive_mode", "needed")):
            connection.execute(
                "INSERT INTO experimenter_settings(organization_id,principal_id,name,value,source,created_at) "
                "VALUES('tenant-a','principal-a',?,?,'screen','2026-10-09T00:00:00+00:00')", (name, value))
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO experimenter_settings(organization_id,principal_id,name,value,source,created_at) "
                "VALUES('tenant-a','principal-a','volume','loud','screen','2026-10-09T00:00:00+00:00')")

    def test_opening_it_again_changes_nothing(self) -> None:
        build_schema_8_store(self.directory)
        store = initialize_workspace_store(WorkspaceSettings(True, self.directory))
        first = list(store._connection.iterdump())
        store.close()
        store = initialize_workspace_store(WorkspaceSettings(True, self.directory))
        try:
            self.assertEqual(list(store._connection.iterdump()), first)
        finally:
            store.close()

    def test_a_new_store_has_the_wider_table(self) -> None:
        store = self.open()
        principal = development_principal()
        store.bootstrap_principal(principal)
        for name, value in (("web_lookup", "off"), ("proactive_mode", "off"),
                            ("proactive_mode", "needed"), ("proactive_mode", "all")):
            store.record_experimenter_setting(principal, name=name, value=value, source="voice")
        self.assertEqual(store.experimenter_settings(principal),
                         {"web_lookup": "off", "proactive_mode": "all"})
        for name, value in (("web_lookup", "maybe"), ("proactive_mode", "loud"), ("volume", "on")):
            with self.subTest(name=name), self.assertRaises(WorkspaceError):
                store.record_experimenter_setting(principal, name=name, value=value, source="screen")


class WebLookupIsKeptTests(unittest.TestCase):
    """"웹 찾아보기" was held in the server's memory; it is kept in the workspace now."""

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

    def test_a_screen_change_outlives_the_servers_memory(self) -> None:
        with patch.dict(server_module._EXPERIMENTER_SETTINGS_MEMORY, {}, clear=True):
            changed = self.call(server_module.put_experimenter_settings, {"web_lookup": "off"})
            self.assertEqual(changed["settings"]["web_lookup"], "off")
        # A server started again has nothing in memory; the workspace has it.
        with patch.dict(server_module._EXPERIMENTER_SETTINGS_MEMORY, {}, clear=True):
            self.assertEqual(
                self.call(server_module.get_experimenter_settings)["settings"]["web_lookup"], "off")
        store = initialize_workspace_store(WorkspaceSettings(True, self.workspace_dir))
        try:
            rows = store._connection.execute(
                "SELECT name,value,source FROM experimenter_settings ORDER BY sequence_id").fetchall()
        finally:
            store.close()
        self.assertEqual([tuple(row) for row in rows], [("web_lookup", "off", "screen")])

    def test_a_voice_change_is_appended_beside_the_others(self) -> None:
        with patch.dict(server_module._EXPERIMENTER_SETTINGS_MEMORY, {}, clear=True):
            self.call(server_module._save_experimenter_settings, {"confirm_mode": "quiet"}, "voice")
            saved = self.call(server_module._save_experimenter_settings, {"web_lookup": "off"}, "voice")
        self.assertEqual((saved["confirm_mode"], saved["web_lookup"]), ("quiet", "off"))
        store = initialize_workspace_store(WorkspaceSettings(True, self.workspace_dir))
        try:
            self.assertEqual(store.experimenter_settings(self.principal),
                             {"confirm_mode": "quiet", "web_lookup": "off"})
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
