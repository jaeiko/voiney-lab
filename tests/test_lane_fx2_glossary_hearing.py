"""Lane FX2, decision 3 (2026-10-12): a protocol's own glossary Korean is heard as its term.

The Korean sentences of a revision come with a glossary (protocol id and
revision together, lane PX): "shaker table" is "진탕기", "heating block" is
"히팅 블록". The step card shows those Korean names, but the voice heard a
term only by its English, its sound or the lab lexicon, so "진탕기가 뭐야?"
was answered as off topic. Now the glossary's source ↔ Korean pairs ride on
the fixture with the stored translations (``with_stored_translations``) and
``ProtocolVocabulary`` hears each Korean name -- with or without its spaces,
followed by a particle -- as the term its source names. A name the glossary
does not hold stays as before, as does one whose source is no word of the
protocol; a one-syllable name and a verb form are not taken; and the names
change nothing the translation check reads.

The protocol is fictional, built from text alone, so the tests run in both
pytest baselines.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import voiney_lab.server as server_module
from tests.protocol_vocabulary_support import build_fixture
from voiney_lab.curated_protocol import (
    CuratedProtocolSession,
    ProtocolVocabulary,
    _semantic_utterance_key,
)
from voiney_lab.protocol_translation import (
    GlossaryEntry,
    translation_revision_key,
    translation_units,
    with_stored_translations,
)
from voiney_lab.runtime_routing import route_curated_runtime_turn
from voiney_lab.workspace_store import (
    TranslationGlossaryRecord,
    WorkspaceSettings,
    initialize_workspace_store,
)

GLOSSARY = (
    GlossaryEntry("shaker table", "진탕기(shaker table)", True),
    GlossaryEntry("Heating blocks", "히팅 블록", False),
    GlossaryEntry("reagent water", "시약수(Reagent water)", True),
    GlossaryEntry("fridge", "냉장고", False),
    # Not a word of this protocol: nothing to hear it as.
    GlossaryEntry("autoclave", "고압증기멸균기", False),
    # One syllable, and a verb: not names.
    GlossaryEntry("water", "물", False),
    GlossaryEntry("Weigh", "칭량하다", False),
)


def soil_fixture():
    return build_fixture(
        protocol_id="lane-fx2-soil", title="Soil extraction with a shaker table (fictional)",
        steps=(
            "1 Weigh 3 g of soil into a 125 mL Erlenmeyer flask.",
            "2 Add 30 mL of Ringer's solution made with reagent water.",
            "3 Shake on the shaker table for 5 minutes.",
            "4 Warm the extract on the heating block at 40 °C for 10 minutes.",
            "5 Keep the filtrate in the fridge.",
        ),
        materials=("Ringer's solution", "Reagent water"),
        equipment=("Shaker table", "Heating block", "Erlenmeyer flask"),
    )


def heard(vocabulary: ProtocolVocabulary, said: str) -> list[str]:
    _key, spans, _corrections = vocabulary.hear(_semantic_utterance_key(said))
    return [entity for _start, _end, entity in spans]


class GlossaryHearingTests(unittest.TestCase):

    def setUp(self) -> None:
        self.plain = soil_fixture()
        self.fixture = with_stored_translations(self.plain, (), GLOSSARY)

    def test_the_glossary_rides_on_the_fixture_with_the_translations(self) -> None:
        self.assertIsNot(self.fixture, self.plain)
        self.assertIn(("shaker table", "진탕기(shaker table)"), self.fixture.translation_glossary)
        # Attached again, it is the same fixture.
        self.assertIs(with_stored_translations(self.fixture, (), GLOSSARY), self.fixture)

    def test_a_glossary_korean_name_is_heard_as_its_term(self) -> None:
        vocabulary = ProtocolVocabulary.from_fixture(self.fixture)
        for said, entity in (
            ("진탕기가 뭐야?", "shaker table"),
            ("진탕 기 어디 있어?", "shaker table"),
            ("히팅 블록은 몇 도로 맞춰?", "heating block"),
            ("히팅블록 켜야 돼?", "heating block"),
            ("시약수가 뭐야?", "reagent water"),
            ("냉장고에 넣어?", "fridge"),
        ):
            with self.subTest(said=said):
                self.assertIn(entity, heard(vocabulary, said))
                # Without the glossary it was not heard.
                self.assertNotIn(entity, heard(ProtocolVocabulary.from_fixture(self.plain), said))

    def test_names_the_glossary_cannot_give_are_not_heard(self) -> None:
        vocabulary = ProtocolVocabulary.from_fixture(self.fixture)
        for said in ("고압증기멸균기가 뭐야?", "물이 뭐야?", "물질이 뭐야?", "칭량하다", "온도계가 뭐야?"):
            with self.subTest(said=said):
                self.assertEqual(heard(vocabulary, said), heard(
                    ProtocolVocabulary.from_fixture(self.plain), said))
        # A name inside a longer word is not the name.
        self.assertNotIn("fridge", heard(vocabulary, "김치냉장고가 뭐야?"))

    def test_a_glossary_name_is_kept_whole_not_heard_as_a_shorter_term(self) -> None:
        # "플라스크가" alone sounds like the term "flask"; said after "삼각"
        # it is the glossary's name of the Erlenmeyer flask.
        plain = build_fixture(
            protocol_id="lane-fx2-flasks", title="Soil extraction (fictional)",
            steps=(
                "1 Weigh 3 g of soil into a 125 mL Erlenmeyer flask.",
                "2 Rinse the flask with reagent water.",
            ),
            materials=("Reagent water",), equipment=("Erlenmeyer flask", "Flask"),
        )
        glossary = (GlossaryEntry("Erlenmeyer flask", "삼각플라스크(Erlenmeyer flask)", True),)
        before = heard(ProtocolVocabulary.from_fixture(plain), "삼각 플라스크가 뭐야?")
        after = heard(
            ProtocolVocabulary.from_fixture(with_stored_translations(plain, (), glossary)),
            "삼각 플라스크가 뭐야?",
        )
        self.assertNotIn("erlenmeyer_flask", before)
        self.assertEqual(after[0], "erlenmeyer_flask")

    def test_the_session_answers_from_the_term_and_moves_nothing(self) -> None:
        session = CuratedProtocolSession(self.fixture)
        session.activate_configured()
        route_curated_runtime_turn(session, "프로토콜 시작해줘", turn_id=1, language="ko")
        before = (session.current_index, session.workflow_status)
        plan = route_curated_runtime_turn(session, "진탕기가 뭐야?", turn_id=2, language="ko").plan
        self.assertEqual(plan.intent_kind, "protocol_entity_question")
        self.assertIn("3단계", plan.display_text)
        self.assertFalse(plan.state_changed)
        self.assertEqual((session.current_index, session.workflow_status), before)

    def test_the_translation_check_reads_the_same_terms(self) -> None:
        self.assertEqual(translation_units(self.fixture), translation_units(self.plain))
        self.assertEqual(
            ProtocolVocabulary.from_fixture(self.fixture).terms,
            ProtocolVocabulary.from_fixture(self.plain).terms,
        )


class ServerGlossaryTests(unittest.TestCase):
    """The server path: the glossary stored for the revision reaches the session."""

    def test_the_stored_glossary_reaches_the_session_vocabulary(self) -> None:
        plain = soil_fixture()
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            store = initialize_workspace_store(WorkspaceSettings(True, workspace))
            try:
                store.record_translation_glossary(TranslationGlossaryRecord(
                    revision_id=translation_revision_key(plain), language="ko",
                    entries_json=json.dumps(
                        [entry.__dict__ for entry in GLOSSARY], ensure_ascii=False),
                    model="fake-translator", model_version="fake-translator",
                    created_at="2026-10-12T00:00:00+00:00",
                ))
            finally:
                store.close()
            with patch.dict(os.environ, {
                "VOINEY_LAB_WORKSPACE_ENABLED": "true",
                "VOINEY_LAB_WORKSPACE_DATA_DIR": str(workspace),
            }):
                attached = server_module._with_revision_translations(plain, start_missing=False)
        session = CuratedProtocolSession(attached)
        session.activate_configured()
        route_curated_runtime_turn(session, "프로토콜 시작해줘", turn_id=1, language="ko")
        plan = route_curated_runtime_turn(session, "진탕기가 뭐야?", turn_id=2, language="ko").plan
        self.assertEqual(plan.intent_kind, "protocol_entity_question")
        self.assertIn("진탕기(shaker table)", plan.display_text)
        self.assertFalse(plan.state_changed)


if __name__ == "__main__":
    unittest.main()
