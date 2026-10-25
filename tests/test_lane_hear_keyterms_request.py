"""Lane HEAR (2026-10-25), brief item (c): the protocol's terms reach the speech-to-text request.

The person's test had many misheard words. Before comparing providers, check
that the run's own terms (``CuratedProtocolSession.stt_keyterms``: the step
words, the control words and the protocol's vocabulary near the current
step) are in the request each provider gets: ElevenLabs as ``keyterms``
fields (under 50 characters, at most 5 words, at most 100), Google Cloud
as ``speechContexts`` phrases. Contract-tested against a fake transport;
whether a provider honours them is not measured here.
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from voiney_lab.curated_protocol import CuratedProtocolSession
from voiney_lab.replay_turns import _demo_fixture
from voiney_lab.server import ListenerSession, cascade_transcription_context, transcribe_cascade_audio


class _Response:
    ok = True
    status_code = 200
    content = b"{}"
    text = "{}"

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _session() -> ListenerSession:
    workflow = CuratedProtocolSession(_demo_fixture())
    workflow.active = True
    workflow.current_index = 0
    session = ListenerSession(curated_protocol_session=workflow)
    session.start()
    session.accept_configuration(41, "cascade", "ko", workflow.fixture.protocol_id)
    session.curated_protocol_session = workflow
    workflow.active = True
    return session


class KeytermRequestTests(unittest.TestCase):
    def test_elevenlabs_gets_the_runs_terms_as_keyterms(self) -> None:
        session = _session()
        context = cascade_transcription_context(session, audio_origin="ordinary")
        self.assertIn("1단계", context.keyterms)
        protocol_terms = [term for term in context.keyterms if any(c.isascii() and c.isalpha() for c in term)]
        self.assertTrue(protocol_terms, context.keyterms)
        sent = {}

        def post(url, **kwargs):
            sent["fields"] = kwargs["files"]
            return _Response({"text": "다음", "language_code": "kor", "words": []})

        with patch.dict("os.environ", {"VOINEY_LAB_STT_PROVIDER": "elevenlabs", "ELEVENLABS_API_KEY": "test-key"}), patch(
            "voiney_lab.voice_providers.requests.post", side_effect=post,
        ):
            transcribe_cascade_audio(b"\0\0" * 1600, context)
        keyterms = [value[1] for name, value in sent["fields"] if name == "keyterms"]
        expected = [t for t in dict.fromkeys(context.keyterms) if len(t) <= 49 and len(t.split()) <= 5][:100]
        self.assertEqual(keyterms, expected)
        self.assertIn("1단계", keyterms)
        self.assertTrue(set(protocol_terms) & set(keyterms))
        self.assertIn(("language_code", (None, "ko")), sent["fields"])

    def test_google_gets_the_runs_terms_as_phrases(self) -> None:
        session = _session()
        context = cascade_transcription_context(session, audio_origin="ordinary")
        sent = {}

        def post(url, **kwargs):
            sent["json"] = kwargs["json"]
            return _Response({"results": []})

        with patch.dict("os.environ", {"VOINEY_LAB_STT_PROVIDER": "google_cloud",
                                       "VOINEY_LAB_GOOGLE_SPEECH_API_KEY": "test-key"}), patch(
            "voiney_lab.voice_providers.requests.post", side_effect=post,
        ):
            transcribe_cascade_audio(b"\0\0" * 1600, context)
        phrases = sent["json"]["config"]["speechContexts"][0]["phrases"]
        self.assertIn("1단계", phrases)
        self.assertEqual(sent["json"]["config"]["languageCode"], "ko-KR")
        self.assertEqual(len(phrases), len(set(phrases)))
        json.dumps(sent["json"])


if __name__ == "__main__":
    unittest.main()
