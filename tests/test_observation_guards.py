"""The endpoint readers at in-gel's repeat steps read an utterance as a whole.

Lane M's combination check (every 1-3 phrase utterance through plan()) found
that the step-7 wordings lane E wrote -- "완전히 탈색…", "젤이 … 투명…",
"색이 … 빠졌…" -- were read as the endpoint reached beside a negation said
before them ("탈색이 안 됐어 완전히 탈색됐어"), beside "아직", and beside a
wish or condition ("완전히 탈색되면"). Lane O measured steps 9 and 20 the same
way and found the same at their wordings ("안 됐어 흰색이야", "흰색이 됐으면
좋겠어"). Each now gets the reading lane M gave its own wordings: a negation
anywhere but an opening "아니" correction is a negative, and "아직", a wish or
a condition is asked again.

The pilot log's "탈색돼 있어" and "탈색이 완료됐어" are now the step-7
endpoint too, under the same reading.

An endpoint reported in the same breath as a problem, while the endpoint
question is open ("완전히 탈색됐는데 튜브가 터졌어"), used to advance on the
endpoint and drop the problem. Now the problem is recorded, nothing moves, and
the question stays open -- without taking a bare "네" -- until the endpoint is
reported again.

A problem left pending for detail at one step used to stay pending after the
step changed: at step 9, "하얗게 변했어" was added to step 7's spill, and the
step-9 endpoint question then would not take a "네". The invitation to add
detail now ends when the step does; what was recorded stays recorded.
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

#: Lane E's step-7 wordings, which read as before on their own.
STEP_7_FRAMES = (
    "완전히 탈색됐어",
    "완전히 탈색",
    "젤이 투명해",
    "젤이 이제 완전히 투명해졌어",
    "색이 빠졌어",
    "색이 완전히 빠졌습니다",
)
#: Steps 9 and 20's wordings.
STEP_9_FRAMES = (
    "흰색으로 변했어",
    "흰색이 됐어",
    "흰색이야",
    "흰색으로 바뀌었어",
    "탈수됐어",
    "완전히 말랐어",
)


def _beside(frame: str) -> dict[str, tuple[str, ...]]:
    """The combinations the check found, around one wording."""

    return {
        "negative": (
            f"탈색이 안 됐어 {frame}",
            f"안 됐어 {frame}",
            f"덜 됐어 {frame}",
            f"{frame} 근데 안 됐어",
            f"{frame} 아니야",
            f"맞아 아니 {frame}",
        ),
        "unread": (
            f"아직 {frame}",
            f"{frame} 아직",
            f"{frame} 탈색되면",
            f"{frame} 탈색이 됐으면 좋겠어",
            f"흰색이 됐으면 좋겠어 {frame}",
            f"흰색으로 변하면 {frame}",
            f"{frame} 될 거야",
        ),
    }


class StepSevenFramesReadAsAWholeTests(unittest.TestCase):
    def test_the_frames_alone_and_after_a_correction_still_report_it(self):
        for frame in STEP_7_FRAMES:
            for wording in (frame, f"아니 {frame}", f"아니야, {frame}", f"어 {frame}"):
                with self.subTest(wording=wording):
                    self.assertEqual(_observation_predicate("7", wording), "positive")

    def test_a_negation_anywhere_makes_it_a_negative(self):
        for frame in STEP_7_FRAMES:
            for wording in _beside(frame)["negative"]:
                with self.subTest(wording=wording):
                    self.assertEqual(_observation_predicate("7", wording), "negative")

    def test_not_yet_a_wish_or_a_condition_is_asked_again(self):
        for frame in STEP_7_FRAMES:
            for wording in _beside(frame)["unread"] + (
                "완전히 탈색되면",
                "젤이 투명해지면",
                "색이 빠지면",
            ):
                with self.subTest(wording=wording):
                    self.assertIsNone(_observation_predicate("7", wording))

    def test_the_english_frames_read_as_before(self):
        self.assertEqual(_observation_predicate("7", "fully destained"), "positive")
        self.assertEqual(
            _observation_predicate("7", "the gel is now transparent"), "positive"
        )
        self.assertEqual(_observation_predicate("7", "not fully destained"), "negative")


class StepNineAndTwentyFramesReadAsAWholeTests(unittest.TestCase):
    def test_the_frames_alone_and_after_a_correction_still_report_it(self):
        for label in ("9", "20"):
            for frame in STEP_9_FRAMES:
                for wording in (frame, f"아니 {frame}", f"어 {frame}"):
                    with self.subTest(step=label, wording=wording):
                        self.assertEqual(
                            _observation_predicate(label, wording), "positive"
                        )

    def test_a_negation_anywhere_makes_it_a_negative(self):
        for label in ("9", "20"):
            for frame in STEP_9_FRAMES:
                for wording in _beside(frame)["negative"]:
                    with self.subTest(step=label, wording=wording):
                        self.assertEqual(
                            _observation_predicate(label, wording), "negative"
                        )

    def test_not_yet_a_wish_or_a_condition_is_asked_again(self):
        for label in ("9", "20"):
            for frame in STEP_9_FRAMES:
                for wording in _beside(frame)["unread"] + (
                    "흰색이 됐으면 좋겠어",
                    "흰색으로 변했다면",
                ):
                    with self.subTest(step=label, wording=wording):
                        self.assertIsNone(_observation_predicate(label, wording))

    def test_the_english_frames_read_as_before(self):
        for label in ("9", "20"):
            with self.subTest(step=label):
                self.assertEqual(
                    _observation_predicate(label, "it turned white"), "positive"
                )
                self.assertEqual(
                    _observation_predicate(label, "not white yet"), "negative"
                )


#: The pilot log's B and C, and the wordings they stand for.
STEP_7_WORDINGS = (
    "어, 결과는 탈색, 탈색돼 있어.",
    "아니, 아, 7단계로 완료했다고. 탈색이 완료됐어.",
    "탈색돼 있어",
    "탈색돼 있어요",
    "탈색이 되어 있어",
    "탈색이 되어 있습니다",
    "탈색되어 있네",
    "탈색이 완료됐어",
    "탈색 완료됐어요",
    "탈색이 완료되었습니다",
    "탈색 완료",
    "탈색 완료야",
    "탈색 완료입니다",
    "지금 탈색 완료.",
)


class StepSevenSpokenWordingsTests(unittest.TestCase):
    def test_the_pilot_wordings_report_the_endpoint(self):
        for wording in STEP_7_WORDINGS:
            with self.subTest(wording=wording):
                self.assertEqual(_observation_predicate("7", wording), "positive")

    def test_they_are_read_as_a_whole_like_the_others(self):
        for frame in ("탈색돼 있어", "탈색이 완료됐어", "탈색 완료"):
            for wording in _beside(frame)["negative"] + ("탈색 완료 아니야",):
                with self.subTest(wording=wording):
                    self.assertEqual(_observation_predicate("7", wording), "negative")
            for wording in _beside(frame)["unread"]:
                with self.subTest(wording=wording):
                    if wording == "아직 탈색돼 있어":
                        # Lane E's "아직 … 탈색 … 있어" (still stained) reads
                        # this first, as a negative.
                        self.assertEqual(
                            _observation_predicate("7", wording), "negative"
                        )
                    else:
                        self.assertIsNone(_observation_predicate("7", wording))
        # A negation inside the wording leaves no wording to read: asked again.
        self.assertIsNone(_observation_predicate("7", "탈색이 완료 안 됐어"))

    def test_a_plan_a_time_or_a_question_about_it_is_no_report(self):
        for wording in (
            "탈색 완료 전이야",
            "탈색 완료하면 다음 단계야",
            "탈색 완료 시간은 얼마야",
            "탈색 완료 여부",
            "탈색 완료 기준이 뭐야",
            "탈색이 완료됐으면 좋겠어",
            "탈색이 완료되면",
            "탈색 완료됐어?",
            "탈색돼 있어?",
            "탈색이 완료됐는지 모르겠어",
            "탈색돼 있는 것 같아",
            "탈색이 완료될 때까지 반복합니다",
        ):
            with self.subTest(wording=wording):
                self.assertNotEqual(_observation_predicate("7", wording), "positive")

    def test_they_are_step_7_only(self):
        for label in ("6", "8", "9", "20"):
            for wording in ("탈색돼 있어", "탈색이 완료됐어", "탈색 완료"):
                with self.subTest(step=label, wording=wording):
                    self.assertIsNone(_observation_predicate(label, wording))


def _step_index(fixture, label: str) -> int:
    return next(
        index for index, step in enumerate(fixture.steps)
        if step.source_label == label
    )


@unittest.skipUnless(
    SOURCE_PDF.is_file(),
    f"requires the externally licensed Candidate A source PDF at {SOURCE_PDF}",
)
class EndpointWithAProblemTests(unittest.TestCase):
    """The session's own decisions while the endpoint question is open."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = load_curated_protocol_fixture(FIXTURE, PROVENANCE, SOURCE_PDF)

    @staticmethod
    def _turn(session, text, turn_id):
        return route_curated_runtime_turn(
            session, text, turn_id=turn_id, language="ko",
            configuration_id=1, generation=1,
        ).plan

    def _asked(self, label: str, *before: str) -> tuple[CuratedProtocolSession, int, int]:
        """A session at ``label`` with its endpoint question open, the step
        index, and the turn id the question is open for."""

        session = CuratedProtocolSession(self.fixture)
        session.activate_configured()
        self._turn(session, "프로토콜 시작해줘", 1)
        session.current_index = _step_index(self.fixture, label)
        asked = self._turn(session, f"{label}단계 완료했어", 19)
        self.assertEqual(asked.intent_kind, "observation_confirmation_required")
        for turn_id, text in enumerate(before, start=20):
            self._turn(session, text, turn_id)
        self.assertIsNotNone(session.pending_observation_confirmation)
        return session, session.current_index, 20 + len(before)

    def _assert_recorded_and_held(self, session, at, plan):
        self.assertEqual(plan.action, CuratedProtocolAction.REPORT_ANOMALY)
        self.assertEqual(plan.intent_kind, "observation_with_anomaly")
        self.assertTrue(plan.reported_anomaly)
        self.assertFalse(plan.state_changed)
        self.assertFalse(plan.reported_observation)
        self.assertEqual(session.current_index, at)
        self.assertEqual(session.endpoint_observations(), {})
        pending = session.pending_observation_confirmation
        self.assertIsNotNone(pending)
        self.assertFalse(pending.accepts_yes_no)
        self.assertIn("종점", plan.display_text)

    def test_the_problem_is_recorded_and_the_endpoint_asked_again(self):
        cases = (
            ("7", "완전히 탈색됐는데 튜브가 터졌어", "sample_deviation"),
            ("7", "탈색 상태야 근데 시료를 흘렸어", "sample_deviation"),
            ("7", "젤이 투명해 그런데 결과가 예상과 달라", "protocol_block"),
            ("7", "탈색돼 있어 근데 뭔가 이상해", "protocol_block"),
            ("9", "흰색이야 근데 튜브가 터졌어", "sample_deviation"),
            ("20", "탈수됐어 그런데 장비가 멈췄어", "equipment_issue"),
        )
        for label, reply, category in cases:
            for before in ((), ("시료를 흘렸어",)):
                with self.subTest(step=label, reply=reply, after_anomaly=bool(before)):
                    session, at, turn_id = self._asked(label, *before)
                    plan = self._turn(session, reply, turn_id)
                    self._assert_recorded_and_held(session, at, plan)
                    self.assertEqual(plan.anomaly_category, category)
                    self.assertEqual(plan.anomaly_text, reply)

    def test_a_bare_yes_then_does_not_release_the_endpoint(self):
        session, at, _turn_id = self._asked("7")
        self._turn(session, "완전히 탈색됐는데 튜브가 터졌어", 20)
        yes = self._turn(session, "네", 21)
        self.assertFalse(yes.state_changed)
        self.assertEqual(yes.intent_kind, "observation_confirmation_reasked")
        self.assertEqual(session.current_index, at)
        self.assertEqual(session.endpoint_observations(), {})

    def test_the_endpoint_said_again_advances_with_the_observation(self):
        session, at, _turn_id = self._asked("7")
        self._turn(session, "완전히 탈색됐는데 튜브가 터졌어", 20)
        done = self._turn(session, "완전히 탈색됐어", 21)
        self.assertEqual(done.action, CuratedProtocolAction.NEXT)
        self.assertTrue(done.state_changed)
        self.assertTrue(done.reported_observation)
        self.assertEqual(done.observation_predicate, "positive")
        self.assertEqual(session.current_index, at + 1)
        self.assertEqual(
            session.endpoint_observations()[self.fixture.steps[at].step_id]["utterance"],
            "완전히 탈색됐어",
        )

    def test_saying_there_is_no_problem_still_reports_the_endpoint(self):
        session, at, _turn_id = self._asked("7")
        plan = self._turn(session, "완전히 탈색됐어 문제가 없어", 20)
        self.assertEqual(plan.intent_kind, "pending_observation_confirmed")
        self.assertTrue(plan.state_changed)
        self.assertEqual(session.current_index, at + 1)


