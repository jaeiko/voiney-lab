"""Lane FX3, decision 7: measuring the voice in a noisy room is said to be unreliable.

Lane SP2 found (its report, 1 and 12-3) that the sentence read to measure
the wearer's voice, read where babble, a pump or a TV keeps the VAD on, runs
to the 15 s bound and takes the room's level for the wearer's: the level
gate then parts nothing. The person decided (2026-10-16) that the server
says so at that moment. When, the reference just measured, the room's noise
floor (lane SP2's ``NoiseFloor``) is within ``LEVEL_ENDPOINT_FLOOR_GAP_DB`` of
the wearer's line -- lane SP2's own "room near the line" -- the reply adds
"방이 시끄러워 맞추기가 정확하지 않아요. 조용한 곳에서 다시 맞추거나 시끄러운 곳
모드를 쓰세요." and the screen shows the same sentence. A suggestion only:
the measurement stands and no setting changes. It counts as the room's
suggestion, so lane SP2's own one does not follow it at once.
"""

from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_lane_sp1_level_gate import Clock, Socket
from tests.test_lane_sp2_noise_suggestion import _Curated
from tests.test_screen_cleanup import run_page_script
from voiney_lab.language import Transcription
from voiney_lab.server import (
    LEVEL_ENDPOINT_FLOOR_GAP_DB,
    NOISY_CALIBRATION_WORDS,
    VOICE_CALIBRATION_DONE,
    VOICE_CALIBRATION_SENTENCE,
    ListenerSession,
    ToolContext,
    TurnState,
    run_turn_safely,
)
from voiney_lab.vad import NOISE_FLOOR_MINIMUM_FRAMES, SENSITIVITY_MARGINS_DB

READ_AT = -25.0
LINE = READ_AT - SENSITIVITY_MARGINS_DB["normal"]


class CalibrationTurnTests(unittest.TestCase):
    def turn(self, *, floor_db: float | None, frames: int = NOISE_FLOOR_MINIMUM_FRAMES,
             level: float | None = READ_AT, heard: str = VOICE_CALIBRATION_SENTENCE):
        session = ListenerSession(
            tool_context=ToolContext(Path("/unused/offline-catalog"), None, "ko", "test_only"),
            clock=Clock())
        session.start()
        session.accept_configuration(41, "cascade", "ko", "lane-fx3")
        if floor_db is not None:
            for _ in range(frames):
                session.noise_floor.add(floor_db, True)
        session.arm_voice_calibration()
        session.active_turn_id = 2
        session.turn_generations[2] = session.generation
        session.turn_committed_at[2] = session.clock()
        session.detector.state = TurnState.PROCESSING
        socket = Socket()
        with patch("voiney_lab.server.transcribe", return_value=Transcription(heard, "ko")), patch(
            "voiney_lab.server.synthesize", return_value=b"\0\0" * 320,
        ), self.assertLogs("voiney_lab", level="INFO") as logs:
            asyncio.run(run_turn_safely(socket, session, b"\0\0" * 160, 2, 25, 20, voiced_level_db=level))
        self.logs = "\n".join(logs.output)
        calibration = next(m for m in socket.text if m["type"] == "speaker.calibration")
        reply = next(m for m in socket.text if m["type"] == "reply.delta")["text"]
        return session, calibration, reply

    def test_a_floor_near_the_line_is_said_and_shown(self) -> None:
        near = LINE - LEVEL_ENDPOINT_FLOOR_GAP_DB + 2
        session, calibration, reply = self.turn(floor_db=near)
        self.assertEqual(calibration["state"], "measured")
        self.assertEqual(calibration["notice"], NOISY_CALIBRATION_WORDS)
        self.assertEqual(
            NOISY_CALIBRATION_WORDS,
            "방이 시끄러워 맞추기가 정확하지 않아요. 조용한 곳에서 다시 맞추거나 시끄러운 곳 모드를 쓰세요.")
        self.assertIn(NOISY_CALIBRATION_WORDS, reply)
        self.assertTrue(reply.startswith("목소리를 맞췄어요."))
        self.assertIn("voice calibration noisy room", self.logs)

    def test_the_measurement_stands_and_nothing_else_changes(self) -> None:
        session, _, _ = self.turn(floor_db=LINE - LEVEL_ENDPOINT_FLOOR_GAP_DB + 2)
        self.assertEqual(session.level_reference.reference_db, READ_AT)
        self.assertEqual(session.speaker_sensitivity, "normal")
        # It was the room's suggestion: lane SP2's does not follow it at once
        # (a quiet-mode curated session with nothing open, as lane SP2 asks).
        self.assertIsNotNone(session.ambient_suggested_at)
        session.curated_protocol_session = _Curated()
        self.assertTrue(session.room_near_the_line())
        self.assertFalse(session.noise_floor_suggestion_due())

    def test_a_quiet_room_is_measured_as_before(self) -> None:
        for floor, frames in ((LINE - LEVEL_ENDPOINT_FLOOR_GAP_DB - 2, NOISE_FLOOR_MINIMUM_FRAMES),
                              (-70.0, NOISE_FLOOR_MINIMUM_FRAMES),
                              (READ_AT, NOISE_FLOOR_MINIMUM_FRAMES - 1),
                              (None, 0)):
            with self.subTest(floor=floor, frames=frames):
                session, calibration, reply = self.turn(floor_db=floor, frames=frames)
                self.assertEqual(calibration["state"], "measured")
                self.assertNotIn("notice", calibration)
                self.assertEqual(reply, VOICE_CALIBRATION_DONE)
                self.assertIsNone(session.ambient_suggested_at)

    def test_a_skip_or_a_retry_says_nothing_of_the_room(self) -> None:
        near = LINE - LEVEL_ENDPOINT_FLOOR_GAP_DB + 2
        for heard, level, state in (("건너뛰기", READ_AT, "skipped"),
                                    (VOICE_CALIBRATION_SENTENCE, None, "prompted")):
            with self.subTest(state=state):
                _, calibration, reply = self.turn(floor_db=near, level=level, heard=heard)
                self.assertEqual(calibration["state"], state)
                self.assertNotIn("notice", calibration)
                self.assertNotIn(NOISY_CALIBRATION_WORDS, reply)


