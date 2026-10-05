"""Model providers by role: settings, and each adapter against a fake SDK (lane M1).

Decision of 2026-10-04: each role (router, answer, translation, analysis,
report, supplemental) names its provider, model and reasoning; the defaults
are xAI and the models the code called before. The adapters for OpenAI
(Responses API), Anthropic (Messages API) and Google (Gemini API) only move a
request and its reply to and from the chat-completions shape the code reads.

Everything here is contract-tested against fake SDK clients -- no request
leaves this machine, and nothing here is "live-tested".
"""

from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace
from typing import Any

from tests.protocol_vocabulary_support import miniprep_fixture
from voiney_lab.curated_protocol import CuratedProtocolSession
from voiney_lab.llm_router import (
    LlmRouterSettings,
    ROUTER_CALL_TOOLS,
    call_router_model,
    route_turn_with_llm_router,
)
from voiney_lab.model_providers import (
    DEFAULT_MODELS,
    ROLE_SETTINGS,
    ROLES,
    ModelProviderError,
    RoleModel,
    chat_client,
)
from voiney_lab.runtime_routing import route_curated_runtime_turn

TOOL_ARGUMENTS = {"type": "observation", "value": "젤이 살짝 부풀었어", "evidence": "기록해 줘"}
SCHEMA_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "reading_v1", "strict": True,
        "schema": {
            "type": "object", "additionalProperties": False,
            "properties": {"korean": {"type": "string"}}, "required": ["korean"],
        },
    },
}
MESSAGES = [
    {"role": "system", "content": "STANDING INSTRUCTIONS"},
    {"role": "system", "content": "PROTOCOL CONTEXT"},
    {"role": "system", "content": "SERVER SNAPSHOT"},
    {"role": "user", "content": "기록해 줘 젤이 살짝 부풀었어"},
]


class _AsyncStream:
    def __init__(self, items: list) -> None:
        self._items = items

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for item in self._items:
            if isinstance(item, BaseException):
                raise item
            yield item


