"""Lane UI0, decision 3 (2026-10-12): the settings in three groups, and the run screen's sheets.

The eleven settings sit in one section in three groups -- 프로토콜, 음성 입력,
안내 방식 -- open across the setup before and after the experiment (no longer
one column beside an empty one) and opened as a sheet during it, where they
still save as before. On the run screen the conversation, the pictures and
the settings open one at a time, are shut by the experimenter (닫기, a touch
beside the sheet, Escape) and shut with the run view; the 그림 button says
"새 자료" while the step has a picture not yet looked at.
"""

from __future__ import annotations

import re
import unittest

from tests.test_screen_cleanup import INDEX, run_page_script

GROUPS = (
    ("프로토콜", ("protocol-id", "question-timing")),
    ("음성 입력", ("input-language", "mic-device", "agc-mode", "voice-calibration-start", "speaker-sensitivity", "ambient-mode")),
    ("안내 방식", ("confirm-mode", "proactive-mode", "web-lookup")),
)

SETUP = r"""
const configuration_id=83;
acceptedSessionConfiguration={configuration_id,mode:"cascade",language:"ko",server_generation:4,protocol_id:"protocol-x",revision_id:"pdf-1-analysis-1"};socket=new WS();sessionActive=true;
document.body=new Element("body");
async function say(message){await onMessage({data:JSON.stringify(message)},sessionGeneration,socket);}
let revision=0;
const state=(extra={})=>({attached:true,protocol_id:"protocol-x",revision_id:"pdf-1-analysis-1",display_name:"In-gel digestion",development_only:false,readiness_status:"guidance_ready",active:true,current_step_label:"4",current_step_id:"step-4",total_steps:12,at_final_step:false,block_reason:null,revision:++revision,display_summary:"Add buffer.",primary_summary:"완충액을 넣습니다.",source_language:"en",spoken_summary:null,warning_texts:[],warning_presentations:[],visual_assets:[],visual_status:"unavailable",source_page_refs:[4],workflow_status:"running",source_sha256:"a".repeat(64),...extra});
async function step(extra={}){await say({type:"protocol.fixture.state",configuration_id,action:"start",state:state(extra)});}
const sheet=()=>document.body.dataset.sheet||"";
const expanded=id=>node(id).attributes["aria-expanded"];
"""


