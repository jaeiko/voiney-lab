"""Lane UI3, decision 1 (2026-10-14): the 실험 중 view draws lane RA's screen fields.

The server's ``protocol.fixture.state`` carries ``screen`` with the next
step's sentence, the kind of the open confirmation question, the candidates a
clarification asked back, the values a note read back and the deviation a note
recorded (lane RA, decision f). The page draws them as the drafts M2, M3a, M3b
and M5b show them -- and only them: what the server does not send is not drawn,
and the three per-turn fields go on the card of the turn whose state carried
them. The candidates are a list, not buttons: the server takes no typed turn.
"""

from __future__ import annotations

import unittest

from tests.test_lane_ui0_run_view import SETUP
from tests.test_screen_cleanup import INDEX, run_page_script

TURNS = SETUP + r"""
const screen=(extra={})=>({source_basis:null,safety_items:[],translation_source:"reviewed",translation_pending:false,repeat_round:null,open_question:null,source_figures:[],next_step_sentence:null,confirm_question:null,clarify_candidates:[],readback_values:[],record_deviation:null,...extra});
// The server's order for one turn: the state with its turn_id, the turn's name, its words.
async function told(id,heard,operation,reply,fields={},extra={}){
 await say({type:"speech.start",turn_id:id,generation:3});
 if(heard)await say({type:"transcript",turn_id:id,generation:3,text:heard});
 await say({type:"protocol.fixture.state",configuration_id,turn_id:id,generation:3,action:"current",state:state(extra),screen:screen(fields)});
 await say({type:"server.operation",turn_id:id,generation:3,route:"curated_protocol",operation});
 await say({type:"reply.delta",turn_id:id,generation:3,segment_index:0,...reply});
 await say({type:"reply.complete",turn_id:id,generation:3,text:reply.text,display_document:reply.display_document??null});
}
const said={answer_origin:"current_protocol",source_pages:[]};
const cls=name=>kids(card()).filter(item=>String(item.className||"").split(" ").includes(name));
"""


