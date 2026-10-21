"""Lane IA, decisions ① and ⑦ (2026-10-19): five tabs by hash route.

Human finding (10/19, on the deployed screen): the protocol list, the upload
and the settings, the step and the conversation, the run's records -- all on
one page with nothing between them. Decisions: ① one screen per hash route
(#/home, #/protocols, #/protocols/<id>, #/upload, #/experiment, #/records,
#/records/<id>, #/settings), a tab bar below on a phone and a menu on the
left of a wide screen, the back button working; while the experiment runs
#/experiment is the default, the bar hides, and another tab carries a band
"실험 중 · n단계 · 돌아가기"; before a login only the login. ⑦ "자세히 보기" is gone: every part has its own place.
(② the home and ⑥ the settings tab have their own test files, which share
this module's SETUP.)

The page draws only what the server sends; a tab is where things are drawn.
Every test here loads the whole page script into Node with the fake DOM and
drives the production functions (tests/test_screen_cleanup.run_page_script).
"""

from __future__ import annotations

import re
import unittest

from tests.test_lane_on_wizard_screen import SETUP as WIZARD_SETUP
from tests.test_screen_cleanup import INDEX, run_page_script

SETUP = WIZARD_SETUP + r"""
globalThis.location.hash="";
globalThis.history={replaced:[],pushed:[],backs:0,replaceState(_,__,path){globalThis.history.replaced.push(path);globalThis.location.hash=path;},pushState(state,_,path){globalThis.history.pushed.push(state);},back(){globalThis.history.backs+=1;globalThis.history.pushed.pop();},popBack(){globalThis.history.backs+=1;globalThis.history.pushed.pop();for(const handler of pageListeners.popstate||[])handler({});}};

const go=hash=>{globalThis.location.hash=hash;return renderRoute();};
const tab=()=>document.body.dataset.tab;
const panelShown=name=>node(`tab-${name}`).hidden===false;
const shownPanels=()=>["home","protocols","upload","experiment","records","settings"].filter(panelShown);
const hasClass=(el,name)=>String(el.className||"").split(" ").includes(name);
const cards=()=>walk(node("home-cards")).filter(el=>hasClass(el,"home-card"));
const cardWords=()=>cards().map(card=>card.textContent);
const cardHref=card=>card.getAttribute("href");
const RESUMABLE={session_id:"s-9",protocol_id:"p-1",protocol_title:"Gamma",status:"paused",current_step_label:"3",started_at:"2026-10-19T01:00:00+00:00",day_sequence:1,day_sequence_date:"2026-10-19"};
const TIMELINE=(status="paused")=>({session:{session_id:"s-9",version:4,status,protocol_id:"p-1",protocol_revision_id:"pdf-1",current_step_id:"s3",current_step_label:"3"},timeline:[],observation_count:0,evidence_count:0,recovery:{eligible:status==="paused",restored:{protocol_id:"p-1",protocol_revision_id:"pdf-1",current_step_id:"s3",current_step_label:"3",completed_step_count:2},not_restored:[],next_action:"resume_voice_session"}});
const withExperiments=(items,answers=base())=>[["/api/workspace/experiments/s-9/timeline",TIMELINE()],["/api/workspace/experiments",{experiments:items}],...answers];
async function boot(answers){serve(answers);await loadProtocolCatalog();await loadWorkspaceSession();await openStartWizard();renderRoute();await settle();}
const runState=()=>({attached:true,protocol_id:"p-1",revision_id:"pdf-1",display_name:"Gamma",development_only:false,readiness_status:"guidance_ready",active:true,current_step_label:"3",current_step_id:"s3",total_steps:6,at_final_step:false,block_reason:null,revision:1,source_page_refs:[],visual_assets:[],visual_status:"unavailable",workflow_status:"running"});
function startRun(){acceptedSessionConfiguration={configuration_id:5,mode:"cascade",language:"ko",protocol_id:"p-1",revision_id:"pdf-1"};sessionActive=true;curatedProtocolState=runState();renderScreenView();}
"""


