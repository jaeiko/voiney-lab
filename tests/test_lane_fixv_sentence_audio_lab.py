"""Lane FIXV decision 3 (2026-10-24): kept sentence audio is the lab's own.

Lane VOX keeps a guidance sentence's audio in the server's memory, keyed by
provider, model, voice, language and sentence. The memory was one for every
lab on the server, so a sentence another lab had made came back faster, and
the response time could tell one lab that another had used the same words.
The organisation (lab) whose request or voice connection is speaking now is
part of the key: each lab makes its own sentences once and reuses only them.

The real ``server.synthesize`` runs; only the provider request under it is a
fake. No provider is called.
"""

from __future__ import annotations

import dataclasses
import unittest
from unittest.mock import patch

import tests.test_lane_vox_sentence_audio_cache as vox_cache
import voiney_lab.server as server_module
from tests.identity_support import development_principal
from tests.lane_n_support import STEPS
from tests.protocol_vocabulary_support import build_fixture
from tests.test_lane_vox_sentence_audio_cache import GOOGLE, REAL_SYNTHESIZE, Provider
from voiney_lab.workspace_store import WorkspaceSettings, initialize_workspace_store

LAB_A = development_principal()
LAB_B = dataclasses.replace(
    LAB_A, principal_id="lab-b-researcher", subject="dev:lab-b-researcher",
    organization_id="tenant-lab-b",
)
#: Lab B's own copy of the fictional wash: the same words, its own protocol.
LAB_B_PROTOCOL = "lane-fixv-lab-b-wash"


class KeyTests(unittest.TestCase):

    def setUp(self) -> None:
        self.provider = Provider()
        for item in (
            patch("voiney_lab.server.synthesize", new=REAL_SYNTHESIZE),
            patch("voiney_lab.server.voice_providers.synthesize", side_effect=self.provider),
            patch.dict("os.environ", GOOGLE),
        ):
            item.start()
            self.addCleanup(item.stop)

    def kept(self, text: str, principal) -> bytes:
        token = server_module._REQUEST_PRINCIPAL.set(principal)
        try:
            with server_module.sentence_audio_kept():
                return server_module.synthesize(text, "ko")
        finally:
            server_module._REQUEST_PRINCIPAL.reset(token)

    def test_another_lab_makes_the_same_sentence_itself(self) -> None:
        sentence = "현재 1단계입니다. 안내를 화면에 표시했습니다."
        self.kept(sentence, LAB_A)
        self.kept(sentence, LAB_A)
        self.assertEqual(self.provider.texts().count(sentence), 1)
        # Lab B is not handed lab A's audio: its own request, then its own memory.
        self.kept(sentence, LAB_B)
        self.assertEqual(self.provider.texts().count(sentence), 2)
        self.kept(sentence, LAB_B)
        self.kept(sentence, LAB_A)
        self.assertEqual(self.provider.texts().count(sentence), 2)

    def test_a_server_with_no_identity_keeps_one_memory(self) -> None:
        # No workspace: one development tenant, as before.
        sentence = "현재 2단계입니다."
        self.kept(sentence, None)
        self.kept(sentence, None)
        self.assertEqual(self.provider.texts().count(sentence), 1)
        self.kept(sentence, LAB_A)
        self.assertEqual(self.provider.texts().count(sentence), 2)

    def test_the_key_holds_the_lab_and_no_other_identity(self) -> None:
        self.kept("현재 3단계입니다.", LAB_A)
        (key,) = server_module.SENTENCE_AUDIO._audio
        self.assertEqual(key[0], LAB_A.organization_id)
        for private in (LAB_A.principal_id, LAB_A.subject, LAB_A.display_name):
            self.assertNotIn(private, key)


class VoiceSessionLabTests(vox_cache.VoiceSessionMemoryTests):
    """Through real voice sessions of two labs on one server."""

    def setUp(self) -> None:
        super().setUp()
        store = initialize_workspace_store(WorkspaceSettings(True, self.workspace_dir))
        store.bootstrap_principal(LAB_B)
        store.bind_resource(LAB_B, "protocol_catalog", LAB_B_PROTOCOL)
        store.close()
        self.lab_fixture = {
            LAB_A.organization_id: self.fixture,
            LAB_B.organization_id: build_fixture(
                protocol_id=LAB_B_PROTOCOL, title="Fictional wash", steps=STEPS,
                materials=("Solution A", "Solution B"), equipment=("Speedvac",)),
        }

    def greeting_as(self, principal):
        resolver = type("Resolver", (), {"resolve": lambda _self, _header: principal})()
        self.principal = principal
        self.fixture = self.lab_fixture[principal.organization_id]
        protocol = self.fixture.draft.protocol.protocol_id
        with patch("voiney_lab.server._identity_resolver", return_value=resolver):
            socket = self.voice_with_greeting(protocol)
        greetings = [item for item in socket.sent if item["type"] == "session.greeting"]
        self.assertEqual(len(greetings), 1, [item for item in socket.sent if item["type"] == "error"])
        return greetings[0]["text"]

    def voice_with_greeting(self, protocol: str):
        async def scenario(socket, listener, _say):
            socket.control({
                "type": "client.audio_ready", "configuration_id": 1,
                "generation": listener.generation,
                "audio_context_state": "running", "sample_rate": 16000,
            })
            await socket.wait_for(
                lambda item: item["type"] == "turn.done"
                and item.get("route") == "server_greeting")

        return self.voice(
            scenario,
            patch("voiney_lab.server.synthesize", new=REAL_SYNTHESIZE),
            patch("voiney_lab.server.voice_providers.synthesize", side_effect=self.provider),
            start={"protocol_id": protocol},
        )

    def test_each_lab_makes_the_greeting_once_and_says_only_its_own(self) -> None:
        greeting = self.greeting_as(LAB_A)
        self.assertEqual(self.provider.texts().count(greeting), 1)
        self.assertEqual(self.greeting_as(LAB_B), greeting)
        self.assertEqual(self.provider.texts().count(greeting), 2, self.provider.texts())
        self.assertEqual(self.greeting_as(LAB_A), greeting)
        self.assertEqual(self.greeting_as(LAB_B), greeting)
        self.assertEqual(self.provider.texts().count(greeting), 2, self.provider.texts())

    # The parent's own cases run in their own module.
    test_a_step_read_again_is_said_from_memory = None
    test_a_record_and_the_experimenter_words_read_back_are_made_every_time = None
    test_the_emergency_reply_is_made_every_time = None
    test_the_greeting_of_the_next_session_is_said_from_memory = None


if __name__ == "__main__":
    unittest.main()