class NextStepTests(unittest.TestCase):

    def test_the_next_step_is_the_server_sentence_while_the_experiment_runs(self) -> None:
        html = INDEX.read_text(encoding="utf-8")
        primary = html.index('id="procedure-primary"')
        self.assertLess(primary, html.index('id="run-next-step"'))
        self.assertLess(html.index('id="run-next-step"'), html.index('id="procedure-open-question"'))
        result = run_page_script(TURNS + r"""
const line=node("run-next-step");
await say({type:"protocol.fixture.state",configuration_id,action:"start",state:state(),screen:screen({next_step_sentence:"  12,000 × g 에서\n원심분리합니다. "})});
assert(line.hidden===false&&line.textContent==="다음 단계: 12,000 × g 에서 원심분리합니다.",`next: ${line.hidden} ${line.textContent}`);
await say({type:"protocol.fixture.state",configuration_id,action:"next",state:state({current_step_label:"12",current_step_id:"step-12",at_final_step:true}),screen:screen({next_step_sentence:null})});
assert(line.hidden===true&&line.textContent==="",`the last step kept a next step: ${line.textContent}`);
await say({type:"protocol.fixture.state",configuration_id,action:"attached",state:state({active:false,workflow_status:"ready"}),screen:screen({next_step_sentence:"원심분리합니다."})});
assert(line.hidden===true,"a next step was drawn before the experiment runs");
await say({type:"protocol.fixture.state",configuration_id,action:"start",state:state(),screen:screen({next_step_sentence:"원심분리합니다."})});
assert(line.hidden===false,"the line did not come back");
resetSessionUI();
assert(line.hidden===true&&line.textContent==="",`a new session kept the line: ${line.textContent}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


class ConfirmationHeadTests(unittest.TestCase):

    def test_the_card_head_names_the_kind_the_server_sends(self) -> None:
        result = run_page_script(TURNS + r"""
await step();
const head=()=>cls("run-card-kind")[0].textContent;
await told(2,"다 했어","completion_confirmation_required",{text:"4단계 완료하셨나요?",...said},{confirm_question:{kind:"completion",text:"4단계 완료하셨나요?"}});
assert(card().dataset.kind==="confirm"&&head()==="완료 확인",`completion: ${head()}`);
await told(3,"OD 0.82 기록해","completion_confirmation_required",{text:"OD600 0.82, 맞습니까?",...said},{confirm_question:{kind:"value",text:"OD600 0.82, 맞습니까?"}});
assert(head()==="값 기록 확인",`value: ${head()}`);
await told(4,"시끄러워","completion_confirmation_required",{text:"시끄러운 곳 모드로 바꿀까요?",...said},{confirm_question:{kind:"ambient",text:"시끄러운 곳 모드로 바꿀까요?"}});
assert(head()==="시끄러운 곳 확인",`ambient: ${head()}`);
await told(5,"타이머 끝났어","completion_confirmation_required",{text:"타이머를 끝낼까요?",...said},{confirm_question:{kind:"timer",text:"타이머를 끝낼까요?"}});
assert(head()==="확인 대기",`a kind the decision does not name: ${head()}`);
await told(6,"다 했어","completion_confirmation_required",{text:"4단계 완료하셨나요?",...said},{confirm_question:null});
assert(head()==="확인 대기",`no kind sent: ${head()}`);
await told(7,"완충액 얼마나?","verified_fact_read",{text:"100 µL 넣어요.",...said,source_pages:[4]},{confirm_question:{kind:"completion",text:"4단계 완료하셨나요?"}});
assert(head()==="답변",`an answer took a confirmation head: ${head()}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


class RecordValueTests(unittest.TestCase):

    def test_read_back_values_are_large_and_a_deviation_names_the_source_value(self) -> None:
        result = run_page_script(TURNS + r"""
await step();
await told(8,"40도 1000 rpm 으로 돌렸어 기록해","experiment_observation_recorded",{text:"편차로 기록했어요: 40 °C, 1000 rpm으로 하셨고, 원문은 37 °C · 800 rpm이에요.",...said},{readback_values:["40 °C","1000 rpm"],record_deviation:{step_label:"4",content:"40도 1000 rpm",deviations:[{kind:"temperature",said:"40 °C",number:"40",unit:"°C",source:"37 °C"},{kind:"speed",said:"1000 rpm",number:"1000",unit:"rpm",source:"800 rpm"}]}});
assert(card().dataset.kind==="record"&&card().dataset.deviation==="true",`record: ${card().dataset.kind} ${card().dataset.deviation}`);
const values=cls("run-card-value");
assert(values.map(value=>value.textContent).join("|")==="40 °C|1000 rpm",`values: ${values.map(value=>value.textContent)}`);
assert(values.every(value=>value.dataset.deviation==="true"),"the differing values are not marked");
const line=cls("run-card-deviation");
assert(line.length===1&&line[0].textContent==="기준치와 다름원문 37 °C · 800 rpm",`deviation: ${line.map(item=>item.textContent)}`);
assert(cls("run-tag")[0].textContent==="기준치와 다름","the tag");
assert(card().textContent.includes("편차로 기록했어요"),"the server's sentence is gone");
await told(9,"0.5 mL 넣었어 기록해","experiment_observation_recorded",{text:"영 점 오 밀리리터로 기록했어요.",...said},{readback_values:["0.5 mL"],record_deviation:null});
assert(cls("run-card-value").map(value=>value.textContent).join("|")==="0.5 mL"&&cls("run-card-value")[0].dataset.deviation==="false",`a value: ${card().textContent}`);
assert(cls("run-card-deviation").length===0&&card().dataset.deviation==="","a deviation outlived its turn");
assert(!/기준치/.test(card().textContent),"a value comparison was drawn for a value that matched");
await told(10,"색이 노랗게 변했어 기록해","experiment_observation_recorded",{text:"4단계에 기록했어요.",...said},{});
assert(cls("run-card-values").length===0,"a note without values drew values");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_the_fields_go_on_their_own_turn_card_only(self) -> None:
        result = run_page_script(TURNS + r"""
await step();
await told(11,"0.5 mL 넣었어 기록해","experiment_observation_recorded",{text:"영 점 오 밀리리터로 기록했어요.",...said},{readback_values:["0.5 mL"]});
assert(cls("run-card-value").length===1,"the turn's own values are missing");
// The next turn's state comes before its name: the card of turn 11 does not take turn 12's fields.
await say({type:"speech.start",turn_id:12,generation:3});
await say({type:"protocol.fixture.state",configuration_id,turn_id:12,generation:3,action:"current",state:state(),screen:screen({readback_values:["37 °C"]})});
assert(cls("run-card-value").length===0,`turn 12's values were drawn on turn 11: ${card().textContent}`);
await say({type:"server.operation",turn_id:12,generation:3,route:"curated_protocol",operation:"experiment_observation_recorded"});
await say({type:"reply.complete",turn_id:12,generation:3,text:"37도로 기록했어요."});
assert(cls("run-card-value").map(value=>value.textContent).join("|")==="37 °C",`turn 12: ${card().textContent}`);
// A state without a turn (a pause, a translation) keeps the fields with their turn.
await say({type:"protocol.fixture.state",configuration_id,action:"translation_update",state:state(),screen:screen({readback_values:["37 °C"]})});
assert(cls("run-card-value").length===1,"a state without a turn dropped the values");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


class CandidateTests(unittest.TestCase):

    def test_candidates_are_a_list_to_say_not_buttons(self) -> None:
        result = run_page_script(TURNS + r"""
await step();
await told(13,"그거 얼마나 넣어?","reference_clarification_required",{text:"어떤 시약인가요?\n1. TBST\n2. Blocking buffer",...said},{clarify_candidates:["TBST","Blocking buffer"]});
assert(card().dataset.kind==="clarify",`kind: ${card().dataset.kind}`);
const list=cls("run-card-choices");
assert(list.length===1&&list[0].tagName==="ul",`list: ${list.map(item=>item.tagName)}`);
assert(list[0].children.map(item=>`${item.tagName}:${item.textContent}`).join("|")==="li:TBST|li:Blocking buffer",list[0].children.map(item=>item.textContent).join("|"));
assert(kids(card()).every(item=>item.tagName!=="button"),"a candidate became a button the server cannot take");
assert(cls("run-card-choices-label")[0].textContent==="후보 · 말로 고르기","the list does not say how to choose");
assert(cls("run-card-body")[0].textContent==="어떤 시약인가요?",`the numbered list was repeated: ${cls("run-card-body")[0].textContent}`);
await told(14,"그거 얼마나 넣어?","reference_clarification_required",{text:"'그거'가 어떤 시약인지 말해 주세요.",...said},{clarify_candidates:[]});
assert(cls("run-card-choices").length===0,"candidates were drawn with none sent");
assert(cls("run-card-body")[0].textContent==="'그거'가 어떤 시약인지 말해 주세요.","the question changed");
await told(15,"버퍼 얼마나?","verified_fact_read",{text:"100 µL 넣어요.",...said},{clarify_candidates:["TBST"]});
assert(cls("run-card-choices").length===0,"an answer card drew candidates");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_the_new_parts_are_styled_for_the_run_view_only(self) -> None:
        html = INDEX.read_text(encoding="utf-8")
        style = html.split("<style>", 1)[1].split("</style>", 1)[0]
        self.assertIn(".run-next-step{display:none}", style)
        self.assertIn('body[data-view="during"] .run-next-step:not([hidden]){display:block;', style)
        self.assertIn(".run-card-choices li{display:inline-flex;align-items:center;min-height:48px;", style)
        self.assertIn(".procedure-card .run-card-value{color:var(--ink);font-size:2.5rem;", style)
        self.assertIn(".procedure-card .run-card-deviation{", style)
        landscape = style[style.index(".run-card-choices li{display:inline-flex"):]
        landscape = landscape[landscape.index("@media (orientation:landscape) and (max-height:480px){"):]
        landscape = landscape[:landscape.index("\n    }")]
        self.assertIn('body[data-view="during"] .run-left:has(> .run-card:not([hidden])) .run-next-step{display:none}', landscape)


if __name__ == "__main__":
    unittest.main()
