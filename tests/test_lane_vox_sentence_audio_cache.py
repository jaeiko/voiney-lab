"""Lane VOX, decision 3 (2026-10-23): a guidance sentence's audio kept in memory.

A step read again, the same start sentences, the greeting of the next
session: the same sentence in the same provider's same voice is made once
and then said from memory. Only the guidance asks for that
(``sentence_audio_kept``); the experimenter's own words read back, a record
and the emergency reply are made anew every time, as before. The memory is
bounded by entries and bytes, and its key holds the provider, its model,
the voice and the language, so one voice's audio is never given to another.

The real ``server.synthesize`` runs; only the provider request under it is a
fake. No provider is called.
"""

from __future__ import annotations

import asyncio
import hashlib
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import voiney_lab.server as server_module
from tests.lane_n_support import VoiceNotesHarness
from voiney_lab import voice_providers
from voiney_lab.emergency import KOREAN_EMERGENCY_RESPONSE
from voiney_lab.language import clean_speech_text

REAL_SYNTHESIZE = server_module.synthesize
REAL_PROVIDER_SYNTHESIZE = voice_providers.synthesize
GOOGLE = {"VOINEY_LAB_TTS_PROVIDER": "google_cloud", "VOINEY_LAB_TTS_VOICE": "voice-a"}


