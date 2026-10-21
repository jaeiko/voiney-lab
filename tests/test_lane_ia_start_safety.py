"""Lane IA stage ③ with lane SAFE's start rule (2026-10-21, ~/reports/safe/HANDOFF_IA.md).

The server now starts a protocol only after this account finished the key-value check of that
analysis (or review version) and left a safety acknowledgement for it. The sheet's safety step
leaves it: [읽었어요] sends POST /api/protocols/{id}/start-check/safety with the whole target the
GET start-check returned. A refusal (422 and the like) shows the server's sentence (or, with only
a code, plain words) and goes no further. A start the server refuses
(session.configuration_required with start_check_required, safety_acknowledgement_required,
start_check_unavailable or start_check_identity_required and message_ko) shows that sentence and
opens the sheet again at the step it names. Shares tests/test_lane_ia_stage3.py's harness.
"""

from __future__ import annotations

import unittest

from tests.test_lane_ia_stage3 import STAGE3
from tests.test_lane_ia_tabs import run

SAFETY = r"""
const TARGET={kind:"analysis",version:null,event_id:null,analysis_id:"an-1",revision_id:"pdf-1",analysis_revision_number:1};
const NO_QUESTIONS={enabled:true,protocol_id:"p-1",target:TARGET,questions:[],skipped_reason:"no_numeric_values"};
const READY={ready:true,reason:null,target:TARGET,missing_question_ids:[],safety_acknowledged:true};
const refusal=(payload)=>{const answer={ok:false,status:422,headers:{get:()=>"application/json"},json:async()=>payload,clone:()=>answer};return answer;};
const safetyPosts=()=>calls.filter(call=>call.url.endsWith("/start-check/safety"));
async function toSafety(answers){await boot(answers);go("#/experiment");pickProtocolFromList("p-1");await settle(8);openStartForProtocol("p-1");await settle(10);assert(wizard.step==="safety",`not at the safety step: ${wizard.step}`);}
"""


def run_safety(body: str):
    return run(STAGE3 + SAFETY + body)


class SafetyAcknowledgementTests(unittest.TestCase):
    def test_read_sends_the_whole_target_and_moves_on(self) -> None:
        result = run_safety(r"""
await toSafety([["/start-check/safety",READY],["/start-check",NO_QUESTIONS],...base()]);
primary().dispatch("click");await settle(8);
const posted=safetyPosts();
assert(posted.length===1&&posted[0].method==="POST"&&JSON.stringify(posted[0].body)===JSON.stringify({target:TARGET}),`posted: ${JSON.stringify(posted.map(call=>call.body))}`);
assert(posted[0].url==="/api/protocols/p-1/start-check/safety",posted[0].url);
assert(wizard.step==="start"&&wizard.checkedProtocolId==="p-1",`after: ${wizard.step} ${wizard.checkedProtocolId}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_refusal_shows_the_servers_sentence_and_goes_no_further(self) -> None:
        result = run_safety(r"""
await toSafety([["/start-check/safety",()=>refusal({detail:"start_check_invalid",message_ko:"핵심 값 확인이 바뀌었어요. 처음부터 다시 확인해 주세요."})],["/start-check",NO_QUESTIONS],...base()]);
let started=0;startReviewedProtocol=async()=>{started+=1;};
primary().dispatch("click");await settle(8);
assert(wizard.step==="safety"&&wizard.checkedProtocolId===null&&started===0,`moved on after a refusal: ${wizard.step} ${wizard.checkedProtocolId} ${started}`);
assert(node("wizard-note").textContent==="핵심 값 확인이 바뀌었어요. 처음부터 다시 확인해 주세요."&&wizard.noteKind==="error",`note: ${node("wizard-note").textContent}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_refusal_with_only_a_code_says_it_in_plain_words(self) -> None:
        result = run_safety(r"""
await toSafety([["/start-check/safety",()=>refusal({detail:"start_check_invalid"})],["/start-check",NO_QUESTIONS],...base()]);
primary().dispatch("click");await settle(8);
assert(wizard.step==="safety",wizard.step);
const note=node("wizard-note").textContent;
assert(note.includes("핵심 값")&&!note.includes("start_check_invalid"),`note: ${note}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_with_the_check_off_nothing_is_sent(self) -> None:
        result = run_safety(r"""
await toSafety([["/start-check/safety",READY],["/start-check",{enabled:false,protocol_id:"p-1",target:null,questions:[],skipped_reason:"disabled"}],...base()]);
primary().dispatch("click");await settle(8);
assert(safetyPosts().length===0&&wizard.step==="start",`check off: posts ${safetyPosts().length} step ${wizard.step}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr)


class StartRefusalTests(unittest.TestCase):
    def refused(self, reason: str, words: str, step: str, check: str = "NO_QUESTIONS") -> None:
        result = run_safety(r"""
await boot([["/start-check",%s],...base()]);
go("#/experiment");pickProtocolFromList("p-1");await settle(8);
socket=new WebSocket("ws://test/ws");pendingSessionConfiguration={configuration_id:9,mode:"cascade",language:"ko",protocol_id:"p-1"};
await deliver(socket,{type:"session.configuration_required",configuration_id:9,mode:"cascade",language:"ko",protocol_id:"p-1",reason:"%s",message_ko:"%s"});await settle(10);
assert(node("session-configuration-status").textContent==="%s",`configuration line: ${node("session-configuration-status").textContent}`);
assert(node("status").textContent==="%s",`state line: ${node("status").textContent}`);
assert(wizard.open===true&&wizard.step==="%s",`sheet: ${wizard.open} ${wizard.step}`);
assert(node("wizard-note").textContent==="%s",`note: ${node("wizard-note").textContent}`);
assert(socket.readyState===1,"the refusal closed the connection");
""" % (check, reason, words, words, words, step, words))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_no_safety_acknowledgement_opens_the_safety_step(self) -> None:
        self.refused("safety_acknowledgement_required", "안전 주의를 읽고 확인해 주세요.", "safety")

    def test_an_unfinished_key_value_check_opens_the_check(self) -> None:
        self.refused("start_check_required", "핵심 값을 먼저 확인해 주세요.", "check", "CHECK")


if __name__ == "__main__":
    unittest.main()
