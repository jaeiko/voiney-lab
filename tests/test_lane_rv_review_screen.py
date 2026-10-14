"""The review screen (lane RV, stage ②): 원문 대조 보기 that a person decides in.

Human decision 2026-10-12, review scope 2026-10-14: the items waiting for a
person come first; the source's numbered lines (and an OCR value's piece of
the page) stand beside the picked item; its decision -- 확인, 고침, 원문에
없음으로 비움, 입력, 메모 -- is one request to the server, and the screen
reads the server's draft again; every value carries where it comes from
(원문 · AI 제안 · 검토자 입력); the reviewer's name and "검토 완료하고 등록"
make the review version; the versions are listed with what each changed.
No approval. One column on a phone.
"""

from __future__ import annotations

import re
import unittest

from tests.test_screen_cleanup import INDEX, run_page_script

SETUP = r"""
const ev=(page,text)=>({page,excerpt:text,continued_page:null,continued_excerpt:null,verified:true,lines:{page,first_line:3,last_line:3},suggested_lines:null});
const item=(id,extra={})=>({item_id:id,kind:"step",kind_ko:"단계",step_id:"s1",step_label:"1",section_id:"prep",text:"1. Add 2 mL of buffer A.",value_kind_ko:null,
 source:ev(1,"1. Add 2 mL of buffer A."),origin:"source",origin_ko:"원문",needs_review:false,covered_by:null,reasons:[],allowed_actions:["confirm","fix","memo","reset"],
 decision:null,memo:null,current:"1. Add 2 mL of buffer A.",origin_after:"source",origin_after_ko:"원문",differs_from_original:false,...extra});
const flagged=item("step:s2",{step_id:"s2",step_label:"2",text:"Spin for an hour.",current:"Spin for an hour.",origin:"ai_suggestion",origin_ko:"AI 제안",origin_after:"ai_suggestion",needs_review:true,
 reasons:[{item_id:"step:s2",reason:"not_in_source",reason_ko:"원문에 없음",field:"evidence",reason_code:"quote_not_found",page_number:1,matching_source_pages:[]}],
 source:{page:1,excerpt:"Spin for an hour.",verified:false,lines:null,suggested_lines:{page:1,first_line:4,last_line:4,similarity:0.7}}});
const timer=item("timer:s2",{kind:"timer",kind_ko:"단계 타이머",step_id:"s2",step_label:"2",text:"10 min",current:"10 min",value_kind_ko:"시간",seconds:[600],candidates:[],allowed_actions:["fix","clear","memo","reset"]});
const draft=(extra={})=>({protocol_id:"p-1",title:"Gamma",analysis_status:"review_pending",available_for_execution:false,
 target:{kind:"review_pending",event_id:"analysis-review-pending-a",recorded_at:"2026-10-14T01:00:00+00:00",revision_id:null,analysis_id:"a"},
 items:[item("step:s1"),flagged,timer],needs_review:["step:s2"],counts:{items:3,needs_review:1,decided:0,undecided:1,changed:0},
 pages:[{page:1,ocr:true,ocr_unclear:true,unclear_line_count:2,line_count:5}],registrable:false,registration_blockers:[{item_id:"step:s2"}],...extra});
const page1={protocol_id:"p-1",page:1,page_count:3,ocr:true,ocr_unclear:true,unclear_line_count:2,
 lines:[{number:1,text:"Protocol Gamma",has_value:false,unclear:false},{number:2,text:"Section preparation",has_value:false,unclear:false},{number:3,text:"1. Add 2 mL of buffer A.",has_value:true,unclear:true},{number:4,text:"2. Incubate for 10 min at 37 C.",has_value:true,unclear:true},{number:5,text:"Wear gloves.",has_value:false,unclear:false}],
 value_boxes:[{index:0,text:"10 min",box:[1,2,3,4]},{index:1,text:"37 C",box:[1,2,3,4]}],render_dpi:144};
const calls=[];
const serve=(answers)=>{globalThis.fetch=async(url,options={})=>{calls.push({url:String(url),method:options.method||"GET",body:typeof options.body==="string"?JSON.parse(options.body):null});for(const [pattern,answer] of answers){if(String(url).includes(pattern))return typeof answer==="function"?answer(options):json(answer);}throw new Error(`unexpected ${url}`);};};
const tick=()=>new Promise(resolve=>setTimeout(resolve,0));
const rows=()=>node("review-items").children.map(row=>row.firstChild);
const buttonsIn=host=>{const found=[];const walk=el=>{if(el.tagName==="button")found.push(el);for(const child of el.children||[])walk(child);};walk(host);return found;};
"""


