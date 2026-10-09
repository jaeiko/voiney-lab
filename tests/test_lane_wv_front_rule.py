"""Lane WV, decision 4 (2026-10-09): a picture request is a front rule.

"그림 보여줘", "써모믹서가 어떻게 생겼어?", "그림으로 그려 줘" are read by the
rules before any model, router on or off, and change no state: the front
plan is the plan the rules alone would make, the session is left as the
rules leave it, and the LLM router never calls its model for the turn.
"""

from __future__ import annotations

import asyncio
import unittest

from tests.protocol_vocabulary_support import build_fixture
from tests.test_front_plan import _Twins
from voiney_lab.curated_protocol import FRONT_RULES, CuratedProtocolAction, CuratedProtocolSession
from voiney_lab.llm_router import LlmRouterSettings, route_turn_with_llm_router
from voiney_lab.runtime_routing import route_curated_runtime_turn

STEPS = (
    "1 Cut the stained band out of the gel and place it in a tube.",
    "2 Wash the band with 500 µL of solution B in the Thermomixer.",
    "3 Remove and discard the supernatant.",
)
PICTURE_TURNS = (
    "그림 보여줘", "이 단계 그림 있어?", "사진 보여줘", "젤 밴드 사진 보여줘",
    "써모믹서가 어떻게 생겼어?", "젤 밴드 실제 사진 보여줘", "그림으로 그려 줘", "이 단계 그려 줘",
)


def _fixture():
    return build_fixture(protocol_id="lane-wv-front", title="Fictional lane WV protocol", steps=STEPS,
                         equipment=("Thermomixer C",))


class PictureFrontRuleTests(_Twins, unittest.TestCase):
    fixture = _fixture()

    def test_the_rule_is_described(self) -> None:
        self.assertIn("visual_request", FRONT_RULES)

    def test_every_picture_request_is_the_front_rules_turn(self) -> None:
        for text in PICTURE_TURNS:
            with self.subTest(text=text):
                front, rule = self.assert_front([text], "visual_request", step_index=1)
                self.assertIs(front.action, CuratedProtocolAction.VISUAL_REQUEST)
                self.assertFalse(front.state_changed)
                self.assertIn(front.visual_kind, ("source_figure", "web_lookup", "drawn_diagram"))

    def test_a_value_question_with_a_picture_word_is_not_the_rule(self) -> None:
        rules_only, front_first = self._twins(1)
        front, rule = self._say(rules_only, front_first, "그림에 나온 용액 얼마나 넣어?", 2)
        self.assertNotEqual(rule, "visual_request")
        self.assertIsNot((front or rules_only.plan("x", turn_id=3, language="ko")).action,
                         CuratedProtocolAction.VISUAL_REQUEST)

    def test_before_the_start_the_rule_still_moves_nothing(self) -> None:
        rules_only, front_first = self._twins(None)
        front, rule = self._say(rules_only, front_first, "그림 보여줘", 2)
        self.assertEqual(front_first.state()["active"], False)
        self.assertEqual(front_first.state()["revision"], rules_only.state()["revision"])


class RouterLineTests(unittest.TestCase):
    """With the router on, the model is never called for a picture request."""

    def test_the_router_hands_the_turn_to_the_front_rule(self) -> None:
        session = CuratedProtocolSession(_fixture())
        session.activate_configured()
        session.plan("프로토콜 시작해줘", turn_id=1, language="ko", configuration_id=1, generation=1)
        session.current_index = 1
        calls = []

        def client_factory():
            calls.append("model")
            raise AssertionError("the model was called for a picture request")

        async def rule_route():
            raise AssertionError("the rules' fallback was taken")

        for turn_id, text in enumerate(("그림 보여줘", "써모믹서가 어떻게 생겼어?", "그림으로 그려 줘"), start=2):
            with self.subTest(text=text):
                before = session.state()
                outcome = asyncio.run(route_turn_with_llm_router(
                    session, text, turn_id=turn_id, language="ko",
                    settings=LlmRouterSettings(enabled=True, model="fake", timeout_seconds=1.0),
                    client_factory=client_factory, rule_route=rule_route,
                    configuration_id=1, generation=1,
                ))
                self.assertEqual(outcome.handled_by, "front:visual_request")
                self.assertFalse(outcome.model_called)
                self.assertIs(outcome.plan.action, CuratedProtocolAction.VISUAL_REQUEST)
                self.assertEqual(session.state()["current_step_label"], before["current_step_label"])
                self.assertEqual(session.state()["revision"], before["revision"])
        self.assertEqual(calls, [])

    def test_the_rules_path_reads_the_same_turn_the_same_way(self) -> None:
        session = CuratedProtocolSession(_fixture())
        session.activate_configured()
        session.plan("프로토콜 시작해줘", turn_id=1, language="ko", configuration_id=1, generation=1)
        session.current_index = 1
        routed = route_curated_runtime_turn(session, "젤 밴드 사진 보여줘", turn_id=2, language="ko",
                                            configuration_id=1, generation=1)
        self.assertIs(routed.plan.action, CuratedProtocolAction.VISUAL_REQUEST)
        self.assertEqual(session.last_front_rule, "visual_request")
        self.assertFalse(routed.state_mutation)


if __name__ == "__main__":
    unittest.main()
