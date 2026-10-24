"""The wizard's "오늘 원문과 다르게 할 값" and the run view's chip (lane EASY, decision 2).

After the safety check the sheet lists the values the server sent -- each
with its step, kind and source value -- and a box for a new value beside
each. The page sends only what a person typed (an empty list when nothing:
the step skipped), shows the server's refusal sentence as it came, and lists
on the start step the values the server kept, in the server's words. During
the run the step card shows the current step's changed values exactly as
the state names them ("20분(오늘 바꾼 값 · 원문 15분)").
"""

from __future__ import annotations

import unittest

from tests.test_lane_on_wizard_screen import run

CHANGES = r"""
const VALUES={protocol_id:"p-1",target:{kind:"analysis",analysis_revision_number:1},revision_id:"pdf-1-analysis-1",limit:12,values:[
 {item_id:"s2#1",step_id:"s2",step_label:"2",kind:"time",kind_ko:"시간",number:"10",unit:"min",written:"10 min",shown_ko:"10분",units_ko:"초·분·시간"},
 {item_id:"s2#2",step_id:"s2",step_label:"2",kind:"temperature",kind_ko:"온도",number:"37",unit:"°C",written:"37 C",shown_ko:"37°C",units_ko:"°C"}]};
const KEPT={change_set_id:"run-changes-1",source:"wizard",changes:[{item_id:"s2#1",step_id:"s2",step_label:"2",kind:"time",kind_ko:"시간",shown_ko:"20분",source_shown_ko:"10분",words_ko:"20분(오늘 바꾼 값 · 원문 10분)"}]};
let changesAnswer=VALUES,choiceAnswer=KEPT;
const changesRoute=["/start-check/changes",call=>call.method==="POST"?(typeof choiceAnswer==="function"?choiceAnswer(call):choiceAnswer):(typeof changesAnswer==="function"?changesAnswer(call):changesAnswer)];
async function toChanges(){
 await open([changesRoute,["/start-check/safety",{ready:true,reason:null,safety_acknowledged:true}],["/start-check",{enabled:true,questions:[],skipped_reason:"no_numeric_values",target:{kind:"analysis"}}],...base()]);
 wizardGo("protocol");await settle();
 walk(body()).filter(el=>el.className==="wizard-option-pick")[0].dispatch("click");await settle(8);
 primary().dispatch("click");await settle(8);
 assert(wizard.step==="safety",`step: ${wizard.step}`);
 primary().dispatch("click");await settle(8);
 assert(wizard.step==="changes",`step: ${wizard.step}`);
}
const changeInput=id=>walk(body()).find(el=>el.id===`wizard-change-${id}`);
"""


