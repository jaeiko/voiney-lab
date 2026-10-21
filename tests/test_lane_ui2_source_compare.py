"""Lane UI2, decision 3 (2026-10-13): 원문 대조 보기, the draft's P2 read only.

The long "올린 PDF 분석 결과" list moved here. The left column lists the steps
the analysis took from the source; the middle the source's own lines the
picked step was read from, page by page, its own line marked (the excerpts
the server keeps as evidence; a runnable revision also offers its page
image); the right what the analysis took from each part -- amounts and
conditions, timers, warnings, values the source does not give -- with the
page. Above it a strip of badges; under it the rest of the analysis result.
Nothing in it can be changed and picking a step asks nothing of the server.
The picked protocol's box by the list keeps the analysis line and buttons,
always open.
"""

from __future__ import annotations

import unittest

from tests.test_screen_cleanup import INDEX, run_page_script

SETUP = r"""
const ev=(page,text,extra={})=>({source_page_number:page,source_excerpt:text,...extra});
const review=(extra={})=>({protocol_id:"p-1",title:"In-gel digestion",revision_id:"pdf-1-analysis-2",available_for_execution:true,development_only:false,analysis_available:true,lifecycle_state:"ready",analysis_status:"review_required",created_at:"2026-10-05T01:00:00+00:00",
 source:{filename:"in-gel.pdf",sha256:"a".repeat(64),page_count:9},readiness:{status:"guidance_ready",reasons:[]},constructs:[],
 pipeline:{stage:"ready",blocked:false,message:"실행할 수 있습니다."},
 execution_blockers:[],execution_notices:[{code:"unresolved_ambiguity",kind:"source_note",message_ko:"원문에 서로 다른 두 서술이 있습니다."}],safety_notices:[],
 cleared_fields:[{field:"metadata.authors",label_ko:"저자"}],analysis_outdated_ko:null,
 sections:[{section_id:"s-1",title_source_text:"Destaining",steps:[
  {step_id:"step-1",source_label:"1",instruction_source_text:"Cut the band into 1 mm pieces.",evidence:ev(3,"Cut the band into 1 mm pieces."),sub_actions:[{action_id:"a-1",instruction_source_text:"Cut the band",evidence:ev(3,"Cut the band"),quantities:[{source_text:"1 mm"}],warnings:[],missing_execution_values:[]}],warnings:[]},
  {step_id:"step-2",source_label:"2",instruction_source_text:"Add 100 µL buffer and incubate for 15 min.",evidence:ev(4,"Add 100 µL buffer and incubate for 15 min.",{continued_on_page_number:5,continued_excerpt:"at 37 °C."}),sub_actions:[
   {action_id:"a-2",instruction_source_text:"Add buffer",evidence:ev(4,"Add 100 µL buffer"),quantities:[{source_text:"100 µL"}],conditions:[{source_text:"until clear",evidence:ev(4,"until clear")}],warnings:[{source_text:"Wear gloves.",evidence:ev(4,"Wear gloves.")}],missing_execution_values:[]},
   {action_id:"a-3",instruction_source_text:"Incubate",evidence:ev(4,"incubate for 15 min"),quantities:[],process_timer:{timer_id:"t-1",duration:{source_text:"15 min"},evidence:ev(4,"for 15 min")},warnings:[],missing_execution_values:[{value_id:"m-1",description:"incubation temperature",evidence:ev(6,"Incubate at the usual temperature.")}]}],warnings:[]},
 ]}],...extra});
const text=id=>visibleText(node(id));
const steps=()=>node("protocol-compare-steps").children.map(item=>item.firstChild).filter(Boolean);
"""


