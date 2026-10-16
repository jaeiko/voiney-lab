"""Lane FX3, decision 8: a voice turn's model at its request limit, late or briefly down.

Lane AN2 kept the provider resend for the background roles (analysis,
translation, report); the voice-turn roles never wait. The person decided
(2026-10-16) that a voice turn stops there: nothing is sent again, the state
does not change, and "지금 답하지 못했어요. 다시 말씀해 주세요." is said once.

What was there: the router, on a timeout or any provider error, handed the
turn to the rules (``fall_back``), which could themselves change the state.
Now, for the router, a timeout and a ``ModelProviderError`` of the transient
kinds (rate_limited, timeout, server, connection) stop the turn with that
sentence; any other failure takes the rules' path as before. The Answer and
supplemental roles already sent once and let the rules' answer stand (the
person's choice: unchanged); the last tests pin that.
"""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests.lane_n_support import VoiceNotesHarness, shown
from tests.lane_ra_support import FakeAsyncOpenAI
from tests.router_fakes import FakeRouterClient, answer_reply
from tests.test_llm_router_wiring import _route, _rules_alone, _session, _state
from tests.test_multi_brain import snapshot
from voiney_lab.external_references import SupplementalKnowledgeSettings, XaiSupplementalKnowledge
from voiney_lab.llm_router import ROUTER_MODEL_UNAVAILABLE_KINDS, LlmRouterSettings
from voiney_lab.model_providers import ModelProviderError
from voiney_lab.multi_brain import HybridMultiBrain, MultiBrainSettings, activation_for
from voiney_lab.server import ROUTER_UNAVAILABLE_WORDS

SAID = "버퍼 1은 뭐야?"
QUICK = LlmRouterSettings(enabled=True, model="m", timeout_seconds=0.5)


def _error(kind: str, status: int | None) -> ModelProviderError:
    return ModelProviderError("openai", kind, status)


class RouterTests(unittest.TestCase):
    def test_a_transient_failure_stops_the_turn_and_changes_nothing(self) -> None:
        for name, script in (
            ("429", _error("rate_limited", 429)),
            ("503", _error("server", 503)),
            ("connection", _error("connection", None)),
            ("provider timeout", _error("timeout", None)),
            ("late", (answer_reply("늦은 답"), 0.6)),
        ):
            with self.subTest(name):
                session = _session()
                before = _state(session)
                client = FakeRouterClient(script)
                outcome, built = _route(session, SAID, client, settings=QUICK)
                self.assertEqual(outcome.handled_by, "model_unavailable")
                self.assertIsNone(outcome.plan)
                self.assertIsNone(outcome.rule_route, "the rules were run")
                self.assertEqual(len(client.requests), 1, "sent again")
                self.assertEqual(len(built), 1)
                self.assertEqual(_state(session), before)

    def test_the_reason_names_the_kind(self) -> None:
        session = _session()
        outcome, _ = _route(session, SAID, FakeRouterClient(_error("rate_limited", 429)), settings=QUICK)
        self.assertEqual(outcome.fallback_reason, "model_unavailable:rate_limited")
        outcome, _ = _route(_session(), SAID, FakeRouterClient((answer_reply("늦은 답"), 0.6)), settings=QUICK)
        self.assertEqual(outcome.fallback_reason, "model_unavailable:timeout")
        self.assertEqual(ROUTER_MODEL_UNAVAILABLE_KINDS, frozenset({"rate_limited", "timeout", "server", "connection"}))

    def test_any_other_failure_still_takes_the_rules_path(self) -> None:
        for name, error in (
            ("401", _error("authentication", 401)),
            ("400", _error("invalid_request", 400)),
            ("402", _error("payment_required", 402)),
            ("other", RuntimeError("provider down")),
        ):
            with self.subTest(name):
                session = _session()
                outcome, _ = _route(session, SAID, FakeRouterClient(error), settings=QUICK)
                twin, plan = _rules_alone(SAID)
                self.assertEqual(outcome.handled_by, "fallback_rules")
                self.assertEqual(outcome.fallback_reason, "model_error")
                self.assertEqual(outcome.plan, plan)
                self.assertEqual(_state(session), _state(twin))

    def test_a_front_rule_turn_never_calls_the_model(self) -> None:
        session = _session()
        client = FakeRouterClient(_error("rate_limited", 429))
        outcome, built = _route(session, "잠깐", client, settings=QUICK)
        self.assertTrue(outcome.handled_by.startswith("front:"))
        self.assertEqual(client.requests, [])


