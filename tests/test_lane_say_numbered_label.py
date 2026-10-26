"""A step numbered "1." in its source may open its Korean with "1단계: " (lane SAY, decision 2).

Why the uploaded PMC8250384 protocol was read in English (measured on a copy
of the development server's data, 2026-10-25): 63 of its 84 stored Korean
sentences were refused as ``quantities_changed``. Its steps are numbered
"1. Harvest …", "2. Centrifuge …"; the style guide opens a step's Korean
with "1단계: "; the check let the label stand only when the source's own
number was followed by neither a digit nor a point -- the guard that keeps
"1.5 mL" a quantity -- so the "1." of a numbered line counted as a number the
Korean had dropped. Every number and unit is still compared: only the
step's own number, followed by a point and a space, is its label.
"""

from __future__ import annotations

import unittest

from voiney_lab.curated_protocol import reader_translation_issue
from voiney_lab.protocol_translation import TranslationUnit, check_translation, statement_issue

HARVEST = (
    "1. Harvest fresh culture(s) in a conical centrifuge tube (15 ml or 50 ml) at room "
    "temperature and \ncount cells."
)
HARVEST_KO = "1단계: 실온에서 conical 원심분리 튜브(15 ml 또는 50 ml)에 신선한 배양물을 수확하고 세포 수를 측정해요."
SPIN = "6. Centrifuge for 4 min at 1,300 × g at 4°C in a swinging bucket rotor and drain liquid."
SPIN_KO = "6단계: swinging bucket rotor로 4°C에서 1,300 × g로 4 min 동안 원심분리하고 액체를 따라 버려요."


def _step(text: str, label: str) -> TranslationUnit:
    return TranslationUnit(f"step-{label}/current_step", "step", text, (),
                           step_index=int(label) - 1, step_label=label)


class NumberedLabelTests(unittest.TestCase):
    def test_a_numbered_source_step_passes_with_its_label(self) -> None:
        self.assertEqual(check_translation(_step(HARVEST, "1"), HARVEST_KO), "passed")
        self.assertEqual(check_translation(_step(SPIN, "6"), SPIN_KO), "passed")

    def test_the_reader_check_with_the_label_passes_too(self) -> None:
        # The server's in-turn reading passes the step label to the check.
        self.assertIsNone(reader_translation_issue(HARVEST, HARVEST_KO, step_label="1"))
        self.assertIsNone(statement_issue(SPIN, SPIN_KO, step_label="6"))

    def test_every_other_number_is_still_compared(self) -> None:
        changed = SPIN_KO.replace("1,300", "1,500")
        self.assertEqual(check_translation(_step(SPIN, "6"), changed), "quantities_changed")
        dropped = HARVEST_KO.replace("(15 ml 또는 50 ml)", "")
        self.assertEqual(check_translation(_step(HARVEST, "1"), dropped), "quantities_changed")
        # Another number than the step's own is no label.
        self.assertEqual(check_translation(_step(HARVEST, "1"), HARVEST_KO.replace("1단계", "2단계")),
                         "quantities_changed")

    def test_a_decimal_opening_the_source_is_a_quantity(self) -> None:
        source = "1.5 mL of buffer is added to the tube."
        unit = _step(source, "1")
        self.assertEqual(check_translation(unit, "1단계: 튜브에 buffer 1.5 mL를 넣어요."), "passed")
        self.assertEqual(check_translation(unit, "1단계: 튜브에 buffer 5 mL를 넣어요."), "quantities_changed")
        self.assertIsNotNone(reader_translation_issue(source, "튜브에 buffer 5 mL를 넣어요.", step_label="1"))


if __name__ == "__main__":
    unittest.main()
