"""Lane CF, decision 3 (field interviews, 2026-10-07/08): going back, by voice only.

"이전 단계로 돌아가", "N단계로 돌아가" (an earlier step) and "방금 완료 취소"
are asked about once -- "3단계 완료를 취소하고 3단계로 돌아갈까요?" -- and a
yes moves the run there and records a step_reverted event. The completion
records stay as they were: the revert is appended beside them. A return
within a repeat said at the step that states it (lane R7) stays a return; a
revert says whether it went back inside a repeat or outside one, and the
report lists it under "원문과 다르게 한 점".
"""

from __future__ import annotations

import unittest

from tests.lane_cf_support import RecordedSession, Session, VoiceNotesHarness, index_of, shown
from voiney_lab import experiment_reports as er
from voiney_lab import server as server_module
from voiney_lab.curated_protocol import CuratedProtocolFixtureError, step_revert_request


class RevertWordsTests(unittest.TestCase):

    def test_the_words_that_go_back(self) -> None:
        cases = {
            "이전 단계로 돌아가": ("previous", None),
            "이전 단계로 돌아가 줘": ("previous", None),
            "전 단계로 돌아가자": ("previous", None),
            "앞 단계로 돌아갈래": ("previous", None),
            "방금 완료 취소": ("undo_completion", None),
            "방금 완료 취소해 줘": ("undo_completion", None),
            "완료 취소해": ("undo_completion", None),
            "방금 완료한 거 취소해 줘": ("undo_completion", None),
            "3단계 완료 취소해 줘": ("undo_completion", 3),
        }
        for said, expected in cases.items():
            with self.subTest(said=said):
                request = step_revert_request(said)
                self.assertIsNotNone(request)
                self.assertEqual((request.kind, request.number), expected)

    def test_questions_and_other_words_are_not_a_revert(self) -> None:
        for said in ("이전 단계로 돌아가도 돼?", "이전 단계 뭐였지", "완료했어", "취소해",
                     "타이머 취소해", "방금 기록 취소해"):
            with self.subTest(said=said):
                self.assertIsNone(step_revert_request(said))


class RevertRuleTests(Session, unittest.TestCase):

    def at_seven(self) -> None:
        self.open_with(index_of("3"))
        self.complete("3", "4", "5", "6")
        self.assertEqual(self.label(), "7")

    def test_previous_step_is_asked_then_moved_with_the_completion_kept(self) -> None:
        self.at_seven()
        plan = self.say("이전 단계로 돌아가")
        self.assertEqual(plan.display_text, "6단계 완료를 취소하고 6단계로 돌아갈까요?")
        self.assertFalse(plan.state_changed)
        self.assertEqual(self.session.last_front_rule, "step_revert")
        plan = self.say("응")
        self.assertTrue(plan.state_changed)
        self.assertEqual(self.label(), "6")
        self.assertTrue(plan.speech_text.startswith("6단계 완료를 취소하고 6단계로 돌아왔어요."))
        record = plan.step_record
        self.assertEqual(record["kind"], "step_revert")
        self.assertEqual(record["from_step"], "7")
        self.assertEqual(record["to_step"], "6")
        self.assertEqual(record["reverted_step_labels"], ["6"])
        self.assertEqual(record["reverted_step_ids"], ["step-6"])
        self.assertEqual(record["said"], "previous")
        self.assertIsNone(record["in_repeat"])
        # Done again, the completion is a second one: the first is kept.
        plan = self.say("6단계 완료했어")
        self.assertEqual(self.label(), "7")
        self.assertEqual(plan.step_record, {"kind": "completed_again", "completed_before": True})

    def test_a_named_earlier_step_takes_back_every_completion_after_it(self) -> None:
        self.at_seven()
        self.assertEqual(self.text("4단계로 돌아가"), "4~6단계 완료를 취소하고 4단계로 돌아갈까요?")
        plan = self.say("네")
        self.assertEqual(self.label(), "4")
        self.assertEqual(plan.step_record["reverted_step_labels"], ["4", "5", "6"])
        self.assertEqual(plan.step_record["said"], "named_step")

    def test_taking_back_the_last_completion(self) -> None:
        self.at_seven()
        self.assertEqual(self.text("방금 완료 취소"), "6단계 완료를 취소하고 6단계로 돌아갈까요?")
        plan = self.say("응")
        self.assertEqual(plan.step_record["said"], "undo_completion")
        self.assertEqual(self.label(), "6")
        self.assertEqual(self.text("3단계 완료 취소해 줘"), "3~5단계 완료를 취소하고 3단계로 돌아갈까요?")

    def test_a_no_keeps_the_step(self) -> None:
        self.at_seven()
        before = self.projection()
        self.say("이전 단계로 돌아가")
        plan = self.say("아니")
        self.assertEqual(plan.display_text, "알겠습니다. 되돌리지 않았습니다. 지금 7단계입니다.")
        self.assertEqual(self.projection(), before)

    def test_what_cannot_be_gone_back_to(self) -> None:
        self.open_with(0)
        self.assertEqual(
            self.text("이전 단계로 돌아가"),
            "1단계가 첫 단계라 돌아갈 단계가 없어요. 지금 1단계를 유지합니다.")
        self.at_seven()
        self.assertEqual(self.text("7단계로 돌아가"), "지금이 7단계예요. 단계를 옮기지 않았어요.")
        self.assertIn("50단계는 없어서", self.text("50단계로 돌아가"))
        self.assertIsNone(self.session._pending_step_move)

    def test_a_steps_own_timer_is_left_behind(self) -> None:
        self.at_seven()
        self.say("이전 단계로 돌아가")
        self.say("응")
        self.assertEqual(self.session.timer_status()["state"], "not_started")


