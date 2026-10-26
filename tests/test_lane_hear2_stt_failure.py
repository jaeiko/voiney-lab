"""Lane HEAR2, decisions 1-2 (2026-10-26): a speech-to-text provider that fails is heard about.

The person's test: from 03:13:02 every one of 44 speech-to-text requests
failed (ElevenLabs answered 401 ``quota_exceeded``: no credits left), and
the person saw "요청 이해 중" turn back into "듣는 중" with no answer, over
and over, until they stopped the test. The server logged "voice turn
failed" with a traceback and sent the screen a turn ``error`` the new screen
shows as a small line ("방금 말을 처리하지 못했어요. 다시 말해 주세요.") --
wrong advice during an outage -- and said nothing aloud.

Now a provider failure is one more dropped sound (``speech.dropped``,
reason ``stt_failed``) with a bounded code and plain words for the screen,
said aloud once per outage ("받아쓰기 서비스가 답하지 않아요. 관리자에게 알려
주세요."), recorded as a metric by its code, and never a traceback or the
provider's message. A response that comes back ends the outage: the next
failure is said again.
"""

from __future__ import annotations

import unittest

import requests

from tests.test_lane_hear_drops import DropHarness, utterance
from voiney_lab.voice_providers import SpeechProviderError

QUOTA = SpeechProviderError(
    'elevenlabs STT failed (401): {"detail":{"type":"invalid_request","code":"quota_exceeded",'
    '"message":"This request exceeds your quota of 10000. You have 0 credits remaining"}}',
    status=401, code="quota_exceeded",
)


class SttFailureTests(DropHarness):
    def test_a_failed_provider_is_shown_recorded_and_said_once_per_outage(self) -> None:
        session, clock = self.listener()
        metrics: list[dict] = []
        outcomes = iter([QUOTA, QUOTA, "다음", QUOTA])

        def heard(pcm):
            outcome = next(outcomes, QUOTA)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        sent = self.run_socket(session, clock, [
            {"bytes": utterance(0.1)}, {"sleep": 0.3},
            {"advance": 5.0}, {"bytes": utterance(0.1)}, {"sleep": 0.3},
            {"advance": 5.0}, {"bytes": utterance(0.1)}, {"sleep": 0.8},
            {"advance": 5.0}, {"bytes": utterance(0.1)}, {"sleep": 0.5},
        ], heard=heard, metrics=metrics)
        dropped = [m for m in self.of(sent, "speech.dropped") if m["reason"] == "stt_failed"]
        self.assertEqual(len(dropped), 3, [m["type"] for m in sent])
        for message in dropped:
            self.assertEqual(message["code"], "quota_exceeded")
            self.assertEqual(message["notice"], "받아쓰기 서비스가 답하지 않아요")
            self.assertIn("한도", message["reason_ko"])
            self.assertGreater(message["duration_ms"], 0)
        said = [m for m in self.of(sent, "server.words") if m["words_kind"] == "stt_failure"]
        self.assertEqual(
            [m["text"] for m in said],
            ["받아쓰기 서비스가 답하지 않아요. 관리자에게 알려 주세요."] * 2,
            "once for the first outage, once again after a response came back")
        self.assertEqual(
            [m for m in self.of(sent, "error") if m.get("message") == "voice turn processing failed"], [],
            "a provider failure is not a turn processing failure")
        codes = [m["dimensions"].get("reason_code") for m in metrics if m.get("metric_name") == "speech_dropped"]
        self.assertEqual(codes.count("stt_failed"), 3)
        self.assertIn("speech.dropped reason=stt_failed", self.logs)
        self.assertIn("code=quota_exceeded status=401", self.logs)
        self.assertNotIn("credits", self.logs, "the provider's message is never logged")
        self.assertNotIn("Traceback", self.logs)

    def test_the_failure_metrics_are_accepted_by_a_real_workspace_store(self) -> None:
        # Lane HEAR's speech_ignored metric was never stored: its dimension
        # name was not on the store's privacy-safe list and the store refused
        # the whole row. The failure's metrics go through a real store here.
        import tempfile
        from pathlib import Path

        from tests.identity_support import development_principal
        from voiney_lab.workspace_store import WorkspaceSettings, initialize_workspace_store

        session, clock = self.listener()
        metrics: list[dict] = []

        def heard(pcm):
            raise QUOTA

        self.run_socket(session, clock, [{"bytes": utterance(0.1)}, {"sleep": 0.3}], heard=heard, metrics=metrics)
        failure_metrics = [m for m in metrics if m.get("metric_name") in {"speech_dropped", "stt_latency_ms"}]
        self.assertEqual({m["metric_name"] for m in failure_metrics}, {"speech_dropped", "stt_latency_ms"})
        with tempfile.TemporaryDirectory() as folder:
            store = initialize_workspace_store(WorkspaceSettings(enabled=True, data_dir=Path(folder)))
            try:
                principal = development_principal()
                store.bootstrap_principal(principal)
                for metric in failure_metrics:
                    store.record_analytics(
                        principal, category=metric["category"], metric_name=metric["metric_name"],
                        metric_value=metric.get("metric_value", 1.0), dimensions=metric["dimensions"])
                rows = store._connection.execute(  # noqa: SLF001
                    "SELECT metric_name,dimensions_json FROM analytics_events ORDER BY rowid").fetchall()
            finally:
                store.close()
        stored = {row[0]: row[1] for row in rows}
        self.assertIn('"reason_code":"stt_failed"', stored["speech_dropped"])
        self.assertIn('"event_kind":"quota_exceeded"', stored["speech_dropped"])
        self.assertIn('"reason_code":"quota_exceeded"', stored["stt_latency_ms"])

    def test_a_timeout_and_an_unreachable_provider_are_named(self) -> None:
        for raised, code in ((requests.Timeout("slow"), "timeout"), (requests.ConnectionError("down"), "unreachable")):
            with self.subTest(code=code):
                session, clock = self.listener()

                def heard(pcm, raised=raised):
                    raise raised

                sent = self.run_socket(session, clock, [{"bytes": utterance(0.1)}, {"sleep": 0.3}], heard=heard)
                dropped = [m for m in self.of(sent, "speech.dropped") if m["reason"] == "stt_failed"]
                self.assertEqual([m["code"] for m in dropped], [code])