class SettingsGroupTests(unittest.TestCase):

    def test_the_eleven_settings_are_in_three_groups_in_one_section(self) -> None:
        html = INDEX.read_text(encoding="utf-8")
        start = html.index('<section id="settings-sheet"')
        section = html[start:html.index("</section>", start)]
        fieldsets = re.findall(r"<fieldset class=\"settings-group\"[^>]*><legend[^>]*>([^<]+)</legend>(.*?)</fieldset>", section, re.S)
        self.assertEqual([legend for legend, _ in fieldsets], [legend for legend, _ in GROUPS])
        for (legend, body), (_, ids) in zip(fieldsets, GROUPS):
            found = re.findall(r'id="(protocol-id|question-timing|input-language|mic-device|agc-mode|voice-calibration-start|speaker-sensitivity|ambient-mode|confirm-mode|proactive-mode|web-lookup)"', body)
            self.assertEqual(tuple(found), ids, legend)
        for _, ids in GROUPS:
            for ident in ids:
                self.assertEqual(html.count(f'id="{ident}"'), 1, ident)
        head = section[:section.index('<div class="settings-groups">')]
        self.assertIn('id="experimenter-settings-status"', head)
        self.assertNotIn('class="setup-col-right"', html)

    def test_a_setting_changed_in_the_sheet_during_the_run_is_saved_as_before(self) -> None:
        result = run_page_script(SETUP + r"""
await step();
assert(openRunSheet("settings")&&sheet()==="settings","the settings sheet did not open");
const calls=[];globalThis.fetch=async(url,options={})=>{calls.push(`${options.method||"GET"} ${url} ${options.body||""}`);return json({settings:{confirm_mode:"confirm"}});};
node("confirm-mode").value="confirm";node("confirm-mode").dispatch("change");
await new Promise(resolve=>setTimeout(resolve,0));
assert(calls.some(call=>call.startsWith("PUT /api/experimenter/settings")&&call.includes('"confirm_mode":"confirm"')),`not saved: ${calls}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


class RunSheetTests(unittest.TestCase):

    def test_sheets_open_only_on_the_run_screen_one_at_a_time(self) -> None:
        result = run_page_script(SETUP + r"""
assert(!openRunSheet("settings")&&sheet()==="","a sheet opened before the experiment");
await step();
node("run-open-settings").dispatch("click");
assert(sheet()==="settings"&&expanded("run-open-settings")==="true"&&node("run-sheet-backdrop").hidden===false,`settings: ${sheet()}`);
node("run-open-conversation").dispatch("click");
assert(sheet()==="conversation"&&expanded("run-open-settings")==="false"&&expanded("run-open-conversation")==="true",`one at a time: ${sheet()}`);
node("run-open-conversation").dispatch("click");
assert(sheet()===""&&node("run-sheet-backdrop").hidden===true,`the opener did not shut it: ${sheet()}`);
openRunSheet("figures");node("run-close-figures").dispatch("click");
assert(sheet()==="","닫기 did not shut the pictures");
openRunSheet("settings");node("run-sheet-backdrop").dispatch("click");
assert(sheet()==="","a touch beside the sheet did not shut it");
openRunSheet("conversation");
assert(onRunSheetKey({key:"Escape"})&&sheet()==="","Escape did not shut it");
assert(!onRunSheetKey({key:"Escape"}),"Escape with nothing open did something");
assert(!openRunSheet("elsewhere")&&sheet()==="","an unknown sheet opened");
openRunSheet("settings");
await say({type:"session.stopped",configuration_id});
assert(sheet()===""&&document.body.dataset.view==="before",`the sheet outlived the run view: ${sheet()}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_the_pictures_button_says_new_material_until_it_is_looked_at(self) -> None:
        result = run_page_script(SETUP + r"""
await step();
const fresh=()=>node("run-figures-fresh").hidden===false;
assert(!fresh(),"new material before any came");
await say({type:"speech.start",turn_id:11,generation:4});
await say({type:"protocol.web.result",configuration_id,turn_id:11,generation:4,status:"success",subject:"중탄산암모늄",screen:"완충액입니다.",citations:[{url:"https://example.org/a",title:"A",domain:"example.org"}],spoken_aloud:true});
assert(fresh(),"the web's explanation did not mark new material");
openRunSheet("figures");
assert(!fresh(),"new material shown while the sheet is open");
closeRunSheet();
assert(!fresh(),"material already looked at is new again");
const asset="b".repeat(64);
await say({type:"speech.start",turn_id:12,generation:4});
await say({type:"protocol.drawing.result",configuration_id,turn_id:12,generation:4,status:"success",diagram:{asset_id:asset,url:`/api/drawn-visuals/${asset}`,mime_type:"image/svg+xml",step_id:"step-4",step_label:"4",label:"AI 가 그린 그림 — 실제와 다를 수 있음",removed_numbers:[]}});
assert(fresh(),"the drawing did not mark new material");
await step({current_step_label:"5",current_step_id:"step-5"});
assert(!fresh(),"step 4's material is new on step 5");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_a_long_answer_card_offers_the_conversation_to_read_it_again(self) -> None:
        result = run_page_script(SETUP + r"""
await step();
const long="완충액은 pH 를 일정하게 유지합니다. ".repeat(8);
await say({type:"speech.start",turn_id:13,generation:4});
await say({type:"server.operation",turn_id:13,generation:4,route:"curated_protocol",operation:"grounded_qa"});
await say({type:"reply.complete",turn_id:13,generation:4,text:long});
const again=node("run-card").children.find(item=>item.className==="run-card-again");
assert(again&&again.textContent==="대화 보기에서 전부 읽기","no way to read the long answer again");
again.dispatch("click");
assert(document.body.dataset.sheet==="conversation","the conversation did not open");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_the_sheets_are_laid_out_from_below_from_the_right_and_as_a_column(self) -> None:
        html = INDEX.read_text(encoding="utf-8")
        style = html.split("<style>", 1)[1].split("</style>", 1)[0]
        self.assertIn('body[data-view="during"][data-sheet="settings"] #settings-sheet,body[data-view="during"][data-sheet="conversation"] .timeline,body[data-view="during"][data-sheet="figures"] .task-body-right{display:flex;', style)
        self.assertIn("@media (orientation:landscape) and (min-width:1100px){", style)
        self.assertIn('grid-template-areas:"left timer fig" "more timer fig"', style)
        self.assertIn(".settings-groups{display:grid;grid-template-columns:repeat(auto-fit,minmax(15rem,1fr))", style)
        for ident in ("run-open-conversation", "run-open-figures", "run-open-settings"):
            self.assertRegex(html, rf'<button id="{ident}" type="button" class="[^"]*run-sheet-open" aria-controls="[a-z-]+" aria-expanded="false">')


if __name__ == "__main__":
    unittest.main()
