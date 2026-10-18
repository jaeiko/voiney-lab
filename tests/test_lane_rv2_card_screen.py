"""The review as one card at a time (lane RV2, stage ②).

Human decision 2026-10-16: review is not a checkbox list or a table to edit;
one card at a time, chosen by buttons. "2 / 5" above; the piece of the page
and the value as read in large type; two or three big buttons; a press moves
to the next card by itself, "← 이전" goes back. After the last card, the
reviewer's name (the last one remembered) and "검토 완료하고 등록"; the versions
folded. 64 px buttons, keys work, one column on a phone. Only the buttons the
server allows are drawn. The card is one function that draws whatever card
the server sends from its fields (a list of values, pictures) -- never by its
type -- so the start check and lane AN3's page card can use it as it is.
"""

from __future__ import annotations

import re
import unittest

from tests.test_screen_cleanup import INDEX, run_page_script

SETUP = r"""
const target={kind:"review_pending",event_id:"analysis-review-pending-a",recorded_at:"2026-10-16T01:00:00+00:00",revision_id:null,analysis_id:"a"};
const choice=(id,label,request,extra={})=>({choice_id:id,label_ko:label,request,needs:null,route:null,primary:false,...extra});
const evidence={card_id:"step:s2",item_id:"step:s2",position:1,total:5,progress_ko:"1 / 5",type:"evidence",kind:"step",kind_ko:"단계",step_label:"2",
 question_ko:"이 단계의 근거를 원문에서 찾지 못했어요. 원문의 어느 줄인가요?",reasons_ko:["원문에 없음"],shown:"Spin for an hour.",
 source:{page:1,excerpt:"Spin for an hour.",verified:false,lines:null,suggested_lines:{page:1,first_line:4,last_line:4,similarity:0.7,unique:false}},
 choices:[choice("confirm_lines","고른 줄이 맞아요",{item_id:"step:s2",action:"confirm"},{needs:"source_lines",primary:true}),
  choice("fix","직접 고칠게요",{item_id:"step:s2",action:"fix"},{needs:"source_lines_and_text"}),
  choice("reanalysis","다시 분석",null,{route:"reanalysis"})],
 proposal:null,change:null,readings:null,steps:null,memo_allowed:true,decided:false,decision:null,current:"Spin for an hour."};
const ocrValue={card_id:"ocr_value:2:4000rpm",item_id:"ocr_value:2:4000rpm",position:2,total:5,progress_ko:"2 / 5",type:"ocr_value",kind:"ocr_value",kind_ko:"OCR 값",step_label:null,
 question_ko:"원문 그림에서 이 값이 4000 rpm 맞나요?",reasons_ko:["OCR 엔진 하나만 읽은 쪽"],shown:"4000 rpm",
 source:{page:2,verified:true,lines:{page:2,first_line:2,last_line:2},value_boxes:[0]},
 choices:[choice("confirm","4000 rpm 맞아요",{item_id:"ocr_value:2:4000rpm",action:"confirm"},{primary:true}),choice("fix","직접 입력",{item_id:"ocr_value:2:4000rpm",action:"fix"},{needs:"text"})],
 proposal:"4000 rpm",change:null,readings:null,steps:null,memo_allowed:true,decided:false,decision:null,current:"4000 rpm"};
// A card of a type this screen has never heard of, with a list of values and their own buttons.
const page3={card_id:"ocr_page:3",item_id:"ocr_page:3",position:3,total:5,progress_ko:"3 / 5",type:"anything_new",kind:"anything_new",kind_ko:"OCR 값",page:3,step_label:null,
 question_ko:"원문 3쪽 그림의 값 1개를 확인해 주세요.",reasons_ko:[],shown:null,source:{page:3,value_boxes:[1]},
 values:[{item_id:"ocr_value:3:5min",shown:"5 min",reason_ko:"두 OCR 엔진이 이 값을 다르게 읽음",readings:[{engine:"google",text:"5 min",token:"5min"},{engine:"clova",text:"6 min",token:"6min"}],value_boxes:[1],
  choices:[choice("reading_0","5 min",{item_id:"ocr_value:3:5min",action:"confirm"},{primary:true}),choice("reading_1","6 min",{item_id:"ocr_value:3:5min",action:"fix",text:"6 min"}),choice("fix","직접 입력",{item_id:"ocr_value:3:5min",action:"fix"},{needs:"text"})],
  decided:false,decision:null,current:"5 min"}],
 choices:[choice("confirm_all","모두 맞아요",{item_id:"ocr_page:3",action:"confirm"},{primary:true})],proposal:null,change:null,readings:null,steps:null,memo_allowed:false,decided:false,decision:null,current:null};
const timer={card_id:"timer:s2",item_id:"timer:s2",position:4,total:5,progress_ko:"4 / 5",type:"value_changed",kind:"timer",kind_ko:"단계 타이머",step_label:"2",
 question_ko:"10 min → 15 min(으)로 고쳤어요. 이 단계 타이머에도 그 값이 있어요. 이렇게 바꿀까요?",reasons_ko:["고친 값이 들어 있음"],shown:"10 min",source:null,
 choices:[choice("fix_proposed","이렇게 바꿔요",{item_id:"timer:s2",action:"fix",seconds:900},{primary:true}),choice("fix","다시 고칠게요",{item_id:"timer:s2",action:"fix"},{needs:"seconds"})],
 proposal:"15분",change:{from:"10 min",to:"15 min",source_item:"action:a2/estimated_duration"},readings:null,steps:null,memo_allowed:true,decided:false,decision:null,current:"10 min"};
const seam={card_id:"seam:p1.s12+p2.s13",item_id:"seam:p1.s12+p2.s13",position:5,total:5,progress_ko:"5 / 5",type:"numbering_seam",kind:"seam",kind_ko:"조각 경계",step_label:null,
 question_ko:"번호를 붙인 부분과 붙이지 않은 부분이 만나는 곳이에요. 앞 단계 다음에 뒤 단계가 이어지나요?",reasons_ko:[],shown:"조각 경계",source:null,
 choices:[choice("confirm","이어져요",{item_id:"seam:p1.s12+p2.s13",action:"confirm"},{primary:true}),choice("reanalysis","아니요",null,{route:"reanalysis"})],
 proposal:null,change:null,readings:null,steps:[{step_id:"p1.s12",label:"12",label_shown:"12",text:"12. Wash twice.",page:14,lines:{page:14,first_line:3,last_line:3}},{step_id:"p2.s13",label:"",label_shown:"13",text:"Dry the pellet.",page:15,lines:{page:15,first_line:1,last_line:1}}],
 memo_allowed:false,decided:false,decision:null,current:null};
const readings={card_id:"step:p1.s4",item_id:"step:p1.s4",position:1,total:1,progress_ko:"1 / 1",type:"two_readings",kind:"step",kind_ko:"단계",step_label:"4",
 question_ko:"두 조각이 2쪽을 다르게 읽었어요. 어느 쪽이 맞나요?",reasons_ko:["두 가지로 읽힘"],shown:"4. Spin at 500 x g",source:{page:2,verified:true},
 choices:[choice("choose_a","이쪽으로 읽기",{item_id:"step:p1.s4",action:"choose",reading:"a"},{primary:true}),choice("choose_b","저쪽으로 읽기",{item_id:"step:p1.s4",action:"choose",reading:"b"}),choice("fix","직접 고칠게요",{item_id:"step:p1.s4",action:"fix"},{needs:"text"})],
 proposal:null,change:null,readings:[[{step_id:"p1.s4",label:"4",text:"4. Spin at 500 x g",page:2,lines:{page:2,first_line:3,last_line:3}}],[{step_id:"p2.s4",label:"4",text:"Spin at 500 x g for 5 min.",page:2,lines:{page:2,first_line:3,last_line:3}}]],steps:null,
 memo_allowed:true,decided:false,decision:null,current:"4. Spin at 500 x g"};
const cards=[evidence,ocrValue,page3,timer,seam];
const draft=(extra={})=>({protocol_id:"p-1",title:"Gamma",analysis_status:"review_pending",available_for_execution:false,target,items:[],cards,next_card:0,
 counts:{items:9,needs_review:5,decided:0,undecided:5,changed:0},pages:[],registrable:false,registration_blockers:[],...extra});
const decided=(card,action="confirm")=>({...card,decided:true,decision:{action}});
const page1={protocol_id:"p-1",page:1,page_count:3,ocr:false,ocr_unclear:false,unclear_line_count:0,value_boxes:[],
 lines:["Protocol Gamma","Section preparation","1. Add 2 mL of buffer A.","2. Incubate for 10 min at 37 C.","Wear gloves."].map((text,index)=>({number:index+1,text,has_value:/\d/.test(text),unclear:false}))};
const page2={...page1,page:2,lines:[{number:1,text:"Section wash",has_value:false,unclear:false},{number:2,text:"3. Spin for an hour at 4 C.",has_value:true,unclear:false}]};
const calls=[];
const serve=answers=>{globalThis.fetch=async(url,options={})=>{calls.push({url:String(url),method:options.method||"GET",body:typeof options.body==="string"?JSON.parse(options.body):null});for(const [pattern,answer] of answers){if(String(url).includes(pattern))return typeof answer==="function"?answer(options):json(answer);}throw new Error(`unexpected ${url}`);};};
const tick=()=>new Promise(resolve=>setTimeout(resolve,0));
const walk=(el,found=[])=>{found.push(el);for(const child of el.children||[])walk(child,found);return found;};
const buttonsIn=host=>walk(host).filter(el=>el.tagName==="button");
const labels=host=>buttonsIn(host).map(button=>button.textContent);
const press=async(host,words)=>{const button=buttonsIn(host).find(b=>b.textContent===words);assert(button,`no "${words}" in ${labels(host)}`);button.dispatch("click");await tick();await tick();await tick();};
const posts=()=>calls.filter(call=>call.method==="POST");
const card=()=>node("review-card");
const answer=(cardsAfter,next)=>({item:{},counts:{},registrable:next===null,target,cards:cardsAfter,next_card:next});
"""


