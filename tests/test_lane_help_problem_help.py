"""Lane HELP (human decision 2026-10-24): help with a problem during a run.

A researcher says a problem at the bench -- "젤이 아직 파래", "침전이 생겼어",
"왜 안 되지?" -- and is told, with nothing changed, what the sources say about
it, each with where it comes from: ① the protocol's own lines at this step,
the steps either side and the section (page and the source's words), ② the
same account's earlier runs of the protocol at the same step and what followed,
③ a public source when one is connected, or -- only with the supplemental role
on -- possible causes as "AI 일반 지식", and ④ the closing sentence. A problem
said as having happened is recorded as an anomaly first, read back as before.

Decision 1 (one routing line, a read-only request kind): the reader is
``intent_arbitration.problem_reading``, the front rule "problem_help", the
action PROBLEM_HELP; nothing moves. Decision 2: the help's order, sources and
the closing; the checks (``answer_checks.problem_help_violations``,
``possible_cause_violations``). Decision 3: ``knowledge`` -- the contract and
the in-process FTS5 search. Everything here runs on a fictional protocol
(``tests/lane_help_support.py``), no licensed PDF and no provider.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.lane_help_support import STEP_3, gel_wash_fixture, session_at
from tests.test_voice_pause_resume_persistence import PROTOCOL_ID, VoiceSessionHarness
from voiney_lab.answer_checks import possible_cause_violations, problem_help_violations
from voiney_lab.curated_protocol import (
    FRONT_RULES,
    ROUTER_FIRST_HANDED_ON,
    CuratedProtocolAction,
    CuratedProtocolSession,
)
from voiney_lab.experiment_reports import (
    EVENT_NAMES_KO,
    ExperimentReportStore,
    problem_help_line,
)
from voiney_lab.intent_arbitration import RequestIntent, arbitrate_request, problem_reading
from voiney_lab.knowledge import CLOSING, NOTHING_FOUND, KnowledgeHit, compose_problem_help
from voiney_lab.language import Transcription
from voiney_lab.runtime_routing import route_curated_runtime_turn
from voiney_lab.server import ListenerSession, run_turn
from voiney_lab.external_references import SupplementalKnowledgeSettings
from voiney_lab.tools import ToolContext
from voiney_lab.vad import TurnState

CLOSING_KO = CLOSING["ko"]


def _route(session: CuratedProtocolSession, said: str, turn_id: int = 2):
    return route_curated_runtime_turn(
        session, said, turn_id=turn_id, language="ko", configuration_id=1, generation=1,
    ).plan


class RouterLineTests(unittest.TestCase):
    """Decision 1 with the router on: the front rule keeps a problem; an anomaly
    the router's record_log proposal records carries the same marker."""

    def test_a_problem_never_reaches_the_model(self) -> None:
        from tests.lane_ra_support import FIRST, route
        from tests.router_fakes import FakeRouterClient

        session = session_at("3")
        session.router_first = True
        client = FakeRouterClient()
        outcome = route(session, "왜 안 되지?", client, settings=FIRST)
        self.assertEqual(outcome.handled_by, "front:problem_help")
        self.assertEqual(client.requests, [])
        self.assertIs(outcome.plan.action, CuratedProtocolAction.PROBLEM_HELP)

    def test_an_anomaly_the_router_records_is_marked_for_help(self) -> None:
        from tests.lane_ra_support import FIRST, route
        from tests.router_fakes import FakeRouterClient, tool_reply

        session = session_at("2")
        session.router_first = True
        said = "용액 색이 이상해"
        outcome = route(session, said, FakeRouterClient(tool_reply(("record_log", {
            "type": "anomaly", "value": said, "evidence": said,
        }))), settings=FIRST)
        self.assertEqual(outcome.handled_by, "llm+tool")
        self.assertIs(outcome.plan.action, CuratedProtocolAction.REPORT_ANOMALY)
        self.assertEqual(outcome.plan.problem_help["kind"], "statement")
        self.assertEqual(outcome.plan.problem_help["words"], said)


