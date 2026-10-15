"""Lane RA, decision (b): the Answer, Source and Visual roles are called together
with the router, never raced against the rules' answer; the Answer role's
answer goes out when it passes the same checks as the router's. Nothing is live.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import patch

from tests.lane_ra_support import (
    AGITATION_STEPS,
    FIRST,
    ON,
    ROLE_ENV,
    FakeRoles,
    RouterFirstVoiceMixin,
    agitation_fixture,
    route,
    session_at,
    terminal,
)
from tests.protocol_vocabulary_support import SOURCE_PDF
from tests.router_fakes import FakeRouterClient, answer_call_reply, tool_reply
from voiney_lab.curated_protocol import CuratedProtocolAction
from voiney_lab.multi_brain import AnswerBrainOutput


class RolesTests(unittest.TestCase):
    """Decision (b): the roles run beside the router, not against the rules."""

    def test_the_answer_role_answers_when_the_router_chose_answer(self) -> None:
        session = session_at(agitation_fixture(), 0, router_first=True)
        roles = FakeRoles({"answer": terminal("answer", AnswerBrainOutput(
            "solution A 500 µL로 밴드를 씻어요.", "1단계: solution A 500 µL로 밴드를 씻고 37°C에서 15 min 흔들어요.",
            ("S1.current_step",), (),
        ))})
        outcome = route(session, "solution A로 뭐 해?", FakeRouterClient(answer_call_reply(
            "밴드를 씻어요.", evidence_ids=("S1.current_step",))), roles=roles)
        self.assertEqual(len(roles.started), 1)
        self.assertEqual(roles.started[0][1], "solution A로 뭐 해?")
        self.assertEqual(outcome.handled_by, "llm")
        self.assertEqual(outcome.answered_by, "answer_brain")
        self.assertEqual(outcome.plan.speech_text, "solution A 500 µL로 밴드를 씻어요.")
        self.assertEqual(outcome.plan.evidence_ids, ("S1.current_step",))
        self.assertIn("answer", outcome.brain_outputs)

    def test_the_routers_answer_stands_when_the_role_fails_or_breaks_a_check(self) -> None:
        for output in (
            terminal("answer", None, status="timeout"),
            terminal("answer", AnswerBrainOutput("900 rpm으로 돌려요.", "900 rpm", ("S1.current_step",), ())),
        ):
            with self.subTest(output.status):
                session = session_at(agitation_fixture(), 0, router_first=True)
                outcome = route(session, "몇 rpm으로 돌려?", FakeRouterClient(answer_call_reply(
                    "800 rpm으로 돌려요.", evidence_ids=("S1.current_step",))),
                    roles=FakeRoles({"answer": output}))
                self.assertEqual(outcome.answered_by, "router")
                self.assertEqual(outcome.plan.speech_text, "800 rpm으로 돌려요.")

    def test_a_state_tool_cancels_the_roles(self) -> None:
        session = session_at(agitation_fixture(), 0, router_first=True)
        roles = FakeRoles({"answer": terminal("answer", AnswerBrainOutput("x", "x", ("S1.current_step",), ()))})
        outcome = route(session, "pH 7.2 나왔어 메모해 줘", FakeRouterClient(tool_reply(
            ("record_log", {"type": "measurement", "value": "pH 7.2 나왔어", "evidence": "pH 7.2 나왔어 메모해 줘"}))),
            roles=roles)
        self.assertIs(outcome.plan.action, CuratedProtocolAction.RECORD_OBSERVATION)
        self.assertEqual(outcome.answered_by, "router")
        self.assertTrue(roles.runs[0].cancelled)

    def test_the_roles_are_not_started_without_the_switch(self) -> None:
        session = session_at(agitation_fixture(), 0)
        roles = FakeRoles({})
        route(session, "solution A로 뭐 해?", FakeRouterClient(answer_call_reply(
            "밴드를 씻어요.", evidence_ids=("S1.current_step",))), settings=ON, roles=roles)
        self.assertEqual(roles.started, [])


if __name__ == "__main__":
    unittest.main()
