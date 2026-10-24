"""Lane PRE, decision 1 (human decision 2026-10-24): the protocol list refreshes itself.

Lane PERSONA (F1, dev 0d45705): the page fetched ``/api/protocols`` when it
loaded and again only for the uploader's own analysis. A labmate (or the
uploader in another tab) who had the page open while an analysis ran kept
seeing "분석 중", and the start sheet refused the protocol ("분석을 통과한
프로토콜을 골라 주세요") until a full reload. Decision: while the page is
shown it asks the server again -- every 5 s while an analysis in the list
runs, every 30 s otherwise, at once when a tab opens or the page comes back
into view; it stops while hidden. Before the start sheet refuses a protocol
it asks the server once more. Another member's upload in the same lab shows
the same way (the catalog is the lab's).

Every test loads the whole page script into Node with the fake DOM and
drives the production functions (tests/test_screen_cleanup.run_page_script).
The page's long timers are captured, not waited for: each test reads the
delay the page asked for and runs the callback itself.
"""

from __future__ import annotations

import unittest

from tests.test_lane_ia_tabs import SETUP as TABS_SETUP
from tests.test_screen_cleanup import run_page_script

SETUP = TABS_SETUP + r"""
const realSetTimeout=setTimeout,realClearTimeout=clearTimeout,longTimers=[];
globalThis.setTimeout=(fn,ms,...rest)=>{if(ms>=1000){const timer={fn,ms,cleared:false,fired:false,unref(){return timer;}};longTimers.push(timer);return timer;}return realSetTimeout(fn,ms,...rest);};
globalThis.clearTimeout=timer=>{if(longTimers.includes(timer)){timer.cleared=true;return;}return realClearTimeout(timer);};
globalThis.document.visibilityState="visible";
const RUN={state:"analyzing",reanalysis:false,failure_code:null,requested_at:"2026-10-24T01:00:00+00:00"};
const THETA_RUNNING=ENTRY("p-new","Theta",false,{lifecycle_state:"analyzing",analysis_status:"structured_analysis_ready",analysis_run:RUN});
const THETA_READY=ENTRY("p-new","Theta",true,{readiness_status:"analysis_required",analysis_run:{...RUN,state:"passed"}});
let catalog={protocols:[ENTRY("p-1","Gamma"),THETA_RUNNING]},catalogAnswer=null;
const reviews={"p-1":REVIEW("p-1"),"p-new":{...REVIEW("p-new"),title:"Theta",available_for_execution:false,lifecycle_state:"analyzing"}};
const answers=(session=DEV_SESSION)=>[["/api/auth/session",session],["/api/experimenter/settings",SETTINGS()],["/api/voice/voices",VOICES],
 ["/review",call=>reviews[call.url.includes("p-new")?"p-new":"p-1"]],["/reagent-tables",{tables:[]}],["/start-check",CHECK],
 ["/api/protocols",()=>catalogAnswer?catalogAnswer():catalog],["/api/workspace/session",{display_name:"김재준"}],["/api/workspace/experiments",{experiments:[]}],["/api/source-formats",{formats:[]}]];
const catalogGets=()=>calls.filter(call=>call.method==="GET"&&/\/api\/protocols$/.test(call.url)).length;
const reviewGets=id=>calls.filter(call=>call.method==="GET"&&call.url.includes(`/api/protocols/${id}/review`)).length;
const fireRefresh=async()=>{const timer=catalogRefreshHandle;assert(timer&&!timer.cleared,"no refresh is waiting");timer.fired=true;timer.fn();await settle(12);return timer;};
const badgeWords=id=>protocolListBadges(protocolCatalog.get(id)).map(badge=>badge.text);
const wizardPicks=()=>walk(body()).filter(el=>el.className==="wizard-option-pick");
const thetaPick=()=>wizardPicks().find(el=>el.dataset.protocolId==="p-new");
async function bootPage(session=DEV_SESSION){serve(answers(session));globalThis.location.hash="#/home";renderRoute();await loadProtocolCatalog();await openStartWizard();renderRoute();await settle();}
"""


def run(body: str):
    return run_page_script(SETUP + body)