@unittest.skipUnless(
    SOURCE_PDF.is_file(),
    f"requires the externally licensed Candidate A source PDF at {SOURCE_PDF}",
)
class EndpointWithAProblemVoiceTests(VoiceSessionHarness, unittest.TestCase):
    def test_the_report_keeps_the_problem_and_step_7_completes_only_later(self):
        step_1 = self.fixture.steps[0]
        step_7_index = _step_index(self.fixture, "7")
        step_7 = self.fixture.steps[step_7_index]
        seen = {}

        async def scenario(socket, listener, say):
            await say(1, "프로토콜 시작해줘")
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
            await say(19, "7단계 완료했어")
            await say(20, "완전히 탈색됐는데 튜브가 터졌어")
            seen["after_20"] = self._snapshot(listener)
            seen["held_20"] = (
                listener.curated_protocol_session.pending_observation_confirmation
            )
            await say(21, "완전히 탈색됐어")
            seen["end"] = self._snapshot(listener)
            seen["report_id"] = listener.experiment_report_id

        socket, _ = self._session(scenario)
        decision_20 = socket.for_turn(20, "turn.route_decision")[-1]
        self.assertEqual(decision_20["action"], "report_anomaly")
        self.assertFalse(decision_20["state_mutation"])
        # The words said here are the server's record acknowledgement, which
        # replaces the session's reply once the report accepts the anomaly
        # (server.py, REPORT_ANOMALY). The question itself is held.
        self.assertIsNotNone(seen["held_20"])
        self.assertFalse(seen["held_20"].accepts_yes_no)
        self.assertEqual(self._errors(socket), [])

        after_20 = seen["after_20"]
        self.assertEqual(after_20["step_index"], step_7_index)
        self.assertEqual(after_20["durable"]["current_step_label"], "7")
        # The anomaly is on the timeline as a deviation; no appearance
        # (endpoint) observation is.
        self.assertEqual(
            [
                event["payload"]["category"]
                for event in after_20["durable"]["events"]
                if event["event_type"] == "observation_recorded"
            ],
            ["deviation"],
        )
        end = seen["end"]
        self.assertEqual(end["step_index"], step_7_index + 1)
        self.assertEqual(end["durable"]["current_step_label"], "8")

        report = ExperimentReportStore(self.report_db).get_report(seen["report_id"])
        anomalies = [
            event for event in report["events"] if event["event_type"] == "anomaly"
        ]
        self.assertEqual(len(anomalies), 1)
        self.assertEqual(anomalies[0]["step_id"], step_7.step_id)
        self.assertIn("튜브가 터졌어", anomalies[0]["user_wording"])
        completed = [
            event for event in report["events"]
            if event["event_type"] == "step_completed"
            and event["step_id"] == step_7.step_id
        ]
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0]["payload"]["observation_predicate"], "positive")
        self.assertEqual(completed[0]["user_wording"], "완전히 탈색됐어")


