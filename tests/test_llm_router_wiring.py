"""The lane R router, wired behind VOINEY_LAB_LLM_ROUTER_ENABLED.

A turn the front rules hand on goes to one model call with the two tools.
The server rules on a proposal and carries it out through the session's own
branches; an answer is checked and used read-only. Whenever the model is
late, fails, says nothing usable, is refused, or answers with something a
check rejects, the turn takes the rules' path exactly as it would with the
router off. The setting is off by default, and off, nothing calls a model.

Every provider here is a fake (tests/router_fakes.py); nothing is live.
"""

from __future__ import annotations

import asyncio
import copy
import unittest
from unittest.mock import patch

from tests.protocol_vocabulary_support import SOURCE_PDF, miniprep_fixture
from tests.router_fakes import (
    FakeRouterClient,
    answer_call_reply,
    answer_reply,
    text_reply,
    tool_reply,
)
from tests.test_voice_pause_resume_persistence import VoiceSessionHarness
from voiney_lab.curated_protocol import CuratedProtocolAction, CuratedProtocolSession
from voiney_lab.llm_router import (
    ROUTER_SYSTEM_PROMPT,
    LlmRouterSettings,
    route_turn_with_llm_router,
)
from voiney_lab.runtime_routing import route_curated_runtime_turn

ON = LlmRouterSettings(enabled=True, model="fake-router-model", timeout_seconds=1.0)


def _session(step_index: int | None = 3) -> CuratedProtocolSession:
    session = CuratedProtocolSession(miniprep_fixture())
    session.activate_configured()
    if step_index is not None:
        session.plan("시작", turn_id=1, language="ko", configuration_id=1, generation=1)
        session.current_index = step_index
    return session


def _route(session, said, client, *, turn_id=2, history=(), settings=ON, progress=None):
    calls = []

    def factory():
        calls.append(1)
        return client

    async def rules():
        return route_curated_runtime_turn(
            session, said, turn_id=turn_id, language="ko",
            configuration_id=1, generation=1,
        )

    outcome = asyncio.run(route_turn_with_llm_router(
        session, said, turn_id=turn_id, language="ko", settings=settings,
        client_factory=factory, rule_route=rules, history=history,
        configuration_id=1, generation=1, on_progress=progress,
    ))
    return outcome, calls


def _rules_alone(said, *, turn_id=2, step_index=3):
    twin = _session(step_index)
    plan = route_curated_runtime_turn(
        twin, said, turn_id=turn_id, language="ko", configuration_id=1, generation=1,
    ).plan
    return twin, plan


def _state(session) -> dict:
    state = copy.deepcopy({k: v for k, v in vars(session).items() if k != "_replay"})
    # Wall-clock values, and the telemetry naming who read the last turn.
    for key in ("_timer_started_at", "_experiment_started_at", "_experiment_ended_at",
                "_paused_at", "_last_front_rule", "_last_semantic_decision"):
        state.pop(key, None)
    return state


class SettingsTests(unittest.TestCase):
    def test_off_unless_turned_on(self) -> None:
        self.assertFalse(LlmRouterSettings.from_environment({}).enabled)
        self.assertFalse(LlmRouterSettings().enabled)
        on = LlmRouterSettings.from_environment({
            "VOINEY_LAB_LLM_ROUTER_ENABLED": "true",
            "VOINEY_LAB_ROUTER_MODEL": "grok-4.6",
            "VOINEY_LAB_LLM_ROUTER_TIMEOUT_SECONDS": "3",
        })
        self.assertEqual((on.enabled, on.model, on.timeout_seconds), (True, "grok-4.6", 3.0))
        self.assertEqual(
            LlmRouterSettings.from_environment({}).model, "grok-4.20-0309-non-reasoning",
        )

    def test_bad_values_fail_closed(self) -> None:
        for env in (
            {"VOINEY_LAB_LLM_ROUTER_ENABLED": "maybe"},
            {"VOINEY_LAB_LLM_ROUTER_TIMEOUT_SECONDS": "soon"},
            {"VOINEY_LAB_LLM_ROUTER_TIMEOUT_SECONDS": "90"},
        ):
            with self.subTest(env=env), self.assertRaises(ValueError):
                LlmRouterSettings.from_environment(env)