class ProblemReaderTests(unittest.TestCase):
    """Decision 1: which words are a problem, said or asked; nothing else is."""

    def test_problems_said_as_having_happened(self) -> None:
        for said in (
            "젤이 아직 파래", "젤이 아직도 파래요", "침전이 생겼어", "거품이 많이 생겼어",
            "색이 안 빠져", "세포가 안 떨어져", "펠릿이 안 보여", "용액이 갑자기 탁해졌어",
            "실패한 것 같아", "결과가 안 좋아", "먼지가 들어갔어", "the gel is still blue",
            "a precipitate formed", "it didn't work",
        ):
            with self.subTest(said=said):
                reading = problem_reading(said)
                self.assertIsNotNone(reading)
                self.assertEqual(reading.kind, "statement")

    def test_problems_asked_about(self) -> None:
        for said in (
            "왜 안 되지?", "왜 안 되지", "젤이 왜 아직 파래?",
            "색이 안 빠지는데 어떻게 해야 돼?", "뭐가 문제야", "why is it not working",
            "what went wrong",
        ):
            with self.subTest(said=said):
                reading = problem_reading(said)
                self.assertIsNotNone(reading)
                self.assertEqual(reading.kind, "question")

    def test_problems_said_as_having_happened_then_asked_about(self) -> None:
        # Lane SAFE2 (2026-10-31): the thing happened, then "what now" -- recorded
        # as the statement is, and answered. It used to be a question alone.
        for said in ("침전이 생겼는데 어떡해", "거품이 났어 어떻게 해?", "침전이 생겼는데 이러면 어떡해?"):
            with self.subTest(said=said):
                reading = problem_reading(said)
                self.assertIsNotNone(reading)
                self.assertEqual(reading.kind, "statement_question")

    def test_words_that_are_no_problem(self) -> None:
        for said in (
            "아직 안 끝났어", "아니 아직 안 끝났어", "소리가 안 나", "안 들려", "화면이 안 보여",
            "그거 만지면 안 돼", "대신 써도 돼?", "침전이 생기면 어떡해?", "타이머 아직 남았어?",
            "다음 단계", "침전 사진 보여 줘", "젤 밴드가 뭐야?", "왜 해야 돼?", "오늘 점심 뭐 먹지",
            "if it stays blue what then",
        ):
            with self.subTest(said=said):
                self.assertIsNone(problem_reading(said))

    def test_arbitration_labels_it_and_authorizes_nothing(self) -> None:
        decision = arbitrate_request("침전이 생겼어")
        self.assertIs(decision.intent, RequestIntent.TROUBLESHOOTING)
        self.assertFalse(decision.state_mutation)
        self.assertIs(arbitrate_request("왜 해야 돼?").intent, RequestIntent.LEARNING)


