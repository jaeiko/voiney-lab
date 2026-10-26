"""Lane HEAR, decision 5 (2026-10-25): the voice chosen before a start is the voice of that run.

The person's test: the start wizard's female voice was chosen and saved, yet
the guidance spoke in the provider's default male voice. The connection read
the choice once, when it opened; the page keeps one connection open across
starts, so a voice saved after it opened was never used. Each start now reads
the saved choice again, speaks with it and says which voice in
``session.ready``.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

import voiney_lab.server as server_module
from tests.lane_n_support import VoiceNotesHarness

CHOSEN = "ko-KR-Chirp3-HD-Aoede"


class ChosenVoiceAtStartTests(VoiceNotesHarness, unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.environment = {
            **self.environment,
            "VOINEY_LAB_TTS_PROVIDER": "google_cloud",
            "VOINEY_LAB_TTS_VOICE": "",
        }

    def _voice_run(self, choice_after_connect: str):
        calls = {"count": 0}
        real_load = server_module._load_experimenter_settings

        def load():
            calls["count"] += 1
            settings = real_load()
            # The first read is the connection's own, when the page opened it;
            # the person picks the voice in the wizard after that.
            if calls["count"] > 1:
                settings = {**settings, "agent_voice": choice_after_connect}
            return settings

        heard: list[str | None] = []

        async def scenario(socket, listener, say):
            heard.append(listener.tts_voice)

        socket = self.voice(scenario, patch.object(
            server_module, "_load_experimenter_settings", side_effect=load))
        return socket, heard

    def test_a_voice_saved_after_the_connection_opened_speaks_the_next_start(self) -> None:
        socket, heard = self._voice_run(CHOSEN)
        ready = next(item for item in socket.sent if item["type"] == "ready")
        self.assertEqual(ready["voice_profile"]["agent_voice"], "default")
        session_ready = next(item for item in socket.sent if item["type"] == "session.ready")
        self.assertEqual(session_ready["agent_voice"], CHOSEN)
        self.assertEqual(session_ready["voice_id"], CHOSEN)
        self.assertEqual(heard, [CHOSEN], "the run's speech uses the chosen voice")

    def test_a_start_with_no_choice_speaks_the_providers_voice(self) -> None:
        socket, heard = self._voice_run("default")
        session_ready = next(item for item in socket.sent if item["type"] == "session.ready")
        self.assertEqual(session_ready["agent_voice"], "default")
        self.assertEqual(heard, [None])


if __name__ == "__main__":
    unittest.main()