class RoutingTests(unittest.TestCase):
    def test_a_front_rule_turn_never_calls_the_model(self) -> None:
        session = _session()
        outcome, calls = _route(session, "잠깐", FakeRouterClient())
        self.assertEqual(calls, [])
        self.assertEqual(outcome.handled_by, "front:pause")
        self.assertIs(outcome.plan.action, CuratedProtocolAction.PAUSE)
        self.assertFalse(outcome.model_called)

    def test_the_model_is_shown_the_tools_the_data_and_the_turn(self) -> None:
        session = _session()
        client = FakeRouterClient(answer_reply("4단계입니다.", source_kind="server_state"))
        history = [{"at_step": "3", "status": "active", "user": "다음 단계", "handled_by": "llm"}]
        _route(session, "어디까지 했지?", client, history=history)
        request = client.requests[0]
        self.assertEqual(request["model"], "fake-router-model")
        self.assertEqual([tool["function"]["name"] for tool in request["tools"]],
                         ["answer", "change_state", "record_log"])
        self.assertEqual((request["tool_choice"], request["temperature"], request["stream"]),
                         ("required", 0, True))
        roles = [message["role"] for message in request["messages"]]
        self.assertEqual(roles, ["system", "system", "system", "system", "user"])
        self.assertEqual(request["messages"][0]["content"], ROUTER_SYSTEM_PROMPT)
        self.assertTrue(request["messages"][1]["content"].startswith("PROTOCOL CONTEXT"))
        self.assertIn('"S4.current_step"', request["messages"][1]["content"])
        self.assertTrue(request["messages"][2]["content"].startswith("SERVER SNAPSHOT"))
        self.assertIn('"current_step":"4"', request["messages"][2]["content"])
        self.assertIn("다음 단계", request["messages"][3]["content"])
        self.assertEqual(request["messages"][4]["content"], "어디까지 했지?")

    def test_a_proposal_that_passes_runs_through_the_sessions_branches(self) -> None:
        session = _session()
        client = FakeRouterClient(tool_reply(("change_state", {"action": "next", "evidence": "다음 거 하자"})))
        outcome, _ = _route(session, "자 이제 다음 거 하자", client)
        self.assertEqual(outcome.handled_by, "llm+tool")
        self.assertEqual(outcome.verdict.question, "completion")
        self.assertEqual(outcome.plan.speech_text, "4단계 완료하셨나요?")
        self.assertFalse(outcome.plan.state_changed)
        self.assertEqual(session.current_index, 3)
        self.assertIsNotNone(session.pending_completion_confirmation)

    def test_a_refused_proposal_takes_the_rules_path(self) -> None:
        said = "그만 끝내자 이제"
        session = _session()
        client = FakeRouterClient(tool_reply(("change_state", {"action": "stop", "evidence": "그만 끝내자"})))
        outcome, _ = _route(session, said, client)
        twin, plan = _rules_alone(said)
        self.assertEqual(outcome.handled_by, "fallback_rules")
        self.assertEqual(outcome.fallback_reason, "refused:end_word_missing")
        self.assertEqual(outcome.plan, plan)
        self.assertEqual(_state(session), _state(twin))

    def test_two_proposals_are_refused_and_the_rules_answer(self) -> None:
        session = _session()
        client = FakeRouterClient(tool_reply(
            ("change_state", {"action": "pause", "evidence": "잠깐"}),
            ("record_log", {"type": "observation", "value": "잠깐", "evidence": "잠깐"}),
        ))
        outcome, _ = _route(session, "잠깐 기록도", client)
        self.assertEqual(outcome.fallback_reason, "refused:more_than_one_proposal")

    def test_a_late_or_failing_model_takes_the_rules_path(self) -> None:
        for name, script, reason in (
            ("late", (answer_reply("늦은 답"), 0.6), "timeout"),
            ("error", RuntimeError("provider down"), "model_error"),
            ("silent", text_reply(""), "no_reply"),
            ("prose", text_reply("버퍼 1은 트리스 완충액입니다."), "answer_unreadable"),
        ):
            with self.subTest(name):
                said = "버퍼 1은 뭐야?"
                session = _session()
                settings = LlmRouterSettings(enabled=True, model="m", timeout_seconds=0.5)
                outcome, _ = _route(session, said, FakeRouterClient(script), settings=settings)
                twin, plan = _rules_alone(said)
                self.assertEqual(outcome.handled_by, "fallback_rules")
                self.assertEqual(outcome.fallback_reason, reason)
                self.assertEqual(outcome.plan, plan)
                self.assertEqual(_state(session), _state(twin))

    def test_an_answer_is_used_read_only(self) -> None:
        session = _session()
        before = _state(session)
        client = FakeRouterClient(answer_reply(
            "50 µL water로 DNA를 용출합니다.", source_kind="pdf",
            evidence_ids=("S4.current_step",),
        ))
        outcome, _ = _route(session, "뭘로 용출해?", client)
        self.assertEqual(outcome.handled_by, "llm")
        plan = outcome.plan
        self.assertIs(plan.action, CuratedProtocolAction.QUESTION)
        self.assertEqual(plan.intent_kind, "llm_router_answer")
        self.assertEqual(plan.speech_text, "50 µL water로 DNA를 용출합니다.")
        self.assertFalse(plan.state_changed)
        self.assertEqual(plan.evidence_ids, ("S4.current_step",))
        self.assertEqual(plan.source_texts, ("4 Elute the DNA with 50 µL water.",))
        self.assertEqual(_state(session), before)

    def test_an_answer_a_check_rejects_takes_the_rules_path(self) -> None:
        for spoken, reason in (
            ("Buffer 1은 300 µL 넣습니다.", "answer_rejected:number_not_in_source"),
            ("4단계로 넘어갔습니다.", "answer_rejected:claims_state_change"),
        ):
            with self.subTest(spoken):
                said = "버퍼 1은 얼마나 넣어?"
                session = _session()
                outcome, _ = _route(
                    session, said, FakeRouterClient(answer_reply(spoken, source_kind="none")),
                )
                twin, plan = _rules_alone(said)
                self.assertEqual(outcome.handled_by, "fallback_rules")
                self.assertEqual(outcome.fallback_reason, reason)
                self.assertEqual(outcome.rule_route.plan, plan)
                self.assertEqual(_state(session), _state(twin))

    def test_a_question_left_open_lapses_when_the_model_answers(self) -> None:
        session = _session()
        # Lane VX, decision 1: "다음 단계" no longer asks; "완료했어" does.
        session.plan("완료했어", turn_id=2, language="ko", configuration_id=1, generation=1)
        self.assertIsNotNone(session.pending_completion_confirmation)
        client = FakeRouterClient(answer_reply("버퍼 1은 Tris-HCl buffer입니다.", source_kind="none"))
        outcome, _ = _route(session, "버퍼 1은 뭐야?", client, turn_id=3)
        self.assertEqual(outcome.handled_by, "llm")
        self.assertIsNone(session.pending_completion_confirmation)
        self.assertFalse(session.plan("네", turn_id=4, language="ko",
                                      configuration_id=1, generation=1).state_changed)

    def test_progress_says_routing_then_composing_or_checking(self) -> None:
        seen = []

        async def progress(state):
            seen.append(state)

        _route(_session(), "버퍼 1은 뭐야?",
               FakeRouterClient(answer_reply("Tris-HCl buffer입니다.", source_kind="none")),
               progress=progress)
        self.assertEqual(seen, ["composing"])
        seen.clear()
        _route(_session(), "자 이제 다음 거 하자",
               FakeRouterClient(tool_reply(("change_state", {"action": "next", "evidence": "다음 거"}))),
               progress=progress)
        self.assertEqual(seen, ["checking_protocol"])
        seen.clear()
        _route(_session(), "버퍼 1은 얼마나 넣어?",
               FakeRouterClient(answer_reply("300 µL 넣습니다.")), progress=progress)
        self.assertEqual(seen, ["composing", "checking_protocol"])

    def test_usage_and_timings_are_kept(self) -> None:
        outcome, _ = _route(_session(), "버퍼 1은 뭐야?",
                            FakeRouterClient(answer_reply("Tris-HCl buffer입니다.", source_kind="none")))
        self.assertEqual(outcome.reply.usage["prompt_tokens"], 1200)
        self.assertIn("first_token_ms", outcome.timings_ms)
        self.assertIn("model_ms", outcome.timings_ms)


