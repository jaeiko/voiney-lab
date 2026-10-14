"""Lane AN2, decision 1: a provider's 429 and transient 5xx are sent again.

Lane AN measured 23 HTTP 429 from Google while long documents ran eight
pieces at once beside other runs; a piece that failed twice failed the whole
document. The adapter now sends the *same* request again after a 429 or a
transient 5xx -- after the provider's Retry-After when it gives one, after a
capped exponential wait when it does not -- at most ``max_retries`` times, and
only for the roles that are background work (analysis, translation, report).

Nothing here changes what a reply must pass: a retried reply goes through the
same checks as any other. Everything is contract-tested against fake SDK
clients and a patched clock: no request leaves this machine and nothing here
is "live-tested".
"""

from __future__ import annotations

import asyncio
import logging
import unittest
from types import SimpleNamespace
from typing import Any
from unittest import mock

from voiney_lab import model_providers
from voiney_lab.model_providers import (
    ModelProviderError,
    ProviderRetryPolicy,
    RoleModel,
    chat_client,
    observe_provider_retries,
)

MESSAGES = [
    {"role": "system", "content": "STANDING INSTRUCTIONS"},
    {"role": "user", "content": "PRIVATE PAGE TEXT"},
]


class _StatusError(Exception):
    """An SDK error: a status, and optionally the response headers and details."""

    def __init__(self, status: int, *, headers: dict | None = None, details: Any = None) -> None:
        super().__init__(f"status {status}")
        self.status_code = status
        self.code = status
        if headers is not None:
            self.response = SimpleNamespace(headers=headers)
        if details is not None:
            self.details = details


def _gemini_reply(text: str = '{"ok": true}') -> SimpleNamespace:
    part = SimpleNamespace(thought=False, text=text, function_call=None)
    return SimpleNamespace(
        model_version="gemini-test",
        candidates=[SimpleNamespace(
            content=SimpleNamespace(parts=[part]),
            finish_reason=SimpleNamespace(name="STOP"))],
        usage_metadata=SimpleNamespace(
            prompt_token_count=10, candidates_token_count=5, thoughts_token_count=0,
            cached_content_token_count=0),
    )


class _SequencedGemini:
    """A fake google-genai client that plays one outcome per call, in order."""

    def __init__(self, *outcomes: Any) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[dict[str, Any]] = []
        self.models = SimpleNamespace(generate_content=self._generate)
        self.aio = SimpleNamespace(models=SimpleNamespace(generate_content=self._agenerate))

    def _next(self, params: dict[str, Any]) -> Any:
        self.requests.append(params)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def _generate(self, **params: Any) -> Any:
        return self._next(params)

    async def _agenerate(self, **params: Any) -> Any:
        return self._next(params)


def _client(fake: Any, role: str = "analysis", *, asynchronous: bool = False,
            policy: ProviderRetryPolicy | None = None, environment: dict | None = None) -> Any:
    return chat_client(
        RoleModel(role, "google", "gemini-3.8-flash", "low"),
        asynchronous=asynchronous, sdk_client=fake,
        retry_policy=policy, environment=environment,
    )


class _Clock:
    """Replaces the adapter's sleep and jitter; remembers every wait."""

    def __init__(self, jitter: float = 1.0) -> None:
        self.waits: list[float] = []
        self._jitter = jitter

    def __enter__(self) -> "_Clock":
        async def asleep(seconds: float) -> None:
            self.waits.append(seconds)

        self._patches = [
            mock.patch.object(model_providers, "_sleep", self.waits.append),
            mock.patch.object(model_providers, "_async_sleep", asleep),
            mock.patch.object(model_providers, "_jitter", lambda: self._jitter),
        ]
        for patch in self._patches:
            patch.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        for patch in self._patches:
            patch.stop()


def _policy() -> ProviderRetryPolicy:
    return ProviderRetryPolicy(max_retries=3, base_seconds=2.0, max_wait_seconds=30.0)


def _call(client: Any) -> Any:
    return client.chat.completions.create(model="gemini-3.8-flash", messages=MESSAGES)


