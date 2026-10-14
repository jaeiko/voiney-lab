"""Lane AN2, decision 2: an empty answer that ended with STOP is sent again once.

On lane AN's 315-page document 7 of 36 pieces came back empty: Gemini ended
them with finish reason STOP and wrote no text, and the same request sent
again was answered normally. The Google adapter now sends such a request one
more time (no wait; the provider did not refuse anything). An answer cut at
the output limit, a blocked answer, one with a function call, and every voice-turn
role are left as they were, and a second empty answer is returned as it is --
the analysis's own checks and its one automatic retry take it from there.

Contract-tested against fake SDK clients; nothing here is "live-tested".
"""

from __future__ import annotations

import asyncio
import logging
import unittest
from types import SimpleNamespace

from tests.lane_an2_support import (
    ENV,
    Clock,
    SequencedGemini,
    StatusError,
    call,
    client,
    gemini_reply,
)
from voiney_lab.model_providers import ModelProviderError, observe_provider_retries


class EmptyStopResendTests(unittest.TestCase):
    def test_an_empty_stop_is_sent_again_once_and_the_second_answer_is_the_reply(self) -> None:
        fake = SequencedGemini(gemini_reply(""), gemini_reply('{"ok": true}'))
        with Clock() as clock:
            reply = call(client(fake))
        self.assertEqual(reply.choices[0].message.content, '{"ok": true}')
        self.assertEqual(len(fake.requests), 2)
        self.assertEqual(fake.requests[0], fake.requests[1])
        self.assertEqual(clock.waits, [])

    def test_a_reply_of_thoughts_only_is_empty(self) -> None:
        fake = SequencedGemini(gemini_reply("", thought="reasoning only"), gemini_reply())
        with Clock():
            reply = call(client(fake))
        self.assertEqual(reply.choices[0].message.content, '{"ok": true}')
        self.assertEqual(len(fake.requests), 2)

    def test_a_reply_of_blanks_is_empty(self) -> None:
        fake = SequencedGemini(gemini_reply("  \n "), gemini_reply())
        with Clock():
            call(client(fake))
        self.assertEqual(len(fake.requests), 2)

    def test_a_second_empty_answer_is_returned_empty_not_sent_a_third_time(self) -> None:
        fake = SequencedGemini(gemini_reply(""), gemini_reply(""), gemini_reply())
        with Clock():
            reply = call(client(fake))
        self.assertEqual(reply.choices[0].message.content, "")
        self.assertEqual(len(fake.requests), 2)

    def test_an_answer_with_text_is_not_sent_again(self) -> None:
        fake = SequencedGemini(gemini_reply("text"), gemini_reply("other"))
        reply = call(client(fake))
        self.assertEqual(reply.choices[0].message.content, "text")
        self.assertEqual(len(fake.requests), 1)

    def test_an_empty_answer_with_another_ending_is_not_sent_again(self) -> None:
        # MAX_TOKENS is the answer cut at the output limit (the analysis has its
        # own retry for that); the rest are refusals a second send does not change.
        for finish in ("MAX_TOKENS", "SAFETY", "RECITATION", "OTHER", "PROHIBITED_CONTENT", None):
            with self.subTest(finish=finish):
                fake = SequencedGemini(gemini_reply("", finish=finish), gemini_reply())
                reply = call(client(fake))
                self.assertEqual(reply.choices[0].message.content, "")
                self.assertEqual(len(fake.requests), 1)

    def test_a_reply_without_a_candidate_is_not_sent_again(self) -> None:
        blocked = SimpleNamespace(
            model_version="gemini-test", candidates=[],
            usage_metadata=SimpleNamespace(
                prompt_token_count=1, candidates_token_count=0, thoughts_token_count=0,
                cached_content_token_count=0))
        fake = SequencedGemini(blocked, gemini_reply())
        reply = call(client(fake))
        self.assertEqual(reply.choices[0].message.content, "")
        self.assertEqual(len(fake.requests), 1)

    def test_a_function_call_is_not_an_empty_answer(self) -> None:
        part = SimpleNamespace(thought=False, text=None, function_call=SimpleNamespace(
            id=None, name="answer", args={"spoken": "x"}))
        fake = SequencedGemini(gemini_reply("", parts=[part]), gemini_reply())
        reply = call(client(fake))
        self.assertEqual(reply.choices[0].message.tool_calls[0].function.name, "answer")
        self.assertEqual(len(fake.requests), 1)

    def test_the_finish_reason_may_arrive_as_a_dotted_enum_name_or_a_string(self) -> None:
        for finish in ("FinishReason.STOP", "stop"):
            with self.subTest(finish=finish):
                reply = gemini_reply("")
                reply.candidates[0].finish_reason = finish  # a plain string, no .name
                fake = SequencedGemini(reply, gemini_reply())
                with Clock():
                    call(client(fake))
                self.assertEqual(len(fake.requests), 2)

    def test_the_asynchronous_client_sends_it_again_too(self) -> None:
        fake = SequencedGemini(gemini_reply(""), gemini_reply())
        reply = asyncio.run(call(client(fake, asynchronous=True)))
        self.assertEqual(reply.choices[0].message.content, '{"ok": true}')
        self.assertEqual(len(fake.requests), 2)


