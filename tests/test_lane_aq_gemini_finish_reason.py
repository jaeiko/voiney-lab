"""The Gemini adapter reports an answer cut at max_output_tokens as finish
reason "length" (lane AQ, decision 4, 2026-10-10).

Before this the adapter said "stop" for every Gemini reply, so a reply cut at
the output limit (Gemini's MAX_TOKENS; DS-2 measured 51 of 195 analysis calls
so) read the same as one the model finished, and the analysis retry could not
tell them apart. Nothing else about the reply changes: the text, the tool
calls, the usage, and "stop" for every other finish reason.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from tests.test_model_providers import FakeGemini, _role, gemini_call, gemini_text
from voiney_lab.model_providers import chat_client

CUT = '{"protocol": {"metadata": {"title": "Protocol Al'
WHOLE = '{"protocol": {}}'


class GeminiFinishReasonTests(unittest.TestCase):
    @staticmethod
    def response(finish_reason, *parts):
        return SimpleNamespace(
            model_version="gemini-test",
            candidates=[SimpleNamespace(
                content=SimpleNamespace(parts=list(parts)), finish_reason=finish_reason)],
            usage_metadata=SimpleNamespace(
                prompt_token_count=3200, candidates_token_count=4897,
                thoughts_token_count=61180, cached_content_token_count=0),
        )

    def reply(self, finish_reason, *parts, reasoning: str = "high"):
        fake = FakeGemini(reply=self.response(finish_reason, *parts))
        client = chat_client(
            _role("google", "gemini-3.8-flash", reasoning, role="analysis"),
            asynchronous=False, sdk_client=fake)
        return client.chat.completions.create(
            model="gemini-3.8-flash",
            messages=[{"role": "system", "content": "S"}, {"role": "user", "content": "D"}],
            max_completion_tokens=60_000, reasoning_effort=reasoning)

    def test_max_tokens_as_the_sdk_enum_reads_as_length(self) -> None:
        reply = self.reply(SimpleNamespace(name="MAX_TOKENS"), gemini_text(CUT))
        self.assertEqual(reply.choices[0].finish_reason, "length")
        # The cut text and the usage are handed back as they were.
        self.assertEqual(reply.choices[0].message.content, CUT)
        self.assertEqual(reply.usage.completion_tokens, 4897 + 61180)
        self.assertEqual(reply.usage.reasoning_tokens, 61180)

    def test_max_tokens_as_a_string_reads_as_length(self) -> None:
        for raw in ("MAX_TOKENS", "FinishReason.MAX_TOKENS"):
            with self.subTest(raw=raw):
                self.assertEqual(self.reply(raw, gemini_text(CUT)).choices[0].finish_reason, "length")

    def test_every_other_finish_reason_reads_as_stop_as_before(self) -> None:
        for raw in (SimpleNamespace(name="STOP"), "STOP", None, SimpleNamespace(name="SAFETY"), 3):
            with self.subTest(raw=raw):
                self.assertEqual(self.reply(raw, gemini_text(WHOLE)).choices[0].finish_reason, "stop")

    def test_a_reply_without_a_candidate_reads_as_stop(self) -> None:
        fake = FakeGemini(reply=SimpleNamespace(model_version="gemini-test", candidates=[], usage_metadata=None))
        client = chat_client(_role("google", "gemini-3.8-flash", "high", role="analysis"),
                             asynchronous=False, sdk_client=fake)
        reply = client.chat.completions.create(
            model="gemini-3.8-flash", messages=[{"role": "user", "content": "D"}])
        self.assertEqual(reply.choices[0].finish_reason, "stop")
        self.assertEqual(reply.choices[0].message.content, "")

    def test_a_tool_call_still_reads_as_tool_calls(self) -> None:
        reply = self.reply(SimpleNamespace(name="STOP"), gemini_call("record_log", {"text": "x"}))
        self.assertEqual(reply.choices[0].finish_reason, "tool_calls")
        reply = self.reply(SimpleNamespace(name="MAX_TOKENS"), gemini_call("record_log", {"text": "x"}))
        self.assertEqual(reply.choices[0].finish_reason, "tool_calls")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