class AnswerCallTests(unittest.TestCase):
    """The answer offered as a function, one call required (after the first
    real evaluation run, where a model called change_state for questions)."""

    def test_an_answer_call_is_an_answer(self) -> None:
        session = _session()
        seen = []

        async def progress(state):
            seen.append(state)

        outcome, _ = _route(session, "뭘로 용출해?", FakeRouterClient(answer_call_reply(
            "50 µL water로 용출합니다.", evidence_ids=("S4.current_step",),
        )), progress=progress)
        self.assertEqual(outcome.handled_by, "llm")
        self.assertEqual(outcome.plan.speech_text, "50 µL water로 용출합니다.")
        self.assertEqual(seen, ["composing"])

    def test_a_state_tool_beside_an_answer_is_what_is_ruled_on(self) -> None:
        session = _session()
        both = tool_reply(
            ("answer", {"spoken": "네", "source_kind": "none", "evidence_ids": []}),
            ("record_log", {"type": "anomaly", "value": "원심분리기에서 이상한 소리가 나",
                            "evidence": "원심분리기에서 이상한 소리가 나"}),
        )
        outcome, _ = _route(session, "원심분리기에서 이상한 소리가 나", FakeRouterClient(both))
        self.assertEqual(outcome.handled_by, "llm+tool")
        self.assertIs(outcome.plan.action, CuratedProtocolAction.REPORT_ANOMALY)

    def test_two_answers_are_unreadable(self) -> None:
        session = _session()
        two = tool_reply(
            ("answer", {"spoken": "하나", "source_kind": "none", "evidence_ids": []}),
            ("answer", {"spoken": "둘", "source_kind": "none", "evidence_ids": []}),
        )
        outcome, _ = _route(session, "버퍼 1은 뭐야?", FakeRouterClient(two))
        self.assertEqual(outcome.fallback_reason, "answer_unreadable")


