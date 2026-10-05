"""Contract tests for the speech provider adapters (lane SV).

Every provider call here goes to a fake ``requests.post``: these tests are
contract-tested, not live-tested. They pin the request each adapter sends,
how it reads a reply, and that a failure, a timeout or an empty result keeps
the caller's existing safe path (raise -> re-ask; empty text -> rejected).
"""

from __future__ import annotations

import base64
import json
import unittest
from unittest.mock import patch

import requests

from voiney_lab import voice_providers as vp
from voiney_lab.audio import pcm_to_wav
from voiney_lab.configuration import ConfigurationError
from voiney_lab.server import _tts_voice, synthesize, transcribe

PCM = b"\x01\x00" * 16000  # one second of 16 kHz mono PCM16
KEYS = {"VOINEY_LAB_GOOGLE_SPEECH_API_KEY": "g-key", "ELEVENLABS_API_KEY": "e-key"}


class FakeResponse:
    def __init__(self, payload=None, *, status=200, content=None,
                 content_type="application/json"):
        self.status_code = status
        self.ok = 200 <= status < 300
        self.headers = {"content-type": content_type}
        if content is None:
            content = json.dumps(payload).encode() if payload is not None else b""
        self.content = content
        self.text = content.decode("utf-8", "replace")
        self._payload = payload

    def raise_for_status(self):
        if not self.ok:
            raise requests.HTTPError(str(self.status_code))

    def json(self):
        if self._payload is None:
            raise ValueError("no JSON")
        return self._payload


def env(**values):
    return patch.dict("os.environ", {**KEYS, **values}, clear=True)


def post(response=None, *, raises=None):
    return patch("voiney_lab.voice_providers.requests.post",
                 return_value=response, side_effect=raises)


GOOGLE_KO = {
    "results": [{
        "alternatives": [{
            "transcript": "15분 동안 37도에서 800 rpm",
            "words": [{"word": "15분", "startTime": "0.100s", "endTime": "0.600s"}],
        }],
        "languageCode": "ko-kr",
    }],
    "totalBilledTime": "1s",
}
ELEVEN_KO = {
    "text": " 트립신 25 µL 넣고 잠깐 멈춰 ",
    "language_code": "kor",
    "language_probability": 0.99,
    "words": [
        {"text": "트립신", "start": 0.0, "end": 0.4, "type": "word"},
        {"text": " ", "start": 0.4, "end": 0.5, "type": "spacing"},
    ],
}


class SettingsTests(unittest.TestCase):
    def test_defaults_are_the_xai_path_that_ran_before(self):
        with patch.dict("os.environ", {}, clear=True):
            stt = vp.SttProviderSettings.from_environment()
            tts = vp.TtsProviderSettings.from_environment()
            self.assertEqual(_tts_voice(), "leo")
        self.assertEqual((stt.provider, stt.model, stt.language, stt.timeout_seconds),
                         ("xai", "", None, 120.0))
        self.assertEqual((tts.provider, tts.model, tts.voice, tts.timeout_seconds),
                         ("xai", "", "leo", 120.0))

    def test_each_provider_has_its_own_defaults_and_overrides(self):
        google = vp.SttProviderSettings.from_environment({"VOINEY_LAB_STT_PROVIDER": "google_cloud"})
        eleven = vp.TtsProviderSettings.from_environment({"VOINEY_LAB_TTS_PROVIDER": "ElevenLabs"})
        self.assertEqual(google.model, "latest_long")
        self.assertEqual((eleven.provider, eleven.model), ("elevenlabs", "eleven_v4_turbo"))
        chosen = vp.TtsProviderSettings.from_environment({
            "VOINEY_LAB_TTS_PROVIDER": "google_cloud", "VOINEY_LAB_TTS_VOICE": "ko-KR-Chirp3-HD-Kore",
            "VOINEY_LAB_TTS_TIMEOUT_SECONDS": "9"})
        self.assertEqual((chosen.voice, chosen.timeout_seconds), ("ko-KR-Chirp3-HD-Kore", 9.0))

    def test_bad_values_are_refused_by_name(self):
        for environment, name in (
            ({"VOINEY_LAB_STT_PROVIDER": "whisper"}, "VOINEY_LAB_STT_PROVIDER"),
            ({"VOINEY_LAB_STT_LANGUAGE": "ja"}, "VOINEY_LAB_STT_LANGUAGE"),
            ({"VOINEY_LAB_STT_TIMEOUT_SECONDS": "0"}, "VOINEY_LAB_STT_TIMEOUT_SECONDS"),
            ({"VOINEY_LAB_STT_TIMEOUT_SECONDS": "soon"}, "VOINEY_LAB_STT_TIMEOUT_SECONDS"),
        ):
            with self.subTest(environment=environment), self.assertRaises(ConfigurationError) as caught:
                vp.SttProviderSettings.from_environment(environment)
            self.assertIn(name, str(caught.exception))
        with self.assertRaises(ConfigurationError):
            vp.TtsProviderSettings.from_environment({"VOINEY_LAB_TTS_PROVIDER": "xai2"})