class StateMachineTests(unittest.TestCase):
    """Decision 1: the front rule, the record, and nothing moved."""

    def setUp(self) -> None:
        self.fixture = gel_wash_fixture()

    def test_a_problem_said_is_recorded_and_marked_for_help(self) -> None:
        session = session_at("2", self.fixture)
        before = session._checkpoint()
        plan = _route(session, "침전이 생겼어")
        self.assertEqual(session.last_front_rule, "problem_help")
        self.assertIs(plan.action, CuratedProtocolAction.REPORT_ANOMALY)
        self.assertTrue(plan.reported_anomaly)
        self.assertEqual(plan.anomaly_text, "침전이 생겼어")
        self.assertFalse(plan.state_changed)
        self.assertEqual(plan.problem_help["kind"], "statement")
        self.assertEqual(plan.problem_help["step_label"], "2")
        self.assertEqual(session._checkpoint()[:4], before[:4])

    def test_a_problem_said_then_asked_is_recorded_and_marked_for_the_grounded_answer(self) -> None:
        # Lane SAFE2 (2026-10-31): "침전이 생겼는데 어떡해" was helped only; now the
        # event is recorded as "침전이 생겼어" is, and the server appends lane
        # QA1's grounded answer (marker kind "grounded_question").
        session = session_at("2", self.fixture)
        before = session._checkpoint()
        plan = _route(session, "침전이 생겼는데 어떡해")
        self.assertEqual(session.last_front_rule, "problem_help")
        self.assertIs(plan.action, CuratedProtocolAction.REPORT_ANOMALY)
        self.assertTrue(plan.reported_anomaly)
        self.assertEqual(plan.intent_kind, "problem_report_question")
        self.assertFalse(plan.state_changed)
        self.assertEqual(plan.problem_help["kind"], "grounded_question")
        self.assertEqual(session._checkpoint()[:4], before[:4])

    def test_a_problem_asked_records_nothing_and_changes_no_checkpoint(self) -> None:
        for said in ("왜 안 되지?", "젤이 왜 아직 파래?"):
            with self.subTest(said=said):
                session = session_at("3", self.fixture)
                before = session._checkpoint()
                plan = _route(session, said)
                self.assertIs(plan.action, CuratedProtocolAction.PROBLEM_HELP)
                self.assertEqual(session.last_front_rule, "problem_help")
                self.assertFalse(plan.reported_anomaly or plan.reported_observation or plan.state_changed)
                self.assertEqual(plan.problem_help["kind"], "question")
                after = session._checkpoint()
                for index in (0, 1, 2, 3, 9, 10):
                    self.assertEqual(after[index], before[index])
                self.assertTrue(plan.display_text.endswith(CLOSING_KO))

    def test_not_yet_at_its_repeat_step_is_helped_not_recorded(self) -> None:
        session = session_at("3", self.fixture)
        plan = _route(session, "젤이 아직 파래")
        self.assertIs(plan.action, CuratedProtocolAction.PROBLEM_HELP)
        self.assertEqual(plan.intent_kind, "problem_not_yet")
        self.assertFalse(plan.reported_anomaly)
        self.assertIn(STEP_3, plan.display_text)
        # Away from the repeat step the same words are a problem, recorded.
        elsewhere = session_at("4", self.fixture)
        self.assertTrue(_route(elsewhere, "젤이 아직 파래").reported_anomaly)

    def test_an_anomaly_the_existing_reader_records_is_marked_too(self) -> None:
        session = session_at("2", self.fixture)
        plan = _route(session, "시료를 흘렸어")
        self.assertEqual(session.last_front_rule, "anomaly_report")
        self.assertTrue(plan.reported_anomaly)
        self.assertEqual(plan.problem_help["kind"], "statement")
        # A spill is a safety event: no possible causes from a model.
        self.assertTrue(plan.problem_help["safety"])

    def test_the_rule_is_in_the_table_and_kept_with_the_router_first(self) -> None:
        self.assertIn("problem_help", FRONT_RULES)
        self.assertNotIn("problem_help", ROUTER_FIRST_HANDED_ON)
        session = session_at("3", self.fixture)
        session.router_first = True
        planned = session.front_plan(
            "왜 안 되지?", turn_id=2, language="ko", configuration_id=1, generation=1,
            router_first=True,
        )
        self.assertIsNotNone(planned)
        self.assertIs(planned.action, CuratedProtocolAction.PROBLEM_HELP)

    def test_a_completion_decline_and_a_move_keep_their_rules(self) -> None:
        session = session_at("3", self.fixture)
        self.assertIsNot(_route(session, "아직 안 끝났어").action, CuratedProtocolAction.PROBLEM_HELP)
        moved = session_at("2", self.fixture)
        plan = _route(moved, "다음")
        self.assertIs(plan.action, CuratedProtocolAction.NEXT)
        self.assertIsNone(plan.problem_help)


