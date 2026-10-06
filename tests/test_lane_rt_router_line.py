"""Lane RT: the router line -- history, the two tools, rule conflicts, spills, safety.

Decisions of the people running the pilot (2026-10-06), after the 10/2 advice
that the front rules' commands and state changes must reach the LLM's history
or the model misjudges the current step:

* decision 2 -- the turns the front rules and the server handled (the words,
  the reply that went out, what changed: step, pause, timer, record, an open
  question) are in the router's history in the order they happened, bounded
  to the last turns; the server's snapshot stays the state;
* decision 3 -- the router's state changes are one tool, ``change_state``
  (action), and its records one, ``record_log`` (type); the server checks the
  action and type against one allow-list, wherever a proposal comes from, and
  rules on it as before; a read-only question is answered with no state tool;

Every model here is a fake (tests/router_fakes.py); nothing is live.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import patch

from tests.protocol_vocabulary_support import miniprep_fixture
from tests.router_fakes import FakeRouterClient, answer_call_reply, tool_reply
from tests.test_voice_pause_resume_persistence import SOURCE_PDF, VoiceSessionHarness
from voiney_lab.brain import ConversationHistory
from voiney_lab.curated_protocol import CuratedProtocolAction, CuratedProtocolSession
from voiney_lab.llm_router import (
    CHANGE_STATE_RULES,
    CHANGE_STATE_TOOL,
    RECORD_LOG_RULES,
    RECORD_LOG_TOOL,
    ROUTER_CALL_TOOLS,
    LlmRouterSettings,
    ProposalBasis,
    RouterHistoryTurn,
    RouterTurnFacts,
    ToolProposal,
    history_before,
    history_turn,
    parse_tool_call,
    route_turn_with_llm_router,
    screen_history_turn,
    validate_tool_proposals,
)
from voiney_lab.runtime_routing import route_curated_runtime_turn

ON = LlmRouterSettings(enabled=True, model="fake-router-model", timeout_seconds=1.0)


def _miniprep(step_index: int | None = 3) -> CuratedProtocolSession:
    session = CuratedProtocolSession(miniprep_fixture())
    session.activate_configured()
    if step_index is not None:
        session.plan("시작", turn_id=1, language="ko", configuration_id=1, generation=1)
        session.current_index = step_index
    return session


def _routed(session, said, client, *, turn_id, history=()):
    async def rules():
        return route_curated_runtime_turn(
            session, said, turn_id=turn_id, language="ko",
            configuration_id=1, generation=1,
        )

    return asyncio.run(route_turn_with_llm_router(
        session, said, turn_id=turn_id, language="ko", settings=ON,
        client_factory=lambda: client, rule_route=rules, history=history,
        configuration_id=1, generation=1,
    ))


def _recent_turns(request: dict) -> list[dict]:
    """The RECENT TURNS block of one router request, as data."""

    for message in request["messages"]:
        if message["content"].startswith("RECENT TURNS"):
            return json.loads(message["content"].split("\n", 1)[1])
    return []


class _FakeAsyncOpenAI:
    """Stands in for AsyncOpenAI in server.py; hands out one scripted client."""

    client: FakeRouterClient | None = None

    def __new__(cls, *args, **kwargs):
        return cls.client


@unittest.skipUnless(
    SOURCE_PDF.is_file(),
    f"requires the externally licensed Candidate A source PDF at {SOURCE_PDF}",
)
class ServerHistoryTests(VoiceSessionHarness, unittest.TestCase):
    """Decision 2 over the WebSocket: what the router's history holds after
    turns the front rules, the emergency gate and the screen handled."""

    def _run(self, scenario_steps, script=()):
        self.environment["VOINEY_LAB_LLM_ROUTER_ENABLED"] = "true"
        self.environment["XAI_API_KEY"] = "test-only-not-a-key"
        self.environment["VOINEY_LAB_TRANSLATION_PROVIDER"] = "anthropic"
        self.environment["ANTHROPIC_API_KEY"] = ""
        client = FakeRouterClient(*script)
        _FakeAsyncOpenAI.client = client
        seen: dict = {}

        async def scenario(socket, listener, say):
            await scenario_steps(socket, listener, say, seen)
            seen["history"] = listener.history.router_history()

        socket, _ = self._session(
            scenario, patch("voiney_lab.server.AsyncOpenAI", _FakeAsyncOpenAI),
        )
        return socket, seen, client

    def test_a_front_rule_step_change_keeps_the_reply_that_went_out(self) -> None:
        async def steps(socket, listener, say, seen):
            await say(1, "프로토콜 시작해줘")
            await say(2, "1단계 완료했어")

        socket, seen, _ = self._run(steps)
        last = seen["history"][-1]
        self.assertEqual(last["handled_by"], "front:targeted_completion")
        self.assertEqual(last["server"]["result"], "executed")
        self.assertEqual(last["server"]["state_after"]["step"], "2")
        spoken = socket.for_turn(2, "reply.delta")[-1]["speech_text"]
        self.assertEqual(last["assistant"], " ".join(spoken.split())[:200])

    def test_the_emergency_gate_turn_is_kept_without_its_reply(self) -> None:
        async def steps(socket, listener, say, seen):
            await say(1, "프로토콜 시작해줘")
            await say(2, "불이 났어")

        _, seen, _ = self._run(steps)
        self.assertEqual(
            [(item["handled_by"], item["user"]) for item in seen["history"]],
            [("front:start_command", "프로토콜 시작해줘"), ("front:emergency", "불이 났어")],
        )
        self.assertIsNone(seen["history"][-1]["assistant"])
        self.assertEqual(seen["history"][-1]["server"]["state_after"]["step"], "1")

    def test_a_pause_pressed_on_the_screen_is_kept_in_order(self) -> None:
        async def steps(socket, listener, say, seen):
            await say(1, "프로토콜 시작해줘")
            socket.control({"type": "workflow.pause"})
            await socket.wait_for(lambda item: (
                item["type"] == "protocol.fixture.state" and item.get("action") == "pause"
            ))
            socket.control({"type": "workflow.resume"})
            await socket.wait_for(lambda item: (
                item["type"] == "protocol.fixture.state" and item.get("action") == "resume"
            ))
            await say(2, "버퍼 1은 뭐야?")

        _, seen, client = self._run(
            steps, script=(answer_call_reply("PDF에서 확인할 수 없어요.", source_kind="none"),),
        )
        self.assertEqual(
            [item["handled_by"] if "handled_by" in item else "screen:" + item["control"]
             for item in seen["history"]],
            ["front:start_command", "screen:pause", "screen:resume", "llm"],
        )
        pause = seen["history"][1]
        self.assertEqual(pause["source"], "screen")
        self.assertEqual(pause["server"]["result"], "executed")
        self.assertEqual(pause["server"]["state_after"]["status"], "paused")
        # The model saw both presses, before the turn it answered.
        shown = _recent_turns(client.requests[0])
        self.assertEqual(
            [item.get("control") for item in shown], [None, "pause", "resume"],
        )

    def test_a_record_says_what_was_stored(self) -> None:
        async def steps(socket, listener, say, seen):
            await say(1, "프로토콜 시작해줘")
            await say(2, "관찰 기록해줘 젤이 투명해")

        _, seen, _ = self._run(steps, script=(tool_reply((
            "record_log",
            {"type": "observation", "value": "젤이 투명해", "evidence": "관찰 기록해줘 젤이 투명해"},
        )),))
        last = seen["history"][-1]
        self.assertEqual(last["server"]["result"], "recorded")
        self.assertEqual(
            last["server"]["recorded"], {"type": "observation", "words": "젤이 투명해"},
        )

    def test_a_started_timer_is_in_the_state_after(self) -> None:
        async def steps(socket, listener, say, seen):
            await say(1, "프로토콜 시작해줘")
            await say(2, "1단계 완료했어")
            await say(3, "2단계 완료했어")
            await say(4, "타이머 시작해줘")

        _, seen, _ = self._run(steps, script=(tool_reply((
            "change_state", {"action": "start_timer", "evidence": "타이머 시작해줘"},
        )),))
        last = seen["history"][-1]
        self.assertEqual(last["server"]["state_after"]["step"], "3")
        self.assertEqual(last["server"]["state_after"]["timer"], "running")

    def test_a_question_the_server_left_open_is_named(self) -> None:
        async def steps(socket, listener, say, seen):
            await say(1, "프로토콜 시작해줘")
            await say(2, "실험 종료")

        _, seen, _ = self._run(steps)
        last = seen["history"][-1]
        self.assertEqual(last["handled_by"], "front:end_command")
        self.assertEqual(last["server"]["result"], "stop_prompt_opened")
        self.assertEqual(last["server"]["question_open"], "stop")


class HistoryBundleTests(unittest.TestCase):
    """Decision 2 on the bundles themselves (no PDF needed)."""

    def test_a_screen_control_is_a_bundle_of_its_own(self) -> None:
        session = _miniprep()
        before = history_before(session)
        session.pause_workflow()
        bundle = screen_history_turn(session, before, control="pause", changed=True)
        self.assertEqual(bundle.prompt_payload(), {
            "at_step": "4", "status": "active", "source": "screen", "control": "pause",
            "server": {"result": "executed", "state_after": {"step": "4", "status": "paused"}},
        })
        with self.assertRaises(ValueError):
            screen_history_turn(session, before, control="next", changed=True)

    def test_what_was_stored_is_kept_short_and_typed(self) -> None:
        bundle = RouterHistoryTurn(
            at_step="4", status="active", user="메모해줘 " + "가" * 200,
            handled_by="llm+tool", proposal_tool="record_log", proposal_kind="observation",
            server_result="recorded", state_after=("4", "active"),
            recorded=("observation", "가" * 200),
        )
        words = bundle.prompt_payload()["server"]["recorded"]["words"]
        self.assertEqual(len(words), 120)
        self.assertTrue(words.endswith("…"))
        with self.assertRaises(ValueError):
            RouterHistoryTurn(
                at_step="4", status="active", user="x", handled_by="llm",
                server_result="recorded", recorded=("note", "x"),
            )

    def test_a_front_turn_bundle_says_the_state_and_the_open_question(self) -> None:
        session = _miniprep()
        before = history_before(session)
        outcome = _routed(session, "실험 종료", FakeRouterClient(), turn_id=2)
        bundle = history_turn(
            session, outcome, before, user="실험 종료", plan=outcome.plan,
            said=outcome.plan.speech_text, next_turn_id=3,
            configuration_id=1, generation=1,
        )
        payload = bundle.prompt_payload()
        self.assertEqual(payload["handled_by"], "front:end_command")
        self.assertEqual(payload["server"]["question_open"], "stop")
        self.assertEqual(payload["assistant"], "실험을 종료할까요?")

    def test_the_model_reads_the_front_and_screen_turns_in_order(self) -> None:
        session = _miniprep()
        history = ConversationHistory()
        before = history_before(session)
        outcome = _routed(session, "4단계 완료했어", FakeRouterClient(), turn_id=2)
        history.record_router_turn(history_turn(
            session, outcome, before, user="4단계 완료했어", plan=outcome.plan,
            said=outcome.plan.speech_text, next_turn_id=3, configuration_id=1, generation=1,
        ))
        before = history_before(session)
        session.pause_workflow()
        history.record_router_turn(
            screen_history_turn(session, before, control="pause", changed=True)
        )
        client = FakeRouterClient(answer_call_reply("지금은 일시정지 중이에요.", source_kind="server_state"))
        _routed(session, "지금 어디까지 했지?", client, turn_id=3, history=history.router_history())
        shown = _recent_turns(client.requests[0])
        self.assertEqual(shown[0]["handled_by"], "front:targeted_completion")
        self.assertEqual(shown[0]["server"]["state_after"]["step"], "5")
        self.assertEqual(shown[1]["control"], "pause")
        self.assertEqual(shown[1]["server"]["state_after"]["status"], "paused")
        snapshot = next(
            message["content"] for message in client.requests[0]["messages"]
            if message["content"].startswith("SERVER SNAPSHOT")
        )
        self.assertIn('"phase":"paused"', snapshot)


def _facts(utterance: str, **changes: object) -> RouterTurnFacts:
    values: dict[str, object] = dict(
        utterance=utterance, language="ko", turn_id=5, generation=1,
        workflow_revision=3, step_id="step-4", current_step_label="4",
        workflow_active=True, workflow_status="active", paused=False,
        experiment_started=True, experiment_running=True, open_question=None,
        observation_step=False, step_timer_seconds=900, timer_running=False,
        control_question=False, transcript_unreliable=False,
    )
    values.update(changes)
    return RouterTurnFacts(**values)  # type: ignore[arg-type]


_BASIS = ProposalBasis(turn_id=5, generation=1, workflow_revision=3, step_id="step-4")


class AllowListTests(unittest.TestCase):
    """Decision 3: one allow-list for each tool's values, read everywhere."""

    def test_the_offered_values_are_the_allow_list(self) -> None:
        self.assertEqual(
            [tool["function"]["name"] for tool in ROUTER_CALL_TOOLS],
            ["answer", "change_state", "record_log"],
        )
        self.assertEqual(
            CHANGE_STATE_TOOL["function"]["parameters"]["properties"]["action"]["enum"],
            list(CHANGE_STATE_RULES),
        )
        self.assertEqual(
            RECORD_LOG_TOOL["function"]["parameters"]["properties"]["type"]["enum"],
            list(RECORD_LOG_RULES),
        )
        self.assertEqual(
            parse_tool_call("change_state", {"action": "skip", "evidence": "x"}),
            "arguments_invalid",
        )

    def test_a_value_off_the_list_is_refused_however_it_arrives(self) -> None:
        # Given straight to the validation, these used to be ruled on as a
        # timer start (any other action or tool) or an anomaly (any other type).
        cases = (
            (ToolProposal(tool="change_state", action="skip", evidence="타이머 시작해줘"),
             "arguments_invalid"),
            (ToolProposal(tool="advance_step", action="next", evidence="타이머 시작해줘"),
             "tool_unknown"),
            (ToolProposal(tool="record_log", log_type="note", value="시료를 흘렸어",
                          evidence="시료를 흘렸어"), "arguments_invalid"),
        )
        for proposal, code in cases:
            with self.subTest(proposal=proposal):
                verdict = validate_tool_proposals(
                    [proposal], _facts(proposal.evidence), _BASIS,
                )
                self.assertEqual((verdict.effect, verdict.reason_code), ("refuse", code))

    def test_every_listed_value_runs_as_the_rules_own_action(self) -> None:
        cases = (
            ("change_state", "next", "이제 다음 거 하자", 3, CuratedProtocolAction.CLARIFY_COMPLETION),
            ("change_state", "stop", "오늘은 이쯤에서 종료하는 게 좋겠어", 3, CuratedProtocolAction.STOP),
            ("change_state", "pause", "잠깐 쉬었다 할게", 3, CuratedProtocolAction.PAUSE),
            ("change_state", "start", "자 이제 실험 시작해 볼까", None, CuratedProtocolAction.START),
            ("record_log", "observation", "관찰 기록해줘 침전이 생겼어", 3, CuratedProtocolAction.RECORD_OBSERVATION),
            ("record_log", "anomaly", "튜브가 터졌어", 3, CuratedProtocolAction.REPORT_ANOMALY),
        )
        for tool, value, said, step, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(
                    (CHANGE_STATE_RULES if tool == "change_state" else RECORD_LOG_RULES)[value].runs_as,
                    expected.value,
                )
                session = _miniprep(step)
                arguments = (
                    {"type": value, "value": said, "evidence": said}
                    if tool == "record_log" else {"action": value, "evidence": said}
                )
                outcome = _routed(
                    session, said, FakeRouterClient(tool_reply((tool, arguments))), turn_id=2,
                )
                self.assertEqual(outcome.handled_by, "llm+tool")
                self.assertIs(outcome.plan.action, expected)

    def test_resume_and_the_timer_run_as_the_rules_own_action(self) -> None:
        self.assertEqual(CHANGE_STATE_RULES["resume"].runs_as, "resume")
        self.assertEqual(CHANGE_STATE_RULES["start_timer"].runs_as, "start_timer")
        session = _miniprep(3)
        session.pause_workflow()
        outcome = _routed(session, "이어서 하자", FakeRouterClient(tool_reply((
            "change_state", {"action": "resume", "evidence": "이어서 하자"},
        ))), turn_id=2)
        self.assertEqual(outcome.handled_by, "llm+tool")
        self.assertIs(outcome.plan.action, CuratedProtocolAction.RESUME)
        session = _miniprep(3)
        with patch.object(session, "timer_seconds_for_step", return_value=600):
            outcome = _routed(session, "이 단계 시간 재 줘", FakeRouterClient(tool_reply((
                "change_state", {"action": "start_timer", "evidence": "시간 재 줘"},
            ))), turn_id=2)
        self.assertEqual(outcome.handled_by, "llm+tool")
        self.assertIs(outcome.plan.action, CuratedProtocolAction.START_TIMER)


if __name__ == "__main__":
    unittest.main()
