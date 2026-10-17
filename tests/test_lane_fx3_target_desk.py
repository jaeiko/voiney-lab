"""Lane FX3, decision 2: ``--target-desk`` is defined, so the desk's touch rules apply.

Five rules (lanes UI2·UI3: the microphone check's settings button, the
source-compare rows, the after-run review's buttons and input, the install
button) asked for ``min-height:var(--target-desk)`` while no rule defined the
token, so the browser dropped those heights; lane RV's five later rules
carried a ``44px`` fallback. The token now sits with lane UI0's tokens, at
the 44px the page already uses for its desk-side touch targets, and every
rule reads the one definition.
"""

from __future__ import annotations

import re
import unittest

from tests.test_lane_ui0_tokens import _tokens
from tests.test_screen_cleanup import INDEX

#: The rules that used the token before it existed (lanes UI2, UI3).
UNDEFINED_BEFORE = (
    ".mic-check-settings{", ".compare-step{", ".report-review-actions button{",
    ".report-review-actions input{", "#install-app{",
)
#: Desk-side touch targets the page already sized at 44px by hand.
LITERAL_44 = (
    ".reagent-tables .btn-file-select{", ".reagent-table-row button{",
    ".install-guide>summary{",
)


def _rule(style: str, selector: str) -> str:
    """The rule whose selector list is ``selector`` alone (at a line's start)."""

    start = re.search(rf"(?m)^\s*{re.escape(selector)}", style).start()
    return style[start:style.index("}", start)]


class TargetDeskTests(unittest.TestCase):
    def setUp(self) -> None:
        html = INDEX.read_text(encoding="utf-8")
        self.style = html.split("<style>", 1)[1].split("</style>", 1)[0]

    def test_the_token_is_defined_once_with_the_draft_tokens(self) -> None:
        self.assertEqual(_tokens(self.style).get("--target-desk"), "44px")
        self.assertEqual(len(re.findall(r"--target-desk\s*:", self.style)), 1)

    def test_the_rules_that_lacked_it_now_get_a_height(self) -> None:
        for selector in UNDEFINED_BEFORE:
            with self.subTest(selector=selector):
                self.assertIn("min-height:var(--target-desk)", _rule(self.style, selector))

    def test_every_use_reads_the_one_definition(self) -> None:
        uses = re.findall(r"var\(--target-desk([^)]*)\)", self.style)
        self.assertGreaterEqual(len(uses), 10)
        self.assertEqual([use for use in uses if use], [], "a use still carries its own fallback")

    def test_the_value_is_the_page_s_own_desk_target(self) -> None:
        for selector in LITERAL_44:
            with self.subTest(selector=selector):
                self.assertIn("min-height:44px", _rule(self.style, selector))
        self.assertIn("--run-statusbar-h:44px", self.style)


if __name__ == "__main__":
    unittest.main()
