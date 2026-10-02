"""The endpoint readers at in-gel's repeat steps read an utterance as a whole.

Lane M's combination check (every 1-3 phrase utterance through plan()) found
that the step-7 wordings lane E wrote -- "완전히 탈색…", "젤이 … 투명…",
"색이 … 빠졌…" -- were read as the endpoint reached beside a negation said
before them ("탈색이 안 됐어 완전히 탈색됐어"), beside "아직", and beside a
wish or condition ("완전히 탈색되면"). Lane O measured steps 9 and 20 the same
way and found the same at their wordings ("안 됐어 흰색이야", "흰색이 됐으면
좋겠어"). Each now gets the reading lane M gave its own wordings: a negation
anywhere but an opening "아니" correction is a negative, and "아직", a wish or
a condition is asked again.

The pilot log's "탈색돼 있어" and "탈색이 완료됐어" are now the step-7
endpoint too, under the same reading.
"""

from __future__ import annotations

import unittest

from voiney_lab.curated_protocol import _observation_predicate

#: Lane E's step-7 wordings, which read as before on their own.
STEP_7_FRAMES = (
    "완전히 탈색됐어",
    "완전히 탈색",
    "젤이 투명해",
    "젤이 이제 완전히 투명해졌어",
    "색이 빠졌어",
    "색이 완전히 빠졌습니다",
)
#: Steps 9 and 20's wordings.
STEP_9_FRAMES = (
    "흰색으로 변했어",
    "흰색이 됐어",
    "흰색이야",
    "흰색으로 바뀌었어",
    "탈수됐어",
    "완전히 말랐어",
)


def _beside(frame: str) -> dict[str, tuple[str, ...]]:
    """The combinations the check found, around one wording."""

    return {
        "negative": (
            f"탈색이 안 됐어 {frame}",
            f"안 됐어 {frame}",
            f"덜 됐어 {frame}",
            f"{frame} 근데 안 됐어",
            f"{frame} 아니야",
            f"맞아 아니 {frame}",
        ),
        "unread": (
            f"아직 {frame}",
            f"{frame} 아직",
            f"{frame} 탈색되면",
            f"{frame} 탈색이 됐으면 좋겠어",
            f"흰색이 됐으면 좋겠어 {frame}",
            f"흰색으로 변하면 {frame}",
            f"{frame} 될 거야",
        ),
    }


class StepSevenFramesReadAsAWholeTests(unittest.TestCase):
    def test_the_frames_alone_and_after_a_correction_still_report_it(self):
        for frame in STEP_7_FRAMES:
            for wording in (frame, f"아니 {frame}", f"아니야, {frame}", f"어 {frame}"):
                with self.subTest(wording=wording):
                    self.assertEqual(_observation_predicate("7", wording), "positive")

    def test_a_negation_anywhere_makes_it_a_negative(self):
        for frame in STEP_7_FRAMES:
            for wording in _beside(frame)["negative"]:
                with self.subTest(wording=wording):
                    self.assertEqual(_observation_predicate("7", wording), "negative")

    def test_not_yet_a_wish_or_a_condition_is_asked_again(self):
        for frame in STEP_7_FRAMES:
            for wording in _beside(frame)["unread"] + (
                "완전히 탈색되면",
                "젤이 투명해지면",
                "색이 빠지면",
            ):
                with self.subTest(wording=wording):
                    self.assertIsNone(_observation_predicate("7", wording))

    def test_the_english_frames_read_as_before(self):
        self.assertEqual(_observation_predicate("7", "fully destained"), "positive")
        self.assertEqual(
            _observation_predicate("7", "the gel is now transparent"), "positive"
        )
        self.assertEqual(_observation_predicate("7", "not fully destained"), "negative")