class DefaultPathTests(unittest.TestCase):
    def test_default_settings_never_reach_the_new_adapters(self):
        with patch.dict("os.environ", {"XAI_API_KEY": "x"}, clear=True), \
             patch("voiney_lab.voice_providers.requests.post") as adapter, \
             patch("voiney_lab.server.requests.post", return_value=FakeResponse(
                 {"text": "네", "language": "Korean", "duration": 0.4})) as original:
            self.assertEqual(transcribe(PCM).text, "네")
        adapter.assert_not_called()
        self.assertTrue(original.call_args.args[0].endswith("/stt"))
        self.assertEqual(original.call_args.kwargs["timeout"], 120.0)


class GoogleSttTests(unittest.TestCase):
    def test_success_sends_v1_linear16_and_reads_korean_numbers(self):
        with env(VOINEY_LAB_STT_PROVIDER="google_cloud"), post(FakeResponse(GOOGLE_KO)) as sent:
            result = transcribe(PCM, language="ko", keyterms=("AMBIC", "HPLC water", "x" * 101))
        self.assertEqual(result.text, "15분 동안 37도에서 800 rpm")
        self.assertEqual((result.detected_language, result.duration_seconds, result.response_status),
                         ("ko", 1.0, 200))
        self.assertEqual(result.words, ({"word": "15분", "start": 0.1, "end": 0.6},))
        self.assertEqual(sent.call_args.args[0], "https://speech.googleapis.com/v1/speech:recognize")
        self.assertEqual(sent.call_args.kwargs["headers"], {"X-goog-api-key": "g-key"})
        body = sent.call_args.kwargs["json"]
        self.assertEqual(body["config"]["languageCode"], "ko-KR")
        self.assertEqual(body["config"]["model"], "latest_long")
        self.assertEqual(body["config"]["sampleRateHertz"], 16000)
        self.assertEqual(body["config"]["speechContexts"], [{"phrases": ["AMBIC", "HPLC water"]}])
        self.assertEqual(base64.b64decode(body["audio"]["content"]), PCM)
        self.assertNotIn("g-key", json.dumps(body))

    def test_auto_language_uses_the_setting_then_korean(self):
        with env(VOINEY_LAB_STT_PROVIDER="google_cloud"), post(FakeResponse(GOOGLE_KO)) as sent:
            transcribe(PCM)
        self.assertEqual(sent.call_args.kwargs["json"]["config"]["languageCode"], "ko-KR")
        with env(VOINEY_LAB_STT_PROVIDER="google_cloud", VOINEY_LAB_STT_LANGUAGE="en"), \
             post(FakeResponse(GOOGLE_KO)) as sent:
            transcribe(PCM)
        self.assertEqual(sent.call_args.kwargs["json"]["config"]["languageCode"], "en-US")

    def test_empty_result_is_an_empty_transcript_not_an_error(self):
        for payload in ({}, {"results": []}, {"results": [{"alternatives": []}]}):
            with self.subTest(payload=payload), env(VOINEY_LAB_STT_PROVIDER="google_cloud"), \
                 post(FakeResponse(payload)):
                result = transcribe(PCM, language="ko")
            self.assertEqual(result.text, "")
            self.assertIsNone(result.detected_language)

    def test_http_error_raises_without_the_key(self):
        refused = FakeResponse({"error": {"code": 429, "status": "RESOURCE_EXHAUSTED"}}, status=429)
        with env(VOINEY_LAB_STT_PROVIDER="google_cloud"), post(refused), \
             self.assertRaises(vp.SpeechProviderError) as caught:
            transcribe(PCM, language="ko")
        self.assertIn("google_cloud STT failed (429)", str(caught.exception))
        self.assertNotIn("g-key", str(caught.exception))

    def test_timeout_and_missing_key_raise(self):
        with env(VOINEY_LAB_STT_PROVIDER="google_cloud", VOINEY_LAB_STT_TIMEOUT_SECONDS="3"), \
             post(raises=requests.Timeout("slow")) as sent, self.assertRaises(requests.Timeout):
            transcribe(PCM, language="ko")
        self.assertEqual(sent.call_args.kwargs["timeout"], 3.0)
        with patch.dict("os.environ", {"VOINEY_LAB_STT_PROVIDER": "google_cloud"}, clear=True), \
             post(FakeResponse(GOOGLE_KO)) as sent, self.assertRaises(RuntimeError):
            transcribe(PCM, language="ko")
        sent.assert_not_called()

    def test_malformed_json_raises(self):
        with env(VOINEY_LAB_STT_PROVIDER="google_cloud"), \
             post(FakeResponse(None, content=b"<html>")), self.assertRaises(vp.SpeechProviderError):
            transcribe(PCM, language="ko")


