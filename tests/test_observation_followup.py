"""Lane O's follow-ups: the endpoint with a problem, the paused voice, a range.

An endpoint reported in the same breath as a problem ("완전히 탈색됐는데
튜브가 터졌어") was held while the endpoint question was open (lane O), but
with the experiment record on the server replaced the reply with its record
acknowledgement, so the question asked again was never heard; and with no
question open the same words advanced past the endpoint and dropped the
problem. Both now record the problem, keep the step, leave the endpoint
question open without taking a bare yes, and say -- once the record took it --
"이상 사항은 기록했어요. 탈색이 끝났으면 한 번 더 말씀해 주세요."

"탈색 완료했어" (and "탈수 완료했어" at steps 9 and 20) reports the endpoint,
read as a whole as lane O's wordings are.

"3단계부터 5단계까지 알려줘" on a protocol with no Korean translation failed
with an AttributeError; it reads the source lines now.

While paused, a word other than resume, stop or pause got only an on-screen
notice. The first such word in each pause is now answered aloud, once; the
pause reply and the notice both name the same resume word, '재개'.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from tests.test_digit_step_numbers import _fixture as _ninety_nine_step_fixture
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
import voiney_lab.server as server_module
from voiney_lab.workspace_store import (
    WorkspaceConflictError,
    WorkspaceSettings,
    initialize_workspace_store,
)

#: What is said once the record took the problem, per step.
RECORDED_REASK = {
    "7": "이상 사항은 기록했어요. 탈색이 끝났으면 한 번 더 말씀해 주세요.",
    "9": "이상 사항은 기록했어요. 젤이 하얗게 말랐으면 한 번 더 말씀해 주세요.",
    "20": "이상 사항은 기록했어요. 젤이 하얗게 말랐으면 한 번 더 말씀해 주세요.",
}
#: The record write failing is answered as before.
RECORD_FAILED = "실험 기록을 저장하지 못해 이상 사항이 기록되었다고 확인할 수 없습니다."
PAUSED_REPLY = "일시정지했어요. '재개'라고 하시면 이어서 할게요."
TIMER_RUNS_ON = "타이머는 실제로는 계속 흐르고 있어요."
PAUSED_SPOKEN = "지금 일시정지 중이에요. '재개'라고 말씀해 주세요."
PAUSED_NOTICE = (
    "현재 실험 안내가 일시정지 상태입니다. '재개'라고 말씀하시거나 재개 버튼을 눌러주세요."
)


def _step_index(fixture, label: str) -> int:
    return next(
        index for index, step in enumerate(fixture.steps)
        if step.source_label == label
    )


def _turn(session, text, turn_id):
    return route_curated_runtime_turn(
        session, text, turn_id=turn_id, language="ko",
        configuration_id=1, generation=1,
    ).plan


class CompletedWordingTests(unittest.TestCase):
    """Decision 3, on the reader alone (no document needed)."""

    def test_completed_reports_the_endpoint(self):
        for label, said in (
            ("7", "탈색 완료했어"),
            ("7", "탈색 완료했어요"),
            ("7", "탈색 완료했습니다"),
            ("7", "탈색을 완료했어"),
            ("9", "탈수 완료했어"),
            ("9", "탈수 완료했어요"),
            ("20", "탈수 완료했습니다"),
        ):
            with self.subTest(step=label, said=said):
                self.assertEqual(_observation_predicate(label, said), "positive")

    def test_it_is_read_as_a_whole(self):
        for label, frame in (("7", "탈색 완료했어"), ("9", "탈수 완료했어")):
            for said, reading in (
                (f"{frame[:-1]}으면 좋겠어", None),
                (f"아직 {frame}", None),
                (f"{frame} 아니야", "negative"),
                (f"안 됐어 {frame}", "negative"),
                (f"{frame}?", None),
                (f"{frame} 아마", None),
                (f"{frame} {frame[:-1]}으면 좋겠어", None),
            ):
                with self.subTest(step=label, said=said):
                    self.assertEqual(_observation_predicate(label, said), reading)

    def test_each_wording_stays_at_its_own_step(self):
        self.assertIsNone(_observation_predicate("9", "탈색 완료했어"))
        self.assertIsNone(_observation_predicate("7", "탈수 완료했어"))
        self.assertIsNone(_observation_predicate("8", "탈색 완료했어"))


class _InGelSteps:
    """A session on in-gel at one step, its endpoint question open or not."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = load_curated_protocol_fixture(FIXTURE, PROVENANCE, SOURCE_PDF)

    def _at(self, label: str, *, asked: bool = False):
        session = CuratedProtocolSession(self.fixture)
        session.activate_configured()
        _turn(session, "프로토콜 시작해줘", 1)
        session.current_index = _step_index(self.fixture, label)
        if asked:
            _turn(session, f"{label}단계 완료했어", 19)
            self.assertIsNotNone(session.pending_observation_confirmation)
            return session, session.current_index, 20
        self.assertIsNone(session.pending_observation_confirmation)
        return session, session.current_index, 19


