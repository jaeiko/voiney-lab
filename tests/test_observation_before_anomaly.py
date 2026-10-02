"""While the step-7 endpoint question is open, a description is read as the
observation first, and only a reported problem is an anomaly.

2026-10-01 voice test (in-gel, experiment record on), at step 7:
  turn 19 "7단계 완료했어"              -> the endpoint question (right)
  turn 20 "어, 결과는 탈색으로 바뀌었어." -> recorded as an anomaly, asked
                                          "어떤 종류의 색 변화를 보셨나요?"
  turn 21 "아니, 아니. 현재 단계 완료했다고. 지금 탈색 상태야."
                                       -> answered as a question about the
                                          term "destained"

The endpoint phrases did not know "탈색으로 바뀌었어" or "탈색 상태야", so the
reply fell through to the general classifier, where any "결과 ... 바뀌" is an
anomaly. Recording it also let the endpoint question go, so turn 21 had
nothing to answer.

Now "탈색으로 바뀌었어", "탈색 상태야", "탈색이 됐어" and "완전히 탈색됐어" are
the step-7 endpoint; a reply the endpoint phrases cannot read is an anomaly
only when it names a problem ("이상해", "문제", "예상과 달라", "터졌어",
"흘렸어"); and an anomaly recorded while the question is open keeps it open.
Kept open after an anomaly, it does not take a bare yes until the endpoint
question has been asked again: the last question the person heard was about
the problem, so a "네" then says nothing about the gel.
"""

from __future__ import annotations

import unittest

from tests.test_voice_pause_resume_persistence import (
    FIXTURE,
    PROVENANCE,
    SOURCE_PDF,
    VoiceSessionHarness,
)
from voiney_lab.curated_protocol import (
    CuratedProtocolAction,
    CuratedProtocolSession,
    _observation_predicate,
    load_curated_protocol_fixture,
)
from voiney_lab.experiment_reports import ExperimentReportStore
from voiney_lab.runtime_routing import route_curated_runtime_turn
from voiney_lab.workspace_store import WorkspaceSettings, initialize_workspace_store

TURN_19 = "7단계 완료했어"
TURN_20 = "어, 결과는 탈색으로 바뀌었어."
TURN_21 = "아니, 아니. 현재 단계 완료했다고. 지금 탈색 상태야."

#: The wordings the person asked to be read as the step-7 endpoint.
REPORTED_ENDPOINT = (
    "탈색으로 바뀌었어",
    "탈색 상태야",
    "탈색이 됐어",
    "완전히 탈색됐어",
)
#: Their spoken variants, which say the same thing, and a correction that
#: opens with "아니" as turn 21 did.
ENDPOINT_VARIANTS = (
    "아니, 아니. 현재 단계 완료했다고. 지금 탈색 상태야.",
    "아니야, 탈색 상태야",
    "결과는 탈색으로 바뀌었어요",
    "탈색으로 바꼈어",
    "지금 탈색 상태예요",
    "탈색된 상태입니다",
    "탈색이 됐어요",
    "탈색됐어",
    "탈색이 되었습니다",
)
#: Negated, questioned, hedged, wished-for or read-back: not a report.
NOT_A_REPORT = (
    "탈색이 안 됐어",
    "탈색됐어?",
    "탈색 상태야?",
    "탈색이 됐나요",
    "탈색으로 바뀌었어?",
    "탈색 상태 아니야",
    "탈색 상태가 아니야",
    "탈색이 덜 됐어",
    "탈색이 됐는지 모르겠어",
    "탈색으로 바뀐 것 같아",
    "탈색이 됐으면 좋겠어",
    "탈색으로 바뀌었으면 좋겠어",
    "아마 탈색 상태야",
    "탈색이 될 때까지 반복합니다",
    "탈색됐어 아니야",
    "아직 탈색 상태야",
    "아직 탈색 됐어",
)


def _step_index(fixture, label: str) -> int:
    return next(
        index for index, step in enumerate(fixture.steps)
        if step.source_label == label
    )