def run(body: str):
    return run_page_script(SETUP + body)


def panel(html: str, name: str) -> str:
    start = html.index(f'<section id="tab-{name}"')
    end = html.index('<section id="tab-', start + 1) if name != "settings" else html.index("</main>", start)
    return html[start:end]


#: Decision ① / ⑦: every part of the old page and its tab (the report's table).
PLACES = {
    "home": ("page-title",),
    "protocols": ("protocol-library", "protocol-list", "protocol-review-panel", "protocol-reagent-tables",
                  "protocol-queue", "protocol-start-summary", "protocol-start",
                  "protocol-compare", "protocol-review-editor", "review-card", "review-register", "review-history-fold"),
    "experiment": ("experiment-start-open", "state", "status", "start", "stop", "run-caption-text", "mic-recovery",
                   "voice-calibration", "experiment-context-name", "experiment-resume-disclosure", "mic-check",
                   "procedure-primary", "run-card", "run-timer", "run-next-step", "procedure-warning",
                   "figures-sheet", "procedure-visual", "task-web-reference-panel", "after-state",
                   "report-review-checklist", "export-report-docx", "pilot-feedback", "procedure-report-details",
                   "conversation-sheet", "log", "ignored-speech"),
    "upload": ("protocol-pdf", "protocol-pdf-pick", "protocol-upload-status", "protocol-heic-hint", "protocol-duplicate-notice"),
    "records": ("experiment-session-ledger", "experiment-session-select", "experiment-checkpoint-restart",
                "manual-observation-content", "experiment-evidence-file", "experiment-event-timeline"),
    "settings": ("settings-sheet", "protocol-id", "question-timing", "mic-device", "confirm-mode", "web-lookup",
                 "install-guide", "dev-diagnostics", "protocol-readiness"),
}