class Provider:
    """The provider's side of TTS: records each request, answers by voice and text."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str, str, str]] = []
        self.fail = False

    def __call__(self, text, language, settings) -> bytes:
        self.requests.append((settings.provider, settings.voice, language, text))
        if self.fail:
            raise RuntimeError("offline provider failure")
        seed = hashlib.sha256(f"{settings.provider}|{settings.voice}|{text}".encode()).digest()
        return seed[:2] * 800

    def texts(self) -> list[str]:
        return [text for *_, text in self.requests]


class MemoryTests(unittest.TestCase):

    def setUp(self) -> None:
        self.provider = Provider()
        for item in (
            patch("voiney_lab.server.voice_providers.synthesize", side_effect=self.provider),
            patch.dict("os.environ", GOOGLE),
        ):
            item.start()
            self.addCleanup(item.stop)

    def kept(self, text: str, language: str = "ko") -> bytes:
        with server_module.sentence_audio_kept():
            return server_module.synthesize(text, language)

    def test_the_same_sentence_in_the_same_voice_is_made_once(self) -> None:
        first = self.kept("1단계: 튜브에 넣습니다.")
        again = self.kept("1단계: 튜브에 넣습니다.")
        self.assertEqual(first, again)
        self.assertEqual(len(self.provider.requests), 1)
        # Cleaned the same way (spacing, a citation mark), it is the same sentence.
        self.kept("1단계:  튜브에 넣습니다. [1]")
        self.assertEqual(len(self.provider.requests), 1)

    def test_another_voice_provider_or_language_makes_its_own(self) -> None:
        sentence = "현재 1단계입니다."
        voice_a = self.kept(sentence)
        with patch.dict("os.environ", {"VOINEY_LAB_TTS_VOICE": "voice-b"}):
            voice_b = self.kept(sentence)
        with patch.dict("os.environ", {"VOINEY_LAB_TTS_PROVIDER": "elevenlabs"}):
            other_provider = self.kept(sentence)
        english = self.kept(sentence, "en")
        # The voice the experimenter chose for the session (lane ON).
        token = server_module._SPEAKING_SESSION.set(
            SimpleNamespace(tts_voice="voice-c", remember_spoken=lambda _text: None))
        try:
            chosen = self.kept(sentence)
        finally:
            server_module._SPEAKING_SESSION.reset(token)
        self.assertEqual(
            [(provider, voice, language) for provider, voice, language, _ in self.provider.requests],
            [("google_cloud", "voice-a", "ko"), ("google_cloud", "voice-b", "ko"),
             ("elevenlabs", "voice-a", "ko"), ("google_cloud", "voice-a", "en"),
             ("google_cloud", "voice-c", "ko")])
        self.assertEqual(len({voice_a, voice_b, other_provider, chosen}), 4)
        # Each voice keeps its own: voice A again is voice A's audio.
        self.assertEqual(self.kept(sentence), voice_a)
        with patch.dict("os.environ", {"VOINEY_LAB_TTS_VOICE": "voice-b"}):
            self.assertEqual(self.kept(sentence), voice_b)
        self.assertEqual(len(self.provider.requests), 5)
        self.assertEqual(english, self.kept(sentence, "en"))

    def test_nothing_is_kept_unless_the_guidance_asks(self) -> None:
        server_module.synthesize("타이머가 끝났어요.", "ko")
        server_module.synthesize("타이머가 끝났어요.", "ko")
        self.assertEqual(len(self.provider.requests), 2)
        self.assertEqual(len(server_module.SENTENCE_AUDIO), 0)

    def test_a_failed_request_keeps_nothing(self) -> None:
        self.provider.fail = True
        with self.assertRaisesRegex(RuntimeError, "offline provider failure"):
            self.kept("현재 1단계입니다.")
        self.provider.fail = False
        self.kept("현재 1단계입니다.")
        self.assertEqual(len(self.provider.requests), 2)

    def test_an_unset_provider_still_fails_by_its_name(self) -> None:
        with patch.dict("os.environ", {"VOINEY_LAB_TTS_PROVIDER": ""}), patch(
            "voiney_lab.server.voice_providers.synthesize", new=REAL_PROVIDER_SYNTHESIZE,
        ):
            with self.assertRaisesRegex(Exception, "VOINEY_LAB_TTS_PROVIDER"):
                self.kept("현재 1단계입니다.")
        self.assertEqual(len(server_module.SENTENCE_AUDIO), 0)

    def test_the_memory_is_bounded_by_entries_and_bytes(self) -> None:
        memory = server_module.SentenceAudioCache(max_entries=3, max_bytes=4000)
        for index in range(5):
            memory.put(("p", "m", "v", "ko", f"s{index}"), b"\0" * 1000)
        self.assertEqual(len(memory), 3)
        self.assertIsNone(memory.get(("p", "m", "v", "ko", "s0")))
        self.assertIsNotNone(memory.get(("p", "m", "v", "ko", "s4")))
        # The one used last stays; the oldest goes first.
        memory.get(("p", "m", "v", "ko", "s2"))
        memory.put(("p", "m", "v", "ko", "big"), b"\0" * 2500)
        self.assertLessEqual(memory.bytes, 4000)
        self.assertIsNotNone(memory.get(("p", "m", "v", "ko", "s2")))
        self.assertIsNone(memory.get(("p", "m", "v", "ko", "s3")))
        # Audio larger than the whole memory is never kept.
        memory.put(("p", "m", "v", "ko", "huge"), b"\0" * 5000)
        self.assertIsNone(memory.get(("p", "m", "v", "ko", "huge")))
        self.assertLessEqual(memory.bytes, 4000)
        self.assertGreater(server_module.SENTENCE_AUDIO_MAX_ENTRIES, 0)
        self.assertLessEqual(server_module.SENTENCE_AUDIO_MAX_BYTES, 64 * 1024 * 1024)


class VoiceSessionMemoryTests(VoiceNotesHarness, unittest.TestCase):
    """Through real voice sessions: what is said from memory and what never is."""

    def setUp(self) -> None:
        super().setUp()
        self.provider = Provider()
        self.environment = {**self.environment, **GOOGLE}

    def session(self, *said: str, greeting: bool = False):
        async def scenario(socket, listener, say):
            if greeting:
                socket.control({
                    "type": "client.audio_ready", "configuration_id": 1,
                    "generation": listener.generation,
                    "audio_context_state": "running", "sample_rate": 16000,
                })
                await socket.wait_for(
                    lambda item: item["type"] == "turn.done"
                    and item.get("route") == "server_greeting")
                listener.playback_ended(2_000_000_000)
                listener.cooldown_until = 0
                listener.refresh_cooldown()
            for turn_id, text in enumerate(said, 1):
                await say(turn_id, text)

        return self.voice(
            scenario,
            # The real synthesize, its provider request a fake.
            patch("voiney_lab.server.synthesize", new=REAL_SYNTHESIZE),
            patch("voiney_lab.server.voice_providers.synthesize", side_effect=self.provider),
        )

    def test_a_step_read_again_is_said_from_memory(self) -> None:
        socket = self.session("프로토콜 시작해줘", "지금 단계 읽어 줘", "지금 단계 읽어 줘")
        reading = [item for item in socket.for_turn(2, "reply.delta")][0]["speech_text"]
        self.assertEqual(socket.for_turn(3, "reply.delta")[0]["speech_text"], reading)
        made = [text for text in self.provider.texts() if text in reading]
        self.assertTrue(made)
        self.assertEqual(len(made), len(set(made)), self.provider.texts())
        # The second reading sent the same audio, segment for segment.
        self.assertEqual(
            [item["frame_count"] for item in socket.for_turn(3, "audio.segment.start")],
            [item["frame_count"] for item in socket.for_turn(2, "audio.segment.start")])

    def test_a_record_and_the_experimenter_words_read_back_are_made_every_time(self) -> None:
        socket = self.session(
            "프로토콜 시작해줘", "메모해 줘 튜브 라벨 A-170", "메모해 줘 튜브 라벨 A-170",
            "기록해 줘: OD600 0.45", "기록해 줘: OD600 0.45")
        said = {
            turn: socket.for_turn(turn, "reply.delta")[-1]["speech_text"] for turn in (2, 3, 4, 5)
        }
        # The note says where it went; the measurement reads the value back.
        self.assertEqual(said[2], said[3])
        self.assertEqual(said[4], said[5])
        self.assertIn("기록했어요", said[2])
        self.assertIn("영 점 사 오", said[4])
        texts = self.provider.texts()
        self.assertEqual(texts.count(said[2]), 2, texts)
        self.assertEqual(texts.count(said[4]), 2, texts)

    def test_the_emergency_reply_is_made_every_time(self) -> None:
        self.session("불이 났어요! 도와주세요!", "불이 났어요! 도와주세요!")
        emergency = [
            text for text in self.provider.texts()
            if text == clean_speech_text(KOREAN_EMERGENCY_RESPONSE)
        ]
        self.assertEqual(len(emergency), 2, self.provider.texts())

    def test_the_greeting_of_the_next_session_is_said_from_memory(self) -> None:
        first = self.session(greeting=True)
        greeting = [item for item in first.sent if item["type"] == "session.greeting"][0]["text"]
        self.assertEqual(self.provider.texts().count(greeting), 1)
        second = self.session(greeting=True)
        self.assertEqual(
            [item for item in second.sent if item["type"] == "session.greeting"][0]["text"],
            greeting)
        self.assertEqual(self.provider.texts().count(greeting), 1, self.provider.texts())
        self.assertTrue([
            item for item in second.sent
            if item["type"] == "audio.segment.start" and item["turn_id"] == 2_000_000_000])


if __name__ == "__main__":
    unittest.main()
