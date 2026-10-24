"""Lane UI4 (2026-10-23), the sheet's start before the pilot: lane QA's QA-14.

QA-14: the sheet's last [이 프로토콜로 시작] closed the sheet and moved to the experiment tab
whatever happened -- the page's start returned early, saying nothing, when the protocol's review
was not on the screen yet (the home's "이어서 하기" opened the sheet before it arrived, or it
failed to load), when an upload was still going, or when an upload that finished in the meantime
had put its own protocol's review on the screen. The start now reads the sheet's protocol's
review when it is not the one on the screen, and a start that does not go keeps the sheet open
with the reason; the experiment tab's card does the same through the sheet.

Shares tests/test_lane_ia_stage3.py's harness (the ON wizard with the tab router).
"""

from __future__ import annotations

import unittest

from tests.test_lane_ia_stage3 import STAGE3
from tests.test_lane_ia_tabs import run

UI4_START = r"""
let sessions=0;startSession=async()=>{sessions+=1;};
const answer=(status,payload)=>{const reply={ok:status<400,status,headers:{get:()=>"application/json"},json:async()=>payload,clone:()=>reply};return reply;};
const note=()=>node("wizard-note").textContent;
"""


def run_start(body: str):
    return run(STAGE3 + UI4_START + body)


class SheetStartTests(unittest.TestCase):
    """QA-14: a start that does not go keeps the sheet open and says why."""

    def test_a_review_not_on_the_screen_is_read_before_the_start_and_a_failed_one_is_said(self) -> None:
        result = run_start(r"""
let reviewFails=true;
await boot([["/review",()=>reviewFails?answer(500,{detail:"unexpected_server_error"}):json(REVIEW())],...base()]);
go("#/home");
pickProtocolFromList("p-1");await settle(8);
assert(lastProtocolReview===null,"the failing review reached the screen");
openWizardSheet("start");await settle(4);
assert(wizard.step==="start"&&wizardOn(),`not at the start: ${wizard.step}`);
primary().dispatch("click");await settle(10);
assert(wizard.open===true&&wizardOn(),"the sheet closed on a start that did not go");
assert(sessions===0,"a session started without the protocol's review");
assert(tab()==="home",`moved to the ${tab()} tab`);
assert(note().includes("분석 결과를 불러오지 못했어요")&&wizard.noteKind==="error",`note: ${note()}`);
// The review reads now: the same press starts and the sheet gives way to the run's tab.
reviewFails=false;
primary().dispatch("click");await settle(10);
assert(sessions===1,`sessions: ${sessions}`);
assert(wizard.open===false&&tab()==="experiment",`after the start: ${wizard.open} ${tab()}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_an_upload_in_flight_keeps_the_sheet_open(self) -> None:
        result = run_start(r"""
await boot(base());
go("#/home");
pickProtocolFromList("p-1");await settle(8);
openWizardSheet("start");await settle(4);
isRegisteringProtocol=true;
primary().dispatch("click");await settle(8);
assert(wizard.open===true&&sessions===0,`closed or started during an upload: ${wizard.open} ${sessions}`);
assert(note().includes("올리는 중"),`note: ${note()}`);
isRegisteringProtocol=false;
primary().dispatch("click");await settle(8);
assert(sessions===1&&wizard.open===false,`after the upload: ${sessions} ${wizard.open}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_start_follows_the_sheets_protocol_after_an_upload_put_another_review_on_the_screen(self) -> None:
        result = run_start(r"""
await boot(base());
go("#/home");
pickProtocolFromList("p-1");await settle(8);
openWizardSheet("start");await settle(4);
// An upload that finished meanwhile shows its own protocol (lane IA's acceptRegisteredProtocol).
currentProtocolReviewId="p-2";lastProtocolReview={...REVIEW("p-2"),available_for_execution:false};
primary().dispatch("click");await settle(10);
assert(sessions===1,`the sheet's protocol did not start: ${sessions} (${note()})`);
assert(node("protocol-id").value==="p-1"&&currentProtocolReviewId==="p-1",`started: ${node("protocol-id").value} / ${currentProtocolReviewId}`);
assert(wizard.open===false&&tab()==="experiment",`${wizard.open} ${tab()}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_tabs_card_says_why_through_the_sheet(self) -> None:
        result = run_start(r"""
await boot([["/start-check/changes",call=>call.method==="POST"?{change_set_id:"run-changes-"+"1".repeat(24),source:"wizard",changes:[]}:{values:[],target:{kind:"analysis"}}],["/start-check",{enabled:true,questions:[],skipped_reason:"no_numeric_values"}],...base()]);
go("#/experiment");
pickProtocolFromList("p-1");await settle(8);
openStartForProtocol("p-1");await settle(10);
primary().dispatch("click");await settle(6);
// Lane EASY decision 2 (2026-10-24): "오늘 원문과 다르게 할 값" now follows the safety check.
assert(wizard.step==="changes"&&wizard.checkedProtocolId==="p-1",`not read: ${wizard.step}`);
// Human decision 2026-10-24: the card starts at once only after that step was passed too.
primary().dispatch("click");await settle(8);
closeWizardSheet();
isRegisteringProtocol=true;
node("experiment-start-open").dispatch("click");await settle(8);
assert(sessions===0,"started during an upload");
assert(wizard.open===true&&wizard.step==="start"&&note().includes("올리는 중"),`the card's refusal was not said: ${wizard.open} ${wizard.step} ${note()}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