@unittest.skipUnless(
    SOURCE_PDF.is_file(),
    f"requires the externally licensed Candidate A source PDF at {SOURCE_PDF}",
)
class CompletedInTheSessionTests(_InGelSteps, unittest.TestCase):
    """Decision 3 in the session, with the endpoint question open or not."""

    def test_completed_advances_and_a_wish_is_asked_again(self):
        for asked in (True, False):
            with self.subTest(asked=asked):
                session, at, turn_id = self._at("7", asked=asked)
                done = _turn(session, "탈색 완료했어", turn_id)
                self.assertEqual(done.action, CuratedProtocolAction.NEXT)
                self.assertTrue(done.state_changed)
                self.assertEqual(done.observation_predicate, "positive")
                self.assertEqual(session.current_index, at + 1)
        session, at, turn_id = self._at("7", asked=True)
        wish = _turn(session, "탈색 완료했으면 좋겠어", turn_id)
        self.assertFalse(wish.state_changed)
        self.assertEqual(wish.intent_kind, "observation_confirmation_reasked")
        self.assertEqual(session.current_index, at)
        for label in ("9", "20"):
            with self.subTest(step=label):
                session, at, turn_id = self._at(label, asked=True)
                done = _turn(session, "탈수 완료했어", turn_id)
                self.assertTrue(done.state_changed)
                self.assertEqual(session.current_index, at + 1)


@unittest.skipUnless(
    SOURCE_PDF.is_file(),
    f"requires the externally licensed Candidate A source PDF at {SOURCE_PDF}",
)
class EndpointWithAProblemNoQuestionTests(_InGelSteps, unittest.TestCase):
    """Decisions 1 and 2 in the session: no endpoint question is open."""

    def test_the_problem_is_recorded_and_the_endpoint_question_opened(self):
        for label, said in (
            ("7", "완전히 탈색됐는데 튜브가 터졌어"),
            ("7", "탈색 완료했어 근데 시료를 흘렸어"),
            ("9", "흰색이야 근데 튜브가 터졌어"),
            ("20", "탈수 완료했어 그런데 장비가 멈췄어"),
        ):
            with self.subTest(step=label, said=said):
                session, at, turn_id = self._at(label)
                plan = _turn(session, said, turn_id)
                self.assertEqual(plan.action, CuratedProtocolAction.REPORT_ANOMALY)
                self.assertEqual(plan.intent_kind, "observation_with_anomaly")
                self.assertTrue(plan.reported_anomaly)
                self.assertFalse(plan.state_changed)
                self.assertFalse(plan.reported_observation)
                self.assertEqual(session.current_index, at)
                self.assertEqual(session.endpoint_observations(), {})
                pending = session.pending_observation_confirmation
                self.assertIsNotNone(pending)
                self.assertEqual(pending.step_index, at)
                self.assertFalse(pending.accepts_yes_no)
                self.assertTrue(plan.speech_text.endswith(
                    RECORDED_REASK[label].split(". ", 1)[1]
                ))
                # Before the record answers, nothing is said to be recorded.
                self.assertNotIn("기록했어요", plan.speech_text)

    def test_then_a_bare_yes_is_asked_again_and_the_endpoint_advances(self):
        session, at, turn_id = self._at("7")
        _turn(session, "완전히 탈색됐는데 튜브가 터졌어", turn_id)
        yes = _turn(session, "네", turn_id + 1)
        self.assertFalse(yes.state_changed)
        self.assertEqual(yes.intent_kind, "observation_confirmation_reasked")
        self.assertEqual(session.current_index, at)
        done = _turn(session, "완전히 탈색됐어", turn_id + 2)
        self.assertTrue(done.state_changed)
        self.assertEqual(done.observation_predicate, "positive")
        self.assertEqual(session.current_index, at + 1)


