"""Lane HEAR2, decisions 1-2 (2026-10-26): a provider failure carries a bounded code.

``SpeechProviderError`` names the provider and the HTTP status; now it also
carries ``status`` and a short ``code`` the screens and metrics can use:
the provider's own code when its JSON reply has one (ElevenLabs'
``quota_exceeded`` of the person's test), else one by status
(``unauthorized``, ``rate_limited``, ``provider_error``, ``provider_refused``),
and ``timeout`` / ``unreachable`` for a request that never got an answer.
The message still holds at most a bounded slice of the reply, never a key.
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

import requests

from voiney_lab import voice_providers as vp


class _Response:
    def __init__(self, status: int, body: object) -> None:
        self.status_code = status
        self.text = json.dumps(body) if not isinstance(body, str) else body
        self.content = self.text.encode("utf-8")
        self.ok = status < 400

    def json(self):
        return json.loads(self.text)


class ProviderErrorCodeTests(unittest.TestCase):
    def test_the_providers_own_code_is_kept_and_bounded(self) -> None:
        error = vp._failure("elevenlabs", "STT", _Response(401, {
            "detail": {"type": "invalid_request", "code": "quota_exceeded",
                       "message": "This request exceeds your quota of 10000. You have 0 credits remaining"}}))
        self.assertIsInstance(error, vp.SpeechProviderError)
        self.assertEqual((error.status, error.code), (401, "quota_exceeded"))
        self.assertIn("elevenlabs STT failed (401)", str(error))
        bad = vp._failure("elevenlabs", "STT", _Response(401, {"detail": {"code": "x" * 200 + "!"}}))
        self.assertEqual(bad.code, "unauthorized", "an unbounded or odd code falls back to the status's")

    def test_codes_by_status_when_the_reply_names_none(self) -> None:
        for status, code in ((401, "unauthorized"), (403, "unauthorized"), (429, "rate_limited"),
                             (500, "provider_error"), (503, "provider_error"), (400, "provider_refused")):
            with self.subTest(status=status):
                self.assertEqual(vp._failure("google_cloud", "STT", _Response(status, "nothing")).code, code)
        self.assertEqual(vp.SpeechProviderError("plain").code, "provider_error")
        self.assertIsNone(vp.SpeechProviderError("plain").status)

    def test_a_request_that_never_answered_is_a_provider_error_too(self) -> None:
        settings = vp.SttProviderSettings(provider=vp.ELEVENLABS, model="scribe_v2", timeout_seconds=1.0)
        for raised, code in ((requests.Timeout("slow"), "timeout"), (requests.ConnectionError("down"), "unreachable")):
            with self.subTest(code=code), patch.dict("os.environ", {vp.ELEVENLABS_KEY_ENV: "k"}), \
                    patch.object(vp.requests, "post", side_effect=raised):
                with self.assertRaises(vp.SpeechProviderError) as caught:
                    vp.transcribe(b"\0\0" * 320, settings, language="ko")
                self.assertEqual(caught.exception.code, code)
                self.assertNotIn("k", str(caught.exception).split("failed")[-1][:3])


if __name__ == "__main__":
    unittest.main()