class ElevenLabsSttTests(unittest.TestCase):
    def test_success_sends_scribe_and_keeps_korean_text_and_units(self):
        with env(VOINEY_LAB_STT_PROVIDER="elevenlabs"), post(FakeResponse(ELEVEN_KO)) as sent:
            result = transcribe(PCM, language="ko",
                                keyterms=("요오드아세트아마이드", "one two three four five six", "y" * 50))
        self.assertEqual(result.text, "트립신 25 µL 넣고 잠깐 멈춰")
        self.assertEqual(result.detected_language, "ko")
        self.assertEqual(result.words, ({"word": "트립신", "start": 0.0, "end": 0.4},))
        self.assertEqual(sent.call_args.args[0], "https://api.elevenlabs.io/v1/speech-to-text")
        self.assertEqual(sent.call_args.kwargs["headers"], {"xi-api-key": "e-key"})
        fields = sent.call_args.kwargs["files"]
        named = [(name, value[1]) for name, value in fields[:-1]]
        self.assertEqual(named, [
            ("model_id", "scribe_v2"), ("tag_audio_events", "false"),
            ("timestamps_granularity", "word"), ("language_code", "ko"),
            ("keyterms", "요오드아세트아마이드"),
        ])
        self.assertEqual(fields[-1][0], "file")
        self.assertEqual(fields[-1][1][1], pcm_to_wav(PCM))

    def test_auto_language_lets_the_provider_detect(self):
        with env(VOINEY_LAB_STT_PROVIDER="elevenlabs"), post(FakeResponse(ELEVEN_KO)) as sent:
            transcribe(PCM)
        self.assertNotIn("language_code", [name for name, _ in sent.call_args.kwargs["files"]])

    def test_empty_error_and_timeout(self):
        with env(VOINEY_LAB_STT_PROVIDER="elevenlabs"), post(FakeResponse({"text": "", "words": []})):
            self.assertEqual(transcribe(PCM, language="ko").text, "")
        limited = FakeResponse({"detail": {"status": "quota_exceeded"}}, status=401)
        with env(VOINEY_LAB_STT_PROVIDER="elevenlabs"), post(limited), \
             self.assertRaises(vp.SpeechProviderError) as caught:
            transcribe(PCM, language="ko")
        self.assertIn("elevenlabs STT failed (401)", str(caught.exception))
        with env(VOINEY_LAB_STT_PROVIDER="elevenlabs"), post(raises=requests.ConnectTimeout()), \
             self.assertRaises(requests.Timeout):
            transcribe(PCM, language="ko")


def wav16(pcm=b"\x02\x00" * 800, rate=16000, channels=1):
    import io
    import wave
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return out.getvalue()


