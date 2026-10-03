"""Korean for a Protocol revision, made once, stored by sentence, shown honestly.

When a revision becomes executable its sentences -- step guidance,
sub-actions, warnings, notes, expected results, before-start prerequisites
and the purpose -- are translated once, in batches, and stored one row per
sentence. Every row is held to ``reader_translation_issue``; a refused row is
kept as refused and never shown. A session picks reviewed Korean, then a
stored machine translation, then the source. The page is told when a machine
translation was shown, the voice says "자동 번역입니다." once a session, and a
sentence with a stored translation is never translated again per turn.
Every translator here is fake.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from voiney_lab import experiment_protocol as domain
from voiney_lab.curated_protocol import (
    PURPOSE_FACT_KEY,
    CuratedProtocolSession,
)
from voiney_lab.experiment_protocol_config import ProtocolPersistenceSettings
from voiney_lab.experiment_protocol_store import initialize_protocol_store
from voiney_lab.language import Transcription
from voiney_lab.protocol_catalog import (
    ProtocolCatalog,
    SharedSecretApprovalPolicy,
)
from voiney_lab.protocol_translation import (
    BatchResult,
    TranslationUnit,
    generate_revision_translations,
    openai_batch_translator,
    source_sha256,
    translation_units,
    with_stored_translations,
)
from voiney_lab.server import (
    curated_safety_items,
    curated_screen_fields,
    run_turn,
)
import voiney_lab.server as server_module
from voiney_lab.vad import TurnState
from voiney_lab.workspace_store import (
    SCHEMA,
    WORKSPACE_DATABASE_FILENAME,
    WORKSPACE_SCHEMA_VERSION,
    FactTranslationRecord,
    WorkspaceSettings,
    initialize_workspace_store,
)

from tests.protocol_vocabulary_support import (
    MINIPREP_STEPS,
    SOURCE_PDF,
    in_gel_fixture,
    miniprep_fixture,
)
from tests.test_protocol_catalog import analysis_draft, write_text_pdf
from tests.test_reader_speech import _FakeModel, _listener, _Socket

KOREAN_STEP_5 = "5 RT-PCR로 산물을 확인한다."
WARNING = "Do not vortex the lysate."
NOTE = "Keep the tube at 4 °C."
EXPECTED = "The lysate becomes clear."
SUB_ACTION = "Pipette up and down 10 times."
PREREQUISITE = "Thaw Buffer 1 at room temperature."


def _evidence(text: str) -> domain.SourceEvidence:
    return domain.SourceEvidence(1, text)


def rich_fixture(revision: str = "fictional-miniprep-v1"):
    """The miniprep with every translated kind, and one step in Korean."""

    base = miniprep_fixture()
    protocol = base.draft.protocol
    section = protocol.sections[0]
    steps = list(section.steps)
    steps[0] = replace(
        steps[0],
        sub_actions=(domain.ProtocolSubAction(
            "sub-1", SUB_ACTION, _evidence(SUB_ACTION)),),
        warnings=(domain.SourceStatement("w-1", WARNING, _evidence(WARNING)),),
        notes=(domain.SourceStatement("n-1", NOTE, _evidence(NOTE)),),
        expected_results=(domain.SourceStatement(
            "e-1", EXPECTED, _evidence(EXPECTED)),),
    )
    steps[4] = replace(steps[4], instruction_source_text=KOREAN_STEP_5)
    protocol = replace(
        protocol,
        sections=(replace(section, steps=tuple(steps)),),
        before_start=(domain.BeforeStartPrerequisite(
            "pre-1", PREREQUISITE, _evidence(PREREQUISITE)),),
    )
    return replace(base, draft=replace(base.draft, protocol=protocol),
                   revision_id=revision)


def good_korean(source: str) -> str:
    """A reading that keeps the numbers, the terms and the negation."""

    negated = " 하지 마세요" if "not" in source.casefold().split() else ""
    return f"한국어 번역: {source.replace(' five times', ' 5 times')}{negated}"


class FakeTranslator:
    """Answers every id; ``wrong`` maps an id to the reading returned instead."""

    def __init__(self, wrong: dict[str, str] | None = None,
                 error: Exception | None = None) -> None:
        self.wrong = wrong or {}
        self.error = error
        self.batches: list[list[str]] = []

    async def __call__(self, batch):
        self.batches.append([unit.fact_key for unit in batch])
        if self.error is not None:
            raise self.error
        return BatchResult(
            translations={
                unit.fact_key: self.wrong.get(unit.fact_key, good_korean(unit.source_text))
                for unit in batch
            },
            model_version="grok-test-0001",
            prompt_tokens=100 * len(batch),
            completion_tokens=40 * len(batch),
        )


def generate(fixture, translator, stored=()):
    return asyncio.run(generate_revision_translations(
        fixture, translator, model="grok-test", stored=stored))


class SentencesTranslatedTests(unittest.TestCase):
    """1. Every sentence the screen and the voice use; Korean is left alone."""

    def test_every_drawn_kind_and_the_purpose_is_a_unit(self) -> None:
        fixture = rich_fixture()
        units = {unit.fact_key: unit for unit in translation_units(fixture)}
        self.assertEqual(units[PURPOSE_FACT_KEY].kind, "purpose")
        self.assertEqual(
            units[PURPOSE_FACT_KEY].source_text,
            "A fictional protocol for vocabulary tests.")
        for key, kind in (
            ("step-1/current_step", "step"),
            ("step-1/sub_action_1", "sub_action"),
            ("step-1/warning_1", "warning"),
            ("step-1/note_1", "note"),
            ("step-1/expected_result_1", "expected_result"),
            ("step-1/prerequisite_1", "prerequisite"),
            ("step-4/current_step", "step"),
        ):
            with self.subTest(key=key):
                self.assertEqual(units[key].kind, kind)
        # Names of materials and equipment stay in their original spelling.
        self.assertFalse(any("/material_" in key or "/equipment_" in key
                             for key in units))

    def test_a_korean_source_sentence_is_not_translated(self) -> None:
        translator = FakeTranslator()
        report = generate(rich_fixture(), translator)
        sent = {key for batch in translator.batches for key in batch}
        self.assertNotIn("step-5/current_step", sent)
        self.assertEqual(report.skipped_korean, 1)
        self.assertIn("step-1/warning_1", sent)
        self.assertIn(PURPOSE_FACT_KEY, sent)

    def test_sentences_go_in_batches_not_one_call_each(self) -> None:
        translator = FakeTranslator()
        report = generate(rich_fixture(), translator)
        # 11 sentences, one of them Korean: 10 in two calls of at most 8.
        self.assertEqual(report.units, 11)
        self.assertEqual(report.calls, 2)
        self.assertEqual([len(batch) for batch in translator.batches], [8, 2])
        self.assertEqual(report.prompt_tokens, 1000)
        self.assertEqual(report.completion_tokens, 400)


class CheckTests(unittest.TestCase):
    """2. Each sentence is checked; a refused one is kept and never shown."""

    def test_numbers_units_and_negation_are_checked_per_sentence(self) -> None:
        translator = FakeTranslator(wrong={
            "step-1/current_step": good_korean(MINIPREP_STEPS[0]).replace("250", "25"),
            "step-1/warning_1": "한국어 번역: 용해물을 볼텍스합니다.",
            "step-1/note_1": "한국어 번역: Keep the tube at 4 °F.",
        })
        report = generate(rich_fixture(), translator)
        refused = {item.fact_key: item.check_result for item in report.refused}
        self.assertEqual(refused, {
            "step-1/current_step": "quantities_changed",
            "step-1/warning_1": "negation_changed",
            "step-1/note_1": "quantities_changed",
        })
        self.assertEqual(report.passed, 7)

    def test_a_refused_sentence_leaves_its_source_as_the_body(self) -> None:
        translator = FakeTranslator(wrong={
            "step-1/current_step": good_korean(MINIPREP_STEPS[0]).replace("250", "25"),
            "step-1/warning_1": "한국어 번역: 용해물을 볼텍스합니다.",
        })
        fixture = rich_fixture()
        report = generate(fixture, translator)
        shown = with_stored_translations(fixture, report.records)
        self.assertIsNone(shown.localized_fact("step-1", "current_step"))
        self.assertIsNone(shown.localized_fact("step-1", "warning_1"))
        curated = CuratedProtocolSession(shown)
        curated.active = True
        self.assertIsNone(curated.state()["primary_summary"])
        item = curated_safety_items(curated)[0]
        self.assertEqual(item["source_text"], WARNING)
        self.assertIsNone(item["primary_text"])
        # A passed sentence of the same step is used.
        self.assertEqual(
            shown.localized_fact("step-1", "note_1"), good_korean(NOTE))

    def test_a_row_is_checked_again_when_it_is_read(self) -> None:
        fixture = rich_fixture()
        forged = FactTranslationRecord(
            fixture.revision_id, "step-2/current_step", "ko",
            source_sha256(MINIPREP_STEPS[1]), "한국어 번역: lysozyme 100 µL",
            "machine", "passed", "grok-test", "grok-test", "2026-10-03T00:00:00+00:00")
        self.assertIsNone(
            with_stored_translations(fixture, [forged]).localized_fact(
                "step-2", "current_step"))


class StorageTests(unittest.TestCase):
    """3. One row per sentence, with what made it; old rows still read."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def store(self):
        return initialize_workspace_store(WorkspaceSettings(True, self.root))

    def test_a_row_holds_revision_fact_language_text_status_check_model_time(self) -> None:
        fixture = rich_fixture()
        report = generate(fixture, FakeTranslator(wrong={
            "step-1/warning_1": "한국어 번역: 용해물을 볼텍스합니다."}))
        store = self.store()
        try:
            self.assertEqual(store.record_fact_translations(report.records), 10)
            rows = {row.fact_key: row for row in store.fact_translations(
                fixture.revision_id, "ko")}
        finally:
            store.close()
        row = rows["step-1/sub_action_1"]
        self.assertEqual(row.revision_id, "fictional-miniprep-v1")
        self.assertEqual(row.language, "ko")
        self.assertEqual(row.translated_text, good_korean(SUB_ACTION))
        self.assertEqual(row.status, "machine")
        self.assertEqual(row.check_result, "passed")
        self.assertEqual(row.model, "grok-test")
        self.assertEqual(row.model_version, "grok-test-0001")
        self.assertEqual(row.source_sha256, source_sha256(SUB_ACTION))
        self.assertTrue(row.created_at)
        self.assertEqual(rows["step-1/warning_1"].check_result, "negation_changed")

    def test_the_same_revision_is_translated_once(self) -> None:
        fixture = rich_fixture()
        store = self.store()
        try:
            store.record_fact_translations(generate(fixture, FakeTranslator()).records)
            again = FakeTranslator()
            report = generate(
                fixture, again, store.fact_translations(fixture.revision_id, "ko"))
            self.assertEqual(again.batches, [])
            self.assertEqual(report.skipped_stored, 10)
            self.assertEqual(store.record_fact_translations(report.records), 0)
        finally:
            store.close()

    def test_a_new_revision_is_translated_anew(self) -> None:
        store = self.store()
        try:
            first = rich_fixture()
            store.record_fact_translations(generate(first, FakeTranslator()).records)
            second = rich_fixture("fictional-miniprep-v2")
            translator = FakeTranslator()
            generate(second, translator,
                     store.fact_translations(second.revision_id, "ko"))
            self.assertEqual(sum(len(batch) for batch in translator.batches), 10)
            # The first revision's rows do not reach the second.
            self.assertIsNone(with_stored_translations(
                second, store.fact_translations(first.revision_id, "ko"),
            ).machine_localizations)
        finally:
            store.close()

    def test_a_changed_source_sentence_is_not_given_the_old_translation(self) -> None:
        fixture = rich_fixture()
        records = generate(fixture, FakeTranslator()).records
        protocol = fixture.draft.protocol
        section = protocol.sections[0]
        steps = list(section.steps)
        steps[1] = replace(steps[1], instruction_source_text="2 Add 20 µL lysozyme.")
        changed = replace(fixture, draft=replace(fixture.draft, protocol=replace(
            protocol, sections=(replace(section, steps=tuple(steps)),))))
        self.assertIsNone(with_stored_translations(changed, records).localized_fact(
            "step-2", "current_step"))

    def test_rows_are_append_only(self) -> None:
        fixture = rich_fixture()
        store = self.store()
        try:
            store.record_fact_translations(generate(fixture, FakeTranslator()).records)
            with self.assertRaises(sqlite3.DatabaseError):
                store._connection.execute(
                    "UPDATE protocol_fact_translations SET translated_text='x'")
            with self.assertRaises(sqlite3.DatabaseError):
                store._connection.execute("DELETE FROM protocol_fact_translations")
        finally:
            store.close()

    def test_a_version_6_workspace_migrates_and_keeps_its_old_translations(self) -> None:
        # Build a v6 workspace with one whole-revision translation row.
        store = self.store()
        try:
            store._connection.executescript(
                "DROP TABLE protocol_fact_translations;"
                "DROP TABLE schema_metadata;"
                "CREATE TABLE schema_metadata(schema_version INTEGER PRIMARY KEY "
                "CHECK(schema_version=6));"
                "INSERT INTO schema_metadata VALUES(6);")
            store._connection.execute("PRAGMA foreign_keys=OFF")
            store._connection.execute(
                "INSERT INTO protocol_translations VALUES(?,?,?,?,?,?,?,?,?)",
                ("translation-old", "tenant-a", "revision-a", "ko", "machine",
                 "옛 번역 250 µL", "0" * 64, "principal-a",
                 "2026-08-01T00:00:00+00:00"))
            store._connection.commit()
        finally:
            store.close()
        store = self.store()
        try:
            self.assertEqual(WORKSPACE_SCHEMA_VERSION, 7)
            self.assertEqual(store._connection.execute(
                "SELECT schema_version FROM schema_metadata").fetchone()[0], 7)
            self.assertEqual(store._connection.execute(
                "SELECT content_text FROM protocol_translations "
                "WHERE translation_id='translation-old'").fetchone()[0],
                "옛 번역 250 µL")
            self.assertEqual(store.fact_translations("revision-a", "ko"), ())
        finally:
            store.close()

    def test_a_new_workspace_starts_at_the_current_schema(self) -> None:
        path = self.root / WORKSPACE_DATABASE_FILENAME
        self.assertFalse(path.exists())
        store = self.store()
        try:
            self.assertIsNotNone(store._connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE name='protocol_fact_translations'").fetchone())
        finally:
            store.close()
        self.assertIn("protocol_translations", SCHEMA)


class PickingOrderTests(unittest.TestCase):
    """4. Reviewed Korean, then a stored machine translation, then the source."""

    def test_reviewed_wins_over_machine_and_machine_over_source(self) -> None:
        fixture = replace(rich_fixture(), localizations={
            "step-1/current_step": "1단계: 검토된 번역 250 µL Buffer 1 Tris-HCl buffer"})
        translator = FakeTranslator()
        report = generate(fixture, translator)
        sent = {key for batch in translator.batches for key in batch}
        # A reviewed sentence is not sent to the model at all.
        self.assertNotIn("step-1/current_step", sent)
        self.assertEqual(report.skipped_reviewed, 1)
        shown = with_stored_translations(fixture, report.records)
        self.assertEqual(shown.localized_fact("step-1", "current_step"),
                         "1단계: 검토된 번역 250 µL Buffer 1 Tris-HCl buffer")
        self.assertEqual(shown.localization_source("step-1", "current_step"), "reviewed")
        self.assertEqual(shown.localized_fact("step-2", "current_step"),
                         good_korean(MINIPREP_STEPS[1]))
        self.assertEqual(shown.localization_source("step-2", "current_step"), "machine")
        self.assertIsNone(shown.localized_fact("step-5", "current_step"))
        self.assertIsNone(shown.localization_source("step-5", "current_step"))

    def test_a_stored_reviewed_row_counts_as_reviewed(self) -> None:
        fixture = rich_fixture()
        reviewed = FactTranslationRecord(
            fixture.revision_id, "step-2/current_step", "ko",
            source_sha256(MINIPREP_STEPS[1]), "2단계: 검토자가 고친 번역 10 µL lysozyme 5 times",
            "reviewed", "passed", "reviewer", "reviewer", "2026-10-03T00:00:00+00:00")
        records = [*generate(fixture, FakeTranslator()).records, reviewed]
        shown = with_stored_translations(fixture, records)
        self.assertEqual(shown.localization_source("step-2", "current_step"), "reviewed")
        self.assertEqual(shown.localized_fact("step-2", "current_step"),
                         "2단계: 검토자가 고친 번역 10 µL lysozyme 5 times")

    def test_the_purpose_answer_shows_its_translation_over_the_source(self) -> None:
        fixture = rich_fixture()
        shown = with_stored_translations(fixture, generate(fixture, FakeTranslator()).records)
        curated = CuratedProtocolSession(shown)
        curated.active = True
        from voiney_lab.runtime_routing import route_curated_runtime_turn
        plan = route_curated_runtime_turn(
            curated, "이 실험의 목적이 뭐야?", turn_id=1, language="ko").plan
        self.assertIn(good_korean("A fictional protocol for vocabulary tests."),
                      plan.display_text)
        self.assertNotIn("원문에 적힌 실험 목적입니다.", plan.display_text)
        # Without a translation the source is the body, as before.
        bare = CuratedProtocolSession(fixture)
        bare.active = True
        plan = route_curated_runtime_turn(
            bare, "이 실험의 목적이 뭐야?", turn_id=1, language="ko").plan
        self.assertIn("원문에 적힌 실험 목적입니다.", plan.display_text)


@unittest.skipUnless(
    SOURCE_PDF.is_file(),
    f"requires the externally licensed Candidate A source PDF at {SOURCE_PDF}",
)
class InGelPickingTests(unittest.TestCase):
    """4. In-gel keeps its reviewed sidecar; the model fills only the gaps."""

    def test_reviewed_sentences_are_kept_and_the_purpose_is_filled(self) -> None:
        fixture = in_gel_fixture()
        reviewed = dict(fixture.localizations)
        # "Once" counts as 1 for the check, so a faithful reading says 한 번.
        translator = FakeTranslator(wrong={PURPOSE_FACT_KEY: (
            "시설에서 쓰는 in-gel 프로토콜입니다. 한 번 소화를 마치면 Evotip에 "
            "시료를 올립니다. keratin contamination을 막는 팁은 Warning 절을 "
            "보세요.")})
        report = generate(fixture, translator)
        sent = {key for batch in translator.batches for key in batch}
        self.assertEqual(report.skipped_reviewed, len(reviewed))
        self.assertFalse(sent & set(reviewed))
        self.assertIn(PURPOSE_FACT_KEY, sent)
        shown = with_stored_translations(fixture, report.records)
        for key, text in reviewed.items():
            step_id, fact_id = key.split("/", 1)
            self.assertEqual(shown.localized_fact(step_id, fact_id), text)
            self.assertEqual(shown.localization_source(step_id, fact_id), "reviewed")
        step_id, fact_id = PURPOSE_FACT_KEY.split("/", 1)
        self.assertEqual(shown.localization_source(step_id, fact_id), "machine")


def _turn_events(session, transcript: str, turn_id: int, model=None):
    session.active_turn_id = turn_id
    session.next_turn_id = turn_id + 1
    session.turn_generations[turn_id] = session.generation
    session.detector.state = TurnState.PROCESSING
    spoken: list[str] = []
    socket = _Socket()

    async def immediate(function, *args, **kwargs):
        return function(*args, **kwargs)

    def synthesize(text, language=None):
        spoken.append(text)
        return b"\0\0"

    with patch(
        "voiney_lab.server.transcribe", return_value=Transcription(transcript, "ko"),
    ), patch(
        "voiney_lab.server.synthesize", side_effect=synthesize,
    ), patch(
        "voiney_lab.server.asyncio.to_thread", side_effect=immediate,
    ), patch(
        "voiney_lab.server.AsyncOpenAI",
        model if model is not None else _FakeModel(error=AssertionError("no model call")),
    ), patch(
        "voiney_lab.server.require_env", return_value="offline",
    ):
        asyncio.run(run_turn(socket, session, b"\0\0", turn_id, session.generation))
    reply = next(item for item in socket.text if item["type"] == "reply.delta")
    screen = next(
        item["screen"] for item in socket.text if item["type"] == "protocol.fixture.state"
    )
    return spoken, reply, screen


def _stored_miniprep(keys: tuple[str, ...]):
    """The miniprep with stored machine translations for these keys only."""

    fixture = miniprep_fixture()
    records = [
        record for record in generate(fixture, FakeTranslator()).records
        if record.fact_key in keys
    ]
    return with_stored_translations(fixture, records)


class ScreenSourceTests(unittest.TestCase):
    """5. The page is told once a machine translation has been shown."""

    def test_a_machine_translation_on_the_card_says_machine(self) -> None:
        fixture = _stored_miniprep(("step-1/current_step",))
        curated = CuratedProtocolSession(fixture)
        curated.active = True
        self.assertEqual(curated.state()["primary_summary"],
                         good_korean(MINIPREP_STEPS[0]))
        self.assertEqual(curated_screen_fields(curated)["translation_source"], "machine")
        # It stays said for the session, on a step that has none.
        curated.current_index = 2
        self.assertEqual(curated_screen_fields(curated)["translation_source"], "machine")

    def test_a_machine_translated_warning_says_machine(self) -> None:
        fixture = rich_fixture()
        records = [r for r in generate(fixture, FakeTranslator()).records
                   if r.fact_key == "step-1/warning_1"]
        curated = CuratedProtocolSession(with_stored_translations(fixture, records))
        curated.active = True
        item = curated_safety_items(curated)[0]
        self.assertEqual(item["translation_source"], "machine")
        self.assertEqual(item["primary_text"], good_korean(WARNING))
        self.assertEqual(curated_screen_fields(curated)["translation_source"], "machine")

    def test_reviewed_or_nothing_never_says_machine(self) -> None:
        curated = CuratedProtocolSession(replace(miniprep_fixture(), localizations={
            "step-1/current_step": "1단계: 검토 250 µL Buffer 1 Tris-HCl buffer"}))
        curated.active = True
        self.assertEqual(curated_screen_fields(curated)["translation_source"], "reviewed")
        bare = CuratedProtocolSession(miniprep_fixture())
        bare.active = True
        self.assertEqual(curated_screen_fields(bare)["translation_source"], "none")

    def test_a_reply_showing_a_machine_translation_is_labelled_unreviewed(self) -> None:
        session = _listener(_stored_miniprep(("step-2/current_step",)), 1,
                            model_roles=True)
        spoken, reply, screen = _turn_events(session, "이 단계 읽어줘", 1)
        self.assertEqual(reply["translation_status"], "model_assisted_unreviewed")
        self.assertIn(good_korean(MINIPREP_STEPS[1]), reply["text"])
        self.assertEqual(screen["translation_source"], "machine")


class SpokenLeadTests(unittest.TestCase):
    """6. "자동 번역입니다." is said once a session."""

    def test_stored_readings_are_announced_once(self) -> None:
        session = _listener(
            _stored_miniprep(("step-1/current_step", "step-2/current_step")), 0,
            model_roles=True)
        spoken, _, _ = _turn_events(session, "이 단계 읽어줘", 1)
        self.assertEqual(len(spoken), 1)
        self.assertTrue(spoken[0].startswith("자동 번역입니다. "))
        self.assertIn(good_korean(MINIPREP_STEPS[0]), spoken[0])
        session.curated_protocol_session.current_index = 1
        spoken, _, _ = _turn_events(session, "이 단계 읽어줘", 2)
        self.assertNotIn("자동 번역입니다.", spoken[0])
        self.assertIn(good_korean(MINIPREP_STEPS[1]), spoken[0])

    def test_a_live_reading_after_a_stored_one_is_not_announced_again(self) -> None:
        session = _listener(_stored_miniprep(("step-1/current_step",)), 0,
                            model_roles=True)
        spoken, _, _ = _turn_events(session, "이 단계 읽어줘", 1)
        self.assertTrue(spoken[0].startswith("자동 번역입니다. "))
        session.curated_protocol_session.current_index = 3
        reading = "4단계: DNA를 50 µL water로 용출합니다."
        spoken, reply, _ = _turn_events(
            session, "이 단계 읽어줘", 2, _FakeModel(reading))
        self.assertEqual(spoken, [reading])
        self.assertEqual(reply["translation_status"], "model_assisted_unreviewed")

    def test_a_screen_only_translation_is_not_announced(self) -> None:
        session = _listener(_stored_miniprep(("step-1/current_step",)), 0,
                            model_roles=True)
        spoken, _, screen = _turn_events(session, "현재 단계 알려줘", 1)
        self.assertEqual(spoken, ["현재 1단계입니다. 안내를 화면에 표시했습니다."])
        self.assertEqual(screen["translation_source"], "machine")
        self.assertFalse(session.auto_translation_announced)


class LiveTranslationTests(unittest.TestCase):
    """7. A stored sentence is not translated again per turn; others are."""

    def test_a_stored_sentence_makes_no_model_call(self) -> None:
        session = _listener(_stored_miniprep(("step-1/current_step",)), 0,
                            model_roles=True)
        model = _FakeModel(error=AssertionError("no model call"))
        spoken, reply, _ = _turn_events(session, "이 단계 읽어줘", 1, model)
        self.assertEqual(model.calls, [])
        self.assertNotIn("확인하지 못해", spoken[0])
        self.assertIn(good_korean(MINIPREP_STEPS[0]), spoken[0])

    def test_a_sentence_without_one_is_translated_per_turn_as_before(self) -> None:
        session = _listener(_stored_miniprep(("step-1/current_step",)), 3,
                            model_roles=True)
        reading = "4단계: DNA를 50 µL water로 용출합니다."
        model = _FakeModel(reading)
        spoken, _, _ = _turn_events(session, "이 단계 읽어줘", 1, model)
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(spoken, ["자동 번역입니다. " + reading])


class ActivationTests(unittest.TestCase):
    """1 and 8. Made when a revision becomes executable, with the reader model."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = initialize_protocol_store(
            ProtocolPersistenceSettings(True, self.root / "catalog"))
        self.calls: list[str] = []
        self.catalog = ProtocolCatalog(
            self.store,
            on_execution_authorized=lambda catalog, pid: self.calls.append(pid))
        self.sample_pdf = self.root / "sample.pdf"
        write_text_pdf(
            self.sample_pdf,
            "Protocol Test\nSection preparation\n1. Add solution.\nWear gloves.",
            title="Protocol Test",
        )
        registration = self.catalog.register(
            self.sample_pdf, source_filename="sample.pdf",
            media_type="application/pdf")
        self.protocol_id = registration.entry.protocol_id
        draft = analysis_draft(self.sample_pdf, self.protocol_id, "Protocol Test")
        self.store.append_analysis_revision(
            self.protocol_id, 1, f"analysis-{registration.entry.source_sha256[:24]}",
            draft.protocol, draft.readiness, draft.capability_policy_id)
        self.catalog.acknowledge_readiness_gate(
            self.protocol_id, "pdf-1-analysis-1",
            reason_code=domain.ReadinessReasonCode.NO_DECLARED_SAFETY_WARNINGS.value,
            actor_principal_id="reviewer@example.org", actor_role="reviewer",
            comment="Warnings reviewed against the source.")

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def test_activation_and_approval_each_hand_over_the_revision(self) -> None:
        self.assertEqual(self.calls, [])
        self.catalog.activate_development(self.protocol_id)
        self.assertEqual(self.calls, [self.protocol_id])
        self.catalog.approve(
            self.protocol_id, "pdf-1-analysis-1",
            policy=SharedSecretApprovalPolicy("secret"), presented_secret="secret")
        self.assertEqual(self.calls, [self.protocol_id, self.protocol_id])

    def test_a_failed_translation_never_undoes_the_activation(self) -> None:
        def broken(catalog, protocol_id):
            raise RuntimeError("provider down")

        self.catalog.on_execution_authorized = broken
        entry = self.catalog.activate_development(self.protocol_id)
        self.assertTrue(entry.available_for_execution)

    def test_the_server_translates_the_fixture_sessions_run(self) -> None:
        seen = []
        self.catalog.activate_development(self.protocol_id)
        saved = getattr(server_module.app.state, "revision_translation_runner", None)
        server_module.app.state.revision_translation_runner = seen.append
        try:
            with patch.object(server_module, "server_config"), patch.object(
                server_module, "_configured_candidate_fixture", return_value=None,
            ):
                server_module._translate_authorized_revision(
                    self.catalog, self.protocol_id)
        finally:
            server_module.app.state.revision_translation_runner = saved
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].revision_id,
                         self.catalog.get_entry(self.protocol_id).revision_id)

    def test_without_the_workspace_or_the_model_role_nothing_runs(self) -> None:
        with patch.dict(os.environ, {
            "VOICE_WORKFLOW_AGENT_WORKSPACE_ENABLED": "false",
            "VOICE_WORKFLOW_AGENT_MULTI_BRAIN_ENABLED": "true",
            "XAI_API_KEY": "offline",
        }), patch.object(server_module, "_start_revision_translation") as start:
            server_module._translate_authorized_revision(self.catalog, self.protocol_id)
        start.assert_not_called()

    def test_generation_is_stored_through_the_workspace(self) -> None:
        workspace = self.root / "workspace"
        fixture = rich_fixture()
        translator = FakeTranslator()
        with patch.dict(os.environ, {
            "VOICE_WORKFLOW_AGENT_WORKSPACE_ENABLED": "true",
            "VOICE_WORKFLOW_AGENT_WORKSPACE_DATA_DIR": str(workspace),
        }):
            report = asyncio.run(server_module.store_revision_translations(
                fixture, translator, model="grok-test"))
            attached = server_module._with_revision_translations(fixture)
        self.assertEqual(report.calls, 2)
        self.assertEqual(attached.localized_fact("step-2", "current_step"),
                         good_korean(MINIPREP_STEPS[1]))

    def test_the_batch_call_uses_the_reader_model_settings(self) -> None:
        model = _BatchModel()
        translate = openai_batch_translator(lambda: model, "grok-answer")
        unit = TranslationUnit("step-1/current_step", "step", MINIPREP_STEPS[0])
        result = asyncio.run(translate([unit]))
        self.assertEqual(model.calls[0]["model"], "grok-answer")
        self.assertEqual(model.calls[0]["temperature"], 0)
        request = json.loads(model.calls[0]["messages"][1]["content"])
        self.assertEqual(request, {"items": [
            {"id": "step-1/current_step", "source_text": MINIPREP_STEPS[0]}]})
        self.assertEqual(result.translations, {"step-1/current_step": "번역"})
        self.assertEqual((result.prompt_tokens, result.completion_tokens), (12, 3))
        self.assertEqual(result.model_version, "grok-answer-0928")

    def test_a_failed_batch_stores_nothing_so_it_is_tried_again(self) -> None:
        report = generate(rich_fixture(), FakeTranslator(error=TimeoutError()))
        self.assertEqual(report.failed_calls, 2)
        self.assertEqual(report.records, [])


class _BatchModel:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        from types import SimpleNamespace
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        from types import SimpleNamespace
        self.calls.append(kwargs)
        return SimpleNamespace(
            model="grok-answer-0928",
            usage=SimpleNamespace(prompt_tokens=12, completion_tokens=3),
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(
                {"items": [{"id": "step-1/current_step", "korean": "번역"}]},
                ensure_ascii=False)))],
        )


if __name__ == "__main__":
    unittest.main()
