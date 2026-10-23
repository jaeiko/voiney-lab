"""Lane TAB (2026-10-23): one experiment from start to end on a tablet.

Decision 1: on a tablet -- a touch screen (a coarse pointer) wider than a
phone's 720 px and taller than a phone on its side -- every tap target is at
least the desk's 44 px on every screen. The phone and the desktop keep their
layout: none of the tablet rules applies to them. The browser walk itself is
tests/e2e/tablet-tab.spec.ts (the 'Tablet portrait' and 'Tablet landscape'
projects); this file reads the page's own rules and the Playwright configs.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from tests.test_screen_cleanup import INDEX

ROOT = Path(__file__).resolve().parents[1]

# The screens the walk runs on (CSS px; pointer as Chromium reports it with and without touch).
PHONE = {"width": 393, "height": 727, "pointer": "coarse"}
PHONE_ON_ITS_SIDE = {"width": 851, "height": 393, "pointer": "coarse"}
DESKTOP = {"width": 1280, "height": 720, "pointer": "fine"}
WIDE_DESKTOP = {"width": 1920, "height": 1080, "pointer": "fine"}
TABLET_PORTRAIT = {"width": 820, "height": 1180, "pointer": "coarse"}
TABLET_LANDSCAPE = {"width": 1180, "height": 820, "pointer": "coarse"}
NOT_TABLETS = (PHONE, PHONE_ON_ITS_SIDE, DESKTOP, WIDE_DESKTOP)


def _style() -> str:
    return INDEX.read_text(encoding="utf-8").split("<style>", 1)[1].split("</style>", 1)[0]


def _matches(query: str, screen: dict[str, object]) -> bool:
    """Evaluate the media features this page's tablet rules use, joined by 'and'."""
    width, height = int(screen["width"]), int(screen["height"])
    for feature in re.findall(r"\(([^()]+)\)", query):
        name, _, value = (part.strip() for part in feature.partition(":"))
        if name == "pointer":
            ok = screen["pointer"] == value
        elif name == "orientation":
            ok = (value == "landscape") == (width > height)
        elif name in {"min-width", "max-width", "min-height", "max-height"}:
            limit = int(value.removesuffix("px"))
            size = width if name.endswith("width") else height
            ok = size >= limit if name.startswith("min") else size <= limit
        else:
            raise AssertionError(f"a feature this reader does not know: {feature}")
        if not ok:
            return False
    return True


def _tablet_blocks(style: str) -> list[tuple[str, str]]:
    """Every @media block whose query asks for a coarse pointer: (query, body)."""
    blocks = []
    for match in re.finditer(r"@media ([^{]*pointer:coarse[^{]*)\{", style):
        depth, at = 1, match.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(style[at], 0)
            at += 1
        blocks.append((match.group(1).strip(), style[match.end():at - 1]))
    return blocks


class TabletTargetTests(unittest.TestCase):

    def setUp(self) -> None:
        self.style = _style()
        self.blocks = _tablet_blocks(self.style)

    def test_every_tap_target_on_a_tablet_is_at_least_44_px(self) -> None:
        self.assertIn("--target-desk:44px;", self.style)
        targets = [body for query, body in self.blocks if _matches(query, TABLET_PORTRAIT) and _matches(query, TABLET_LANDSCAPE)]
        self.assertTrue(targets, "no rule for both tablets")
        rules = "".join(targets)
        self.assertIn(
            'button,select,summary,[role="button"],input:not([type="checkbox"]):not([type="radio"]):not([type="hidden"]){min-height:var(--target-desk)}',
            rules,
        )
        self.assertIn('button,summary,[role="button"]{min-width:var(--target-desk)}', rules)
        self.assertIn(
            'label:has(> input[type="checkbox"]),label:has(> input[type="radio"]){display:inline-flex;align-items:center;min-height:var(--target-desk)}',
            rules,
        )

    def test_no_tablet_rule_reaches_a_phone_or_a_desktop(self) -> None:
        self.assertTrue(self.blocks)
        for query, _ in self.blocks:
            for screen in NOT_TABLETS:
                self.assertFalse(_matches(query, screen), f"@media {query} reaches {screen}")


def _root_tokens(style: str) -> dict[str, str]:
    tokens: dict[str, str] = {}
    for block in re.findall(r"(?m)^    :root\{([^}]*)\}", style):
        tokens.update(dict(re.findall(r"(--[a-z0-9-]+):([^;]+);", block)))
    return tokens


