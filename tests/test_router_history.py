"""The lane R router's structured history (design §4, decision D14).

ConversationHistory keeps, beside the legacy tool-loop groups, one bundle per
router turn: the step it was said at, the words, who handled them, the
proposal's tool and action, the server's result and a short spoken reply. At
most six bundles and about 1,200 tokens, trimmed oldest-first and whole.
Nothing writes these bundles until the router is wired and enabled, and the
legacy messages() never includes them.
"""

from __future__ import annotations

import json
import unittest

from voiney_lab.brain import (
    ROUTER_HISTORY_MAX_TOKENS,
    ROUTER_HISTORY_MAX_TURNS,
    ConversationHistory,
    RouterTurnRecord,
    estimate_tokens,
)


def _turn(index: int, **changes: object) -> RouterTurnRecord:
    values: dict[str, object] = dict(
        at_step="4", status="active", user=f"질문 {index}", handled_by="llm",
        assistant=f"답 {index}",
    )
    values.update(changes)
    return RouterTurnRecord(**values)  # type: ignore[arg-type]


class RouterHistoryTests(unittest.TestCase):
    def test_a_bundle_keeps_what_was_talked_about(self) -> None:
        record = RouterTurnRecord(
            at_step="7", status="active", user="탈색 아직 덜 됐는데 그냥 다음 단계 가자",
            handled_by="llm+tool", proposal_tool="change_state", proposal_kind="next",
            server_result="refused:completion_not_reached", state_after=("7", "active"),
            assistant="아직 끝나지 않았다고 하셔서 7단계에 머뭅니다.",
        )
        self.assertEqual(record.prompt_payload(), {
            "at_step": "7", "status": "active",
            "user": "탈색 아직 덜 됐는데 그냥 다음 단계 가자",
            "handled_by": "llm+tool",
            "proposal": {"tool": "change_state", "action": "next"},
            "server": {"result": "refused:completion_not_reached",
                       "state_after": {"step": "7", "status": "active"}},
            "assistant": "아직 끝나지 않았다고 하셔서 7단계에 머뭅니다.",
        })
        logged = RouterTurnRecord(
            at_step="5", status="active", user="메모 튜브 라벨 A-170", handled_by="llm+tool",
            proposal_tool="record_log", proposal_kind="observation", server_result="recorded",
        )
        self.assertEqual(logged.prompt_payload()["proposal"], {"tool": "record_log", "type": "observation"})

    def test_a_proposal_keeps_no_evidence_or_value(self) -> None:
        fields = set(RouterTurnRecord.__dataclass_fields__)
        self.assertFalse(fields & {"evidence", "value", "latency_ms", "evidence_ids", "path", "audio"})
        with self.assertRaises(ValueError):
            RouterTurnRecord(at_step="4", status="active", user="x", handled_by="llm+tool",
                             proposal_tool="change_state")

    def test_the_reply_is_short_and_spoken_only(self) -> None:
        record = _turn(1, assistant=(
            "트립신 용액은 25 µL 넣습니다.\n\n원문 · English\n22 Add 25 µL trypsin.\n\n"
            "출처\ncurrent_step · 원문 p.8"
        ))
        self.assertEqual(record.assistant, "트립신 용액은 25 µL 넣습니다.")
        long = _turn(2, assistant="가" * 500)
        self.assertEqual(len(long.assistant), 200)
        self.assertTrue(long.assistant.endswith("…"))
        self.assertEqual(len(_turn(3, user="나" * 900).user), 300)

    def test_emergency_and_interrupted_replies_are_not_kept(self) -> None:
        emergency = _turn(1, handled_by="front:emergency", assistant="즉시 대피하세요.")
        self.assertIsNone(emergency.assistant)
        self.assertEqual(emergency.prompt_payload()["handled_by"], "front:emergency")
        cut = _turn(2, assistant="말하는 도중", interrupted=True)
        self.assertEqual(cut.prompt_payload()["assistant"], None)
        self.assertTrue(cut.prompt_payload()["interrupted"])

    def test_handlers_and_results_are_closed_lists(self) -> None:
        for handled_by in ("front:pause", "llm", "llm+tool", "fallback_rules"):
            _turn(1, handled_by=handled_by)
        for result in ("executed", "confirm_opened", "observation_prompt_opened",
                       "recorded", "record_failed", "refused:pending_gate_owns_turn"):
            _turn(1, server_result=result)
        for handled_by in ("model", "front:", ""):
            with self.subTest(handled_by=handled_by), self.assertRaises(ValueError):
                _turn(1, handled_by=handled_by)
        for result in ("done", "refused:"):
            with self.subTest(result=result), self.assertRaises(ValueError):
                _turn(1, server_result=result)

    def test_at_most_six_turns_oldest_dropped_first(self) -> None:
        history = ConversationHistory()
        for index in range(10):
            history.record_router_turn(_turn(index))
        self.assertEqual(ROUTER_HISTORY_MAX_TURNS, 6)
        self.assertEqual([item["user"] for item in history.router_history()],
                         [f"질문 {index}" for index in range(4, 10)])

    def test_about_1200_tokens_trimmed_at_bundle_boundaries(self) -> None:
        history = ConversationHistory()
        for index in range(6):
            history.record_router_turn(_turn(index, user="가" * 290, assistant="나" * 190))
        kept = history.router_history()
        self.assertLess(len(kept), 6)
        self.assertEqual(kept[-1]["user"], "가" * 290)
        total = sum(
            estimate_tokens(json.dumps(item, ensure_ascii=False, separators=(",", ":")))
            for item in kept
        )
        self.assertLessEqual(total, ROUTER_HISTORY_MAX_TOKENS)
        # Every bundle kept is whole.
        for item in kept:
            self.assertEqual(item["assistant"], "나" * 190)

    def test_the_legacy_history_is_untouched(self) -> None:
        history = ConversationHistory()
        before = history.messages()
        history.record_router_turn(_turn(1))
        self.assertEqual(history.messages(), before)
        self.assertEqual(history.groups, [])
        history.reset()
        self.assertEqual(history.router_history(), [])

    def test_token_estimate_counts_korean_high(self) -> None:
        self.assertEqual(estimate_tokens("안녕하세요"), 5)
        self.assertEqual(estimate_tokens("hello world!"), 3)
        self.assertEqual(estimate_tokens(""), 0)


if __name__ == "__main__":
    unittest.main()
