"""Lane UI0, decision 2 (2026-10-12): the 실험 중 view, after the draft's M2.

The run view draws only what the server sent: the step timer as the server
times it (도는 중, 1분 남음, 끝남 · 지남, 없음) with the server's own notice
line, one line of voice from the words said or heard, and a card under the
step for the server's answer to the last turn -- 확인 대기, 되묻기, 답변 with
where its words come from, 기록 -- named by the server's operation. What the
server does not send (a value set against the source's, a clarification's
candidates as a list, the next step's words) is not drawn.
"""

from __future__ import annotations

import unittest

from tests.test_screen_cleanup import INDEX, run_page_script

SETUP = r"""
const configuration_id=82;
acceptedSessionConfiguration={configuration_id,mode:"cascade",language:"ko",server_generation:3,protocol_id:"protocol-x",revision_id:"pdf-1-analysis-1"};socket=new WS();sessionActive=true;
document.body=new Element("body");
async function say(message){await onMessage({data:JSON.stringify(message)},sessionGeneration,socket);}
let revision=0;
const timer=(seconds,extra={})=>({step:{state:seconds>0?"running":"expired",duration_seconds:900,name:"15분",step_id:"step-4",step_index:3,deadline_at:new Date(Date.now()+seconds*1000).toISOString(),elapsed_seconds:900-seconds,remaining_seconds:Math.max(0,seconds),...extra}});
const state=(extra={})=>({attached:true,protocol_id:"protocol-x",revision_id:"pdf-1-analysis-1",display_name:"In-gel digestion",development_only:false,readiness_status:"guidance_ready",active:true,current_step_label:"4",current_step_id:"step-4",total_steps:12,at_final_step:false,block_reason:null,revision:++revision,display_summary:"Add 100 µL of buffer.",primary_summary:"완충액 100 µL 를 넣습니다.",source_language:"en",spoken_summary:null,warning_texts:[],warning_presentations:[],visual_assets:[],visual_status:"unavailable",source_page_refs:[4],workflow_status:"running",timers:{step:null},...extra});
async function step(extra={}){await say({type:"protocol.fixture.state",configuration_id,action:"start",state:state(extra)});}
async function turn(id,heard,operation,reply){
 await say({type:"speech.start",turn_id:id,generation:3});
 if(heard)await say({type:"transcript",turn_id:id,generation:3,text:heard});
 if(operation)await say({type:"server.operation",turn_id:id,generation:3,route:"curated_protocol",operation});
 await say({type:"reply.delta",turn_id:id,generation:3,segment_index:0,...reply});
 await say({type:"reply.complete",turn_id:id,generation:3,text:reply.text,display_document:reply.display_document??null});
}
const card=()=>node("run-card");
const kids=item=>item.children.flatMap(child=>[child,...kids(child)]);
const badges=()=>kids(card()).filter(item=>String(item.className||"").split(" ").includes("run-badge")).map(item=>item.textContent);
"""