class GoogleTtsTests(unittest.TestCase):
    def test_success_strips_the_wav_header(self):
        audio = base64.b64encode(wav16()).decode()
        with env(VOINEY_LAB_TTS_PROVIDER="google_cloud"), post(FakeResponse({"audioContent": audio})) as sent:
            pcm = synthesize("다음 단계는 37°C에서 15분입니다.", "ko")
        self.assertEqual(pcm, b"\x02\x00" * 800)
        body = sent.call_args.kwargs["json"]
        self.assertEqual(body["voice"], {"languageCode": "ko-KR", "name": "ko-KR-Chirp3-HD-Charon"})
        self.assertEqual(body["audioConfig"], {"audioEncoding": "LINEAR16", "sampleRateHertz": 16000})
        self.assertEqual(sent.call_args.args[0], "https://texttospeech.googleapis.com/v1/text:synthesize")

    def test_chirp_voice_follows_the_turn_language(self):
        self.assertEqual(vp.google_voice_for("ko-KR-Chirp3-HD-Charon", "en"),
                         ("en-US", "en-US-Chirp3-HD-Charon"))
        self.assertEqual(vp.google_voice_for("ko-KR-Neural2-A", "ko"), ("ko-KR", "ko-KR-Neural2-A"))

    def test_wrong_audio_shape_error_and_timeout_raise(self):
        for payload in ({"audioContent": base64.b64encode(wav16(rate=24000)).decode()},
                        {"audioContent": ""}, {}):
            with self.subTest(payload=list(payload)), env(VOINEY_LAB_TTS_PROVIDER="google_cloud"), \
                 post(FakeResponse(payload)), self.assertRaises(vp.SpeechProviderError):
                synthesize("네", "ko")
        with env(VOINEY_LAB_TTS_PROVIDER="google_cloud"), \
             post(FakeResponse({"error": {"code": 403}}, status=403)), \
             self.assertRaises(vp.SpeechProviderError):
            synthesize("네", "ko")
        with env(VOINEY_LAB_TTS_PROVIDER="google_cloud"), post(raises=requests.ReadTimeout()), \
             self.assertRaises(requests.Timeout):
            synthesize("네", "ko")

    def test_empty_text_makes_no_request(self):
        with env(VOINEY_LAB_TTS_PROVIDER="google_cloud"), post(FakeResponse({})) as sent:
            self.assertEqual(synthesize("   ", "ko"), b"")
        sent.assert_not_called()


class ElevenLabsTtsTests(unittest.TestCase):
    def test_success_asks_for_raw_16khz_pcm(self):
        with env(VOINEY_LAB_TTS_PROVIDER="elevenlabs"), \
             post(FakeResponse(None, content=b"\x03\x00" * 10, content_type="audio/pcm")) as sent:
            self.assertEqual(synthesize("트립신 25 µL", "ko"), b"\x03\x00" * 10)
        self.assertEqual(sent.call_args.args[0],
                         "https://api.elevenlabs.io/v1/text-to-speech/JBFqnCBsd6RMkjVDRZzb")
        self.assertEqual(sent.call_args.kwargs["params"], {"output_format": "pcm_16000"})
        self.assertEqual(sent.call_args.kwargs["json"]["model_id"], "eleven_v4_turbo")
        self.assertEqual(sent.call_args.kwargs["json"]["language_code"], "ko")

    def test_json_odd_bytes_error_and_timeout_raise(self):
        for response in (FakeResponse({"detail": "x"}),
                         FakeResponse(None, content=b"\x01", content_type="audio/pcm"),
                         FakeResponse(None, content=b"", content_type="audio/pcm"),
                         FakeResponse({"detail": {"status": "quota_exceeded"}}, status=401)):
            with self.subTest(status=response.status_code, size=len(response.content)), \
                 env(VOINEY_LAB_TTS_PROVIDER="elevenlabs"), post(response), \
                 self.assertRaises(vp.SpeechProviderError):
                synthesize("네", "ko")
        with env(VOINEY_LAB_TTS_PROVIDER="elevenlabs"), post(raises=requests.Timeout()), \
             self.assertRaises(requests.Timeout):
            synthesize("네", "ko")


if __name__ == "__main__":
    unittest.main()