class ComposeAndCheckTests(unittest.TestCase):
    """Decision 2: the order, the closing, and what is never said."""

    def test_order_sources_and_closing(self) -> None:
        protocol = KnowledgeHit("protocol", "3단계 단계", STEP_3, "p.1 · 3단계", details=(
            ("kind", "step"), ("page", "1"), ("step_label", "3"), ("reading", "3단계: 반복합니다."),
            ("reading_source", "reviewed")))
        history = KnowledgeHit("history", "지난 실험", "탈색이 덜 됐어", "10월 20일 · 3단계", details=(
            ("kind", "anomaly"), ("day", "10월 20일"), ("step_label", "3"), ("after", "관찰: 한 번 더 씻음")))
        help_ = compose_problem_help((history, protocol), language="ko")
        said = help_.spoken
        self.assertLess(said.index("원문 1쪽"), said.index("지난 10월 20일"))
        self.assertTrue(said.endswith(CLOSING_KO))
        shown = help_.shown
        self.assertIn(f"“{STEP_3}”", shown)
        self.assertIn("원문 · p.1 · 3단계", shown)
        self.assertIn("지난 기록 · 10월 20일 · 3단계", shown)
        self.assertTrue(shown.endswith(CLOSING_KO))
        self.assertEqual(problem_help_violations(f"{shown}\n{said}", quoted=help_.quoted_text.splitlines()), ())

    def test_a_long_reading_said_in_part_keeps_its_numbers_as_quoted(self) -> None:
        # A reading too long to say whole is said by its first sentence; the
        # source's number in that sentence is the source's, not the frame's.
        reading = ("젤 조각의 염색이 진하면 세척 버퍼 부피를 1000 µL로 올릴 수 있습니다. "
                   "37C 배양에는 Eppendorf Thermomixer를 사용할 수 있고, 대안으로 실온에서 흔들 수 있습니다.")
        note = KnowledgeHit("protocol", "2단계 노트", "Note If strongly stained ... 1000 µL.", "p.1 · 2단계", details=(
            ("kind", "note"), ("page", "1"), ("step_label", "2"), ("reading", reading),
            ("reading_source", "reviewed")))
        help_ = compose_problem_help((note,), language="ko")
        self.assertIn("1000 µL로 올릴 수 있습니다. 나머지는 화면에 있어요.", help_.spoken)
        self.assertEqual(problem_help_violations(
            f"{help_.shown}\n{help_.spoken}", quoted=help_.quoted_text.splitlines()), ())

    def test_closing_held_for_possible_causes(self) -> None:
        help_ = compose_problem_help((), language="ko", causes_follow=True)
        self.assertEqual(help_.spoken, NOTHING_FOUND["ko"])
        self.assertTrue(help_.shown.endswith(CLOSING_KO))

    def test_the_help_frame_may_state_no_number_of_its_own(self) -> None:
        self.assertIn("number_not_in_sources", problem_help_violations(
            "원문 1쪽: 반복합니다. 보통 3번이면 돼요.", quoted=["반복합니다."]))
        self.assertIn("permission", problem_help_violations("계속 진행해도 괜찮아요.", quoted=()))

    def test_possible_causes_are_said_only_as_possible(self) -> None:
        protocol = "Wash the gel piece with wash buffer until destained. Solution A"
        self.assertEqual(possible_cause_violations(
            "세척 횟수가 부족했을 수 있어요. 젤 조각이 컸을 수 있어요.",
            sources_text="", protocol_text=protocol), ())
        for bad, code in (
            ("세척 버퍼를 1000 µL로 늘리세요.", "condition_change"),
            ("버퍼가 3배 진했을 수 있어요.", "number_not_in_sources"),
            ("메탄올이 섞였을 수 있어요.", "term_not_in_protocol"),
            ("Tween contamination may cause it.", "term_not_in_protocol"),
            ("이대로 계속해도 돼요.", "permission"),
            ("이 정도면 안전해요.", "safety_judgment"),
            ("세척이 부족했어요.", "not_hedged"),
            ("다음 단계로 넘어갔어요.", "state_claim"),
        ):
            with self.subTest(bad=bad):
                self.assertIn(code, possible_cause_violations(
                    bad, sources_text="", protocol_text=protocol))

    def test_the_notebook_line(self) -> None:
        line = problem_help_line("3", {
            "found": {"protocol": 2, "history": 1},
            "sources": [{"tier": "protocol", "page": "1"}, {"tier": "protocol", "page": "2"},
                        {"tier": "history"}]}, "왜 안 되지?", "10:31")
        self.assertEqual(line, "도움 · 3단계 (10:31) — 물음 “왜 안 되지?”: 원문 p.1·p.2 · 지난 기록 1건")
        self.assertEqual(EVENT_NAMES_KO["problem_help"], "문제 도움")