def _jump_to(harness, listener, label: str) -> None:
    """Move the live session and its durable record to ``label``."""

    step_1 = harness.fixture.steps[0]
    index = _step_index(harness.fixture, label)
    target = harness.fixture.steps[index]
    store = initialize_workspace_store(WorkspaceSettings(True, harness.workspace_dir))
    try:
        jumped = store.record_experiment_progress(
            harness.principal, listener.session_id,
            expected_version=listener.experiment_state_version,
            expected_voice_connection_id=listener.voice_connection_id,
            event_key=f"test-setup-step-{label}",
            event_type="step_advanced",
            step_id=step_1.step_id, step_label=step_1.source_label,
            next_step_id=target.step_id,
            next_step_label=target.source_label,
            payload={"authority": "test_setup"},
        )
    finally:
        store.close()
    listener.experiment_state_version = jumped["version"]
    listener.curated_protocol_session.current_index = index


@unittest.skipUnless(
    SOURCE_PDF.is_file(),
    f"requires the externally licensed Candidate A source PDF at {SOURCE_PDF}",
)
class EndpointWithAProblemVoiceTests(VoiceSessionHarness, unittest.TestCase):
    """Decisions 1 and 2 over the WebSocket, workspace and report on."""

    def _problem_at_step_7(self, *, asked: bool, extra_patches=()):
        step_7_index = _step_index(self.fixture, "7")
        seen = {}

        async def scenario(socket, listener, say):
            await say(1, "프로토콜 시작해줘")
            _jump_to(self, listener, "7")
            if asked:
                await say(19, "7단계 완료했어")
            await say(20, "완전히 탈색됐는데 튜브가 터졌어")
            seen["after"] = self._snapshot(listener)
            seen["held"] = (
                listener.curated_protocol_session.pending_observation_confirmation
            )
            seen["report_id"] = listener.experiment_report_id

        socket, _ = self._session(scenario, *extra_patches)
        return socket, seen, step_7_index

    def test_the_question_asked_again_is_heard_and_one_problem_recorded(self):
        for asked in (True, False):
            with self.subTest(asked=asked):
                self._fresh_tenant()
                socket, seen, at = self._problem_at_step_7(asked=asked)
                self.assertEqual(self._errors(socket), [])
                decision = socket.for_turn(20, "turn.route_decision")[-1]
                self.assertEqual(decision["action"], "report_anomaly")
                self.assertFalse(decision["state_mutation"])
                self.assertEqual(socket.reply(20), RECORDED_REASK["7"])
                self.assertEqual(
                    socket.for_turn(20, "reply.delta")[-1]["speech_text"],
                    RECORDED_REASK["7"],
                )
                done = socket.for_turn(20, "turn.done")
                self.assertEqual(len(done), 1)
                self.assertEqual(done[0]["segment_count"], 1)
                self.assertEqual(seen["after"]["step_index"], at)
                self.assertEqual(seen["after"]["durable"]["current_step_label"], "7")
                self.assertIsNotNone(seen["held"])
                self.assertFalse(seen["held"].accepts_yes_no)
                report = ExperimentReportStore(self.report_db).get_report(
                    seen["report_id"]
                )
                anomalies = [
                    event for event in report["events"]
                    if event["event_type"] == "anomaly"
                ]
                self.assertEqual(len(anomalies), 1)
                self.assertEqual(
                    anomalies[0]["step_id"], self.fixture.steps[at].step_id
                )
                self.assertIn("튜브가 터졌어", anomalies[0]["user_wording"])
                self.assertFalse([
                    event for event in report["events"]
                    if event["event_type"] == "step_completed"
                    and event["step_id"] == self.fixture.steps[at].step_id
                ])

    def test_a_failed_record_says_so_and_not_recorded(self):
        real = server_module._record_experiment_report_plan

        def refuse_anomaly(session, curated, plan, **kwargs):
            if plan.action is CuratedProtocolAction.REPORT_ANOMALY:
                raise RuntimeError("synthetic report failure")
            return real(session, curated, plan, **kwargs)

        # With the session timeline on, its own acknowledgement follows a
        # failed report write (server.py, unchanged here); with it off, the
        # failure is what is said.
        step_7_index = _step_index(self.fixture, "7")
        seen = {}

        async def scenario(socket, listener, say):
            await say(1, "프로토콜 시작해줘")
            listener.curated_protocol_session.current_index = step_7_index
            await say(20, "완전히 탈색됐는데 튜브가 터졌어")
            seen["step_index"] = listener.curated_protocol_session.current_index

        socket, _ = self._session(
            scenario,
            patch.dict(
                "os.environ",
                {"VOINEY_LAB_WORKSPACE_ENABLED": "false"},
                clear=False,
            ),
            patch(
                "voiney_lab.server._record_experiment_report_plan",
                side_effect=refuse_anomaly,
            ),
        )
        reply = socket.reply(20)
        self.assertIn(RECORD_FAILED, reply)
        self.assertNotIn("기록했어요", reply)
        self.assertEqual(seen["step_index"], step_7_index)


