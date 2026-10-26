"""Lane HEAR2, decision 3 (2026-10-26): the current screen shows a timer paused by voice.

Through the production page script: a step timer the server reports as
``paused`` shows its remaining time standing still with "일시정지"; the
timer commands' operations are named and draw no card; the record's new
events have their Korean names.
"""

from __future__ import annotations

import unittest

from tests.test_screen_cleanup import run_page_script

SETUP = r"""
const configuration_id=71;
acceptedSessionConfiguration={configuration_id,mode:"cascade",language:"ko",server_generation:3,protocol_id:"protocol-x",revision_id:"pdf-1-analysis-1"};sessionActive=true;socket=new WS();
"""


class PausedTimerPageTests(unittest.TestCase):
    def test_a_paused_timer_stands_still_with_the_word(self) -> None:
        result = run_page_script(SETUP + r"""
curatedProtocolState={attached:true,active:true,protocol_id:"protocol-x",current_step_id:"step-2",timers:{step:{state:"paused",duration_seconds:600,remaining_seconds:540,deadline_at:null,paused_at:"2026-10-26T03:13:40+00:00",name:"10분",step_id:"step-2"}}};
const kind=renderRunTimer();
assert(kind==="paused",`kind: ${kind}`);
assert(node("run-timer-digits").textContent==="9:00",`digits: ${node("run-timer-digits").textContent}`);
assert(node("run-timer-foot").textContent.includes("일시정지"),`foot: ${node("run-timer-foot").textContent}`);
assert(node("run-timer").dataset.timer==="paused","the panel's state");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_the_timer_commands_are_named_and_draw_no_card(self) -> None:
        result = run_page_script(SETUP + r"""
for(const op of ["step_timer_paused","step_timer_resumed","step_timer_cancelled","step_timer_restarted"]){
 assert(typeof SERVER_OPERATION_LABELS[op]==="string"&&SERVER_OPERATION_LABELS[op],`no label for ${op}`);
 assert(runCardKind(op)===null,`${op} draws a card: ${runCardKind(op)}`);
}
assert(serverCodeText("experiment_report_event","timer_paused","이벤트")==="타이머 일시정지","paused name");
assert(serverCodeText("experiment_report_event","timer_resumed","이벤트")==="타이머 다시 시작","resumed name");
assert(serverCodeText("experiment_report_event","timer_cancelled","이벤트")==="타이머 취소","cancelled name");
assert(serverCodeText("experiment_report_event","timer_restarted","이벤트")==="타이머 처음부터 다시","restarted name");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