class TabletRunScreenTests(unittest.TestCase):
    """Decision 2: the run screen on a tablet, read from 1.5 m and pressed with gloves."""

    def setUp(self) -> None:
        self.style = _style()
        self.tokens = _root_tokens(self.style)
        self.blocks = _tablet_blocks(self.style)

    def test_the_reading_and_glove_sizes_are_tokens(self) -> None:
        self.assertEqual(self.tokens.get("--read-far-step"), "3.25rem")
        self.assertEqual(self.tokens.get("--read-far-min"), "2.5rem")
        self.assertEqual(self.tokens.get("--read-far-timer"), "8rem")
        self.assertEqual(self.tokens.get("--target-glove"), "80px")
        self.assertEqual(self.tokens.get("--gap-glove"), "24px")
        # The comment says how each was worked out (the distance, the angle, the millimetres).
        for words in ("1.5 m", "ANSI/HFES 100-2007", "20'", "16'", "40'", "15 mm", "4.6 mm"):
            self.assertIn(words, self.style)

    def test_both_tablets_read_the_step_and_the_timer_at_the_token_sizes(self) -> None:
        both = "".join(body for query, body in self.blocks if _matches(query, TABLET_PORTRAIT) and _matches(query, TABLET_LANDSCAPE))
        self.assertIn('body[data-view="during"]{--target-run:var(--target-glove);--run-primary-w:8rem}', both)
        self.assertIn('p.step-body[data-length="medium"]{font-size:var(--read-far-step);', both)
        self.assertIn('p.step-body[data-length="long"]{font-size:var(--read-far-min);', both)
        self.assertIn('.run-timer:not([data-digits="long"]) .run-timer-digits{font-size:max(var(--read-far-timer),min(9rem,34cqi))}', both)
        # A short step is larger still (3.5rem, the existing tablet scale), never under the token.
        self.assertIn('body[data-view="during"] .procedure-card p.step-body{font-size:3.5rem;', both)
        self.assertIn('#rail-pause-session,body[data-view="during"] #rail-stop-session{margin-left:var(--gap-glove);', both)
        self.assertIn('body[data-view="during"] .rail-right{background:var(--paper)}', both)

    def test_a_tablet_on_its_side_has_two_columns_and_set_apart_buttons(self) -> None:
        side = "".join(body for query, body in self.blocks if _matches(query, TABLET_LANDSCAPE) and not _matches(query, TABLET_PORTRAIT))
        # The timer panel is as wide as 8rem digits need: its content (the panel less 1.5rem each side) times .34 is the token.
        self.assertIn("--run-timer-panel-w:calc(var(--read-far-timer) / .34 + 3rem)", side)
        self.assertIn('body[data-view="during"] .run-timer{padding:1.5rem}', side)
        self.assertIn("right:calc(var(--run-action-w) * 3 + var(--run-primary-w) * 2 + var(--gap-glove) * 2)", side)
        # The three columns (step, timer, pictures) are a mouse's wide screen only: never a tablet's, still the desktop's.
        query = re.search(r"@media ([^{]*min-width:1100px[^{]*)\{\n     body\[data-view=\"during\"\]\{--run-actions:4;", self.style)
        self.assertIsNotNone(query)
        self.assertFalse(_matches(query.group(1), TABLET_LANDSCAPE))
        self.assertTrue(_matches(query.group(1), DESKTOP))
        self.assertTrue(_matches(query.group(1), WIDE_DESKTOP))


class TabletProjectTests(unittest.TestCase):

    def test_both_configs_run_the_tablet_spec_on_two_touch_tablets_only(self) -> None:
        for name in ("playwright.ci.config.ts", "playwright.config.ts"):
            config = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn("const TABLET = { ...devices['Galaxy Tab S4'], deviceScaleFactor: 2 };", config, name)
            self.assertIn("const TABLET_SPECS = /tablet-.*\\.spec\\.ts$/;", config, name)
            compact = re.sub(r"\s+", " ", config)
            self.assertIn("name: 'Tablet portrait', testMatch: TABLET_SPECS, use: { ...TABLET, viewport: { width: 820, height: 1180 }", compact, name)
            self.assertIn("name: 'Tablet landscape', testMatch: TABLET_SPECS, use: { ...TABLET, viewport: { width: 1180, height: 820 }", compact, name)
            self.assertIn("name: 'Desktop Chrome', testIgnore: TABLET_SPECS", compact, name)
            self.assertIn("name: 'Mobile Chrome', testIgnore: TABLET_SPECS", compact, name)
        self.assertTrue((ROOT / "tests/e2e/tablet-tab.spec.ts").is_file())


if __name__ == "__main__":
    unittest.main()