class StepRangeWithoutTranslationTests(unittest.TestCase):
    """Decision 4: a range on a protocol that has no Korean translation."""

    def test_the_source_lines_are_read(self):
        fixture = _ninety_nine_step_fixture()
        session = CuratedProtocolSession(fixture)
        session.active = True
        session.current_index = 0
        plan = session.plan("3단계부터 5단계까지 알려줘", turn_id=1, language="ko")
        self.assertEqual(plan.action, CuratedProtocolAction.STEP_RANGE)
        self.assertFalse(plan.state_changed)
        lines = [line for line in plan.display_text.splitlines() if line.startswith("•")]
        self.assertEqual(lines, [
            f"• {n}단계: {n}. Label sample tube {n} with the marker."
            for n in (3, 4, 5)
        ])
        for n in (3, 4, 5):
            self.assertIn(f"Label sample tube {n} with the marker.", plan.speech_text)


@unittest.skipUnless(
    SOURCE_PDF.is_file(),
    f"requires the externally licensed Candidate A source PDF at {SOURCE_PDF}",
)
class PausedReplyTests(unittest.TestCase):
    """Decisions 5 and 6 in the session."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = load_curated_protocol_fixture(FIXTURE, PROVENANCE, SOURCE_PDF)

    def _started(self) -> CuratedProtocolSession:
        session = CuratedProtocolSession(self.fixture)
        session.activate_configured()
        _turn(session, "프로토콜 시작해줘", 1)
        return session

    def test_the_pause_reply_names_resume_and_a_running_timer_only(self):
        session = self._started()
        paused = _turn(session, "정지", 2)
        self.assertEqual(paused.action, CuratedProtocolAction.PAUSE)
        self.assertEqual(paused.speech_text, PAUSED_REPLY)
        self.assertNotIn(TIMER_RUNS_ON, paused.speech_text)

        session = self._started()
        session.current_index = _step_index(self.fixture, "3")
        started, _duration, _message = session.start_timer()
        self.assertTrue(started)
        paused = _turn(session, "정지", 2)
        self.assertEqual(paused.speech_text, f"{PAUSED_REPLY} {TIMER_RUNS_ON}")

    def test_only_the_first_word_in_each_pause_is_spoken(self):
        session = self._started()
        _turn(session, "정지", 2)
        first = _turn(session, "완료했어", 3)
        self.assertEqual(first.action, CuratedProtocolAction.PAUSE)
        self.assertFalse(first.state_changed)
        self.assertEqual(first.speech_policy, "speak")
        self.assertEqual(first.speech_text, PAUSED_SPOKEN)
        self.assertEqual(first.display_text, PAUSED_NOTICE)
        second = _turn(session, "AMBIC가 뭐야?", 4)
        self.assertEqual(second.speech_policy, "silent")
        self.assertEqual(second.speech_text, "")
        self.assertEqual(second.display_text, PAUSED_NOTICE)
        self.assertEqual(session.current_index, 0)
        _turn(session, "재개해줘", 5)
        _turn(session, "정지", 6)
        again = _turn(session, "완료했어", 7)
        self.assertEqual(again.speech_policy, "speak")
        self.assertEqual(again.speech_text, PAUSED_SPOKEN)

    def test_the_spoken_mark_rolls_back_with_the_turn(self):
        session = self._started()
        _turn(session, "정지", 2)
        checkpoint = session._checkpoint()
        _turn(session, "완료했어", 3)
        session._restore(checkpoint)
        self.assertEqual(_turn(session, "완료했어", 3).speech_policy, "speak")
        checkpoint = session._checkpoint()
        _turn(session, "재개해줘", 4)
        session._restore(checkpoint)
        self.assertEqual(_turn(session, "완료했어", 4).speech_policy, "silent")


@unittest.skipUnless(
    SOURCE_PDF.is_file(),
    f"requires the externally licensed Candidate A source PDF at {SOURCE_PDF}",
)
class PausedVoiceTests(VoiceSessionHarness, unittest.TestCase):
    """Decisions 5 and 6 over the WebSocket, workspace and report on."""

    def _report_events(self, listener) -> int:
        report_id = listener.experiment_report_id
        if report_id is None:
            return 0
        return len(ExperimentReportStore(self.report_db).get_report(report_id)["events"])

    def _spoken(self, socket, turn_id: int) -> bool:
        done = socket.for_turn(turn_id, "turn.done")
        self.assertEqual(len(done), 1)
        self.assertEqual(done[0]["result_kind"], "pause")
        return done[0]["segment_count"] > 0

    def test_the_first_word_is_spoken_then_silence_until_the_next_pause(self):
        seen = {}

        async def scenario(socket, listener, say):
            await say(1, "프로토콜 시작해줘")
            await say(2, "정지")
            seen["paused"] = (self._snapshot(listener), self._report_events(listener))
            await say(3, "완료했어")
            await say(4, "다음")
            seen["held"] = (self._snapshot(listener), self._report_events(listener))
            await say(5, "재개해줘")
            await say(6, "정지")
            await say(7, "완료했어")
            await say(8, "다음")

        socket, _ = self._session(scenario)
        self.assertEqual(self._errors(socket), [])
        self.assertEqual(socket.reply(2), PAUSED_REPLY)
        self.assertTrue(self._spoken(socket, 3))
        self.assertEqual(socket.for_turn(3, "reply.delta")[-1]["speech_text"], PAUSED_SPOKEN)
        self.assertEqual(socket.reply(3), PAUSED_NOTICE)
        self.assertFalse(self._spoken(socket, 4))
        self.assertEqual(socket.for_turn(4, "reply.delta")[-1]["speech_text"], "")
        self.assertEqual(socket.reply(4), PAUSED_NOTICE)
        (paused, paused_events), (held, held_events) = seen["paused"], seen["held"]
        self.assertEqual(held["step_index"], 0)
        self.assertEqual(held["pause_state"], "paused")
        self.assertEqual(held["durable_status"], "paused")
        self.assertEqual(held["durable"]["completed_steps"], [])
        self.assertEqual(held["durable"]["version"], paused["durable"]["version"])
        self.assertEqual(len(held["durable"]["events"]), len(paused["durable"]["events"]))
        self.assertEqual(held_events, paused_events)
        # A new pause counts afresh.
        self.assertTrue(self._spoken(socket, 7))
        self.assertFalse(self._spoken(socket, 8))

    def test_a_refused_resume_keeps_the_word_already_spoken(self):
        real_transition = server_module._transition_workspace_experiment

        def refuse_resume(session, *, action, event_key, reason=None):
            if action == "resume":
                raise WorkspaceConflictError("synthetic refusal")
            return real_transition(
                session, action=action, event_key=event_key, reason=reason
            )

        async def scenario(socket, listener, say):
            await say(1, "프로토콜 시작해줘")
            await say(2, "정지")
            await say(3, "완료했어")
            await say(4, "재개해줘")
            await say(5, "완료했어")

        socket, _ = self._session(
            scenario,
            patch(
                "voiney_lab.server._transition_workspace_experiment",
                side_effect=refuse_resume,
            ),
        )
        self.assertTrue(self._spoken(socket, 3))
        self.assertIn("실험 세션을 저장하지 못해", socket.reply(4))
        self.assertFalse(self._spoken(socket, 5))


if __name__ == "__main__":
    unittest.main()