class EndpointWordingTests(unittest.TestCase):
    """The step-7 phrase reader alone; it needs no document."""

    def test_the_reported_wordings_are_the_step_7_endpoint(self):
        for wording in REPORTED_ENDPOINT + ENDPOINT_VARIANTS:
            with self.subTest(wording=wording):
                self.assertEqual(_observation_predicate("7", wording), "positive")

    def test_negated_questioned_and_hedged_wordings_stay_non_positive(self):
        for wording in NOT_A_REPORT:
            with self.subTest(wording=wording):
                self.assertNotEqual(_observation_predicate("7", wording), "positive")

    def test_the_new_wordings_are_step_7_only(self):
        for label in ("6", "8", "9", "20"):
            for wording in REPORTED_ENDPOINT[:3]:
                with self.subTest(label=label, wording=wording):
                    self.assertIsNone(_observation_predicate(label, wording))


@unittest.skipUnless(
    SOURCE_PDF.is_file(),
    f"requires the externally licensed Candidate A source PDF at {SOURCE_PDF}",
)
class OpenEndpointQuestionTests(unittest.TestCase):
    """The session's own decisions, turn by turn, at step 7."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = load_curated_protocol_fixture(FIXTURE, PROVENANCE, SOURCE_PDF)

    def _asked(self) -> tuple[CuratedProtocolSession, int]:
        session = CuratedProtocolSession(self.fixture)
        session.activate_configured()
        self._turn(session, "프로토콜 시작해줘", 1)
        session.current_index = _step_index(self.fixture, "7")
        asked = self._turn(session, TURN_19, 19)
        self.assertEqual(asked.intent_kind, "observation_confirmation_required")
        return session, session.current_index

    @staticmethod
    def _turn(session, text, turn_id):
        return route_curated_runtime_turn(
            session, text, turn_id=turn_id, language="ko",
            configuration_id=1, generation=1,
        ).plan

    def test_a_change_described_without_a_problem_is_not_an_anomaly(self):
        session, step_7 = self._asked()
        plan = self._turn(session, "결과는 색이 바뀌었어", 20)
        self.assertNotEqual(plan.action, CuratedProtocolAction.REPORT_ANOMALY)
        self.assertFalse(plan.reported_anomaly)
        self.assertEqual(plan.intent_kind, "observation_confirmation_reasked")
        self.assertIsNotNone(session.pending_observation_confirmation)
        self.assertEqual(session.current_index, step_7)

    def test_a_reported_problem_is_recorded_and_the_question_stays_open(self):
        for problem in (
            "결과가 예상과 달라",
            "색이 이상해",
            "용액 색깔이 이상하게 변했어",
            "젤 색깔에 문제가 있어",
            "시료를 흘렸어",
        ):
            with self.subTest(problem=problem):
                session, step_7 = self._asked()
                anomaly = self._turn(session, problem, 20)
                self.assertEqual(anomaly.action, CuratedProtocolAction.REPORT_ANOMALY)
                self.assertTrue(anomaly.reported_anomaly)
                self.assertFalse(anomaly.state_changed)
                self.assertIsNotNone(session.pending_observation_confirmation)
                done = self._turn(session, "지금 탈색 상태야", 21)
                self.assertEqual(done.action, CuratedProtocolAction.NEXT)
                self.assertTrue(done.state_changed)
                self.assertTrue(done.reported_observation)
                self.assertEqual(done.observation_predicate, "positive")
                self.assertEqual(session.current_index, step_7 + 1)

    def test_a_yes_right_after_an_anomaly_does_not_release_the_endpoint(self):
        session, step_7 = self._asked()
        self._turn(session, "시료를 흘렸어", 20)
        yes = self._turn(session, "네", 21)
        self.assertFalse(yes.state_changed)
        self.assertEqual(yes.intent_kind, "observation_confirmation_reasked")
        self.assertEqual(session.current_index, step_7)
        # The endpoint question was asked again; a yes now answers it.
        again = self._turn(session, "네", 22)
        self.assertTrue(again.state_changed)
        self.assertEqual(session.current_index, step_7 + 1)

    def test_detail_added_to_the_anomaly_keeps_the_question_open(self):
        session, step_7 = self._asked()
        self._turn(session, "결과가 예상과 달라", 20)
        detail = self._turn(session, "노란색으로 변했어", 21)
        self.assertEqual(detail.intent_kind, "enrich_pending_anomaly")
        self.assertIsNotNone(session.pending_observation_confirmation)
        yes = self._turn(session, "네", 22)
        self.assertFalse(yes.state_changed)
        self.assertEqual(session.current_index, step_7)


@unittest.skipUnless(
    SOURCE_PDF.is_file(),
    f"requires the externally licensed Candidate A source PDF at {SOURCE_PDF}",
)
class VoiceTestReplayTests(VoiceSessionHarness, unittest.TestCase):
    def test_turns_19_to_21_complete_step_7_with_the_observation_recorded(self):
        step_1 = self.fixture.steps[0]
        step_7_index = _step_index(self.fixture, "7")
        step_7 = self.fixture.steps[step_7_index]
        seen = {}

        async def scenario(socket, listener, say):
            await say(1, "프로토콜 시작해줘")
            # Steps 1-6 are not what this replays: move the session and its
            # durable record to step 7 together, as the record would be.
            store = initialize_workspace_store(
                WorkspaceSettings(True, self.workspace_dir)
            )
            try:
                jumped = store.record_experiment_progress(
                    self.principal, listener.session_id,
                    expected_version=listener.experiment_state_version,
                    expected_voice_connection_id=listener.voice_connection_id,
                    event_key="test-setup-step-7",
                    event_type="step_advanced",
                    step_id=step_1.step_id, step_label=step_1.source_label,
                    next_step_id=step_7.step_id,
                    next_step_label=step_7.source_label,
                    payload={"authority": "test_setup"},
                )
            finally:
                store.close()
            listener.experiment_state_version = jumped["version"]
            listener.curated_protocol_session.current_index = step_7_index
            await say(19, TURN_19)
            await say(20, TURN_20)
            seen["after_20"] = self._snapshot(listener)
            await say(21, TURN_21)
            seen["end"] = self._snapshot(listener)
            seen["report_id"] = listener.experiment_report_id

        socket, _ = self._session(scenario)
        decisions = {
            turn_id: socket.for_turn(turn_id, "turn.route_decision")[-1]
            for turn_id in (19, 20, 21)
        }
        self.assertEqual(decisions[19]["action"], "clarify_completion")
        self.assertFalse(decisions[19]["state_mutation"])
        self.assertEqual(decisions[20]["action"], "next")
        self.assertTrue(decisions[20]["state_mutation"])
        self.assertNotIn("이상 사항", socket.reply(20))
        self.assertFalse(decisions[21]["state_mutation"])
        self.assertEqual(self._errors(socket), [])

        self.assertEqual(seen["after_20"]["step_index"], step_7_index + 1)
        end = seen["end"]
        self.assertEqual(end["step_index"], step_7_index + 1)
        durable = end["durable"]
        self.assertEqual(durable["current_step_label"], "8")
        self.assertIn(
            step_7.step_id,
            [item["step_id"] for item in durable["completed_steps"]],
        )
        observed = [
            event for event in durable["events"]
            if event["event_type"] == "observation_recorded"
        ]
        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0]["step_id"], step_7.step_id)

        report = ExperimentReportStore(self.report_db).get_report(seen["report_id"])
        kinds = [event["event_type"] for event in report["events"]]
        self.assertNotIn("anomaly", kinds)
        completed = [
            event for event in report["events"]
            if event["event_type"] == "step_completed"
            and event["step_id"] == step_7.step_id
        ]
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0]["payload"]["observation_predicate"], "positive")
        self.assertIn("탈색으로 바뀌었어", completed[0]["user_wording"])


if __name__ == "__main__":
    unittest.main()
