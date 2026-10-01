"""The Korean language check admits the active protocol's own terms.

With Korean selected, an English-only transcript is a language contradiction
("음성 인식 언어가 불확실합니다") unless it is only technical terms. The terms
it accepted were in-gel's ("ambic", "hplc", "trypsin", ...), so on any other
protocol saying just its own reagent -- "lysozyme" -- was refused. The server
now hands the check the active protocol's STT keyterms; with no protocol the
previous set still applies.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from voiney_lab.curated_protocol import CuratedProtocolSession
from voiney_lab.language import (
    InputLanguagePreference,
    Transcription,
    classify_transcription_language,
)
from voiney_lab.server import ListenerSession, run_turn
from voiney_lab.tools import ToolContext
from voiney_lab.vad import TurnState

from tests.protocol_vocabulary_support import (
    SOURCE_PDF,
    in_gel_fixture,
    miniprep_fixture,
)

#: Every word the check accepted before, all of them in-gel's.
_PREVIOUS_WORDS = (
    "ambic", "ammonium", "bicarbonate", "hplc", "water", "acetonitrile",
    "dtt", "iodoacetamide", "trypsin", "sds", "page", "sds-page", "gel",
    "thermomixer", "rpm", "evotip", "formic", "acid", "lc-ms", "lc", "ms",
    "solution", "a", "b", "plug", "band", "keratin", "contamination",
)


def _check(text: str, keyterms):
    return classify_transcription_language(
        Transcription(text, "en"), InputLanguagePreference.KOREAN,
        known_terms=keyterms,
    )


class ProtocolTermsLanguageCheckTests(unittest.TestCase):
    def test_another_protocols_own_terms_are_terms(self) -> None:
        keyterms = CuratedProtocolSession(miniprep_fixture()).stt_keyterms(
            include_control_terms=True)
        for text in ("lysozyme", "Tris-HCl buffer", "microcentrifuge", "PBS", "RT-PCR"):
            with self.subTest(text=text):
                self.assertFalse(_check(text, keyterms).clarification_required)

    def test_another_protocol_does_not_admit_in_gel_terms(self) -> None:
        keyterms = CuratedProtocolSession(miniprep_fixture()).stt_keyterms(
            include_control_terms=True)
        for text in ("AMBIC", "trypsin", "Evotip"):
            with self.subTest(text=text):
                admission = _check(text, keyterms)
                self.assertTrue(admission.clarification_required)
                self.assertEqual(admission.mismatch_status, "contradiction")

    def test_english_sentences_are_still_a_contradiction(self) -> None:
        keyterms = CuratedProtocolSession(miniprep_fixture()).stt_keyterms(
            include_control_terms=True)
        for text in ("Current step complete.", "Add the lysozyme now please."):
            with self.subTest(text=text):
                self.assertTrue(_check(text, keyterms).clarification_required)

    def test_without_a_protocol_the_previous_set_applies(self) -> None:
        for word in _PREVIOUS_WORDS:
            with self.subTest(word=word):
                self.assertFalse(_check(word, None).clarification_required)
        self.assertTrue(_check("lysozyme", None).clarification_required)

    @unittest.skipUnless(
        SOURCE_PDF.is_file(),
        f"requires the externally licensed Candidate A source PDF at {SOURCE_PDF}",
    )
    def test_in_gel_still_admits_every_word_it_did(self) -> None:
        session = CuratedProtocolSession(in_gel_fixture())
        for index in range(len(session.fixture.steps)):
            session.current_index = index
            keyterms = session.stt_keyterms(include_control_terms=True)
            for word in _PREVIOUS_WORDS:
                with self.subTest(step=index + 1, word=word):
                    self.assertFalse(_check(word, keyterms).clarification_required)


class _Socket:
    def __init__(self) -> None:
        self.text: list[dict] = []

    async def send_text(self, value: str) -> None:
        self.text.append(json.loads(value))

    async def send_bytes(self, value: bytes) -> None:
        pass


class ServerLanguageCheckTests(unittest.TestCase):
    def test_a_protocols_own_reagent_alone_is_not_a_language_mismatch(self) -> None:
        fixture = miniprep_fixture()
        context = ToolContext(Path("/unused/offline-catalog"), None, "ko", "test_only")
        workflow = CuratedProtocolSession(fixture)
        workflow.active = True
        session = ListenerSession(tool_context=context, curated_protocol_session=workflow)
        session.active = True
        session.active_turn_id = 1
        session.next_turn_id = 2
        session.turn_generations[1] = session.generation
        session.accept_configuration(41, "cascade", "ko", fixture.protocol_id)
        session.accepted_input_language = InputLanguagePreference.KOREAN
        session.detector.state = TurnState.PROCESSING
        socket = _Socket()

        async def immediate(function, *args, **kwargs):
            return function(*args, **kwargs)

        with patch(
            "voiney_lab.server.transcribe", return_value=Transcription("lysozyme", "en"),
        ), patch(
            "voiney_lab.server.synthesize", return_value=b"\0\0",
        ), patch(
            "voiney_lab.server.asyncio.to_thread", side_effect=immediate,
        ):
            asyncio.run(run_turn(socket, session, b"\0\0", 1, session.generation))
        kinds = [item["type"] for item in socket.text]
        self.assertNotIn("stt.language_mismatch", kinds)
        self.assertIn("reply.delta", kinds)


if __name__ == "__main__":
    unittest.main()
