"""Lane SP1, decision 3 (2026-10-10): 시끄러운 곳 mode asks once before it acts.

With "주변 소리: 시끄러움", a command that would change the workflow at once
(다음, 완료, 타이머 시작) and a record (메모, 수치) are asked about once --
"3단계 완료하고 4단계로 갈까요?" -- and only a yes on the next turn does
it; a no leaves everything, any other words let the question lapse and are
handled as a turn of their own. Commands that already ask (종료, 방금 완료
취소, a move to another step) keep their one question. Questions are
answered as before. "조용함" (the default) changes nothing. The setting is
said aloud ("시끄러운 곳 모드로 바꿔 줘"), and after three voices taken for
other people's within a minute the server suggests the mode once; only the
experimenter's yes changes it. Decision 1's "민감도" is a setting the same
way.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_lane_sp1_level_gate import CONFIG, Socket, utterance, voiced_if_loud
from voiney_lab.curated_protocol import CuratedProtocolSession, load_curated_protocol_fixture
from voiney_lab.runtime_routing import route_curated_runtime_turn
from voiney_lab.server import (
    AMBIENT_SUGGESTION_WORDS,
    EXPERIMENTER_SETTING_DEFAULTS,
    ListenerSession,
    LockedSender,
    _suggest_noisy_mode,
)
from voiney_lab.vad import SENSITIVITY_MARGINS_DB, EndpointDetector, TurnState

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_JSON = ROOT / "data/fixtures/development_protocols/candidate_a_curated_analysis.json"
PROVENANCE = ROOT / "data/fixtures/development_protocols/candidate_a_curated_analysis.provenance.json"
SOURCE_PDF = ROOT / "data/runtime/candidate-a-source/in-gel-digestion.pdf"


def in_gel_available() -> bool:
    return FIXTURE_JSON.exists() and PROVENANCE.exists() and SOURCE_PDF.exists()


@unittest.skipUnless(in_gel_available(), "the in-gel fixture and its source PDF are needed")
class NoisyModeRulesTests(unittest.TestCase):
    """Through route_curated_runtime_turn, the rules' path, on in-gel at step 3."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = load_curated_protocol_fixture(FIXTURE_JSON, PROVENANCE, SOURCE_PDF)
        cls.index = {step.source_label: i for i, step in enumerate(cls.fixture.steps)}

    def session(self, *, noisy: bool) -> CuratedProtocolSession:
        session = CuratedProtocolSession(self.fixture)
        session.activate_configured()
        if noisy:
            session.apply_experimenter_settings({"ambient_mode": "noisy"})
        self.turn = 0
        self.say(session, "프로토콜 시작해줘")
        session.current_index = self.index["3"]
        return session

    def say(self, session: CuratedProtocolSession, text: str):
        self.turn += 1
        return route_curated_runtime_turn(
            session, text, turn_id=self.turn, language="ko", configuration_id=1, generation=1).plan

    def test_quiet_is_as_before(self) -> None:
        session = self.session(noisy=False)
        plan = self.say(session, "다음")
        self.assertTrue(plan.state_changed)
        self.assertEqual(session.current_index, self.index["4"])
        self.assertEqual(session.ambient_mode, "quiet")

    def test_next_is_asked_and_a_yes_moves_on(self) -> None:
        session = self.session(noisy=True)
        plan = self.say(session, "다음")
        self.assertEqual(plan.intent_kind, "ambient_confirmation_required")
        self.assertEqual(plan.display_text, "3단계 완료하고 4단계로 갈까요?")
        self.assertFalse(plan.state_changed)
        self.assertEqual(session.current_index, self.index["3"])
        self.assertEqual(session.last_front_rule, "ambient_confirmation")
        plan = self.say(session, "응")
        self.assertTrue(plan.state_changed)
        self.assertEqual(session.current_index, self.index["4"])
        self.assertEqual(session.last_front_rule, "yes_no_open_question")

    def test_a_no_leaves_everything(self) -> None:
        session = self.session(noisy=True)
        self.say(session, "3단계 끝났어")
        plan = self.say(session, "아니")
        self.assertEqual(plan.intent_kind, "ambient_confirmation_declined")
        self.assertEqual(plan.display_text, "알겠어요. 그대로 둘게요.")
        self.assertEqual(session.current_index, self.index["3"])
        # Nothing is left open: a later yes is no answer to anything.
        plan = self.say(session, "응")
        self.assertFalse(plan.state_changed)
        self.assertEqual(session.current_index, self.index["3"])

    def test_other_words_let_the_question_lapse(self) -> None:
        session = self.session(noisy=True)
        self.say(session, "다음")
        plan = self.say(session, "몇 분 남았어?")
        self.assertEqual(plan.action.value, "timer_status")
        self.assertFalse(plan.state_changed)
        plan = self.say(session, "다음")
        self.assertEqual(plan.intent_kind, "ambient_confirmation_required", "asked again")
        plan = self.say(session, "네")
        self.assertTrue(plan.state_changed)
        self.assertEqual(session.current_index, self.index["4"])

    def test_the_question_is_for_the_next_turn_only(self) -> None:
        session = self.session(noisy=True)
        self.say(session, "다음")
        self.turn += 1  # a turn went by (a filler, an ignored voice)
        plan = self.say(session, "응")
        self.assertFalse(plan.state_changed)
        self.assertEqual(session.current_index, self.index["3"])

    def test_the_timer_is_asked_and_a_yes_starts_it(self) -> None:
        session = self.session(noisy=True)
        plan = self.say(session, "타이머 시작해 줘")
        self.assertEqual(plan.display_text, "3단계 타이머를 시작할까요?")
        self.assertEqual(session.timer_status().get("state"), "not_started")
        plan = self.say(session, "응")
        self.assertEqual(session.timer_status().get("state"), "running")
        self.assertTrue(plan.state_changed)

    def test_a_note_and_a_value_are_asked_and_a_yes_records(self) -> None:
        session = self.session(noisy=True)
        plan = self.say(session, "메모해 줘 밴드가 흐릿해")
        self.assertEqual(plan.intent_kind, "note_confirmation_required")
        self.assertEqual(plan.display_text, "'밴드가 흐릿해' 이렇게 기록할까요? 맞으면 '네'라고 해 주세요.")
        self.assertIsNone(plan.note_record)
        plan = self.say(session, "네")
        self.assertEqual(plan.action.value, "record_observation")
        self.assertIsNotNone(plan.note_record)
        plan = self.say(session, "기록해 줘 0.5 밀리리터 넣었어")
        self.assertEqual(plan.intent_kind, "note_confirmation_required")
        self.assertIn("기록할까요", plan.display_text)
        plan = self.say(session, "응")
        self.assertEqual(plan.action.value, "record_observation")

    def test_commands_that_ask_already_ask_once(self) -> None:
        session = self.session(noisy=True)
        plan = self.say(session, "실험 종료")
        self.assertEqual(plan.intent_kind, "stop_confirmation_required")
        plan = self.say(session, "응")
        self.assertEqual(plan.intent_kind, "stop_confirmed")
        self.assertTrue(plan.state_changed)
        session = self.session(noisy=True)
        self.say(session, "다음"); self.say(session, "응")
        plan = self.say(session, "방금 완료 취소")
        self.assertEqual(plan.intent_kind, "step_revert_confirmation_required")
        plan = self.say(session, "응")
        self.assertEqual(session.current_index, self.index["3"])

    def test_questions_are_answered_as_before(self) -> None:
        session = self.session(noisy=True)
        for words in ("이 단계 뭐 해야 해?", "rpm 얼마야?", "몇 분 남았어?"):
            plan = self.say(session, words)
            self.assertFalse(plan.state_changed, words)
            self.assertNotEqual(plan.intent_kind, "ambient_confirmation_required", words)
        self.assertEqual(session.current_index, self.index["3"])

    def test_the_mode_and_the_sensitivity_are_said_aloud(self) -> None:
        session = self.session(noisy=False)
        plan = self.say(session, "시끄러운 곳 모드로 바꿔 줘")
        self.assertEqual(plan.setting_change, {"ambient_mode": "noisy"})
        self.assertEqual(session.ambient_mode, "noisy")
        self.assertIn("시끄러운 곳 모드로 바꿨어요", plan.display_text)
        plan = self.say(session, "조용한 곳 모드")
        self.assertEqual(plan.setting_change, {"ambient_mode": "quiet"})
        self.assertEqual(session.ambient_mode, "quiet")
        plan = self.say(session, "조용한 곳 모드")
        self.assertEqual(plan.intent_kind, "experimenter_setting_unchanged")
        plan = self.say(session, "민감도 높음으로 바꿔 줘")
        self.assertEqual(plan.setting_change, {"speaker_sensitivity": "high"})
        plan = self.say(session, "민감도 낮춰 줘")
        self.assertEqual(plan.setting_change, {"speaker_sensitivity": "low"})
        self.assertEqual(session.speaker_sensitivity, "low")
        # "조용한 모드" is still the readback way of 수치 확인 (lane CF).
        plan = self.say(session, "조용한 모드")
        self.assertEqual(plan.setting_change, {"confirm_mode": "quiet"})
        self.assertEqual(session.experimenter_settings()["ambient_mode"], "quiet")
        self.assertEqual(session.experimenter_settings()["speaker_sensitivity"], "low")

    def test_the_suggestion_is_answered_by_the_next_turn_only(self) -> None:
        session = self.session(noisy=False)
        session.open_ambient_suggestion()
        self.assertTrue(session.ambient_suggestion_open)
        plan = self.say(session, "응")
        self.assertEqual(plan.setting_change, {"ambient_mode": "noisy"})
        self.assertEqual(session.ambient_mode, "noisy")
        self.assertFalse(session.ambient_suggestion_open)
        session = self.session(noisy=False)
        session.open_ambient_suggestion()
        plan = self.say(session, "아니")
        self.assertEqual(plan.intent_kind, "ambient_suggestion_declined")
        self.assertEqual(session.ambient_mode, "quiet")
        session = self.session(noisy=False)
        session.open_ambient_suggestion()
        plan = self.say(session, "다음")
        self.assertTrue(plan.state_changed, "the command after a suggestion is a command")
        self.assertEqual(session.ambient_mode, "quiet")
        self.assertFalse(session.ambient_suggestion_open)


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class _Curated:
    """Just what the server needs of a curated session for the suggestion."""

    def __init__(self, mode: str = "quiet") -> None:
        self.ambient_mode = mode
        self.ambient_suggestion_open = False

    def open_ambient_suggestion(self) -> None:
        self.ambient_suggestion_open = True


