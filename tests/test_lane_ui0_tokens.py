"""Lane UI0, decision 4 (2026-10-12): the draft's design tokens, a light page in place of the dark one.

The page defines the draft's tokens (paper, bench, line, ink, the state
colours) and maps app.css's own token names onto them, so every rule of the
stylesheet draws light without the stylesheet changing. The text and state
pairs the screen uses keep WCAG AA contrast (4.5:1), read from the values
themselves.
"""

from __future__ import annotations

import re
import unittest

from tests.test_screen_cleanup import INDEX

DRAFT = {
    "--paper": "#ffffff", "--bench": "#efefec", "--line": "#d4d4cf", "--line-strong": "#76766f",
    "--ink": "#1c1c1a", "--ink-muted": "#55554f", "--on-ink": "#ffffff",
    "--danger": "#bf2229", "--danger-soft": "#fbeaea",
    "--warning": "#f2a900", "--warning-ink": "#7a4f00", "--warning-soft": "#fff4d6",
    "--pending": "#5b3cc4", "--pending-soft": "#efebfb",
}
MAPPED = {
    "--canvas": "var(--bench)", "--surface-raised": "var(--paper)", "--surface-card": "var(--paper)",
    "--ink": "#1c1c1a", "--muted": "var(--ink-muted)", "--border": "var(--line)",
    "--red": "var(--danger)", "--amber": "var(--warning-ink)", "--blurple": "var(--ink)",
}


def _tokens(style: str) -> dict[str, str]:
    block = style[style.index("    :root{\n      color-scheme:light;"):]
    block = block[:block.index("}")]
    return dict(re.findall(r"(--[a-z0-9-]+):([^;]+);", block))


def _luminance(colour: str) -> float:
    channels = [int(colour[index:index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(first: str, second: str) -> float:
    light, dark = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


class TokenTests(unittest.TestCase):

    def setUp(self) -> None:
        html = INDEX.read_text(encoding="utf-8")
        self.html = html
        self.style = html.split("<style>", 1)[1].split("</style>", 1)[0]
        self.tokens = _tokens(self.style)

    def test_the_draft_tokens_and_a_light_page_are_defined(self) -> None:
        for name, value in DRAFT.items():
            self.assertEqual(self.tokens.get(name), value, name)
        self.assertIn('<meta name="theme-color" content="#ffffff">', self.html)
        self.assertIn("body{background:var(--bench);color:var(--ink);font-family:var(--font-sans)", self.style)

    def test_the_stylesheet_names_are_mapped_onto_the_draft(self) -> None:
        for name, value in MAPPED.items():
            self.assertEqual(self.tokens.get(name), value, name)

    def test_text_and_state_pairs_keep_aa_contrast(self) -> None:
        pairs = (
            ("--ink", "--paper"), ("--ink", "--bench"), ("--ink-muted", "--paper"), ("--ink-muted", "--bench"),
            ("--on-ink", "--ink"), ("--danger", "--paper"), ("--danger", "--danger-soft"),
            ("--warning-ink", "--warning-soft"), ("--warning-ink", "--paper"),
            ("--pending", "--pending-soft"), ("--pending", "--paper"), ("--ink", "--pending-soft"),
        )
        for text, ground in pairs:
            ratio = _contrast(DRAFT[text], DRAFT[ground])
            self.assertGreaterEqual(ratio, 4.5, f"{text} on {ground}: {ratio:.2f}")

    def test_the_run_view_draws_on_ink_with_the_on_ink_token(self) -> None:
        self.assertNotIn("var(--canvas)", self.style.split("    :root{\n      color-scheme:light;")[0])
        self.assertIn('body[data-view="during"] .run-timer[data-timer="done"]{background:var(--ink);color:var(--on-ink)}', self.style)


if __name__ == "__main__":
    unittest.main()