class _StatusError(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(f"status {status}")
        self.status_code = status
        self.code = status


class APITimeoutError(Exception):
    """Named like the SDKs' timeout errors."""


# --- Fake SDK clients ----------------------------------------------------------------

class FakeAnthropic:
    """``messages.create`` -- a message, or a stream of Messages API events."""

    def __init__(self, *, reply: Any = None, events: list | None = None,
                 error: BaseException | None = None, hang: bool = False) -> None:
        self.requests: list[dict[str, Any]] = []
        self._reply, self._events, self._error, self._hang = reply, events, error, hang
        self.messages = SimpleNamespace(create=self._create)

    async def _create(self, **params: Any) -> Any:
        self.requests.append(params)
        if self._hang:
            await asyncio.sleep(30)
        if self._error is not None:
            raise self._error
        if params.get("stream"):
            return _AsyncStream(self._events or [])
        return self._reply


def anthropic_tool_events(name: str, arguments: dict) -> list:
    raw = json.dumps(arguments, ensure_ascii=False)
    usage = SimpleNamespace(input_tokens=120, cache_read_input_tokens=3000,
                            cache_creation_input_tokens=0, output_tokens=1)
    return [
        SimpleNamespace(type="message_start", message=SimpleNamespace(
            model="claude-test", usage=usage)),
        SimpleNamespace(type="content_block_start", index=0, content_block=SimpleNamespace(
            type="thinking")),
        SimpleNamespace(type="content_block_start", index=1, content_block=SimpleNamespace(
            type="tool_use", id="toolu_1", name=name)),
        SimpleNamespace(type="content_block_delta", index=1, delta=SimpleNamespace(
            type="input_json_delta", partial_json=raw[:10])),
        SimpleNamespace(type="content_block_delta", index=1, delta=SimpleNamespace(
            type="input_json_delta", partial_json=raw[10:])),
        SimpleNamespace(type="message_delta", usage=SimpleNamespace(output_tokens=42)),
    ]


class FakeOpenAI:
    """``responses.create`` -- a response, or a stream of Responses API events."""

    def __init__(self, *, reply: Any = None, events: list | None = None,
                 error: BaseException | None = None, hang: bool = False) -> None:
        self.requests: list[dict[str, Any]] = []
        self._reply, self._events, self._error, self._hang = reply, events, error, hang
        self.responses = SimpleNamespace(create=self._create)

    async def _create(self, **params: Any) -> Any:
        self.requests.append(params)
        if self._hang:
            await asyncio.sleep(30)
        if self._error is not None:
            raise self._error
        if params.get("stream"):
            return _AsyncStream(self._events or [])
        return self._reply


def openai_usage() -> SimpleNamespace:
    return SimpleNamespace(
        input_tokens=3100, output_tokens=40,
        input_tokens_details=SimpleNamespace(cached_tokens=2900, cache_write_tokens=0),
        output_tokens_details=SimpleNamespace(reasoning_tokens=0),
    )


def openai_tool_events(name: str, arguments: dict) -> list:
    raw = json.dumps(arguments, ensure_ascii=False)
    return [
        SimpleNamespace(type="response.output_item.added", output_index=1, item=SimpleNamespace(
            type="function_call", call_id="call_1", name=name, arguments="")),
        SimpleNamespace(type="response.function_call_arguments.delta", output_index=1, delta=raw[:7]),
        SimpleNamespace(type="response.function_call_arguments.delta", output_index=1, delta=raw[7:]),
        SimpleNamespace(type="response.completed", response=SimpleNamespace(
            model="gpt-test", usage=openai_usage())),
    ]


class FakeGemini:
    """``aio.models.generate_content(_stream)`` and ``models.generate_content``."""

    def __init__(self, *, reply: Any = None, chunks: list | None = None,
                 error: BaseException | None = None, hang: bool = False) -> None:
        self.requests: list[dict[str, Any]] = []
        self._reply, self._chunks, self._error, self._hang = reply, chunks, error, hang
        self.aio = SimpleNamespace(models=SimpleNamespace(
            generate_content=self._agenerate, generate_content_stream=self._astream))
        self.models = SimpleNamespace(generate_content=self._generate)

    async def _agenerate(self, **params: Any) -> Any:
        self.requests.append(params)
        if self._hang:
            await asyncio.sleep(30)
        if self._error is not None:
            raise self._error
        return self._reply

    async def _astream(self, **params: Any) -> Any:
        self.requests.append(params)
        if self._error is not None:
            raise self._error
        return _AsyncStream(self._chunks or [])

    def _generate(self, **params: Any) -> Any:
        self.requests.append(params)
        if self._error is not None:
            raise self._error
        return self._reply


def gemini_response(*parts: Any) -> SimpleNamespace:
    return SimpleNamespace(
        model_version="gemini-test",
        candidates=[SimpleNamespace(content=SimpleNamespace(parts=list(parts)))],
        usage_metadata=SimpleNamespace(
            prompt_token_count=3200, candidates_token_count=30, thoughts_token_count=12,
            cached_content_token_count=2800),
    )


def gemini_call(name: str, arguments: dict) -> SimpleNamespace:
    return SimpleNamespace(thought=False, text=None, function_call=SimpleNamespace(
        id=None, name=name, args=arguments))


def gemini_text(text: str, *, thought: bool = False) -> SimpleNamespace:
    return SimpleNamespace(thought=thought, text=text, function_call=None)


def _role(provider: str, model: str, reasoning: str | None = None, role: str = "router") -> RoleModel:
    return RoleModel(role, provider, model, reasoning)


def _router_call(client: Any, model: str) -> Any:
    return asyncio.run(call_router_model(client, model=model, messages=MESSAGES, max_output_tokens=400))


# --- Settings -------------------------------------------------------------------------

class RoleSettingsTests(unittest.TestCase):
    def test_every_role_defaults_to_xai_and_the_model_it_called_before(self) -> None:
        self.assertEqual(set(ROLES), set(ROLE_SETTINGS))
        for role in ROLES:
            with self.subTest(role=role):
                setting = RoleModel.from_environment(role, {})
                self.assertEqual(setting.provider, "xai")
                self.assertEqual(setting.model, DEFAULT_MODELS[role])
                self.assertIsNone(setting.reasoning)
        self.assertEqual(DEFAULT_MODELS["router"], "grok-4.20-0309-non-reasoning")
        self.assertEqual(DEFAULT_MODELS["translation"], "grok-4.6")
        self.assertEqual(DEFAULT_MODELS["supplemental"], "grok-4.6")
        self.assertEqual(LlmRouterSettings.from_environment({}).role_model,
                         RoleModel("router", "xai", "grok-4.20-0309-non-reasoning", None))

    def test_each_role_reads_its_own_three_settings(self) -> None:
        environment = {
            "VOINEY_LAB_ROUTER_PROVIDER": "Anthropic",
            "VOINEY_LAB_ROUTER_MODEL": "claude-haiku-4-5-20251001",
            "VOINEY_LAB_TRANSLATION_PROVIDER": "google",
            "VOINEY_LAB_TRANSLATION_REASONING": "high",
        }
        router = RoleModel.from_environment("router", environment)
        self.assertEqual((router.provider, router.model, router.reasoning),
                         ("anthropic", "claude-haiku-4-5-20251001", None))
        translation = RoleModel.from_environment("translation", environment)
        self.assertEqual((translation.provider, translation.model, translation.reasoning),
                         ("google", "grok-4.6", "high"))
        self.assertEqual(RoleModel.from_environment("answer", environment).provider, "xai")
        settings = LlmRouterSettings.from_environment(environment)
        self.assertEqual((settings.provider, settings.model),
                         ("anthropic", "claude-haiku-4-5-20251001"))

    def test_an_unknown_provider_or_reasoning_fails_loudly(self) -> None:
        with self.assertRaisesRegex(ValueError, "VOINEY_LAB_REPORT_PROVIDER"):
            RoleModel.from_environment("report", {"VOINEY_LAB_REPORT_PROVIDER": "mistral"})
        with self.assertRaisesRegex(ValueError, "VOINEY_LAB_ANSWER_REASONING"):
            RoleModel.from_environment("answer", {"VOINEY_LAB_ANSWER_REASONING": "extreme"})

    def test_a_role_without_its_providers_key_has_none(self) -> None:
        role = RoleModel.from_environment("router", {"VOINEY_LAB_ROUTER_PROVIDER": "openai"})
        self.assertEqual(role.api_key_setting, "OPENAI_API_KEY")
        self.assertFalse(role.has_key({}))
        self.assertTrue(role.has_key({"OPENAI_API_KEY": "test-only"}))
        with self.assertRaisesRegex(RuntimeError, "OPENAI_API_KEY"):
            chat_client(role, environment={})


class XaiTests(unittest.TestCase):
    def test_the_default_role_hands_back_the_sdk_client_itself(self) -> None:
        sdk = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=None)))
        self.assertIs(chat_client(_role("xai", "grok-4.6"), sdk_client=sdk), sdk)

    def test_a_reasoning_setting_is_added_unless_the_call_gives_one(self) -> None:
        seen: list[dict[str, Any]] = []

        async def create(**kwargs: Any) -> str:
            seen.append(kwargs)
            return "ok"

        sdk = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        client = chat_client(_role("xai", "grok-4.6", "low"), sdk_client=sdk)
        asyncio.run(client.chat.completions.create(model="grok-4.6", messages=[]))
        asyncio.run(client.chat.completions.create(model="grok-4.6", messages=[], reasoning_effort="high"))
        self.assertEqual([item["reasoning_effort"] for item in seen], ["low", "high"])


