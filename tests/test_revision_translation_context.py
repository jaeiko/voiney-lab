"""Revision translations read the whole protocol and arrive as they are made.

Decided 2026-10-03 (PR #24): a glossary is made once per revision from the
whole protocol and stored with it; every sentence call carries the title, a
short purpose, the glossary and the sentence's step with the steps either
side, in the reviewed in-gel style. Sentences go purpose and safety first,
then the steps open sessions are on, then step order; each batch is stored
and put on open sessions at once, without touching workflow state. A
session opening on a revision with nothing stored starts the generation
once. Every model here is fake.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from voiney_lab.curated_protocol import PURPOSE_FACT_KEY, CuratedProtocolSession
from voiney_lab.protocol_translation import (
    REVISION_TRANSLATION_PROMPT,
    STYLE_GUIDE,
    GlossaryEntry,
    GlossaryResult,
    generate_revision_translations,
    generation_order,
    openai_glossary_maker,
    protocol_context,
    translation_units,
)
import voiney_lab.server as server_module
from voiney_lab.workspace_store import (
    TranslationGlossaryRecord,
    WorkspaceSettings,
    initialize_workspace_store,
)

from tests.protocol_vocabulary_support import MINIPREP_STEPS, miniprep_fixture
from tests.test_revision_translations import (
    FakeTranslator,
    good_korean,
    rich_fixture,
)

GLOSSARY = (
    GlossaryEntry("Tris-HCl buffer", "Tris-HCl buffer", True),
    GlossaryEntry("water", "물", False),
)


class FakeGlossary:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list = []
        self.error = error

    async def __call__(self, context):
        self.calls.append(context)
        if self.error is not None:
            raise self.error
        return GlossaryResult(GLOSSARY, "grok-test-0001", 500, 80)


def run(coroutine):
    return asyncio.run(coroutine)


class GlossaryTests(unittest.TestCase):
    """2a. One glossary per revision, from the whole protocol, stored."""

    def test_the_glossary_reads_the_whole_protocol_once(self) -> None:
        maker = FakeGlossary()
        stored: list[TranslationGlossaryRecord] = []
        report = run(generate_revision_translations(
            rich_fixture(), FakeTranslator(), model="grok-test",
            make_glossary=maker, on_glossary=stored.append))
        self.assertEqual(len(maker.calls), 1)
        context = maker.calls[0]
        self.assertEqual(context.title, "Fictional plasmid miniprep")
        self.assertEqual(context.purpose, "A fictional protocol for vocabulary tests.")
        self.assertEqual(len(context.steps), 5)
        self.assertIn("Lysozyme Thermo Fisher Scientific Catalog #89833", context.materials)
        whole = context.whole_text()
        for text in MINIPREP_STEPS[:4]:
            self.assertIn(" ".join(text.split()[1:]), whole)
        self.assertEqual(report.glossary_calls, 1)
        self.assertEqual(report.glossary, GLOSSARY)
        self.assertEqual(len(stored), 1)
        self.assertEqual(json.loads(stored[0].entries_json), [
            {"source": "Tris-HCl buffer", "korean": "Tris-HCl buffer", "keep_english": True},
            {"source": "water", "korean": "물", "keep_english": False},
        ])
        self.assertEqual(stored[0].revision_id, "fictional-miniprep-v1")

    def test_a_stored_glossary_is_used_and_not_made_again(self) -> None:
        maker = FakeGlossary()
        translator = FakeTranslator()
        held = TranslationGlossaryRecord(
            "fictional-miniprep-v1", "ko",
            json.dumps([{"source": "PBS", "korean": "PBS", "keep_english": True}]),
            "grok-test", "grok-test", "2026-10-03T00:00:00+00:00")
        run(generate_revision_translations(
            rich_fixture(), translator, model="grok-test",
            make_glossary=maker, glossary=held))
        self.assertEqual(maker.calls, [])
        self.assertEqual(translator.contexts[0]["glossary"], [
            {"source": "PBS", "korean": "PBS", "keep_english": True}])

    def test_a_failed_glossary_call_still_translates_the_sentences(self) -> None:
        translator = FakeTranslator()
        report = run(generate_revision_translations(
            rich_fixture(), translator, model="grok-test",
            make_glossary=FakeGlossary(error=TimeoutError())))
        self.assertIsNone(report.glossary_record)
        self.assertEqual(report.passed, 10)
        self.assertEqual(translator.contexts[0]["glossary"], [])

    def test_nothing_to_translate_makes_no_glossary_call(self) -> None:
        fixture = rich_fixture()
        first = run(generate_revision_translations(
            fixture, FakeTranslator(), model="grok-test"))
        maker = FakeGlossary()
        run(generate_revision_translations(
            fixture, FakeTranslator(), model="grok-test",
            stored=first.records, make_glossary=maker))
        self.assertEqual(maker.calls, [])

    def test_the_glossary_is_stored_once_per_revision_and_append_only(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            store = initialize_workspace_store(WorkspaceSettings(True, Path(root)))
            try:
                record = TranslationGlossaryRecord(
                    "rev-1", "ko", json.dumps([]), "m", "m", "2026-10-03T00:00:00+00:00")
                self.assertTrue(store.record_translation_glossary(record))
                self.assertFalse(store.record_translation_glossary(record))
                self.assertEqual(store.translation_glossary("rev-1", "ko"), record)
                self.assertIsNone(store.translation_glossary("rev-2", "ko"))
                with self.assertRaises(sqlite3.DatabaseError):
                    store._connection.execute(
                        "UPDATE protocol_translation_glossaries SET entries_json='[]'")
            finally:
                store.close()

    def test_the_glossary_call_asks_for_names_in_english_and_words_in_korean(self) -> None:
        model = _Model({"entries": [
            {"source": "Tris-HCl buffer", "korean": "Tris-HCl buffer", "keep_english": True},
            {"source": "water", "korean": "물", "keep_english": False},
            {"source": "Water", "korean": "물", "keep_english": False},
        ]})
        result = run(openai_glossary_maker(lambda: model, "grok-answer")(
            protocol_context(miniprep_fixture())))
        self.assertEqual(result.entries, GLOSSARY)
        call = model.calls[0]
        self.assertEqual(call["model"], "grok-answer")
        self.assertIn("keep_english", call["messages"][0]["content"])
        request = json.loads(call["messages"][1]["content"])
        self.assertEqual(request["title"], "Fictional plasmid miniprep")
        self.assertIn("Resuspend the cell pellet", request["protocol"])


class SentenceContextTests(unittest.TestCase):
    """2b-c. A sentence call carries the protocol around it, in one style."""

    def test_a_call_carries_title_purpose_glossary_and_neighbouring_steps(self) -> None:
        translator = FakeTranslator()
        run(generate_revision_translations(
            miniprep_fixture(), translator, model="grok-test",
            make_glossary=FakeGlossary(), priority_steps=lambda: (2,)))
        # The first batch carries the purpose and steps 3-4 (index 2, 3 first).
        context = translator.contexts[0]
        self.assertEqual(context["title"], "Fictional plasmid miniprep")
        self.assertEqual(context["purpose_summary"],
                         "A fictional protocol for vocabulary tests.")
        self.assertEqual(context["glossary"][1],
                         {"source": "water", "korean": "물", "keep_english": False})
        labels = [step["step_label"] for step in context["steps"]]
        self.assertEqual(labels, ["1", "2", "3", "4", "5"])
        self.assertIn("Wash the column", context["steps"][2]["text"])

    def test_only_the_steps_around_a_batch_are_sent(self) -> None:
        context = protocol_context(miniprep_fixture())
        self.assertEqual(
            [step["step_label"] for step in context.neighbourhood([3])], ["3", "4", "5"])
        self.assertEqual(
            [step["step_label"] for step in context.neighbourhood([0])], ["1", "2"])

    def test_the_style_is_the_reviewed_in_gel_style(self) -> None:
        for phrase in ("합니다체", "'당신'", "'단계: '", "'예상 결과: '",
                       "original English spelling", "Write ordinary words in Korean",
                       "exactly as written"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, STYLE_GUIDE)
        self.assertIn(STYLE_GUIDE, REVISION_TRANSLATION_PROMPT)
        self.assertIn("not word by word", REVISION_TRANSLATION_PROMPT)


class OrderTests(unittest.TestCase):
    """3a. Purpose and safety first, then the open sessions' steps, then order."""

    def test_purpose_and_safety_come_first_then_step_order(self) -> None:
        order = [unit.fact_key for unit in generation_order(
            translation_units(rich_fixture()))]
        self.assertEqual(order[:3], [
            PURPOSE_FACT_KEY, "step-1/warning_1", "step-1/note_1"])
        self.assertEqual(order[3:], [
            "step-1/current_step", "step-1/sub_action_1",
            "step-1/expected_result_1", "step-1/prerequisite_1",
            "step-2/current_step", "step-3/current_step", "step-4/current_step",
            "step-5/current_step"])

    def test_an_open_session_moves_its_current_and_next_step_forward(self) -> None:
        order = [unit.fact_key for unit in generation_order(
            translation_units(rich_fixture()), (3, 4))]
        self.assertEqual(order[3:5], ["step-4/current_step", "step-5/current_step"])

    def test_priority_is_asked_before_every_batch(self) -> None:
        asked: list[int] = []

        def priority():
            asked.append(1)
            return (4,) if len(asked) > 1 else ()

        translator = FakeTranslator()
        fixture = rich_fixture()
        report = run(generate_revision_translations(
            fixture, translator, model="grok-test", priority_steps=priority))
        self.assertEqual(len(asked), 2)
        self.assertEqual(len(report.order), 10)


