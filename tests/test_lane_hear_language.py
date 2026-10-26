"""Lane HEAR, decision 8 (2026-10-25): "음성 인식 언어가 불확실합니다" only for clearly another language.

The person's test (a Korean session, Korean words): the server said "음성
인식 언어가 불확실합니다. 다시 한 번 말씀해 주세요." The metrics show why:
the STT labelled the turn Korean ("ko") and the admission still read a
contradiction -- the transcript had no Hangul, and one English request word
or three Latin tokens were enough. The provider is sent the session's
language, so its label says little; Latin letters for Korean speech are a
misheard turn ("ok so thank you", a romanisation), not another language.

Now the question is asked only when the words are clearly English (or
Vietnamese): three or more Latin words of which two are request words. A
Latin-only transcript short of that is dropped as ``language_unclear`` --
recorded, shown as "잘 안 들렸어요", counted for the spoken notice (decision
3) -- and never answered as a question about the language.
"""

from __future__ import annotations

import unittest

from tests.lane_n_support import VoiceNotesHarness
from voiney_lab.language import classify_korean_admission


class AdmissionTests(unittest.TestCase):
    def test_latin_letters_for_korean_speech_are_unclear_not_a_question(self) -> None:
        for text, detected in (("ok so thank you", "ko"), ("do it", "ko"),
                               ("jigeum dangye wallyo", "ko"), ("next step is", None)):
            with self.subTest(text=text):
                admission = classify_korean_admission(text, detected)
                self.assertFalse(admission.clarification_required)
                self.assertEqual(admission.mismatch_status, "unclear")

    def test_clearly_english_words_are_still_asked_about(self) -> None:
        for text, detected in (("What is the next step please", "ko"), ("Can you tell me the volume", "en")):
            with self.subTest(text=text):
                admission = classify_korean_admission(text, detected)
                self.assertTrue(admission.clarification_required)
                self.assertEqual(admission.mismatch_status, "contradiction")

    def test_short_latin_words_that_never_were_a_contradiction_still_pass(self) -> None:
        # "OK" is said in Korean labs; it was admitted before and still is.
        for text in ("OK", "the next"):
            self.assertIsNone(classify_korean_admission(text, "ko").mismatch_status, text)

    def test_korean_terms_and_protocol_terms_pass_as_before(self) -> None:
        self.assertIsNone(classify_korean_admission("다음 단계 알려줘", "ko").mismatch_status)
        self.assertIsNone(classify_korean_admission("AMBIC", "ko").mismatch_status)
        self.assertIsNone(classify_korean_admission("HPLC water", "ko").mismatch_status)


class UnclearTurnTests(VoiceNotesHarness, unittest.TestCase):
    def test_a_latin_only_turn_is_dropped_and_not_answered(self) -> None:
        async def scenario(socket, listener, say):
            await say(1, "프로토콜 시작해줘")
            await say(5, "ok so thank you")

        socket = self.voice(scenario)
        self.assertEqual(socket.for_turn(5, "session.language_confirmation_required"), [])
        self.assertEqual(socket.for_turn(5, "reply.delta"), [])
        dropped = socket.for_turn(5, "speech.dropped")
        self.assertEqual([(m["reason"], m["notice"]) for m in dropped], [("language_unclear", "잘 안 들렸어요")])


if __name__ == "__main__":
    unittest.main()