# --- One contract, three providers -----------------------------------------------------

class _ProviderContract:
    """Tool calls, structured output, errors, timeouts and empty replies."""

    provider = ""
    model = ""

    def fake_tool_call(self, name: str, arguments: dict) -> Any:
        raise NotImplementedError

    def fake_json(self, text: str) -> Any:
        raise NotImplementedError

    def fake_empty(self) -> Any:
        raise NotImplementedError

    def fake_error(self, error: BaseException) -> Any:
        raise NotImplementedError

    def fake_hang(self) -> Any:
        raise NotImplementedError

    def client(self, fake: Any, reasoning: str | None = None, role: str = "router") -> Any:
        return chat_client(_role(self.provider, self.model, reasoning, role), sdk_client=fake)

    def test_a_streamed_tool_call_reads_as_the_router_reads_one(self) -> None:
        fake = self.fake_tool_call("record_log", TOOL_ARGUMENTS)
        reply = _router_call(self.client(fake), self.model)
        self.assertEqual(len(reply.tool_calls), 1)
        name, arguments = reply.tool_calls[0]
        self.assertEqual(name, "record_log")
        self.assertEqual(json.loads(arguments), TOOL_ARGUMENTS)
        self.assertGreater(reply.usage["prompt_tokens"], 0)
        self.assertGreater(reply.usage["completion_tokens"], 0)
        self.assertGreaterEqual(reply.usage.get("cached_prompt_tokens", 0), 0)
        self.assertIn("cache_write_tokens", reply.usage)

    def test_the_answer_function_is_a_tool_call_too(self) -> None:
        answer = {"spoken": "PDF에서 확인할 수 없어요.", "source_kind": "none", "evidence_ids": []}
        fake = self.fake_tool_call("answer", answer)
        reply = _router_call(self.client(fake), self.model)
        self.assertEqual(reply.tool_calls[0][0], "answer")
        self.assertEqual(json.loads(reply.tool_calls[0][1]), answer)

    def test_structured_output_comes_back_as_the_message_content(self) -> None:
        fake = self.fake_json('{"korean": "세척합니다."}')
        response = asyncio.run(self.client(fake, role="translation").chat.completions.create(
            model=self.model, messages=MESSAGES, response_format=SCHEMA_FORMAT, temperature=0,
        ))
        self.assertEqual(json.loads(response.choices[0].message.content), {"korean": "세척합니다."})
        self.assert_schema_sent(fake.requests[-1])

    def test_an_empty_reply_is_empty(self) -> None:
        reply = _router_call(self.client(self.fake_empty()), self.model)
        self.assertEqual((reply.content, reply.tool_calls), ("", ()))

    def test_provider_errors_are_typed_and_carry_no_prompt(self) -> None:
        for status, kind in ((402, "payment_required"), (429, "rate_limited"),
                             (401, "authentication"), (400, "invalid_request"), (503, "server")):
            with self.subTest(status=status):
                with self.assertRaises(ModelProviderError) as caught:
                    _router_call(self.client(self.fake_error(_StatusError(status))), self.model)
                self.assertEqual((caught.exception.kind, caught.exception.status_code), (kind, status))
                self.assertEqual(caught.exception.provider, self.provider)
                self.assertNotIn("기록해", str(caught.exception))

    def test_an_sdk_timeout_is_a_timeout(self) -> None:
        with self.assertRaises(ModelProviderError) as caught:
            _router_call(self.client(self.fake_error(APITimeoutError("slow"))), self.model)
        self.assertEqual(caught.exception.kind, "timeout")

    def test_a_late_reply_is_cut_by_the_callers_deadline(self) -> None:
        async def run() -> None:
            await asyncio.wait_for(call_router_model(
                self.client(self.fake_hang()), model=self.model, messages=MESSAGES,
            ), timeout=0.05)

        with self.assertRaises(asyncio.TimeoutError):
            asyncio.run(run())

    def test_a_router_turn_is_ruled_on_by_the_server_unchanged(self) -> None:
        session = CuratedProtocolSession(miniprep_fixture())
        session.activate_configured()
        session.plan("시작", turn_id=1, language="ko", configuration_id=1, generation=1)
        session.current_index = 3
        said = "기록해 줘 젤이 살짝 부풀었어"
        fake = self.fake_tool_call("record_log", {
            "type": "observation", "value": "젤이 살짝 부풀었어", "evidence": said,
        })

        async def rules():
            return route_curated_runtime_turn(
                session, said, turn_id=2, language="ko", configuration_id=1, generation=1)

        settings = LlmRouterSettings(enabled=True, model=self.model, timeout_seconds=5.0,
                                     provider=self.provider)
        outcome = asyncio.run(route_turn_with_llm_router(
            session, said, turn_id=2, language="ko", settings=settings,
            client_factory=lambda: chat_client(settings.role_model, sdk_client=fake),
            rule_route=rules, configuration_id=1, generation=1,
        ))
        self.assertEqual(outcome.handled_by, "llm+tool")
        self.assertEqual(outcome.verdict.reason_code, "accepted")
        self.assertTrue(outcome.plan.reported_observation)

    def test_a_proposal_the_server_refuses_takes_the_rules_path(self) -> None:
        session = CuratedProtocolSession(miniprep_fixture())
        session.activate_configured()
        session.plan("시작", turn_id=1, language="ko", configuration_id=1, generation=1)
        session.current_index = 3
        said = "다음 단계로 넘어가자"
        # A step other than the current one is refused (no skipping).
        fake = self.fake_tool_call("change_state", {
            "action": "next", "target_step": "1", "evidence": "다음 단계로 넘어가자",
        })

        async def rules():
            return route_curated_runtime_turn(
                session, said, turn_id=2, language="ko", configuration_id=1, generation=1)

        settings = LlmRouterSettings(enabled=True, model=self.model, timeout_seconds=5.0,
                                     provider=self.provider)
        before = session.state()["revision"]
        outcome = asyncio.run(route_turn_with_llm_router(
            session, said, turn_id=2, language="ko", settings=settings,
            client_factory=lambda: chat_client(settings.role_model, sdk_client=fake),
            rule_route=rules, configuration_id=1, generation=1,
        ))
        self.assertEqual(outcome.handled_by, "fallback_rules")
        self.assertEqual(outcome.fallback_reason, "refused:target_not_current_step")
        self.assertEqual(session.state()["revision"], before)

    def assert_schema_sent(self, request: dict[str, Any]) -> None:
        raise NotImplementedError