class RateLimitRetryTests(unittest.TestCase):
    def test_a_429_is_sent_again_and_the_second_answer_is_the_reply(self) -> None:
        fake = _SequencedGemini(_StatusError(429), _gemini_reply())
        with _Clock() as clock:
            reply = _call(_client(fake, policy=_policy()))
        self.assertEqual(reply.choices[0].message.content, '{"ok": true}')
        self.assertEqual(len(fake.requests), 2)
        self.assertEqual(fake.requests[0], fake.requests[1])
        self.assertEqual(clock.waits, [2.0])

    def test_the_same_request_is_sent_each_time(self) -> None:
        fake = _SequencedGemini(_StatusError(429), _StatusError(429), _gemini_reply())
        with _Clock():
            _call(_client(fake, policy=_policy()))
        self.assertEqual(fake.requests[0], fake.requests[2])

    def test_a_retry_after_header_is_waited_exactly(self) -> None:
        error = _StatusError(429, headers={"Retry-After": "7"})
        fake = _SequencedGemini(error, _gemini_reply())
        with _Clock() as clock:
            _call(_client(fake, policy=_policy()))
        self.assertEqual(clock.waits, [7.0])

    def test_a_retry_after_header_in_any_letter_case(self) -> None:
        error = _StatusError(429, headers={"retry-after": "4.5"})
        fake = _SequencedGemini(error, _gemini_reply())
        with _Clock() as clock:
            _call(_client(fake, policy=_policy()))
        self.assertEqual(clock.waits, [4.5])

    def test_googles_retry_info_in_the_error_details_is_read_like_the_header(self) -> None:
        details = {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "details": [
            {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "3.5s"},
        ]}}
        fake = _SequencedGemini(_StatusError(429, details=details), _gemini_reply())
        with _Clock() as clock:
            _call(_client(fake, policy=_policy()))
        self.assertEqual(clock.waits, [3.5])

    def test_without_retry_after_the_wait_doubles_up_to_the_cap(self) -> None:
        policy = ProviderRetryPolicy(max_retries=5, base_seconds=2.0, max_wait_seconds=10.0)
        fake = _SequencedGemini(*[_StatusError(429)] * 5, _gemini_reply())
        with _Clock(jitter=1.0) as clock:
            _call(_client(fake, policy=policy))
        self.assertEqual(clock.waits, [2.0, 4.0, 8.0, 10.0, 10.0])

    def test_the_wait_is_jittered_between_half_and_all_of_the_step(self) -> None:
        fake = _SequencedGemini(_StatusError(429), _gemini_reply())
        with _Clock(jitter=0.0) as clock:
            _call(_client(fake, policy=_policy()))
        self.assertEqual(clock.waits, [1.0])

    def test_it_gives_up_after_max_retries_with_the_providers_own_error(self) -> None:
        fake = _SequencedGemini(*[_StatusError(429)] * 4)
        with _Clock(), self.assertRaises(ModelProviderError) as caught:
            _call(_client(fake, policy=_policy()))
        self.assertEqual((caught.exception.kind, caught.exception.status_code), ("rate_limited", 429))
        self.assertEqual(len(fake.requests), 1 + _policy().max_retries)

    def test_a_retry_after_longer_than_the_cap_is_not_waited_for(self) -> None:
        error = _StatusError(429, headers={"Retry-After": "120"})
        fake = _SequencedGemini(error, _gemini_reply())
        with _Clock() as clock, self.assertRaises(ModelProviderError) as caught:
            _call(_client(fake, policy=_policy()))
        self.assertEqual(caught.exception.kind, "rate_limited")
        self.assertEqual(len(fake.requests), 1)
        self.assertEqual(clock.waits, [])

    def test_the_error_carries_the_retry_after_it_was_given(self) -> None:
        error = _StatusError(429, headers={"Retry-After": "120"})
        with _Clock(), self.assertRaises(ModelProviderError) as caught:
            _call(_client(_SequencedGemini(error), policy=_policy()))
        self.assertEqual(caught.exception.retry_after, 120.0)


class TransientServerErrorTests(unittest.TestCase):
    def test_500_502_and_503_are_sent_again(self) -> None:
        for status in (500, 502, 503):
            with self.subTest(status=status):
                fake = _SequencedGemini(_StatusError(status), _gemini_reply())
                with _Clock() as clock:
                    reply = _call(_client(fake, policy=_policy()))
                self.assertEqual(reply.choices[0].message.content, '{"ok": true}')
                self.assertEqual(len(clock.waits), 1)

    def test_a_refusal_that_a_second_send_cannot_change_is_not_sent_again(self) -> None:
        for status in (400, 401, 402, 403, 404, 422, 504):
            with self.subTest(status=status):
                fake = _SequencedGemini(_StatusError(status), _gemini_reply())
                with _Clock() as clock, self.assertRaises(ModelProviderError):
                    _call(_client(fake, policy=_policy()))
                self.assertEqual(len(fake.requests), 1)
                self.assertEqual(clock.waits, [])

    def test_a_timeout_is_not_sent_again(self) -> None:
        class APITimeoutError(Exception):
            pass

        fake = _SequencedGemini(APITimeoutError("slow"), _gemini_reply())
        with _Clock(), self.assertRaises(ModelProviderError) as caught:
            _call(_client(fake, policy=_policy()))
        self.assertEqual(caught.exception.kind, "timeout")
        self.assertEqual(len(fake.requests), 1)