class TheChangesStepTests(unittest.TestCase):
    def test_the_list_is_the_servers_and_nothing_typed_skips_with_an_empty_choice(self) -> None:
        result = run(CHANGES + r"""
await toChanges();
assert(title()==="오늘 원문과 다르게 할 값",title());
const text=walk(body()).map(el=>el.textContent).join(" ");
assert(text.includes("2단계 · 시간 · 원문 10분")&&text.includes("2단계 · 온도 · 원문 37°C"),text);
assert(changeInput("s2#1")&&changeInput("s2#1").placeholder==="새 값 · 초·분·시간","no box for the time");
assert(primary().textContent==="바꿀 값 없이 다음",primary().textContent);
choiceAnswer={change_set_id:"run-changes-2",source:"wizard",changes:[]};
primary().dispatch("click");await settle(8);
const post=posts().find(call=>call.url.endsWith("/start-check/changes"));
assert(post&&JSON.stringify(post.body.changes)==="[]"&&post.body.target.kind==="analysis"&&post.body.source==="wizard",JSON.stringify(post&&post.body));
assert(wizard.step==="start",`step: ${wizard.step}`);
assert(!walk(body()).some(el=>String(el.textContent).startsWith("오늘 바꾼 값")),"a skipped step listed values");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_typed_value_is_sent_and_the_start_lists_the_servers_words(self) -> None:
        result = run(CHANGES + r"""
await toChanges();
const box=changeInput("s2#1");box.value="20분";box.dispatch("input");await settle();
assert(primary().textContent==="이 값으로 할게요 (1개)",primary().textContent);
primary().dispatch("click");await settle(8);
const post=posts().find(call=>call.url.endsWith("/start-check/changes"));
assert(JSON.stringify(post.body.changes)===JSON.stringify([{item_id:"s2#1",value:"20분"}]),JSON.stringify(post.body));
assert(wizard.step==="start",`step: ${wizard.step}`);
const text=walk(body()).map(el=>el.textContent).join(" ");
assert(text.includes("오늘 바꾼 값 · 2단계 시간 20분(오늘 바꾼 값 · 원문 10분)"),text);
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_servers_refusal_is_shown_as_it_came_and_the_step_stays(self) -> None:
        result = run(CHANGES + r"""
await toChanges();
const box=changeInput("s2#1");box.value="40도";box.dispatch("input");await settle();
const message="2단계 10분: 단위 종류가 달라요. 원문 값(10분)과 같은 종류인 초·분·시간 단위로 넣어 주세요.";
choiceAnswer=()=>json({detail:{code:"run_change_unit_kind",message_ko:message}},false);
primary().dispatch("click");await settle(8);
assert(wizard.step==="changes",`step: ${wizard.step}`);
assert(node("wizard-note").textContent===message,node("wizard-note").textContent);
assert(changeInput("s2#1").value==="40도","the typed value was lost");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_failed_list_is_said_with_a_retry_and_nothing_is_sent(self) -> None:
        result = run(CHANGES + r"""
changesAnswer=()=>json({detail:{code:"run_change_unavailable",message_ko:"실행할 수 있는 분석이 없어요."}},false);
await toChanges();
assert(node("wizard-lead").textContent.includes("실행할 수 있는 분석이 없어요."),node("wizard-lead").textContent);
assert(primary().textContent==="다시 불러오기",primary().textContent);
changesAnswer=VALUES;primary().dispatch("click");await settle(8);
assert(wizard.step==="changes"&&changeInput("s2#1"),"the retry did not load the list");
assert(!posts().some(call=>call.url.endsWith("/start-check/changes")),"a failed list sent a choice");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_stale_start_returns_to_the_changes_step(self) -> None:
        result = run(CHANGES + r"""
await toChanges();
closeWizardSheet({silent:true});
returnToStartStep("run_changes_stale","오늘 바꾼 값이 지금 분석과 맞지 않아요. 시작 화면에서 다시 골라 주세요.");await settle(8);
assert(wizard.open===true&&wizard.step==="changes",`step: ${wizard.step}`);
assert(node("wizard-note").textContent.includes("다시 골라 주세요"),node("wizard-note").textContent);
""")
        self.assertEqual(result.returncode, 0, result.stderr)


class TheRunViewChipTests(unittest.TestCase):
    def test_the_step_card_shows_the_current_steps_changes_in_the_servers_words(self) -> None:
        result = run(r"""
assert(renderRunChanges([{kind_ko:"시간",words_ko:"20분(오늘 바꾼 값 · 원문 15분)"}])===true,"nothing drawn");
const list=node("procedure-run-changes");
assert(list.hidden===false&&list.children.length===1,"the chip is hidden");
assert(list.children[0].textContent==="시간 20분(오늘 바꾼 값 · 원문 15분)",list.children[0].textContent);
assert(renderRunChanges([])===false&&list.hidden===true&&list.children.length===0,"an empty list kept the chip");
renderRunChanges([{kind_ko:"<b>",words_ko:"<img src=x>"}]);
assert(list.children[0].children.length===0&&list.children[0].textContent==="<b> <img src=x>","server words became HTML");
""")
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