class TabMarkupTests(unittest.TestCase):
    """Decision ①: the markup holds one panel per tab, and a bar of five links."""

    def setUp(self) -> None:
        self.html = INDEX.read_text(encoding="utf-8")
        self.style = self.html.split("<style>", 1)[1].split("</style>", 1)[0]

    def test_five_panels_in_order_inside_the_workspace(self) -> None:
        main = self.html[self.html.index('<main id="researcher-workspace"'):self.html.index("</main>")]
        found = re.findall(r'<section id="tab-([a-z]+)" class="tab-panel"[^>]*data-tab="([a-z]+)"', main)
        # Lane IA decision ③: the upload is a panel of its own under the protocols link.
        self.assertEqual(found, [(name, name) for name in ("home", "protocols", "upload", "experiment", "records", "settings")])
        self.assertEqual(self.html.count('id="start-wizard"'), 1)
        # Only the home panel is open before the script runs; a page without its script shows the home.
        for name in ("protocols", "upload", "experiment", "records", "settings"):
            self.assertRegex(main, rf'<section id="tab-{name}" class="tab-panel"[^>]*\bhidden>')
        self.assertNotRegex(main, r'<section id="tab-home" class="tab-panel"[^>]*\bhidden>')
        self.assertIn('<body data-view="before">', self.html)

    def test_every_part_keeps_its_id_and_sits_in_its_tab(self) -> None:
        self.assertIn('class="example-strip"', panel(self.html, "home"))
        for name, ids in PLACES.items():
            block = panel(self.html, name)
            for ident in ids:
                with self.subTest(tab=name, id=ident):
                    self.assertEqual(self.html.count(f'id="{ident}"'), 1, ident)
                    self.assertIn(f'id="{ident}"', block)

    def test_the_tab_bar_links_the_five_routes(self) -> None:
        nav = self.html[self.html.index('<nav id="tab-bar"'):self.html.index("</nav>")]
        links = re.findall(r'<a id="tab-link-([a-z]+)" class="tab-link" href="(#/[a-z]+)" data-tab="([a-z]+)"', nav)
        self.assertEqual(links, [(n, f"#/{n}", n) for n in ("home", "protocols", "experiment", "records", "settings")])
        for word in ("홈", "프로토콜", "실험", "기록", "설정"):
            self.assertIn(f"<span>{word}</span>", nav)
        self.assertIn('aria-label="화면"', nav)
        # The bar comes after the workspace, before the modal, and outside the panels.
        self.assertLess(self.html.index("</main>"), self.html.index('<nav id="tab-bar"'))
        self.assertLess(self.html.index('<nav id="tab-bar"'), self.html.index('id="new-session-modal"'))

    def test_the_old_wrappers_and_the_detail_view_are_gone(self) -> None:
        for gone in ('id="hero-setup"', 'id="toggle-setup-btn"', 'class="setup-grid"', 'class="panel voice-panel"',
                     'class="voice-actions"', 'id="wizard-detail"', "자세히 보기", "voiney-lab.start-view",
                     "wizardDetailChosen", "wizardChooseDetail"):
            with self.subTest(gone=gone):
                self.assertNotIn(gone, self.html)

    def test_the_sheet_markup_wraps_the_wizard_after_the_main(self) -> None:
        # Lane IA addendum (2026-10-19): the sheet, its backdrop and its ×; the wizard inside it, outside every panel.
        sheet = self.html[self.html.index('<div id="wizard-sheet"'):self.html.index('<nav id="tab-bar"')]
        self.assertLess(self.html.index("</main>"), self.html.index('<div id="wizard-sheet"'))
        for needle in ('id="wizard-sheet-backdrop"', 'role="dialog" aria-modal="true" aria-labelledby="wizard-title"',
                       '<button id="wizard-close" type="button" class="wizard-close" aria-label="닫기">×</button>', '<section id="start-wizard"'):
            self.assertIn(needle, sheet, needle)
        self.assertNotIn('id="start-wizard"', panel(self.html, "experiment"))
        self.assertIn('<button id="experiment-start-open" type="button" class="home-card" data-card="new-experiment">', panel(self.html, "experiment"))
        self.assertIn(".wizard-sheet{position:fixed;inset:0;", self.style)
        self.assertIn('.wizard-sheet[data-mode="page"]{position:static;display:block}', self.style)
        self.assertIn('body[data-auth="required"] .topbar,body[data-auth="required"] .sticky-rail,body[data-auth="required"] .shell{display:none}', self.style)
        wide = self.style[self.style.index("@media (min-width:900px){\n     .wizard-sheet{"):]
        self.assertIn(".wizard-sheet-panel{width:min(560px,100%);", wide[:wide.index("\n    }\n")])
        self.assertIn('body[data-view="before"] #tab-experiment .voice-console .controls{display:none}', self.style)
        self.assertIn('body[data-view="before"] #tab-experiment .workflow-grid,body[data-view="before"] #tab-experiment .timeline{display:none}', self.style)

    def test_the_bar_is_fixed_below_on_a_phone_and_a_menu_on_the_left_of_a_wide_screen(self) -> None:
        self.assertIn(".tab-bar{position:fixed;left:0;right:0;bottom:0;", self.style)
        self.assertIn("--tab-bar-h:64px", self.style)
        self.assertIn("min-height:var(--target-run)", self.style[self.style.index(".tab-link{"):self.style.index("}", self.style.index(".tab-link{"))])
        wide = self.style[self.style.index("@media (min-width:900px){\n     .tab-bar{"):]
        wide = wide[:wide.index("\n    }\n")]
        self.assertIn(".tab-bar{top:0;bottom:0;right:auto;width:var(--side-menu-w);flex-direction:column;", wide)
        self.assertIn('body:not([data-view="during"]):not([data-auth="required"]){padding-left:var(--side-menu-w);padding-bottom:0}', wide)
        # During the run and before a login there is no bar.
        self.assertIn('body[data-view="during"] .tab-bar,body[data-auth="required"] .tab-bar{display:none}', self.style)
        self.assertIn(".tab-panel[hidden]{display:none}", self.style)
        # The run's band over another tab, fixed below where the bar was.
        self.assertIn('<div id="run-return-band" class="run-return-band" role="status" aria-live="polite" hidden>', self.html)
        self.assertIn('<a id="run-return-link" class="run-return-link" href="#/experiment">돌아가기</a>', self.html)
        self.assertIn(".run-return-band{position:fixed;left:0;right:0;bottom:0;", self.style)
        # On another tab during the run the rail's timer and safety badges show (never hidden by the tab).
        self.assertIn('body[data-view="during"]:not([data-tab="experiment"]) #rail-timer-badge,body[data-view="during"]:not([data-tab="experiment"]) #rail-safety-badge{display:inline-flex}', self.style)
        self.assertIn('body[data-view="during"]:not([data-tab="experiment"]) .rail-right{display:none}', self.style)


