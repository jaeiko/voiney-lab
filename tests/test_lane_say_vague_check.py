"""A vague doubt gets the step's expected result, then the problem help (lane SAY, decision 7, 2026-10-25).

The persona test (``~/reports/lane_persona_report.md`` F3): "이거 맞아요?",
"잘 된 건가", "망한 것 같아요" fell to the general "질문을 도와드릴 수 있어요"
(or to a search with nothing named). Human decision 7: such words say again,
briefly, what the current step should look like -- its expected result, or
the endpoint it repeats until -- and lead into the problem help (lane HELP).
Nothing is recorded and nothing moves.
"""

from __future__ import annotations

import unittest

from tests.lane_n_support import notes_fixture
from tests.protocol_vocabulary_support import SOURCE_PDF, in_gel_fixture, miniprep_fixture
from voiney_lab.curated_protocol import CuratedProtocolAction, CuratedProtocolSession
from voiney_lab.intent_arbitration import vague_check_reading
from voiney_lab.runtime_routing import route_curated_runtime_turn

DOUBTS = (
    "이거 맞아요?", "이게 맞아?", "이렇게 하는 거 맞나", "잘 된 건가", "잘 된 거 맞아?",
    "제대로 한 건지 모르겠어", "망한 것 같아요", "망했어", "다 망한 것 같아", "잘못한 것 같아",
    "이 정도면 돼?",
)
NOT_DOUBTS = (
    "젤이 망한 것 같아", "튜브가 터졌어", "온도가 맞아?", "10분 맞아?", "2단계 맞아?",
    "다음", "맞아", "네 맞아요",
)


def _session(fixture, index: int) -> CuratedProtocolSession:
    session = CuratedProtocolSession(fixture)
    session.activate_configured()
    route_curated_runtime_turn(session, "프로토콜 시작해줘", turn_id=1, language="ko",
                               configuration_id=1, generation=1)
    session._notebook_question = None
    session.current_index = index
    return session


def _say(session: CuratedProtocolSession, words: str):
    return route_curated_runtime_turn(session, words, turn_id=2, language="ko",
                                      configuration_id=1, generation=1).plan


class ReadingTests(unittest.TestCase):
    def test_doubts_with_nothing_named_are_read_as_vague(self) -> None:
        for words in DOUBTS:
            with self.subTest(words=words):
                self.assertTrue(vague_check_reading(words))
        for words in NOT_DOUBTS:
            with self.subTest(words=words):
                self.assertFalse(vague_check_reading(words))


class PlanTests(unittest.TestCase):
    def test_the_expected_result_is_said_then_the_help_is_marked(self) -> None:
        fixture = notes_fixture()
        step = fixture.steps[4]
        # Step 5 ("Repeat steps 2-4 until the band is clear") has no expected
        # result fact; its endpoint is what it repeats until.
        for words in DOUBTS:
            with self.subTest(words=words):
                session = _session(fixture, 4)
                before = session.state()
                plan = _say(session, words)
                self.assertEqual(plan.intent_kind, "vague_check")
                self.assertIs(plan.action, CuratedProtocolAction.COMPLETION_CRITERIA)
                self.assertTrue(plan.speech_text.startswith("5단계는 원문의 이 상태가 되면 끝나요."),
                                plan.speech_text)
                self.assertIn("until the band is clear", plan.speech_text)
                self.assertTrue(plan.speech_text.endswith(
                    "다르게 보이는 게 있으면 무엇이 다른지 말해 주세요."))
                self.assertIsNotNone(plan.problem_help)
                self.assertEqual(plan.problem_help["step_label"], step.source_label)
                self.assertFalse(plan.state_changed)
                self.assertFalse(plan.reported_anomaly or plan.reported_observation)
                self.assertIsNone(plan.notebook_record)
                self.assertEqual(session.state()["revision"], before["revision"])
                self.assertEqual(session.last_front_rule, "problem_help")

    @unittest.skipUnless(SOURCE_PDF.is_file(), f"requires the licensed source PDF at {SOURCE_PDF}")
    def test_a_step_with_an_expected_result_says_it_in_korean(self) -> None:
        fixture = in_gel_fixture()
        step = fixture.steps[6]  # "7 Repeat steps 2-7 until the gel band is fully destained"
        reviewed = fixture.localized_fact(step.step_id, "expected_result_1")
        session = _session(fixture, 6)
        plan = _say(session, "이거 맞아요?")
        self.assertTrue(plan.speech_text.startswith("7단계는 이렇게 되면 돼요. "), plan.speech_text)
        self.assertIn(reviewed.removeprefix("예상 결과: "), plan.speech_text)
        self.assertNotIn("예상 결과:", plan.speech_text)

    def test_a_step_stating_nothing_to_check_says_so(self) -> None:
        session = _session(miniprep_fixture(), 1)
        plan = _say(session, "잘 된 건가")
        self.assertEqual(
            plan.speech_text,
            "2단계 원문에는 확인할 결과가 따로 적혀 있지 않아요. "
            "다르게 보이는 게 있으면 무엇이 다른지 말해 주세요.")
        self.assertIsNotNone(plan.problem_help)

    def test_a_problem_with_a_thing_named_is_as_before(self) -> None:
        session = _session(notes_fixture(), 1)
        plan = _say(session, "젤이 아직 파래")
        self.assertNotEqual(plan.intent_kind, "vague_check")


class ServerTurnTests(unittest.TestCase):
    """Through ``server.run_turn``: the problem help follows the restated step."""

    def test_the_help_follows_what_the_step_should_look_like(self) -> None:
        import tests.test_reader_speech as reader

        session = reader._listener(notes_fixture(), 4, model_roles=False)
        before = session.curated_protocol_session.state()
        spoken, reply, state = reader._turn(session, "이거 맞아요?", 1)
        said = spoken[0]
        self.assertTrue(said.startswith("5단계는 원문의 이 상태가 되면 끝나요."), said)
        # The quoted endpoint is said Korean-first (no reading: not translated yet).
        self.assertNotIn("until the band is clear", said)
        self.assertIn("다르게 보이는 게 있으면 무엇이 다른지 말해 주세요.", said)
        # Lane HELP's closing comes after the restated step.
        self.assertIn("조건을 바꾸려면 담당자와 확인하세요.", said)
        self.assertLess(said.index("다르게 보이는 게"), said.index("조건을 바꾸려면"))
        self.assertEqual(state["revision"], before["revision"])


if __name__ == "__main__":
    unittest.main()
