"""Lane UI4 (2026-10-23): lane QA's QA-21 -- after [원문과 달라요] the review version cannot be
registered, but the screen said the opposite.

Lane FX4 (decision 5): a safety statement's number that no OCR reading matches is marked
[원문과 달라요]; it waits for the source (원문 확인 필요) and holds the version. The server says so
three ways: the value's ``source_check`` {state_ko, guidance_ko}, the draft's
``registration_blockers`` (its message_ko, source_check true) with ``registrable`` false, and a
409 ``review_source_check_needed`` on a register. The page read none of them: the value got the
green "정함" badge and the register line said "확인 필요 항목 0건을 정하면 등록할 수 있습니다.";
a register sent anyway said "요청을 처리하지 못했습니다". The value now carries the server's state
and guidance, the register line the server's blockers, and the refusal its own words. The card
is still drawn from its fields alone (lane RV2's pin: never by its type).

Shares tests/test_lane_rv2_card_screen.py's harness.
"""

from __future__ import annotations

import unittest

from tests.test_lane_rv2_card_screen import SETUP
from tests.test_screen_cleanup import run_page_script

SOURCE_CHECK = r"""
const SOURCE_CHECK={state_ko:"원문 확인 필요",guidance_ko:"이 쪽을 더 선명하게 다시 찍어 올리거나 다시 분석해 주세요."};
const held={...page3,decided:true,decision:{action:"confirm"},values:[{...page3.values[0],decided:true,decision:{action:"source_differs"},source_check:SOURCE_CHECK}]};
const heldDraft=draft({cards:[decided(evidence),held],next_card:null,registrable:false,counts:{items:9,needs_review:2,decided:2,undecided:0,changed:0},
 registration_blockers:[{item_id:"ocr_value:3:5min",message_ko:"원문 확인 필요: 이 쪽을 더 선명하게 다시 찍어 올리거나 다시 분석해 주세요.",source_check:true}]});
const badges=host=>walk(host).filter(el=>String(el.className||"").split(" ").includes("ui-badge")).map(el=>({text:el.textContent,kind:el.dataset.kind}));
"""


def run(body: str):
    return run_page_script(SETUP + SOURCE_CHECK + body)


class SourceCheckTests(unittest.TestCase):

    def test_a_value_waiting_for_the_source_says_the_servers_state_not_decided(self) -> None:
        result = run(r"""
const host=node("ui4-card");
renderChoiceCard(host,held,{});
const shown=badges(host);
assert(shown.some(badge=>badge.text==="원문 확인 필요"&&badge.kind==="warning"),`badges: ${JSON.stringify(shown)}`);
const row=walk(host).find(el=>el.dataset?.valueId==="ocr_value:3:5min");
assert(row,"no value row");
assert(!badges(row).some(badge=>badge.kind==="done"),`the held value is shown as decided: ${JSON.stringify(badges(row))}`);
assert(visibleText(row).includes("이 쪽을 더 선명하게 다시 찍어 올리거나 다시 분석해 주세요."),`guidance: ${visibleText(row)}`);
// A value decided another way keeps its words.
renderChoiceCard(host,{...held,values:[{...held.values[0],decision:{action:"confirm"},source_check:undefined}]},{});
assert(badges(host).some(badge=>badge.text==="확인함"&&badge.kind==="done"),`confirmed: ${JSON.stringify(badges(host))}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_the_register_line_says_the_servers_blockers(self) -> None:
        result = run(r"""
serve([["/review-draft",heldDraft]]);
await loadReviewDraft("p-1");await tick();
node("reviewer-name").value="김재준";node("reviewer-name").dispatch("input");
const line=node("review-register-status").textContent;
assert(node("review-register").disabled===true,"the held version can be registered");
assert(line.includes("원문 확인 필요: 이 쪽을 더 선명하게 다시 찍어 올리거나 다시 분석해 주세요."),`line: ${line}`);
assert(!line.includes("0건을 정하면"),`the line counts no undecided card as the reason: ${line}`);
// Undecided cards still say how many are left, with the server's other blockers after them.
serve([["/review-draft",{...heldDraft,counts:{...heldDraft.counts,undecided:1},registration_blockers:[{item_id:"step:s2",message_ko:"확인 필요 항목을 정하지 않았습니다."},...heldDraft.registration_blockers]}]]);
await loadReviewDraft("p-1");await tick();
const both=node("review-register-status").textContent;
assert(both.includes("확인 필요 항목 1건을 정하면")&&both.includes("원문 확인 필요"),`both: ${both}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_a_register_held_for_the_source_is_refused_in_words(self) -> None:
        result = run(r"""
const refused={ok:false,status:409,headers:{get:()=>"application/json"},json:async()=>({detail:"review_source_check_needed"})};
const done=draft({cards:[decided(evidence)],next_card:null,registrable:true,counts:{items:9,needs_review:1,decided:1,undecided:0,changed:0}});
serve([["/review-versions",()=>refused],["/review-draft",done]]);
await loadReviewDraft("p-1");await tick();
node("reviewer-name").value="김재준";node("reviewer-name").dispatch("input");
node("review-register").dispatch("click");await tick();await tick();await tick();await tick();
const line=node("review-register-status").textContent;
assert(line.includes("원문 확인 필요")&&line.includes("다시 찍어 올리거나 다시 분석"),`refusal: ${line}`);
assert(!line.includes("요청을 처리하지 못했습니다"),`a generic refusal: ${line}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
