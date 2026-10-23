"""Recognized emergencies precede calibration and language admission on a real turn.

Speech transports are fake; this checks server precedence, not acoustic
recognition or field emergency performance.
"""
from __future__ import annotations

import asyncio
import copy
import time
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tests.protocol_vocabulary_support import miniprep_fixture
from tests.test_lane_sp1_level_gate import Socket
from voiney_lab import server
from voiney_lab.curated_protocol import CuratedProtocolSession
from voiney_lab.emergency import ENGLISH_EMERGENCY_RESPONSE, KOREAN_EMERGENCY_RESPONSE
from voiney_lab.language import Transcription
from voiney_lab.vad import TurnState


class EmergencyAdmissionBoundaryTests(unittest.TestCase):
    def turn(
        self, transcription: Transcription, *, router_first: bool = False,
        tts_failed: bool = False, echo: bool = False, fragment: str | None = None,
    ) -> tuple[list[dict], server.ListenerSession, AsyncMock]:
        context = server.ToolContext(Path("/unused/offline-catalog"), None, "ko", "test_only")
        session = server.ListenerSession(tool_context=context, clock=time.monotonic)
        session.start()
        session.accept_configuration(41, "cascade", "ko", "synthetic-emergency")
        session.set_level_reference(-30.0)
        session.arm_voice_calibration()
        session.active_turn_id = 2
        session.turn_generations[2] = session.generation
        session.turn_committed_at[2] = session.clock()
        session.detector.state = TurnState.PROCESSING
        session.llm_router_settings = replace(
            session.llm_router_settings, enabled=router_first, router_first=router_first,
            model="fake-router")
        if fragment is not None:
            session.hold_fragment(1, session.generation, fragment, ended_at=session.clock() - 0.6)
        curated = CuratedProtocolSession(miniprep_fixture())
        curated.activate_configured()
        session.curated_protocol_session = curated
        before = copy.deepcopy(curated._checkpoint())
        socket = Socket()
        calibration = AsyncMock()
        router = AsyncMock(side_effect=AssertionError("emergency reached router"))
        synthesize = (
            {"side_effect": RuntimeError("synthetic_tts_failure")}
            if tts_failed else {"return_value": b"\0\0" * 320})
        with patch.object(server, "transcribe", return_value=transcription), \
             patch.object(server, "synthesize", **synthesize), \
             patch.object(server, "_finish_voice_calibration", calibration), \
             patch.object(server, "_route_with_llm_router", router), \
             patch.object(session, "own_speech_echo", return_value=(
                 SimpleNamespace(overlap=1.0) if echo else None)):
            asyncio.run(server.run_turn_safely(
                socket, session, b"\0\0" * 160, 2, 25, 20, voiced_level_db=-20.0))
        self.assertEqual(curated._checkpoint(), before)
        router.assert_not_called()
        self.assertEqual(session.level_reference.reference_db, -30.0)
        self.assertTrue(session.calibration_pending)
        return socket.text, session, calibration

    def test_emergency_does_not_become_a_voice_calibration_sample(self) -> None:
        events, _, calibration = self.turn(Transcription("도와주세요!", "ko"))
        calibration.assert_not_called()
        done = next(event for event in events if event["type"] == "turn.done")
        self.assertEqual(done["route"], "deterministic_emergency")
        reply = next(event for event in events if event["type"] == "reply.complete")
        self.assertEqual(reply["text"], KOREAN_EMERGENCY_RESPONSE)
        self.assertEqual(sum(event["type"] == "transcript" for event in events), 1)
        states = [event["state"] for event in events if event["type"] == "turn.state"]
        self.assertLess(states.index("routing"), states.index("synthesizing"))

    def test_english_emergency_precedes_korean_language_confirmation(self) -> None:
        events, _, calibration = self.turn(Transcription("Help!", "en"), router_first=True)
        calibration.assert_not_called()
        self.assertEqual(next(event for event in events if event["type"] == "reply.complete")["text"],
                         ENGLISH_EMERGENCY_RESPONSE)
        self.assertNotIn("session.language_confirmation_required", [event["type"] for event in events])

    def test_fixed_emergency_reply_precedes_soft_non_speech_admission(self) -> None:
        events, _, calibration = self.turn(Transcription("도와주세요!", "ko", no_speech_probability=1.0))
        calibration.assert_not_called()
        self.assertEqual(next(event for event in events if event["type"] == "turn.done")["route"],
                         "deterministic_emergency")

    def test_a_previous_command_fragment_does_not_hide_a_new_emergency(self) -> None:
        events, session, calibration = self.turn(
            Transcription("도와주세요!", "ko"), router_first=True, fragment="메모해 줘")
        calibration.assert_not_called()
        self.assertNotIn("turn.fragment_joined", [event["type"] for event in events])
        self.assertEqual(next(event for event in events if event["type"] == "reply.complete")["text"],
                         KOREAN_EMERGENCY_RESPONSE)
        self.assertIsNone(session.held_fragment())

    def test_an_emergency_completed_by_a_fragment_uses_the_same_gate(self) -> None:
        events, _, calibration = self.turn(
            Transcription("a fire!", "en"), router_first=True, fragment="There is")
        calibration.assert_not_called()
        self.assertIn("turn.fragment_joined", [event["type"] for event in events])
        self.assertEqual(next(event for event in events if event["type"] == "reply.complete")["text"],
                         ENGLISH_EMERGENCY_RESPONSE)

    def test_tts_failure_keeps_the_fixed_text_and_workflow_checkpoint(self) -> None:
        events, _, calibration = self.turn(Transcription("도와주세요!", "ko"), tts_failed=True)
        calibration.assert_not_called()
        self.assertEqual(next(event for event in events if event["type"] == "reply.complete")["text"],
                         KOREAN_EMERGENCY_RESPONSE)
        self.assertEqual(next(event for event in events if event["type"] == "audio.complete")["segment_count"], 0)

    def test_hypothetical_still_uses_calibration_without_an_emergency_reply(self) -> None:
        events, _, calibration = self.turn(Transcription("불이 나면 어떻게 해야 하나요?", "ko"))
        calibration.assert_awaited_once()
        self.assertFalse(any(event.get("route") == "deterministic_emergency" for event in events))

    def test_empty_and_self_echo_are_still_rejected_before_emergency_recognition(self) -> None:
        for transcription, echo in ((Transcription("", "ko"), False), (Transcription("도와주세요!", "ko"), True)):
            with self.subTest(echo=echo):
                events, _, calibration = self.turn(transcription, echo=echo)
                calibration.assert_not_called()
                self.assertNotIn("reply.complete", [event["type"] for event in events])
