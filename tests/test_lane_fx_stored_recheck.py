"""Stored Korean is judged by today's check when it is read (lane FX, decision 2).

Human decision of 2026-10-07: a stored sentence translation is used when
today's ``check_translation`` passes it, and its source is shown when today's
check refuses it -- whatever the row's ``check_result`` said when it was
stored. Nothing stored changes: not the row, its text, its verdict or its
source hash; only the judgement and what is shown.

Before it, a row was used only when it had passed both when it was stored
and again when read. A check fixed later therefore never reached a revision
already translated (lane AN: headspace step 21 on EC2 stayed in English),
while a row passed by an older, looser check was still dropped only because
today's check happened to run as well. The judgement is computed once per
revision and sentence: the status line reads it every second.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from tests.protocol_vocabulary_support import MINIPREP_STEPS
from tests.test_revision_translations import FakeTranslator, good_korean, rich_fixture
from voiney_lab import protocol_translation
from voiney_lab import server as server_module
from voiney_lab.curated_protocol import CuratedProtocolSession, reader_translation_issue
from voiney_lab.protocol_translation import (
    GlossaryEntry,
    generate_revision_translations,
    source_sha256,
    translation_revision_key,
    with_stored_translations,
)
from voiney_lab.workspace_store import (
    FactTranslationRecord,
    WorkspaceSettings,
    initialize_workspace_store,
)

#: The step-1 warning of ``rich_fixture``; its Korean below drops the "not"
#: behind 관계없이, which the check of 2026-10-06 passed (decision 1).
WARNING = "Do not vortex the lysate."
DROPPED = "한국어 번역: 양과 관계없이 용해물을 볼텍스합니다."


def row(fixture, fact_key: str, source: str, korean: str, check: str,
        status: str = "machine") -> FactTranslationRecord:
    return FactTranslationRecord(
        translation_revision_key(fixture), fact_key, "ko", source_sha256(source), korean,
        status, check, "fake", "fake", "2026-10-06T00:00:00+00:00")


def generated(fixture) -> list[FactTranslationRecord]:
    return list(asyncio.run(generate_revision_translations(
        fixture, FakeTranslator(), model="fake")).records)


class TodayDecidesTests(unittest.TestCase):
    def test_a_row_refused_when_stored_is_shown_when_today_passes_it(self) -> None:
        fixture = rich_fixture()
        korean = good_korean(MINIPREP_STEPS[1])
        stored = row(fixture, "step-2/current_step", MINIPREP_STEPS[1], korean, "negation_changed")
        shown = with_stored_translations(fixture, [stored])
        self.assertEqual(shown.localized_fact("step-2", "current_step"), korean)
        self.assertEqual(shown.localization_source("step-2", "current_step"), "machine")
        curated = CuratedProtocolSession(shown)
        curated.active = True
        curated.current_index = 1
        self.assertEqual(curated.state()["primary_summary"], korean)

    def test_a_reviewed_row_refused_when_stored_is_used_the_same_way(self) -> None:
        fixture = rich_fixture()
        korean = good_korean(MINIPREP_STEPS[1])
        stored = row(fixture, "step-2/current_step", MINIPREP_STEPS[1], korean,
                     "term_missing", status="reviewed")
        shown = with_stored_translations(fixture, [stored])
        self.assertEqual(shown.localization_source("step-2", "current_step"), "reviewed")

    def test_a_row_passed_when_stored_is_the_source_when_today_refuses_it(self) -> None:
        fixture = rich_fixture()
        self.assertEqual(
            reader_translation_issue(WARNING, DROPPED), "negation_changed")
        stored = row(fixture, "step-1/warning_1", WARNING, DROPPED, "passed")
        shown = with_stored_translations(fixture, [stored])
        self.assertIsNone(shown.localized_fact("step-1", "warning_1"))
        curated = CuratedProtocolSession(shown)
        curated.active = True
        item = server_module.curated_safety_items(curated)[0]
        self.assertEqual(item["source_text"], WARNING)
        self.assertIsNone(item["primary_text"])

    def test_a_row_for_another_sentence_or_revision_is_still_not_used(self) -> None:
        fixture = rich_fixture()
        korean = good_korean(MINIPREP_STEPS[1])
        other_source = row(fixture, "step-2/current_step", MINIPREP_STEPS[2], korean, "passed")
        other_revision = replace(
            row(fixture, "step-2/current_step", MINIPREP_STEPS[1], korean, "passed"),
            revision_id="another-revision")
        shown = with_stored_translations(fixture, [other_source, other_revision])
        self.assertIsNone(shown.localized_fact("step-2", "current_step"))


class NothingStoredChangesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name) / "workspace"
        self.env = patch.dict(os.environ, {
            "VOINEY_LAB_WORKSPACE_ENABLED": "true",
            "VOINEY_LAB_WORKSPACE_DATA_DIR": str(self.workspace),
        })
        self.env.start()
        self.saved_runner = getattr(server_module.app.state, "revision_translation_runner", None)
        self.started: list[object] = []
        server_module.app.state.revision_translation_runner = self.started.append

    def tearDown(self) -> None:
        server_module.app.state.revision_translation_runner = self.saved_runner
        self.env.stop()
        self.temp.cleanup()

    def stored(self, fixture, rows) -> tuple:
        store = initialize_workspace_store(WorkspaceSettings(True, self.workspace))
        try:
            if rows:
                store.record_fact_translations(rows)
            return store.fact_translations(translation_revision_key(fixture), "ko")
        finally:
            store.close()

    def rows(self, fixture) -> list[FactTranslationRecord]:
        records = generated(fixture)
        for index, record in enumerate(records):
            if record.fact_key == "step-2/current_step":
                records[index] = replace(record, check_result="negation_changed")
            elif record.fact_key == "step-3/current_step":
                records[index] = replace(
                    record, translated_text=record.translated_text.replace("50", "5"),
                    check_result="quantities_changed")
            elif record.fact_key == "step-1/warning_1":
                records[index] = replace(record, translated_text=DROPPED, check_result="passed")
        return records

    def test_reading_leaves_every_stored_row_as_it_was(self) -> None:
        fixture = rich_fixture()
        before = self.stored(fixture, self.rows(fixture))
        attached = server_module._with_revision_translations(fixture)
        self.assertEqual(
            attached.localized_fact("step-2", "current_step"), good_korean(MINIPREP_STEPS[1]))
        self.assertIsNone(attached.localized_fact("step-1", "warning_1"))
        self.assertEqual(self.stored(fixture, []), before)
        self.assertEqual(self.started, [])
        # The fixture's own source text is the source still.
        self.assertEqual(attached.steps[1].instruction_source_text, MINIPREP_STEPS[1])

    def test_the_status_line_counts_what_today_refuses(self) -> None:
        fixture = rich_fixture()
        self.stored(fixture, self.rows(fixture))

        class Catalog:
            def load_analysis_fixture(self, protocol_id):
                return fixture

        progress = server_module._translation_progress(Catalog(), "protocol-x")
        # step-3 (a number changed) and the step-1 warning (negation dropped)
        # are refused today; step-2, refused when stored, passes today.
        self.assertEqual(progress["refused"], 2)
        self.assertEqual(progress["state"], "done")
        steps = len(fixture.steps)
        self.assertEqual(progress["steps_korean"], steps - 1)
        self.assertEqual(
            progress["message"],
            f"번역 끝 · 한국어 단계 {steps - 1}/{steps} · 검사 실패 2문장은 원문으로 보입니다")


class JudgedOnceTests(unittest.TestCase):
    def test_a_sentence_is_judged_once_per_revision_and_reading(self) -> None:
        fixture = rich_fixture("lane-fx-judged-once")
        records = [replace(record, translated_text=record.translated_text + " (lane FX)")
                   for record in generated(fixture)]
        spy = patch.object(
            protocol_translation, "reader_translation_issue", wraps=reader_translation_issue)
        with spy as calls:
            first = with_stored_translations(fixture, records)
            judged = calls.call_count
            again = with_stored_translations(fixture, records)
            self.assertEqual(calls.call_count, judged)
            # Another glossary is another judgement.
            with_stored_translations(fixture, records, (GlossaryEntry("lysozyme", "리소자임", False),))
            self.assertGreater(calls.call_count, judged)
        self.assertEqual(judged, len(records))
        self.assertEqual(first.machine_localizations, again.machine_localizations)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
