"""The replay check reports every way a replay can go wrong.

What is left of the rule-parity tests: the claim prompt, schema and refusal
table they compared were deleted on 2026-10-13 (lane AN, decision 1; the git
history keeps them). The replay check never depended on them.
"""

from __future__ import annotations

import unittest

from voiney_lab.replay_turns import check


class ReplayCheckTests(unittest.TestCase):
    """A verification step that cannot fail is not a verification step."""

    BASE = {
        "turn_id": 1,
        "transcript": "왜 해야 돼?",
        "normalized_text": "왜 해야 돼",
        "intent": "learning",
        "confidence": 1.0,
        "runtime_router": "curated_protocol",
        "action": "question",
        "intent_kind": "current_step_learning",
        "answer_origin": "approved_step_metadata",
        "state_mutation": False,
        "step_before": "1",
        "step_after": "1",
        "speech_text": "이 단계의 수행 목적은",
        "tools_used": [],
        "visual_intent": None,
    }

    def test_a_clean_replay_reports_no_problem(self) -> None:
        self.assertEqual(check([self.BASE], 1), [])

    def test_a_short_replay_is_reported(self) -> None:
        self.assertEqual(check([], 7), ["replayed 0 turns, expected 7"])

    def test_a_turn_with_no_route_is_reported(self) -> None:
        self.assertEqual(
            check([{**self.BASE, "runtime_router": ""}], 1),
            ["turn 1 produced no runtime_router"],
        )

    def test_a_mutation_that_did_not_move_the_step_is_reported(self) -> None:
        self.assertEqual(
            check([{**self.BASE, "state_mutation": True}], 1),
            [
                "turn 1 claimed a state mutation but the step did not move",
            ],
        )

    def test_a_silent_turn_is_reported(self) -> None:
        self.assertEqual(
            check([{**self.BASE, "speech_text": "   "}], 1),
            ["turn 1 produced no speech"],
        )

    def test_a_missing_field_is_reported(self) -> None:
        broken = {key: value for key, value in self.BASE.items() if key != "action"}
        self.assertEqual(check([broken], 1), ["turn 1 is missing ['action']"])

    def test_the_real_demo_replay_passes_its_own_check(self) -> None:
        from voiney_lab.replay_turns import (
            DEFAULT_TURNS,
            parse_args,
            replay,
        )

        records = replay(parse_args([]))
        self.assertEqual(check(records, len(DEFAULT_TURNS)), [])
        self.assertEqual(len(records), len(DEFAULT_TURNS))


if __name__ == "__main__":
    unittest.main()