@unittest.skipUnless(
    SOURCE_PDF.is_file(),
    f"requires the externally licensed Candidate A source PDF at {SOURCE_PDF}",
)
class PendingAnomalyEndsWithTheStepTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = load_curated_protocol_fixture(FIXTURE, PROVENANCE, SOURCE_PDF)

    @staticmethod
    def _turn(session, text, turn_id):
        return route_curated_runtime_turn(
            session, text, turn_id=turn_id, language="ko",
            configuration_id=1, generation=1,
        ).plan

    def _at_step_9_after_a_step_7_anomaly(self) -> CuratedProtocolSession:
        session = CuratedProtocolSession(self.fixture)
        session.activate_configured()
        self._turn(session, "프로토콜 시작해줘", 1)
        session.current_index = _step_index(self.fixture, "7")
        self._turn(session, "7단계 완료했어", 2)
        anomaly = self._turn(session, "시료를 흘렸어", 3)
        self.assertTrue(anomaly.reported_anomaly)
        self.assertTrue(session.awaiting_server_confirmation)
        self._turn(session, "완전히 탈색됐어", 4)
        self.assertEqual(session.current_index, _step_index(self.fixture, "8"))
        return session

    def test_the_pending_anomaly_ends_when_the_step_changes(self):
        session = self._at_step_9_after_a_step_7_anomaly()
        self.assertIsNone(session._pending_anomaly)
        # Nothing is left for the server to own the next turn with.
        self.assertFalse(session.awaiting_server_confirmation)

    def test_a_yes_to_the_next_endpoint_question_is_not_held_back(self):
        session = self._at_step_9_after_a_step_7_anomaly()
        self._turn(session, "8단계 완료했어", 5)
        step_9 = _step_index(self.fixture, "9")
        self.assertEqual(session.current_index, step_9)
        asked = self._turn(session, "9단계 완료했어", 6)
        self.assertEqual(asked.intent_kind, "observation_confirmation_required")
        described = self._turn(session, "하얗게 변했어", 7)
        self.assertNotEqual(described.intent_kind, "enrich_pending_anomaly")
        self.assertFalse(described.reported_anomaly)
        self.assertEqual(described.intent_kind, "observation_confirmation_reasked")
        self.assertTrue(session.pending_observation_confirmation.accepts_yes_no)
        yes = self._turn(session, "네", 8)
        self.assertEqual(yes.intent_kind, "pending_observation_confirmed")
        self.assertTrue(yes.state_changed)
        self.assertEqual(session.current_index, step_9 + 1)

    def test_detail_at_the_same_step_is_still_added_to_it(self):
        session = CuratedProtocolSession(self.fixture)
        session.activate_configured()
        self._turn(session, "프로토콜 시작해줘", 1)
        session.current_index = _step_index(self.fixture, "7")
        self._turn(session, "7단계 완료했어", 2)
        self._turn(session, "결과가 예상과 달라", 3)
        detail = self._turn(session, "노란색으로 변했어", 4)
        self.assertEqual(detail.intent_kind, "enrich_pending_anomaly")
        self.assertIsNotNone(session._pending_anomaly)