PAGE = r"""
const configuration_id=41;
acceptedSessionConfiguration={configuration_id,mode:"cascade",language:"ko",server_generation:0,protocol_id:"p",revision_id:"r"};sessionActive=true;socket=new WS();
const say=m=>onMessage({data:JSON.stringify({configuration_id,generation:0,...m})},sessionGeneration,socket);
node("voice-calibration-notice").hidden=true;  // as the markup has it
const WORDS="방이 시끄러워 맞추기가 정확하지 않아요. 조용한 곳에서 다시 맞추거나 시끄러운 곳 모드를 쓰세요.";
"""


class PageTests(unittest.TestCase):
    def test_the_screen_shows_the_server_sentence_until_the_next_measurement(self) -> None:
        result = run_page_script(PAGE + r"""
await say({type:"speaker.calibration",turn_id:2,state:"measured",sentence:"s",sensitivity:"normal",level_db:-25,margin_db:12,threshold_db:-37,notice:WORDS});
const notice=node("voice-calibration-notice");
assert(notice.hidden===false&&node("voice-calibration-notice-text").textContent===WORDS,`notice: ${notice.hidden} ${node("voice-calibration-notice-text").textContent}`);
assert(node("mic-check-voice").textContent.endsWith(WORDS),node("mic-check-voice").textContent);
await say({type:"speaker.calibration",turn_id:3,state:"prompting",sentence:"s",sensitivity:"normal"});
assert(notice.hidden===true,"the notice stayed while measuring again");
await say({type:"speaker.calibration",turn_id:4,state:"measured",sentence:"s",sensitivity:"normal",level_db:-20,margin_db:12,threshold_db:-32});
assert(notice.hidden===true&&!node("mic-check-voice").textContent.includes("시끄러워"),"a quiet measurement shows the notice");
await say({type:"speaker.calibration",turn_id:5,state:"measured",sentence:"s",sensitivity:"normal",level_db:-25,margin_db:12,threshold_db:-37,notice:WORDS});
assert(notice.hidden===false,"the notice did not come back with the next noisy measurement");
node("voice-calibration-notice-close").dispatch("click");
assert(notice.hidden===true,"the close button did not put the notice away");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_another_session_s_notice_is_not_shown(self) -> None:
        result = run_page_script(PAGE + r"""
await onMessage({data:JSON.stringify({type:"speaker.calibration",configuration_id:999,turn_id:2,state:"measured",level_db:-25,margin_db:12,notice:WORDS})},sessionGeneration,socket);
assert(node("voice-calibration-notice").hidden!==false,"another session's notice was shown");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
