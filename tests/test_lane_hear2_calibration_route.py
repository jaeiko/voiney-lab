"""Lane HEAR2, decision 6 (2026-10-26): 목소리 맞추기 works against a workspace store.

The person's test: ten measurements, ten failures. The server answered 500
to each ``POST /api/voice/calibration`` because the workspace store's
settings table refused the measurement's two names (schema 11; see
``test_lane_hear2_settings_store_v12``). Through the production route with
the workspace on, the real VAD and a recording of the size the screens
post (five seconds of 16 kHz PCM16): the measurement is kept, the filter
can be turned on, and each refusal carries a code the screens turn into
plain words.
"""

from __future__ import annotations

import array
import os
import tempfile
import asyncio
import base64
import math
import unittest
from unittest.mock import patch

import httpx

import voiney_lab.server as server_module
from voiney_lab.server import CALIBRATION_MAX_BYTES, app, parse_voice_reference

SAMPLE_RATE = 16000


def _speech_like(seconds: float, gain: float = 0.08) -> bytes:
    """A voice-shaped sound (a 110 Hz voice with harmonics, syllables at 4 Hz)."""

    samples = array.array("h")
    for i in range(int(SAMPLE_RATE * seconds)):
        t = i / SAMPLE_RATE
        envelope = 0.5 * (1 + math.sin(2 * math.pi * 4 * t))
        pitch = 110 + 20 * math.sin(2 * math.pi * 0.7 * t)
        value = sum(math.sin(2 * math.pi * pitch * k * t) / k for k in range(1, 12))
        samples.append(int(max(-32768, min(32767, gain * envelope * value * 32767))))
    return samples.tobytes()


def _post(path, payload):
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.post(path, json=payload)
    return asyncio.run(run())


def _put(path, payload):
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.put(path, json=payload)
    return asyncio.run(run())


def _get(path):
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.get(path)
    return asyncio.run(run())


class CalibrationWithAWorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # patch.dict puts every name back on stop, the popped ones included.
        environ = patch.dict("os.environ", {
            "VOINEY_LAB_WORKSPACE_ENABLED": "true",
            "VOINEY_LAB_WORKSPACE_DATA_DIR": self.tmp.name,
            "VOINEY_LAB_USAGE_SCOPE": "demo",
        })
        environ.start()
        self.addCleanup(environ.stop)
        for name in ("VOINEY_LAB_AUTH_MODE", "VOINEY_LAB_OIDC_ISSUER", "VOINEY_LAB_OIDC_AUDIENCE", "VOINEY_LAB_OIDC_JWKS_URL"):
            os.environ.pop(name, None)

    def test_a_five_second_recording_is_measured_and_kept_in_the_workspace(self) -> None:
        pcm = _speech_like(5.0)
        self.assertLessEqual(len(pcm), CALIBRATION_MAX_BYTES)
        response = _post("/api/voice/calibration", {
            "pcm_base64": base64.b64encode(pcm).decode("ascii"), "mic_id": "mic-3f9a1c2b"})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["state"], "measured")
        self.assertEqual(body["mic_id"], "mic-3f9a1c2b")
        self.assertTrue(-60.0 < body["level_db"] < -10.0, body)
        settings = _get("/api/experimenter/settings").json()["settings"]
        reference = parse_voice_reference(settings["voice_reference"])
        self.assertIsNotNone(reference, settings)
        self.assertEqual(reference.mic_id, "mic-3f9a1c2b")
        self.assertEqual(settings["voice_calibration"], "measured")
        # The filter the measurement enables is kept in the same store.
        turned_on = _put("/api/experimenter/settings", {"speaker_filter": "on"})
        self.assertEqual(turned_on.status_code, 200, turned_on.text)
        self.assertEqual(turned_on.json()["settings"]["speaker_filter"], "on")

    def test_a_refusal_carries_a_code_the_screens_can_name(self) -> None:
        quiet = bytes(SAMPLE_RATE * 2 * 3)
        refused = _post("/api/voice/calibration", {
            "pcm_base64": base64.b64encode(quiet).decode("ascii"), "mic_id": "mic-1"})
        self.assertEqual(refused.status_code, 422)
        self.assertEqual(refused.json()["detail"]["code"], "too_little_voice")
        short = _post("/api/voice/calibration", {
            "pcm_base64": base64.b64encode(_speech_like(0.2)).decode("ascii"), "mic_id": "mic-1"})
        self.assertEqual(short.status_code, 422)
        self.assertEqual(short.json()["detail"]["code"], "audio_length")
        self.assertEqual(_get("/api/experimenter/settings").json()["settings"]["voice_reference"], "none")


if __name__ == "__main__":
    unittest.main()
