"""A protocol's words are heard through its own vocabulary (lane GE, decision 3).

The in-gel mis-hearing repairs -- "엠빅" -> AMBIC, "트립씬" -> trypsin,
"아세토나이트릴" -> acetonitrile -- used to be a list of that document's
reagents. Now a Korean word is repaired to a term because it sounds like a
term the active protocol's own text contains (``ProtocolVocabulary.hear``),
whichever protocol that is; a protocol without the word never hears it, and a
caller with no protocol hears nothing. A lexicon lab word said in Korean
("탈색", "원심분리") counts where the protocol writes its English.

Turns go through ``route_curated_runtime_turn``, the boundary the Cascade
runtime calls.
"""

from __future__ import annotations

import unittest

from voiney_lab.curated_protocol import (
    CuratedProtocolSession,
    ProtocolVocabulary,
    normalize_scientific_request,
)
from voiney_lab.runtime_routing import route_curated_runtime_turn

from tests.protocol_vocabulary_support import (
    SOURCE_PDF,
    build_fixture,
    in_gel_fixture,
    miniprep_fixture,
)

#: What in-gel hears: the words as a researcher says them, the entity key the
#: term's spelling gives, and the term the heard words are repaired to.
# Lane GEN1: in-gel's terms are named as the protocol spells them, as every protocol's are.
HEARD = (
    ("아세토나이트릴이 뭐야?", "acetonitrile", "acetonitrile"),
    ("엠빅이 뭐야?", "AMBIC", "AMBIC"),
    ("에이 엠 빅이 뭐야?", "AMBIC", "AMBIC"),
    ("트립씬이 뭐야?", "trypsin", "trypsin"),
    ("디티티가 뭐야?", "DTT", "DTT"),
    # A lexicon lab word said in Korean names its entity; the key keeps the
    # Korean, so there is no correction to record.
    ("폼산이 뭐야?", "formic_acid", None),
    ("아이오도아세트아마이드가 뭐야?", "iodoacetamide", "iodoacetamide"),
    ("솔루션 A는 뭐야?", "solution_a", "Solution A"),
    ("H PLC water가 뭐야?", "HPLC water", "HPLC water"),
)


def _turn(fixture, transcript: str):
    session = CuratedProtocolSession(fixture)
    session.active = True
    session.current_index = min(2, len(fixture.steps) - 1)
    return route_curated_runtime_turn(
        session, transcript, turn_id=1, language="ko"
    ).plan


@unittest.skipUnless(
    SOURCE_PDF.is_file(),
    f"requires the externally licensed Candidate A source PDF at {SOURCE_PDF}",
)
class InGelHearingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = in_gel_fixture()
        cls.vocabulary = ProtocolVocabulary.from_fixture(cls.fixture)

    def test_in_gel_has_every_entity_it_hears(self) -> None:
        for _transcript, entity, _term in HEARD:
            with self.subTest(entity=entity):
                self.assertTrue(self.vocabulary.mentions_entity(entity))

    def test_in_gel_hears_its_reagents_said_in_korean(self) -> None:
        for transcript, entity, term in HEARD:
            with self.subTest(transcript=transcript):
                plan = _turn(self.fixture, transcript)
                self.assertIn(entity, plan.requested_entities)
                if term is not None:
                    self.assertTrue(
                        any(label == term for _observed, label in plan.transcript_corrections),
                        plan.transcript_corrections,
                    )

    def test_the_korean_name_said_is_repeated_before_the_spelling(self) -> None:
        plan = _turn(self.fixture, "트립씬이 뭐야?")
        self.assertIn("트립씬(trypsin)", plan.display_text)

    def test_a_number_is_never_repaired(self) -> None:
        plan = _turn(self.fixture, "37도에서 800 rpm으로 15분 돌려?")
        self.assertFalse(any("37" in observed or "800" in observed or "15" in observed
                             for observed, _label in plan.transcript_corrections))


class OtherProtocolHearingTests(unittest.TestCase):
    def test_a_protocol_without_the_words_does_not_hear_them(self) -> None:
        fixture = miniprep_fixture()
        for transcript, entity, _term in HEARD:
            with self.subTest(transcript=transcript):
                plan = _turn(fixture, transcript)
                self.assertNotIn(entity, plan.requested_entities)
                self.assertNotIn("활성 프로토콜에 언급된", plan.speech_text)

    def test_the_rewrite_itself_is_skipped(self) -> None:
        vocabulary = ProtocolVocabulary.from_fixture(miniprep_fixture())
        for transcript, observed in (
            ("엠빅이 뭐야?", "엠빅"),
            ("트립씬이 뭐야?", "트립씬"),
            ("솔루션 a는 뭐야?", "솔루션 a"),
            ("h plc water가 뭐야?", "h plc water"),
        ):
            with self.subTest(transcript=transcript):
                normalized, entities, note, corrections = normalize_scientific_request(
                    transcript, protocol_vocabulary=vocabulary,
                )
                self.assertIn(observed, normalized)
                self.assertEqual(entities, ())
                self.assertIsNone(note)
                self.assertEqual(corrections, ())

    def test_another_protocol_hears_its_own_term_said_in_korean(self) -> None:
        plan = _turn(miniprep_fixture(), "라이소자임이 뭐야?")
        self.assertIn("lysozyme", plan.requested_entities)
        self.assertIn(("라이소자임이", "lysozyme"), plan.transcript_corrections)

    def test_a_word_the_steps_use_is_heard_without_a_materials_list(self) -> None:
        fixture = build_fixture(
            protocol_id="fictional-rinse",
            title="Fictional column rinse",
            steps=("1 Rinse the column with 1 mL acetonitrile.", "2 Dry the column."),
        )
        rinse = _turn(fixture, "아세토나이트릴이 뭐야?")
        self.assertIn("acetonitrile", rinse.requested_entities)
        ambic = _turn(fixture, "엠빅이 뭐야?")
        self.assertNotIn("ambic", ambic.requested_entities)
        self.assertEqual(ambic.transcript_corrections, ())

    def test_a_lexicon_word_is_heard_where_the_protocol_writes_it(self) -> None:
        spinning = build_fixture(
            protocol_id="fictional-spin",
            title="Fictional spin",
            steps=("1 Centrifuge the tube for 1 minute.", "2 Discard the supernatant."),
        )
        self.assertIn("centrifuge", _turn(spinning, "원심분리가 뭐야?").requested_entities)
        self.assertNotIn("centrifuge", _turn(miniprep_fixture(), "원심분리가 뭐야?").requested_entities)

    def test_a_caller_with_no_protocol_hears_nothing(self) -> None:
        _, entities, note, corrections = normalize_scientific_request("엠빅이 뭐야?")
        self.assertEqual(entities, ())
        self.assertIsNone(note)
        self.assertEqual(corrections, ())


if __name__ == "__main__":
    unittest.main()