class RepeatRevertTests(Session, unittest.TestCase):
    """Lane R7's return at the step that states the repeat is unchanged."""

    def at_sixteen(self) -> None:
        self.open_with(index_of("12"))
        self.complete("12", "13", "14", "15")
        self.assertEqual(self.label(), "16")

    def test_the_return_at_the_stating_step_is_still_a_return(self) -> None:
        self.at_sixteen()
        self.assertEqual(self.text("12단계로 돌아가"), "12단계로 돌아갈까요?")
        plan = self.say("응")
        self.assertEqual(plan.step_record["kind"], "repeat_return")

    def test_going_back_inside_a_repeat_says_so(self) -> None:
        self.open_with(index_of("12"))
        self.complete("12", "13")
        self.assertEqual(self.text("12단계로 돌아가"), "12~13단계 완료를 취소하고 12단계로 돌아갈까요?")
        plan = self.say("응")
        self.assertEqual(plan.step_record["kind"], "step_revert")
        self.assertEqual(plan.step_record["in_repeat"], {
            "repetition_id": "repeat-12-15", "repeated_step_labels": ["12", "15"]})

    def test_previous_at_the_stating_step_is_a_revert(self) -> None:
        self.at_sixteen()
        self.assertEqual(self.text("이전 단계로 돌아가"), "15단계 완료를 취소하고 15단계로 돌아갈까요?")
        plan = self.say("응")
        self.assertEqual(plan.step_record["kind"], "step_revert")
        self.assertEqual(plan.step_record["in_repeat"]["repetition_id"], "repeat-12-15")

    def test_an_earlier_step_outside_the_repeat_is_a_revert_outside(self) -> None:
        self.at_sixteen()
        self.assertEqual(self.text("11단계로 돌아가"), "12~15단계 완료를 취소하고 11단계로 돌아갈까요?")
        plan = self.say("응")
        self.assertEqual(self.label(), "11")
        self.assertIsNone(plan.step_record["in_repeat"])


class RecoveryTests(Session, unittest.TestCase):

    def test_a_reverted_run_is_restored_with_its_reverted_steps(self) -> None:
        self.open_with(None)
        completed = ("step-1", "step-2", "step-3", "step-4")
        with self.assertRaises(CuratedProtocolFixtureError):
            self.session.restore_experiment_progress(
                current_step_id="step-3", completed_step_ids=completed)
        self.session.restore_experiment_progress(
            current_step_id="step-3", completed_step_ids=completed,
            reverted_step_ids=("step-3", "step-4"))
        self.assertEqual(self.label(), "3")
        plan = self.say("3단계 완료했어")
        self.assertEqual(plan.step_record, {"kind": "completed_again", "completed_before": True})

    def test_the_durable_record_names_the_reverted_steps(self) -> None:
        state = {"events": [
            {"event_type": "step_completed", "payload": {}},
            {"event_type": "step_reverted",
             "payload": {"step_record": {"kind": "step_revert", "reverted_step_ids": ["step-3", "step-4"]}}},
        ]}
        self.assertEqual(server_module._workspace_reverted_step_ids(state), ("step-3", "step-4"))


