"""Lane IA stage ③ (2026-10-21): the experiment tab, the records tab, one address function,
and the screen items lane QA marked "파일럿 전에 꼭" (~/reports/lane_qa_report.md section 0).

④ the experiment tab: every start goes through the sheet (QA-01: the 실험 후 view's console
  [새 실험 시작] sent session.start with no questions and no safety notice, and the after
  view's experiment panel showed on every tab); [종료] ends on the server's word and shows
  the end screen (QA-10: the page closed its socket right after session.stop, so the
  server's answer never arrived), and an ended experiment is never offered as "진행 중"
  (QA-95); the questions the server asks before the start show while they are heard
  (QA-12); a turn's error the server survives does not end the session (QA-13).
⑤ the records tab: the list of past experiments, then one experiment's record.
⑨ every server address is read through one function (values unchanged).
Accounts: a logout lands on the login, not on a spent invite (QA-16); a 401 sends the page
to the login and the reconnecting stops (QA-17).
Shares tests/test_lane_ia_tabs.py's SETUP (the ON wizard harness with the tab router).
"""

from __future__ import annotations

import re
import unittest

from tests.test_lane_ia_tabs import run
from tests.test_screen_cleanup import INDEX

STAGE3 = r"""
const ENDED={...RESUMABLE,session_id:"s-8",status:"stopped",current_step_label:"5",started_at:"2026-10-18T01:00:00+00:00"};
let made=0;const RealWS=globalThis.WebSocket;globalThis.WebSocket=class extends RealWS{constructor(...args){super(...args);made+=1;this.url=args[0];}};
function running(id="s-9"){socket=new WebSocket("ws://test/ws");acceptedSessionConfiguration={configuration_id:5,mode:"cascade",language:"ko",protocol_id:"p-1",revision_id:"pdf-1",experiment_session_id:id};sessionActive=true;curatedProtocolState=runState();renderScreenView();return socket;}
const sentTypes=ws=>ws.sent.map(value=>JSON.parse(value).type);
const deliver=(ws,message)=>onMessage({data:JSON.stringify(message)},sessionGeneration,ws);
const unauthorized=()=>({ok:false,status:401,headers:{get:()=>"application/json"},json:async()=>({detail:"authentication_required"})});
"""


def run3(body: str):
    return run(STAGE3 + body)


class AddressTests(unittest.TestCase):
    """⑨ one function reads every server address; the values stay the same."""

    def test_every_request_and_the_socket_go_through_one_function(self) -> None:
        html = INDEX.read_text(encoding="utf-8")
        script = html.split("<script>", 1)[1]
        self.assertEqual(re.findall(r"""(?<![\w.])fetch\(\s*["`]/""", script), [], "a request names its address itself")
        self.assertNotIn("location.host", script)
        self.assertEqual(script.count("new WebSocket("), 1)
        result = run3(r"""
await boot(base());
calls.length=0;
serverOrigin=()=>"https://lab.example";
await loadProtocolCatalog();
assert(calls.length>0&&calls.every(call=>call.url.startsWith("https://lab.example/api/")),`requests: ${calls.map(call=>call.url)}`);
assert(serverSocketUrl()==="wss://lab.example/ws",serverSocketUrl());
serverOrigin=()=>"";
assert(serverUrl("/api/protocols")==="/api/protocols","the same-origin address changed");
assert(serverSocketUrl()==="ws://test/ws",serverSocketUrl());
""")
        self.assertEqual(result.returncode, 0, result.stderr)


