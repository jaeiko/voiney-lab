"""Lane RA, decision (c): a note with values goes through record_log, and a value
that differs from the step's source value is recorded as a deviation with the
source value beside it, read back the way the experimenter chose, and shown to
the screen. Off, the rules' note is as it was. Nothing is live.
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
from voiney_lab.curated_protocol import (
    CuratedProtocolAction,
    measured_values,
    source_value_deviations,
)


class DeviationTests(unittest.TestCase):
    """Decision (c): a value that differs from the source is a deviation."""

    def test_values_are_compared_by_kind_against_the_source(self) -> None:
        source = AGITATION_STEPS[0]
        found = source_value_deviations(measured_values("37.2도에서 790 rpm 으로 돌렸어"), source)
        self.assertEqual(
            [(d["kind"], d["said"], d["source"]) for d in found],
            [("temperature", "37.2도", "37°C"), ("speed", "790 rpm", "800 rpm")],
        )
        self.assertEqual(source_value_deviations(measured_values("37도에서 800 rpm으로 했어"), source), [])
        # A kind the source does not state is no deviation (nothing to differ from).
        self.assertEqual(source_value_deviations(measured_values("pH 7.2 나왔어"), source), [])
        # One of two stated values of a kind: 15 min matches; 20 min differs from both.
        self.assertEqual(source_value_deviations(measured_values("15분 돌렸어"), source), [])
        self.assertEqual(
            [(d["said"], d["source"]) for d in source_value_deviations(measured_values("20분 돌렸어"), source)],
            [("20분", "15 min")],
        )

    def test_the_note_carries_the_deviation_only_with_the_switch(self) -> None:
        said = "37.2도에서 790 rpm 으로 돌렸어, 특이사항으로 메모해 줘"
        proposal = tool_reply(("record_log", {
            "type": "deviation", "value": "37.2도에서 790 rpm 으로 돌렸어", "evidence": said,
        }))
        session = session_at(agitation_fixture(), 0, router_first=True)
        outcome = route(session, said, FakeRouterClient(proposal))
        note = outcome.plan.note_record
        self.assertEqual(note["category"], "deviation")
        self.assertEqual(
            [(d["kind"], d["said"], d["source"]) for d in note["deviations"]],
            [("temperature", "37.2도", "37°C"), ("speed", "790 rpm", "800 rpm")],
        )
        self.assertEqual(session._last_note_deviation["deviations"], note["deviations"])
        self.assertEqual(session._last_readback_values, ["37.2도", "790 rpm"])
        # The rules' own note, with the switch off, is as it was: no comparison.
        plain = session_at(agitation_fixture(), 0)
        plan = plain.plan(said, turn_id=2, language="ko", configuration_id=1, generation=1)
        self.assertNotIn("deviations", plan.note_record)
        self.assertIsNone(plain._last_note_deviation)

    def test_a_matching_value_is_a_measurement_not_a_deviation(self) -> None:
        said = "37도에서 800 rpm으로 돌렸어, 메모해 줘"
        session = session_at(agitation_fixture(), 0, router_first=True)
        outcome = route(session, said, FakeRouterClient(tool_reply(("record_log", {
            "type": "measurement", "value": "37도에서 800 rpm으로 돌렸어", "evidence": said,
        }))))
        note = outcome.plan.note_record
        self.assertEqual(note["category"], "measurement")
        self.assertEqual(note["deviations"], [])


@unittest.skipUnless(
    SOURCE_PDF.is_file(),
    f"requires the externally licensed Candidate A source PDF at {SOURCE_PDF}",
)
class DeviationVoiceTests(RouterFirstVoiceMixin, unittest.TestCase):
    """Over the WebSocket: recorded, read back, shown."""

    def test_a_deviation_note_is_recorded_read_back_and_shown(self) -> None:
        said = "젤 플러그를 37.2도에서 790 rpm 으로 교반했어, 특이사항으로 메모해 줘"

        async def steps(socket, listener, say, seen):
            await say(1, "프로토콜 시작해줘")
            await say(2, "1단계 완료했어")
            await say(3, "2단계 완료했어")
            await say(4, said)

        socket, seen, _ = self._run(steps, script=(tool_reply(("record_log", {
            "type": "deviation", "value": "젤 플러그를 37.2도에서 790 rpm 으로 교반했어", "evidence": said,
        })),))
        reply = socket.for_turn(4, "reply.delta")[-1]
        self.assertIn("37.2도", reply["text"])
        self.assertIn("790 rpm", reply["text"])
        self.assertIn("37°C", reply["text"])
        self.assertIn("800 rpm", reply["text"])
        self.assertTrue(reply["speech_text"].startswith("편차로 기록했어요."))
        self.assertIn("원문은", reply["speech_text"])
        self.assertEqual(seen["history"][-1]["server"]["result"], "recorded")


if __name__ == "__main__":
    unittest.main()
