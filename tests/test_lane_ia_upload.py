"""Lane IA, decision ③ (2026-10-19): the upload as one screen at a time (#/upload).

Pick the file (PDF · Word · a photo) → "분석하고 있어요" with the time taken
and what is being done → the result: "바로 쓸 수 있어요" with [이 프로토콜로
실험 시작], or "확인할 것 n개가 있어요" with [확인 시작] to the protocol's
screen and its cards, or "분석하지 못했어요" with the reason and [분석 다시
시도]. The page's own upload path (lane UI3), analysis call, polling and
notices (lanes PA·RN) are kept; the flow is drawn from the catalog's entry
and those notices. Shares the fake page of tests/test_lane_ia_tabs.py.
"""

from __future__ import annotations

import unittest

from tests.test_lane_ia_tabs import SETUP, panel, run
from tests.test_screen_cleanup import INDEX

FLOW = r"""
const REGISTERED=(status,extra={})=>({protocol_id:"p-9",title:"Theta",revision_id:"pdf-9",source_filename:"theta.pdf",source_sha256:"d".repeat(64),available_for_execution:status==="ready",analysis_status:status==="ready"?"review_required":status,lifecycle_state:status==="ready"?"ready":status,step_count:4,created_at:"2026-10-19T02:00:00+00:00",development_only:false,...extra});
let catalogEntry=null,statusAnswer={state:"analyzing",worker_running:true,requested_at:new Date(Date.now()-65000).toISOString()};
function flowAnswers(){return [
 ["/api/protocols/p-9/analysis/status",()=>statusAnswer],
 ["/api/protocols/p-9/analysis",{analysis_request_accepted:true,analysis_run:{state:"analyzing"}}],
 ["/api/protocols/p-9/review",()=>({...REVIEW("p-9"),protocol_id:"p-9",title:"Theta",available_for_execution:catalogEntry?.available_for_execution===true,review_pending:catalogEntry?.review_pending??null})],
 ["/api/protocols/p-9/reagent-tables",{tables:[]}],
 ["/api/protocols?filename=",call=>{catalogEntry=catalogEntry||REGISTERED("structured_analysis_ready");return json({protocol:catalogEntry,deduplicated:false});}],
 ["/api/protocols",()=>({protocols:[...CATALOG.protocols,...(catalogEntry?[catalogEntry]:[])]})],
 ...base().filter(([pattern])=>pattern!=="/api/protocols"&&pattern!=="/api/protocols?")];}
const step=()=>node("upload-flow").dataset.step;
const shownSteps=()=>["pick","progress","result"].filter(name=>node(`upload-step-${name}`).hidden===false);
const resultButtons=()=>walk(node("upload-result-actions")).filter(el=>el.tagName==="button"||el.tagName==="a").map(el=>el.textContent);
async function pickFile(){node("protocol-pdf").files=[{name:"theta.pdf",type:"application/pdf",size:10}];node("protocol-pdf").dispatch("change");await settle(10);}
"""


def run_flow(body: str):
    return run(FLOW + body)


class UploadMarkupTests(unittest.TestCase):
    def test_the_upload_panel_holds_the_three_steps_and_the_file_box(self) -> None:
        html = INDEX.read_text(encoding="utf-8")
        block = panel(html, "upload")
        self.assertIn('<div id="upload-flow" class="upload-flow" data-step="pick">', block)
        order = [block.index(f'id="upload-step-{name}"') for name in ("pick", "progress", "result")]
        self.assertEqual(order, sorted(order))
        pick = block[block.index('id="upload-step-pick"'):block.index('id="upload-step-progress"')]
        for ident in ("protocol-pdf", "protocol-pdf-pick", "selected-file-name", "protocol-upload-status", "protocol-heic-hint", "protocol-duplicate-notice", "protocol-source-formats"):
            self.assertIn(f'id="{ident}"', pick, ident)
        self.assertIn('<label id="protocol-pdf-pick" for="protocol-pdf" class="btn-file-select" role="button" tabindex="0">원문 파일 선택</label>', pick)
        for ident in ("upload-progress-elapsed", "upload-progress-text"):
            self.assertIn(f'id="{ident}"', block[block.index('id="upload-step-progress"'):block.index('id="upload-step-result"')], ident)
        for ident in ("upload-result-title", "upload-result-text", "upload-result-actions"):
            self.assertIn(f'id="{ident}"', block[block.index('id="upload-step-result"'):], ident)
        self.assertIn('<a id="upload-back" class="protocol-detail-back" href="#/protocols">← 프로토콜 목록</a>', block)
        # The list's side offers the upload instead of holding the box.
        protocols = panel(html, "protocols")
        self.assertNotIn('id="protocol-pdf"', protocols)
        self.assertIn('<a id="protocol-upload-link" class="home-card" data-card="upload" href="#/upload">', protocols)
        self.assertIn('id="protocol-queue"', protocols)
        style = html.split("<style>", 1)[1].split("</style>", 1)[0]
        self.assertIn(".upload-step[hidden]{display:none}", style)
        self.assertIn("min-height:var(--target-run)", style[style.index(".upload-actions button,.upload-actions a{"):style.index("}", style.index(".upload-actions button,.upload-actions a{"))])
        for word in ("검토", "실행 가능", "리비전"):
            self.assertNotIn(word, block)


