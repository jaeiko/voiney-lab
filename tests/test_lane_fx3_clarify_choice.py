"""Lane FX3, decision 4: a candidate asked back can be tapped, and goes as if it were said.

Lane UI3 drew the candidates a clarification asked back ("Buffer A요, Buffer
B요?") as a list to say, because the server took no words but spoken ones.
Now the page sends ``client.clarify_choice`` with the candidate, and the
server -- after checking it is one the last reply asked back, and that
nothing else is under way -- opens the next turn with those words, exactly
where the STT's words of an utterance go: the same rules, the same
confirmations, the same state changes or none. Nothing else is taken.
"""

from __future__ import annotations

import asyncio
import json
import unittest

from tests.lane_n_support import PROTOCOL_ID, VoiceNotesHarness, shown
from tests.protocol_vocabulary_support import build_fixture
from tests.test_lane_ui0_run_view import SETUP
from tests.test_lane_vt_vague_reference import STEPS
from tests.test_screen_cleanup import run_page_script
from voiney_lab.curated_protocol import CuratedProtocolSession
from voiney_lab.protocol import ProtocolError, parse_control
from voiney_lab.server import TurnState

def choice_fixture():
    """Lane VT's fictional mix (two buffers at step 1), under the voice harness's protocol id."""

    return build_fixture(
        protocol_id=PROTOCOL_ID, title="Fictional mix", steps=STEPS,
        materials=("Buffer A", "Buffer B", "Enzyme A", "Enzyme B", "Enzyme C", "Enzyme D"),
    )


CHOICE = {
    "type": "client.clarify_choice", "configuration_id": 1, "generation": 0,
    "turn_id": 2, "candidate": "Buffer B",
}


class ParseTests(unittest.TestCase):
    def test_the_choice_is_read_with_its_turn_and_session(self) -> None:
        parsed = parse_control(json.dumps({**CHOICE, "candidate": "  Buffer   B "}))
        self.assertEqual(parsed, {**CHOICE, "candidate": "Buffer B"})

    def test_a_malformed_choice_is_refused(self) -> None:
        for change in (
            {"candidate": ""}, {"candidate": "   "}, {"candidate": None}, {"candidate": 3},
            {"candidate": "x" * 201}, {"turn_id": 0}, {"turn_id": True}, {"turn_id": "2"},
            {"configuration_id": 0}, {"generation": -1}, {"generation": False},
        ):
            with self.subTest(change=change), self.assertRaises(ProtocolError):
                parse_control(json.dumps({**CHOICE, **change}))


class SessionTests(unittest.TestCase):
    def _asked(self) -> CuratedProtocolSession:
        session = CuratedProtocolSession(choice_fixture())
        session.activate_configured()
        session.plan("프로토콜 시작해줘", turn_id=1, language="ko", configuration_id=1, generation=1)
        session.plan("그거 얼마나 넣어?", turn_id=2, language="ko", configuration_id=1, generation=1)
        self.assertEqual(session.clarify_candidates(), ["Buffer A", "Buffer B"])
        return session

    def test_only_a_candidate_the_last_reply_asked_back_is_taken(self) -> None:
        session = self._asked()
        self.assertEqual(session.clarify_choice(" Buffer  B"), "Buffer B")
        self.assertIsNone(session.clarify_choice("Buffer C"))
        self.assertIsNone(session.clarify_choice(""))

    def test_a_turn_in_between_ends_the_choice(self) -> None:
        session = self._asked()
        session.plan("현재 단계 다시 알려줘", turn_id=3, language="ko", configuration_id=1, generation=1)
        self.assertIsNone(session.clarify_choice("Buffer B"))


