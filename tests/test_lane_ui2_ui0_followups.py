"""Lane UI2, decision 6 (2026-10-13): lane UI0's open points the screen settles alone.

Of the eight points lane UI0 left for a person (its report, section 9), two
are matters of the screen alone and are settled here; the rest wait for a
person's word (lane UI2's report lists them):

* 9.6 -- on the run screen an ended timer's dark panel ran into the dark
  bar of voice and buttons below it with no edge between them. A light line
  now runs above the bottom bar (and, on a screen held sideways, above the
  buttons beside it).
* 9.7 -- before and after the experiment a phone's top rail scattered its
  words and buttons over several lines (the rail did not wrap, so its words
  were squeezed to nothing behind the buttons). On a phone the rail is now
  one line of words and one row of buttons, each keeping its name on one
  line; the run's step, timer and basis placeholders wait for the run screen.
"""

from __future__ import annotations

import unittest

from tests.test_screen_cleanup import INDEX


class Ui0FollowUpTests(unittest.TestCase):

    def setUp(self) -> None:
        html = INDEX.read_text(encoding="utf-8")
        self.style = html.split("<style>", 1)[1].split("</style>", 1)[0]

    def test_a_light_line_parts_an_ended_timer_from_the_dark_bottom_bar(self) -> None:
        self.assertIn('body[data-view="during"] .voice-console{box-shadow:0 -4px 0 var(--paper)}', self.style)
        self.assertIn('@media (orientation:landscape){body[data-view="during"] .rail-right{box-shadow:0 -4px 0 var(--paper)}}', self.style)
        self.assertIn('body[data-view="during"] .run-timer[data-timer="done"]{background:var(--ink);color:var(--on-ink)}', self.style)

    def test_a_phone_rail_is_one_line_of_words_and_one_row_of_buttons(self) -> None:
        phone = self.style[self.style.index("Lane UI2 decision 6"):]
        phone = phone[phone.index("@media (max-width:720px){"):]
        phone = phone[:phone.index("\n    }\n")]
        for rule in (
            'body:not([data-view="during"]) .sticky-rail{flex-wrap:wrap;',
            'body:not([data-view="during"]) .rail-left{flex:1 1 100%;',
            'body:not([data-view="during"]) #rail-pdf-title{flex:1 1 0;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}',
            'body:not([data-view="during"]) .rail-divider,body:not([data-view="during"]) #rail-timer-badge,body:not([data-view="during"]) #rail-step-badge,body:not([data-view="during"]) .rail-basis-badge{display:none}',
            'body:not([data-view="during"]) .rail-right{flex-wrap:nowrap;',
        ):
            self.assertIn(rule, phone)
        buttons = phone[phone.index('body:not([data-view="during"]) .rail-right button{'):]
        buttons = buttons[:buttons.index("}")]
        for part in ("min-height:44px", "white-space:nowrap", "flex:1 1 auto"):
            self.assertIn(part, buttons)


if __name__ == "__main__":
    unittest.main()
