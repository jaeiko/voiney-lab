"""The screen knows an analysis kept for a person's review (lane RV, stage ②).

Stage ① (#81) keeps an analysis whose items the source does not confirm as
one waiting for review ("review_pending"), registers review versions, and
suggests a review version when the same file comes again. The list says
"검토 대기" and which review version runs, the watch over an analysis ends
on it in the server's words (not "중단"), and the upload says the
suggestion. The review screen itself follows in the next commit.
"""

from __future__ import annotations

import unittest

from tests.test_screen_cleanup import run_page_script

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


if __name__ == "__main__":
    unittest.main()
