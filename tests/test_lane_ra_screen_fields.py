"""Lane RA, decision (f): the screen is told the next step's sentence, the kind
of the open confirmation question, the candidates asked back, the values read
back and the deviation a note recorded -- the session's own state, for lanes
UI2 and RV (the page is not changed). Nothing is live.
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


class ScreenFieldTests(unittest.TestCase):
    """Decision (f): what the screen is told, from the session's own state."""

    def test_the_next_step_sentence_and_the_open_question_kind(self) -> None:
        session = session_at(agitation_fixture(), 0)
        self.assertEqual(session.next_step_sentence("ko"), " ".join(AGITATION_STEPS[1].split()))
        self.assertIsNone(session.confirm_question())
        session.plan("완료했어", turn_id=2, language="ko", configuration_id=1, generation=1)
        question = session.confirm_question()
        self.assertEqual(question["kind"], "completion")
        self.assertIn("완료하셨나요", question["text"])
        session.plan("네", turn_id=3, language="ko", configuration_id=1, generation=1)
        self.assertEqual(session.current_index, 1)
        self.assertIsNone(session.confirm_question())
        last = session_at(agitation_fixture(), 2)
        self.assertIsNone(last.next_step_sentence("ko"))

    def test_the_fields_ride_with_the_screen(self) -> None:
        from voiney_lab.server import curated_screen_fields

        session = session_at(agitation_fixture(), 0)
        fields = curated_screen_fields(session)
        for name in ("next_step_sentence", "confirm_question", "clarify_candidates",
                     "readback_values", "record_deviation"):
            self.assertIn(name, fields)
        self.assertEqual(fields["clarify_candidates"], [])
        self.assertEqual(fields["readback_values"], [])
        self.assertIsNone(fields["record_deviation"])


@unittest.skipUnless(
    SOURCE_PDF.is_file(),
    f"requires the externally licensed Candidate A source PDF at {SOURCE_PDF}",
)
class DeviationScreenVoiceTests(RouterFirstVoiceMixin, unittest.TestCase):
    """Over the WebSocket: a deviation note's comparison and readback reach the screen."""

    def test_the_deviation_and_the_readback_values_ride_with_the_state(self) -> None:
        said = "젤 플러그를 37.2도에서 790 rpm 으로 교반했어, 특이사항으로 메모해 줘"

        async def steps(socket, listener, say, seen):
            await say(1, "프로토콜 시작해줘")
            await say(2, "1단계 완료했어")
            await say(3, "2단계 완료했어")
            await say(4, said)

        socket, seen, _ = self._run(steps, script=(tool_reply(("record_log", {
            "type": "deviation", "value": "젤 플러그를 37.2도에서 790 rpm 으로 교반했어", "evidence": said,
        })),))
        state = socket.for_turn(4, "protocol.fixture.state")[-1]
        deviation = state["screen"]["record_deviation"]
        self.assertEqual([d["kind"] for d in deviation["deviations"]], ["temperature", "speed"])
        self.assertEqual(state["screen"]["readback_values"], ["37.2도", "790 rpm"])
        self.assertEqual(state["screen"]["clarify_candidates"], [])
        self.assertEqual(state["screen"]["confirm_question"], None)
        self.assertTrue(state["screen"]["next_step_sentence"])


if __name__ == "__main__":
    unittest.main()
