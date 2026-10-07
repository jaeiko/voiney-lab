"""The report ledger on the served path, and words kept as said (lane N, decisions 4-5).

4. A timer start, a pause and a resume reach the experiment report's ledger on
   the real server path (run_turn), so the report's timer column and its
   "(나)" are right: before, server.py's _EXPERIMENT_REPORT_ACTIONS left the
   three out, and a report said "타이머 기록 없음" for a timer that ran.
   Kept only when they took effect: a second "타이머 시작해줘" adds nothing.
5. Observations and notes are stored as the STT gave them -- "LB 배지가 …"
   is not "lb 배지가 …". Reading the words still uses the normalized key;
   only what is stored changed.
"""

from __future__ import annotations

import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from tests.lane_n_support import VoiceNotesHarness
from tests.protocol_vocabulary_support import SOURCE_PDF, in_gel_fixture
from tests.test_lane_r7_rule_gaps import _Turns
from tests.lane_n_support import notes_fixture
from voiney_lab.curated_protocol import _observation_capture


class _Clock:
    """One clock for the timer, the pause and the report's event times."""

    def __init__(self) -> None:
        self.now = time.time()

    def advance(self, minutes: float) -> None:
        self.now += minutes * 60

    def iso(self) -> str:
        return datetime.fromtimestamp(self.now, timezone.utc).isoformat()


class ServedTimerLedgerTests(VoiceNotesHarness, unittest.TestCase):
    """Decision 4 through run_turn, the experiment report store and the export."""

    def run_timed(self, steps) -> None:
        clock = _Clock()

        async def scenario(socket, listener, say):
            turn = 0
            for item in steps:
                if isinstance(item, (int, float)):
                    clock.advance(item)
                    continue
                turn += 1
                await say(turn, item)

        self.voice(
            scenario,
            patch("voiney_lab.curated_protocol.time.time", side_effect=lambda: clock.now),
            patch("voiney_lab.experiment_reports._now", side_effect=clock.iso),
        )

    def test_a_timer_paused_and_resumed_reaches_the_report(self) -> None:
        self.run_timed((
            "프로토콜 시작해줘", "1단계 완료했어",
            "타이머 시작해줘", 1, "타이머 시작해줘", 3,
            "잠깐 멈춰", 3, "다시 시작", 5,
            "2단계 완료했어",
        ))
        events = [(e["event_type"], e["step_label"]) for e in self.report()["events"]]
        for kind in ("timer_started", "workflow_paused", "workflow_resumed"):
            with self.subTest(kind=kind):
                self.assertEqual(events.count((kind, "2")), 1)
        markdown = self.report_markdown()
        self.assertRegex(markdown, r"\| 2 \| [^|]+ \| \d\d:\d\d \| 원문 10분 / 실제 12분 \|")
        self.assertRegex(markdown, r"- 2단계에서 3분 동안 멈췄다가 다시 진행했다\(\d\d:\d\d–\d\d:\d\d\)\.")
        self.assertNotIn("타이머 기록이 없다", markdown)
        self.assertNotIn("타이머 기록 없음", markdown)

    def test_a_timer_ended_early_is_written_as_ended_early(self) -> None:
        self.run_timed((
            "프로토콜 시작해줘", "1단계 완료했어", "타이머 시작해줘", 6, "2단계 완료했어",
        ))
        markdown = self.report_markdown()
        self.assertRegex(markdown, r"\| 2 \| [^|]+ \| \d\d:\d\d \| 원문 10분 / 6분에 끝냄 \|")
        self.assertIn("2단계: 타이머를 일찍 끝낸 것이 결과에 영향을 주었는지 확인한다.", markdown)
        self.assertNotIn("타이머 기록이 없다", markdown)


class VerbatimNoteTests(_Turns, unittest.TestCase):
    """Decision 5 on the rules' path: what is stored is what was said."""

    def test_an_observation_keeps_its_case(self) -> None:
        self.open(1, fixture=notes_fixture())
        plan = self.say("관찰 기록 LB 배지가 맑고 덩어리 없이 다 녹았어")
        self.assertEqual(plan.observation_outcome, "LB 배지가 맑고 덩어리 없이 다 녹았어")

    def test_the_classifier_reads_the_key_and_stores_the_words(self) -> None:
        self.assertEqual(
            _observation_capture("관찰 기록해: LB 배지가 맑아"), ("note", "LB 배지가 맑아"))
        self.assertEqual(
            _observation_capture("Record an observation that The LB plate is CLEAR"),
            ("note", "The LB plate is CLEAR"))
        self.assertEqual(
            _observation_capture("음 관찰 기록해: LB 배지가 맑아"), ("note", "LB 배지가 맑아"))


@unittest.skipUnless(
    SOURCE_PDF.is_file(),
    f"requires the externally licensed Candidate A source PDF at {SOURCE_PDF}",
)
class VerbatimEndpointTests(_Turns, unittest.TestCase):
    """An endpoint observation is stored as said, punctuation included."""

    def test_the_endpoint_answer_is_stored_as_said(self) -> None:
        fixture = in_gel_fixture()
        index = next(i for i, step in enumerate(fixture.steps) if step.source_label == "7")
        self.open(index, fixture=fixture)
        plan = self.say("젤이 완전히 탈색됐어요!")
        self.assertTrue(plan.reported_observation)
        self.assertEqual(plan.observation_outcome, "젤이 완전히 탈색됐어요!")


if __name__ == "__main__":
    unittest.main()