class VoiceProblemHelpTests(VoiceSessionHarness, unittest.TestCase):
    """The production boundary: the /ws voice loop, the record and the notebook."""

    @classmethod
    def setUpClass(cls) -> None:
        # The harness starts the in-gel fixture's id; the fictional protocol runs under it.
        cls.fixture = gel_wash_fixture(PROTOCOL_ID)

    def _said(self, steps, spoken: list[str], at: list[str] | None = None):
        def synthesize(text, *args, **kwargs):
            spoken.append(text)
            return b"\0\0"

        async def scenario(socket, listener, say):
            for turn, words in enumerate(steps, 1):
                await say(turn, words)
                workflow = listener.curated_protocol_session
                if at is not None:
                    at.append(workflow.fixture.steps[workflow.current_index].source_label)

        return self._session(scenario, patch("voiney_lab.server.synthesize", side_effect=synthesize))

    def test_a_problem_is_read_back_then_helped_and_the_help_recorded(self) -> None:
        spoken: list[str] = []
        socket, listener = self._said(("시작", "넘어가", "다음", "다음", "젤이 아직 파래"), spoken)
        reply = socket.reply(5)
        self.assertNotIn("이상 사항", reply)  # not yet at its repeat step: helped, not recorded
        self.assertIn(f"“{STEP_3}”", reply)
        self.assertIn("원문 · p.2 · 문제 해결", reply)
        self.assertTrue(reply.endswith(CLOSING_KO))
        (operation,) = socket.for_turn(5, "server.operation")
        self.assertEqual(operation["operation"], "problem_help_read")
        store = ExperimentReportStore(self.report_db)
        (report,) = store.list_reports()
        events = store.get_report(report["report_id"])["events"]
        helped = [event for event in events if event["event_type"] == "problem_help"]
        self.assertEqual(len(helped), 1)
        self.assertEqual(helped[0]["step_label"], "3")
        self.assertEqual(helped[0]["payload"]["found"]["protocol"], 3)
        self.assertFalse(helped[0]["payload"]["state_mutation"])
        self.assertFalse(any(event["event_type"] == "anomaly" for event in events))

    def test_a_recorded_problem_is_read_back_first_and_the_next_run_hears_it(self) -> None:
        spoken: list[str] = []
        socket, _listener = self._said(("시작", "넘어가", "다음", "침전이 생겼어"), spoken)
        reply = socket.reply(4)
        self.assertTrue(reply.startswith("말씀하신 이상 사항을 현재 2단계 실험 기록에 남겼습니다."))
        self.assertIn(NOTHING_FOUND["ko"], reply)
        self.assertTrue(spoken[-1].endswith("말해 주세요.") or CLOSING_KO in " ".join(spoken))
        store = ExperimentReportStore(self.report_db)
        (report,) = store.list_reports()
        kinds = [event["event_type"] for event in store.get_report(report["report_id"])["events"]]
        self.assertLess(kinds.index("anomaly"), kinds.index("problem_help"))

        again: list[str] = []
        socket, _listener = self._said(("시작", "넘어가", "다음", "침전이 보여"), again)
        reply = socket.reply(4)
        self.assertIn("지난 기록 · ", reply)
        self.assertIn("“침전이 생겼어”", reply)
        self.assertIn("지난 ", " ".join(again))

    def test_nothing_moves_and_no_model_is_asked(self) -> None:
        spoken: list[str] = []
        at: list[str] = []
        socket, _listener = self._said(("시작", "넘어가", "다음", "왜 안 되지?", "뭐가 문제야"), spoken, at)
        self.assertEqual(at[2:], ["2", "2", "2"])
        for turn in (4, 5):
            (operation,) = socket.for_turn(turn, "server.operation")
            self.assertEqual(operation["operation"], "problem_help_read")
        self.assertEqual(socket.for_turn(4, "research.result"), [])