class RoleBoundaryTests(unittest.TestCase):
    def test_the_background_roles_are_retried(self) -> None:
        for role in ("analysis", "translation", "report"):
            with self.subTest(role=role):
                fake = _SequencedGemini(_StatusError(429), _gemini_reply())
                with _Clock():
                    _call(_client(fake, role=role, policy=_policy()))
                self.assertEqual(len(fake.requests), 2)

    def test_a_voice_turn_role_is_never_made_to_wait(self) -> None:
        for role in ("router", "answer", "supplemental"):
            with self.subTest(role=role):
                fake = _SequencedGemini(_StatusError(429), _gemini_reply())
                with _Clock() as clock, self.assertRaises(ModelProviderError):
                    _call(_client(fake, role=role, policy=_policy()))
                self.assertEqual(len(fake.requests), 1)
                self.assertEqual(clock.waits, [])


class AsynchronousClientTests(unittest.TestCase):
    def test_the_asynchronous_client_waits_without_blocking_and_sends_again(self) -> None:
        fake = _SequencedGemini(_StatusError(429, headers={"Retry-After": "3"}), _gemini_reply())
        with _Clock() as clock:
            reply = asyncio.run(_call(_client(fake, asynchronous=True, policy=_policy())))
        self.assertEqual(reply.choices[0].message.content, '{"ok": true}')
        self.assertEqual(clock.waits, [3.0])

    def test_the_asynchronous_client_gives_up_too(self) -> None:
        fake = _SequencedGemini(*[_StatusError(503)] * 4)
        with _Clock(), self.assertRaises(ModelProviderError):
            asyncio.run(_call(_client(fake, asynchronous=True, policy=_policy())))
        self.assertEqual(len(fake.requests), 4)

    def test_a_cancelled_wait_sends_nothing_more(self) -> None:
        fake = _SequencedGemini(_StatusError(429), _gemini_reply())

        async def run() -> None:
            await asyncio.wait_for(_call(_client(fake, asynchronous=True, policy=_policy())), 0.05)

        # The real async sleep: a 2 s wait is cut by the caller's 50 ms deadline.
        with mock.patch.object(model_providers, "_jitter", lambda: 1.0):
            with self.assertRaises(asyncio.TimeoutError):
                asyncio.run(run())
        self.assertEqual(len(fake.requests), 1)


class OtherProvidersTests(unittest.TestCase):
    def test_anthropic_and_openai_adapters_are_retried_the_same_way(self) -> None:
        anthropic_reply = SimpleNamespace(
            model="claude-test", content=[SimpleNamespace(type="text", text="ok")],
            usage=SimpleNamespace(input_tokens=1, output_tokens=1,
                                  cache_read_input_tokens=0, cache_creation_input_tokens=0))
        openai_reply = SimpleNamespace(
            model="gpt-test", output=[SimpleNamespace(type="message", content=[
                SimpleNamespace(type="output_text", text="ok")])], output_text="ok",
            usage=SimpleNamespace(input_tokens=1, output_tokens=1))
        cases = (
            ("anthropic", "claude-sonnet-5-5", lambda create: SimpleNamespace(
                messages=SimpleNamespace(create=create)), anthropic_reply),
            ("openai", "gpt-6.1-sol", lambda create: SimpleNamespace(
                responses=SimpleNamespace(create=create)), openai_reply),
        )
        for provider, model, build, reply in cases:
            with self.subTest(provider=provider):
                outcomes = [_StatusError(429, headers={"retry-after": "1"}), reply]
                seen: list[dict] = []

                def create(**params: Any) -> Any:
                    seen.append(params)
                    outcome = outcomes.pop(0)
                    if isinstance(outcome, BaseException):
                        raise outcome
                    return outcome

                client = chat_client(
                    RoleModel("analysis", provider, model, "low"), asynchronous=False,
                    sdk_client=build(create), retry_policy=_policy())
                with _Clock() as clock:
                    answer = client.chat.completions.create(model=model, messages=MESSAGES)
                self.assertEqual(answer.choices[0].message.content, "ok")
                self.assertEqual(len(seen), 2)
                self.assertEqual(clock.waits, [1.0])


