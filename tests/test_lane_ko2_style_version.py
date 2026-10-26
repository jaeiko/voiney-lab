"""Lane KO2, decision 1 (2026-10-26): a stored translation names its style version.

The machine Korean made before lane SAY's style (합니다체, "귀하", "Korean(English)")
stayed for good: the store keeps one row per sentence, and a sentence the check
refused was never tried again. Now every stored row and glossary carries the
version of the translation instructions it was made with
(``protocol_translation.STYLE_VERSION``) in its identity. Generation makes what
the current version lacks -- sentences stored only under an older version,
refused ones included -- and adds the rows beside the old ones (append-only;
nothing is rewritten). Reading takes, sentence by sentence, the newest
version's row that passes today's check, else an older one that passes, else
the source. A reviewed row or the fixture's reviewed sidecar is never made
again and still wins. A schema-11 store migrates with its rows kept as version
0 ("before versions were kept") and reads as it did. Every translator here is
fake.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from tests.test_revision_translations import (
    FakeTranslator,
    generate,
    good_korean,
    rich_fixture,
)
from tests.protocol_vocabulary_support import MINIPREP_STEPS
from voiney_lab import protocol_translation as pt
from voiney_lab import workspace_store as ws
from voiney_lab.protocol_translation import (
    GlossaryEntry,
    GlossaryResult,
    generate_revision_translations,
    glossary_entries,
    source_sha256,
    translation_revision_key,
    with_stored_translations,
)
from voiney_lab.workspace_store import (
    FactTranslationRecord,
    TranslationGlossaryRecord,
    WorkspaceSettings,
    initialize_workspace_store,
)
import voiney_lab.server as server_module

OLD_STEP_2 = "2단계: 옛 번역입니다 10 µL lysozyme 5 times"


def old_rows(fixture, *, step_2_check: str = "passed"):
    """Rows as a store before this lane holds them: no version (0)."""

    key = translation_revision_key(fixture)
    return [
        FactTranslationRecord(
            key, "step-1/current_step", "ko", source_sha256(MINIPREP_STEPS[0]),
            "1단계: 옛 번역 250 µL Buffer 1 Tris-HCl buffer 합니다", "machine", "passed",
            "old-model", "old-model", "2026-10-01T00:00:00+00:00"),
        FactTranslationRecord(
            key, "step-2/current_step", "ko", source_sha256(MINIPREP_STEPS[1]),
            OLD_STEP_2 if step_2_check == "passed" else "2단계: 틀린 번역 99 µL",
            "machine", step_2_check, "old-model", "old-model", "2026-10-01T00:00:00+00:00"),
    ]


class _Glossaries:
    """A glossary maker that counts its calls."""

    def __init__(self, entries=(GlossaryEntry("lysozyme", "lysozyme", True),)) -> None:
        self.calls = 0
        self.entries = tuple(entries)

    async def __call__(self, context):
        self.calls += 1
        return GlossaryResult(entries=self.entries, model_version="glossary-test")


class VersionTests(unittest.TestCase):
    def test_the_current_style_is_version_4_and_new_rows_carry_it(self) -> None:
        # Lane SAY's 해요체 guide was v3 (never stored); lane KO2's (a)-(e) is v4.
        self.assertEqual(pt.STYLE_VERSION, 4)
        report = generate(rich_fixture(), FakeTranslator())
        self.assertTrue(report.records)
        self.assertEqual({row.style_version for row in report.records}, {pt.STYLE_VERSION})
        # A row made by code that names no version is a row of before versions.
        self.assertEqual(old_rows(rich_fixture())[0].style_version, 0)

    def test_rows_of_an_older_version_are_made_again_refused_ones_too(self) -> None:
        fixture = rich_fixture()
        stored = old_rows(fixture, step_2_check="quantities_changed")
        translator = FakeTranslator()
        report = generate(fixture, translator, stored)
        sent = {key for batch in translator.batches for key in batch}
        # Lane SAY found the refused step 2 never asked again; now it is.
        self.assertIn("step-1/current_step", sent)
        self.assertIn("step-2/current_step", sent)
        self.assertEqual(report.skipped_stored, 0)
        # A second run under the same version makes no call.
        again = FakeTranslator()
        second = generate(fixture, again, [*stored, *report.records])
        self.assertEqual(again.batches, [])
        self.assertEqual(second.skipped_stored, 10)
        # A refused row of the current version is not asked again either.
        refusing = FakeTranslator(wrong={"step-3/current_step": "3단계: 틀린 번역 99 µL"})
        made = generate(fixture, refusing, stored)
        self.assertEqual(
            {row.fact_key: row.check_result for row in made.records}["step-3/current_step"],
            "quantities_changed")
        third = FakeTranslator()
        generate(fixture, third, [*stored, *made.records])
        self.assertEqual(third.batches, [])

    def test_reading_takes_the_newest_version_that_passes(self) -> None:
        fixture = rich_fixture()
        stored = old_rows(fixture)
        newer = generate(fixture, FakeTranslator(wrong={
            # The new version's reading of step 2 is refused: the old one stays.
            "step-2/current_step": "2단계: 틀린 번역 99 µL",
        }), stored).records
        shown = with_stored_translations(fixture, [*stored, *newer])
        self.assertEqual(shown.localized_fact("step-1", "current_step"),
                         good_korean(MINIPREP_STEPS[0]))
        self.assertEqual(shown.localized_fact("step-2", "current_step"), OLD_STEP_2)
        # The order rows are read in does not matter.
        shown = with_stored_translations(fixture, [*reversed(newer), *stored])
        self.assertEqual(shown.localized_fact("step-1", "current_step"),
                         good_korean(MINIPREP_STEPS[0]))

    def test_a_reviewed_translation_is_never_made_again_and_wins(self) -> None:
        reviewed_text = "1단계: 검토된 번역 250 µL Buffer 1 Tris-HCl buffer"
        fixture = replace(rich_fixture(), localizations={"step-1/current_step": reviewed_text})
        key = translation_revision_key(fixture)
        stored_reviewed = FactTranslationRecord(
            key, "step-2/current_step", "ko", source_sha256(MINIPREP_STEPS[1]),
            "2단계: 사람이 확인한 번역 10 µL lysozyme 5 times", "reviewed", "passed",
            "reviewer", "reviewer", "2026-10-02T00:00:00+00:00")
        translator = FakeTranslator()
        report = generate(fixture, translator, [*old_rows(fixture), stored_reviewed])
        sent = {key for batch in translator.batches for key in batch}
        self.assertNotIn("step-1/current_step", sent)
        self.assertNotIn("step-2/current_step", sent)
        shown = with_stored_translations(fixture, [*old_rows(fixture), stored_reviewed, *report.records])
        self.assertEqual(shown.localized_fact("step-1", "current_step"), reviewed_text)
        self.assertEqual(shown.localization_source("step-2", "current_step"), "reviewed")

    def test_each_version_keeps_its_own_glossary(self) -> None:
        fixture = rich_fixture()
        old = TranslationGlossaryRecord(
            translation_revision_key(fixture), "ko",
            '[{"source": "lysozyme", "korean": "라이소자임", "keep_english": false}]',
            "old-model", "old-model", "2026-10-01T00:00:00+00:00")
        self.assertEqual(old.style_version, 0)
        maker = _Glossaries()
        # A glossary of an older version is not the current version's: one is made.
        report = asyncio.run(generate_revision_translations(
            fixture, FakeTranslator(), model="grok-test", stored=old_rows(fixture),
            glossary=None, make_glossary=maker))
        self.assertEqual(maker.calls, 1)
        self.assertEqual(report.glossary_record.style_version, pt.STYLE_VERSION)
        # The old row "라이소자임" passes with its own glossary, not with the new
        # one that keeps the name in English.
        key = translation_revision_key(fixture)
        row = FactTranslationRecord(
            key, "step-2/current_step", "ko", source_sha256(MINIPREP_STEPS[1]),
            "2단계: 라이소자임 10 µL 5 times", "machine", "passed", "old-model", "old-model",
            "2026-10-01T00:00:00+00:00")
        by_version = {0: glossary_entries(old), pt.STYLE_VERSION: report.glossary}
        shown = with_stored_translations(fixture, [row], glossaries=by_version)
        self.assertEqual(shown.localized_fact("step-2", "current_step"), "2단계: 라이소자임 10 µL 5 times")
        shown = with_stored_translations(fixture, [row], report.glossary)
        self.assertIsNone(shown.localized_fact("step-2", "current_step"))


def _schema_11_store(path: Path, fixture) -> None:
    """A workspace exactly as schema 11 left it, with two translation rows and a glossary."""

    connection = sqlite3.connect(path)
    connection.executescript(ws.SCHEMA)
    for migration in (
        ws.MIGRATION_1_TO_2, ws.MIGRATION_2_TO_3, ws.MIGRATION_3_TO_4, ws.MIGRATION_4_TO_5,
        ws.MIGRATION_5_TO_6, ws.MIGRATION_6_TO_7, ws.MIGRATION_7_TO_8, ws.MIGRATION_8_TO_9,
        ws.MIGRATION_9_TO_10, ws.MIGRATION_10_TO_11,
    ):
        connection.executescript("BEGIN IMMEDIATE;\n" + migration + "\nCOMMIT;")
    import hashlib
    for row in old_rows(fixture):
        identity = f"{row.revision_id}:{row.fact_key}:{row.language}:{row.source_sha256}:{row.status}"
        connection.execute(
            "INSERT INTO protocol_fact_translations(translation_id,revision_id,fact_key,language,"
            "source_sha256,translated_text,status,check_result,model,model_version,created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("fact-translation-" + hashlib.sha256(identity.encode()).hexdigest()[:32],
             row.revision_id, row.fact_key, row.language, row.source_sha256,
             row.translated_text, row.status, row.check_result, row.model,
             row.model_version, row.created_at))
    connection.execute(
        "INSERT INTO protocol_translation_glossaries VALUES(?,?,?,?,?,?,?)",
        ("glossary-old", translation_revision_key(fixture), "ko",
         '[{"source": "lysozyme", "korean": "lysozyme", "keep_english": true}]',
         "old-model", "old-model", "2026-10-01T00:00:00+00:00"))
    connection.commit()
    assert connection.execute("SELECT schema_version FROM schema_metadata").fetchone()[0] == 11
    connection.close()


def _objects(connection, table: str, kind: str) -> set[str]:
    return {
        str(name) for (name,) in connection.execute(
            "SELECT name FROM sqlite_master WHERE type=? AND tbl_name=?", (kind, table))
    }


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.folder = Path(self._temp.name)

    def store(self):
        return initialize_workspace_store(WorkspaceSettings(True, self.folder))

    def test_a_schema_11_store_migrates_and_reads_its_old_rows_as_before(self) -> None:
        fixture = rich_fixture()
        _schema_11_store(self.folder / ws.WORKSPACE_DATABASE_FILENAME, fixture)
        before = sqlite3.connect(self.folder / ws.WORKSPACE_DATABASE_FILENAME)
        triggers = _objects(before, "protocol_fact_translations", "trigger")
        glossary_triggers = _objects(before, "protocol_translation_glossaries", "trigger")
        ids = [row[0] for row in before.execute(
            "SELECT translation_id FROM protocol_fact_translations ORDER BY rowid")]
        before.close()
        store = self.store()
        try:
            connection = store._connection  # noqa: SLF001
            # Lane HEAR2's schema 12 (settings names) comes first, then this
            # lane's 13 (translation style versions).
            self.assertEqual(ws.WORKSPACE_SCHEMA_VERSION, 13)
            self.assertEqual(connection.execute(
                "SELECT schema_version FROM schema_metadata").fetchone()[0], 13)
            key = translation_revision_key(fixture)
            rows = store.fact_translations(key, "ko")
            self.assertEqual([row.style_version for row in rows], [0, 0])
            self.assertEqual([row.translated_text for row in rows],
                             [row.translated_text for row in old_rows(fixture)])
            self.assertEqual([row[0] for row in connection.execute(
                "SELECT translation_id FROM protocol_fact_translations ORDER BY rowid")], ids)
            glossary = store.translation_glossary(key, "ko")
            self.assertEqual((glossary.style_version, glossary.model), (0, "old-model"))
            self.assertIsNone(store.translation_glossary(key, "ko", style_version=pt.STYLE_VERSION))
            self.assertEqual(_objects(connection, "protocol_fact_translations", "trigger"), triggers)
            self.assertEqual(
                _objects(connection, "protocol_translation_glossaries", "trigger"), glossary_triggers)
            shown = with_stored_translations(fixture, rows, glossary_entries(glossary))
            self.assertEqual(shown.localized_fact("step-2", "current_step"), OLD_STEP_2)
            # The same sentence under the current version is a new row beside the old one.
            made = generate(fixture, FakeTranslator(), rows)
            self.assertEqual(store.record_fact_translations(made.records), len(made.records))
            self.assertTrue(store.record_translation_glossary(replace(
                glossary, style_version=pt.STYLE_VERSION, model="new-model")))
            self.assertEqual(len(store.fact_translations(key, "ko")), 2 + len(made.records))
            self.assertEqual(store.translation_glossary(key, "ko").model, "new-model")
            self.assertEqual(
                [item.style_version for item in store.translation_glossaries(key, "ko")],
                [0, pt.STYLE_VERSION])
            for statement in (
                "UPDATE protocol_fact_translations SET translated_text='x'",
                "DELETE FROM protocol_fact_translations",
                "UPDATE protocol_translation_glossaries SET entries_json='[]'",
                "DELETE FROM protocol_translation_glossaries",
            ):
                with self.assertRaises(sqlite3.DatabaseError):
                    connection.execute(statement)
        finally:
            store.close()
        again = self.store()
        try:
            self.assertEqual(len(again.fact_translations(translation_revision_key(fixture), "ko")),
                             2 + len(made.records))
        finally:
            again.close()

    def test_a_fresh_store_holds_versions(self) -> None:
        fixture = rich_fixture()
        store = self.store()
        try:
            rows = generate(fixture, FakeTranslator()).records
            self.assertEqual(store.record_fact_translations(rows), len(rows))
            self.assertEqual(store.record_fact_translations(rows), 0)
            older = [replace(row, style_version=0) for row in rows]
            self.assertEqual(store.record_fact_translations(older), len(rows))
            self.assertEqual(
                sorted({row.style_version for row in store.fact_translations(
                    translation_revision_key(fixture), "ko")}),
                [0, pt.STYLE_VERSION])
            with self.assertRaises(ws.WorkspaceError):
                store.record_fact_translations([replace(rows[0], style_version=-1)])
        finally:
            store.close()


SETTING_ROWS = (
    (1, "tenant-a", "principal-a", "confirm_mode", "quiet", "voice", "2026-10-20T01:00:00+00:00"),
    (2, "tenant-a", "principal-a", "speaker_filter", "on", "screen", "2026-10-26T02:00:00+00:00"),
    (5, "tenant-a", "principal-a", "voice_reference", "v1:-24.0:1792990000:mic-1", "voice",
     "2026-10-26T02:01:00+00:00"),
)


def _hear2_schema_12_store(path: Path, fixture) -> None:
    """A store as lane HEAR2's schema 12 left it: its settings names, the old translation tables."""

    _schema_11_store(path, fixture)
    connection = sqlite3.connect(path)
    connection.executescript("BEGIN IMMEDIATE;\n" + ws.MIGRATION_11_TO_12 + "\nCOMMIT;")
    connection.execute("INSERT INTO organizations VALUES('tenant-a','Lab A','2026-10-08T00:00:00+00:00')")
    connection.execute("INSERT INTO principals VALUES(?,?,?,?)",
                       ("principal-a", "dev:principal-a", "principal-a", "2026-10-08T00:00:00+00:00"))
    connection.executemany(
        "INSERT INTO experimenter_settings(sequence_id,organization_id,principal_id,name,value,source,created_at)"
        " VALUES(?,?,?,?,?,?,?)", SETTING_ROWS)
    connection.commit()
    assert connection.execute("SELECT schema_version FROM schema_metadata").fetchone()[0] == 12
    columns = [row[1] for row in connection.execute("PRAGMA table_info(protocol_fact_translations)")]
    assert "style_version" not in columns
    connection.close()