class UploadRouteTests(unittest.TestCase):
    def test_the_upload_route_shows_its_panel_under_the_protocols_link(self) -> None:
        result = run_flow(r"""
await boot(base());
go("#/upload");
assert(tab()==="upload"&&shownPanels().join()==="upload",`panels: ${shownPanels()}`);
assert(node("tab-link-protocols").getAttribute("aria-current")==="page","the protocols link is not current for the upload");
assert(step()==="pick"&&shownSteps().join()==="pick",`step ${step()} shown ${shownSteps()}`);
assert(node("upload-dots").children.length===3&&node("upload-dots").children[0].dataset.state==="current","dots");
""")
        self.assertEqual(result.returncode, 0, result.stderr)


class UploadFlowTests(unittest.TestCase):
    def test_a_file_goes_from_pick_to_the_analysis_to_a_ready_result(self) -> None:
        result = run_flow(r"""
await boot(flowAnswers());
go("#/upload");
await pickFile();
assert(step()==="progress"&&shownSteps().join()==="progress",`after the pick: step ${step()} shown ${shownSteps()}`);
assert(node("upload-dots").children[1].dataset.state==="current","the second dot is not current");
assert(node("upload-progress-elapsed").textContent.includes("걸린 시간"),node("upload-progress-elapsed").textContent);
assert(node("upload-progress-text").textContent.includes("분석 중"),node("upload-progress-text").textContent);
// Leaving and coming back keeps the analysis on screen.
go("#/home");go("#/upload");
assert(step()==="progress","coming back lost the analysis");
// The analysis ends (the server's review_required) and the catalog says the protocol can run.
catalogEntry=REGISTERED("ready");statusAnswer={state:"review_required",worker_running:false,requested_at:statusAnswer.requested_at};
await pollProtocolAnalysisStatus("p-9");await settle(10);
assert(step()==="result"&&shownSteps().join()==="result",`after the pass: step ${step()} shown ${shownSteps()}`);
assert(node("upload-result-title").textContent==="바로 쓸 수 있어요",node("upload-result-title").textContent);
assert(node("upload-result-text").textContent.includes("Theta"),node("upload-result-text").textContent);
assert(JSON.stringify(resultButtons())===JSON.stringify(["이 프로토콜로 실험 시작","프로토콜 화면 보기","다른 파일 올리기"]),`buttons: ${resultButtons()}`);
const start=walk(node("upload-result-actions")).find(el=>el.textContent==="이 프로토콜로 실험 시작");
start.dispatch("click");await settle(8);
assert(wizard.open===true&&["check","safety"].includes(wizard.step)&&node("protocol-id").value==="p-9",`the start did not open the sheet for the new protocol: ${wizard.open} ${wizard.step} ${node("protocol-id").value}`);
closeWizardSheet();
const another=walk(node("upload-result-actions")).find(el=>el.textContent==="다른 파일 올리기");
another.dispatch("click");await settle();
assert(step()==="pick","another file did not return to the pick");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_an_analysis_that_waits_for_a_person_leads_to_the_cards(self) -> None:
        result = run_flow(r"""
await boot(flowAnswers());
go("#/upload");
await pickFile();
catalogEntry=REGISTERED("review_pending",{review_pending:{needs_review_count:2}});statusAnswer={state:"review_pending",worker_running:false,pipeline:{message:"확인 필요 2건"}};
await pollProtocolAnalysisStatus("p-9");await settle(10);
assert(step()==="result","not at the result");
assert(node("upload-result-title").textContent==="확인할 것 2개가 있어요",node("upload-result-title").textContent);
assert(JSON.stringify(resultButtons())===JSON.stringify(["확인 시작","다른 파일 올리기"]),`buttons: ${resultButtons()}`);
const check=walk(node("upload-result-actions")).find(el=>el.textContent==="확인 시작");
assert(check.getAttribute("href")==="#/protocols/p-9",`check link: ${check.getAttribute("href")}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_failed_analysis_says_why_and_offers_another_try(self) -> None:
        result = run_flow(r"""
await boot(flowAnswers());
go("#/upload");
await pickFile();
catalogEntry=REGISTERED("analysis_failed",{analysis_run:{state:"analysis_failed",failure_code:"protocol_analysis_timeout",reanalysis:false,requested_at:statusAnswer.requested_at}});
statusAnswer={state:"analysis_failed",worker_running:false,failure_code:"protocol_analysis_timeout",requested_at:statusAnswer.requested_at};
await pollProtocolAnalysisStatus("p-9");await settle(10);
assert(step()==="result","not at the result");
assert(node("upload-result-title").textContent==="분석하지 못했어요",node("upload-result-title").textContent);
assert(node("upload-result-text").textContent.includes("분석 시간 초과"),node("upload-result-text").textContent);
assert(JSON.stringify(resultButtons())===JSON.stringify(["분석 다시 시도","다른 파일 올리기"]),`buttons: ${resultButtons()}`);
let asked=null;analyzeRegisteredProtocol=async(id,options)=>{asked={id,...options};};
walk(node("upload-result-actions")).find(el=>el.textContent==="분석 다시 시도").dispatch("click");await settle(8);
assert(asked&&asked.id==="p-9"&&!asked.reanalysis,`retry: ${JSON.stringify(asked)}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_fresh_visit_after_a_finished_upload_starts_at_the_pick(self) -> None:
        result = run_flow(r"""
await boot(flowAnswers());
go("#/upload");await pickFile();
catalogEntry=REGISTERED("ready");statusAnswer={state:"review_required",worker_running:false,requested_at:statusAnswer.requested_at};
await pollProtocolAnalysisStatus("p-9");await settle(10);
assert(step()==="result");
go("#/protocols");go("#/upload");
assert(step()==="pick","a finished upload stayed on screen on a fresh visit");
""")
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
