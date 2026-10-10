"""Lane VF, decision 6 (2026-10-10): the check before the start folds once the experiment runs.

In the voice test of 2026-10-10 the "시작 전 확인 · 분석 결과 요약" box and its
"이 프로토콜로 시작" button stayed on the screen through the whole experiment
(the box was drawn once, before the start, and never looked at the state
again), and its analysis and timer lists stretched the page. Now, with an
experiment of the reviewed protocol running by the server's state, the box
folds to one line -- "시작 전 확인 완료 · 11:30 · 다시 보기" -- and the start
button is gone; "다시 보기" opens the check again; the long boxes scroll
inside a fixed height. Everything here reads the server's state; nothing
changes it.
"""

from __future__ import annotations

import unittest

from tests.test_screen_cleanup import run_page_script

SETUP = r"""
const protocol_id="protocol-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",revision_id="pdf-1-analysis-1",configuration_id=71;
const review={protocol_id,title:"In-gel",revision_id,lifecycle_state:"ready",readiness_status:"guidance_ready",available_for_execution:true,analysis_available:true,step_count:9,
 source:{filename:"in-gel.pdf",sha256:"b".repeat(64),page_count:9},readiness:{status:"guidance_ready",label:"ready",reasons:[]},
 execution_blockers:[],execution_notices:[],safety_notices:[{step_label:"4",step_id:"step-4",source_page_number:3,source_text:"Wear gloves.",primary_text:"장갑을 끼세요."}],
 timers:{verified:[{step_label:"3",value_ko:"15분",bound_ko:"",source_literal:"15min",source_page_number:2,source_excerpt:"15min"}],refused:[]},sections:[]};
protocolCatalog.set(protocol_id,{protocol_id,title:"In-gel",revision_id,available_for_execution:true,development_only:false});
renderProtocolReview(review);
const summary=node("protocol-start-summary"),done=node("protocol-start-done"),doneText=node("protocol-start-done-text"),body=node("protocol-start-body"),start=node("protocol-start"),reopen=node("protocol-start-review");
const started=new Date(Date.now()-5*60*1000),hhmm=`${String(started.getHours()).padStart(2,"0")}:${String(started.getMinutes()).padStart(2,"0")}`;
const state={attached:true,protocol_id,revision_id,display_name:"In-gel",development_only:false,readiness_status:"guidance_ready",active:true,current_step_label:"3",current_step_id:"step-3",total_steps:9,at_final_step:false,block_reason:null,revision:1,primary_summary:"15분 둡니다.",display_summary:"3. Leave for 15 min.",spoken_summary:"3단계입니다.",source_filename:"in-gel.pdf",source_sha256:"b".repeat(64),source_page_refs:[2],visual_assets:[],visual_status:"unavailable",warning_texts:[],critical_warning_texts:[],timers:{experiment:{state:"running",started_at:started.toISOString(),elapsed_seconds:300},step:{state:"not_started",duration_seconds:900,remaining_seconds:900,deadline_at:null}}};
"""


class StartSummaryFoldTests(unittest.TestCase):

    def test_it_folds_to_one_line_while_the_experiment_runs_and_opens_on_request(self) -> None:
        result = run_page_script(SETUP + r"""
// Before the start: the whole check and the start button.
assert(summary.hidden===false&&done.hidden===true&&body.hidden===false,"the check is not open before the start");
assert(start.hidden===false,"the start button is missing before the start");
// The experimenter pressed start; the server's state says the experiment runs.
acceptedSessionConfiguration={configuration_id,mode:"cascade",language:"ko",protocol_id,revision_id};sessionActive=true;
await onMessage({data:JSON.stringify({type:"protocol.fixture.state",configuration_id,turn_id:1,generation:5,state,action:"current"})},sessionGeneration,socket);
assert(done.hidden===false,"the one-line check is not shown while the experiment runs");
assert(doneText.textContent===`시작 전 확인 완료 · ${hhmm}`,`the folded line: ${doneText.textContent}`);
assert(reopen.textContent==="다시 보기",`the reopen button: ${reopen.textContent}`);
assert(body.hidden===true,"the long check is still open while the experiment runs");
assert(start.hidden===true,"the start button is still on the screen while the experiment runs");
// "다시 보기" opens it; "접기" folds it again.
reopen.dispatch("click");
assert(body.hidden===false&&reopen.textContent==="접기","다시 보기 did not open the check");
assert(start.hidden===true,"the start button came back with the check open");
reopen.dispatch("click");
assert(body.hidden===true&&reopen.textContent==="다시 보기","접기 did not fold the check");
// The review drawn again mid-experiment (a refresh) keeps the fold.
renderProtocolReview(review);
assert(done.hidden===false&&body.hidden===true&&start.hidden===true,"a redraw lost the fold");
// The session ends: the check opens and the start button is back.
await onMessage({data:JSON.stringify({type:"session.stopped"})},sessionGeneration,socket);
assert(done.hidden===true&&body.hidden===false,"the check did not open after the session ended");
assert(start.hidden===false,"the start button did not come back after the session ended");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_a_saved_experiment_of_the_same_revision_hides_the_start_button(self) -> None:
        result = run_page_script(SETUP + r"""
currentExperimentSessionId="exp-1";currentExperimentSessionVersion=3;currentExperimentSessionStatus="paused";currentExperimentProtocolRevision=revision_id;
renderExperimentContext();
assert(start.hidden===true,"the start button is shown beside a saved experiment of this revision");
assert(done.hidden===false&&doneText.textContent==="시작 전 확인 완료",`folded without a time: ${doneText.textContent}`);
// Another revision's saved experiment does not fold this one.
currentExperimentProtocolRevision="pdf-9-analysis-9";renderExperimentContext();
assert(start.hidden===false&&done.hidden===true,"another revision's experiment folded this check");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_the_long_boxes_scroll_inside_a_fixed_height(self) -> None:
        html = (__import__("pathlib").Path(__file__).resolve().parents[1] / "src/voiney_lab/static/index.html").read_text(encoding="utf-8")
        self.assertIn(".protocol-start-summary .protocol-safety-notices,.protocol-start-summary .protocol-execution-reasons{max-height:14rem;overflow:auto}", html)


if __name__ == "__main__":
    unittest.main()
