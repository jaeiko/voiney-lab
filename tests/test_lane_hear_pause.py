"""Lane HEAR, decision 7 (2026-10-25): while paused, only a resume word is heard.

The person's test: after [일시정지], words spoken nearby were answered --
"음성 인식 언어가 불확실합니다. 다시 한 번 말씀해 주세요." While a run is
paused the server now takes only the resume words ("다시 시작", "이어서
하자", "계속하자", "재개" and their endings) and answers nothing else with
sound: no language question, no asking back, no drop notice. An emergency
is still answered (the emergency gate comes first, AGENTS.md rule 3). A
guidance sentence playing when [일시정지] is pressed stops with it; the step
timer goes on as before. The current screen's band says how to resume.
"""

from __future__ import annotations

import asyncio
import unittest

from tests.lane_n_support import VoiceNotesHarness
from tests.test_screen_cleanup import run_page_script
from voiney_lab.curated_protocol import CuratedProtocolSession, is_paused_resume_utterance
from voiney_lab.replay_turns import _demo_fixture
from voiney_lab.runtime_routing import route_curated_runtime_turn
from voiney_lab.vad import TurnState


class ResumeWordsTests(unittest.TestCase):
    def test_the_resume_words_and_only_they(self) -> None:
        for words in ("다시 시작", "다시 시작해 줘", "이어서 하자", "이어서 할게요", "이어서 진행해 줘",
                      "계속하자", "계속 진행해줘", "재개", "재개해 주세요", "실험 계속", "resume"):
            self.assertTrue(is_paused_resume_utterance(words), words)
        for words in ("다음 단계 알려줘", "일시정지", "다시 시작할까요?", "ok so thank you", "이어서 뭐 해?",
                      "3개", "타이머 몇 분 남았어"):
            self.assertFalse(is_paused_resume_utterance(words), words)

    def test_a_paused_run_resumes_on_ieoseo_haja(self) -> None:
        session = CuratedProtocolSession(_demo_fixture())
        session.active = True
        self.assertTrue(session.pause_workflow())
        plan = route_curated_runtime_turn(
            session, "이어서 하자", turn_id=1, language="ko", configuration_id=1, generation=1).plan
        self.assertEqual(session.workflow_status, "active", plan.intent_kind)


class PausedVoiceTests(VoiceNotesHarness, unittest.TestCase):
    def paused_run(self, words: list[tuple[int, str]]):
        seen: dict = {}

        async def scenario(socket, listener, say):
            await say(1, "프로토콜 시작해줘")
            socket.control({"type": "workflow.pause", "configuration_id": 1, "generation": listener.generation})
            await socket.wait_for(lambda item: item["type"] == "protocol.fixture.state" and item.get("action") == "pause")
            for turn_id, text in words:
                await say(turn_id, text)
            seen["status"] = listener.curated_protocol_session.workflow_status

        socket = self.voice(scenario)
        return socket, seen

    def test_other_words_get_no_sound_and_no_question(self) -> None:
        socket, seen = self.paused_run([(10, "다음 단계 알려줘"), (11, "ok so thank you"), (12, "일시정지")])
        for turn_id in (10, 11, 12):
            self.assertEqual(socket.for_turn(turn_id, "reply.delta"), [], turn_id)
            self.assertEqual(socket.for_turn(turn_id, "audio.segment.start"), [], turn_id)
            self.assertEqual(socket.for_turn(turn_id, "stt.language_mismatch"), [], turn_id)
            dropped = socket.for_turn(turn_id, "speech.dropped")
            self.assertEqual([(m["reason"], m["paused"], m["notice"]) for m in dropped], [("paused", True, None)])
        self.assertEqual(seen["status"], "paused")
        self.assertNotIn(10, self.spoken)
        self.assertNotIn(11, self.spoken)

    def test_a_resume_word_resumes_and_is_answered(self) -> None:
        socket, seen = self.paused_run([(10, "다음 단계 알려줘"), (11, "이어서 하자")])
        self.assertEqual(seen["status"], "active")
        self.assertTrue(socket.for_turn(11, "reply.delta"))

    def test_an_emergency_is_still_answered(self) -> None:
        socket, seen = self.paused_run([(10, "불났어")])
        self.assertTrue(socket.for_turn(10, "reply.delta"), "an emergency went unanswered while paused")


class PauseStopsPlaybackTests(VoiceNotesHarness, unittest.TestCase):
    def test_the_pause_button_stops_the_answer_playing(self) -> None:
        seen: dict = {}

        async def scenario(socket, listener, say):
            await say(1, "프로토콜 시작해줘")
            # An answer of turn 7 is playing when the button is pressed.
            listener.active_turn_id = 7
            listener.turn_generations[7] = listener.generation
            listener.detector.state = TurnState.PROCESSING
            listener.start_playback(7)
            socket.control({"type": "workflow.pause", "configuration_id": 1, "generation": listener.generation})
            await socket.wait_for(lambda item: item["type"] == "playback.stop")
            await asyncio.sleep(0.05)
            seen["state"] = listener.state
            seen["turn"] = listener.active_turn_id

        socket = self.voice(scenario)
        stop = next(item for item in socket.sent if item["type"] == "playback.stop")
        self.assertEqual((stop["turn_id"], stop["reason"]), (7, "paused"))
        self.assertIn(seen["state"], (TurnState.COOLDOWN, TurnState.IDLE))
        self.assertIsNone(seen["turn"])


class PauseBandPageTests(unittest.TestCase):
    def test_the_band_says_how_to_resume_and_the_stop_clears_playback(self) -> None:
        result = run_page_script(r"""
const text=node("workflow-paused-text").textContent;
const html=require("fs").readFileSync("src/voiney_lab/static/index.html","utf8");
assert(html.includes("일시정지 중 — ‘다시 시작’이라고 말하거나 [다시 시작]을 누르세요"),"the band's words are not the decided ones");
acceptedSessionConfiguration={configuration_id:71,mode:"cascade",language:"ko",server_generation:3,protocol_id:"p",revision_id:"r"};sessionActive=true;socket=new WS();
let cleared=0;clearPlayback=()=>{cleared+=1;};
await onMessage({data:JSON.stringify({type:"playback.stop",configuration_id:71,turn_id:7,generation:3,reason:"paused"})},sessionGeneration,socket);
assert(cleared===1,"the page did not stop the playing answer");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