class SuggestionTests(unittest.TestCase):
    def listener(self) -> ListenerSession:
        session = ListenerSession(EndpointDetector(CONFIG, classifier=voiced_if_loud, listening_onset=True), clock=Clock())
        session.start()
        session.accept_configuration(41, "cascade", "ko", "lane-sp1")
        session.set_level_reference(-20.0)
        session.curated_protocol_session = _Curated()
        return session

    def ignore(self, session: ListenerSession, times: int) -> None:
        for _ in range(times):
            for i in range(0, len(utterance(0.01)), 640):
                session.accept_chunk(utterance(0.01)[i:i + 640])
            session.clock.now += 1.0

    def test_three_ignored_voices_in_a_minute_make_the_suggestion_due_once(self) -> None:
        session = self.listener()
        self.ignore(session, 2)
        self.assertFalse(session.noisy_mode_suggestion_due())
        self.ignore(session, 1)
        self.assertTrue(session.noisy_mode_suggestion_due())
        session.curated_protocol_session.ambient_mode = "noisy"
        self.assertFalse(session.noisy_mode_suggestion_due(), "already noisy")
        session.curated_protocol_session.ambient_mode = "quiet"
        session.curated_protocol_session.open_ambient_suggestion()
        self.assertFalse(session.noisy_mode_suggestion_due(), "a suggestion is open")
        session.curated_protocol_session.ambient_suggestion_open = False
        session.ambient_suggested_at = session.clock()
        self.assertFalse(session.noisy_mode_suggestion_due(), "suggested just now")
        session.clock.now += 601
        self.ignore(session, 3)
        self.assertTrue(session.noisy_mode_suggestion_due())
        session.ignored_speech_at.clear()
        session.ignored_speech_at.extend([session.clock() - 61, session.clock() - 61, session.clock() - 61])
        self.assertFalse(session.noisy_mode_suggestion_due(), "older than a minute")

    def test_the_suggestion_is_said_as_the_servers_words_and_opened_to_the_rules(self) -> None:
        session = self.listener()
        self.ignore(session, 3)
        socket = Socket()
        with patch("voiney_lab.server.synthesize", return_value=b"\0\0" * 320 * 5):
            said = asyncio.run(_suggest_noisy_mode(LockedSender(socket), session))
        self.assertTrue(said)
        words = next(m for m in socket.text if m["type"] == "server.words")
        self.assertEqual((words["words_kind"], words["text"]), ("ambient_suggestion", AMBIENT_SUGGESTION_WORDS))
        self.assertTrue(session.curated_protocol_session.ambient_suggestion_open)
        self.assertEqual(session.ambient_suggested_at, session.clock())
        self.assertEqual(session.curated_protocol_session.ambient_mode, "quiet", "suggested only")
        with patch("voiney_lab.server.synthesize", return_value=b"\0\0" * 320) as tts:
            self.assertFalse(asyncio.run(_suggest_noisy_mode(LockedSender(socket), session)))
        tts.assert_not_called()


class SettingsReachTheListenerTests(unittest.TestCase):
    def test_the_defaults_name_both_settings(self) -> None:
        self.assertEqual(EXPERIMENTER_SETTING_DEFAULTS["ambient_mode"], "quiet")
        self.assertEqual(EXPERIMENTER_SETTING_DEFAULTS["speaker_sensitivity"], "normal")

    def test_a_sensitivity_said_aloud_changes_this_sessions_margin(self) -> None:
        session = ListenerSession(clock=Clock())
        session.start()
        session.set_level_reference(-20.0)
        self.assertEqual(session.set_speaker_sensitivity("high"), "high")
        self.assertEqual(session.level_reference.margin_db, SENSITIVITY_MARGINS_DB["high"])
        self.assertEqual(session.level_reference.reference_db, -20.0)


if __name__ == "__main__":
    unittest.main()
