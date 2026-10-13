"""Lane UI2, decision 4 (2026-10-13): the 실험 후 view after the drafts' P4 and M6.

The record comes first: a head saying how the experiment ended (the
server's experiment.ended), the badge of the source it followed and who ran
it; the counts of the report's values (the screen counts the server's own
list, as it counted "남은 N개" before -- the server sends no summary); the
first value not yet confirmed as the M6 card with 64px buttons; the values
by step with the time each was recorded, a correction with its first words
and whether the experimenter confirmed it; and 보고서 만들기, the Word
report as before. Confirming and correcting ask the server as before.
There is no approval and no rejection; a value set against the source's
own (기준치와 다름) is not sent and not drawn.
"""

from __future__ import annotations

import re
import unittest

from tests.test_screen_cleanup import INDEX, run_page_script

SETUP = r"""
// The fake DOM has no replaceWith or focus; a browser swaps the node in place and focuses.
Element.prototype.focus=function(){globalThis.document.activeElement=this;};
Element.prototype.replaceWith=function(...nodes){const parent=this.parentNode;const at=parent.children.indexOf(this);parent.children.splice(at,1,...nodes);for(const item of nodes)item.parentNode=parent;};
const configuration_id=9;
acceptedSessionConfiguration={configuration_id,mode:"cascade",language:"ko",server_generation:0,protocol_id:"p-1",revision_id:"pdf-1-analysis-1"};socket=new WS();
const say=message=>onMessage({data:JSON.stringify({configuration_id,...message})},sessionGeneration,socket);
const items=(confirmedSeven=false)=>[
 {item_id:"r.e-2",kind:"측정",step_label:"2",text:"150 µL",source_key:"e-2",corrected:true,original:"100 µL",confirmed:true},
 {item_id:"r.e-4",kind:"이상",step_label:"4",text:"젤 조각이 아직 파래",source_key:"e-4",corrected:false,original:"",confirmed:true},
 {item_id:"r.e-7",kind:"측정",step_label:"7",text:"OD600 0.82",source_key:"e-7",corrected:false,original:"",confirmed:confirmedSeven}];
const report=(extra={})=>({report_id:"ER-0000000000000000AAAA",status:"completed",started_at:"2026-10-13T00:02:00+00:00",protocol_id:"p-1",protocol_title:"In-gel digestion",event_count:4,anomaly_count:1,blocker_count:0,
 events:[{event_key:"e-2",event_type:"observation",step_label:"2",created_at:"2026-10-13T00:18:00Z",user_wording:"100 µL"},{event_key:"e-4",event_type:"anomaly",step_label:"4",created_at:"2026-10-13T00:31:00Z",user_wording:"젤 조각이 아직 파래"},{event_key:"e-7",event_type:"observation",step_label:"7",created_at:"2026-10-13T00:42:00Z",user_wording:"OD600 0.82"}],
 review:items(),...extra});
const clock=iso=>localDateParts(iso).time;
const box=()=>node("report-review-checklist");
const kids=(element,className)=>element.children.filter(child=>child.className===className);
const deep=(element,className)=>[element,...element.children.flatMap(child=>deep(child,className))].filter(child=>child.className===className);
const settle=()=>new Promise(resolve=>setTimeout(resolve,10));
"""