class RecordTests(unittest.TestCase):
    def test_each_429_is_reported_to_the_observer_with_nothing_private(self) -> None:
        fake = _SequencedGemini(
            _StatusError(429, headers={"Retry-After": "5"}), _StatusError(503), _gemini_reply())
        events = []
        with _Clock(), observe_provider_retries(events.append):
            _call(_client(fake, policy=_policy()))
        self.assertEqual([(e.reason, e.status_code, e.attempt, e.wait_seconds, e.will_retry)
                          for e in events],
                         [("rate_limited", 429, 1, 5.0, True), ("server", 503, 2, 4.0, True)])
        first = events[0]
        self.assertEqual((first.provider, first.role, first.model, first.retry_after),
                         ("google", "analysis", "gemini-3.8-flash", 5.0))
        self.assertNotIn("PRIVATE", repr(events))

    def test_the_failure_that_ends_the_retries_is_reported_as_not_retried(self) -> None:
        fake = _SequencedGemini(*[_StatusError(429)] * 4)
        events = []
        with _Clock(), observe_provider_retries(events.append), self.assertRaises(ModelProviderError):
            _call(_client(fake, policy=_policy()))
        self.assertEqual([e.will_retry for e in events], [True, True, True, False])
        self.assertEqual(events[-1].wait_seconds, 0.0)

    def test_an_observer_is_scoped_to_its_block(self) -> None:
        events = []
        with _Clock():
            with observe_provider_retries(events.append):
                pass
            _call(_client(_SequencedGemini(_StatusError(429), _gemini_reply()), policy=_policy()))
        self.assertEqual(events, [])

    def test_an_observer_that_raises_does_not_break_the_call(self) -> None:
        def broken(_event: Any) -> None:
            raise RuntimeError("observer bug")

        with _Clock(), observe_provider_retries(broken):
            reply = _call(_client(_SequencedGemini(_StatusError(429), _gemini_reply()), policy=_policy()))
        self.assertEqual(reply.choices[0].message.content, '{"ok": true}')

    def test_the_server_log_names_the_retry_without_the_prompt(self) -> None:
        fake = _SequencedGemini(_StatusError(429), _gemini_reply())
        with _Clock(), self.assertLogs("voiney_lab.model_providers", level=logging.INFO) as logs:
            _call(_client(fake, policy=_policy()))
        text = "\n".join(logs.output)
        self.assertIn("provider.retry", text)
        for word in ("provider=google", "role=analysis", "reason=rate_limited", "status=429", "attempt=1"):
            self.assertIn(word, text)
        self.assertNotIn("PRIVATE", text)


class PolicySettingsTests(unittest.TestCase):
    def test_the_defaults_are_bounded(self) -> None:
        policy = ProviderRetryPolicy.from_environment({})
        self.assertGreaterEqual(policy.max_retries, 1)
        self.assertLessEqual(policy.max_retries, 6)
        self.assertLessEqual(policy.max_wait_seconds, 120.0)

    def test_the_settings_name_the_retries_and_the_longest_wait(self) -> None:
        policy = ProviderRetryPolicy.from_environment({
            "VOINEY_LAB_PROVIDER_RETRY_MAX": "2",
            "VOINEY_LAB_PROVIDER_RETRY_MAX_WAIT_SECONDS": "15",
        })
        self.assertEqual((policy.max_retries, policy.max_wait_seconds), (2, 15.0))

    def test_zero_turns_retries_off(self) -> None:
        fake = _SequencedGemini(_StatusError(429), _gemini_reply())
        with _Clock(), self.assertRaises(ModelProviderError):
            _call(_client(fake, environment={"VOINEY_LAB_PROVIDER_RETRY_MAX": "0"}))
        self.assertEqual(len(fake.requests), 1)

    def test_a_value_that_is_not_a_number_or_is_out_of_range_falls_back_or_clamps(self) -> None:
        default = ProviderRetryPolicy.from_environment({})
        for raw in ("many", "", "-1", "1.5"):
            with self.subTest(raw=raw):
                policy = ProviderRetryPolicy.from_environment({"VOINEY_LAB_PROVIDER_RETRY_MAX": raw})
                self.assertEqual(policy.max_retries, default.max_retries)
        clamped = ProviderRetryPolicy.from_environment({
            "VOINEY_LAB_PROVIDER_RETRY_MAX": "99",
            "VOINEY_LAB_PROVIDER_RETRY_MAX_WAIT_SECONDS": "100000",
        })
        self.assertLessEqual(clamped.max_retries, 10)
        self.assertLessEqual(clamped.max_wait_seconds, 600.0)

    def test_the_client_reads_the_environment_when_no_policy_is_given(self) -> None:
        fake = _SequencedGemini(*[_StatusError(429)] * 3)
        with _Clock(), self.assertRaises(ModelProviderError):
            _call(_client(fake, environment={"VOINEY_LAB_PROVIDER_RETRY_MAX": "2"}))
        self.assertEqual(len(fake.requests), 3)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