class PossibleCausesTests(unittest.TestCase):
    """Decision 2 ③: possible causes after the answer, then the closing; or the closing alone."""

    def _session(self, *, supplemental: bool) -> ListenerSession:
        context = ToolContext(Path("/unused/offline-catalog"), None, "ko", "test_only")
        workflow = session_at("2")
        session = ListenerSession(tool_context=context, curated_protocol_session=workflow)
        session.active = True
        # The workflow's turn 1 was its start (``session_at``).
        session.active_turn_id = 2
        session.next_turn_id = 3
        session.turn_generations[2] = session.generation
        session.accept_configuration(41, "cascade", "ko", workflow.fixture.protocol_id)
        session.detector.state = TurnState.PROCESSING
        session.supplemental_knowledge_settings = SupplementalKnowledgeSettings(
            supplemental, "offline-supplement", 2.0)
        return session

    def _turn(self, session, said, answer, *, delay: float = 0.0):
        sent: list[dict] = []
        spoken: list[str] = []

        class Socket:
            async def send_text(self, value):
                sent.append(json.loads(value))

            async def send_bytes(self, value):
                pass

        class Explainer:
            calls: list[str] = []

            def __init__(self, *args):
                pass

            async def explain_possible_causes(self, problem, *, step_text, source_lines=(),
                                              public_lines=(), language):
                Explainer.calls.append(problem)
                await asyncio.sleep(delay)
                return {"status": "success", "answer": answer, "backend": "supplemental_possible_causes"}

        async def immediate(function, *args, **kwargs):
            return function(*args, **kwargs)

        def synthesize(text, *args, **kwargs):
            spoken.append(text)
            return b"\0\0" * 400

        with patch("voiney_lab.server.transcribe", return_value=Transcription(said, "ko")), \
                patch("voiney_lab.server.synthesize", side_effect=synthesize), \
                patch("voiney_lab.server.XaiSupplementalKnowledge", Explainer), \
                patch("voiney_lab.server._role_client", return_value=SimpleNamespace()), \
                patch.dict("os.environ", {"VOINEY_LAB_SUPPLEMENTAL_PROVIDER": "xai",
                                          "VOINEY_LAB_SUPPLEMENTAL_MODEL": "offline-supplement"}), \
                patch("voiney_lab.server.asyncio.to_thread", side_effect=immediate):
            asyncio.run(run_turn(Socket(), session, b"\0\0", 2, 1))
        return sent, spoken, Explainer.calls

    def test_checked_causes_are_said_after_the_answer_then_the_closing(self) -> None:
        session = self._session(supplemental=True)
        sent, spoken, calls = self._turn(
            session, "왜 안 되지?", "세척 횟수가 부족했을 수 있어요.")
        self.assertEqual(calls, ["왜 안 되지?"])
        self.assertEqual(spoken[-1], f"AI 일반 지식으로는, 세척 횟수가 부족했을 수 있어요. {CLOSING_KO}")
        self.assertNotIn(CLOSING_KO, " ".join(spoken[:-1]))
        (result,) = [item for item in sent if item["type"] == "research.result"]
        self.assertEqual(result["source_label"], "AI 일반 지식")
        segments = [item["segment_index"] for item in sent if item["type"] == "audio.segment.start"]
        (complete,) = [item for item in sent if item["type"] == "audio.complete"]
        self.assertEqual(complete["segment_count"], len(segments))
        self.assertEqual(session.curated_protocol_session.current_index, 1)

    def test_a_cause_that_fails_a_check_leaves_the_closing_alone(self) -> None:
        session = self._session(supplemental=True)
        sent, spoken, _calls = self._turn(session, "왜 안 되지?", "세척 버퍼를 1000 µL로 늘리세요.")
        self.assertEqual(spoken[-1], CLOSING_KO)
        self.assertNotIn("1000 µL로 늘리세요", " ".join(spoken))
        self.assertEqual([item for item in sent if item["type"] == "research.result"], [])

    def test_with_the_role_off_the_closing_ends_the_answer(self) -> None:
        session = self._session(supplemental=False)
        sent, spoken, calls = self._turn(session, "왜 안 되지?", "unused")
        self.assertEqual(calls, [])
        self.assertTrue(spoken[-1].endswith("말해 주세요."))
        self.assertIn(CLOSING_KO, " ".join(spoken))

    def test_a_spill_gets_no_model_causes(self) -> None:
        session = self._session(supplemental=True)
        _sent, spoken, calls = self._turn(session, "시료를 흘렸어", "넘쳤을 수 있어요.")
        self.assertEqual(calls, [])
        self.assertIn(CLOSING_KO, " ".join(spoken))


if __name__ == "__main__":
    unittest.main()