def _session_on(fixture, *, active=True, index=0):
    session = server_module.ListenerSession()
    session.set_curated_protocol_fixture(fixture)
    session.active = active
    session.accept_configuration(7, "cascade", "ko", fixture.protocol_id)
    session.curated_protocol_session.active = True
    session.curated_protocol_session.current_index = index
    return session


class _Sender:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []

    async def text(self, kind, **fields):
        self.sent.append((kind, fields))


class OpenSessionTests(unittest.TestCase):
    """3b and 3d. A stored batch reaches an open session's card at once."""

    def tearDown(self) -> None:
        server_module._TRANSLATION_SUBSCRIBERS.clear()

    def test_each_batch_redraws_the_open_card_and_changes_no_workflow_state(self) -> None:
        fixture = rich_fixture()
        session = _session_on(fixture, index=1)
        sender = _Sender()
        curated = session.curated_protocol_session
        before = curated.state()

        async def go():
            server_module._subscribe_translations(session, sender)
            self.assertEqual(
                server_module._open_session_steps(fixture.revision_id), [1, 2])
            report = await generate_revision_translations(
                fixture, FakeTranslator(), model="grok-test",
                priority_steps=lambda: server_module._open_session_steps(
                    fixture.revision_id),
                on_batch=lambda rows: server_module._publish_translations(
                    fixture.revision_id, rows))
            return report

        report = run(go())
        # The session's step (2) was sent in the first batch, with the purpose
        # and the safety lines.
        self.assertIn("step-2/current_step", report.order[:8])
        self.assertEqual(len(sender.sent), 2)
        kind, fields = sender.sent[0]
        self.assertEqual(kind, "protocol.fixture.state")
        self.assertEqual(fields["action"], "translation_update")
        self.assertEqual(fields["state"]["primary_summary"],
                         good_korean(MINIPREP_STEPS[1]))
        self.assertEqual(fields["screen"]["translation_source"], "machine")
        after = curated.state()
        for key in ("revision", "active", "current_step_id", "workflow_status",
                    "block_reason"):
            with self.subTest(key=key):
                self.assertEqual(after.get(key), before.get(key))

    def test_another_revision_or_a_closed_session_is_left_alone(self) -> None:
        fixture = rich_fixture()
        other = _session_on(rich_fixture("fictional-miniprep-v9"))
        closed = _session_on(fixture)
        senders = (_Sender(), _Sender())

        async def go():
            server_module._subscribe_translations(other, senders[0])
            server_module._subscribe_translations(closed, senders[1])
            server_module._unsubscribe_translations(closed)
            report = await generate_revision_translations(
                fixture, FakeTranslator(), model="grok-test")
            await server_module._publish_translations(fixture.revision_id, report.records)

        run(go())
        self.assertEqual(senders[0].sent, [])
        self.assertEqual(senders[1].sent, [])
        self.assertIsNone(other.curated_protocol_session.fixture.machine_localizations)
        self.assertIsNone(closed.curated_protocol_session.fixture.machine_localizations)

    def test_a_sentence_not_yet_translated_keeps_its_source(self) -> None:
        fixture = rich_fixture()
        session = _session_on(fixture, index=4)
        first = run(generate_revision_translations(
            fixture, FakeTranslator(), model="grok-test")).records[:8]

        async def go():
            server_module._subscribe_translations(session, _Sender())
            await server_module._publish_translations(fixture.revision_id, first)

        run(go())
        shown = session.curated_protocol_session.fixture
        self.assertIsNotNone(shown.localized_fact("step-1", "current_step"))
        self.assertIsNone(shown.localized_fact("step-4", "current_step"))