class RoleAndSwitchTests(unittest.TestCase):
    def test_the_background_roles_are_sent_again(self) -> None:
        for role in ("analysis", "translation", "report"):
            with self.subTest(role=role):
                fake = SequencedGemini(gemini_reply(""), gemini_reply())
                call(client(fake, role=role))
                self.assertEqual(len(fake.requests), 2)

    def test_a_voice_turn_role_is_not(self) -> None:
        for role in ("router", "answer", "supplemental"):
            with self.subTest(role=role):
                fake = SequencedGemini(gemini_reply(""), gemini_reply())
                reply = call(client(fake, role=role))
                self.assertEqual(reply.choices[0].message.content, "")
                self.assertEqual(len(fake.requests), 1)

    def test_zero_retries_turns_the_resend_off_too(self) -> None:
        fake = SequencedGemini(gemini_reply(""), gemini_reply())
        call(client(fake, environment={"VOINEY_LAB_PROVIDER_RETRY_MAX": "0"}))
        self.assertEqual(len(fake.requests), 1)

    def test_the_other_providers_replies_are_never_taken_for_empty_stops(self) -> None:
        from voiney_lab.model_providers import RoleModel, chat_client

        reply = SimpleNamespace(
            model="claude-test", content=[SimpleNamespace(type="text", text="")],
            usage=SimpleNamespace(input_tokens=1, output_tokens=0,
                                  cache_read_input_tokens=0, cache_creation_input_tokens=0))
        seen: list[dict] = []

        def create(**params):
            seen.append(params)
            return reply

        sdk = SimpleNamespace(messages=SimpleNamespace(create=create))
        anthropic = chat_client(
            RoleModel("analysis", "anthropic", "claude-sonnet-5-5", "low"),
            asynchronous=False, sdk_client=sdk, environment=ENV)
        answer = anthropic.chat.completions.create(model="claude-sonnet-5-5", messages=[
            {"role": "user", "content": "x"}])
        self.assertEqual(answer.choices[0].message.content, "")
        self.assertEqual(len(seen), 1)


class EmptyStopWithRefusalsTests(unittest.TestCase):
    def test_the_two_resends_are_counted_apart(self) -> None:
        fake = SequencedGemini(gemini_reply(""), StatusError(429), gemini_reply())
        with Clock() as clock:
            reply = call(client(fake))
        self.assertEqual(reply.choices[0].message.content, '{"ok": true}')
        self.assertEqual(len(fake.requests), 3)
        self.assertEqual(len(clock.waits), 1)

    def test_a_429_then_two_empty_answers_ends_with_the_empty_reply(self) -> None:
        fake = SequencedGemini(StatusError(429), gemini_reply(""), gemini_reply(""), gemini_reply())
        with Clock():
            reply = call(client(fake))
        self.assertEqual(reply.choices[0].message.content, "")
        self.assertEqual(len(fake.requests), 3)

    def test_a_refusal_after_an_empty_answer_still_gives_up_at_its_limit(self) -> None:
        fake = SequencedGemini(gemini_reply(""), *[StatusError(429)] * 4)
        with Clock(), self.assertRaises(ModelProviderError):
            call(client(fake))
        self.assertEqual(len(fake.requests), 1 + 1 + 3)


class RecordTests(unittest.TestCase):
    def test_each_empty_answer_is_reported_and_the_last_as_not_retried(self) -> None:
        fake = SequencedGemini(gemini_reply(""), gemini_reply(""))
        events = []
        with Clock(), observe_provider_retries(events.append):
            call(client(fake))
        self.assertEqual(
            [(e.reason, e.status_code, e.attempt, e.wait_seconds, e.will_retry) for e in events],
            [("empty_stop", None, 1, 0.0, True), ("empty_stop", None, 2, 0.0, False)])
        self.assertEqual((events[0].provider, events[0].role), ("google", "analysis"))

    def test_an_answer_that_is_fine_the_second_time_reports_one_event(self) -> None:
        fake = SequencedGemini(gemini_reply(""), gemini_reply())
        events = []
        with Clock(), observe_provider_retries(events.append):
            call(client(fake))
        self.assertEqual([e.reason for e in events], ["empty_stop"])

    def test_the_server_log_names_it_without_the_prompt(self) -> None:
        fake = SequencedGemini(gemini_reply(""), gemini_reply())
        with Clock(), self.assertLogs("voiney_lab.model_providers", level=logging.INFO) as logs:
            call(client(fake))
        text = "\n".join(logs.output)
        self.assertIn("reason=empty_stop", text)
        self.assertNotIn("PRIVATE", text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
