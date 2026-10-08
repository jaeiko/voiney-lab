"""A Korean reading that turns the source around is refused (lane TS, decision 1).

Human decision of 2026-10-08. Three mechanical checks are added to the one
every Korean reading of a source statement passes
(``protocol_translation.statement_issue``: the step card's machine Korean, the
safety box, the start screen's safety notices, the reader translation and the
spoken safety warning):

* negations pair up: the source and the Korean hold as many negation and
  avoidance words of ``NEGATION_VOCABULARY`` (an "un-" word may be read with
  or without its own 않), so a double negation lost on one side is refused;
* hazard words carry over: a source word of ``HAZARD_VOCABULARY`` (toxic,
  flammable, corrosive, carcinogen, hazard, caution, warning, do not, avoid,
  never, must, glove, fume hood, ...) needs its Korean (독성, 인화성, 부식성,
  발암, 위험, 주의, 경고, 하지 마, 피하, 절대, 반드시, 장갑, 흄후드, ...) or the
  English word kept;
* sentences are not dropped: a Korean with fewer than half the source's
  sentences is refused.

Numbers and units were already checked (``quantities_changed``); the last
class confirms they still are. Before it, "Never open the lid without
waiting for the rotor to stop." ↔ "로터가 멈추기를 기다리지 않고 뚜껑을 엽니다."
and "Work in a fume hood; acrylamide is a neurotoxin." ↔ "아크릴아마이드를
다룹니다." both passed (audit, 2026-10-07, T1).
"""

from __future__ import annotations

import re
import unittest

from voiney_lab.protocol_translation import (
    HAZARD_VOCABULARY,
    NEGATION_VOCABULARY,
    TranslationUnit,
    check_translation,
    negation_count,
    sentence_count,
    statement_issue,
    stored_localizations,
)

ROTOR = "Never open the lid without waiting for the rotor to stop."
ACRYLAMIDE = "Work in a fume hood; acrylamide is a neurotoxin."


def unit(source: str, kind: str = "warning", label: str = "4") -> TranslationUnit:
    return TranslationUnit(f"s{label}/{kind}_1", kind, source, step_index=int(label) - 1,
                           step_label=label)


class NegationPairTests(unittest.TestCase):
    def test_a_double_negation_lost_on_one_side_is_refused(self) -> None:
        for source, korean in (
            (ROTOR, "로터가 멈추기를 기다리지 않고 뚜껑을 엽니다."),
            ("Do not remove the tube without closing the lid.",
             "뚜껑을 닫지 않고 튜브를 꺼냅니다."),
            ("Never leave the gel without a cover.", "젤을 덮개 없이 둡니다."),
        ):
            with self.subTest(korean=korean):
                self.assertEqual(statement_issue(source, korean), "negation_count_changed")
                self.assertEqual(check_translation(unit(source), korean), "negation_count_changed")

    def test_a_negation_added_on_the_korean_side_is_refused(self) -> None:
        source = "Do not close the lid until the tube has cooled."
        korean = "튜브가 식지 않으면 뚜껑을 닫지 마세요."
        self.assertEqual(statement_issue(source, korean), "negation_count_changed")

    def test_both_negations_kept_pass(self) -> None:
        for source, korean in (
            (ROTOR, "로터가 멈추기를 기다리지 않고는 절대 뚜껑을 열지 마세요."),
            ("Do not remove the tube without closing the lid.",
             "뚜껑을 닫지 않은 채 튜브를 꺼내지 마세요."),
            ("Do not vortex the lysate.", "lysate를 볼텍스하지 마세요."),
        ):
            with self.subTest(korean=korean):
                self.assertIsNone(statement_issue(source, korean))
                self.assertEqual(check_translation(unit(source), korean), "passed")

    def test_an_un_word_may_be_read_with_or_without_its_own_negation(self) -> None:
        # Lane PX's step 21, as stored on 2026-10-06: "without" and
        # "uninoculated" read as "넣지 않은" and "비접종".
        source = ("21 Repeat steps 19-20 for the required number of bacterial "
                  "isolates/replicates, leaving one agar plate without any inoculum "
                  "as an uninoculated control.")
        for korean in (
            "21단계: 필요한 수의 세균 분리주/반복에 대해 19-20단계를 반복하고, 한천 평판 "
            "하나는 접종원을 전혀 넣지 않은 비접종 대조군으로 남겨 둡니다.",
            "21단계: 필요한 수의 세균 분리주/반복에 대해 19-20단계를 반복하고, 한천 평판 "
            "하나는 접종원 없이 접종하지 않은 대조군으로 남겨 둡니다.",
        ):
            with self.subTest(korean=korean):
                self.assertIsNone(statement_issue(source, korean, step_label="21"))

    def test_must_said_as_a_double_negation_is_not_counted(self) -> None:
        # "~지 않으면 안 됩니다" says "must": no negation in the count. (The
        # narrow check still reads its 않 and refuses it, as before.)
        self.assertEqual(negation_count("ko", "장갑을 착용하지 않으면 안 됩니다."), 0)
        self.assertEqual(negation_count("ko", "장갑이 없으면 안 됩니다."), 0)

    def test_the_count_reads_the_one_table(self) -> None:
        self.assertIn(r"unless", NEGATION_VOCABULARY["en"])
        self.assertEqual(negation_count("en", ROTOR), 2)
        self.assertEqual(negation_count("ko", "기다리지 않고는 절대 열지 마세요"), 2)
        # "Regardless" is still no negation (lane FX, decision 1).
        self.assertEqual(negation_count("ko", "부피와 관계없이 섞습니다"), 0)