class AnthropicContractTests(_ProviderContract, unittest.TestCase):
    provider = "anthropic"
    model = "claude-haiku-4-5-20251001"

    def fake_tool_call(self, name, arguments):
        return FakeAnthropic(events=anthropic_tool_events(name, arguments))

    def fake_json(self, text):
        return FakeAnthropic(reply=SimpleNamespace(
            model="claude-test", content=[SimpleNamespace(type="text", text=text)],
            usage=SimpleNamespace(input_tokens=10, output_tokens=5,
                                  cache_read_input_tokens=0, cache_creation_input_tokens=0)))

    def fake_empty(self):
        return FakeAnthropic(events=[SimpleNamespace(type="message_start", message=SimpleNamespace(
            model="claude-test", usage=SimpleNamespace(input_tokens=1, output_tokens=0))),
            SimpleNamespace(type="message_delta", usage=SimpleNamespace(output_tokens=0))])

    def fake_error(self, error):
        return FakeAnthropic(error=error)

    def fake_hang(self):
        return FakeAnthropic(hang=True)

    def assert_schema_sent(self, request):
        self.assertEqual(request["output_config"]["format"]["type"], "json_schema")
        self.assertEqual(request["output_config"]["format"]["schema"],
                         SCHEMA_FORMAT["json_schema"]["schema"])

    def test_the_request_caches_the_stable_system_blocks(self) -> None:
        fake = self.fake_tool_call("record_log", TOOL_ARGUMENTS)
        _router_call(self.client(fake), self.model)
        request = fake.requests[0]
        self.assertEqual([block["text"] for block in request["system"]],
                         ["STANDING INSTRUCTIONS", "PROTOCOL CONTEXT", "SERVER SNAPSHOT"])
        self.assertNotIn("cache_control", request["system"][0])
        self.assertEqual(request["system"][1]["cache_control"], {"type": "ephemeral"})
        self.assertNotIn("cache_control", request["system"][2])
        self.assertEqual(request["messages"], [{"role": "user", "content": MESSAGES[-1]["content"]}])
        self.assertEqual(request["tool_choice"], {"type": "any"})
        self.assertEqual([tool["name"] for tool in request["tools"]],
                         [tool["function"]["name"] for tool in ROUTER_CALL_TOOLS])
        # anthropic 1.x has no temperature keyword: it goes in the body.
        self.assertNotIn("temperature", request)
        self.assertEqual(request["extra_body"], {"temperature": 0})
        self.assertEqual(request["max_tokens"], 400)
        self.assertNotIn("thinking", request)

    def test_sonnet_5_5_at_reasoning_none_asks_without_forcing_or_sampling(self) -> None:
        fake = self.fake_tool_call("record_log", TOOL_ARGUMENTS)
        client = chat_client(_role("anthropic", "claude-sonnet-5-5", "none"), sdk_client=fake)
        _router_call(client, "claude-sonnet-5-5")
        request = fake.requests[0]
        self.assertEqual(request["tool_choice"], {"type": "auto"})
        self.assertEqual(request["thinking"], {"type": "between_tools"})
        self.assertEqual(request["output_config"], {"effort": "low"})
        self.assertNotIn("temperature", request)
        self.assertNotIn("extra_body", request)
        self.assertEqual(request["max_tokens"], 400)

    def test_a_tool_loop_turns_into_tool_use_and_tool_result_blocks(self) -> None:
        fake = self.fake_json("{}")
        client = self.client(fake, role="answer")
        asyncio.run(client.chat.completions.create(model=self.model, messages=[
            {"role": "system", "content": "S"},
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function",
             "function": {"name": "lookup", "arguments": "{\"q\": 1}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "result"},
        ]))
        messages = fake.requests[0]["messages"]
        self.assertEqual(messages[1]["content"][0], {"type": "tool_use", "id": "c1", "name": "lookup", "input": {"q": 1}})
        self.assertEqual(messages[2]["content"][0]["type"], "tool_result")
        self.assertEqual(messages[2]["content"][0]["tool_use_id"], "c1")


class OpenAIContractTests(_ProviderContract, unittest.TestCase):
    provider = "openai"
    model = "gpt-6-luna"

    def fake_tool_call(self, name, arguments):
        return FakeOpenAI(events=openai_tool_events(name, arguments))

    def fake_json(self, text):
        return FakeOpenAI(reply=SimpleNamespace(model="gpt-test", usage=openai_usage(), output=[
            SimpleNamespace(type="reasoning"),
            SimpleNamespace(type="message", content=[SimpleNamespace(type="output_text", text=text)]),
        ]))

    def fake_empty(self):
        return FakeOpenAI(events=[SimpleNamespace(type="response.completed", response=SimpleNamespace(
            model="gpt-test", usage=openai_usage()))])

    def fake_error(self, error):
        return FakeOpenAI(error=error)

    def fake_hang(self):
        return FakeOpenAI(hang=True)

    def assert_schema_sent(self, request):
        self.assertEqual(request["text"]["format"]["type"], "json_schema")
        self.assertEqual(request["text"]["format"]["name"], "reading_v1")
        self.assertTrue(request["text"]["format"]["strict"])

    def test_the_request_is_a_responses_call_with_the_tools(self) -> None:
        fake = self.fake_tool_call("record_log", TOOL_ARGUMENTS)
        client = chat_client(_role("openai", self.model, "none"), sdk_client=fake)
        _router_call(client, self.model)
        request = fake.requests[0]
        self.assertTrue(request["stream"])
        self.assertFalse(request["store"])
        self.assertEqual(request["reasoning"], {"effort": "none"})
        self.assertEqual(request["temperature"], 0)
        self.assertEqual(request["tool_choice"], "required")
        self.assertEqual(request["max_output_tokens"], 400)
        self.assertEqual([item["role"] for item in request["input"]],
                         ["system", "system", "system", "user"])
        self.assertEqual({tool["type"] for tool in request["tools"]}, {"function"})

    def test_reasoning_above_none_drops_temperature(self) -> None:
        fake = self.fake_json("{}")
        client = chat_client(_role("openai", "gpt-6.1-sol", "low", "translation"), sdk_client=fake)
        asyncio.run(client.chat.completions.create(model="gpt-6.1-sol", messages=MESSAGES, temperature=0))
        self.assertNotIn("temperature", fake.requests[0])
        self.assertEqual(fake.requests[0]["reasoning"], {"effort": "low"})

    def test_the_cached_and_written_tokens_are_reported(self) -> None:
        reply = _router_call(self.client(self.fake_tool_call("record_log", TOOL_ARGUMENTS)), self.model)
        self.assertEqual(reply.usage["cached_prompt_tokens"], 2900)
        self.assertEqual(reply.usage["cache_write_tokens"], 0)
        self.assertEqual(reply.usage["reasoning_tokens"], 0)


class GeminiContractTests(_ProviderContract, unittest.TestCase):
    provider = "google"
    model = "gemini-3.8-flash"

    def fake_tool_call(self, name, arguments):
        return FakeGemini(chunks=[gemini_response(gemini_text("…", thought=True)),
                                  gemini_response(gemini_call(name, arguments))])

    def fake_json(self, text):
        return FakeGemini(reply=gemini_response(gemini_text(text)))

    def fake_empty(self):
        return FakeGemini(chunks=[gemini_response()])

    def fake_error(self, error):
        return FakeGemini(error=error)

    def fake_hang(self):
        return FakeGemini(hang=True)

    def assert_schema_sent(self, request):
        config = request["config"]
        self.assertEqual(config["response_mime_type"], "application/json")
        self.assertEqual(config["response_json_schema"], SCHEMA_FORMAT["json_schema"]["schema"])

    def test_a_late_reply_is_cut_by_the_callers_deadline(self) -> None:
        async def run() -> None:
            await asyncio.wait_for(self.client(self.fake_hang(), role="translation").chat.completions.create(
                model=self.model, messages=MESSAGES), timeout=0.05)

        with self.assertRaises(asyncio.TimeoutError):
            asyncio.run(run())

    def test_the_request_names_the_function_mode_and_thinking_level(self) -> None:
        fake = self.fake_tool_call("record_log", TOOL_ARGUMENTS)
        client = chat_client(_role("google", self.model, "none"), sdk_client=fake)
        _router_call(client, self.model)
        config = fake.requests[0]["config"]
        self.assertEqual(config["tool_config"], {"function_calling_config": {"mode": "ANY"}})
        # Gemini 3 cannot turn thinking off; "none" asks for the least.
        self.assertEqual(config["thinking_config"], {"thinking_level": "low"})
        self.assertEqual(config["system_instruction"],
                         "STANDING INSTRUCTIONS\n\nPROTOCOL CONTEXT\n\nSERVER SNAPSHOT")
        self.assertEqual(fake.requests[0]["contents"],
                         [{"role": "user", "parts": [{"text": MESSAGES[-1]["content"]}]}])

    def test_thinking_tokens_count_as_output_and_cached_tokens_are_reported(self) -> None:
        reply = _router_call(self.client(self.fake_tool_call("record_log", TOOL_ARGUMENTS)), self.model)
        self.assertEqual(reply.usage["completion_tokens"], 42)
        self.assertEqual(reply.usage["cached_prompt_tokens"], 2800)

    def test_a_spent_prepaid_balance_is_payment_required(self) -> None:
        # ai.google.dev, billing: requests fail with HTTP 402 once prepaid
        # credits run out.
        with self.assertRaises(ModelProviderError) as caught:
            _router_call(self.client(self.fake_error(_StatusError(402))), self.model)
        self.assertEqual(caught.exception.kind, "payment_required")


class AnthropicSdkSignatureTests(unittest.TestCase):
    def test_every_keyword_the_adapter_sends_is_one_the_sdk_takes(self) -> None:
        # The pinned SDK's own signature, so a request never fails with an
        # unexpected keyword (the 1.x SDK dropped temperature).
        import inspect

        from anthropic.resources.messages import AsyncMessages

        accepted = set(inspect.signature(AsyncMessages.create).parameters)
        fake = FakeAnthropic(events=anthropic_tool_events("record_log", TOOL_ARGUMENTS))
        for model, reasoning in (
            ("claude-haiku-4-5-20251001", None), ("claude-sonnet-5-5", "none"),
            ("claude-opus-5-5", "high"),
        ):
            client = chat_client(_role("anthropic", model, reasoning), sdk_client=fake)
            _router_call(client, model)
            with self.subTest(model=model):
                self.assertLessEqual(set(fake.requests[-1]) - {"stream"}, accepted)


class SupplementalShapeTests(unittest.TestCase):
    def test_a_responses_style_call_reads_back_as_output_text(self) -> None:
        fake = FakeAnthropic(reply=SimpleNamespace(
            model="claude-test", content=[SimpleNamespace(type="text", text="일반 설명입니다.")],
            usage=SimpleNamespace(input_tokens=10, output_tokens=5)))
        client = chat_client(_role("anthropic", "claude-haiku-4-5-20251001", role="supplemental"),
                             sdk_client=fake)
        response = asyncio.run(client.responses.create(
            model="claude-haiku-4-5-20251001",
            input=[{"role": "system", "content": "S"}, {"role": "user", "content": "q"}],
            max_output_tokens=240, timeout=SimpleNamespace(read=8.0),
        ))
        self.assertEqual(response.output_text, "일반 설명입니다.")
        self.assertEqual(fake.requests[0]["max_tokens"], 240)
        self.assertEqual(fake.requests[0]["timeout"], 8.0)


if __name__ == "__main__":
    unittest.main()
