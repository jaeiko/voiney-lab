"""Lane ON, decisions ② and ③ (2026-10-18): the start, one thing per screen.

Human decision (10/16, 10/18): before the 실험 전 view, like a phone app's
identity check -- login (when accounts are on) → the microphone and 목소리
맞추기 (skipped once measured) → the agent's voice (the provider's Korean
voices, [들어 보기], saved) → the protocol (list, upload) → two key values →
the safety check → [이 프로토콜로 시작]. Dots above, one big button, "← 이전",
one column. From the second time one button, "지난번 설정으로 바로 시작",
skips what is saved. The wizard is a sheet over the page (lane IA addendum). The page
draws only what the server sends: the questions, their choices and the
cards are the server's. Every test loads the whole page script into Node
with the fake DOM and drives the production functions.
"""

from __future__ import annotations

import unittest

from tests.test_screen_cleanup import INDEX, run_page_script

SETUP = r"""
const calls=[];
const tick=()=>new Promise(resolve=>setTimeout(resolve,0));
const settle=async(n=6)=>{for(let i=0;i<n;i++)await tick();};
const walk=(el,found=[])=>{found.push(el);for(const child of el.children||[])walk(child,found);return found;};
const buttonsIn=host=>walk(host).filter(el=>el.tagName==="button");
const labels=host=>buttonsIn(host).map(button=>button.textContent);
const press=async(host,words)=>{const button=buttonsIn(host).find(b=>b.textContent===words||b.textContent.startsWith(words));assert(button,`no "${words}" in ${labels(host)}`);button.dispatch("click");await settle();};
const posts=()=>calls.filter(call=>call.method==="POST"||call.method==="PUT");
const body=()=>node("wizard-body");
const title=()=>node("wizard-title").textContent;
const primary=()=>node("wizard-primary");
const wizardOn=()=>document.body.dataset.wizard==="on";
globalThis.document.body=node("body");
const DEV_SESSION={mode:"dev",signed_in:true,account:{account_id:"dev-local-admin",name:"실험자 (개발 로그인)",admin:false,entitlement:null,organization_id:"tenant-local-demo",disabled:false}};
const ACCOUNT_SESSION=(signed)=>({mode:"accounts",signed_in:signed,account:signed?{account_id:"a1",name:"김재준",admin:false,entitlement:"pilot",organization_id:"lab-pilot",disabled:false}:null});
const SETTINGS=(extra={})=>({settings:{confirm_mode:"readback",question_timing:"before_start",web_lookup:"on",proactive_mode:"all",ambient_mode:"quiet",speaker_sensitivity:"normal",agent_voice:"default",voice_calibration:"none",...extra}});
const VOICES={provider:"google_cloud",default_voice_id:"ko-KR-Chirp3-HD-Charon",selected:"default",preview_text_ko:"안녕하세요",voices:[{voice_id:"ko-KR-Chirp3-HD-Charon",label_ko:"차론 · 남성"},{voice_id:"ko-KR-Chirp3-HD-Kore",label_ko:"코레 · 여성"},{voice_id:"ko-KR-Chirp3-HD-Aoede",label_ko:"아오이데 · 여성"},{voice_id:"ko-KR-Chirp3-HD-Puck",label_ko:"퍽 · 남성"}]};
const ENTRY=(id,title,runnable=true,extra={})=>({protocol_id:id,title,revision_id:"pdf-1",source_filename:`${id}.pdf`,available_for_execution:runnable,analysis_status:runnable?"structured_analysis_ready":"review_pending",lifecycle_state:runnable?"ready":"review_pending",step_count:6,created_at:"2026-10-18T01:00:00+00:00",development_only:false,...extra});
const CATALOG={protocols:[ENTRY("p-1","Gamma"),ENTRY("p-2","Delta (검토 대기)",false,{review_pending:{needs_review_count:2}})]};
const REVIEW=(id="p-1")=>({protocol_id:id,title:"Gamma",revision_id:"pdf-1",lifecycle_state:"ready",readiness_status:"guidance_ready",available_for_execution:true,analysis_available:true,source:{filename:"p-1.pdf",sha256:"a".repeat(64),page_count:3},readiness:{status:"guidance_ready",label:"안내 준비 완료",reasons:[]},analysis_failure:null,constructs:[],execution_blockers:[],execution_notices:[{code:"x",message:"Parallel work.",message_ko:"동시 작업은 아직 안내 기능이 없습니다.",kind:"no_guidance_yet",source_page_number:2,source_excerpt:"Meanwhile",step_id:"s3"}],safety_notices:[{step_label:"2",step_id:"s2",source_page_number:1,source_text:"Wear gloves.",primary_text:"장갑을 끼세요."}],safety_scope:{usage_scope:"source_protocol_only",source_protocol_only:true,notice:"기관 안전 문서 없음 · 원문 안전 주의만"},sections:[],step_count:6});
const Q=(position,id,kind,kindKo,question,excerpt,choices)=>({question_id:id,position,total:2,progress_ko:`${position} / 2`,kind,kind_ko:kindKo,step_label:"2",item_id:"step:s2",question_ko:question,source:{page:1,excerpt,lines:{page:1,first_line:4,last_line:4}},choices:choices.map((label,index)=>({choice_id:`c${index+1}`,label_ko:label}))});
const CHECK={enabled:true,protocol_id:"p-1",target:{kind:"analysis"},questions:[Q(1,"q1:step:s2:10min","time","시간","2단계의 시간은(는) 원문에 얼마로 적혀 있나요?","2. Incubate for 10 min at 37 C.",["100 min","10 min","5 min"]),Q(2,"q2:step:s2:37°c","temperature","온도","2단계의 온도은(는) 원문에 얼마로 적혀 있나요?","2. Incubate for 10 min at 37 C.",["37 C","370 C","4 C"])],skipped_reason:null};
const CARD={card_id:"start-check:step:s2",item_id:"step:s2",position:1,total:1,progress_ko:"1 / 1",type:"start_check_value",kind:"step",kind_ko:"단계",step_label:"2",question_ko:"분석이 읽은 시간 10 min 이(가) 원문과 다르면 원문대로 고쳐 주세요.",reasons_ko:["시작 전 확인에서 다르게 고름"],shown:"2. Incubate for 10 min at 37 C.",source:{page:1,excerpt:"2. Incubate for 10 min at 37 C.",verified:true,lines:{page:1,first_line:4,last_line:4}},choices:[{choice_id:"fix",label_ko:"원문대로 고칠게요",request:{item_id:"step:s2",action:"fix"},needs:"text",route:null,primary:true},{choice_id:"keep",label_ko:"그대로 둘게요",request:null,needs:null,route:"back",primary:false}],proposal:null,change:null,readings:null,steps:null,memo_allowed:false,decided:false,decision:null,current:"2. Incubate for 10 min at 37 C."};
const WRONG={question_id:"q1:step:s2:10min",correct:false,proceed:false,chosen_label:"100 min",next_question_id:"q2:step:s2:37°c",message_ko:"원문 조각을 다시 봐 주세요.",followups:[{choice_id:"accept",label_ko:"원문대로 이 값이 맞아요",request:{question_id:"q1:step:s2:10min",action:"accept"}},{choice_id:"open_card",label_ko:"분석 값이 틀렸어요 → 검토 카드 열기",request:null}],card:CARD};
const RIGHT=(next)=>({question_id:"q",correct:true,proceed:true,chosen_label:"x",next_question_id:next,message_ko:"",followups:[],card:null});
function serve(answers){globalThis.fetch=async(url,options={})=>{const call={url:String(url),method:options.method||"GET",body:typeof options.body==="string"?JSON.parse(options.body):null};calls.push(call);for(const [pattern,answer] of answers){if(call.url.includes(pattern)){const value=typeof answer==="function"?answer(call):answer;return value&&typeof value.then==="function"?value:value&&value.ok!==undefined&&value.json?value:json(value);}}throw new Error(`unexpected ${call.url}`);};}
const base=(session=DEV_SESSION,settings=SETTINGS(),voices=VOICES,catalog=CATALOG)=>[["/api/auth/session",session],["/api/experimenter/settings",settings],["/api/voice/voices",voices],["/api/protocols?",catalog],["/review",REVIEW()],["/api/protocols",catalog],["/api/workspace/session",{display_name:"김재준"}],["/api/workspace/experiments",{experiments:[]}],["/api/source-formats",{formats:[]}]];
// Lane IA addendum (2026-10-19): the wizard is a sheet the page opens (the login shows by itself), so these tests open it as the home's [새 실험 시작] does.
async function open(answers){serve(answers);await loadProtocolCatalog();await openStartWizard();openWizardSheet();await settle();}
"""


