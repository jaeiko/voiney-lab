"""Lane PRE, decision 2 (human decision 2026-10-24): a runnable protocol never looks unready.

Lane PERSONA (F5): an analysis that passed with notices is runnable
(``available_for_execution`` true, no blocker) while its readiness code stays
``analysis_required`` -- the readiness status says "notices to read before
the start", not "not analysed". Other fields can look unfinished too: a
second analysis runs over a passed one (``analysis_run.state`` analyzing). The
screen reads ``available_for_execution`` first and says so everywhere a
protocol's state is drawn; the codes themselves are not renamed (they are
stored under a CHECK constraint and carried by the session's events).

Every test loads the whole page script into Node with the fake DOM and
drives the production functions (tests/test_screen_cleanup.run_page_script).
"""

from __future__ import annotations

import unittest

from tests.test_lane_ia_tabs import SETUP as TABS_SETUP
from tests.test_screen_cleanup import run_page_script

SETUP = TABS_SETUP + r"""
// Runnable, with a notice, while a second analysis runs over the passed one.
const AGAIN={state:"analyzing",reanalysis:true,failure_code:null,requested_at:"2026-10-24T01:00:00+00:00"};
const RUNNABLE=ENTRY("p-1","Gamma",true,{readiness_status:"analysis_required",analysis_status:"structured_analysis_ready",analysis_run:AGAIN});
const UNREADY=/분석 필요|분석 전|분석 대기|문서 처리 중|구조화 분석 요청 대기|(^|[^시])분석 중|실행 불가/;
async function bootWith(entry){serve(base(DEV_SESSION,SETTINGS(),VOICES,{protocols:[entry]}));await loadProtocolCatalog();await openStartWizard();renderRoute();await settle();}
"""


def run(body: str):
    return run_page_script(SETUP + body)


class RunnableFirstTests(unittest.TestCase):
    def test_the_upload_result_says_ready_while_a_second_analysis_runs(self):
        result = run(r"""
await bootWith(RUNNABLE);
uploadFlow.protocolId="p-1";
assert(uploadFlowStep()==="result",`a runnable protocol shows as still being analysed: ${uploadFlowStep()}`);
renderUploadFlow();
assert(node("upload-result-title").textContent==="바로 쓸 수 있어요",node("upload-result-title").textContent);
assert(node("upload-step-progress").hidden===true,"the progress step is still shown");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_review_states_runnable_before_the_notices(self):
        result = run(r"""
const review={...REVIEW("p-1"),readiness_status:"analysis_required",readiness:{status:"analysis_required",label:"Protocol 분석 필요",reasons:[{code:"unresolved_ambiguity",message:"A source ambiguity remains unresolved.",message_ko:"원문에 서로 다른 두 서술이 있습니다."}]}};
renderProtocolReview(review);
const shown=visibleText(node("protocol-review-content"));
assert(shown.includes("상태 · 실행 가능 · 시작 전에 읽을 알림 있음"),`the state does not say runnable first: ${shown}`);
assert(!/분석 필요|분석 전/.test(shown),`a runnable protocol reads as unanalysed: ${shown}`);
assert(devText(node("protocol-review-content")).includes("analysis_required"),"the server's code left 개발 상세 정보");
// Not runnable: the state is the code's word, as before.
renderProtocolReview({...review,available_for_execution:false,lifecycle_state:"blocked"});
const blocked=visibleText(node("protocol-review-content"));
assert(blocked.includes("상태 · 시작 전에 읽을 알림 있음")&&!blocked.includes("상태 · 실행 가능"),blocked);
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_no_list_card_or_sheet_calls_a_runnable_protocol_unready(self):
        result = run(r"""
for(const entry of [RUNNABLE,{...RUNNABLE,lifecycle_state:"analyzing"},{...RUNNABLE,analysis_status:"chunk_analysis_in_progress",analysis_run:{...AGAIN,reanalysis:false}}]){
 await bootWith(entry);
 const words=[
  ["badges",protocolListBadges(entry).map(badge=>badge.text).join(" · ")],
  ["catalog",catalogStatus(entry).label],
  ["context",analysisStateLabel(entry)],
  ["list",node("protocol-list").textContent],
  ["count",node("protocol-library-count").textContent],
 ];
 go("#/protocols/p-1");await settle(12);
 words.push(["detail",node("protocol-detail-badges").textContent]);
 openWizardSheet("protocol");await settle(12);
 const pick=walk(body()).find(el=>el.className==="wizard-option-pick"&&el.dataset.protocolId==="p-1");
 words.push(["sheet",pick?.textContent||""]);
 assert(pick&&pick.disabled===false,"the sheet does not offer the runnable protocol");
 for(const [where,text] of words){
  assert(!UNREADY.test(text.replace(/다시 분석 중/g,"")),`${where} calls a runnable protocol unready: ${text}`);
 }
 assert(words.find(([where])=>where==="badges")[1].startsWith("바로 쓸 수 있음"),words[0][1]);
 assert(words.find(([where])=>where==="count")[1].includes("바로 쓸 수 있는 것 1개"),"the count leaves it out");
 closeWizardSheet();go("#/home");await settle();
}
""")
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
