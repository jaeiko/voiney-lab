""""그만"·"정지"·"멈춰" pause; "다시 말해줘" repeats the observation question.

"그만" used to end the protocol session, after which "다시 시작" began again
at step 1, while "멈춰" paused and kept the place and "정지" was not
understood at all. All of them now pause, as "멈춰" did; "종료" and "중지"
still end the session.

While an observation prompt is outstanding, "다시 말해줘", "다시 들려줘" and
"한 번 더 말해줘" say the question again as it was last asked and do not count
toward _OBSERVATION_REPROMPT_LIMIT. What reads as an observation and when the
prompt is let go are unchanged.

Turns go through ``route_curated_runtime_turn``, the boundary the Cascade
runtime calls.
"""

from __future__ import annotations

import unittest

from voiney_lab.curated_protocol import CuratedProtocolAction, CuratedProtocolSession
from voiney_lab.runtime_routing import route_curated_runtime_turn

from tests.protocol_vocabulary_support import SOURCE_PDF, in_gel_fixture, miniprep_fixture

PAUSE_WORDS = ("그만", "그만해", "그만해줘", "정지", "정지해", "정지해줘", "멈춰", "멈춰줘")
REPEAT_REQUESTS = (
    "다시 말해줘", "다시 말해 줘", "다시 말해 주세요", "다시 들려줘",
    "한 번 더 말해줘", "한번 더 말해줘",
)


def _turn(session: CuratedProtocolSession, transcript: str, turn_id: int):
    return route_curated_runtime_turn(
        session, transcript, turn_id=turn_id, language="ko"
    ).plan


class StopWordTests(unittest.TestCase):
    def _session_at_step_3(self) -> CuratedProtocolSession:
        session = CuratedProtocolSession(miniprep_fixture())
        _turn(session, "시작", 1)
        session.current_index = 2
        return session

    def test_the_stop_words_pause_and_keep_the_place(self) -> None:
        for word in PAUSE_WORDS:
            with self.subTest(word=word):
                session = self._session_at_step_3()
                paused = _turn(session, word, 2)
                self.assertEqual(paused.action, CuratedProtocolAction.PAUSE)
                self.assertEqual(paused.intent_kind, "pause_workflow")
                self.assertTrue(session.active)
                self.assertEqual(session.workflow_status, "paused")
                resumed = _turn(session, "다시 시작", 3)
                self.assertEqual(resumed.action, CuratedProtocolAction.RESUME)
                self.assertEqual(session.current_index, 2)
                self.assertEqual(resumed.step_label, "3")

    def test_ending_the_session_still_takes_an_explicit_word(self) -> None:
        for word in ("종료", "중지", "프로토콜 종료"):
            with self.subTest(word=word):
                session = self._session_at_step_3()
                stopped = _turn(session, word, 2)
                self.assertEqual(stopped.action, CuratedProtocolAction.STOP)
                self.assertFalse(session.active)

    def test_a_sentence_that_mentions_stopping_is_not_a_pause(self) -> None:
        for sentence in ("정지 버튼이 뭐야?", "반응을 정지시키는 시약이 뭐야?", "그만큼 넣으면 돼?"):
            with self.subTest(sentence=sentence):
                session = self._session_at_step_3()
                plan = _turn(session, sentence, 2)
                self.assertNotEqual(plan.action, CuratedProtocolAction.PAUSE)
                self.assertNotEqual(session.workflow_status, "paused")


@unittest.skipUnless(
    SOURCE_PDF.is_file(),
    f"requires the externally licensed Candidate A source PDF at {SOURCE_PDF}",
)
class ObservationRepeatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = in_gel_fixture()
        cls.index = {step.source_label: i for i, step in enumerate(cls.fixture.steps)}

    def _prompted_at(self, label: str):
        session = CuratedProtocolSession(self.fixture)
        session.active = True
        session._workflow_status = "active"
        session.current_index = self.index[label]
        opened = _turn(session, "현재 단계를 완료했어요", 1)
        self.assertEqual(opened.intent_kind, "observation_confirmation_required")
        return session, opened

    def test_a_repeat_request_says_the_question_again_without_counting(self) -> None:
        for label in ("7", "9", "20"):
            for request in REPEAT_REQUESTS:
                with self.subTest(step=label, request=request):
                    session, opened = self._prompted_at(label)
                    for turn_id in (2, 3, 4):
                        repeated = _turn(session, request, turn_id)
                        self.assertEqual(
                            repeated.intent_kind, "observation_confirmation_repeated"
                        )
                        self.assertEqual(repeated.display_text, opened.display_text)
                        self.assertEqual(repeated.speech_text, opened.speech_text)
                        self.assertFalse(repeated.state_changed)
                        pending = session.pending_observation_confirmation
                        self.assertIsNotNone(pending)
                        self.assertEqual(pending.reprompt_count, 0)
                    self.assertEqual(session.current_index, self.index[label])

    def test_after_a_re_ask_the_yes_no_question_is_the_one_repeated(self) -> None:
        session, _ = self._prompted_at("7")
        reasked = _turn(session, "음 글쎄", 2)
        self.assertEqual(reasked.intent_kind, "observation_confirmation_reasked")
        repeated = _turn(session, "다시 들려줘", 3)
        self.assertEqual(repeated.display_text, reasked.display_text)
        self.assertEqual(repeated.speech_text, reasked.speech_text)
        self.assertEqual(session.pending_observation_confirmation.reprompt_count, 1)

    def test_the_release_still_comes_after_two_counted_re_asks(self) -> None:
        session, _ = self._prompted_at("7")
        kinds = [
            _turn(session, reply, turn_id).intent_kind
            for turn_id, reply in enumerate((
                "음 글쎄", "다시 들려줘", "결과는 그렇게 됐어",
                "한 번 더 말해줘", "잘 모르겠네",
            ), 2)
        ]
        self.assertEqual(kinds, [
            "observation_confirmation_reasked",
            "observation_confirmation_repeated",
            "observation_confirmation_reasked",
            "observation_confirmation_repeated",
            "observation_confirmation_released",
        ])
        self.assertIsNone(session.pending_observation_confirmation)
        self.assertEqual(session.current_index, self.index["7"])
        self.assertEqual(session.endpoint_observations(), {})

    def test_a_yes_after_a_repeat_is_read_as_before(self) -> None:
        session, _ = self._prompted_at("7")
        _turn(session, "다시 말해줘", 2)
        confirmed = _turn(session, "네", 3)
        self.assertEqual(confirmed.intent_kind, "pending_observation_confirmed")
        self.assertEqual(session.current_index, self.index["7"] + 1)

    def test_other_unread_replies_still_count(self) -> None:
        session, _ = self._prompted_at("7")
        reasked = _turn(session, "다시 알려줘", 2)
        self.assertEqual(reasked.intent_kind, "observation_confirmation_reasked")
        self.assertEqual(session.pending_observation_confirmation.reprompt_count, 1)


if __name__ == "__main__":
    unittest.main()
