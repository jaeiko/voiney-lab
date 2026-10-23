"""Lane UI4 (2026-10-23): lane QA's QA-15 -- the key-value check (GET
/api/protocols/{id}/start-check) that failed to load was skipped without a word (wizardLoadCheck
turned the failure into "skipped" and moved to the safety step); it is now said where it is
asked -- the server's sentence when it sent one -- with [다시 불러오기]. Since lane SAFE the
server refuses a start whose check was not finished, so the failure is not skipped either.

Shares tests/test_lane_ui4_start.py's harness.
"""

from __future__ import annotations

import unittest

from tests.test_lane_ui4_start import run_start


class StartCheckLoadTests(unittest.TestCase):
    """QA-15: a key-value check that failed to load is said where it is asked."""

    def test_a_failed_load_stays_at_the_check_with_a_retry(self) -> None:
        result = run_start(r"""
let checkFails=true;
await boot([["/start-check",()=>checkFails?answer(500,{detail:"unexpected_server_error"}):json(CHECK)],...base()]);
go("#/experiment");
pickProtocolFromList("p-1");await settle(8);
openStartForProtocol("p-1");await settle(10);
assert(wizard.step==="check",`a failed check was skipped to: ${wizard.step}`);
assert(title()==="핵심 값을 불러오지 못했어요",`title: ${title()}`);
assert(node("wizard-lead").textContent.includes("다시 불러오기"),`lead: ${node("wizard-lead").textContent}`);
assert(primary().hidden===false&&primary().textContent==="다시 불러오기",`primary: ${primary().hidden} ${primary().textContent}`);
checkFails=false;
primary().dispatch("click");await settle(10);
assert(wizard.step==="check"&&wizardQuestion()?.question_id==="q1:step:s2:10min",`the retry did not ask the questions: ${wizard.step} ${JSON.stringify(wizardQuestion())}`);
assert(primary().hidden===true,"the questions show the primary button");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_servers_sentence_and_a_lost_network_are_said(self) -> None:
        result = run_start(r"""
let mode="refused";
await boot([["/start-check",()=>{if(mode==="offline")throw new Error("offline");return answer(503,{detail:"start_check_unavailable",message_ko:"핵심 값 확인을 지금 할 수 없어요."});}],...base()]);
go("#/experiment");
pickProtocolFromList("p-1");await settle(8);
openStartForProtocol("p-1");await settle(10);
assert(wizard.step==="check"&&node("wizard-lead").textContent.includes("핵심 값 확인을 지금 할 수 없어요."),`refused: ${wizard.step} ${node("wizard-lead").textContent}`);
mode="offline";
primary().dispatch("click");await settle(10);
assert(wizard.step==="check"&&node("wizard-lead").textContent.includes("서버에 연결할 수 없어요"),`offline: ${wizard.step} ${node("wizard-lead").textContent}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_check_with_nothing_to_ask_still_moves_on(self) -> None:
        result = run_start(r"""
await boot([["/start-check",{enabled:true,questions:[],skipped_reason:"no_numeric_values"}],...base()]);
go("#/experiment");
pickProtocolFromList("p-1");await settle(8);
openStartForProtocol("p-1");await settle(10);
assert(wizard.step==="safety",`step: ${wizard.step}`);
assert(walk(body()).some(el=>el.textContent.includes("확인할 숫자 값이 없어")),"the skip is not said");
""")
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