class ValuesByStepTests(unittest.TestCase):

    def test_counts_the_m6_card_and_the_values_by_step(self) -> None:
        result = run_page_script(SETUP + r"""
await say({type:"experiment.report.state",generation:0,report:report()});
assert(box().hidden===false,"the values are hidden after the end");
assert(box().children[0].textContent==="보고서에 넣을 값 확인 · 남은 1개",box().children[0].textContent);
const summary=kids(box(),"report-review-summary")[0];
assert(summary.textContent==="실험자 확인 2미확인 1정정 1이상 1",summary.textContent);
const card=kids(box(),"report-pending")[0];
assert(card.children[0].textContent==="미확인 기록 1건",card.children[0].textContent);
const pending=card.children[1];
assert(deep(pending,"report-review-text")[0].textContent==="7단계 측정: OD600 0.82"&&deep(pending,"report-review-time")[0].textContent===clock("2026-10-13T00:42:00Z"),visibleText(pending));
assert(deep(pending,"report-review-actions")[0].children.map(button=>button.textContent).join("|")==="✓ 확인|고치기","the card's buttons");
const steps=kids(box(),"report-review-step");
assert(steps.map(step=>step.children[0].textContent).join("|")==="2단계|4단계|7단계",steps.map(step=>step.children[0].textContent).join("|"));
const corrected=steps[0].children[1];
assert(deep(corrected,"report-review-text")[0].textContent==="측정: 150 µL"&&deep(corrected,"report-review-history")[0].textContent==="정정됨 (처음: 100 µL)"&&deep(corrected,"report-review-time")[0].textContent===clock("2026-10-13T00:18:00Z"),visibleText(corrected));
assert(deep(corrected,"ui-badge")[0].textContent==="✓ 실험자 확인"&&deep(corrected,"ui-badge")[0].dataset.kind==="quiet","a confirmed value's tag");
const open=steps[2].children[1];
assert(open.dataset.confirmed==="false"&&deep(open,"ui-badge")[0].textContent==="미확인"&&deep(open,"ui-badge")[0].dataset.kind==="provisional","an unconfirmed value's tag");
const text=visibleText(box());
for(const word of ["승인","반려","기준치","PI"])assert(!text.includes(word),`${word} on the record`);
assert(node("export-report-docx").href==="/api/experiment-reports/ER-0000000000000000AAAA.docx"&&node("export-report-docx").attributes["aria-disabled"]==="false","보고서 만들기 is not the Word report");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_confirming_and_correcting_ask_the_server_as_before(self) -> None:
        result = run_page_script(SETUP + r"""
const calls=[];globalThis.fetch=async(url,options={})=>{calls.push(`${options.method||"GET"} ${url} ${options.body||""}`);if(String(url).includes("/review/"))return json({items:items(true)});return json({state:"ready",label:"보고서 문장 준비됨"});};
await say({type:"experiment.report.state",generation:0,report:report()});
const confirm=deep(kids(box(),"report-pending")[0],"report-review-actions")[0].children[0];
confirm.dispatch("click");await settle();
assert(calls.some(call=>call.startsWith("POST /api/experiment-reports/ER-0000000000000000AAAA/review/r.e-7")&&call.includes('"action":"confirm"')),`confirm: ${calls}`);
assert(kids(box(),"report-pending").length===0&&box().children[0].textContent==="보고서에 넣을 값 3개 · 모두 확인함","the list was not drawn again from the server's answer");
const row=kids(box(),"report-review-step")[2].children[1],actions=deep(row,"report-review-actions")[0];
actions.children[0].dispatch("click");
const input=actions.children.find(child=>child.tagName==="input"),save=actions.children.find(child=>child.textContent==="고쳐서 확인");
assert(input&&save&&input.value==="OD600 0.82","고치기 did not offer the value to correct");
input.value="OD600 0.84";save.dispatch("click");await settle();
assert(calls.some(call=>call.includes("/review/r.e-7")&&call.includes('"action":"correct"')&&call.includes("OD600 0.84")),`correct: ${calls}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_a_record_still_running_lists_nothing_to_confirm(self) -> None:
        result = run_page_script(SETUP + r"""
await say({type:"experiment.report.state",generation:0,report:report({status:"in_progress"})});
assert(box().hidden===true,"values to confirm while the record is still running");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


class AfterHeadTests(unittest.TestCase):

    def test_the_head_says_how_it_ended_what_it_followed_and_who_ran_it(self) -> None:
        result = run_page_script(SETUP + r"""
document.body=new Element("body");sessionActive=true;workspaceDisplayName="연구자 A";
await say({type:"protocol.fixture.state",action:"start",state:{attached:true,protocol_id:"p-1",revision_id:"pdf-1-analysis-1",display_name:"In-gel digestion",development_only:false,readiness_status:"guidance_ready",active:true,current_step_label:"12",current_step_id:"step-12",total_steps:12,at_final_step:true,block_reason:null,revision:3,display_summary:"Store.",primary_summary:"보관합니다.",source_language:"en",visual_assets:[],source_page_refs:[9],workflow_status:"running"},screen:{source_basis:{filename:"in-gel.pdf",uploaded_at:"2026-10-05T01:00:00+00:00",words:"지금 기준: in-gel.pdf · 2026년 10월 5일 올림"}}});
await say({type:"experiment.ended",status:"completed",report:"saved"});
assert(document.body.dataset.view==="after","not the after view");
assert(node("after-state").hidden===false&&node("after-state").textContent==="실험 완료",node("after-state").textContent);
assert(node("after-basis").textContent==="in-gel.pdf · 2026년 10월 5일 올림 · 분석 통과",node("after-basis").textContent);
assert(node("after-experimenter").textContent==="실험자 연구자 A",node("after-experimenter").textContent);
await say({type:"session.stopped"});
assert(node("after-state").textContent==="실험 완료"&&node("after-basis").textContent.includes("in-gel.pdf"),"stopping the session lost the head");
resetSessionUI();
assert(node("after-state").hidden===true&&node("after-basis").hidden===true&&node("after-experimenter").textContent==="","a new session kept the last head");
showExperimentEnded({status:"stopped"});
assert(node("after-state").textContent==="실험 종료"&&node("after-basis").hidden===true,"an end with no protocol state guessed a badge");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


class AfterViewMarkupTests(unittest.TestCase):

    def setUp(self) -> None:
        self.html = INDEX.read_text(encoding="utf-8")
        self.style = self.html.split("<style>", 1)[1].split("</style>", 1)[0]

    def test_the_record_comes_first_with_the_conversation_beside_it(self) -> None:
        self.assertIn('body[data-view="after"] .shell{grid-template-areas:"work chat" "setup setup"}', self.style)
        self.assertIn('@media (max-width:980px){body[data-view="after"] .shell{grid-template-areas:"work" "chat" "setup"}}', self.style)
        hidden = re.search(r'body\[data-view="after"\] #protocol-library,[^{]*\{display:none\}', self.style).group(0)
        for part in ("#protocol-compare", "#protocol-start-summary", ".mic-check"):
            self.assertIn(part, hidden)
        self.assertIn('body[data-view="after"] .after-head{display:flex;', self.style)
        self.assertIn(".after-head{display:none}", self.style)

    def test_report_make_is_the_word_report_out_of_the_fold_and_only_with_a_record(self) -> None:
        card = self.html[self.html.index('<aside class="report-card procedure-report"'):self.html.index("</aside>", self.html.index('<aside class="report-card procedure-report"'))]
        actions = card[card.index('<div class="report-actions">'):card.index('<details id="procedure-report-details">')]
        self.assertIn('<a id="export-report-docx" class="report-make" aria-disabled="true">보고서 만들기 (Word)</a>', actions)
        self.assertLess(actions.index('id="export-report-docx"'), actions.index('id="export-report-markdown"'))
        self.assertIn('.report-actions:not(:has(a[aria-disabled="false"])){display:none}', self.style)
        self.assertIn('body[data-view="after"] .report-actions a.report-make{min-height:var(--target-run);', self.style)
        self.assertIn(".report-pending .report-review-actions button{min-height:var(--target-run);", self.style)
        for word in ("승인", "반려"):
            self.assertNotIn(word, card)


if __name__ == "__main__":
    unittest.main()
