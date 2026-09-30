"""Spoken Korean step numbers past 25 name that step, not a neighbour."""

import unittest

from voiney_lab.completion_intent import resolve_korean_completion_decision
from voiney_lab.curated_protocol import (
    CuratedProtocolAction,
    classify_curated_control_intent,
)


class SpokenStepNumeralTests(unittest.TestCase):
    def test_steps_26_67_and_99_spoken_in_korean_target_that_step(self):
        # 이십육 is the case that used to go wrong twice over: the dictionary
        # stopped at 이십오, and the demonstrative "이" in front of the step
        # number swallowed the tens digit so the rest read as 십육 (16).
        spoken = {
            "이십육 단계 완료했어": 26,
            "육십칠 단계 완료했어": 67,
            "구십구 단계 완료했어": 99,
        }
        for transcript, step in spoken.items():
            with self.subTest(transcript=transcript):
                decision = resolve_korean_completion_decision(
                    transcript, language="ko"
                )
                self.assertTrue(decision.is_completion)
                self.assertEqual(decision.target_kind, "explicit_step")
                self.assertEqual(decision.target_step_number, step)
                self.assertEqual(decision.target_step_label, str(step))

                # The production router hands that label on as the target the
                # session compares with its current step before advancing.
                intent = classify_curated_control_intent(
                    transcript, language="ko"
                )
                self.assertEqual(intent.action, CuratedProtocolAction.NEXT)
                self.assertTrue(intent.reported_completion)
                self.assertEqual(intent.target_step, str(step))


if __name__ == "__main__":
    unittest.main()
