"""Lane VF, decision 4 (2026-10-10): a timer that has run out is shown as over, not counting up.

In the voice test of 2026-10-10 the step timer reached 0:00 and the badge at
the top then showed a clock running up (15:00, 15:01, ...). The page had no
drawing for the server's "expired" state: it dropped the step timer and the
badge fell back to the experiment's elapsed time. Now the server's state is
drawn as it is -- "끝남 · 2:13 지남" (the time gone past the end, from the
server's deadline) -- in the top badge and the timer row alike, and the clock
keeps counting how long ago it ended. Nothing here changes state.
"""

from __future__ import annotations

import unittest

from tests.test_screen_cleanup import run_page_script

STATE = r"""
const protocol_id="protocol-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",revision_id="pdf-1-analysis-1",configuration_id=71;
acceptedSessionConfiguration={configuration_id,mode:"cascade",language:"ko",protocol_id,revision_id};sessionActive=true;
const base={attached:true,protocol_id,revision_id,display_name:"Protocol Alpha",development_only:false,readiness_status:"guidance_ready",active:true,current_step_label:"3",current_step_id:"step-3",total_steps:9,at_final_step:false,block_reason:null,revision:1,primary_summary:"15분 둡니다.",display_summary:"3. Leave for 15 min.",spoken_summary:"3단계입니다.",source_filename:"alpha.pdf",source_sha256:"b".repeat(64),source_page_refs:[2],visual_assets:[],visual_status:"unavailable",warning_texts:[],critical_warning_texts:[]};
const iso=offsetSeconds=>new Date(Date.now()+offsetSeconds*1000).toISOString();
function stateWith(revision,step){return {...base,revision,timers:{experiment:{state:"running",started_at:iso(-1032),elapsed_seconds:1032},step}};}
async function show(revision,step){await onMessage({data:JSON.stringify({type:"protocol.fixture.state",configuration_id,turn_id:revision,generation:5,state:stateWith(revision,step),action:"current"})},sessionGeneration,socket);}
const row=()=>node("procedure-timer").textContent,badge=()=>node("rail-timer-badge").textContent;
"""


class ExpiredTimerTests(unittest.TestCase):

    def test_a_timer_that_ran_out_is_shown_as_over_in_the_badge_and_the_row(self) -> None:
        result = run_page_script(STATE + r"""
await show(1,{state:"running",duration_seconds:900,remaining_seconds:767,elapsed_seconds:133,step_index:2,step_id:"step-3",step_label:"3",deadline_at:iso(767),started_at:iso(-133)});
assert(row().includes("단계 12:47 남음")||row().includes("단계 12:46 남음"),`running row: ${row()}`);
assert(badge().startsWith("⏱ 12:4")&&badge().endsWith("남음"),`running badge: ${badge()}`);
await show(2,{state:"expired",duration_seconds:900,remaining_seconds:0,elapsed_seconds:1033,step_index:2,step_id:"step-3",step_label:"3",deadline_at:iso(-133),started_at:iso(-1033)});
assert(row().includes("단계 15:00 타이머 끝남 · 2:13 지남")||row().includes("단계 15:00 타이머 끝남 · 2:14 지남"),`expired row: ${row()}`);
assert(badge()==="⏱ 끝남 · 2:13 지남"||badge()==="⏱ 끝남 · 2:14 지남",`expired badge: ${badge()}`);
assert(!badge().includes("17:12")&&!row().includes("남음"),`the badge fell back to the experiment clock: ${badge()} / ${row()}`);
assert(workflowTimerHandle!==null,"the clock stopped counting how long ago the timer ended");
// Said to have just ended: no negative or counting-up clock either.
await show(3,{state:"expired",duration_seconds:900,remaining_seconds:0,elapsed_seconds:900,step_index:2,step_id:"step-3",step_label:"3",deadline_at:iso(0),started_at:iso(-900)});
assert(badge()==="⏱ 끝남 · 0:00 지남"||badge()==="⏱ 끝남 · 0:01 지남",`just expired badge: ${badge()}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_a_timer_still_to_start_and_the_experiment_clock_are_as_before(self) -> None:
        result = run_page_script(STATE + r"""
await show(1,{state:"not_started",duration_seconds:900,remaining_seconds:900,step_index:2,step_id:"step-3",step_label:"3",deadline_at:null});
assert(row().includes("단계 타이머 15:00 · 시작 전"),`not started row: ${row()}`);
assert(row().includes("실험 진행 경과 시간 17:12")||row().includes("실험 진행 경과 시간 17:13"),`experiment clock: ${row()}`);
assert(badge().startsWith("⏱ 17:1"),`badge without a step timer: ${badge()}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