class CatalogRefreshTimingTests(unittest.TestCase):
    def test_a_running_analysis_is_asked_after_every_5_s_and_a_quiet_list_every_30_s(self):
        result = run(r"""
assert(CATALOG_REFRESH_RUNNING_MS===5000&&CATALOG_REFRESH_IDLE_MS===30000,"the decided intervals changed");
await bootPage();
const before=catalogGets();
assert(catalogRefreshHandle&&catalogRefreshHandle.ms===5000,`an analysis runs, the next ask is not in 5 s: ${catalogRefreshHandle?.ms}`);
assert(badgeWords("p-new").includes("분석 중"),"the running analysis is not shown");
// The analysis ends on the server; the page has not asked yet.
catalog={protocols:[ENTRY("p-1","Gamma"),THETA_READY]};
assert(protocolCatalog.get("p-new").available_for_execution===false,"the page knew before it asked");
await fireRefresh();
assert(catalogGets()===before+1,`one refresh asked ${catalogGets()-before} times`);
assert(protocolCatalog.get("p-new").available_for_execution===true,"the list did not take the server's answer");
assert(badgeWords("p-new").includes("바로 쓸 수 있음")&&!badgeWords("p-new").includes("분석 중"),`badges: ${badgeWords("p-new")}`);
assert(node("protocol-library-count").textContent.includes("바로 쓸 수 있는 것 2개"),node("protocol-library-count").textContent);
assert(catalogRefreshHandle.ms===30000,`nothing runs, the next ask is not in 30 s: ${catalogRefreshHandle.ms}`);
// Each refresh puts the next one in place, never two at once.
assert(longTimers.filter(timer=>!timer.cleared&&!timer.fired&&[5000,30000].includes(timer.ms)).length===1,"two refreshes are waiting");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_labmates_upload_shows_up_and_is_announced_as_running_within_30_s(self):
        result = run(r"""
catalog={protocols:[ENTRY("p-1","Gamma")]};
await bootPage();
assert(catalogRefreshHandle.ms===30000,"an idle list is asked more often than every 30 s");
// Another member of the lab uploads: the lab's catalog has a new running item.
catalog={protocols:[ENTRY("p-1","Gamma"),THETA_RUNNING]};
await fireRefresh();
assert(protocolCatalog.has("p-new")&&badgeWords("p-new").includes("분석 중"),"the labmate's upload is not on the list");
assert(catalogRefreshHandle.ms===5000,"a running analysis is not followed every 5 s");
catalog={protocols:[ENTRY("p-1","Gamma"),THETA_READY]};
await fireRefresh();
assert(protocolCatalog.get("p-new").available_for_execution===true,"the passed analysis is not on the list");
""")
        self.assertEqual(result.returncode, 0, result.stderr)


class CatalogRefreshVisibilityTests(unittest.TestCase):
    def test_a_hidden_page_stops_asking_and_asks_at_once_when_it_is_shown_again(self):
        result = run(r"""
await bootPage();
const waiting=catalogRefreshHandle,before=catalogGets();
document.visibilityState="hidden";
await onCatalogVisibilityChange();await settle();
assert(waiting.cleared&&catalogRefreshHandle===null,"the refresh still waits while the page is hidden");
// A timer that slipped through, or a load of the page's own, asks nothing more and schedules nothing.
waiting.fn();await settle();
assert(catalogGets()===before,"a hidden page asked the server");
await loadProtocolCatalog();
assert(catalogGets()===before+1&&catalogRefreshHandle===null,"a hidden page scheduled a refresh");
catalog={protocols:[ENTRY("p-1","Gamma"),THETA_READY]};
document.visibilityState="visible";
await onCatalogVisibilityChange();await settle(12);
assert(catalogGets()===before+2,"coming back did not ask at once");
assert(protocolCatalog.get("p-new").available_for_execution===true,"coming back did not show the passed analysis");
assert(catalogRefreshHandle&&catalogRefreshHandle.ms===30000,"no refresh waits after coming back");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_opening_a_tab_asks_at_once_and_staying_on_it_does_not(self):
        result = run(r"""
await bootPage();
let before=catalogGets();
go("#/protocols");await settle(12);
assert(catalogGets()===before+1,`opening the protocols tab asked ${catalogGets()-before} times`);
before=catalogGets();
go("#/protocols/p-1");await settle(12);renderRoute();await settle(12);
assert(catalogGets()===before,"staying on the tab asked again");
go("#/experiment");await settle(12);
assert(catalogGets()===before+1,"opening the experiment tab did not ask");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_before_a_login_nothing_is_asked(self):
        result = run(r"""
await bootPage(ACCOUNT_SESSION(false));
assert(authRequired(),"the login is not required");
const before=catalogGets();
if(catalogRefreshHandle){catalogRefreshHandle.fn();await settle();}
assert(await refreshProtocolCatalog()===false,"a refresh ran before the login");
await onCatalogVisibilityChange();await settle();
assert(catalogGets()===before&&catalogRefreshHandle===null,"the list was asked for before the login");
""")
        self.assertEqual(result.returncode, 0, result.stderr)


class QuietRefreshTests(unittest.TestCase):
    def test_a_refresh_keeps_the_status_lines_and_does_not_redraw_an_unchanged_list(self):
        result = run(r"""
await bootPage();
node("protocol-upload-status").textContent="등록 완료 · 실행 가능한 버전입니다.";
const readiness=node("protocol-readiness").textContent,row=node("protocol-list").children[0];
await fireRefresh();
assert(node("protocol-upload-status").textContent==="등록 완료 · 실행 가능한 버전입니다.",`the status line was overwritten: ${node("protocol-upload-status").textContent}`);
assert(node("protocol-readiness").textContent===readiness,"the readiness line flashed");
assert(node("protocol-list").children[0]===row,"an unchanged list was drawn again");
assert(catalogState==="ready","the list went back to loading");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_refresh_that_fails_keeps_the_list_and_tries_again(self):
        result = run(r"""
await bootPage();
catalogAnswer=()=>json({detail:"busy"},false);
await fireRefresh();
assert(protocolCatalog.size===2&&catalogState==="ready","a failed refresh emptied the list");
assert(!node("protocol-upload-status").textContent.includes("불러오지 못했"),"a failed refresh said the list failed");
assert(catalogRefreshHandle&&!catalogRefreshHandle.cleared,"a failed refresh stopped the refreshes");
catalogAnswer=null;catalog={protocols:[ENTRY("p-1","Gamma"),THETA_READY]};
await fireRefresh();
assert(protocolCatalog.get("p-new").available_for_execution===true,"the next refresh did not recover");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_an_answer_older_than_the_one_drawn_is_dropped(self):
        result = run(r"""
await bootPage();
let release=null;
catalogAnswer=()=>new Promise(resolve=>{release=()=>resolve(json({protocols:[ENTRY("p-1","Gamma"),THETA_RUNNING]}));});
const slow=refreshProtocolCatalog();await settle();
assert(typeof release==="function","the refresh did not ask");
catalogAnswer=null;catalog={protocols:[ENTRY("p-1","Gamma"),THETA_READY]};
await loadProtocolCatalog("p-new");
assert(protocolCatalog.get("p-new").available_for_execution===true,"the newer load was not drawn");
release();await slow;await settle();
assert(protocolCatalog.get("p-new").available_for_execution===true,"the older answer was drawn over the newer one");
""")
        self.assertEqual(result.returncode, 0, result.stderr)


class StartAsksAgainTests(unittest.TestCase):
    def test_the_sheet_asks_the_server_when_it_opens_and_offers_the_passed_protocol(self):
        result = run(r"""
await bootPage();
// The list on the page is stale: the analysis passed on the server meanwhile.
catalog={protocols:[ENTRY("p-1","Gamma"),THETA_READY]};reviews["p-new"]={...REVIEW("p-new"),title:"Theta"};
const before=catalogGets();
openWizardSheet("protocol");await settle(12);
assert(catalogGets()===before+1,"opening the sheet did not ask the server");
assert(thetaPick()&&thetaPick().disabled===false,"the passed protocol is still not offered");
thetaPick().dispatch("click");await settle(12);
assert(node("protocol-id").value==="p-new","the pick did not take");
primary().dispatch("click");await settle(12);
assert(wizard.step==="check",`the sheet did not go on: ${wizard.step} · ${wizard.note}`);
assert(wizard.note==="","the sheet refused the passed protocol");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_protocol_step_asks_once_before_refusing(self):
        result = run(r"""
await bootPage();
node("protocol-id").value="p-new";
wizard.open=true;wizard.step="protocol";renderWizard();
// Still running on the server: one more question, then the refusal.
let before=catalogGets();
await wizardPrimary();await settle();
assert(catalogGets()===before+1,`the refusal asked the server ${catalogGets()-before} times`);
assert(wizard.note==="분석을 통과한 프로토콜을 골라 주세요."&&wizard.step==="protocol","a running protocol was let through");
// Passed on the server: the same press goes on.
catalog={protocols:[ENTRY("p-1","Gamma"),THETA_READY]};reviews["p-new"]={...REVIEW("p-new"),title:"Theta"};
before=catalogGets();
await wizardPrimary();await settle(12);
assert(catalogGets()===before+1,"the press did not ask the server");
assert(wizard.step==="check"&&wizard.note==="",`the passed protocol was refused: ${wizard.note}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_start_asks_once_before_saying_the_protocol_cannot_run(self):
        result = run(r"""
await bootPage();
let started=0;startSession=async()=>{started++;};
pickProtocolFromList("p-new");await settle(12);
assert(currentProtocolReviewId==="p-new"&&lastProtocolReview?.available_for_execution===false,"the stale review is not on screen");
let before=catalogGets();
assert(await startReviewedProtocol()===START_NOT_SENT_WORDS.not_runnable,"a running protocol started");
assert(catalogGets()===before+1&&started===0,"the refusal did not ask the server once");
catalog={protocols:[ENTRY("p-1","Gamma"),THETA_READY]};reviews["p-new"]={...REVIEW("p-new"),title:"Theta"};
before=catalogGets();const reviewsBefore=reviewGets("p-new");
const refused=await startReviewedProtocol();
assert(refused===undefined,`the passed protocol was refused: ${refused}`);
assert(started===1&&catalogGets()===before+1&&reviewGets("p-new")===reviewsBefore+1,"the start did not ask the server and read the review again");
""")
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