class SessionStartTests(unittest.TestCase):
    """3c. A session on a revision with nothing stored starts it, once."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            "VOINEY_LAB_WORKSPACE_ENABLED": "true",
            "VOINEY_LAB_WORKSPACE_DATA_DIR": self.temp.name,
        })
        self.env.start()
        self.started: list[str] = []
        self.saved = getattr(server_module.app.state, "revision_translation_runner", None)
        server_module.app.state.revision_translation_runner = (
            lambda fixture: self.started.append(fixture.revision_id))

    def tearDown(self) -> None:
        server_module.app.state.revision_translation_runner = self.saved
        server_module._REVISION_TRANSLATIONS_RUNNING.clear()
        self.env.stop()
        self.temp.cleanup()

    def test_nothing_stored_and_not_running_starts_it(self) -> None:
        fixture = rich_fixture()
        self.assertIs(server_module._with_revision_translations(fixture), fixture)
        self.assertEqual(self.started, ["fictional-miniprep-v1"])

    def test_a_running_generation_is_not_started_twice(self) -> None:
        server_module._REVISION_TRANSLATIONS_RUNNING.add("fictional-miniprep-v1")
        server_module._with_revision_translations(rich_fixture())
        self.assertEqual(self.started, [])

    def test_a_revision_with_stored_rows_is_not_started_again(self) -> None:
        fixture = rich_fixture()
        run(server_module.store_revision_translations(
            fixture, FakeTranslator(), model="grok-test", make_glossary=FakeGlossary()))
        attached = server_module._with_revision_translations(fixture)
        self.assertEqual(self.started, [])
        self.assertEqual(attached.localized_fact("step-2", "current_step"),
                         good_korean(MINIPREP_STEPS[1]))
        store = initialize_workspace_store(WorkspaceSettings(True, Path(self.temp.name)))
        try:
            self.assertIsNotNone(store.translation_glossary(fixture.revision_id, "ko"))
        finally:
            store.close()

    def test_without_the_model_role_nothing_starts(self) -> None:
        server_module.app.state.revision_translation_runner = None
        # Lane F, decision 1: the model role is the translation role's key,
        # not the answer brain -- set apart from a repository .env's key.
        with patch.dict(os.environ, {
            "VOINEY_LAB_MULTI_BRAIN_ENABLED": "false",
            "VOINEY_LAB_ANSWER_BRAIN_ENABLED": "false",
            "VOINEY_LAB_TRANSLATION_PROVIDER": "xai",
            "XAI_API_KEY": "",
        }), patch.object(server_module, "_start_revision_translation") as start:
            server_module._with_revision_translations(rich_fixture())
        start.assert_not_called()


class _Model:
    def __init__(self, payload) -> None:
        from types import SimpleNamespace
        self.payload = payload
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        from types import SimpleNamespace
        self.calls.append(kwargs)
        return SimpleNamespace(
            model="grok-answer-0928",
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
            choices=[SimpleNamespace(message=SimpleNamespace(
                content=json.dumps(self.payload, ensure_ascii=False)))],
        )


if __name__ == "__main__":
    unittest.main()
