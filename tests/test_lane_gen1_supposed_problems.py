"""GEN1: a problem supposed is answered from the source, never recorded.

The 10/31 voice test: "여기서 침전이 생기면" (the transcript cut after -면) was
recorded as step 3's anomaly. A supposition reports nothing, so nothing may be
recorded; it goes to the grounded read-only answer (lane QA1). A supposition
said as what then happens ("섞으면 침전이 생겨") may be what just happened, so
it is asked once. Every public protocol of the SIM corpus -- biology,
chemistry, soil and materials -- is held to the same behaviour.
"""

from __future__ import annotations

import unittest

from scripts.sim.corpus import load_corpus
from scripts.sim.runner import snapshot
from voiney_lab.curated_protocol import (
    SUPPOSED_OR_HAPPENED_QUESTION,
    CuratedProtocolAction,
    CuratedProtocolSession,
)
from voiney_lab.intent_arbitration import (
    RequestIntent,
    arbitrate_request,
    supposed_problem_reading,
    supposes,
)
from voiney_lab.runtime_routing import route_curated_runtime_turn

#: Development wording only; the SIM holdout of supposed questions is scored, not read.
SUPPOSED = (
    "여기서 침전이 생기면",
    "여기서 침전이 생기면 어떡해?",
    "혹시 거품이 나면",
    "만약 색이 변하면 어떻게 돼?",
    "용액이 탁해지면",
    "침전이 생긴다면 어떻게 해?",
    "온도가 너무 높으면 어떡해?",
    "혹시 시료가 덜 마르면 어떻게 해?",
)
REPORTED = (
    "침전이 생겼어",
    "여기서 침전이 생겼어",
    "용액이 탁해졌어",
    "표면에 거품이 생겼어",
    "만약을 위해 봤는데 침전이 생겼어",
    "저으면서 보니까 침전이 생겼어",
)
UNCLEAR = "섞으면 침전이 생겨"


def _session(fixture, index: int = 2) -> CuratedProtocolSession:
    workflow = CuratedProtocolSession(fixture)
    workflow.active = True
    workflow._workflow_status = "running"
    workflow.current_index = min(index, len(fixture.steps) - 1)
    return workflow


def _turn(workflow: CuratedProtocolSession, words: str, turn_id: int = 1):
    return route_curated_runtime_turn(
        workflow, words, turn_id=turn_id, language="ko", configuration_id=1, generation=1,
    ).plan


class SupposedReadingTests(unittest.TestCase):
    def test_suppositions_are_read_as_supposed(self) -> None:
        for words in SUPPOSED:
            with self.subTest(words=words):
                self.assertEqual(supposed_problem_reading(words), "supposed")
                self.assertTrue(supposes(words))
                decision = arbitrate_request(words)
                self.assertIs(decision.intent, RequestIntent.TROUBLESHOOTING)
                self.assertEqual(decision.reason_code, "problem_contingency_question")

    def test_reports_are_not_suppositions(self) -> None:
        for words in REPORTED:
            with self.subTest(words=words):
                self.assertIsNone(supposed_problem_reading(words))
                self.assertFalse(supposes(words))

    def test_a_report_before_the_condition_is_no_supposition(self) -> None:
        # Said as having happened, then asked about: lane HELP's problem,
        # not a supposition ("만약 침전이 생겼으면" stays one).
        for words in ("침전이 생겼는데 이러면 어떡해?", "용액이 탁해졌는데 괜찮으면 다음 가도 돼?"):
            with self.subTest(words=words):
                self.assertIsNone(supposed_problem_reading(words))
                self.assertFalse(supposes(words))
        self.assertEqual(supposed_problem_reading("만약 침전이 생겼으면 어떡해?"), "supposed")

    def test_a_supposition_said_as_a_statement_is_unclear(self) -> None:
        self.assertEqual(supposed_problem_reading(UNCLEAR), "unclear")
        self.assertEqual(arbitrate_request(UNCLEAR).reason_code, "problem_supposed_or_happened")
        # Asked, it is a supposition; reported in the past, it happened.
        self.assertEqual(supposed_problem_reading("섞으면 거품이 나요?"), "supposed")
        self.assertIsNone(supposed_problem_reading("섞으면 침전이 생겼어"))

    def test_a_supposed_command_or_request_is_no_problem_but_supposes(self) -> None:
        for words in ("타이머 끝나면 다음으로 가자", "다 끝나면 알려줘"):
            with self.subTest(words=words):
                self.assertIsNone(supposed_problem_reading(words))
                self.assertTrue(supposes(words))


