"""Lane XO front-rule decisions 3, 4, 5, 8 and 9 (2026-10-05).

From a person's voice test on 2026-10-05 (router gpt-6-luna, STT ElevenLabs
scribe_v2):

* 3 -- "포즈 해줘", "포즈", "잠깐만요", "잠깐만예" and the ways STT writes it
  ("잠깐만 얘", "잠깐만, 얘", "잠깐만래") pause, matched whole; "포즈" or
  "잠깐" inside other words do not.
* 4 -- the pause and resume words go to the STT provider as key terms, but a
  transcript made of them is not taken for a key-term dump.
* 5 -- "끝났어", "다 끝났어" and "끝" said alone are asked about by the front
  rules ("N단계 완료하셨나요?"), whichever model the router runs.
* 8 -- while paused, a word one letter (jamo) from a resume word ("제개")
  is asked about, "다시 시작할까요?", and a yes resumes; a word with a digit
  ("3개") is not. The pause reply names the word STT hears well.
* 9 -- particles follow the final consonant of what they attach to.
"""

from __future__ import annotations

import asyncio
import unittest

from tests.protocol_vocabulary_support import build_fixture
from voiney_lab.curated_protocol import (
    FRONT_RULES,
    STT_CONTROL_KEYTERMS,
    CuratedProtocolAction,
    CuratedProtocolSession,
    _has_final_consonant,
    _josa,
    _near_resume_word,
)
from voiney_lab.language import Transcription, classify_input_event
from voiney_lab.llm_router import LlmRouterSettings, route_turn_with_llm_router
from voiney_lab.runtime_routing import route_curated_runtime_turn
from voiney_lab.server import keyterm_dump_terms

STEPS = (
    "1 Wash the column with 500 µL of Buffer 1 and spin it for 1 minute.",
    "2 Prepare a solution of 1.5mg/mL of DTT and 10mg/mL of iodoacetamide.",
    "3 Remove and discard the supernatant.",
    "4 Add 25uL of the above trypsin solution and wait for 10 minutes.",
    "5 Store the tube at 4 degrees.",
)


def _fixture():
    return build_fixture(
        protocol_id="lane-xo-front-test", title="Fictional lane XO protocol",
        steps=STEPS, materials=("DTT Sigma-Aldrich Catalog #D0632",),
    )


def _session(step_index: int = 2, *, paused: bool = False) -> tuple[CuratedProtocolSession, int]:
    session = CuratedProtocolSession(_fixture())
    session.activate_configured()
    session.plan("시작", turn_id=1, language="ko", configuration_id=1, generation=1)
    session.current_index = step_index
    turn = 2
    if paused:
        session.plan("잠깐만", turn_id=turn, language="ko", configuration_id=1, generation=1)
        turn += 1
    return session, turn


def _turn(session, said, turn_id, language="ko"):
    return route_curated_runtime_turn(
        session, said, turn_id=turn_id, language=language,
        configuration_id=1, generation=1,
    ).plan


def _router(session, said, turn_id):
    def no_model():
        raise AssertionError("the model was asked")

    async def rules():
        raise AssertionError("the rules' fallback was used")

    return asyncio.run(route_turn_with_llm_router(
        session, said, turn_id=turn_id, language="ko",
        settings=LlmRouterSettings(enabled=True), client_factory=no_model,
        rule_route=rules, configuration_id=1, generation=1,
    ))


class PauseWordTests(unittest.TestCase):
    WORDS = ("pause 해줘", "포즈 해줘", "포즈", "잠깐만요", "잠깐만예", "잠깐만 얘", "잠깐만, 얘", "잠깐만래")

    def test_each_word_pauses_as_a_front_rule(self) -> None:
        for said in self.WORDS:
            with self.subTest(said=said):
                session, turn = _session()
                plan = session.front_plan(said, turn_id=turn, language="ko",
                                          configuration_id=1, generation=1)
                self.assertEqual(session.last_front_rule, "pause")
                self.assertIs(plan.action, CuratedProtocolAction.PAUSE)
                self.assertEqual(session._pause_state, "paused")

    def test_the_words_inside_other_words_are_not_widened(self) -> None:
        for said in ("포즈가 뭐야", "요가 포즈 알려줘", "잠깐 이 단계 왜 해?", "잠깐만 얘기 좀 하자"):
            with self.subTest(said=said):
                session, turn = _session()
                session.front_plan(said, turn_id=turn, language="ko",
                                   configuration_id=1, generation=1)
                self.assertNotEqual(session.last_front_rule, "pause")
                self.assertNotEqual(session._pause_state, "paused")

    def test_the_pause_reply_names_what_stt_hears_well(self) -> None:
        session, turn = _session()
        plan = _turn(session, "포즈", turn)
        self.assertEqual(plan.speech_text, "일시정지했어요. '다시 시작'이라고 하시면 이어서 할게요.")


class ControlKeytermTests(unittest.TestCase):
    def test_the_control_words_are_sent_as_key_terms(self) -> None:
        self.assertEqual(
            STT_CONTROL_KEYTERMS, ("잠깐", "멈춰", "정지", "일시정지", "스톱", "재개", "다시 시작"))
        session, _turn_id = _session()
        sent = session.stt_keyterms(include_control_terms=True)
        for term in STT_CONTROL_KEYTERMS:
            self.assertIn(term, sent)
        self.assertNotIn("재개", session.stt_keyterms())

    def test_a_string_of_control_words_is_not_a_key_term_dump(self) -> None:
        session, _turn_id = _session()
        sent = session.stt_keyterms(include_control_terms=True)
        said = Transcription("잠깐 멈춰 정지 스톱", "ko")
        # Counted as key terms, it would have been dropped as the provider
        # reading back its prompt.
        self.assertFalse(classify_input_event(said, keyterms=sent).accepted)
        self.assertTrue(classify_input_event(said, keyterms=keyterm_dump_terms(sent)).accepted)
        self.assertEqual(
            set(sent) - set(keyterm_dump_terms(sent)), set(STT_CONTROL_KEYTERMS))