class StartThroughTheSheetTests(unittest.TestCase):
    """QA-01: every start goes through the sheet; the after view keeps to its tab."""

    def test_the_after_views_start_opens_the_sheet_and_sends_nothing(self) -> None:
        result = run3(r"""
await boot(base());
go("#/experiment");pickProtocolFromList("p-1");await settle(8);
const ws=running();
await deliver(ws,{type:"experiment.ended",configuration_id:5,status:"completed",report:"saved"});await settle(4);
assert(document.body.dataset.view==="after",`view: ${document.body.dataset.view}`);
// As lane QA did it: [종료] on the end screen, then the console's [새 실험 시작].
await stopSession();await settle(4);
assert(document.body.dataset.view==="after"&&node("start").disabled!==true,`the end screen or its start is gone: ${document.body.dataset.view}`);
const before=made,sent=ws.sent.length;
node("start").dispatch("click");await settle(8);
assert(made===before&&ws.sent.length===sent,`the console start opened a session: sockets ${made-before}, sent ${ws.sent.slice(sent)}`);
assert(wizard.open===true&&document.body.dataset.wizard==="on",`the sheet did not open: ${wizard.open} ${document.body.dataset.wizard}`);
closeWizardSheet();
assert(document.body.dataset.view==="after","closing the sheet left the end screen");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_after_views_panel_keeps_to_the_experiment_tab(self) -> None:
        html = INDEX.read_text(encoding="utf-8")
        self.assertNotIn('body[data-view="after"] #tab-experiment{display', html)
        self.assertIn('body[data-view="after"] #tab-experiment:not([hidden]){display:grid;', html)


class StopTests(unittest.TestCase):
    """QA-10 and QA-95: [종료] ends on the server's word; an ended experiment is not 진행 중."""

    def test_the_stop_waits_for_the_servers_answer_and_shows_the_end(self) -> None:
        result = run3(r"""
let list=[{...RESUMABLE,status:"in_progress"}];
await boot([["/api/workspace/experiments/s-9/timeline",()=>TIMELINE(list[0].status)],["/api/workspace/experiments",()=>({experiments:list})],...base()]);
go("#/experiment");pickProtocolFromList("p-1");chooseExperimentSession("s-9",{force:true});await settle(8);
const ws=running();
assert(document.body.dataset.view==="during",document.body.dataset.view);
const stopping=stopSession();await settle(2);
assert(sentTypes(ws).includes("session.stop"),`sent: ${sentTypes(ws)}`);
assert(ws.readyState===1,"the socket was closed before the server answered the stop");
list=[{...RESUMABLE,status:"stopped"}];
await deliver(ws,{type:"experiment.session.state",state:{session_id:"s-9",version:5,status:"stopped"}});
await deliver(ws,{type:"session.stopped",state:"IDLE"});
await stopping;await settle(10);
assert(document.body.dataset.view==="after",`view after the stop: ${document.body.dataset.view}`);
assert(node("procedure-status").textContent.startsWith("실험이 종료되었습니다"),node("procedure-status").textContent);
assert(ws.readyState===3&&sessionActive===false,"the socket stayed open after the answer");
assert(!experimentIsResumable(),"the stopped experiment is still offered to continue");
go("#/home");
assert(!cards().some(card=>card.dataset.card==="resume"),`home: ${cardWords()}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_no_answer_lets_go_after_a_while_and_rereads_the_list(self) -> None:
        result = run3(r"""
await boot(withExperiments([{...RESUMABLE,status:"in_progress"}]));
go("#/experiment");pickProtocolFromList("p-1");await settle(8);
STOP_ANSWER_MS=40;
const ws=running();calls.length=0;
await stopSession();await settle(6);
assert(ws.readyState===3&&sessionActive===false,"no answer kept the socket");
assert(document.body.dataset.view==="before",`view: ${document.body.dataset.view}`);
assert(calls.some(call=>call.url.endsWith("/api/workspace/experiments")),"the list was not read again");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_voice_end_rereads_the_list_so_home_does_not_offer_it(self) -> None:
        result = run3(r"""
let list=[{...RESUMABLE,status:"in_progress"}];
await boot([["/api/workspace/experiments/s-9/timeline",()=>TIMELINE(list[0].status)],["/api/workspace/experiments",()=>({experiments:list})],...base()]);
go("#/experiment");pickProtocolFromList("p-1");chooseExperimentSession("s-9",{force:true});await settle(8);
const ws=running();
list=[{...RESUMABLE,status:"completed"}];
await deliver(ws,{type:"experiment.ended",configuration_id:5,status:"completed",report:"saved"});await settle(10);
go("#/home");
assert(!cards().some(card=>card.dataset.card==="resume"),`home still offers the ended experiment: ${cardWords()}`);
assert(!experimentIsResumable(),"the ended experiment is resumable");
""")
        self.assertEqual(result.returncode, 0, result.stderr)


class PrestartQuestionTests(unittest.TestCase):
    """QA-12: while the server asks its questions before the start, the screen shows the question."""

    def test_the_question_before_the_start_is_on_the_run_view(self) -> None:
        result = run3(r"""
await boot(base());
go("#/experiment");pickProtocolFromList("p-1");await settle(8);
running();
renderCuratedProtocolState({configuration_id:5,state:{...runState(),active:false,workflow_status:"preview",revision:7},screen:{open_question:{kind:"prestart",text:"7단계 조건에 해당하나요? 네 · 아니요 · 나중에"}}});
renderScreenView();
assert(document.body.dataset.view==="during",`view while the question is asked: ${document.body.dataset.view}`);
const box=node("procedure-open-question");
assert(box.hidden===false&&box.textContent.includes("7단계 조건에 해당하나요?"),`question: ${box.hidden} ${box.textContent}`);
// The server's state before the start is "preview" (curated_protocol.workflow_status). No question: still before the start.
renderCuratedProtocolState({configuration_id:5,state:{...runState(),active:false,workflow_status:"preview",revision:8},screen:{}});renderScreenView();
assert(document.body.dataset.view==="before",`view with no question: ${document.body.dataset.view}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr)


class TurnErrorTests(unittest.TestCase):
    """QA-13: a turn's error the server survives leaves the session on."""

    def test_a_turn_error_keeps_the_session_and_another_error_still_stops(self) -> None:
        result = run3(r"""
await boot(base());
go("#/experiment");pickProtocolFromList("p-1");await settle(8);
let closed=0;closeAudio=async()=>{closed+=1;};
const ws=running();
await deliver(ws,{type:"error",turn_id:3,generation:2,message:"voice turn processing failed"});await settle(2);
assert(sessionActive===true&&closed===0&&visibleState!=="ERROR",`a turn error ended the session: active ${sessionActive} closed ${closed} state ${visibleState}`);
assert(String(turnNode(3).querySelector(".error")?.textContent||"").length>0,"the turn does not say it failed");
await deliver(ws,{type:"error",message:"session is not active"});await settle(2);
assert(sessionActive===false&&visibleState==="ERROR","an error with no turn did not stop");
""")
        self.assertEqual(result.returncode, 0, result.stderr)


class AccountTests(unittest.TestCase):
    """QA-16 and QA-17: the way out of an account leads to the login."""

    LOCATION = r"""
let replaced=null,reloaded=0;
Object.assign(globalThis.location,{pathname:"/",search:"?invite=spent-token",replace(url){replaced=url;},reload(){reloaded+=1;}});
"""

    def test_a_logout_lands_on_the_login_not_on_the_spent_invite(self) -> None:
        result = run3(self.LOCATION + r"""
await boot([["/api/auth/logout",{signed_out:true}],...base(ACCOUNT_SESSION(true))]);
go("#/settings");
await logOutAccount();await settle(4);
assert(replaced==="/"&&reloaded===0,`left for: ${replaced} reloads ${reloaded}`);
assert(document.body.dataset.auth==="required","the screen did not go to the login");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_logout_answered_401_is_already_signed_out(self) -> None:
        result = run3(self.LOCATION + r"""
await boot([["/api/auth/logout",unauthorized],...base(ACCOUNT_SESSION(true))]);
go("#/settings");
await logOutAccount();await settle(4);
assert(replaced==="/",`a 401 logout stayed: ${replaced} · ${node("account-password-status").textContent}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_401_sends_the_page_to_the_login_and_stops_the_session(self) -> None:
        result = run3(self.LOCATION + r"""
let refuse=false;
await boot([["/api/workspace/experiments",()=>refuse?unauthorized():{experiments:[]}],...base(ACCOUNT_SESSION(true))]);
go("#/experiment");pickProtocolFromList("p-1");await settle(8);
const ws=running(),generation=sessionGeneration;
refuse=true;
await loadExperimentSessions();await settle(4);
assert(document.body.dataset.auth==="required",`auth: ${document.body.dataset.auth}`);
assert(sessionActive===false&&sessionGeneration!==generation&&ws.readyState===3,"the session kept going after a 401 (the reconnecting would never stop)");
assert(replaced==="/","the page did not leave for the login");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_refused_login_is_not_a_signed_out_session(self) -> None:
        result = run3(self.LOCATION + r"""
await boot([["/api/auth/login",unauthorized],...base(ACCOUNT_SESSION(false))]);
assert(document.body.dataset.auth==="required");
calls.length=0;
await serverFetch("/api/auth/login",{method:"POST"});await settle(2);
assert(replaced===null,"a refused login reloaded the page");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

if __name__ == "__main__":
    unittest.main()
