"""Korean first: no source sentence is read aloud in English (lane SAY, decision 1, 2026-10-25).

The person's voice test of 2026-10-25 on the uploaded in-gel PDF: "다음 단계
뭐야" read step 2 in English ("2 Prepare two wash solutions: Solution A: …")
on the speaker and the screen, while step 1 was Korean. Step 2's stored
machine Korean had been refused by the check ("2 parts" read as "2부피"), and
the preview fell back to the source.

Human decision 1: the words said and shown are Korean first wherever a source
sentence is carried. A sentence with no Korean reading is translated in the
turn by the translation role and used only when it passes the check; with
none, "이 문장은 아직 번역되지 않았어요. 원문을 화면에 띄울게요." is said and the
source is shown. The English is read aloud only on "원문 그대로 읽어 줘".

Measured over every step of the uploaded protocols on a copy of the
development server's data (``~/reports/say/tools/english_sweep.py``): Korean
replies that said a source sentence in English went from 40 / 21 / 389 / 23
(protein-experiment, headspace, PMC8250384, in-gel) to 0. Provider calls here
are fake.
"""

from __future__ import annotations

import logging
import unittest
from dataclasses import replace

import tests.test_reader_speech as reader
from tests.lane_n_support import notes_fixture
from tests.protocol_vocabulary_support import miniprep_fixture
from voiney_lab import server as server_module
from voiney_lab.curated_protocol import (
    SAFETY_SOURCE_READ_LEAD,
    SOURCE_READ_ALOUD_INTENT,
    UNTRANSLATED_SHOWN,
    UNTRANSLATED_SPOKEN,
    UNTRANSLATED_SPOKEN_AGAIN,
    CuratedProtocolAction,
    CuratedProtocolSession,
)
from voiney_lab.runtime_routing import route_curated_runtime_turn

STEP_TWO = "2 Wash the pieces with 500 µL of Solution A for 10 min."
STEP_TWO_KO = "2단계: Solution A 500 µL로 조각을 10 min 동안 씻어요."
CONDITION = "until the band is clear"
CONDITION_KO = "밴드가 투명해질 때까지"


def _session(fixture=None, index: int = 0) -> CuratedProtocolSession:
    session = CuratedProtocolSession(fixture or notes_fixture())
    session.activate_configured()
    route_curated_runtime_turn(session, "프로토콜 시작해줘", turn_id=1, language="ko",
                               configuration_id=1, generation=1)
    session._notebook_question = None
    session.current_index = index
    return session


def _plan(session: CuratedProtocolSession, words: str, turn_id: int = 2):
    return route_curated_runtime_turn(session, words, turn_id=turn_id, language="ko",
                                      configuration_id=1, generation=1).plan


def _clear_spot_readings() -> None:
    server_module._SPOT_READINGS.clear()
    server_module._SPOT_ATTEMPTED.clear()


