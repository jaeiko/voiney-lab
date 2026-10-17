"""Lane GM, decision 3 (b): what the Google adapter does with a Gemini 4 request.

Measured live on 2026-10-16 (gemini-4-argon, Agent Platform express, one tiny
call per setting; the contract below is what the adapter already did, so these
tests pin it and change no code):

* the model string reaches ``generate_content`` as written -- on this key path
  the model is served at location ``us`` only (the default us-central1 answers
  404), which a full resource name selects: ``projects/<n>/locations/us/
  publishers/google/models/gemini-4-argon``;
* Gemini 4 takes thinking levels low, medium and high only (minimal and a
  thinking budget answer 400), which is what the adapter sends: "none" asks for
  low, "xhigh" and "max" for high, and no thinking budget is ever sent;
* an answer cut at the output limit ends MAX_TOKENS (read as "length"), and the
  reply's thought tokens are billed as output (usage counts candidates plus
  thoughts), as for Gemini 3.x.

Contract-tested against fake SDK clients; nothing here is "live-tested" except
what the lines above say was measured.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from tests.lane_an2_support import SequencedGemini, gemini_reply
from voiney_lab.model_providers import RoleModel, chat_client

RESOURCE_MODEL = "projects/123/locations/us/publishers/google/models/gemini-4-argon"
MESSAGES = [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}]


def send(fake: SequencedGemini, reasoning: str | None, model: str = RESOURCE_MODEL):
    client = chat_client(
        RoleModel("analysis", "google", model, reasoning), asynchronous=False,
        sdk_client=fake, environment={},
    )
    return client.chat.completions.create(model=model, messages=MESSAGES)


class Gemini4RequestTests(unittest.TestCase):
    def test_a_full_resource_name_reaches_the_service_as_written(self) -> None:
        fake = SequencedGemini(gemini_reply())
        send(fake, "low")
        self.assertEqual(fake.requests[0]["model"], RESOURCE_MODEL)

    def test_the_short_id_is_sent_as_written_too(self) -> None:
        fake = SequencedGemini(gemini_reply())
        send(fake, "low", model="gemini-4-argon")
        self.assertEqual(fake.requests[0]["model"], "gemini-4-argon")

    def test_the_thinking_levels_gemini_4_takes_are_sent_as_levels(self) -> None:
        for reasoning, level in (
            ("low", "low"), ("medium", "medium"), ("high", "high"),
            ("none", "low"), ("xhigh", "high"), ("max", "high"),
        ):
            with self.subTest(reasoning=reasoning):
                fake = SequencedGemini(gemini_reply())
                send(fake, reasoning)
                self.assertEqual(fake.requests[0]["config"]["thinking_config"], {"thinking_level": level})

    def test_no_thinking_budget_and_never_minimal_is_sent(self) -> None:
        for reasoning in (None, "none", "low", "medium", "high", "xhigh"):
            with self.subTest(reasoning=reasoning):
                fake = SequencedGemini(gemini_reply())
                send(fake, reasoning)
                config = fake.requests[0]["config"]
                thinking = config.get("thinking_config") or {}
                self.assertNotIn("thinking_budget", thinking)
                self.assertNotEqual(str(thinking.get("thinking_level", "")).lower(), "minimal")


class Gemini4ReplyTests(unittest.TestCase):
    def test_an_answer_cut_at_the_output_limit_reads_as_length(self) -> None:
        reply = send(SequencedGemini(gemini_reply("{", finish="MAX_TOKENS")), "low")
        self.assertEqual(reply.choices[0].finish_reason, "length")
        self.assertEqual(reply.choices[0].message.content, "{")

    def test_thought_tokens_are_billed_as_output(self) -> None:
        response = gemini_reply()
        response.usage_metadata = SimpleNamespace(
            prompt_token_count=36, candidates_token_count=5, thoughts_token_count=63,
            cached_content_token_count=0)
        usage = send(SequencedGemini(response), "high").usage
        self.assertEqual(usage.prompt_tokens, 36)
        self.assertEqual(usage.completion_tokens, 68)
        self.assertEqual(usage.reasoning_tokens, 63)

    def test_a_reply_without_thought_tokens_counts_the_answer_alone(self) -> None:
        response = gemini_reply()
        response.usage_metadata = SimpleNamespace(
            prompt_token_count=36, candidates_token_count=5, thoughts_token_count=None,
            cached_content_token_count=None)
        usage = send(SequencedGemini(response), "low").usage
        self.assertEqual(usage.completion_tokens, 5)


if __name__ == "__main__":
    unittest.main()