class HazardWordTests(unittest.TestCase):
    def test_a_hazard_sentence_dropped_whole_is_refused(self) -> None:
        self.assertEqual(statement_issue(ACRYLAMIDE, "아크릴아마이드를 다룹니다."),
                         "hazard_missing")
        self.assertEqual(check_translation(unit(ACRYLAMIDE), "아크릴아마이드를 다룹니다."),
                         "hazard_missing")
        self.assertIsNone(statement_issue(
            ACRYLAMIDE, "흄후드에서 작업합니다. 아크릴아마이드는 신경독성 물질입니다."))

    def test_each_word_of_the_table_needs_its_korean(self) -> None:
        cases = {
            "toxic": ("Phenol is toxic.", "Phenol은 독성이 있습니다.", "Phenol을 사용합니다."),
            "flammable": ("Ethanol is flammable.", "Ethanol은 인화성이 있습니다.",
                          "Ethanol을 사용합니다."),
            "corrosive": ("NaOH is corrosive.", "NaOH는 부식성입니다.", "NaOH를 사용합니다."),
            "carcinogen": ("Formaldehyde is a carcinogen.", "Formaldehyde는 발암 물질입니다.",
                           "Formaldehyde를 사용합니다."),
            "hazard": ("Chloroform is a hazard.", "Chloroform은 위험 물질입니다.",
                       "Chloroform을 씁니다."),
            "caution": ("Use caution with the blade.", "칼날을 주의해서 다룹니다.",
                        "칼날을 다룹니다."),
            "warning": ("Warning: hot plate.", "경고: hot plate.", "뜨거운 hot plate."),
            "prohibition": ("Do not touch the plate.", "plate를 만지지 마세요.",
                            "plate를 만지면 안 되지 않습니다."),
            "avoid": ("Avoid skin contact.", "피부 접촉을 피하세요.", "피부 접촉을 막습니다."),
            "must": ("The lid must be closed.", "뚜껑은 반드시 닫혀 있어야 합니다.",
                     "뚜껑을 닫습니다."),
            "glove": ("Wear gloves.", "장갑을 착용합니다.", "보호구를 착용합니다."),
            "fume_hood": ("Open the bottle in a fume hood.", "흄후드에서 병을 엽니다.",
                          "병을 엽니다."),
            "uv": ("Protect your eyes from UV light.", "UV 빛으로부터 눈을 보호합니다.",
                   "빛으로부터 눈을 보호합니다."),
        }
        names = {name for name, _en, _ko in HAZARD_VOCABULARY}
        for name, (source, kept, dropped) in cases.items():
            with self.subTest(name=name):
                self.assertIn(name, names)
                self.assertNotEqual(statement_issue(source, kept), "hazard_missing")
                self.assertIn(statement_issue(source, dropped),
                              {"hazard_missing", "negation_count_changed", "negation_changed"})

    def test_the_english_word_kept_in_the_korean_counts(self) -> None:
        # The in-gel purpose, as stored: the section is named "Warning".
        source = 'Please see "Warning" section for tips on avoiding keratin contamination.'
        self.assertIsNone(statement_issue(
            source, '케라틴 오염을 피하는 요령은 "Warning" 섹션을 참조합니다.'))


class SentenceTests(unittest.TestCase):
    def test_fewer_than_half_the_sentences_is_refused(self) -> None:
        source = "Mix the sample gently. Place the tube on the rack. Close the lid."
        self.assertEqual(sentence_count(source), 3)
        self.assertEqual(statement_issue(source, "샘플을 부드럽게 섞습니다."),
                         "sentences_dropped")

    def test_two_sentences_read_as_one_pass(self) -> None:
        self.assertIsNone(statement_issue(
            "Mix the sample gently. Place the tube on the rack.",
            "샘플을 부드럽게 섞고 튜브를 랙에 놓습니다."))

    def test_abbreviations_decimals_and_links_are_no_sentence_ends(self) -> None:
        self.assertEqual(sentence_count(
            "Add 0.5 mL buffer (e.g. PBS, approx. 1 vol.) and mix."), 1)
        self.assertEqual(sentence_count(
            "Spin it. protocols.io | https://dx.doi.org/10.17504/protocols.io.kq"), 2)


class QuantitiesStillCheckedTests(unittest.TestCase):
    def test_a_dropped_number_or_unit_is_refused(self) -> None:
        for source, korean in (
            ("Incubate at 37 °C for 15 min.", "37 °C에서 배양합니다."),
            ("Add 500 µL of solution B.", "solution B 500을 넣습니다."),
        ):
            with self.subTest(korean=korean):
                self.assertEqual(statement_issue(source, korean), "quantities_changed")


class StoredRowTests(unittest.TestCase):
    def test_a_stored_row_today_refuses_is_not_shown_and_not_changed(self) -> None:
        from tests.test_lane_fx_stored_recheck import row
        from tests.test_revision_translations import rich_fixture

        fixture = rich_fixture()
        step = fixture.steps[0]
        source = step.warnings[0].source_text
        flipped = "lysate를 볼텍스하지 않고 두지 않습니다."
        stored = row(fixture, f"{step.step_id}/warning_1", source, flipped, "passed")
        reviewed, machine = stored_localizations(fixture, [stored])
        self.assertNotIn(f"{step.step_id}/warning_1", {**reviewed, **machine})
        self.assertEqual(stored.translated_text, flipped)
        self.assertEqual(stored.check_result, "passed")


class TableShapeTests(unittest.TestCase):
    def test_every_entry_compiles_and_names_korean(self) -> None:
        for name, english, korean in HAZARD_VOCABULARY:
            with self.subTest(name=name):
                re.compile(english)
                self.assertRegex(korean, r"[가-힣]")


if __name__ == "__main__":
    unittest.main()
