"""Lane UI4 (2026-10-23), the home during and after the run: lane QA's QA-18.

QA-18: during and after the run the home's cards led to closed or empty places -- the
protocols tab hid its list in both views (lane UI2's one-page rules still applied to the tab),
and the upload is closed while a voice session is on (its file input and link are off outside
IDLE/ERROR). During the run the home now offers the way back to it alone; after it, the upload
card waits for the voice session to let go (a line says so), and the protocols tab is the same
in every view.

Shares tests/test_lane_ia_tabs.py's harness (the ON wizard with the tab router).
"""

from __future__ import annotations

import re
import unittest

from tests.test_lane_ia_tabs import run
from tests.test_screen_cleanup import INDEX

VIEWS = r"""
const cardKinds=()=>cards().map(card=>card.dataset.card);
const homeNotes=()=>walk(node("home-cards")).filter(el=>hasClass(el,"home-note")).map(el=>el.textContent);
const endRun=()=>onMessage({data:JSON.stringify({type:"experiment.ended",configuration_id:5,status:"completed",report:"saved"})},sessionGeneration,socket);
"""


def run_views(body: str):
    return run(VIEWS + body)


def style() -> str:
    return INDEX.read_text(encoding="utf-8").split("<style>", 1)[1].split("</style>", 1)[0]


class HomeDuringAndAfterTests(unittest.TestCase):
    """QA-18: the home offers only places that are open now."""

    def test_during_the_run_the_home_offers_the_way_back_alone(self) -> None:
        result = run_views(r"""
await boot(base());
go("#/home");
assert(cardKinds().includes("upload")&&cardKinds().includes("review"),`before the run: ${cardKinds()}`);
startRun();go("#/home");
assert(document.body.dataset.view==="during","not running");
assert(JSON.stringify(cardKinds())==='["running"]',`cards during the run: ${cardKinds()}`);
assert(cardHref(cards()[0])==="#/experiment","the way back does not lead to the run");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_after_the_run_the_upload_waits_for_the_voice_session_to_let_go(self) -> None:
        result = run_views(r"""
await boot(base());
startRun();renderState("LISTENING");endRun();
assert(document.body.dataset.view==="after","not after the run");
go("#/home");
assert(cardKinds().includes("new-experiment")&&cardKinds().includes("review"),`cards: ${cardKinds()}`);
assert(!cardKinds().includes("upload"),`the closed upload is offered: ${cardKinds()}`);
assert(homeNotes().some(text=>text.includes("음성 연결이 끝나면 새 프로토콜을 올릴 수 있어요")),`notes: ${homeNotes()}`);
// [종료] lets the session go: the upload is open again.
renderState("IDLE");go("#/home");
assert(cardKinds().includes("upload"),`after the session let go: ${cardKinds()}`);
assert(!homeNotes().some(text=>text.includes("음성 연결이 끝나면")),`the note stayed: ${homeNotes()}`);
// The new experiment opens the sheet over the end screen.
cards().find(card=>card.dataset.card==="new-experiment").dispatch("click");await settle(4);
assert(wizardOn(),"the sheet did not open after the run");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_protocols_tab_is_the_same_in_every_view(self) -> None:
        css = style()
        hidden_parts = ("#protocol-library", ".protocol-library", "#protocol-list", "#protocol-compare",
                        "#protocol-start-summary", "#protocol-review-panel", "#protocol-detail-view")
        for rule in re.finditer(r"([^{}]*)\{([^{}]*)\}", css):
            selectors, declarations = rule.group(1), rule.group(2)
            if "display:none" not in declarations.replace(" ", ""):
                continue
            for selector in selectors.split(","):
                if 'data-view="after"' not in selector and 'data-view="during"' not in selector:
                    continue
                for part in hidden_parts:
                    self.assertNotIn(part, selector, f"a view rule hides the protocols tab's {part}: {selector.strip()}")
        # The parts of the experiment tab those rules were also for stay as they were.
        self.assertIn('body[data-view="after"] .mic-check', css)
        self.assertIn('body[data-view="during"] .mic-check', css)


if __name__ == "__main__":
    unittest.main()