class _FakeAsyncOpenAI:
    """Stands in for AsyncOpenAI in server.py; hands out one scripted client."""

    client: FakeRouterClient | None = None
    built = 0

    def __new__(cls, *args, **kwargs):
        cls.built += 1
        return cls.client


@unittest.skipUnless(
    SOURCE_PDF.is_file(),
    f"requires the externally licensed Candidate A source PDF at {SOURCE_PDF}",
)
class RouterVoiceTests(VoiceSessionHarness, unittest.TestCase):
    """Over the WebSocket, with the workspace and the report store on."""

    def _run(self, *said, enabled: bool, script=()):
        if enabled:
            self.environment["VOINEY_LAB_LLM_ROUTER_ENABLED"] = "true"
            self.environment["XAI_API_KEY"] = "test-only-not-a-key"
            # The key is the router's. Step translation follows its own role's key
            # (lane F, decision 1), so that role is left keyless here.
            self.environment["VOINEY_LAB_TRANSLATION_PROVIDER"] = "anthropic"
            self.environment["ANTHROPIC_API_KEY"] = ""
        _FakeAsyncOpenAI.client = FakeRouterClient(*script)
        _FakeAsyncOpenAI.built = 0
        seen = {}

        async def scenario(socket, listener, say):
            await say(1, "프로토콜 시작해줘")
            for turn_id, text in enumerate(said, 2):
                await say(turn_id, text)
                seen[turn_id] = self._snapshot(listener)
            seen["history"] = listener.history.router_history()

        extra = (patch("voiney_lab.server.AsyncOpenAI", _FakeAsyncOpenAI),) if enabled else ()
        socket, _ = self._session(scenario, *extra)
        return socket, seen

    def test_off_means_no_model_and_no_router_fields(self) -> None:
        socket, seen = self._run("버퍼 1은 뭐야?", enabled=False)
        self.assertEqual(self._errors(socket), [])
        decision = socket.for_turn(2, "turn.route_decision")[-1]
        self.assertNotIn("router", decision)
        self.assertEqual(decision["runtime_router"], "curated_protocol")
        self.assertEqual(seen["history"], [])

    def test_an_answer_turn_says_the_models_answer(self) -> None:
        socket, seen = self._run(
            "탈색 용액이 뭐야?", enabled=True,
            script=(answer_reply("Solution A와 B로 탈색합니다.", source_kind="none"),),
        )
        self.assertEqual(self._errors(socket), [])
        self.assertEqual(socket.reply(2), "Solution A와 B로 탈색합니다.")
        decision = socket.for_turn(2, "turn.route_decision")[-1]
        self.assertEqual(decision["runtime_router"], "llm_router")
        self.assertEqual(decision["router"]["handled_by"], "llm")
        self.assertEqual(decision["router"]["usage"]["prompt_tokens"], 1200)
        states = [item["state"] for item in socket.for_turn(2, "turn.state")]
        self.assertIn("composing", states)
        self.assertEqual(seen[2]["step_index"], 0)
        self.assertEqual(seen["history"][-1]["handled_by"], "llm")
        self.assertEqual(seen["history"][-1]["assistant"], "Solution A와 B로 탈색합니다.")

    def test_the_start_is_a_front_turn_and_the_history_keeps_it(self) -> None:
        socket, seen = self._run("잠깐", enabled=True)
        self.assertEqual(_FakeAsyncOpenAI.built, 0)
        self.assertEqual(socket.for_turn(2, "turn.route_decision")[-1]["router"]["handled_by"],
                         "front:pause")
        self.assertEqual(
            [item["handled_by"] for item in seen["history"]],
            ["front:start_command", "front:pause"],
        )
        self.assertEqual(seen["history"][0]["server"]["result"], "executed")

    def test_a_failing_model_answers_as_the_rules_do(self) -> None:
        said = "탈색 용액이 뭐야?"
        off, _ = self._run(said, enabled=False)
        self._fresh_tenant()
        on, seen = self._run(said, enabled=True, script=(RuntimeError("provider down"),))
        self.assertEqual(on.reply(2), off.reply(2))
        self.assertEqual(on.for_turn(2, "turn.route_decision")[-1]["router"]["fallback_reason"],
                         "model_error")
        self.assertEqual(seen["history"][-1]["handled_by"], "fallback_rules")

    def test_a_proposal_moves_nothing_until_the_researcher_says_yes(self) -> None:
        socket, seen = self._run(
            "이제 다음 거 하자", "네", enabled=True,
            script=(tool_reply(("change_state", {"action": "next", "evidence": "다음 거 하자"})),),
        )
        self.assertEqual(self._errors(socket), [])
        self.assertEqual(socket.reply(2), "1단계 완료하셨나요?")
        self.assertEqual(seen[2]["step_index"], 0)
        self.assertEqual(seen[3]["step_index"], 1)
        self.assertEqual(seen[3]["durable"]["current_step_label"], "2")
        self.assertEqual(
            [(item["handled_by"], item.get("server", {}).get("result")) for item in seen["history"][1:]],
            [("llm+tool", "confirm_opened"), ("front:yes_no_open_question", "executed")],
        )


if __name__ == "__main__":
    unittest.main()