class VoiceTests(VoiceNotesHarness, unittest.TestCase):
    fixture = choice_fixture()

    def _run(self, *, tap: dict | None = None, said: str | None = None, before=None):
        seen: dict = {}

        async def scenario(socket, listener, say):
            await say(1, "프로토콜 시작해줘")
            await say(2, "그거 얼마나 넣어?")
            # The voice turns 1 and 2 took their ids from the listener, and the
            # short cooldown after the answer has passed before a finger lands.
            listener.next_turn_id = 3
            listener.cooldown_until = 0
            if before is not None:
                before(listener)
            if said is not None:
                await say(3, said)
            if tap is not None:
                socket.control({**CHOICE, "generation": listener.generation, **tap})
                await socket.wait_for(lambda item: item["type"] in {
                    "clarify.choice.refused", "turn.done"} and item.get("turn_id") in {2, 3})
                if socket.for_turn(3, "turn.done"):
                    listener.playback_ended(3)
                await asyncio.sleep(0.05)
            curated = listener.curated_protocol_session
            seen["state"] = (curated.current_index, curated.workflow_status, curated.state()["revision"])
            seen["candidates"] = curated.clarify_candidates()

        socket = self.voice(scenario)
        return socket, seen

    def test_a_tapped_candidate_goes_the_way_the_same_words_said_go(self) -> None:
        tapped, after_tap = self._run(tap={})
        spoken, after_words = self._run(said="Buffer B")
        self.assertEqual(shown(tapped)[3], "이 단계의 Buffer B는 30 µL예요.")
        self.assertEqual(shown(tapped)[3], shown(spoken)[3])
        self.assertEqual(after_tap, after_words)
        self.assertEqual(tapped.for_turn(3, "transcript")[-1]["text"], "Buffer B")
        start = tapped.for_turn(3, "speech.start")[-1]
        self.assertEqual(start["input"], "clarify_choice")
        decision = tapped.for_turn(3, "turn.route_decision")[-1]
        self.assertEqual(decision["action"], spoken.for_turn(3, "turn.route_decision")[-1]["action"])
        self.assertEqual(tapped.for_turn(2, "clarify.choice.refused"), [])

    def test_a_candidate_not_asked_back_is_refused_and_nothing_runs(self) -> None:
        socket, seen = self._run(tap={"candidate": "Buffer C"})
        refused = socket.for_turn(2, "clarify.choice.refused")
        self.assertEqual([item["reason"] for item in refused], ["not_asked"])
        self.assertTrue(refused[0]["text"])
        self.assertEqual(socket.for_turn(3, "speech.start"), [])
        self.assertEqual(seen["candidates"], ["Buffer A", "Buffer B"])

    def test_a_choice_for_another_session_is_refused(self) -> None:
        socket, _ = self._run(tap={"configuration_id": 9})
        self.assertEqual(
            [item["reason"] for item in socket.for_turn(2, "clarify.choice.refused")],
            ["stale_session"])
        self.assertEqual(socket.for_turn(3, "speech.start"), [])

    def test_a_choice_while_a_turn_is_under_way_is_refused(self) -> None:
        def busy(listener):
            listener.detector.state = TurnState.USER_SPEAKING

        socket, seen = self._run(tap={}, before=busy)
        self.assertEqual(
            [item["reason"] for item in socket.for_turn(2, "clarify.choice.refused")], ["busy"])
        self.assertEqual(socket.for_turn(3, "speech.start"), [])
        self.assertEqual(seen["candidates"], ["Buffer A", "Buffer B"])


PAGE = SETUP + r"""
const screen=(extra={})=>({source_basis:null,safety_items:[],translation_source:"reviewed",translation_pending:false,repeat_round:null,open_question:null,source_figures:[],next_step_sentence:null,confirm_question:null,clarify_candidates:[],readback_values:[],record_deviation:null,...extra});
async function asked(id){
 await say({type:"speech.start",turn_id:id,generation:3});
 await say({type:"transcript",turn_id:id,generation:3,text:"그거 얼마나 넣어?"});
 await say({type:"protocol.fixture.state",configuration_id,turn_id:id,generation:3,action:"current",state:state(),screen:screen({clarify_candidates:["TBST","Blocking buffer"]})});
 await say({type:"server.operation",turn_id:id,generation:3,route:"curated_protocol",operation:"reference_clarification_required"});
 await say({type:"reply.delta",turn_id:id,generation:3,segment_index:0,text:"어떤 시약인가요?\n1. TBST\n2. Blocking buffer",answer_origin:"current_protocol",source_pages:[]});
 await say({type:"reply.complete",turn_id:id,generation:3,text:"어떤 시약인가요?\n1. TBST\n2. Blocking buffer"});
}
const cls=name=>kids(card()).filter(item=>String(item.className||"").split(" ").includes(name));
"""


class PageTests(unittest.TestCase):
    def test_a_candidate_is_a_button_that_sends_the_choice(self) -> None:
        result = run_page_script(PAGE + r"""
await step();
await asked(13);
const buttons=cls("run-card-choice");
assert(buttons.length===2&&buttons.every(item=>item.tagName==="button"),`buttons: ${buttons.map(item=>item.tagName)}`);
assert(buttons.map(item=>item.textContent).join("|")==="TBST|Blocking buffer",buttons.map(item=>item.textContent).join("|"));
assert(cls("run-card-choices-label")[0].textContent==="후보 · 누르거나 말해서 고르기",cls("run-card-choices-label")[0].textContent);
socket.sent.length=0;
buttons[1].dispatch("click");
const sent=socket.sent.map(item=>JSON.parse(item));
assert(sent.length===1,`sent: ${JSON.stringify(sent)}`);
assert(JSON.stringify(sent[0])===JSON.stringify({type:"client.clarify_choice",configuration_id,generation:3,turn_id:13,candidate:"Blocking buffer"}),JSON.stringify(sent[0]));
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_a_refusal_shows_the_server_words_on_the_card(self) -> None:
        result = run_page_script(PAGE + r"""
await step();
await asked(13);
await say({type:"clarify.choice.refused",configuration_id,turn_id:13,generation:3,reason:"busy",text:"지금은 고를 수 없어요. 말이 끝난 뒤 다시 눌러 주세요."});
assert(cls("run-card-choices-label")[0].textContent==="지금은 고를 수 없어요. 말이 끝난 뒤 다시 눌러 주세요.",cls("run-card-choices-label")[0].textContent);
assert(cls("run-card-choice").length===2,"the candidates went away");
await say({type:"clarify.choice.refused",configuration_id:999,turn_id:13,generation:3,reason:"busy",text:"다른 세션"});
assert(cls("run-card-choices-label")[0].textContent!=="다른 세션","another session's refusal was drawn");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_no_session_sends_nothing(self) -> None:
        result = run_page_script(PAGE + r"""
await step();
await asked(13);
sessionActive=false;socket.sent.length=0;
cls("run-card-choice")[0].dispatch("click");
assert(socket.sent.length===0,"a choice was sent with no session");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