def run(body: str):
    return run_page_script(SETUP + body)


class OneCardAtATimeTests(unittest.TestCase):

    def test_the_card_shows_its_question_value_progress_and_only_the_servers_buttons(self) -> None:
        result = run(r"""
serve([["/review-draft/source-pages/1",page1],["/review-draft",draft({next_card:1})]]);
await loadReviewDraft("p-1");await tick();
const editor=node("protocol-review-editor");
assert(editor.hidden===false&&editor.open===true,"the review did not open for an analysis waiting for a person");
assert(node("review-card-progress").textContent==="2 / 5",node("review-card-progress").textContent);
const text=visibleText(card());
for(const words of ["OCR 값","원문 그림에서 이 값이 4000 rpm 맞나요?","OCR 엔진 하나만 읽은 쪽","4000 rpm"])assert(text.includes(words),`${words}: ${text}`);
const big=walk(card()).find(el=>el.className==="choice-card-value");
assert(big&&big.textContent==="4000 rpm",`value: ${big?.textContent}`);
assert(JSON.stringify(labels(card()).filter(label=>label!=="메모 남기기"))===JSON.stringify(["4000 rpm 맞아요","직접 입력"]),`buttons: ${labels(card())}`);
const image=walk(card()).find(el=>el.tagName==="img");
assert(image&&image.src==="/api/protocols/p-1/review-draft/source-pages/2/value-boxes/0/image",`picture: ${image?.src}`);
assert(node("review-card-prev").disabled===false&&node("review-card-next").hidden===true,"navigation");
assert(node("review-register-block").hidden===true,"the name and register showed before the last card");
assert(node("review-editor-summary").textContent===" · 확인할 카드 5장 · 남은 5장",node("review-editor-summary").textContent);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_a_press_sends_the_buttons_request_and_moves_on_and_back_goes_back(self) -> None:
        result = run(r"""
serve([["/review-decisions",answer([evidence,decided(ocrValue),page3,timer,seam],2)],["/review-draft",draft({next_card:1})]]);
await loadReviewDraft("p-1");await tick();
node("reviewer-name").value=" 김재준 ";
await press(card(),"4000 rpm 맞아요");
const [post]=posts();
assert(post.url==="/api/protocols/p-1/review-decisions",post.url);
assert(JSON.stringify(post.body)===JSON.stringify({item_id:"ocr_value:2:4000rpm",action:"confirm",target,reviewer_name:"김재준"}),JSON.stringify(post.body));
assert(node("review-card-progress").textContent==="3 / 5",`moved to ${node("review-card-progress").textContent}`);
assert(visibleText(card()).includes("원문 3쪽 그림의 값 1개를 확인해 주세요."),visibleText(card()));
assert(calls.filter(call=>call.url.endsWith("/review-draft")).length===1,"the draft was read again after a press");
node("review-card-prev").dispatch("click");await tick();
assert(node("review-card-progress").textContent==="2 / 5"&&visibleText(card()).includes("확인함"),visibleText(card()));
assert(node("review-card-next").hidden===false,"no way past a decided card");
node("review-card-next").dispatch("click");await tick();
assert(node("review-card-progress").textContent==="3 / 5",node("review-card-progress").textContent);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_a_refusal_is_said_on_the_card_in_words(self) -> None:
        result = run(r"""
serve([["/review-decisions",()=>json({detail:"review_decision_invalid"},false)],["/review-draft",draft({next_card:1})]]);
await loadReviewDraft("p-1");await tick();
await press(card(),"4000 rpm 맞아요");
assert(visibleText(card()).includes("이 항목에는 그렇게 할 수 없습니다."),visibleText(card()));
assert(node("review-card-progress").textContent==="2 / 5","moved on after a refusal");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


class WhatAButtonNeedsTests(unittest.TestCase):

    def test_lines_are_picked_on_the_card_before_a_button_that_needs_them(self) -> None:
        result = run(r"""
serve([["/review-draft/source-pages/2",page2],["/review-draft/source-pages/1",page1],["/review-decisions",answer([decided(evidence),ocrValue,page3,timer,seam],1)],["/review-draft",draft()]]);
await loadReviewDraft("p-1");await tick();await tick();await tick();
const lines=()=>walk(card()).filter(el=>String(el.className).startsWith("review-line")&&el.tagName==="button");
assert(lines().length===5,`lines: ${lines().length}`);
assert(lines()[3].className.includes("is-suggested"),lines().map(line=>line.className).join("|"));
await press(card(),"고른 줄이 맞아요");
assert(posts().length===0,"sent without the lines");
assert(visibleText(card()).includes("원문에서 이 항목이 있는 줄을 눌러 골라 주세요."),visibleText(card()));
await tick();await tick();
// The line may stand on another page.
const turn=buttonsIn(card()).find(button=>button.textContent==="다음 쪽");turn.dispatch("click");await tick();await tick();await tick();
assert(visibleText(card()).includes("원문 p.2"),visibleText(card()));
lines()[1].dispatch("click");await tick();await tick();await tick();
assert(visibleText(card()).includes("고른 줄 · 원문 p.2 2줄"),visibleText(card()));
await press(card(),"고른 줄이 맞아요");
assert(JSON.stringify(posts()[0].body)===JSON.stringify({item_id:"step:s2",action:"confirm",target,source_lines:{page:2,first_line:2,last_line:2}}),JSON.stringify(posts()[0].body));
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_a_button_that_needs_words_opens_an_input_with_the_value_written(self) -> None:
        result = run(r"""
serve([["/review-decisions",answer([evidence,decided(ocrValue,"fix"),page3,timer,seam],2)],["/review-draft",draft({next_card:1})]]);
await loadReviewDraft("p-1");await tick();
await press(card(),"직접 입력");
assert(posts().length===0,"sent before anything was written");
const input=walk(card()).find(el=>el.tagName==="input"&&el.type==="text"&&el.className==="review-input"&&el.attributes["aria-label"]==="고친 값");
assert(input&&input.value==="4000 rpm",`input: ${input?.value}`);
input.value="400 rpm";
await press(card(),"보내기");
assert(JSON.stringify(posts()[0].body)===JSON.stringify({item_id:"ocr_value:2:4000rpm",action:"fix",target,text:"400 rpm"}),JSON.stringify(posts()[0].body));
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_a_timer_is_written_in_minutes_and_sent_in_seconds(self) -> None:
        result = run(r"""
serve([["/review-decisions",answer([evidence,ocrValue,page3,decided(timer,"fix"),seam],4)],["/review-draft",draft({next_card:3})]]);
await loadReviewDraft("p-1");await tick();
const text=visibleText(card());
for(const words of ["고친 값 · 10 min → 15 min","이렇게 · 15분","2단계 · 단계 타이머"])assert(text.includes(words),`${words}: ${text}`);
await press(card(),"다시 고칠게요");
const input=walk(card()).find(el=>el.tagName==="input"&&el.type==="number");
assert(input&&input.attributes["aria-label"]==="타이머(분)",`timer input: ${input?.type}`);
input.value="20";await press(card(),"보내기");
assert(posts()[0].body.seconds===1200&&posts()[0].body.action==="fix",JSON.stringify(posts()[0].body));
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_the_reanalysis_route_asks_for_one_more_analysis_not_a_decision(self) -> None:
        result = run(r"""
serve([["/review-draft",draft({next_card:4})]]);
let asked=null;analyzeRegisteredProtocol=async(id,options)=>{asked={id,...options};};loadProtocolCatalog=async()=>{};
globalThis.confirm=()=>true;
await loadReviewDraft("p-1");await tick();
await press(card(),"아니요");
assert(asked&&asked.id==="p-1"&&asked.reanalysis===true,JSON.stringify(asked));
assert(posts().length===0,`a decision was sent: ${JSON.stringify(posts())}`);
globalThis.confirm=()=>false;asked=null;
await press(card(),"아니요");
assert(asked===null,"re-analysed without the person's confirmation");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


class GenericCardTests(unittest.TestCase):

    def test_values_and_pictures_come_from_the_card_whatever_its_type(self) -> None:
        result = run(r"""
serve([["/review-decisions",answer(cards,2)],["/review-draft",draft({next_card:2})]]);
await loadReviewDraft("p-1");await tick();
const rows=walk(card()).filter(el=>el.className==="choice-card-values-row");
assert(rows.length===1&&rows[0].dataset.valueId==="ocr_value:3:5min",`rows: ${rows.length}`);
const text=visibleText(rows[0]);
for(const words of ["5 min","두 OCR 엔진이 이 값을 다르게 읽음","Google · 5 min  /  CLOVA · 6 min"])assert(text.includes(words),`${words}: ${text}`);
const images=walk(card()).filter(el=>el.tagName==="img");
assert(images.length===1&&images[0].src.endsWith("/source-pages/3/value-boxes/1/image"),images.map(image=>image.src).join("|"));
assert(JSON.stringify(labels(rows[0]))===JSON.stringify(["5 min","6 min","직접 입력"]),`value buttons: ${labels(rows[0])}`);
await press(rows[0],"6 min");
assert(JSON.stringify(posts()[0].body)===JSON.stringify({item_id:"ocr_value:3:5min",action:"fix",text:"6 min",target}),JSON.stringify(posts()[0].body));
await press(card(),"모두 맞아요");
assert(JSON.stringify(posts()[1].body)===JSON.stringify({item_id:"ocr_page:3",action:"confirm",target}),JSON.stringify(posts()[1].body));
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_two_readings_side_by_side_and_the_steps_a_card_is_about(self) -> None:
        result = run(r"""
serve([["/review-decisions",answer([decided(readings,"choose")],null)],["/review-draft",draft({cards:[readings],next_card:0})]]);
await loadReviewDraft("p-1");await tick();
const columns=walk(card()).filter(el=>el.className==="choice-card-reading").map(visibleText);
assert(columns.length===2&&columns[0].includes("이쪽(앞 조각)")&&columns[0].includes("4. 4. Spin at 500 x g · 원문 p.2 3줄")&&columns[1].includes("저쪽(뒤 조각)")&&columns[1].includes("Spin at 500 x g for 5 min."),columns.join(" || "));
await press(card(),"저쪽으로 읽기");
assert(JSON.stringify(posts()[0].body)===JSON.stringify({item_id:"step:p1.s4",action:"choose",reading:"b",target}),JSON.stringify(posts()[0].body));
renderReviewEditor(draft({next_card:4}));
const steps=walk(card()).filter(el=>el.tagName==="li").map(visibleText);
assert(steps.length===2&&steps[0].includes("12. 12. Wash twice. · 원문 p.14 3줄")&&steps[1].includes("13. Dry the pellet. · 원문 p.15 1줄"),steps.join(" || "));
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_the_card_function_stands_alone_for_another_caller(self) -> None:
        result = run(r"""
const host=document.createElement("div");const chosen=[];
const startCheck={card_id:"key:speed",kind_ko:"핵심 값",question_ko:"원심 속도가 얼마인가요?",shown:null,
 values:[{item_id:"v1",shown:"4000 rpm",choices:[choice("v1","골라요",{item_id:"v1",action:"confirm"})]}],
 choices:[choice("a","4000 rpm",{item_id:"key:speed",action:"choose",reading:"a"},{primary:true}),choice("b","400 rpm",{item_id:"key:speed",action:"choose",reading:"b"})]};
const count=renderChoiceCard(host,startCheck,{choose:(choiceMade,{value}={})=>chosen.push([choiceMade.choice_id,value?.item_id||null])});
assert(count===2,`top buttons: ${count}`);
assert(visibleText(host).includes("원심 속도가 얼마인가요?"),visibleText(host));
await press(host,"400 rpm");await press(host,"골라요");
assert(JSON.stringify(chosen)===JSON.stringify([["b",null],["v1","v1"]]),JSON.stringify(chosen));
assert(renderChoiceCard(host,null,{})===0&&host.children.length===0,"an empty card drew something");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


class AfterTheLastCardTests(unittest.TestCase):

    def test_the_name_is_remembered_and_the_version_registered(self) -> None:
        result = run(r"""
const stored=new Map([["voiney.reviewer.name","최수진"]]);
globalThis.localStorage={getItem:key=>stored.get(key)??null,setItem:(key,value)=>stored.set(key,String(value)),removeItem:key=>stored.delete(key)};
const done=draft({cards:cards.map(each=>decided(each)),next_card:null,registrable:true,counts:{items:9,needs_review:5,decided:5,undecided:0,changed:0}});
serve([["/review-versions",{protocol:{protocol_id:"p-1"},review_version:{version:1,version_ko:"검토본 v1",revision_id:"pdf-1-review-1",reviewer_name:"김재준"}}],["/review-draft",done],["/api/protocols/p-1/review",{protocol_id:"p-1"}],["/api/protocols",{protocols:[]}]]);
await loadReviewDraft("p-1");await tick();
assert(node("review-card-progress").textContent==="5 / 5 · 모두 정함",node("review-card-progress").textContent);
assert(visibleText(card()).includes("모든 카드를 정했습니다."),visibleText(card()));
assert(node("review-register-block").hidden===false,"no name and register after the last card");
assert(node("reviewer-name").value==="최수진",`remembered: ${node("reviewer-name").value}`);
node("reviewer-name").value="김재준";node("reviewer-name").dispatch("input");
assert(node("review-register").disabled===false,"register still off");
node("review-register").dispatch("click");await tick();await tick();await tick();
assert(stored.get("voiney.reviewer.name")==="김재준",`stored: ${stored.get("voiney.reviewer.name")}`);
assert(node("review-register-status").textContent.startsWith("검토본 v1을 등록했습니다"),node("review-register-status").textContent);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_a_passed_analysis_has_nothing_to_ask_and_can_be_registered_as_it_is(self) -> None:
        result = run(r"""
const passed=draft({target:{kind:"analysis",analysis_revision_number:2},cards:[],next_card:null,registrable:true,counts:{items:4,needs_review:0,decided:0,undecided:0,changed:0}});
serve([["/review-draft",passed]]);
assert(showReviewEditor("p-1",{protocol_id:"p-1",analysis_available:true,review_pending:null})===true,"not offered");
const editor=node("protocol-review-editor");
assert(editor.open!==true&&calls.length===0,"read before it was opened");
editor.open=true;editor.dispatch("toggle");await tick();await tick();
assert(visibleText(card()).includes("확인할 것이 없습니다."),visibleText(card()));
assert(node("review-editor-summary").textContent===" · 확인할 것 없음",node("review-editor-summary").textContent);
assert(node("review-register-block").hidden===false&&node("review-card-prev").hidden===true,"register or navigation");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


class KeysTests(unittest.TestCase):

    def test_arrow_keys_move_and_number_keys_press_the_cards_buttons(self) -> None:
        result = run(r"""
serve([["/review-decisions",answer([evidence,decided(ocrValue),page3,timer,seam],2)],["/review-draft",draft({next_card:1})]]);
await loadReviewDraft("p-1");await tick();
const key=async (name,target={tagName:"section"})=>{const handled=reviewCardKey({key:name,target});await tick();await tick();await tick();return handled;};
assert(await key("1"),"1 did nothing");
assert(posts()[0]?.body?.action==="confirm"&&node("review-card-progress").textContent==="3 / 5",JSON.stringify(posts()));
assert(await key("ArrowLeft")&&node("review-card-progress").textContent==="2 / 5",node("review-card-progress").textContent);
assert(await key("ArrowRight")&&node("review-card-progress").textContent==="3 / 5",node("review-card-progress").textContent);
assert(!(await key("1",{tagName:"input"})),"a key typed into an input pressed a button");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


class CardMarkupTests(unittest.TestCase):

    def setUp(self) -> None:
        self.html = INDEX.read_text(encoding="utf-8")
        begin = self.html.index('<details id="protocol-review-editor"')
        self.editor = self.html[begin:self.html.index('<div class="setup-grid">', begin)]

    def test_cards_instead_of_an_item_list_and_the_versions_folded(self) -> None:
        for ident in ("review-cards", "review-card-progress", "review-card", "review-card-prev",
                      "review-card-next", "review-register-block", "reviewer-name", "review-register",
                      "review-history-fold", "review-history"):
            self.assertIn(f'id="{ident}"', self.editor, ident)
        for gone in ("review-items", "review-source", 'id="review-item"', "<table", 'type="checkbox"'):
            self.assertNotIn(gone, self.editor, gone)
        fold = re.search(r'<details id="review-history-fold"[^>]*>', self.editor)
        self.assertIsNotNone(fold)
        self.assertNotIn(" open", fold.group(0))
        for word in ("승인", "반려"):
            self.assertNotIn(word, self.editor)

    def test_buttons_are_a_hands_width_and_stack_on_a_phone(self) -> None:
        self.assertIn("--target-run:64px", self.html)
        self.assertRegex(self.html, r"\.choice-card-choices button\{[^}]*min-height:var\(--target-run\)")
        self.assertRegex(self.html, r"\.review-cards-nav button\{[^}]*min-height:var\(--target-run\)")
        self.assertIn("@media (max-width:600px){.choice-card-choices button{flex-basis:100%}}", self.html)

    def test_the_card_never_branches_on_a_card_type_and_puts_text_in_as_text(self) -> None:
        script = self.html.split("function renderChoiceCard(", 1)[1].split("\n}\n", 1)[0]
        self.assertNotIn("card.type", script)
        self.assertNotIn("card?.type", script)
        block = self.html.split("// Lane RV2 stage ② (human decision 2026-10-16)", 1)[1].split("function renderReviewRegister", 1)[0]
        self.assertNotIn("innerHTML", block)
        self.assertNotIn("insertAdjacentHTML", block)


if __name__ == "__main__":
    unittest.main()