if __name__ == "__main__":
    unittest.main()


class BargeInSttFailureTests(unittest.TestCase):
    """A provider that fails on words said while an answer plays: one drop, the provider's."""

    def test_one_provider_drop_and_no_second_transcription_drop_over_it(self) -> None:
        import asyncio
        import json
        from types import SimpleNamespace
        from unittest.mock import patch

        from tests.test_server_helpers import Decisions, frame
        from voiney_lab.server import ListenerSession, voice_socket
        from voiney_lab.vad import EndpointDetector, TurnState, VadConfig

        config = VadConfig(
            onset_voiced_frames=2, onset_window_frames=3, prefix_frames=3,
            endpoint_silence_frames=2, minimum_voiced_frames=2,
            maximum_utterance_frames=30, cooldown_ms=0,
            playback_onset_voiced_frames=2, playback_onset_window_frames=3,
        )
        session = ListenerSession(EndpointDetector(config, classifier=lambda _: False))
        session.start()
        session.accept_configuration(41, "cascade", "ko", "lane-hear2")
        session.active_turn_id = 1
        session.turn_generations[1] = session.generation
        session.detector.state = TurnState.PROCESSING
        self.assertTrue(session.start_playback(1))
        session._interrupt_detector = EndpointDetector(
            config, classifier=Decisions([False, True, True, True, False, False]))

        class Socket:
            def __init__(self):
                self.text = []
                self.messages = [{"bytes": frame(11) * 6}, {"type": "websocket.disconnect", "code": 1000}]

            async def accept(self):
                return None

            async def receive(self):
                await asyncio.sleep(0.05)
                return self.messages.pop(0)

            async def send_text(self, value):
                self.text.append(json.loads(value))

            async def send_bytes(self, value):
                return None

            async def close(self, **kwargs):
                return None

        socket = Socket()
        with patch("voiney_lab.server.ListenerSession", return_value=session), patch(
            "voiney_lab.server.VoiceVadSettings.from_environment",
            return_value=SimpleNamespace(cascade=object()),
        ), patch("voiney_lab.server.VadConfig.from_settings", return_value=config), patch(
            "voiney_lab.server.transcribe", side_effect=QUOTA,
        ), patch("voiney_lab.server.synthesize", return_value=b"\0\0" * 320):
            asyncio.run(voice_socket(socket))
        rejected = [m for m in socket.text if m["type"] == "barge_in_rejected"]
        self.assertEqual([m["reason"] for m in rejected], ["stt_failed"])
        dropped = [m for m in socket.text if m["type"] == "speech.dropped"]
        self.assertEqual([m["reason"] for m in dropped], ["stt_failed"],
                         "the provider's drop is the only one; no transcription drop overwrites it")
        self.assertEqual(dropped[0]["code"], "quota_exceeded")
        self.assertTrue(dropped[0]["during_playback"])