class SchemaThirteenTests(unittest.TestCase):
    """Lane HEAR2's 11 -> 12 (settings names) and this lane's 12 -> 13 (style versions).

    Both lanes had raised the schema to 12; HEAR2 merged first (person's
    signal of 2026-10-26), so this lane's migration is 12 -> 13. A store at 11
    takes both, a store at HEAR2's 12 takes the second, and each keeps its rows.
    """

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.folder = Path(self._temp.name)
        self.path = self.folder / ws.WORKSPACE_DATABASE_FILENAME

    def opened(self):
        return initialize_workspace_store(WorkspaceSettings(True, self.folder))

    def check_both_migrations(self, store, fixture) -> None:
        connection = store._connection  # noqa: SLF001
        self.assertEqual(ws.WORKSPACE_SCHEMA_VERSION, 13)
        self.assertEqual(connection.execute(
            "SELECT schema_version FROM schema_metadata").fetchone()[0], 13)
        settings = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='experimenter_settings'").fetchone()[0]
        for name in ("'speaker_filter'", "'voice_reference'", "'voice_calibration'"):
            self.assertIn(name, settings)  # lane HEAR2's 11 -> 12
        for table in ("protocol_fact_translations", "protocol_translation_glossaries"):
            columns = [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
            self.assertIn("style_version", columns)  # this lane's 12 -> 13
            self.assertEqual(
                _objects(connection, table, "trigger"),
                {"fact_translations_no_update", "fact_translations_no_delete"}
                if table == "protocol_fact_translations" else
                {"translation_glossaries_no_update", "translation_glossaries_no_delete"})
        key = translation_revision_key(fixture)
        rows = store.fact_translations(key, "ko")
        self.assertEqual([row.style_version for row in rows], [0, 0])
        self.assertEqual([row.translated_text for row in rows],
                         [row.translated_text for row in old_rows(fixture)])
        made = generate(fixture, FakeTranslator(), rows)
        self.assertEqual(store.record_fact_translations(made.records), len(made.records))

    def test_a_schema_11_store_takes_both_migrations(self) -> None:
        fixture = rich_fixture()
        _schema_11_store(self.path, fixture)
        store = self.opened()
        try:
            self.check_both_migrations(store, fixture)
        finally:
            store.close()

    def test_a_hear2_schema_12_store_takes_the_translation_migration_and_keeps_its_settings(self) -> None:
        fixture = rich_fixture()
        _hear2_schema_12_store(self.path, fixture)
        store = self.opened()
        try:
            self.check_both_migrations(store, fixture)
            connection = store._connection  # noqa: SLF001
            self.assertEqual(
                [tuple(row) for row in connection.execute(
                    "SELECT sequence_id,organization_id,principal_id,name,value,source,created_at"
                    " FROM experimenter_settings ORDER BY sequence_id")],
                list(SETTING_ROWS))
            self.assertEqual(connection.execute(
                "SELECT seq FROM sqlite_sequence WHERE name='experimenter_settings'").fetchone()[0], 5)
        finally:
            store.close()
        again = self.opened()  # opening again changes nothing
        try:
            self.assertEqual(len(again.fact_translations(translation_revision_key(fixture), "ko")),
                             2 + len(generate(fixture, FakeTranslator(), old_rows(fixture)).records))
        finally:
            again.close()


class ServerPathTests(unittest.TestCase):
    """The server makes and reads the current version through the workspace."""

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.workspace = Path(self._temp.name) / "workspace"

    def test_the_server_adds_the_current_version_beside_old_rows(self) -> None:
        fixture = rich_fixture()
        key = translation_revision_key(fixture)
        with patch.dict(os.environ, {
            "VOINEY_LAB_WORKSPACE_ENABLED": "true",
            "VOINEY_LAB_WORKSPACE_DATA_DIR": str(self.workspace),
        }):
            store = server_module._revision_translation_store()
            try:
                store.record_fact_translations(old_rows(fixture, step_2_check="quantities_changed"))
                store.record_translation_glossary(TranslationGlossaryRecord(
                    key, "ko", "[]", "old-model", "old-model", "2026-10-01T00:00:00+00:00"))
            finally:
                store.close()
            before = server_module._with_revision_translations(fixture, start_missing=False)
            self.assertIsNone(before.localized_fact("step-2", "current_step"))
            maker = _Glossaries()
            report = asyncio.run(server_module.store_revision_translations(
                fixture, FakeTranslator(), model="grok-test", make_glossary=maker))
            self.assertEqual(maker.calls, 1)
            self.assertEqual(report.skipped_stored, 0)
            after = server_module._with_revision_translations(fixture, start_missing=False)
            self.assertEqual(after.localized_fact("step-2", "current_step"),
                             good_korean(MINIPREP_STEPS[1]))
            # A second run makes nothing.
            again = FakeTranslator()
            report = asyncio.run(server_module.store_revision_translations(
                fixture, again, model="grok-test", make_glossary=maker))
            self.assertEqual((again.batches, maker.calls), ([], 1))
            store = server_module._revision_translation_store()
            try:
                self.assertEqual(
                    [row.style_version for row in store.translation_glossaries(key, "ko")],
                    [0, pt.STYLE_VERSION])
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