class ShortCompletionTests(unittest.TestCase):
    def test_a_short_completion_asks_about_the_current_step(self) -> None:
        self.assertIn("short_completion", FRONT_RULES)
        for said in ("끝났어", "다 끝났어", "끝", "끝났어요"):
            with self.subTest(said=said):
                session, turn = _session(3)
                plan = session.front_plan(said, turn_id=turn, language="ko",
                                          configuration_id=1, generation=1)
                self.assertEqual(session.last_front_rule, "short_completion")
                self.assertIs(plan.action, CuratedProtocolAction.CLARIFY_COMPLETION)
                self.assertEqual(plan.speech_text, "4단계 완료하셨나요?")
                self.assertEqual(session.current_index, 3)
                yes = _turn(session, "네", turn + 1)
                self.assertTrue(yes.state_changed)
                self.assertEqual(session.current_index, 4)

    def test_the_router_never_asks_the_model(self) -> None:
        session, turn = _session(3)
        outcome = _router(session, "끝났어", turn)
        self.assertEqual(outcome.handled_by, "front:short_completion")
        self.assertFalse(outcome.model_called)

    def test_only_the_whole_words(self) -> None:
        for said in ("끝났어?", "언제 끝나", "끝까지 해", "다 끝났어 근데 색이 남았어"):
            with self.subTest(said=said):
                session, turn = _session(3)
                session.front_plan(said, turn_id=turn, language="ko",
                                   configuration_id=1, generation=1)
                self.assertNotEqual(session.last_front_rule, "short_completion")

    def test_not_while_paused_or_before_the_start(self) -> None:
        session, turn = _session(3, paused=True)
        session.front_plan("끝났어", turn_id=turn, language="ko", configuration_id=1, generation=1)
        self.assertNotEqual(session.last_front_rule, "short_completion")
        idle = CuratedProtocolSession(_fixture())
        idle.activate_configured()
        idle.front_plan("끝났어", turn_id=1, language="ko", configuration_id=1, generation=1)
        self.assertNotEqual(idle.last_front_rule, "short_completion")


class NearResumeWordTests(unittest.TestCase):
    def test_one_letter_away_but_never_a_digit_or_the_word_itself(self) -> None:
        for said in ("제개", "재게", "재게해", "다시 시잭", "다시 시잭해"):
            with self.subTest(said=said):
                self.assertTrue(_near_resume_word(said))
        # "재개헤" holds the word itself: the resume rule takes it.
        for said in ("재개", "재개헤", "다시 시작해줘", "3개", "재개 3번", "체크해", "다음 단계", "네", ""):
            with self.subTest(said=said):
                self.assertFalse(_near_resume_word(said))

    def test_while_paused_a_near_miss_is_asked_and_a_yes_resumes(self) -> None:
        session, turn = _session(paused=True)
        plan = _turn(session, "제개", turn)
        self.assertEqual(plan.speech_text, "다시 시작할까요?")
        self.assertEqual(session.last_front_rule, "stt_unreliable")
        self.assertFalse(plan.state_changed)
        self.assertEqual(session._pause_state, "paused")
        resumed = _turn(session, "네", turn + 1)
        self.assertIs(resumed.action, CuratedProtocolAction.RESUME)
        self.assertNotEqual(session._pause_state, "paused")

    def test_a_no_keeps_the_pause(self) -> None:
        session, turn = _session(paused=True)
        _turn(session, "제개", turn)
        _turn(session, "아니", turn + 1)
        self.assertEqual(session._pause_state, "paused")

    def test_a_count_is_not_taken_for_resume(self) -> None:
        session, turn = _session(paused=True)
        plan = _turn(session, "3개", turn)
        self.assertNotEqual(plan.speech_text, "다시 시작할까요?")
        self.assertEqual(session._pause_state, "paused")

    def test_only_while_paused(self) -> None:
        session, turn = _session()
        plan = _turn(session, "제개", turn)
        self.assertNotEqual(plan.speech_text, "다시 시작할까요?")

    def test_the_router_never_asks_the_model(self) -> None:
        session, turn = _session(paused=True)
        outcome = _router(session, "제개", turn)
        self.assertEqual(outcome.handled_by, "front:stt_unreliable")
        self.assertFalse(outcome.model_called)


class ParticleTests(unittest.TestCase):
    def test_the_particle_follows_the_final_consonant(self) -> None:
        cases = {
            "AMBIC": True, "DTT": False, "HPLC": False, "acetonitrile": True,
            "trypsin solution": True, "iodoacetamide": False, "Solution A": False,
            "25mM": True, "200 µL": False, "6ng/uL": False, "10%": False,
            "용액": True, "버퍼": False, "Buffer 1": True, "Buffer 2": False,
        }
        for word, final in cases.items():
            with self.subTest(word=word):
                self.assertIs(_has_final_consonant(word), final)
        self.assertEqual(_josa("AMBIC", "을", "를"), "을")
        self.assertEqual(_josa("solution A", "을", "를"), "를")


if __name__ == "__main__":
    unittest.main()
