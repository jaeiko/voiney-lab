"""Lane HEAR, decisions 1 and 2 (2026-10-25): every word is heard unless the person turned the filter on.

Decision 1: the voice is not measured during a run. It is measured in the
settings (and, in the new screen, the first-login guide): the page records
the sentence and posts it to ``POST /api/voice/calibration``, which keeps
the measured level, when, and on which microphone as the experimenter's
``voice_reference`` -- a number, a time and a short microphone id, never the
sound. In a run, the screen's request and "목소리 맞춰 줘" are answered that
it is done in the settings.

Decision 2: by default every voice is heard. The level filter ("옆 사람 말
거르기") is used only when the person turned ``speaker_filter`` on after a
measurement, the measurement is younger than VOICE_REFERENCE_MAX_AGE_DAYS,
and the run's microphone is the one measured; otherwise nothing is
filtered, and the page is told which (``speaker.filter``).
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
import unittest
from unittest.mock import patch

import httpx

import voiney_lab.server as server_module
from tests.lane_n_support import VoiceNotesHarness
from tests.test_lane_hear_drops import DropHarness, at_db, tone_frame, utterance
from voiney_lab.audio import FRAME_BYTES
from voiney_lab.server import (
    VOICE_REFERENCE_MAX_AGE_DAYS,
    app,
    parse_voice_reference,
    voice_reference_value,
)
from voiney_lab.vad import SENSITIVITY_MARGINS_DB

SILENT = bytes(FRAME_BYTES)


def _client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _call(method, path, payload=None):
    async def run():
        async with _client() as client:
            return await client.request(method, path, json=payload)
    return asyncio.run(run())


def _loud_classifier(*_args, **_kwargs):
    return lambda frame: any(frame)


def _pcm(level_db: float | None, seconds: float = 2.0) -> str:
    frames = int(seconds / 0.02)
    if level_db is None:
        raw = SILENT * frames
    else:
        raw = b"".join(tone_frame(at_db(level_db), i) for i in range(frames))
    return base64.b64encode(raw).decode("ascii")


class CalibrationRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.environ = patch.dict("os.environ", {"VOINEY_LAB_WORKSPACE_ENABLED": "false"})
        self.environ.start()
        self.addCleanup(self.environ.stop)
        server_module._EXPERIMENTER_SETTINGS_MEMORY.clear()
        self.addCleanup(server_module._EXPERIMENTER_SETTINGS_MEMORY.clear)
        classifier = patch.object(server_module, "WebRtcVadClassifier", side_effect=_loud_classifier)
        classifier.start()
        self.addCleanup(classifier.stop)

    def test_the_sentence_read_is_measured_and_kept_as_a_number_a_time_and_a_microphone(self) -> None:
        response = _call("POST", "/api/voice/calibration", {"pcm_base64": _pcm(-23.0), "mic_id": "mic-3f9a1c2b"})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["state"], "measured")
        self.assertAlmostEqual(body["level_db"], -23.0, delta=0.3)
        self.assertEqual(body["mic_id"], "mic-3f9a1c2b")
        self.assertEqual(body["speaker_filter"], "off", "measuring does not turn the filter on")
        settings = _call("GET", "/api/experimenter/settings").json()["settings"]
        self.assertEqual(settings["voice_calibration"], "measured")
        reference = parse_voice_reference(settings["voice_reference"])
        self.assertAlmostEqual(reference.level_db, -23.0, delta=0.3)
        self.assertEqual(reference.mic_id, "mic-3f9a1c2b")
        self.assertLess(abs(reference.measured_at - time.time()), 60)

    def test_silence_is_refused_and_nothing_is_kept(self) -> None:
        response = _call("POST", "/api/voice/calibration", {"pcm_base64": _pcm(None), "mic_id": "mic-1"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["code"], "too_little_voice")
        settings = _call("GET", "/api/experimenter/settings").json()["settings"]
        self.assertEqual(settings["voice_calibration"], "none")
        self.assertEqual(settings["voice_reference"], "none")

    def test_bad_audio_is_refused(self) -> None:
        for payload in (
            {"pcm_base64": "not base64!!", "mic_id": "mic-1"},
            {"pcm_base64": _pcm(-23.0, seconds=0.2), "mic_id": "mic-1"},
            {"pcm_base64": _pcm(-23.0, seconds=13.0), "mic_id": "mic-1"},
            {"mic_id": "mic-1"},
        ):
            response = _call("POST", "/api/voice/calibration", payload)
            self.assertEqual(response.status_code, 422, payload.keys())

    def test_the_screen_cannot_write_a_reference_but_can_turn_the_filter_on_and_off(self) -> None:
        refused = _call("PUT", "/api/experimenter/settings", {"voice_reference": voice_reference_value(-10.0, time.time(), "mic-1")})
        self.assertEqual(refused.status_code, 403)
        self.assertEqual(refused.json()["detail"], "setting_server_only")
        self.assertEqual(_call("PUT", "/api/experimenter/settings", {"speaker_filter": "on"}).status_code, 200)
        self.assertEqual(_call("GET", "/api/experimenter/settings").json()["settings"]["speaker_filter"], "on")
        self.assertEqual(_call("PUT", "/api/experimenter/settings", {"speaker_filter": "loud"}).status_code, 400)

    def test_the_defaults_hear_everything(self) -> None:
        settings = _call("GET", "/api/experimenter/settings").json()["settings"]
        self.assertEqual(settings["speaker_filter"], "off")
        self.assertEqual(settings["voice_reference"], "none")


class SessionFilterTests(DropHarness):
    """At the audio-ready the run learns its microphone and whether to filter."""

    def run_with(self, settings: dict, mic_id: str | None, frames: list[dict]):
        session, clock = self.listener()
        ready = {"type": "client.audio_ready", "configuration_id": 41, "generation": session.generation,
                 "audio_context_state": "running", "sample_rate": 48000}
        if mic_id is not None:
            ready["mic_id"] = mic_id
        loaded = {**server_module.EXPERIMENTER_SETTING_DEFAULTS, **settings}
        async def no_greeting(*_args, **_kwargs):
            return None

        with patch.object(server_module, "_load_experimenter_settings", return_value=loaded), patch.object(
            server_module, "_send_session_greeting", new=no_greeting,
        ):
            return self.run_socket(session, clock, [{"text": json.dumps(ready)}, {"sleep": 0.2}, *frames])

    def filter_events(self, sent):
        return [m for m in sent if m["type"] == "speaker.filter"]

    def test_by_default_a_quiet_voice_is_heard(self) -> None:
        reference = voice_reference_value(-20.0, time.time(), "mic-1")
        sent = self.run_with({"voice_calibration": "measured", "voice_reference": reference}, "mic-1",
                             [{"bytes": utterance(at_db(-45.0))}, {"sleep": 0.3}])
        self.assertEqual([(m["state"], m["reason"]) for m in self.filter_events(sent)], [("inactive", "off")])
        self.assertNotIn("speech.ignored", [m["type"] for m in sent])
        self.assertIn("speech.end", [m["type"] for m in sent])

    def test_turned_on_measured_lately_on_this_microphone_it_filters(self) -> None:
        reference = voice_reference_value(-20.0, time.time() - 3600, "mic-1")
        sent = self.run_with({"speaker_filter": "on", "voice_calibration": "measured", "voice_reference": reference},
                             "mic-1", [{"bytes": utterance(at_db(-45.0))}, {"sleep": 0.3}])
        events = self.filter_events(sent)
        self.assertEqual(events[0]["state"], "active")
        self.assertEqual(events[0]["margin_db"], SENSITIVITY_MARGINS_DB["normal"])
        self.assertIn("speech.ignored", [m["type"] for m in sent])

    def test_an_old_measurement_another_microphone_or_none_filters_nothing(self) -> None:
        old = voice_reference_value(-20.0, time.time() - (VOICE_REFERENCE_MAX_AGE_DAYS + 1) * 86400, "mic-1")
        fresh = voice_reference_value(-20.0, time.time(), "mic-1")
        for settings, mic, reason in (
            ({"speaker_filter": "on", "voice_calibration": "measured", "voice_reference": old}, "mic-1", "stale_age"),
            ({"speaker_filter": "on", "voice_calibration": "measured", "voice_reference": fresh}, "mic-2", "stale_device"),
            ({"speaker_filter": "on", "voice_calibration": "measured", "voice_reference": fresh}, None, "stale_device"),
            ({"speaker_filter": "on"}, "mic-1", "not_measured"),
        ):
            with self.subTest(reason=reason, mic=mic):
                sent = self.run_with(settings, mic, [{"bytes": utterance(at_db(-45.0))}, {"sleep": 0.3}])
                self.assertEqual([(m["state"], m["reason"]) for m in self.filter_events(sent)], [("inactive", reason)])
                self.assertNotIn("speech.ignored", [m["type"] for m in sent])


class NoCalibrationInARunTests(VoiceNotesHarness, unittest.TestCase):
    def test_the_screens_request_in_a_run_is_answered_and_nothing_is_armed(self) -> None:
        seen = {}

        async def scenario(socket, listener, say):
            socket.control({"type": "client.voice_calibration", "action": "start",
                            "configuration_id": 1, "generation": listener.generation})
            await socket.wait_for(lambda item: item["type"] == "speaker.calibration")
            await asyncio.sleep(0.05)
            seen["pending"] = listener.calibration_pending

        socket = self.voice(scenario)
        calibration = [item for item in socket.sent if item["type"] == "speaker.calibration"]
        self.assertEqual([(m["state"], m["reason"]) for m in calibration], [("refused", "settings_only")])
        self.assertFalse(seen["pending"])
        self.assertNotIn("voice_calibration", [m.get("words_kind") for m in socket.sent if m["type"] == "server.words"])

    def test_asking_by_voice_in_a_run_is_answered_and_nothing_is_armed(self) -> None:
        seen = {}

        async def scenario(socket, listener, say):
            await say(40, "목소리 다시 맞춰 줘")
            seen["pending"] = listener.calibration_pending

        socket = self.voice(scenario)
        self.assertFalse(seen["pending"])
        replies = [m["text"] for m in socket.for_turn(40, "reply.delta")]
        self.assertTrue(any("설정에서" in text for text in replies), replies)
        self.assertNotIn("prompted", [m.get("state") for m in socket.sent if m["type"] == "speaker.calibration"])


if __name__ == "__main__":
    unittest.main()