class CompareColumnsTests(unittest.TestCase):

    def test_steps_source_lines_and_what_was_taken_side_by_side(self) -> None:
        result = run_page_script(SETUP + r"""
renderProtocolReview(review(),{open:false});
assert(steps().length===2,`steps: ${steps().length}`);
assert(steps()[0].attributes["aria-current"]==="true"&&visibleText(steps()[0]).includes("1단계")&&visibleText(steps()[0]).includes("Cut the band into 1 mm pieces."),visibleText(steps()[0]));
assert(visibleText(steps()[1]).includes("원문에 없음 1")&&visibleText(steps()[1]).includes("주의 1")&&visibleText(steps()[1]).includes("타이머"),visibleText(steps()[1]));
assert(text("protocol-compare-source").includes("원문 p.3")&&text("protocol-compare-source").includes("Cut the band into 1 mm pieces."),text("protocol-compare-source"));
steps()[1].dispatch("click");
assert(steps()[1].attributes["aria-current"]==="true"&&steps()[0].attributes["aria-current"]==="false","picking a step did not mark it");
const source=node("protocol-compare-source");
const pages=source.children.map(block=>block.children[0].children[0].textContent);
assert(JSON.stringify(pages)===JSON.stringify(["원문 p.4","원문 p.6"]),`pages: ${pages}`);
const lines=source.children[0].children.slice(1);
assert(lines[0].className==="compare-line is-linked"&&lines[0].textContent==="Add 100 µL buffer and incubate for 15 min.",`the step's own line is not first and marked: ${lines[0].className} ${lines[0].textContent}`);
assert(lines[1].className==="compare-line-next"&&lines[1].textContent==="p.5 에서 이어짐 · at 37 °C.",`continued page: ${lines[1].textContent}`);
assert(lines.filter(line=>line.className==="compare-line").map(line=>line.textContent).join("|")==="Add 100 µL buffer|until clear|Wear gloves.|incubate for 15 min|for 15 min",lines.map(line=>line.textContent).join("|"));
const fields=text("protocol-compare-fields");
for(const words of ["2단계","Destaining","동작","Add buffer","수량·조건","100 µL","조건","until clear","주의","Wear gloves.","타이머","15 min","원문에 없는 값","incubation temperature","원문에 없음","원문 p.4","원문 p.6"])assert(fields.includes(words),`${words} missing: ${fields}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_a_runnable_revision_offers_its_page_image_and_others_do_not(self) -> None:
        result = run_page_script(SETUP + r"""
renderProtocolReview(review());
const link=node("protocol-compare-source").children[0].children[0].children[1];
assert(link?.tagName==="a"&&link.href==="/api/protocols/p-1/revisions/pdf-1-analysis-2/source-pages/3/image"&&link.target==="_blank"&&link.rel==="noopener noreferrer",`link: ${link?.href}`);
renderProtocolReview(review({available_for_execution:false,lifecycle_state:"blocked"}));
assert(node("protocol-compare-source").children[0].children[0].children.length===1,"a revision that cannot run offered a page image");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_the_strip_and_the_rest_of_the_result(self) -> None:
        result = run_page_script(SETUP + r"""
renderProtocolReview(review({analysis_outdated_ko:"분석기 개선 전 분석 · 다시 분석 권장",execution_blockers:[{code:"no_executable_steps",kind:"blocking",message_ko:"실행할 단계를 원문에서 찾지 못했습니다."}]}));
const strip=node("protocol-compare-strip").children.map(badge=>`${badge.dataset.kind}:${badge.textContent}`);
assert(JSON.stringify(strip)===JSON.stringify(["done:in-gel.pdf · 2026년 10월 5일 올림 · 분석 통과","missing:원문에 없는 값 1건","missing:비운 칸 1건","quiet:시작 전 알림 1건","danger:실행을 막는 사유 1건","warning:분석기 개선 전 분석 · 다시 분석 권장"]),`strip: ${strip}`);
assert(node("protocol-compare-summary").textContent===" · 단계 2개 · 원문 9쪽",node("protocol-compare-summary").textContent);
const rest=text("protocol-review-content");
assert(rest.includes("진행 상태")&&rest.includes("원문 파일")&&rest.includes("9쪽")&&rest.includes("실행 준비 판정"),rest);
assert(!rest.includes("Destaining")&&!rest.includes("Cut the band"),"the steps are still in the long list");
for(const word of ["승인","검토자","임시 저장"])assert(!text("protocol-compare-strip").includes(word)&&!text("protocol-compare-fields").includes(word),`${word} on the compare view`);
renderProtocolReview(review({sections:[],analysis_available:false}));
assert(steps().length===0&&text("protocol-compare-steps").includes("분석이 끝나면")&&node("protocol-compare-source").children.length===0&&node("protocol-compare-fields").children.length===0,"an unanalysed protocol shows steps");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


class CompareStateTests(unittest.TestCase):

    def test_open_when_it_cannot_run_folded_when_it_can_and_hidden_with_no_pick(self) -> None:
        result = run_page_script(SETUP + r"""
const compare=node("protocol-compare");compare.hidden=true;
renderProtocolReview(review({available_for_execution:false,lifecycle_state:"blocked"}),{open:true});
assert(compare.hidden===false&&compare.open===true,"a protocol that cannot run folded its compare view");
renderProtocolReview(review(),{open:false});
assert(compare.hidden===false&&compare.open===false,"a runnable protocol opened its compare view");
node("protocol-id").value="";onProtocolSelectionChanged();
assert(compare.hidden===true&&node("protocol-review-panel").hidden===true,"clearing the pick left the compare view");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_picking_a_step_asks_nothing_of_the_server(self) -> None:
        result = run_page_script(SETUP + r"""
const calls=[];globalThis.fetch=async url=>{calls.push(String(url));throw new Error("no call expected");};
renderProtocolReview(review());
// Lane UI3 decision 4 (2026-10-14): drawing the picked protocol reads its reagent tables, once.
assert(calls.join("|")==="/api/protocols/p-1/reagent-tables",`calls: ${calls}`);
calls.length=0;
for(const step of steps())step.dispatch("click");
assert(calls.length===0,`calls: ${calls}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


class CompareMarkupTests(unittest.TestCase):

    def setUp(self) -> None:
        self.html = INDEX.read_text(encoding="utf-8")

    def test_the_compare_view_is_read_only_and_holds_the_long_list(self) -> None:
        begin = self.html.index('<details id="protocol-compare"')
        view = self.html[begin:self.html.index("</details>", begin)]
        for ident in ("protocol-compare-steps", "protocol-compare-source", "protocol-compare-fields", "protocol-review-content", "protocol-blockers"):
            self.assertIn(f'id="{ident}"', view, ident)
        for tag in ("<input", "<textarea", "<select", "contenteditable"):
            self.assertNotIn(tag, view)
        script = self.html.split("function renderProtocolCompare", 1)[1].split("async function loadProtocolReview", 1)[0]
        for word in ('createElement("input")', 'createElement("textarea")', "fetch(", "innerHTML"):
            self.assertNotIn(word, script)

    def test_the_picked_box_is_always_open_with_its_line_and_buttons(self) -> None:
        begin = self.html.index('<section id="protocol-review-panel"')
        box = self.html[begin:self.html.index("</section>", begin)]
        for ident in ("protocol-review-status", "protocol-review-analysis-notice", "protocol-review-refresh", "protocol-ocr-run", "protocol-analysis-retry"):
            self.assertIn(f'id="{ident}"', box, ident)
        self.assertNotIn("<details", box)
        self.assertNotIn('id="protocol-review-content"', box)
        # Lane IA decision ③: the picked box is on the protocol's own screen (#/protocols/<id>), after the list view.
        detail = self.html[self.html.index('<div id="protocol-detail-view"'):self.html.index('<section id="tab-experiment"')]
        self.assertIn('id="protocol-review-panel"', detail)


if __name__ == "__main__":
    unittest.main()
