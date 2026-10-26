"""Lane HEAR2, decision 5 (2026-10-26): "다시 들려줘" replays the last answer with no words first.

The person's test: every "방금 답변 다시 들려줘" came with "마지막으로 재생
가능한 답변을 한 번 다시 들려드릴게요. 프로토콜 상태는 변경하지 않습니다." before
the answer. Now the replay turn says nothing of its own: the plan is silent
(a short line for the screen only), the server sends the replay request as
before, and the page plays the kept answer once it has the turn's end --
with no audio piece of the turn's own.
"""

from __future__ import annotations

import unittest

from tests.lane_cb_support import Turns
from tests.test_lane_fixv_replay import SETUP, run_page_script
from voiney_lab.curated_protocol import CuratedProtocolAction


class SilentReplayPlanTests(Turns, unittest.TestCase):
    def test_the_replay_turn_has_no_words_of_its_own(self) -> None:
        self.open(1)
        before = self.projection()
        for said in ("방금 답변 다시 들려줘", "안 들려", "다시 들려줘"):
            with self.subTest(said=said):
                plan = self.say(said)
                self.assertIs(plan.action, CuratedProtocolAction.AUDIO_RECOVERY, plan.speech_text)
                self.assertEqual(plan.speech_policy, "silent")
                self.assertEqual(plan.speech_text, "")
                self.assertNotIn("마지막으로 재생 가능한", plan.display_text or "")
                self.assertLess(len(plan.display_text or ""), 40, plan.display_text)
                self.assertFalse(plan.state_changed)
                self.assertEqual(self.projection(), before)


class SilentReplayPageTests(unittest.TestCase):
    def test_a_replay_turn_with_no_audio_of_its_own_plays_the_kept_answer_once(self) -> None:
        result = run_page_script(SETUP + r"""
await turn(1,[[11,12],[21,22,23],[31]]);
const before=played.length;
// The replay turn: speech.start, the server's operation, the replay request, then the turn's end with no piece.
await turn(2,[],{replay:true});
assert(played.length===before+1,`the replay did not play (played ${played.length-before} pieces)`);
assert(same(Array.from(played.at(-1)),[11,12,21,22,23,31]),`the replay played ${Array.from(played.at(-1))}`);
await playedToEnd();
assert(same(values(lastReplayableAudio),[11,12,21,22,23,31]),"the replay turn replaced the kept answer");
// Once more, the same answer again.
await turn(3,[],{replay:true});
assert(same(Array.from(played.at(-1)),[11,12,21,22,23,31]),`the second replay played ${Array.from(played.at(-1))}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
