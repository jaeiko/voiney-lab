"""Lane HEAR, decision 9 (2026-10-25): no "시끄러운 곳 모드" question during a run.

The person's test: "주변이 시끄러워 보여요. 시끄러운 곳 모드로 바꿀까요?" came
again and again and interrupted the run. The room (quiet or noisy) is now a
setting chosen once before the start. During a run nothing is said or
shown about the noise: the two conditions that used to make the server ask
-- three voices taken for other people's within a minute (lane SP1), the
noise floor near the wearer's line (lane SP2) -- are recorded as codes and
numbers only, and the noise floor itself is logged as a number once a
minute while listening.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests.test_lane_hear_drops import CONFIG, Clock, Socket, at_db, utterance, voiced_if_loud
from voiney_lab.language import Transcription
from voiney_lab.server import NOISE_FLOOR_RECORD_SECONDS, ListenerSession, voice_socket
from voiney_lab.vad import NOISE_FLOOR_MINIMUM_FRAMES, EndpointDetector


class _Curated:
    """Just what the server reads of a run for the room's conditions."""

    def __init__(self) -> None:
        self.ambient_mode = "quiet"
        self.ambient_suggestion_open = False
        self.workflow_status = "active"
        self.active = True

    def open_ambient_suggestion(self) -> None:
        self.ambient_suggestion_open = True

    def next_timer_notice_due(self, *args, **kwargs):
        return None

    def reset(self) -> None:
        return None


class NoNoiseQuestionTests(unittest.TestCase):
    def run_socket(self, session, clock, messages):
        socket = Socket(messages, clock)
        metrics: list[dict] = []
        with patch("voiney_lab.server.ListenerSession", return_value=session), patch(
            "voiney_lab.server.VoiceVadSettings.from_environment",
            return_value=SimpleNamespace(cascade=object()),
        ), patch("voiney_lab.server.VadConfig.from_settings", return_value=CONFIG), patch(
            "voiney_lab.server.transcribe", return_value=Transcription("", "ko"),
        ), patch("voiney_lab.server.synthesize", return_value=b"\0\0" * 320), patch(
            "voiney_lab.server._record_workspace_metric",
            side_effect=lambda **kwargs: metrics.append(kwargs),
        ), self.assertLogs("voiney_lab", level="INFO") as logs:
            asyncio.run(voice_socket(socket))
        self.logs = "\n".join(logs.output)
        self.metrics = metrics
        return socket.sent

    def session(self) -> tuple[ListenerSession, Clock]:
        clock = Clock()
        session = ListenerSession(
            EndpointDetector(CONFIG, classifier=voiced_if_loud, listening_onset=True), clock=clock)
        session.start()
        session.accept_configuration(41, "cascade", "ko", "lane-hear")
        session.curated_protocol_session = _Curated()
        session.set_level_reference(-20.0)
        return session, clock

    def test_three_voices_taken_for_others_in_a_minute_are_recorded_not_asked(self) -> None:
        session, clock = self.session()
        frames = []
        for _ in range(4):
            frames += [{"bytes": utterance(at_db(-55.0))}, {"sleep": 0.1}, {"advance": 5.0}]
        sent = self.run_socket(session, clock, frames + [{"sleep": 0.4}])
        self.assertEqual([m for m in sent if m["type"] == "server.words" and m.get("words_kind") == "ambient_suggestion"], [])
        self.assertFalse(session.curated_protocol_session.ambient_suggestion_open, "no question is opened")
        self.assertIn("noise.condition kind=other_voices", self.logs)
        recorded = [m for m in self.metrics if m.get("metric_name") == "noise_condition"]
        self.assertEqual([m["dimensions"]["reason_code"] for m in recorded], ["other_voices"], "once, not each time")

    def test_a_room_near_the_line_is_recorded_not_asked_and_the_floor_is_logged(self) -> None:
        session, clock = self.session()
        room = b"".join(utterance(at_db(-30.0), voiced_frames=40)[:-3 * 640] for _ in range(1))
        frames = [{"bytes": room}] * ((NOISE_FLOOR_MINIMUM_FRAMES // 40) + 3)
        sent = self.run_socket(session, clock, [*frames, {"advance": NOISE_FLOOR_RECORD_SECONDS + 1}, {"bytes": room}, {"sleep": 0.4}])
        self.assertEqual([m for m in sent if m["type"] == "server.words"], [])
        self.assertIn("noise.condition kind=floor_near_line", self.logs)
        self.assertIn("noise.floor floor_db=", self.logs)
        self.assertTrue(any(m.get("metric_name") == "noise_floor_db" for m in self.metrics))


if __name__ == "__main__":
    unittest.main()