class CheckpointTests(unittest.TestCase):
    """A stopped run offers no place that carries a completion a revert took back."""

    def test_places_after_a_revert(self) -> None:
        from voiney_lab.workspace_store import WorkspaceStore

        def completion(step: int, event_id: str) -> dict:
            return {"event_id": event_id, "step_id": f"step-{step}", "step_label": str(step),
                    "completed_at": f"2026-10-08T10:0{step}:00+00:00"}

        def progress(step: int, event_id: str) -> dict:
            return {"event_id": event_id, "event_type": "step_completed",
                    "step_id": f"step-{step}", "step_label": str(step), "payload": {}}

        session = {
            "status": "stopped", "current_step_id": "step-2", "current_step_label": "2",
            "completed_steps": [completion(1, "e1"), completion(2, "e2"), completion(3, "e3")],
            "events": [
                progress(1, "e1"), progress(2, "e2"), progress(3, "e3"),
                {"event_id": "e4", "event_type": "step_reverted", "step_id": "step-4",
                 "step_label": "4", "payload": {"step_record": {
                     "kind": "step_revert", "reverted_step_ids": ["step-2", "step-3"]}}},
            ],
        }
        places = WorkspaceStore._experiment_checkpoints(session)
        self.assertEqual(
            [(place["step_id"], place["carried_step_ids"], place["stopped_here"]) for place in places],
            [("step-2", ["step-1"], True)],
        )


class RevertReportTests(RecordedSession, unittest.TestCase):

    def setUp(self) -> None:
        self.start_recording()

    def facts(self) -> er.ReportFacts:
        return er.build_report_facts(self.report(), fixture=self.session.fixture)

    def test_the_revert_is_an_event_and_a_point_done_differently(self) -> None:
        self.open_with(None)
        self.record("프로토콜 시작해줘")
        self.session.current_index = index_of("3")
        for label in ("3", "4", "5", "6"):
            self.record(f"{label}단계 완료했어")
        self.record("4단계로 돌아가")
        self.record("응")
        kinds = [kind for kind, _, _ in self.events()]
        self.assertEqual(kinds.count("step_completed"), 4)
        self.assertEqual(kinds[-1], "step_reverted")
        _, label, record = self.events()[-1]
        self.assertEqual(label, "4")
        self.assertEqual(record["reverted_step_labels"], ["4", "5", "6"])
        lines = [line for line in self.facts().deviations if "되돌아갔다" in line]
        self.assertEqual(len(lines), 1)
        self.assertRegex(
            lines[0],
            r"^7단계에서 4단계로 되돌아갔다 — 4~6단계 완료 취소\(처음 완료 기록은 남김\), "
            r"반복 구간 밖, \d\d:\d\d\.$")

    def test_inside_a_repeat_the_report_says_which(self) -> None:
        self.open_with(None)
        self.record("프로토콜 시작해줘")
        self.session.current_index = index_of("12")
        self.record("12단계 완료했어")
        self.record("이전 단계로 돌아가")
        self.record("응")
        lines = [line for line in self.facts().deviations if "되돌아갔다" in line]
        self.assertRegex(lines[0], r"^13단계에서 12단계로 되돌아갔다 — 12단계 완료 취소\(처음 완료 기록은 남김\), "
                                   r"반복 구간 12~15단계 안, \d\d:\d\d\.$")


class ServedRevertTests(VoiceNotesHarness, unittest.TestCase):
    """The workspace keeps the completion and appends the revert; the run continues."""

    def test_the_workspace_keeps_the_completion_and_the_run_goes_on(self) -> None:
        socket = self.turns(
            "프로토콜 시작해줘", "1단계 완료했어", "이전 단계로 돌아가", "응", "1단계 완료했어")
        replies = shown(socket)
        self.assertEqual(replies[3], "1단계 완료를 취소하고 1단계로 돌아갈까요?")
        self.assertIn("1단계 완료를 취소하고 1단계로 돌아왔어요.", replies[4])
        experiment = self.experiment()
        self.assertEqual(experiment["current_step_label"], "2")
        self.assertEqual([item["step_id"] for item in experiment["completed_steps"]], ["step-1"])
        kinds = [event["event_type"] for event in experiment["events"]]
        self.assertIn("step_reverted", kinds)
        reverted = next(e for e in experiment["events"] if e["event_type"] == "step_reverted")
        self.assertEqual(reverted["payload"]["step_record"]["reverted_step_ids"], ["step-1"])
        self.assertIn("step_reverted", [e["event_type"] for e in self.report()["events"]])


if __name__ == "__main__":
    unittest.main()