class VoiceTests(VoiceNotesHarness, unittest.TestCase):
    def _run(self, *script):
        self.environment.update({
            "VOINEY_LAB_LLM_ROUTER_ENABLED": "true", "VOINEY_LAB_ROUTER_PROVIDER": "xai",
            "VOINEY_LAB_ROUTER_MODEL": "fake-router-model", "XAI_API_KEY": "test-only-not-a-key",
            "VOINEY_LAB_TRANSLATION_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "",
        })
        client = FakeRouterClient(*script)
        FakeAsyncOpenAI.client = client
        seen: dict = {}

        async def scenario(socket, listener, say):
            await say(1, "프로토콜 시작해줘")
            curated = listener.curated_protocol_session
            seen["before"] = (curated.current_index, curated.workflow_status, curated.state()["revision"])
            await say(2, "솔루션 에이는 어디에 보관해?")
            seen["after"] = (curated.current_index, curated.workflow_status, curated.state()["revision"])

        socket = self.voice(scenario, patch("voiney_lab.server.AsyncOpenAI", FakeAsyncOpenAI))
        return socket, seen, client

    def test_the_sentence_is_said_once_and_the_state_stays(self) -> None:
        socket, seen, client = self._run(_error("rate_limited", 429))
        self.assertEqual(ROUTER_UNAVAILABLE_WORDS, "지금 답하지 못했어요. 다시 말씀해 주세요.")
        self.assertEqual(shown(socket)[2], ROUTER_UNAVAILABLE_WORDS)
        self.assertEqual(self.spoken.get(2), [ROUTER_UNAVAILABLE_WORDS])
        self.assertEqual(len(client.requests), 1)
        self.assertEqual(seen["after"], seen["before"])
        done = socket.for_turn(2, "turn.done")[-1]
        self.assertEqual(done["route"], "model_unavailable")
        self.assertEqual(socket.for_turn(2, "turn.route_decision"), [])
        self.assertEqual(self._errors(socket), [])

    @staticmethod
    def _errors(socket) -> list[dict]:
        return [item for item in socket.sent if item["type"] in {"experiment.session.error", "error"}]


class OtherVoiceRolesTests(unittest.IsolatedAsyncioTestCase):
    """Answer and supplemental: sent once; the caller keeps the rules' answer."""

    async def test_the_answer_role_is_sent_once_and_ends_unsuccessful(self) -> None:
        calls = []

        async def create(**kwargs):
            calls.append(kwargs)
            raise _error("rate_limited", 429)

        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        activation = activation_for(intent_kind="protocol_entity_question", visual_requested=False,
                                    unresolved_dimensions=())
        run = HybridMultiBrain(client, MultiBrainSettings(True, "fake", 2, 2, .2)).start(snapshot(), activation)
        terminal = await run.terminal("answer")
        self.assertNotEqual(terminal.status, "success")
        self.assertIsNone(terminal.output)
        self.assertEqual(len(calls), 1)

    async def test_the_supplemental_role_is_sent_once_and_reports_the_failure(self) -> None:
        calls = []

        async def create(**kwargs):
            calls.append(kwargs)
            raise _error("rate_limited", 429)

        client = SimpleNamespace(responses=SimpleNamespace(create=create))
        result = await XaiSupplementalKnowledge(
            client, SupplementalKnowledgeSettings(True, "fake", 2.0),
        ).explain("버퍼는 왜 써?", language="ko")
        self.assertNotEqual(result["status"], "success")
        self.assertNotIn("answer", result)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
