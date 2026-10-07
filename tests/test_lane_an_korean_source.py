"""A Korean sentence that names its instrument in English is Korean (lane AN, 2).

Human decision of 2026-10-07: a source sentence is already Korean when it
holds at least four Hangul letters and ends in Hangul; such a sentence is not
sent for translation. Measured 2026-10-06 (lane PX): glycolysis step 9
"Seahorse XF glycolysis stress test 를 실행합니다" (Hangul 6 : Latin 30) read
as English under the "a quarter as many Hangul as Latin letters" rule, was
sent, and came back refused; the glycolysis steps counted Korean were 11 of
13. "Ends in Hangul" reads the last letter of the sentence -- Hangul or
Latin -- so a trailing note number ("… 준비합니다 [노트 2].") does not hide
the Korean ending.
"""

from __future__ import annotations

import asyncio
import unittest
from dataclasses import replace

from tests.test_revision_translations import FakeTranslator, rich_fixture
from voiney_lab.protocol_translation import (
    generate_revision_translations,
    is_korean,
    translation_units,
)

#: The glycolysis (Seahorse, Korean) step sentences as the 2026-10-06 analysis
#: read them from the PDF.
GLYCOLYSIS_STEPS = (
    "1. 안정화를 위해서 Seahorse XFe/XF Analyzer 를 켜서 예열합니다.",
    "2. 적절한 세포 배양 성장 배지를 사용하여 Seahorse XF 마이크로 플레이트(microplate)에 세포를 깝니다 [노트 1].",
    "3. 센서 카트리지(sensor cartridge)를 Seahorse XF Calibrant (37°C in a non-CO2 incubator)에서 오버나잇 동안 hydrate 합니다.",
    "1. Seahorse XF Base 배지를 보충하여 assay 배지를 준비합니다 [노트 2].",
    "2. Assay 배지를 37°C 에 보관합니다.",
    "3. 0.1N NaOH 로 pH 를 7.4 로 조정합니다 [노트 3].",
    "4. 사용할 때까지 37°C 로 유지합니다.",
    "5. 화합물은 실험 당일에 준비하고, 다시 냉동하거나 재사용하지 않습니다.",
    "6. 센서 카트리지(sensor cartridge)의 주입 포트에 준비한 화합물을 로딩합니다 [노트 4].",
    "7. 37°C CO2 배양기에서 세포 배양 마이크로 플레이트를 분리하고, 현미경으로 확인합니다.",
    "8. 세포 배양 마이크로 플레이트에 있는 세포 배양 배지를 따뜻한 assay 배지로 바꾸고, assay 전 45 분에서 "
    "1 시간 동안 세포 배양 마이크로 플레이트를 37°C non-CO2 배양기에 넣습니다.",
    "9. Seahorse XF glycolysis stress test 를 실행합니다.",
    "10. 데이터를 분석합니다.",
)


class KoreanSourceTests(unittest.TestCase):
    def test_the_two_glycolysis_steps_read_as_english_on_2026_10_06_are_korean(self) -> None:
        self.assertTrue(is_korean("9. Seahorse XF glycolysis stress test 를 실행합니다."))
        self.assertTrue(is_korean("1. Seahorse XF Base 배지를 보충하여 assay 배지를 준비합니다 [노트 2]."))

    def test_every_glycolysis_step_is_korean(self) -> None:
        korean = [text for text in GLYCOLYSIS_STEPS if is_korean(text)]
        self.assertEqual(len(korean), 13, [t for t in GLYCOLYSIS_STEPS if not is_korean(t)])

    def test_english_sentences_stay_english(self) -> None:
        for text in (
            "Add 5 mL buffer (완충액).",  # three Hangul letters only
            "API ZYM",
            "1 Prepare 400 mL of LB broth (25 g per litre).",
            "6 Transfer 10 mL of autoclaved LB into two 50 mL falcon tubes.",
            # Hangul inside, but the sentence ends in English.
            "Load 배지를 보충한 assay medium into the Seahorse XF plate.",
            # Ends in Hangul, but fewer than four Hangul letters.
            "Run the Seahorse XF glycolysis stress test 실행.",
        ):
            with self.subTest(text=text):
                self.assertFalse(is_korean(text))

    def test_a_korean_sentence_naming_an_instrument_is_not_sent_for_translation(self) -> None:
        fixture = rich_fixture()
        protocol = fixture.draft.protocol
        section = protocol.sections[0]
        steps = list(section.steps)
        steps[4] = replace(
            steps[4], instruction_source_text="5 Seahorse XF glycolysis stress test 를 실행합니다.")
        fixture = replace(fixture, draft=replace(fixture.draft, protocol=replace(
            protocol, sections=(replace(section, steps=tuple(steps)),))))
        unit = next(u for u in translation_units(fixture) if u.fact_key == "step-5/current_step")
        self.assertIn("Seahorse", unit.source_text)
        translator = FakeTranslator()
        report = asyncio.run(generate_revision_translations(
            fixture, translator, model="grok-test"))
        sent = {key for batch in translator.batches for key in batch}
        self.assertNotIn("step-5/current_step", sent)
        self.assertEqual(report.skipped_korean, 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