class ReviewItemsTests(unittest.TestCase):

    def test_items_waiting_for_a_person_come_first_with_their_badges(self) -> None:
        result = run_page_script(SETUP + r"""
serve([["/review-draft/source-pages/1",page1],["/review-draft",draft()]]);
await loadReviewDraft("p-1");await tick();await tick();
const editor=node("protocol-review-editor");
assert(editor.hidden===false&&editor.open===true,"the editor did not open for a review-pending analysis");
assert(rows().map(row=>row.dataset.itemId).join(",")==="step:s2,step:s1,timer:s2",rows().map(row=>row.dataset.itemId).join(","));
const first=visibleText(rows()[0]);
for(const words of ["2단계 · 단계","Spin for an hour.","AI 제안","확인 필요 · 원문에 없음"])assert(first.includes(words),`${words}: ${first}`);
assert(visibleText(rows()[1]).includes("원문")&&!visibleText(rows()[1]).includes("확인 필요"),visibleText(rows()[1]));
const strip=node("review-editor-strip").children.map(badge=>`${badge.dataset.kind}:${badge.textContent}`);
assert(JSON.stringify(strip)===JSON.stringify(["warning:검토 대기 분석","danger:확인 필요 1건","warning:OCR 흐린 줄 2개"]),`strip: ${strip}`);
assert(node("review-editor-summary").textContent===" · 확인 필요 1건 · 남은 1건",node("review-editor-summary").textContent);
// The flagged item is picked first: its page's numbered lines, the server's guess dashed.
const lines=node("review-source").children[1].children.map(row=>row.firstChild);
assert(lines.length===5&&lines[3].className.includes("is-suggested")&&lines[2].className.includes("is-unclear"),lines.map(line=>line.className).join("|"));
assert(visibleText(lines[3]).includes("4")&&visibleText(lines[3]).includes("2. Incubate for 10 min at 37 C."),visibleText(lines[3]));
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_a_refused_quote_is_confirmed_only_with_the_lines_picked(self) -> None:
        result = run_page_script(SETUP + r"""
serve([["/review-draft/source-pages/1",page1],["/review-decisions",{item:{},counts:{},registrable:true}],["/review-draft",draft()]]);
await loadReviewDraft("p-1");await tick();await tick();
const confirm=()=>buttonsIn(node("review-item")).find(button=>button.dataset.action==="confirm");
confirm().dispatch("click");await tick();
assert(!calls.some(call=>call.method==="POST"),"a confirm without lines was sent");
assert(visibleText(node("review-item")).includes("원문에서 이 항목이 있는 줄을 눌러 골라 주세요."),visibleText(node("review-item")));
const lines=node("review-source").children[1].children.map(row=>row.firstChild);
lines[3].dispatch("click");await tick();
confirm().dispatch("click");await tick();await tick();
const post=calls.find(call=>call.method==="POST");
assert(post&&post.url==="/api/protocols/p-1/review-decisions",JSON.stringify(calls));
assert(JSON.stringify(post.body)===JSON.stringify({item_id:"step:s2",action:"confirm",target:draft().target,source_lines:{page:1,first_line:4,last_line:4}}),JSON.stringify(post.body));
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_the_lines_may_be_picked_on_another_page(self) -> None:
        result = run_page_script(SETUP + r"""
const page2={...page1,page:2,value_boxes:[],lines:[{number:1,text:"Spin the tube for an hour at 4 C.",has_value:true,unclear:false},{number:2,text:"Discard the supernatant.",has_value:false,unclear:false}]};
serve([["/review-draft/source-pages/2",page2],["/review-draft/source-pages/1",page1],["/review-decisions",{item:{},counts:{},registrable:true}],["/review-draft",draft()]]);
await loadReviewDraft("p-1");await tick();await tick();
const head=()=>node("review-source").children[0],turn=()=>buttonsIn(head());
assert(turn().map(button=>`${button.textContent}:${button.disabled}`).join("|")==="앞 쪽:true|다음 쪽:false",turn().map(button=>`${button.textContent}:${button.disabled}`).join("|"));
turn()[1].dispatch("click");await tick();await tick();
assert(visibleText(head()).includes("원문 p.2"),visibleText(head()));
const lines=node("review-source").children[1].children.map(row=>row.firstChild);
assert(lines.length===2,String(lines.length));
lines[0].dispatch("click");await tick();await tick();
buttonsIn(node("review-item")).find(button=>button.dataset.action==="confirm").dispatch("click");await tick();await tick();
const post=calls.find(call=>call.method==="POST");
assert(JSON.stringify(post?.body?.source_lines)===JSON.stringify({page:2,first_line:1,last_line:1}),JSON.stringify(post?.body));
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_a_timer_is_fixed_in_minutes_and_a_name_goes_with_the_decision(self) -> None:
        result = run_page_script(SETUP + r"""
serve([["/review-draft/source-pages/1",page1],["/review-decisions",{item:{},counts:{},registrable:true}],["/review-draft",draft()]]);
await loadReviewDraft("p-1");await tick();
rows()[2].dispatch("click");await tick();
const host=node("review-item"),input=host.children.find(child=>child.id==="review-item-value");
assert(input&&input.type==="number"&&input.value==="10",`timer input: ${input?.type} ${input?.value}`);
assert(!buttonsIn(host).some(button=>button.dataset.action==="confirm"),"a timer offered a plain confirm");
input.value="15";node("reviewer-name").value="  김재준 ";
buttonsIn(host).find(button=>button.dataset.action==="fix").dispatch("click");await tick();
const post=calls.find(call=>call.method==="POST");
assert(post.body.item_id==="timer:s2"&&post.body.action==="fix"&&post.body.seconds===900&&post.body.reviewer_name==="김재준"&&!("source_lines" in post.body),JSON.stringify(post.body));
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_the_actions_are_the_servers_and_a_refusal_is_said_in_words(self) -> None:
        result = run_page_script(SETUP + r"""
serve([["/review-draft/source-pages/1",page1],["/review-decisions",()=>json({detail:"review_decision_invalid"},false)],["/review-draft",draft()]]);
await loadReviewDraft("p-1");await tick();
rows()[1].dispatch("click");await tick();
const host=node("review-item"),actions=buttonsIn(host).map(button=>button.textContent);
assert(JSON.stringify(actions)===JSON.stringify(["확인","고침","메모"]),`actions: ${actions}`);
host.children.find(child=>child.id==="review-item-value").value="1. Add 2.5 mL of buffer A.";
buttonsIn(host).find(button=>button.dataset.action==="fix").dispatch("click");await tick();await tick();
assert(visibleText(node("review-item")).includes("이 항목에는 그렇게 할 수 없습니다."),visibleText(node("review-item")));
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_a_passed_analysis_is_read_only_when_a_person_opens_it(self) -> None:
        result = run_page_script(SETUP + r"""
const passed=draft({target:{kind:"analysis",analysis_revision_number:2},items:[item("step:s1")],needs_review:[],
 counts:{items:1,needs_review:0,decided:0,undecided:0,changed:0},registrable:true,registration_blockers:[]});
serve([["/review-draft/source-pages/1",page1],["/review-draft",passed]]);
const editor=node("protocol-review-editor");
assert(showReviewEditor("p-1",{protocol_id:"p-1",analysis_available:true,review_pending:null})===true,"the editor was not offered for a passed analysis");
assert(editor.hidden===false&&editor.open!==true,`hidden=${editor.hidden} open=${editor.open}`);
assert(calls.length===0,`read before it was opened: ${JSON.stringify(calls)}`);
editor.open=true;editor.dispatch("toggle");await tick();await tick();
assert(calls[0]?.url==="/api/protocols/p-1/review-draft",JSON.stringify(calls));
assert(rows().map(row=>row.dataset.itemId).join(",")==="step:s1",rows().map(row=>row.dataset.itemId).join(","));
assert(node("review-editor-strip").children[0].textContent==="통과한 분석",node("review-editor-strip").children[0].textContent);
// Nothing to review before an analysis exists.
assert(showReviewEditor("p-2",{protocol_id:"p-2",analysis_available:false,review_pending:null})===false&&editor.hidden===true,"the editor was offered without an analysis");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


class RegisterAndHistoryTests(unittest.TestCase):

    def test_register_needs_every_item_decided_and_a_name(self) -> None:
        result = run_page_script(SETUP + r"""
const ready=draft({registrable:true,counts:{items:3,needs_review:1,decided:1,undecided:0,changed:1},registration_blockers:[]});
serve([["/review-draft/source-pages/1",page1],["/review-versions",{protocol:{protocol_id:"p-1"},review_version:{version:1,version_ko:"검토본 v1",revision_id:"pdf-1-review-1",reviewer_name:"김재준"}}],["/review-draft",ready],["/api/protocols/p-1/review",{protocol_id:"p-1"}],["/api/protocols",{protocols:[]}]]);
reviewProtocolId="p-1";renderReviewEditor(draft());
assert(node("review-register").disabled===true,"register enabled with an item undecided");
assert(node("review-register-status").textContent.includes("확인 필요 항목 1건"),node("review-register-status").textContent);
renderReviewEditor(ready);
assert(node("review-register").disabled===true&&node("review-register-status").textContent.includes("검토자 이름"),"register enabled without a name");
node("reviewer-name").value="김재준";node("reviewer-name").dispatch("input");
assert(node("review-register").disabled===false,"register still disabled");
node("review-register").dispatch("click");await tick();await tick();await tick();
const post=calls.find(call=>call.url.endsWith("/review-versions"));
assert(post&&post.method==="POST"&&post.body.reviewer_name==="김재준"&&post.body.target.kind==="review_pending",JSON.stringify(post));
assert(node("review-register-status").textContent.startsWith("검토본 v1을 등록했습니다 · 검토자 김재준"),node("review-register-status").textContent);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_the_versions_are_listed_and_one_shows_what_it_changed(self) -> None:
        result = run_page_script(SETUP + r"""
protocolCatalog.set("p-1",{protocol_id:"p-1",review_versions:[{version:1,version_ko:"검토본 v1",registered_on:"2026-10-14",reviewer_name:"김재준",change_count:1},{version:2,version_ko:"검토본 v2",registered_on:"2026-10-15",reviewer_name:"최수진",change_count:1}]});
serve([["/review-draft/source-pages/1",page1],["/review-versions/2",{version:2,registered_on:"2026-10-15",reviewer_name:"최수진",base:{kind:"review_version",version:1},
 changes:[{item_id:"timer:s2",kind:"timer",kind_ko:"단계 타이머",step_label:"2",action:"fix",original:"10분",reviewed:"15분",origin:"reviewer_input",decided_by:"최수진"}]}],["/review-draft",draft()]]);
await loadReviewDraft("p-1");await tick();
const versions=node("review-history").children.map(row=>row.firstChild.textContent);
assert(JSON.stringify(versions)===JSON.stringify(["검토본 v1 · 2026-10-14 · 김재준 · 바꾼 값 1","검토본 v2 · 2026-10-15 · 최수진 · 바꾼 값 1"]),`versions: ${versions}`);
node("review-history").children[1].firstChild.dispatch("click");await tick();await tick();
const changes=visibleText(node("review-history-changes"));
for(const words of ["v1 → v2 · 2026-10-15 · 최수진","2단계 · 단계 타이머 · 10분 → 15분 · 검토자 입력 · 최수진"])assert(changes.includes(words),`${words}: ${changes}`);
for(const word of ["승인","반려"])assert(!changes.includes(word),`${word} in the history`);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


class CatalogAndPollingTests(unittest.TestCase):

    def test_the_list_says_review_pending_and_the_version_that_runs(self) -> None:
        result = run_page_script(SETUP + r"""
const pending={protocol_id:"p-1",analysis_status:"review_pending",lifecycle_state:"review_pending",available_for_execution:false,review_pending:{needs_review_count:3}};
assert(catalogStatusWord(pending).label.startsWith("검토 대기"),catalogStatusWord(pending).label);
assert(protocolListBadges(pending).map(badge=>`${badge.kind}:${badge.text}`).join("|")==="missing:검토 대기|warning:검토 대기 · 확인 필요 3",protocolListBadges(pending).map(badge=>`${badge.kind}:${badge.text}`).join("|"));
const versioned={protocol_id:"p-2",available_for_execution:true,current_review_version:{version_ko:"검토본 v2"}};
assert(protocolListBadges(versioned).map(badge=>badge.text).join("|")==="분석 통과|검토본 v2",protocolListBadges(versioned).map(badge=>badge.text).join("|"));
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_an_analysis_kept_for_review_ends_the_watch_in_the_servers_words(self) -> None:
        result = run_page_script(SETUP + r"""
currentProtocolReviewId="p-1";
serve([["/analysis/status",{state:"review_pending",worker_running:false,reanalysis:false,failure_code:null,pipeline:{message:"검토 대기 · 확인 필요 2건(원문에 없음 2). 분석은 끝났지만 원문에서 확인하지 못한 항목이 있어 이대로는 실행하지 않습니다."}}]]);
await pollProtocolAnalysisStatus("p-1");
const said=node("protocol-upload-status").textContent;
assert(said.startsWith("검토 대기 · 확인 필요 2건")&&!said.includes("중단")&&!said.includes("통과"),said);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_the_same_file_again_suggests_its_review_version(self) -> None:
        result = run_page_script(SETUP + r"""
const entry={protocol_id:"p-1",source_filename:"gamma.pdf",analysis_status:"review_registered",available_for_execution:true,revision_id:"pdf-1-review-1",current_review_version:{version:1,version_ko:"검토본 v1"}};
const suggestion="이미 올린 원문입니다. 다시 올리지 않고 검토본 v1(2026-10-14 · 김재준)로 바로 시작할 수 있습니다.";
serve([["/api/protocols?filename=",{deduplicated:true,protocol:entry,review_suggestion:{version:1,version_ko:"검토본 v1",message_ko:suggestion}}],
 ["/review-draft",draft()],["/api/protocols/p-1/review",{protocol_id:"p-1",analysis_available:true,available_for_execution:true,review_pending:null}],["/api/protocols",{protocols:[entry]}]]);
node("protocol-pdf").files=[{name:"gamma-again.pdf",type:"application/pdf"}];await registerSelectedProtocol();
const notice=node("protocol-duplicate-notice");
assert(notice.hidden===false&&notice.textContent===suggestion,`${notice.hidden}: ${notice.textContent}`);
assert(!calls.some(call=>call.method==="POST"&&call.url.includes("/analysis")),`the same file again started an analysis: ${JSON.stringify(calls)}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


class ReviewMarkupTests(unittest.TestCase):

    def setUp(self) -> None:
        self.html = INDEX.read_text(encoding="utf-8")

    def test_the_editor_sits_beside_the_read_only_compare_view(self) -> None:
        begin = self.html.index('<details id="protocol-review-editor"')
        editor = self.html[begin:self.html.index("</details>", begin)]
        for ident in ("review-items", "review-source", "review-item", "reviewer-name", "review-register", "review-history"):
            self.assertIn(f'id="{ident}"', editor, ident)
        self.assertIn("검토 완료하고 등록", editor)
        self.assertIn('maxlength="80"', editor)
        for word in ("승인", "반려", "PI"):
            self.assertNotIn(word, editor)
        # The compare view stays read only (lane UI2's own test reads it too).
        compare = self.html[self.html.index('<details id="protocol-compare"'):begin]
        self.assertNotIn("<input", compare)

    def test_one_column_on_a_phone_three_on_a_wide_screen(self) -> None:
        css = self.html[self.html.index(".review-editor-grid{"):]
        self.assertTrue(css.startswith(".review-editor-grid{display:grid;grid-template-columns:minmax(0,1fr);"))
        wide = re.search(r"@media \(min-width:1024px\)\{\s*\.review-editor-grid\{grid-template-columns:18rem minmax\(0,1fr\) minmax\(0,1fr\)", self.html)
        self.assertIsNotNone(wide)

    def test_text_is_put_in_as_text(self) -> None:
        script = self.html.split("// Lane RV (2026-10-14): 검토 -- the compare view a person decides in.", 1)[1].split("async function pollProtocolAnalysisStatus", 1)[0]
        self.assertNotIn("innerHTML", script)
        self.assertNotIn("insertAdjacentHTML", script)


if __name__ == "__main__":
    unittest.main()