def run(body: str):
    return run_page_script(SETUP + body)


class WizardStepsTests(unittest.TestCase):
    def test_in_dev_mode_the_wizard_opens_on_the_voice_step_with_six_dots(self):
        result = run(r"""
await open(base());
assert(wizardOn(),"the wizard is not on");
assert(!node("start-wizard").hidden,"the wizard section is hidden");
assert(wizard.step==="voice",`first step: ${wizard.step}`);
// Lane EASY decision 2 (2026-10-24): "오늘 원문과 다르게 할 값" after the safety step is one more dot.
assert(node("wizard-dots").children.length===7,`dots: ${node("wizard-dots").children.length}`);
assert(node("wizard-dots").children[0].dataset.state==="current","the first dot is not current");
assert(title().includes("마이크 확인"),`title: ${title()}`);
assert(node("wizard-kicker").textContent.startsWith("1 / 7"),`kicker: ${node("wizard-kicker").textContent}`);
assert(labels(body()).some(words=>words.startsWith("마이크 켜서 확인")),`no mic button: ${labels(body())}`);
assert(primary().textContent==="다음",`primary: ${primary().textContent}`);
assert(node("wizard-back").hidden,"← 이전 shows on the first step");
assert(!wizardSteps().includes("login"),"dev mode asks for a login");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_measured_voice_skips_its_step_and_one_voice_skips_the_agent_step(self):
        result = run(r"""
await open(base(DEV_SESSION,SETTINGS({voice_calibration:"measured"}),{...VOICES,voices:VOICES.voices.slice(0,1)}));
// Lane EASY decision 2: the step "changes" after the safety check.
assert(wizardSteps().join(",")==="protocol,check,safety,changes,start",`steps: ${wizardSteps()}`);
assert(wizard.step==="protocol",`step: ${wizard.step}`);
assert(node("wizard-dots").children.length===5,"dots");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_wizard_leaves_once_the_experiment_runs(self):
        result = run(r"""
await open(base());
assert(wizardOn());
sessionActive=true;curatedProtocolState={attached:true,active:true};renderScreenView();
assert(document.body.dataset.view==="during","view");
assert(!wizardOn(),"the wizard stays over the run view");
""")
        self.assertEqual(result.returncode, 0, result.stderr)


class LoginStepTests(unittest.TestCase):
    def test_accounts_mode_asks_for_a_login_first_and_a_refusal_is_said_in_words(self):
        result = run(r"""
let signed=false;
await open([["/api/auth/login",call=>json({detail:"login_refused"},false)],["/api/auth/session",()=>ACCOUNT_SESSION(signed)],...base().slice(1)]);
assert(wizard.step==="login",`step: ${wizard.step}`);
assert(title()==="로그인",`title: ${title()}`);
assert(primary().textContent==="로그인");
// Before the login the voices cannot be read (401), so the agent step is not yet counted.
// Lane EASY decision 2: one more step after the safety check.
assert(node("wizard-dots").children.length===7,`dots: ${node("wizard-dots").children.length}`);
node("wizard-name").value="김재준";node("wizard-password").value="wrong wrong";
primary().dispatch("click");await settle();
assert(node("wizard-note").textContent.includes("맞지 않아요"),`note: ${node("wizard-note").textContent}`);
assert(wizard.step==="login","moved on after a refusal");
const post=posts().find(call=>call.url.includes("/api/auth/login"));
assert(post&&post.body.name==="김재준"&&post.body.password==="wrong wrong",JSON.stringify(post));
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_login_moves_on_and_reloads_what_was_closed_before_it(self):
        result = run(r"""
let signed=false;
await open([["/api/auth/login",call=>{signed=true;return json({account:ACCOUNT_SESSION(true).account});}],["/api/auth/session",()=>ACCOUNT_SESSION(signed)],...base().slice(1)]);
calls.length=0;
node("wizard-name").value="김재준";node("wizard-password").value="correct horse battery";
primary().dispatch("click");await settle(10);
assert(wizard.step==="voice",`step after login: ${wizard.step}`);
assert(wizard.auth.signed_in===true,"auth not reloaded");
for(const path of ["/api/protocols","/api/workspace/session","/api/experimenter/settings","/api/voice/voices"])assert(calls.some(call=>call.url.includes(path)),`${path} not read again after login`);
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_an_invite_link_opens_the_signup_and_posts_the_invite(self):
        result = run(r"""
globalThis.location.search="?invite=tok-123";
let signed=false;
await open([["/api/auth/invites/tok-123",{valid:true,organization_id:"lab-pilot",admin:false,entitlement:"pilot",expires_at:"2026-10-21T00:00:00+00:00"}],["/api/auth/signup",call=>{signed=true;return json({account:ACCOUNT_SESSION(true).account});}],["/api/auth/session",()=>ACCOUNT_SESSION(signed)],...base().slice(1)]);
assert(wizard.step==="login");
assert(title()==="가입하기",`title: ${title()}`);
assert(node("wizard-lead").textContent.includes("lab-pilot"),node("wizard-lead").textContent);
assert(primary().textContent==="가입하고 시작");
node("wizard-name").value="김재준";node("wizard-password").value="correct horse battery";
primary().dispatch("click");await settle(10);
const post=posts().find(call=>call.url.includes("/api/auth/signup"));
assert(post&&post.body.invite==="tok-123"&&post.body.name==="김재준",JSON.stringify(post));
assert(wizard.step==="voice",`step: ${wizard.step}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_used_invite_disables_the_signup_and_says_why(self):
        result = run(r"""
globalThis.location.search="?invite=old";
await open([["/api/auth/invites/old",json({detail:"invite_invalid"},false)],["/api/auth/session",ACCOUNT_SESSION(false)],...base().slice(1)]);
assert(primary().disabled===true,"signup allowed with a dead invite");
assert(node("wizard-lead").textContent.includes("만료"),node("wizard-lead").textContent);
""")
        self.assertEqual(result.returncode, 0, result.stderr)


class VoiceStepsTests(unittest.TestCase):
    def test_the_mic_check_opens_the_microphone_once_and_the_toggle_is_the_sp1_one(self):
        result = run(r"""
let opened=0;
Object.defineProperty(globalThis,"navigator",{value:{mediaDevices:{getUserMedia:async()=>{opened+=1;return {getTracks:()=>[{label:"USB 마이크",stop(){this.stopped=true;}}]};}}},configurable:true});
await open(base());
await press(body(),"마이크 켜서 확인");
assert(opened===1,"the microphone was not opened once");
assert(labels(body()).some(words=>words.includes("USB 마이크")),`mic words: ${labels(body())}`);
const box=walk(body()).find(el=>el.id==="wizard-calibrate");
assert(box&&box.checked===true,"the calibrate-at-start box is not on by default");
box.checked=false;box.dispatch("change");
assert(node("voice-calibration-at-start").checked===false,"the SP1 setting did not follow");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_agent_step_lists_the_servers_voices_previews_one_and_saves_the_pick(self):
        result = run(r"""
let saved=null;
await open([["/api/voice/preview",call=>({ok:true,headers:{get:()=>"audio/wav"},json:async()=>({}),blob:async()=>({size:3200})})],["/api/experimenter/settings",call=>{if(call.method==="PUT"){saved=call.body;return json(SETTINGS({agent_voice:call.body.agent_voice}));}return json(SETTINGS());}],...base()]);
wizardGo("agent");
assert(title()==="안내 목소리 고르기",title());
const picks=walk(body()).filter(el=>el.className==="wizard-option-pick");
assert(picks.length===4,`voices: ${picks.length}`);
assert(picks[0].getAttribute("aria-pressed")==="true","the default voice is not shown as chosen");
const listens=buttonsIn(body()).filter(b=>b.textContent==="들어 보기");
assert(listens.length===4,"one 들어 보기 per voice");
listens[1].dispatch("click");await settle();
const preview=posts().find(call=>call.url.includes("/api/voice/preview"));
assert(preview&&preview.body.voice_id==="ko-KR-Chirp3-HD-Kore",JSON.stringify(preview));
picks[1].dispatch("click");await settle();
assert(saved&&saved.agent_voice==="ko-KR-Chirp3-HD-Kore",`saved: ${JSON.stringify(saved)}`);
const after=walk(body()).filter(el=>el.className==="wizard-option-pick");
assert(after[1].getAttribute("aria-pressed")==="true"&&after[0].getAttribute("aria-pressed")==="false","the pick is not shown");
assert(node("wizard-note").textContent.includes("저장"),node("wizard-note").textContent);
assert(primary().textContent==="이 목소리로 할게요");
primary().dispatch("click");await settle();
assert(wizard.step==="protocol",`step: ${wizard.step}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr)


class ProtocolStepTests(unittest.TestCase):
    def test_the_protocol_step_lists_runnable_first_and_picking_reads_the_review(self):
        result = run(r"""
await open(base());
wizardGo("protocol");
const picks=walk(body()).filter(el=>el.className==="wizard-option-pick");
assert(picks.length===2,`protocols: ${picks.length}`);
assert(picks[0].textContent.startsWith("Gamma")&&picks[1].disabled===true,"the review-pending protocol is pickable");
assert(primary().disabled===true,"next allowed before a pick");
let clicked=false;node("protocol-pdf").click=()=>{clicked=true;};
await press(body(),"원문 올리기");
assert(clicked,"the upload button did not open the file chooser");
calls.length=0;
picks[0].dispatch("click");await settle(8);
assert(calls.some(call=>call.url.includes("/api/protocols/p-1/review")),"the review was not read");
assert(node("protocol-id").value==="p-1","the select did not follow");
assert(primary().disabled===false,"next still off after a pick");
assert(primary().textContent==="이 프로토콜로 할게요");
""")
        self.assertEqual(result.returncode, 0, result.stderr)


class CheckStepTests(unittest.TestCase):
    ANSWERS = r"""
const answers=[];
await open([["/start-check/answers",call=>{answers.push(call.body);return json(answers.length===1?WRONG:RIGHT(null));}],["/start-check",CHECK],...base()]);
wizardGo("protocol");await settle();
walk(body()).filter(el=>el.className==="wizard-option-pick")[0].dispatch("click");await settle(8);
primary().dispatch("click");await settle(8);
"""

    def test_the_questions_are_the_servers_with_their_choices_and_a_wrong_pick_offers_two_ways(self):
        result = run(self.ANSWERS + r"""
assert(wizard.step==="check",`step: ${wizard.step}`);
assert(title()==="2단계의 시간은(는) 원문에 얼마로 적혀 있나요?",`title: ${title()}`);
assert(node("wizard-kicker").textContent.includes("1 / 2"),node("wizard-kicker").textContent);
assert(walk(body()).some(el=>el.className==="wizard-excerpt"&&el.textContent.includes("Incubate for 10 min")),"no excerpt");
assert(walk(body()).some(el=>el.textContent.includes("원문 p.1")),"no page");
const choices=buttonsIn(body()).filter(b=>b.className==="choice-card-choice").map(b=>b.textContent);
assert(choices.join("|")==="100 min|10 min|5 min",`choices: ${choices}`);
assert(primary().hidden===true,"a big next button beside the choices");
await press(body(),"100 min");
assert(answers[0].question_id==="q1:step:s2:10min"&&answers[0].choice_id==="c1",JSON.stringify(answers[0]));
assert(walk(body()).some(el=>el.textContent.includes("원문 조각을 다시 봐 주세요")),"the look-again words are missing");
const follow=buttonsIn(body()).filter(b=>b.className==="choice-card-choice").map(b=>b.textContent);
assert(follow.join("|")==="원문대로 이 값이 맞아요|분석 값이 틀렸어요 → 검토 카드 열기",`followups: ${follow}`);
await press(body(),"원문대로 이 값이 맞아요");
assert(answers[1].action==="accept"&&answers[1].question_id==="q1:step:s2:10min",JSON.stringify(answers[1]));
assert(title().includes("온도"),`second question: ${title()}`);
await press(body(),"37 C");
assert(wizard.step==="safety",`after the last question: ${wizard.step}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_card_fixes_the_item_through_the_review_and_asks_again(self):
        result = run(r"""
const answers=[],decisions=[],versions=[];let checks=0;
await open([["/start-check/answers",call=>{answers.push(call.body);return json(WRONG);}],["/start-check",()=>{checks+=1;return json(CHECK);}],["/review-decisions",call=>{decisions.push(call.body);return json({item:{},cards:[],next_card:null,registrable:true,target:{kind:"analysis"}});}],["/review-versions",call=>{versions.push(call.body);return json({review_version:{version:1,version_ko:"검토본 v1",reviewer_name:call.body.reviewer_name}});}],["/review-draft",{protocol_id:"p-1",target:{kind:"analysis",analysis_id:"x"},items:[],cards:[],next_card:null,counts:{},pages:[],registrable:true,registration_blockers:[]}],...base(ACCOUNT_SESSION(true))]);
wizardGo("protocol");await settle();
walk(body()).filter(el=>el.className==="wizard-option-pick")[0].dispatch("click");await settle(8);
primary().dispatch("click");await settle(8);
await press(body(),"100 min");
await press(body(),"분석 값이 틀렸어요");
const cardButtons=buttonsIn(body()).filter(b=>b.className==="choice-card-choice").map(b=>b.textContent);
assert(cardButtons.join("|")==="원문대로 고칠게요|그대로 둘게요",`card buttons: ${cardButtons}`);
assert(walk(body()).some(el=>el.className==="choice-card-value"&&el.textContent.includes("Incubate for 10 min")),"the item is not shown");
assert(!walk(body()).some(el=>el.id==="wizard-reviewer-name"),"a signed-in account is asked for a reviewer name");
await press(body(),"원문대로 고칠게요");
const input=walk(body()).find(el=>el.tagName==="input"&&el.className==="review-input");
assert(input,"no text input to write the fix");
input.value="2. Incubate for 15 min at 37 C.";
await press(body(),"보내기");await settle(12);
assert(decisions.length===1&&decisions[0].action==="fix"&&decisions[0].text==="2. Incubate for 15 min at 37 C."&&decisions[0].item_id==="step:s2",JSON.stringify(decisions));
assert(decisions[0].reviewer_name==="김재준","the account's name was not sent");
assert(versions.length===1&&versions[0].reviewer_name==="김재준",JSON.stringify(versions));
assert(checks>=2,"the questions were not asked again after the fix");
assert(wizard.card===null&&wizard.wrong===null,"the card stayed");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_without_numeric_values_the_check_is_skipped_and_said_on_the_safety_step(self):
        result = run(r"""
await open([["/start-check",{enabled:true,protocol_id:"p-1",target:{kind:"analysis"},questions:[],skipped_reason:"no_numeric_values"}],...base()]);
wizardGo("protocol");await settle();
walk(body()).filter(el=>el.className==="wizard-option-pick")[0].dispatch("click");await settle(8);
primary().dispatch("click");await settle(8);
assert(wizard.step==="safety",`step: ${wizard.step}`);
assert(walk(body()).some(el=>el.textContent.includes("확인할 숫자 값이 없어")),"the skip is not said");
""")
        self.assertEqual(result.returncode, 0, result.stderr)


class SafetyAndStartTests(unittest.TestCase):
    def test_the_safety_step_shows_the_servers_notices_and_the_start_presses_the_one_button(self):
        result = run(r"""
let started=0;startReviewedProtocol=async()=>{started+=1;};
await open([["/start-check/changes",call=>call.method==="POST"?{change_set_id:"run-changes-1",changes:[],source:"wizard"}:{values:[],target:{kind:"analysis"}}],["/start-check",{enabled:true,questions:[],skipped_reason:"no_numeric_values"}],...base()]);
wizardGo("protocol");await settle();
walk(body()).filter(el=>el.className==="wizard-option-pick")[0].dispatch("click");await settle(8);
primary().dispatch("click");await settle(8);
assert(wizard.step==="safety");
assert(title()==="안전 주의",title());
const text=walk(body()).map(el=>el.textContent).join(" ");
assert(text.includes("장갑을 끼세요.")&&text.includes("Wear gloves.")&&text.includes("2단계 · 원문 p.1"),`safety text: ${text}`);
assert(text.includes("기관 안전 문서 없음 · 원문 안전 주의만"),"the scope line is missing");
assert(text.includes("동시 작업은 아직 안내 기능이 없습니다."),"the notice is missing");
assert(primary().textContent==="읽었어요");
primary().dispatch("click");await settle();
// Lane EASY decision 2: "오늘 원문과 다르게 할 값" comes between, skipped here with nothing typed.
assert(wizard.step==="changes",`step: ${wizard.step}`);
primary().dispatch("click");await settle();
assert(wizard.step==="start",`step: ${wizard.step}`);
assert(title()==="Gamma",title());
assert(primary().textContent==="이 프로토콜로 시작");
assert(!node("wizard-back").hidden,"← 이전 is missing on the last step");
primary().dispatch("click");await settle();
assert(started===1,"the start was not the page's own start");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_back_goes_to_the_step_before(self):
        result = run(r"""
await open(base());
wizardGo("agent");
node("wizard-back").dispatch("click");
assert(wizard.step==="voice",`step: ${wizard.step}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_quick_start_skips_to_the_questions_with_the_last_protocol(self):
        result = run(r"""
const stored={"voiney-lab.last-protocol":"p-1"};globalThis.localStorage={getItem:key=>stored[key]??null,setItem:(key,value)=>{stored[key]=String(value);},removeItem:key=>{delete stored[key];}};
await open([["/start-check",CHECK],...base()]);
assert(wizard.step==="voice");
assert(node("wizard-quick").hidden===false,"the quick start is hidden although a protocol was remembered");
node("wizard-quick").dispatch("click");await settle(10);
assert(wizard.step==="check",`step: ${wizard.step}`);
assert(node("protocol-id").value==="p-1");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_no_remembered_protocol_means_no_quick_start(self):
        result = run(r"""
await open(base());
assert(node("wizard-quick").hidden===true,"a quick start with nothing remembered");
""")
        self.assertEqual(result.returncode, 0, result.stderr)


class MarkupTests(unittest.TestCase):
    def test_the_wizard_markup_and_its_tokens_are_in_the_page(self):
        html = INDEX.read_text(encoding="utf-8")
        # Lane IA decision ⑦ (2026-10-19): "자세히 보기" and its button are gone.
        for needle in ('id="start-wizard"', 'id="wizard-dots"', 'id="wizard-primary"', 'id="wizard-back"',
                       'id="wizard-quick"', "지난번 설정으로 바로 시작",
                       'body[data-wizard="on"]', "min-height:var(--target-run)", "max-width:480px"):
            self.assertIn(needle, html, needle)


if __name__ == "__main__":
    unittest.main()
