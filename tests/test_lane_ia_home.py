"""Lane IA, decision ② (2026-10-19): the home -- big cards from the server's lists.

[이어서 하기 · the saved experiment] [새 실험 시작] [검토할 프로토콜 n개] [새 프로토콜
올리기]; a first user, with an empty catalog, sees "먼저 프로토콜을 올려 주세요".
The counts are the server's alone (the catalog's review_pending, the
workspace's experiments). Shares the fake page of tests/test_lane_ia_tabs.py.
"""

from __future__ import annotations

import unittest

from tests.test_lane_ia_tabs import SETUP, panel, run
from tests.test_screen_cleanup import INDEX


class HomeTests(unittest.TestCase):
    """Decision ②: big cards from the server's lists; a first user sees one."""

    def test_the_cards_live_on_the_home_panel(self) -> None:
        html = INDEX.read_text(encoding="utf-8")
        self.assertEqual(html.count('id="home-cards"'), 1)
        self.assertIn('<div id="home-cards" class="home-cards" aria-label="지금 할 수 있는 일"></div>', panel(html, "home"))
        self.assertIn(".home-card{display:flex;flex-direction:column;", html)
        self.assertIn("min-height:96px", html[html.index(".home-card{"):html.index("}", html.index(".home-card{"))])

    def test_an_empty_catalog_shows_one_card_to_upload(self) -> None:
        result = run(r"""
await boot(base(DEV_SESSION,SETTINGS(),VOICES,{protocols:[]}));
assert(cards().length===1,`cards: ${cardWords()}`);
assert(cards()[0].textContent.includes("먼저 프로토콜을 올려 주세요"),cardWords());
assert(cardHref(cards()[0])==="#/upload",cardHref(cards()[0]));
assert(cards()[0].dataset.card==="first");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_cards_say_the_servers_counts_and_link_the_tabs(self) -> None:
        result = run(r"""
await boot(base());
const kinds=cards().map(card=>card.dataset.card);
assert(kinds.join()==="new-experiment,review,upload",`cards: ${kinds}`);
const [start,review,upload]=cards();
assert(start.textContent.includes("새 실험 시작")&&cardHref(start)==="#/experiment",start.textContent);
assert(review.textContent.includes("검토할 프로토콜 1개")&&cardHref(review)==="#/protocols",review.textContent);
assert(upload.textContent.includes("새 프로토콜 올리기")&&cardHref(upload)==="#/upload",upload.textContent);
// The count is the catalog's review_pending alone; a second pending protocol makes two.
protocolCatalog.set("p-3",ENTRY("p-3","Epsilon",false,{review_pending:{needs_review_count:5}}));renderHome();
assert(cards()[1].textContent.includes("검토할 프로토콜 2개"),cards()[1].textContent);
protocolCatalog.delete("p-2");protocolCatalog.delete("p-3");renderHome();
assert(cards().map(card=>card.dataset.card).join()==="new-experiment,upload","a review card without pending protocols");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_resumable_experiment_comes_first_and_continues_through_the_wizard(self) -> None:
        result = run(r"""
await boot(withExperiments([RESUMABLE]));
assert(cards()[0].dataset.card==="resume",`cards: ${cards().map(card=>card.dataset.card)}`);
assert(cards()[0].textContent.includes("이어서 하기")&&cards()[0].textContent.includes("Gamma")&&cards()[0].textContent.includes("3단계"),cards()[0].textContent);
calls.length=0;
cards()[0].dispatch("click");await settle(10);
assert(tab()==="experiment",`tab: ${tab()}`);
assert(node("protocol-id").value==="p-1"&&node("experiment-session-select").value==="s-9","the protocol and the session were not picked");
assert(calls.some(call=>call.url.includes("/experiments/s-9/timeline")),"the session was not read");
assert(wizard.step==="start",`step: ${wizard.step}`);
assert(primary().textContent==="실험 이어하기",`primary: ${primary().textContent}`);
assert(walk(body()).some(el=>el.textContent.includes("3단계")),"the step to continue from is not said");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_completed_experiment_is_not_offered_to_continue(self) -> None:
        result = run(r"""
await boot(withExperiments([{...RESUMABLE,status:"completed"}]));
assert(!cards().some(card=>card.dataset.card==="resume"),`cards: ${cards().map(card=>card.dataset.card)}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_during_the_run_the_home_offers_the_way_back(self) -> None:
        result = run(r"""
await boot(base());
startRun();go("#/home");
assert(cards()[0].dataset.card==="running",`cards: ${cards().map(card=>card.dataset.card)}`);
assert(cards()[0].textContent.includes("실험 중")&&cards()[0].textContent.includes("3단계")&&cardHref(cards()[0])==="#/experiment",cards()[0].textContent);
assert(!cards().some(card=>card.dataset.card==="new-experiment"),"a new experiment is offered during the run");
""")
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
