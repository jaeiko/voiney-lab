"""In-gel mis-hearing repairs apply only to a protocol that has the substance.

``normalize_scientific_request`` repairs the in-gel document's reagents --
"엠빅" -> AMBIC, "트립씬" -> trypsin, "아세토나이트릴" -> acetonitrile -- and
those repairs used to run on every protocol. A protocol with no acetonitrile
was then told "활성 프로토콜에 언급된 Acetonitrile…". The rules are unchanged;
the session now passes its own vocabulary, and a rule applies only when that
protocol's text contains the substance.

Turns go through ``route_curated_runtime_turn``, the boundary the Cascade
runtime calls.
"""

from __future__ import annotations

import unittest

from voiney_lab.curated_protocol import (
    _SUBSTANCE_PRESENCE_ALIASES,
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

#: Each in-gel repair, the entity it leads to, and the correction it records
#: (``None`` where the variant spelling is recognised without a rewrite).
REPAIRS = (
    ("아세토나이트릴이 뭐야?", "acetonitrile", None),
    ("엠빅이 뭐야?", "ambic", ("엠빅", "AMBIC")),
    ("에이 엠 빅이 뭐야?", "ambic", ("에이 엠 빅", "AMBIC")),
    ("트립씬이 뭐야?", "trypsin", ("트립씬", "trypsin")),
    ("디티티가 뭐야?", "dtt", ("디티티", "DTT")),
    ("폼산이 뭐야?", "formic_acid", ("폼산", "formic acid")),
    ("아이오도아세트아마이드가 뭐야?", "iodoacetamide", ("아이오도아세트아마이드", "iodoacetamide")),
    ("솔루션 A는 뭐야?", "solution_a", None),
    ("제트 플러그가 뭐야?", "gel_plug", ("제트 플러그", "gel plug")),
    ("H PLC water가 뭐야?", "hplc_water", ("h plc water", "HPLC water")),
    ("단백질 뱀드가 뭐야?", "stained_protein_band", ("단백질 뱀드", "단백질 밴드")),
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
class InGelCorrectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = in_gel_fixture()
        cls.vocabulary = ProtocolVocabulary.from_fixture(cls.fixture)

    def test_in_gel_has_every_substance_the_repairs_resolve_to(self) -> None:
        for entity in _SUBSTANCE_PRESENCE_ALIASES:
            with self.subTest(entity=entity):
                self.assertTrue(self.vocabulary.mentions_entity(entity))

    def test_in_gel_is_repaired_as_before(self) -> None:
        for transcript, entity, correction in REPAIRS:
            with self.subTest(transcript=transcript):
                plan = _turn(self.fixture, transcript)
                self.assertIn(entity, plan.requested_entities)
                if correction is not None:
                    self.assertIn(correction, plan.transcript_corrections)

    def test_scoping_to_in_gel_changes_no_result(self) -> None:
        inventory = ("ambic", "hplc water", "solution a", "trypsin")
        for transcript in (
            *(case[0] for case in REPAIRS),
            "AM BIC와 HPLG water가 뭐야? 800 rpm, Solution A, 37도",
            "솔루션 B 구성은 뭐야?",
            "anbi c는 왜 넣어?",
            "jel tug을 어디에 넣어?",
        ):
            with self.subTest(transcript=transcript):
                self.assertEqual(
                    normalize_scientific_request(
                        transcript, entity_inventory=inventory,
                        protocol_vocabulary=self.vocabulary,
                    ),
                    normalize_scientific_request(transcript, entity_inventory=inventory),
                )


class OtherProtocolCorrectionTests(unittest.TestCase):
    def test_a_protocol_without_the_substances_is_not_repaired_toward_them(self) -> None:
        fixture = miniprep_fixture()
        for transcript, entity, correction in REPAIRS:
            with self.subTest(transcript=transcript):
                plan = _turn(fixture, transcript)
                self.assertNotIn(entity, plan.requested_entities)
                self.assertEqual(plan.transcript_corrections, ())
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

    def test_each_rule_follows_its_own_substance(self) -> None:
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

    def test_general_lab_vocabulary_is_recognised_anywhere(self) -> None:
        plan = _turn(miniprep_fixture(), "원심분리기가 뭐야?")
        self.assertIn("centrifuge", plan.requested_entities)

    def test_a_caller_with_no_protocol_keeps_every_rule(self) -> None:
        _, entities, _, corrections = normalize_scientific_request("엠빅이 뭐야?")
        self.assertEqual(entities, ("ambic",))
        self.assertIn(("엠빅", "AMBIC"), corrections)


if __name__ == "__main__":
    unittest.main()
