"""아니오 declines a confirmation question exactly as 아니요 does.

아니오 is the standard spelling of the negative reply and 아니요 the spoken
one; speech-to-text produces either. Only 아니요 was in the negative reply
patterns, so a 아니오 answer fell through to the off-topic route and the
pending question was dropped without a decline being recorded.

Uses the fictional in-memory fixture from ``test_curated_answer_gating``, so
it runs with or without the externally licensed source PDF.
"""

from __future__ import annotations

import unittest

from tests.test_curated_answer_gating import _fixture
from voiney_lab.curated_protocol import (
    _NEGATIVE_COMPLETION_CONFIRMATION,
    CuratedProtocolAction,
    CuratedProtocolSession,
    _binary_frame_reply,
    _semantic_utterance_key,
)

#: Two different ways the session asks for confirmation before advancing:
#: a bare "next", and a completion report naming a step other than the
#: current one.
#: Lane VX, decision 1: "다음" no longer asks; "완료했어" does.
QUESTION_OPENERS = ("완료했어", "2단계 완료했어")
#: Each pair is the same answer with 아니요 and with 아니오.
REPLY_PAIRS = (
    ("아니요", "아니오"),
    ("아니요.", "아니오."),
    ("아니요 아직 안 했어", "아니오 아직 안 했어"),
)


def _answer(opener: str, reply: str):
    session = CuratedProtocolSession(_fixture("fictional-anio-protocol"))
    session.active = True
    session.current_index = 0
    session.plan(opener, turn_id=1, language="ko")
    asked = session.pending_completion_confirmation is not None
    plan = session.plan(reply, turn_id=2, language="ko")
    return asked, plan, session


class AnioConfirmationReplyTests(unittest.TestCase):
    def test_anio_answers_a_confirmation_question_the_way_aniyo_does(self):
        for opener in QUESTION_OPENERS:
            for aniyo, anio in REPLY_PAIRS:
                with self.subTest(opener=opener, reply=anio):
                    asked_aniyo, expected, aniyo_session = _answer(opener, aniyo)
                    asked_anio, plan, session = _answer(opener, anio)

                    # Guard the guard: both replies met an outstanding question.
                    self.assertTrue(asked_aniyo)
                    self.assertTrue(asked_anio)

                    self.assertEqual(
                        expected.action, CuratedProtocolAction.DECLINE_COMPLETION
                    )
                    self.assertEqual(expected.intent_kind, "pending_completion_declined")
                    self.assertEqual(plan.action, expected.action)
                    self.assertEqual(plan.intent_kind, expected.intent_kind)
                    self.assertEqual(plan.state_changed, expected.state_changed)
                    self.assertFalse(plan.state_changed)
                    self.assertEqual(plan.display_text, expected.display_text)
                    self.assertEqual(session.current_index, aniyo_session.current_index)
                    self.assertEqual(session.current_index, 0)
                    self.assertIsNone(session.pending_completion_confirmation)

    def test_both_negative_reply_patterns_read_anio_as_negative(self):
        # The completion prompt accepts either pattern, so the session test
        # above would still pass if one of them lost 아니오. The observation
        # prompt reads only the binary one.
        for reply in ("아니오", "아니오."):
            with self.subTest(reply=reply):
                key = _semantic_utterance_key(reply)
                self.assertIsNotNone(_NEGATIVE_COMPLETION_CONFIRMATION.fullmatch(key))
                self.assertEqual(_binary_frame_reply(reply), "negative")
                self.assertEqual(
                    _binary_frame_reply(reply),
                    _binary_frame_reply(reply.replace("아니오", "아니요")),
                )


if __name__ == "__main__":
    unittest.main()