class SupposedProblemBoundaryTests(unittest.TestCase):
    """The rules boundary on every public protocol, router off."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.protocols = load_corpus()

    def test_a_supposed_problem_gets_the_grounded_answer_and_changes_nothing(self) -> None:
        self.assertGreaterEqual(len({p.field for p in self.protocols}), 3)
        for protocol in self.protocols:
            for words in SUPPOSED:
                with self.subTest(protocol=protocol.code, words=words):
                    workflow = _session(protocol.fixture)
                    before = snapshot(workflow)
                    plan = _turn(workflow, words)
                    self.assertIs(plan.action, CuratedProtocolAction.PROBLEM_HELP)
                    self.assertEqual(plan.intent_kind, "grounded_problem_question")
                    self.assertFalse(plan.reported_anomaly)
                    self.assertEqual(snapshot(workflow), before)
                    self.assertTrue(
                        "원문 · p." in plan.display_text or "원문에 없음" in plan.display_text,
                        plan.display_text,
                    )

    def test_a_reported_problem_is_still_recorded(self) -> None:
        for protocol in self.protocols:
            for words in REPORTED:
                with self.subTest(protocol=protocol.code, words=words):
                    workflow = _session(protocol.fixture)
                    plan = _turn(workflow, words)
                    self.assertIs(plan.action, CuratedProtocolAction.REPORT_ANOMALY)
                    self.assertTrue(plan.reported_anomaly)

    def test_a_supposed_command_records_no_anomaly(self) -> None:
        for protocol in self.protocols:
            for words in ("타이머 끝나면 다음으로 가자", "침전이 안 생기면 좋겠어"):
                with self.subTest(protocol=protocol.code, words=words):
                    workflow = _session(protocol.fixture)
                    before = snapshot(workflow)
                    plan = _turn(workflow, words)
                    self.assertIsNot(plan.action, CuratedProtocolAction.REPORT_ANOMALY)
                    self.assertEqual(snapshot(workflow)["anomaly"], before["anomaly"])
                    self.assertEqual(snapshot(workflow)["step"], before["step"])

    def test_an_unclear_supposition_is_asked_once_and_the_reply_decides(self) -> None:
        for protocol in self.protocols:
            with self.subTest(protocol=protocol.code, reply="happened"):
                workflow = _session(protocol.fixture)
                before = snapshot(workflow)
                asked = _turn(workflow, UNCLEAR, 1)
                self.assertEqual(asked.display_text, SUPPOSED_OR_HAPPENED_QUESTION)
                self.assertEqual(snapshot(workflow), before)
                recorded = _turn(workflow, "지금 생겼어", 2)
                self.assertIs(recorded.action, CuratedProtocolAction.REPORT_ANOMALY)
                self.assertEqual(recorded.anomaly_text, UNCLEAR)
                self.assertEqual(workflow._pending_anomaly["text"], UNCLEAR)

            with self.subTest(protocol=protocol.code, reply="asked"):
                workflow = _session(protocol.fixture)
                _turn(workflow, UNCLEAR, 1)
                before = snapshot(workflow)
                answered = _turn(workflow, "생기면 어떻게 하는지 물어본 거야", 2)
                self.assertIs(answered.action, CuratedProtocolAction.PROBLEM_HELP)
                self.assertEqual(answered.intent_kind, "grounded_problem_question")
                self.assertEqual(answered.problem_help["words"], UNCLEAR)
                self.assertEqual(snapshot(workflow), before)

            with self.subTest(protocol=protocol.code, reply="same words again"):
                workflow = _session(protocol.fixture)
                _turn(workflow, UNCLEAR, 1)
                before = snapshot(workflow)
                again = _turn(workflow, UNCLEAR, 2)
                self.assertNotEqual(again.display_text, SUPPOSED_OR_HAPPENED_QUESTION)
                self.assertIs(again.action, CuratedProtocolAction.PROBLEM_HELP)
                self.assertEqual(snapshot(workflow), before)

            with self.subTest(protocol=protocol.code, reply="something else"):
                workflow = _session(protocol.fixture)
                _turn(workflow, UNCLEAR, 1)
                other = _turn(workflow, "현재 단계 알려줘", 2)
                self.assertIs(other.action, CuratedProtocolAction.CURRENT)


if __name__ == "__main__":
    unittest.main()