class TimerPanelTests(unittest.TestCase):

    def test_running_last_minute_ended_and_none_are_read_from_the_server_timer(self) -> None:
        result = run_page_script(SETUP + r"""
const panel=node("run-timer");
await step({timers:timer(612.4)});
assert(panel.dataset.timer==="running",`running: ${panel.dataset.timer}`);
assert(/^10:1\d$/.test(node("run-timer-digits").textContent),`digits: ${node("run-timer-digits").textContent}`);
assert(node("run-timer-foot").textContent==="15:00 중 남은 시간",`foot: ${node("run-timer-foot").textContent}`);
assert(node("run-timer-name").textContent==="15분 타이머",`name: ${node("run-timer-name").textContent}`);
assert(/^\d+%$/.test(node("run-timer-bar").style.width),`bar: ${node("run-timer-bar").style.width}`);
await step({timers:timer(48.2)});
assert(panel.dataset.timer==="soon",`last minute: ${panel.dataset.timer}`);
assert(node("run-timer-foot").textContent.startsWith("1분 남음"),`last minute foot: ${node("run-timer-foot").textContent}`);
await step({timers:timer(-133)});
assert(panel.dataset.timer==="done",`ended: ${panel.dataset.timer}`);
assert(node("run-timer-digits").textContent==="끝남",`ended digits: ${node("run-timer-digits").textContent}`);
assert(/^2:1\d 지남$/.test(node("run-timer-foot").textContent),`ended foot: ${node("run-timer-foot").textContent}`);
await step({timers:{step:{state:"not_started",duration_seconds:900,remaining_seconds:900,step_id:"step-4",deadline_at:null}}});
assert(panel.dataset.timer==="idle"&&node("run-timer-digits").textContent==="15:00"&&node("run-timer-foot").textContent==="시작 전",`not started: ${panel.dataset.timer} ${node("run-timer-digits").textContent}`);
await step({timers:{step:null}});
assert(panel.dataset.timer==="none"&&node("run-timer-digits").textContent==="--:--"&&node("run-timer-foot").textContent==="진행 중인 타이머 없음",`none: ${panel.dataset.timer}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_the_server_notice_is_shown_as_told_first_and_leaves_with_its_step(self) -> None:
        html = INDEX.read_text(encoding="utf-8")
        panel = html[html.index('id="run-timer"'):html.index("</aside>", html.index('id="run-timer"'))]
        self.assertIn('id="timer-notice-container"', panel)
        self.assertIn("먼저 알림", panel)
        result = run_page_script(SETUP + r"""
await step({timers:timer(48)});
await say({type:"protocol.timer.notice",configuration_id,generation:3,notice_kind:"timer_last_minute",text:"1분 남았어요.",chime:false,spoken:true,step_id:"step-4",step_label:"4"});
const row=node("timer-notice-container");
assert(row.hidden===false&&node("timer-notice").textContent==="1분 남았어요.","the notice line is not shown");
assert(row.dataset.stale==="false",`a current notice is stale: ${row.dataset.stale}`);
await step({current_step_label:"5",current_step_id:"step-5",timers:{step:null}});
assert(row.dataset.stale==="true","a notice about step 4 stays on step 5");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


class CaptionTests(unittest.TestCase):

    def test_the_line_of_voice_is_the_last_words_heard_or_said(self) -> None:
        result = run_page_script(SETUP + r"""
await step();
const line=()=>node("run-caption-text").textContent;
await say({type:"speech.start",turn_id:1,generation:3});
await say({type:"transcript",turn_id:1,generation:3,text:"타이머 얼마나 남았어?"});
assert(line()==="“타이머 얼마나 남았어?”",`heard: ${line()}`);
await say({type:"reply.delta",turn_id:1,generation:3,segment_index:0,text:"15분 타이머가\n10분 남았어요."});
assert(line()==="15분 타이머가 10분 남았어요.",`said: ${line()}`);
await say({type:"protocol.notice.speech",configuration_id,turn_id:2000000101,generation:3,notice_id:"n1",notice_kind:"timer_ended",text:"15분 타이머가 끝났어요."});
assert(line()==="15분 타이머가 끝났어요.",`notice: ${line()}`);
resetSessionUI();
assert(line()==="",`a new session kept the line: ${line()}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


class RunCardTests(unittest.TestCase):

    def test_a_completion_question_while_the_timer_runs_is_a_waiting_card(self) -> None:
        result = run_page_script(SETUP + r"""
await step({timers:timer(400)});
await turn(2,"다음 단계로 넘어가도 돼?","completion_confirmation_required",{text:"4단계 완료하셨나요?",answer_origin:"current_protocol",source_pages:[]});
assert(!card().hidden&&card().dataset.kind==="confirm",`kind: ${card().dataset.kind}`);
assert(card().textContent.startsWith("확인 대기"),`head: ${card().textContent}`);
assert(badges().includes("타이머 도는 중"),`badges: ${badges()}`);
assert(card().textContent.includes("4단계 완료하셨나요?"),`question: ${card().textContent}`);
await step({timers:{step:null}});
await turn(3,"다 했어","completion_confirmation_required",{text:"4단계 완료하셨나요?",answer_origin:"current_protocol",source_pages:[]});
assert(card().dataset.kind==="confirm"&&!badges().includes("타이머 도는 중"),`no timer, badges: ${badges()}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_an_answer_card_names_where_its_words_come_from(self) -> None:
        result = run_page_script(SETUP + r"""
await step();
await turn(3,"완충액 얼마나 넣어?","verified_fact_read",{text:"완충액을 100 µL 넣어요.",answer_origin:"current_protocol",source_pages:[4]});
assert(card().dataset.kind==="answer"&&card().textContent.startsWith("답변"),`answer: ${card().textContent}`);
assert(JSON.stringify(badges())==='["원문 p.4"]',`source badge: ${badges()}`);
assert(card().textContent.includes("질문 · 완충액 얼마나 넣어?")&&card().textContent.includes("완충액을 100 µL 넣어요."),`answer text: ${card().textContent}`);
await turn(4,"중탄산암모늄은 왜 넣어?","grounded_qa",{text:"일반적으로 완충액입니다.",answer_origin:"supplemental_model_knowledge",limitations:["outside_pdf_explanation"],source_pages:[],display_document:{title:"4단계",sections:[{kind:"notice",text:"PDF 밖 설명이에요."},{kind:"section",heading:"",text:"일반적으로 pH 를 맞추는 완충액입니다."}]}});
assert(JSON.stringify(badges())==='["일반 설명"]',`general badge: ${badges()}`);
assert(card().textContent.includes("PDF 밖 설명이에요.")&&card().textContent.includes("pH 를 맞추는 완충액"),`general text: ${card().textContent}`);
await turn(5,"트립신 보관은?","grounded_qa",{text:"PDF에는 따로 설명이 없어요.",answer_origin:"current_protocol",source_pages:[]});
assert(badges().length===0,`no source, badges: ${badges()}`);
await say({type:"research.result",configuration_id,turn_id:5,generation:3,status:"success",terminal_status:"success",primary_text:"일반적으로 영하 20도에 보관합니다.",answer_origin:"supplemental_model_knowledge",citations:[],limitations:["outside_pdf_explanation"],source_label:"AI 일반 지식",outside_pdf:true});
assert(badges().includes("일반 설명")&&card().textContent.includes("영하 20도"),`research added: ${card().textContent}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_a_clarification_is_the_server_question_and_no_invented_choices(self) -> None:
        result = run_page_script(SETUP + r"""
await step();
await turn(6,"그거 얼마나 넣어?","reference_clarification_required",{text:"'그거'가 어떤 시약인지 말해 주세요.",answer_origin:"current_protocol",source_pages:[]});
assert(card().dataset.kind==="clarify"&&card().textContent.startsWith("되묻기"),`clarify: ${card().textContent}`);
assert(card().textContent.includes("'그거'가 어떤 시약인지 말해 주세요."),"the server question is missing");
assert(badges().length===0,`choices were drawn: ${badges()}`);
assert(!/기준치/.test(card().textContent),"a value comparison was drawn");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_a_record_a_move_and_a_new_step_follow_the_server(self) -> None:
        result = run_page_script(SETUP + r"""
await step();
await turn(7,"0.5 mL 넣었어","experiment_observation_recorded",{text:"영 점 오 밀리리터로 기록했어요.",answer_origin:"current_protocol",source_pages:[]});
assert(card().dataset.kind==="record"&&card().textContent.startsWith("실험 관찰 기록"),`record: ${card().textContent}`);
await turn(8,"현재 단계 알려 줘","current_step_read",{text:"4단계입니다.",answer_origin:"current_protocol",source_pages:[4]});
assert(card().hidden,"a step read drew a card");
await turn(9,"완충액 얼마나 넣어?","verified_fact_read",{text:"100 µL 넣어요.",answer_origin:"current_protocol",source_pages:[4]});
assert(!card().hidden,"the answer card is missing");
await step({current_step_label:"5",current_step_id:"step-5"});
assert(card().hidden,"an answer about step 4 stays on step 5");
await say({type:"speech.start",turn_id:10,generation:3});
await say({type:"reply.complete",turn_id:10,generation:3,text:"서버가 이름 붙이지 않은 답"});
assert(card().hidden,"a reply without a server operation drew a card");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


class StatusLineTests(unittest.TestCase):

    def test_the_badge_connection_and_microphone_are_words(self) -> None:
        result = run_page_script(SETUP + r"""
hideMicRecovery();  // the fake DOM does not read the markup's hidden attribute
await step();
assert(node("run-analysis-state").textContent==="분석 통과"&&node("run-analysis-state").hidden===false,`analysis: ${node("run-analysis-state").textContent}`);
assert(node("run-connection").textContent==="연결됨",`connection: ${node("run-connection").textContent}`);
assert(node("run-mic").textContent==="마이크 꺼짐",`no microphone: ${node("run-mic").textContent}`);
micWorkletNode={port:{}};renderState("LISTENING");
assert(node("run-mic").textContent==="마이크 켜짐",`microphone: ${node("run-mic").textContent}`);
showMicRecovery("마이크 연결이 끊겼습니다.");
assert(node("run-mic").textContent==="마이크 끊김",`lost: ${node("run-mic").textContent}`);
hideMicRecovery();micWorkletNode=null;
await step({development_only:true});
assert(node("run-analysis-state").textContent==="개발용 큐레이션 분석",`development: ${node("run-analysis-state").textContent}`);
sessionActive=false;renderState("IDLE");
assert(node("run-connection").textContent==="연결 안 됨",`no session: ${node("run-connection").textContent}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_the_run_view_is_laid_out_for_landscape_portrait_and_a_tablet_with_64px_targets(self) -> None:
        html = INDEX.read_text(encoding="utf-8")
        style = html.split("<style>", 1)[1].split("</style>", 1)[0]
        self.assertIn("--target-run:64px", style)
        self.assertIn('body[data-view="during"] .procedure-card{display:grid;', style)
        self.assertIn("@media (orientation:portrait){", style)
        self.assertIn("@media (min-width:768px) and (min-height:700px){", style)
        self.assertIn('body[data-view="during"] .rail-right button{width:var(--run-action-w);height:var(--target-run)', style)
        # Lane UI3 decision 1 (2026-10-14): the server now sends the next
        # step's words (screen.next_step_sentence), so the line exists, hidden
        # until the server's sentence fills it (tests/test_lane_ui3_run_fields.py).
        self.assertIn('<p id="run-next-step" class="run-next-step" hidden></p>', html)


if __name__ == "__main__":
    unittest.main()