class SessionReadingTests(unittest.TestCase):
    """``CuratedProtocolSession.korean_first``: what the reply says, no call made."""

    def test_the_next_step_without_korean_is_not_read_in_english(self) -> None:
        session = _session()
        plan = _plan(session, "다음 단계 뭐야")
        self.assertIn(STEP_TWO, plan.speech_text)  # the rules quote the source
        reading = session.korean_first(plan, language="ko")
        said = reading.plan.speech_text
        self.assertNotIn("Wash the pieces", said)
        self.assertEqual(
            said, f"다음 단계 미리보기 · 2단계: {UNTRANSLATED_SPOKEN} 워크플로는 현재 1단계에 그대로 있습니다.")
        self.assertEqual([quote.key for quote in reading.untranslated], ["step-2/current_step"])
        self.assertEqual(reading.untranslated[0].text, STEP_TWO)
        # The screen keeps the source and says why.
        self.assertIn(STEP_TWO, reading.plan.display_text)
        self.assertTrue(reading.plan.display_text.startswith(UNTRANSLATED_SHOWN))
        self.assertFalse(reading.plan.state_changed)

    def test_a_stored_korean_is_read_with_its_label_said_once(self) -> None:
        fixture = replace(notes_fixture(), machine_localizations={"step-2/current_step": STEP_TWO_KO})
        session = _session(fixture)
        # The rules already read a stored Korean; a quote of it is left alone.
        plan = _plan(session, "다음 단계 뭐야")
        reading = session.korean_first(plan, language="ko")
        self.assertEqual(reading.untranslated, ())
        self.assertIn("2단계: Solution A 500 µL로", reading.plan.speech_text)
        self.assertNotIn("2단계: 2단계", reading.plan.speech_text)

    def test_a_reading_made_in_the_turn_takes_the_place_of_the_source(self) -> None:
        session = _session()
        plan = _plan(session, "다음 단계 뭐야")
        reading = session.korean_first(
            plan, language="ko", readings={"step-2/current_step": STEP_TWO_KO})
        self.assertEqual(reading.readings_used, ("step-2/current_step",))
        self.assertEqual(reading.untranslated, ())
        self.assertEqual(
            reading.plan.speech_text,
            "다음 단계 미리보기 · 2단계: Solution A 500 µL로 조각을 10 min 동안 씻어요. "
            "워크플로는 현재 1단계에 그대로 있습니다.")
        self.assertIn("Solution A 500 µL로", reading.plan.display_text)
        self.assertEqual(reading.plan.translation_status, "model_assisted_unreviewed")

    def test_a_quoted_condition_is_said_as_not_translated_and_the_next_sentence_stays(self) -> None:
        session = _session(index=4)
        plan = _plan(session, "다 했어")
        self.assertIn(CONDITION, plan.speech_text)
        reading = session.korean_first(plan, language="ko")
        said = reading.plan.speech_text
        self.assertNotIn(CONDITION, said)
        self.assertNotIn("“", said)
        self.assertIn(UNTRANSLATED_SPOKEN, said)
        self.assertTrue(said.endswith("그 관찰 결과를 말씀해 주셔야 완료를 기록할 수 있어요."), said)
        quote = reading.untranslated[0]
        self.assertEqual((quote.kind, quote.text), ("phrase", CONDITION))
        self.assertTrue(quote.key.startswith("span:"))
        translated = session.korean_first(plan, language="ko", readings={quote.key: CONDITION_KO})
        self.assertIn(f"“{CONDITION_KO}”", translated.plan.speech_text)

    def test_a_quoting_tail_goes_with_the_quote(self) -> None:
        session = _session()
        plan = replace(
            _plan(session, "지금 단계 뭐야"),
            speech_text=f"원문에는 ‘{STEP_TWO}’라고 적혀 있어요. 타이머를 시작하려면 말씀해 주세요.")
        said = session.korean_first(plan, language="ko").plan.speech_text
        self.assertEqual(said, f"{UNTRANSLATED_SPOKEN} 타이머를 시작하려면 말씀해 주세요.")

    def test_a_second_untranslated_sentence_is_said_shorter(self) -> None:
        session = _session()
        steps = session.fixture.steps
        plan = replace(
            _plan(session, "지금 단계 뭐야"),
            speech_text=f"1단계: {steps[0].instruction_source_text} 2단계: {STEP_TWO}")
        said = session.korean_first(plan, language="ko").plan.speech_text
        self.assertEqual(said, f"1단계: {UNTRANSLATED_SPOKEN} 2단계: {UNTRANSLATED_SPOKEN_AGAIN}")

    def test_names_kept_in_english_are_not_quotes(self) -> None:
        session = _session()
        plan = replace(_plan(session, "지금 단계 뭐야"),
                       speech_text="Solution A 500 µL로 조각을 씻어요. speedvac에서 말려요.")
        reading = session.korean_first(plan, language="ko")
        self.assertIs(reading.plan, plan)

    def test_english_a_korean_reading_keeps_is_left_in_it(self) -> None:
        formula = "Wash the pieces with Solution A"
        fixture = replace(notes_fixture(), machine_localizations={
            "step-2/current_step": f"2단계: 공식: {formula} 를 따라요."})
        session = _session(fixture)
        plan = replace(_plan(session, "지금 단계 뭐야"), speech_text=f"공식: {formula} 를 따라요.")
        self.assertIs(session.korean_first(plan, language="ko").plan, plan)

    def test_the_source_is_read_only_when_asked_for(self) -> None:
        session = _session(index=1)
        plan = _plan(session, "원문 그대로 읽어 줘")
        self.assertIs(plan.action, CuratedProtocolAction.FULL_DETAIL)
        self.assertEqual(plan.intent_kind, SOURCE_READ_ALOUD_INTENT)
        self.assertEqual(plan.speech_text, STEP_TWO)
        self.assertFalse(plan.state_changed)
        self.assertIs(session.korean_first(plan, language="ko").plan, plan)
        for words in ("원문 읽어 줘", "영어로 읽어 줘", "이 단계 원문 그대로 읽어줘"):
            with self.subTest(words=words):
                self.assertEqual(_plan(_session(index=1), words).intent_kind, SOURCE_READ_ALOUD_INTENT)
        # "이 단계 읽어줘" stays the Korean reading.
        self.assertNotEqual(_plan(_session(index=1), "이 단계 읽어줘").intent_kind, SOURCE_READ_ALOUD_INTENT)

    def test_an_english_turn_is_left_alone(self) -> None:
        session = _session()
        plan = _plan(session, "다음 단계 뭐야")
        self.assertIs(session.korean_first(plan, language="en").plan, plan)

    def test_a_safety_warning_lead_says_it_and_the_english_goes(self) -> None:
        session = _session()
        plan = replace(_plan(session, "지금 단계 뭐야"),
                       speech_text=f"{SAFETY_SOURCE_READ_LEAD} {STEP_TWO}")
        said = session.korean_first(plan, language="ko").plan.speech_text
        self.assertEqual(said, SAFETY_SOURCE_READ_LEAD)
        # With a reading, the lead goes and the Korean is said.
        reading = session.korean_first(plan, language="ko", readings={"step-2/current_step": STEP_TWO_KO})
        self.assertEqual(reading.plan.speech_text, "2단계: Solution A 500 µL로 조각을 10 min 동안 씻어요.")