@unittest.skipUnless(
    SOURCE_PDF.is_file(),
    f"requires the externally licensed Candidate A source PDF at {SOURCE_PDF}",
)
class PendingAnomalyEndsWithTheStepVoiceTests(VoiceSessionHarness, unittest.TestCase):
    def test_the_step_7_anomaly_stays_recorded_and_step_9_completes_on_yes(self):
        step_1 = self.fixture.steps[0]
        step_7_index = _step_index(self.fixture, "7")
        step_7 = self.fixture.steps[step_7_index]
        step_9 = self.fixture.steps[_step_index(self.fixture, "9")]
        seen = {}

        async def scenario(socket, listener, say):
            await say(1, "프로토콜 시작해줘")
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
            for turn_id, text in (
                (19, "7단계 완료했어"),
                (20, "시료를 흘렸어"),
                (21, "완전히 탈색됐어"),
                (22, "8단계 완료했어"),
                (23, "9단계 완료했어"),
                (24, "하얗게 변했어"),
                (25, "네"),
            ):
                await say(turn_id, text)
            seen["end"] = self._snapshot(listener)
            seen["report_id"] = listener.experiment_report_id

        socket, _ = self._session(scenario)
        self.assertEqual(self._errors(socket), [])
        self.assertEqual(socket.for_turn(24, "turn.route_decision")[-1]["action"], "clarify_completion")
        self.assertTrue(socket.for_turn(25, "turn.route_decision")[-1]["state_mutation"])
        self.assertEqual(seen["end"]["durable"]["current_step_label"], "10")

        report = ExperimentReportStore(self.report_db).get_report(seen["report_id"])
        anomalies = [
            event for event in report["events"] if event["event_type"] == "anomaly"
        ]
        self.assertEqual(
            [(event["step_id"], event["user_wording"]) for event in anomalies],
            [(step_7.step_id, "시료를 흘렸어")],
        )
        completed = {
            event["step_id"] for event in report["events"]
            if event["event_type"] == "step_completed"
        }
        self.assertLessEqual({step_7.step_id, step_9.step_id}, completed)


if __name__ == "__main__":
    unittest.main()
