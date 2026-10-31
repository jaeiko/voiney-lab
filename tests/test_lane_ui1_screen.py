"""Design rules for lane UI1's shared controls and the experiment screen's halves.

The shared Button / RatingScale / Field (web/src/components/Controls.tsx) are
styled by web/src/styles/controls.css after the design system: ink on paper,
colour only for a state (none here), no shadow, corners of 4 px or less,
Pretendard 400/600/700, targets of 48 px or more. controls.css loads after
run.css, so it must not size ``.btn`` (that shrank the run's 64/80 px buttons
to 48 px during this lane's walk). The experiment screen's wide layout is two
equal halves with the timer in the top third of the right one.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STYLES = ROOT / "web" / "src" / "styles"


class ControlsStylesheetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.css = (STYLES / "controls.css").read_text(encoding="utf-8")
        cls.main = (ROOT / "web" / "src" / "main.tsx").read_text(encoding="utf-8")

    def test_it_is_loaded_last(self) -> None:
        imports = re.findall(r'import "\./styles/([a-z]+)\.css";', self.main)
        self.assertEqual(imports[-1], "controls")

    def test_no_colour_of_its_own_no_shadow_small_corners_three_weights(self) -> None:
        self.assertEqual(re.findall(r"#[0-9a-fA-F]{3,8}\b", self.css), [])
        self.assertIsNone(re.search(r"box-shadow|text-shadow|drop-shadow", self.css))
        for value in re.findall(r"border-radius:\s*([^;]+);", self.css):
            self.assertIn(value.strip(), {"var(--radius)", "var(--radius-small)"})
        self.assertEqual({int(w) for w in re.findall(r"font-weight:\s*(\d+)", self.css)} - {400, 600, 700}, set())

    def test_targets_are_48_px_or_more(self) -> None:
        for value in re.findall(r"min-height:\s*([^;]+);", self.css):
            value = value.strip()
            if value.startswith("var("):
                self.assertIn(value, {"var(--target)"})
            else:
                self.assertGreaterEqual(int(value.removesuffix("px")), 48, value)

    def test_it_never_sizes_a_button_after_the_run_stylesheet(self) -> None:
        for selectors, body in re.findall(r"([^{}]+)\{([^}]*)\}", self.css):
            names = [part.strip() for part in selectors.split(",")]
            if any(re.fullmatch(r"\.btn(-[a-z]+)?", name) for name in names):
                self.assertNotRegex(body, r"min-height|font-size|line-height|padding", selectors)


class RunLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.css = (STYLES / "run.css").read_text(encoding="utf-8")

    def rule(self, selector: str) -> str:
        match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", self.css)
        self.assertIsNotNone(match, selector)
        return match.group(1)

    def test_wide_is_two_equal_halves_with_the_records_under_the_step(self) -> None:
        body = self.rule(".run ")
        self.assertIn("grid-template-columns: minmax(0, 1fr) minmax(0, 1fr)", body)
        self.assertIn('"main side" "records side"', body)

    def test_the_right_half_is_the_timer_third_over_the_pictures(self) -> None:
        self.assertIn("grid-template-rows: minmax(min-content, 1fr) minmax(0, 2fr)", self.rule(".run-side"))

    def test_narrow_is_one_column_with_the_records_at_the_bottom(self) -> None:
        body = self.rule('.run[data-layout="narrow"]')
        self.assertIn('"main" "records" "caption"', body)


if __name__ == "__main__":
    unittest.main()
