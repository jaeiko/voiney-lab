"""Lane O's follow-ups: the endpoint with a problem, the paused voice, a range.

An endpoint reported in the same breath as a problem ("완전히 탈색됐는데
튜브가 터졌어") was held while the endpoint question was open (lane O), but
with the experiment record on the server replaced the reply with its record
acknowledgement, so the question asked again was never heard; and with no
question open the same words advanced past the endpoint and dropped the
problem. Both now record the problem, keep the step, leave the endpoint
question open without taking a bare yes, and say -- once the record took it --
"이상 사항은 기록했어요. 탈색이 끝났으면 한 번 더 말씀해 주세요."

"탈색 완료했어" (and "탈수 완료했어" at steps 9 and 20) reports the endpoint,
read as a whole as lane O's wordings are.

"3단계부터 5단계까지 알려줘" on a protocol with no Korean translation failed
with an AttributeError; it reads the source lines now.

While paused, a word other than resume, stop or pause got only an on-screen
notice. The first such word in each pause is now answered aloud, once; the
pause reply and the notice both name the same resume word, '재개'.
"""

from __future__ import annotations

import unittest

from tests.test_voice_pause_resume_persistence import (
    FIXTURE,
    PROVENANCE,
    SOURCE_PDF,
)
from voiney_lab.curated_protocol import (
    CuratedProtocolAction,
    CuratedProtocolSession,
    _observation_predicate,
    load_curated_protocol_fixture,
)
from voiney_lab.runtime_routing import route_curated_runtime_turn


def _step_index(fixture, label: str) -> int:
    return next(
        index for index, step in enumerate(fixture.steps)
        if step.source_label == label
    )


def _turn(session, text, turn_id):
    return route_curated_runtime_turn(
        session, text, turn_id=turn_id, language="ko",
        configuration_id=1, generation=1,
    ).plan


class CompletedWordingTests(unittest.TestCase):
    """Decision 3, on the reader alone (no document needed)."""

    def test_completed_reports_the_endpoint(self):
        for label, said in (
            ("7", "탈색 완료했어"),
            ("7", "탈색 완료했어요"),
            ("7", "탈색 완료했습니다"),
            ("7", "탈색을 완료했어"),
            ("9", "탈수 완료했어"),
            ("9", "탈수 완료했어요"),
            ("20", "탈수 완료했습니다"),
        ):
            with self.subTest(step=label, said=said):
                self.assertEqual(_observation_predicate(label, said), "positive")

    def test_it_is_read_as_a_whole(self):
        for label, frame in (("7", "탈색 완료했어"), ("9", "탈수 완료했어")):
            for said, reading in (
                (f"{frame[:-1]}으면 좋겠어", None),
                (f"아직 {frame}", None),
                (f"{frame} 아니야", "negative"),
                (f"안 됐어 {frame}", "negative"),
                (f"{frame}?", None),
                (f"{frame} 아마", None),
                (f"{frame} {frame[:-1]}으면 좋겠어", None),
            ):
                with self.subTest(step=label, said=said):
                    self.assertEqual(_observation_predicate(label, said), reading)

    def test_each_wording_stays_at_its_own_step(self):
        self.assertIsNone(_observation_predicate("9", "탈색 완료했어"))
        self.assertIsNone(_observation_predicate("7", "탈수 완료했어"))
        self.assertIsNone(_observation_predicate("8", "탈색 완료했어"))


class _InGelSteps:
    """A session on in-gel at one step, its endpoint question open or not."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = load_curated_protocol_fixture(FIXTURE, PROVENANCE, SOURCE_PDF)

    def _at(self, label: str, *, asked: bool = False):
        session = CuratedProtocolSession(self.fixture)
        session.activate_configured()
        _turn(session, "프로토콜 시작해줘", 1)
        session.current_index = _step_index(self.fixture, label)
        if asked:
            _turn(session, f"{label}단계 완료했어", 19)
            self.assertIsNotNone(session.pending_observation_confirmation)
            return session, session.current_index, 20
        self.assertIsNone(session.pending_observation_confirmation)
        return session, session.current_index, 19


@unittest.skipUnless(
    SOURCE_PDF.is_file(),
    f"requires the externally licensed Candidate A source PDF at {SOURCE_PDF}",
)
class CompletedInTheSessionTests(_InGelSteps, unittest.TestCase):
    """Decision 3 in the session, with the endpoint question open or not."""

    def test_completed_advances_and_a_wish_is_asked_again(self):
        for asked in (True, False):
            with self.subTest(asked=asked):
                session, at, turn_id = self._at("7", asked=asked)
                done = _turn(session, "탈색 완료했어", turn_id)
                self.assertEqual(done.action, CuratedProtocolAction.NEXT)
                self.assertTrue(done.state_changed)
                self.assertEqual(done.observation_predicate, "positive")
                self.assertEqual(session.current_index, at + 1)
        session, at, turn_id = self._at("7", asked=True)
        wish = _turn(session, "탈색 완료했으면 좋겠어", turn_id)
        self.assertFalse(wish.state_changed)
        self.assertEqual(wish.intent_kind, "observation_confirmation_reasked")
        self.assertEqual(session.current_index, at)
        for label in ("9", "20"):
            with self.subTest(step=label):
                session, at, turn_id = self._at(label, asked=True)
                done = _turn(session, "탈수 완료했어", turn_id)
                self.assertTrue(done.state_changed)
                self.assertEqual(session.current_index, at + 1)


if __name__ == "__main__":
    unittest.main()