class ServerTurnTests(unittest.TestCase):
    """Through ``server.run_turn``: the in-turn translation and what is said."""

    def setUp(self) -> None:
        _clear_spot_readings()
        self.addCleanup(_clear_spot_readings)
        self.fixture = notes_fixture()

    def listener(self, index: int = 0, *, model_roles: bool):
        return reader._listener(self.fixture, index, model_roles=model_roles)

    def test_without_the_translation_role_the_sentence_is_said_as_not_translated(self) -> None:
        session = self.listener(model_roles=False)
        before = session.curated_protocol_session.state()
        spoken, reply, state = reader._turn(session, "다음 단계 뭐야", 1)
        self.assertEqual(len(spoken), 1)
        self.assertNotIn("Wash the pieces", spoken[0])
        self.assertIn(UNTRANSLATED_SPOKEN, spoken[0])
        self.assertIn(STEP_TWO, reply["text"])
        self.assertIn(UNTRANSLATED_SHOWN, reply["text"])
        self.assertEqual(state["revision"], before["revision"])
        self.assertEqual(state["spoken_summary"], spoken[0])

    def test_a_checked_reading_is_made_in_the_turn_and_kept(self) -> None:
        model = reader._FakeModel(STEP_TWO_KO)
        session = self.listener(model_roles=True)
        spoken, reply, state = reader._turn(session, "다음 단계 뭐야", 1, model)
        self.assertEqual(len(model.calls), 1)
        self.assertTrue(spoken[0].startswith("자동 번역입니다. 다음 단계 미리보기 · 2단계: Solution A 500 µL로"),
                        spoken[0])
        self.assertNotIn("Wash the pieces", spoken[0])
        self.assertIn("Solution A 500 µL로 조각을", reply["text"])
        self.assertEqual(reply["translation_status"], "model_assisted_unreviewed")
        # The screen's step card reads it as a stored machine translation.
        curated = session.curated_protocol_session
        self.assertEqual(curated.fixture.machine_localizations["step-2/current_step"], STEP_TWO_KO)
        # Moved to step 2 and asked again: no second call, no second "자동 번역입니다."
        curated.current_index = 1
        spoken, _reply, _state = reader._turn(session, "이 단계 읽어줘", 2, model)
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(spoken, [STEP_TWO_KO])

    def test_a_refused_reading_is_not_said_and_not_asked_for_again(self) -> None:
        model = reader._FakeModel("2단계: Solution A 50 µL로 조각을 10 min 동안 씻어요.")
        session = self.listener(model_roles=True)
        spoken, reply, _state = reader._turn(session, "다음 단계 뭐야", 1, model)
        self.assertEqual(len(model.calls), 1)
        self.assertIn(UNTRANSLATED_SPOKEN, spoken[0])
        self.assertNotIn("50 µL로", spoken[0] + reply["text"])
        other = self.listener(model_roles=True)
        spoken, _reply, _state = reader._turn(other, "다음 단계 뭐야", 1, model)
        self.assertEqual(len(model.calls), 1)
        self.assertIn(UNTRANSLATED_SPOKEN, spoken[0])

    def test_a_provider_failure_is_bounded_and_said(self) -> None:
        session = self.listener(model_roles=True)
        spoken, _reply, state = reader._turn(
            session, "다음 단계 뭐야", 1, reader._FakeModel(error=TimeoutError("slow")))
        self.assertIn(UNTRANSLATED_SPOKEN, spoken[0])
        self.assertNotIn("Wash the pieces", spoken[0])

    def test_a_phrase_is_translated_without_a_step_label(self) -> None:
        model = reader._FakeModel(CONDITION_KO)
        session = self.listener(4, model_roles=True)
        spoken, _reply, _state = reader._turn(session, "다 했어", 1, model)
        self.assertEqual(len(model.calls), 1)
        self.assertIn(f"“{CONDITION_KO}”", spoken[0])
        self.assertTrue(spoken[0].startswith("자동 번역입니다. "))
        request = model.calls[0]["messages"][1]["content"]
        self.assertIn(CONDITION, request)
        self.assertNotIn("step_label", request)

    def test_the_source_is_read_aloud_only_when_asked_for(self) -> None:
        session = self.listener(1, model_roles=True)
        spoken, _reply, _state = reader._turn(session, "원문 그대로 읽어 줘", 1)
        self.assertEqual(spoken, [STEP_TWO])

    def test_logs_carry_no_sentence_or_reading(self) -> None:
        session = self.listener(model_roles=True)
        with self.assertLogs("voiney_lab", level=logging.INFO) as captured:
            reader._turn(session, "다음 단계 뭐야", 1, reader._FakeModel(STEP_TWO_KO))
        lines = [line for line in captured.output if "korean_first" in line]
        self.assertEqual(len(lines), 1)
        self.assertIn("statuses=accepted", lines[0])
        for words in ("Wash", "Solution", "씻어요"):
            self.assertNotIn(words, lines[0])

    def test_the_in_turn_reading_is_used_on_another_protocol_session_of_the_revision(self) -> None:
        reader._turn(self.listener(model_roles=True), "다음 단계 뭐야", 1, reader._FakeModel(STEP_TWO_KO))
        later = self.listener(model_roles=False)
        spoken, _reply, _state = reader._turn(later, "다음 단계 뭐야", 1)
        self.assertIn("Solution A 500 µL로", spoken[0])


class MiniprepTests(unittest.TestCase):
    def test_every_step_preview_and_reading_says_no_english_source(self) -> None:
        fixture = miniprep_fixture()
        for index in range(len(fixture.steps)):
            for words in ("다음 단계 뭐야", "이 단계 읽어줘", "이 단계 자세히 설명해줘", "1단계부터 3단계까지 알려줘"):
                with self.subTest(step=index + 1, words=words):
                    session = _session(fixture, index)
                    said = session.korean_first(_plan(session, words), language="ko").plan.speech_text
                    for step in fixture.steps:
                        body = step.instruction_source_text.split(" ", 1)[1]
                        self.assertNotIn(body[:24], said)


if __name__ == "__main__":
    unittest.main()