class StepNineAndTwentyFramesReadAsAWholeTests(unittest.TestCase):
    def test_the_frames_alone_and_after_a_correction_still_report_it(self):
        for label in ("9", "20"):
            for frame in STEP_9_FRAMES:
                for wording in (frame, f"아니 {frame}", f"어 {frame}"):
                    with self.subTest(step=label, wording=wording):
                        self.assertEqual(
                            _observation_predicate(label, wording), "positive"
                        )

    def test_a_negation_anywhere_makes_it_a_negative(self):
        for label in ("9", "20"):
            for frame in STEP_9_FRAMES:
                for wording in _beside(frame)["negative"]:
                    with self.subTest(step=label, wording=wording):
                        self.assertEqual(
                            _observation_predicate(label, wording), "negative"
                        )

    def test_not_yet_a_wish_or_a_condition_is_asked_again(self):
        for label in ("9", "20"):
            for frame in STEP_9_FRAMES:
                for wording in _beside(frame)["unread"] + (
                    "흰색이 됐으면 좋겠어",
                    "흰색으로 변했다면",
                ):
                    with self.subTest(step=label, wording=wording):
                        self.assertIsNone(_observation_predicate(label, wording))

    def test_the_english_frames_read_as_before(self):
        for label in ("9", "20"):
            with self.subTest(step=label):
                self.assertEqual(
                    _observation_predicate(label, "it turned white"), "positive"
                )
                self.assertEqual(
                    _observation_predicate(label, "not white yet"), "negative"
                )


#: The pilot log's B and C, and the wordings they stand for.
STEP_7_WORDINGS = (
    "어, 결과는 탈색, 탈색돼 있어.",
    "아니, 아, 7단계로 완료했다고. 탈색이 완료됐어.",
    "탈색돼 있어",
    "탈색돼 있어요",
    "탈색이 되어 있어",
    "탈색이 되어 있습니다",
    "탈색되어 있네",
    "탈색이 완료됐어",
    "탈색 완료됐어요",
    "탈색이 완료되었습니다",
    "탈색 완료",
    "탈색 완료야",
    "탈색 완료입니다",
    "지금 탈색 완료.",
)


class StepSevenSpokenWordingsTests(unittest.TestCase):
    def test_the_pilot_wordings_report_the_endpoint(self):
        for wording in STEP_7_WORDINGS:
            with self.subTest(wording=wording):
                self.assertEqual(_observation_predicate("7", wording), "positive")

    def test_they_are_read_as_a_whole_like_the_others(self):
        for frame in ("탈색돼 있어", "탈색이 완료됐어", "탈색 완료"):
            for wording in _beside(frame)["negative"] + ("탈색 완료 아니야",):
                with self.subTest(wording=wording):
                    self.assertEqual(_observation_predicate("7", wording), "negative")
            for wording in _beside(frame)["unread"]:
                with self.subTest(wording=wording):
                    if wording == "아직 탈색돼 있어":
                        # Lane E's "아직 … 탈색 … 있어" (still stained) reads
                        # this first, as a negative.
                        self.assertEqual(
                            _observation_predicate("7", wording), "negative"
                        )
                    else:
                        self.assertIsNone(_observation_predicate("7", wording))
        # A negation inside the wording leaves no wording to read: asked again.
        self.assertIsNone(_observation_predicate("7", "탈색이 완료 안 됐어"))

    def test_a_plan_a_time_or_a_question_about_it_is_no_report(self):
        for wording in (
            "탈색 완료 전이야",
            "탈색 완료하면 다음 단계야",
            "탈색 완료 시간은 얼마야",
            "탈색 완료 여부",
            "탈색 완료 기준이 뭐야",
            "탈색이 완료됐으면 좋겠어",
            "탈색이 완료되면",
            "탈색 완료됐어?",
            "탈색돼 있어?",
            "탈색이 완료됐는지 모르겠어",
            "탈색돼 있는 것 같아",
            "탈색이 완료될 때까지 반복합니다",
        ):
            with self.subTest(wording=wording):
                self.assertNotEqual(_observation_predicate("7", wording), "positive")

    def test_they_are_step_7_only(self):
        for label in ("6", "8", "9", "20"):
            for wording in ("탈색돼 있어", "탈색이 완료됐어", "탈색 완료"):
                with self.subTest(step=label, wording=wording):
                    self.assertIsNone(_observation_predicate(label, wording))


if __name__ == "__main__":
    unittest.main()