class RouteTests(unittest.TestCase):
    """Decision ①: the hash names the tab; the panels follow it."""

    def test_no_hash_and_an_unknown_hash_open_the_home(self) -> None:
        result = run(r"""
await boot(base());
assert(tab()==="home",`tab: ${tab()}`);
assert(shownPanels().join()==="home",`panels: ${shownPanels()}`);
assert(node("tab-link-home").getAttribute("aria-current")==="page","the home link is not current");
assert(node("tab-link-settings").getAttribute("aria-current")==="false","another link is current");
go("#/nowhere");
assert(tab()==="home"&&shownPanels().join()==="home",`unknown: ${tab()} ${shownPanels()}`);
assert(parseRoute("#/protocols/p%201").id==="p 1","the id is not decoded");
assert(parseRoute("#/records/").tab==="records"&&parseRoute("#/records/").id===null,"a trailing slash");
assert(routePath("protocols","p 1")==="#/protocols/p%201","the path is not encoded");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_each_route_shows_its_panel_alone(self) -> None:
        result = run(r"""
await boot(base());
for(const [hash,name,link] of [["#/protocols","protocols","protocols"],["#/experiment","experiment","experiment"],["#/records","records","records"],["#/settings","settings","settings"],["#/home","home","home"],["#/upload","upload","protocols"]]){
 go(hash);
 assert(tab()===name,`${hash} → ${tab()}`);
 assert(shownPanels().join()===name,`${hash} panels: ${shownPanels()}`);
 assert(node(`tab-link-${link}`).getAttribute("aria-current")==="page",`${hash}: link not current`);
}
assert(document.body.dataset.route==="#/upload","the route is not kept on the body");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_navigate_sets_the_hash_so_the_back_button_works(self) -> None:
        result = run(r"""
await boot(base());
navigateTab("records");
assert(globalThis.location.hash==="#/records",`hash: ${globalThis.location.hash}`);
assert(tab()==="records");
navigateTab("protocols","p-2");
assert(globalThis.location.hash==="#/protocols/p-2",`hash: ${globalThis.location.hash}`);
navigateTab("home",null,{replace:true});
assert(globalThis.history.replaced.at(-1)==="#/home","a replace did not go through history.replaceState");
assert(tab()==="home");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_protocol_route_picks_that_protocol_and_a_record_route_that_session(self) -> None:
        result = run(r"""
await boot(withExperiments([RESUMABLE]));
calls.length=0;
go("#/protocols/p-1");await settle(8);
assert(node("protocol-id").value==="p-1",`select: ${node("protocol-id").value}`);
assert(calls.some(call=>call.url.includes("/api/protocols/p-1/review")),"the review was not read");
go("#/records/s-9");await settle(8);
assert(node("experiment-session-select").value==="s-9",`session: ${node("experiment-session-select").value}`);
assert(currentExperimentSessionId==="s-9","the page did not take the session");
assert(calls.some(call=>call.url.includes("/api/workspace/experiments/s-9/timeline")),"the timeline was not read");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_wizard_is_a_sheet_the_page_opens_and_closes_over_any_tab(self) -> None:
        # Lane IA addendum (2026-10-19): the step-by-step screens open as a sheet over the page, never by themselves.
        result = run(r"""
await boot(base());
assert(document.body.dataset.wizard==="off"&&node("wizard-sheet").hidden===true,`home: wizard ${document.body.dataset.wizard}`);
go("#/experiment");
assert(document.body.dataset.wizard==="off"&&node("wizard-sheet").hidden===true,"the wizard opened by itself on the experiment tab");
assert(node("experiment-start-open"),"the experiment tab has no start button");
node("experiment-start-open").dispatch("click");
assert(wizard.open===true&&document.body.dataset.wizard==="on"&&node("wizard-sheet").hidden===false,"the start button did not open the sheet");
assert(node("wizard-sheet").dataset.mode==="sheet"&&node("wizard-close").hidden===false,"not a sheet with its ×");
assert(wizard.step==="voice"&&tab()==="experiment",`step ${wizard.step} tab ${tab()}`);
assert(globalThis.history.pushed.length===1,"no history entry for the back button");
node("wizard-close").dispatch("click");
assert(wizard.open===false&&node("wizard-sheet").hidden===true,"× did not close the sheet");
assert(globalThis.history.backs===1,"× did not pop the sheet's history entry");
assert(tab()==="experiment"&&panelShown("experiment"),"the page under the sheet changed");
openWizardSheet();node("wizard-sheet-backdrop").dispatch("click");
assert(wizard.open===false,"a press outside did not close the sheet");
openWizardSheet();onWizardSheetKey({key:"Escape"});
assert(wizard.open===false,"Esc did not close the sheet");
openWizardSheet();onWizardSheetKey({key:"Enter"});
assert(wizard.open===true,"another key closed the sheet");closeWizardSheet();
openWizardSheet();const backsBefore=globalThis.history.backs;globalThis.history.popBack();
assert(wizard.open===false&&globalThis.history.backs===backsBefore+1,"the back button did not close the sheet (or history.back was called again)");
go("#/settings");openWizardSheet("protocol");
assert(wizard.open===true&&tab()==="settings"&&panelShown("settings"),"the sheet took the settings tab away");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_start_closes_the_sheet_and_opens_the_run_tab(self) -> None:
        result = run(r"""
let started=0;startReviewedProtocol=async()=>{started+=1;};
await boot(base());
go("#/protocols/p-1");await settle(8);
node("protocol-start").dispatch("click");await settle(8);
assert(wizard.open===true&&["check","safety"].includes(wizard.step),`the protocol's start did not open the sheet at the questions: ${wizard.step}`);
assert(node("protocol-id").value==="p-1","the protocol was not kept");
wizardGo("start");await wizardPrimary();await settle();
assert(started===1,"the sheet's last press did not start");
assert(wizard.open===false&&tab()==="experiment",`after the start: open ${wizard.open} tab ${tab()}`);
// A run that begins while the sheet is open closes it too.
go("#/home");openWizardSheet();startRun();
assert(wizard.open===false&&node("wizard-sheet").hidden===true&&tab()==="experiment","the run left the sheet open");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_first_login_offers_the_setup_once_as_a_sheet(self) -> None:
        result = run(r"""
const stored={};globalThis.localStorage={getItem:key=>stored[key]??null,setItem:(key,value)=>{stored[key]=String(value);},removeItem:key=>{delete stored[key];}};
let signedIn=false;
await boot([["/api/auth/session",()=>ACCOUNT_SESSION(signedIn)],["/api/auth/login",()=>{signedIn=true;return {signed_in:true};}],...base(ACCOUNT_SESSION(false))]);
assert(node("wizard-sheet").dataset.mode==="page"&&node("wizard-close").hidden===true,"the login is not a page of its own");
node("wizard-name").value="김재준";node("wizard-password").value="password1";
await wizardSubmitLogin();await settle(8);
assert(wizard.open===true&&wizard.mode==="setup"&&node("wizard-sheet").dataset.mode==="sheet",`after the login: open ${wizard.open} mode ${wizard.mode}`);
assert(wizardSteps().join()==="voice,agent",`setup steps: ${wizardSteps()}`);
assert(node("wizard-kicker").textContent.includes("첫 설정"),node("wizard-kicker").textContent);
assert(walk(body()).some(el=>el.textContent.includes("설정 탭에서 언제든")),"the way back to the settings is not said");
assert(node("wizard-quick").hidden===true,"a quick start inside the setup");
primary().dispatch("click");await settle();
assert(wizard.step==="agent"&&primary().textContent==="다 됐어요",`last setup step: ${wizard.step} ${primary().textContent}`);
primary().dispatch("click");await settle();
assert(wizard.open===false,"the setup did not close at its end");
assert(stored["voiney-lab.first-setup:a1"]==="done","the offer was not remembered");
// Offered once: a second login does not ask again.
wizard.open=false;offerFirstSetup();
assert(wizard.open===false,"the setup was offered twice");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_run_opens_the_experiment_tab_and_another_tab_carries_the_band(self) -> None:
        result = run(r"""
await boot(base());
go("#/records");
startRun();
assert(document.body.dataset.view==="during","view");
assert(tab()==="experiment",`the run did not open its tab: ${tab()}`);
assert(globalThis.history.replaced.at(-1)==="#/experiment","the run's tab was pushed, not replaced");
assert(node("run-return-band").hidden===true,"the band shows on the experiment tab");
// The records (the run's own observations) stay under the run view, as lane UI0 left them.
assert(panelShown("experiment")&&panelShown("records"),`panels during: ${shownPanels()}`);
go("#/records");
assert(tab()==="records");
assert(node("run-return-band").hidden===false,"the band is missing on another tab");
assert(node("run-return-text").textContent==="실험 중 · 3단계",`band: ${node("run-return-text").textContent}`);
go("#/experiment");
assert(node("run-return-band").hidden===true,"the band stayed");
// The end of the experiment: the after view keeps the experiment tab; the band is gone.
experimentEndedOnScreen=true;renderScreenView();
assert(document.body.dataset.view==="after"&&node("run-return-band").hidden===true);
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_before_a_login_only_the_login_shows_whatever_the_hash(self) -> None:
        result = run(r"""
let signedIn=false;
await boot([["/api/auth/session",()=>ACCOUNT_SESSION(signedIn)],["/api/auth/login",()=>{signedIn=true;return {signed_in:true};}],...base(ACCOUNT_SESSION(false))]);
go("#/records");
assert(document.body.dataset.auth==="required",`auth: ${document.body.dataset.auth}`);
assert(shownPanels().join()==="",`login panels: ${shownPanels()}`);
assert(document.body.dataset.wizard==="on"&&wizard.step==="login"&&node("wizard-sheet").dataset.mode==="page",`step: ${wizard.step}`);
assert(globalThis.location.hash==="#/records","the hash was rewritten by the login");
node("wizard-name").value="김재준";node("wizard-password").value="password1";
await wizardSubmitLogin();await settle(8);
assert(document.body.dataset.auth==="ok",`after login: ${document.body.dataset.auth}`);
assert(tab()==="records",`the asked tab did not open after the login: ${tab()}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr)


class DetailViewGoneTests(unittest.TestCase):
    """Decision ⑦: a remembered "자세히 보기" means nothing; the wizard is the start."""

    def test_a_remembered_detail_choice_does_not_hide_the_wizard(self) -> None:
        result = run(r"""
const stored={"voiney-lab.start-view":"detail"};globalThis.sessionStorage={getItem:key=>stored[key]??null,setItem:(key,value)=>{stored[key]=String(value);},removeItem:key=>{delete stored[key];}};
await boot(base());
go("#/experiment");openWizardSheet();
assert(document.body.dataset.wizard==="on","the old choice hid the wizard");
assert(typeof globalThis.wizardDetailChosen==="undefined","the detail choice is still read");
""")
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
