"""Lane UI4 (2026-10-23): what is left of lane QA's QA-57 -- connection.js's sentences when the
run cannot be continued (GIVEN_UP_WORDS) named the one-page screen: "처음 화면에서 실험을 다시
골라 주세요" and "처음 화면에서 '실험 이어하기'를 눌러". Since lane IA the page is five tabs: a
saved experiment is picked in the records tab and continued from the home tab's "이어서 하기"
card. The sentences now name those.

Shares lane NET's harness (the page with connection.js under Node, tests/lane_net_support.py).
"""

from __future__ import annotations

import unittest

from tests.lane_net_support import CONNECTION, run_connection_script
from tests.test_lane_net_page_reconnect import SERVER


class GivenUpWordsTests(unittest.TestCase):

    def run_page(self, body: str) -> None:
        result = run_connection_script(SERVER + body)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_an_experiment_not_found_sends_the_person_to_the_records_tab(self) -> None:
        self.run_page(r"""
const ws=await openRun();
experiment=404;
ws.drop();await settle();
await advance(1000);
const said=node("status").textContent;
assert(said==="다시 연결됐지만 이 실험을 찾지 못했어요. 기록 탭에서 실험을 다시 골라 주세요.",`status: ${said}`);
""")

    def test_a_refused_run_sends_the_person_to_the_homes_continue_card(self) -> None:
        self.run_page(r"""
const ws=await openRun();
ws.drop();await settle();
for(let refusal=1;refusal<=3;refusal++){
 for(let step=0;step<300&&opened.length-1<refusal;step++)await advance(100);
 const next=opened.at(-1);next.open();await settle(6);
 await next.deliver({type:"error",message:"invalid session configuration"});
}
const said=node("status").textContent;
assert(said==="다시 연결됐지만 서버가 이 실험을 이어 받지 않았어요. 홈 탭의 '이어서 하기'를 눌러 확인해 주세요.",`status: ${said}`);
""")

    def test_no_sentence_names_the_one_page_screen(self) -> None:
        source = CONNECTION.read_text(encoding="utf-8")
        words = source.split("const GIVEN_UP_WORDS={", 1)[1].split("};", 1)[0]
        self.assertNotIn("처음 화면", words)
        self.assertNotIn("실험 이어하기", words)


if __name__ == "__main__":
    unittest.main()
