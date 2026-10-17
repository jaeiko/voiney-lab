"""Lane GM, decisions 1 and 2: Gemini's safety filter, and what a block says.

Decision 1: the analysis, translation and report requests to Gemini carry
``safety_settings`` at the loosest value the service takes for every category
it lets a caller adjust. ``VOINEY_LAB_GEMINI_SAFETY`` is ``relaxed`` (default),
``default`` (send none: the service's own) or ``strict`` (the way up). Voice-turn
roles are left as they were.

Decision 2: a reply the filter blocked (a prompt block with candidates 0, or a
candidate that ended SAFETY with nothing written) raises a ``ModelProviderError``
of kind ``blocked`` that carries the codes -- the block reason, the finish
reason and the categories -- and never the prompt or the reply.

Contract-tested against fake SDK clients; nothing here is "live-tested".
"""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from tests.lane_an2_support import SequencedGemini, call, client, gemini_reply
from voiney_lab import model_providers
from voiney_lab.model_providers import ModelProviderError

CATEGORIES = {
    "HARM_CATEGORY_HARASSMENT",
    "HARM_CATEGORY_HATE_SPEECH",
    "HARM_CATEGORY_SEXUALLY_EXPLICIT",
    "HARM_CATEGORY_DANGEROUS_CONTENT",
    "HARM_CATEGORY_CIVIC_INTEGRITY",
    "HARM_CATEGORY_JAILBREAK",
}
SECRET = "PRIVATE PAGE TEXT"


def safety_of(fake: SequencedGemini) -> list[dict] | None:
    return fake.requests[0]["config"].get("safety_settings")


def setting(value: str | None) -> dict:
    env = {}
    if value is not None:
        env["VOINEY_LAB_GEMINI_SAFETY"] = value
    return env


def prompt_blocked(reason: str = "SAFETY", ratings: list | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        model_version="gemini-test", candidates=[],
        prompt_feedback=SimpleNamespace(
            block_reason=SimpleNamespace(name=reason),
            block_reason_message=f"The prompt is blocked: {SECRET}", safety_ratings=ratings),
        usage_metadata=SimpleNamespace(
            prompt_token_count=0, candidates_token_count=None, thoughts_token_count=None,
            cached_content_token_count=None))


def rating(category: str, blocked: bool) -> SimpleNamespace:
    return SimpleNamespace(category=SimpleNamespace(name=category), blocked=blocked)


class SafetySettingsTests(unittest.TestCase):
    def test_relaxed_is_the_default_and_covers_every_adjustable_category(self) -> None:
        fake = SequencedGemini(gemini_reply())
        call(client(fake, environment=setting(None)))
        sent = safety_of(fake)
        self.assertEqual({item["category"] for item in sent}, CATEGORIES)
        self.assertEqual({item["threshold"] for item in sent}, {"OFF"})

    def test_relaxed_by_name_is_the_same(self) -> None:
        fake = SequencedGemini(gemini_reply())
        call(client(fake, environment=setting("relaxed")))
        self.assertEqual({item["threshold"] for item in safety_of(fake)}, {"OFF"})

    def test_default_sends_none_so_the_services_own_applies(self) -> None:
        fake = SequencedGemini(gemini_reply())
        call(client(fake, environment=setting("default")))
        self.assertNotIn("safety_settings", fake.requests[0]["config"])

    def test_strict_raises_every_category_to_the_lowest_block_threshold(self) -> None:
        fake = SequencedGemini(gemini_reply())
        call(client(fake, environment=setting("strict")))
        sent = safety_of(fake)
        self.assertEqual({item["category"] for item in sent}, CATEGORIES)
        self.assertEqual({item["threshold"] for item in sent}, {"BLOCK_LOW_AND_ABOVE"})

    def test_the_value_is_read_without_case_or_padding(self) -> None:
        fake = SequencedGemini(gemini_reply())
        call(client(fake, environment=setting("  Strict ")))
        self.assertEqual({item["threshold"] for item in safety_of(fake)}, {"BLOCK_LOW_AND_ABOVE"})

    def test_an_empty_value_is_the_default_level(self) -> None:
        fake = SequencedGemini(gemini_reply())
        call(client(fake, environment=setting("")))
        self.assertEqual({item["threshold"] for item in safety_of(fake)}, {"OFF"})

    def test_a_value_that_is_not_a_level_is_refused_naming_the_setting(self) -> None:
        # A typo for "strict" must not fall back to the loosest level in silence.
        with self.assertRaises(ValueError) as caught:
            client(SequencedGemini(), environment=setting("stricter"))
        self.assertIn("VOINEY_LAB_GEMINI_SAFETY", str(caught.exception))

    def test_the_background_roles_carry_it(self) -> None:
        for role in ("analysis", "translation", "report"):
            with self.subTest(role=role):
                fake = SequencedGemini(gemini_reply())
                call(client(fake, role=role))
                self.assertEqual({item["category"] for item in safety_of(fake)}, CATEGORIES)

    def test_a_voice_turn_role_does_not(self) -> None:
        for role in ("router", "answer", "supplemental"):
            for level in (None, "relaxed", "strict"):
                with self.subTest(role=role, level=level):
                    fake = SequencedGemini(gemini_reply())
                    call(client(fake, role=role, environment=setting(level)))
                    self.assertNotIn("safety_settings", fake.requests[0]["config"])

    def test_the_asynchronous_client_carries_it_too(self) -> None:
        fake = SequencedGemini(gemini_reply())
        asyncio.run(call(client(fake, asynchronous=True)))
        self.assertEqual({item["threshold"] for item in safety_of(fake)}, {"OFF"})

    def test_the_settings_are_kept_on_a_resend(self) -> None:
        fake = SequencedGemini(gemini_reply(""), gemini_reply())
        call(client(fake))
        self.assertEqual(len(fake.requests), 2)
        self.assertEqual(safety_of(fake), fake.requests[1]["config"]["safety_settings"])

    def test_the_levels_and_the_setting_name_are_published(self) -> None:
        self.assertEqual(model_providers.GEMINI_SAFETY_SETTING, "VOINEY_LAB_GEMINI_SAFETY")
        self.assertEqual(model_providers.GEMINI_SAFETY_LEVELS, ("relaxed", "default", "strict"))
        self.assertEqual(model_providers.DEFAULT_GEMINI_SAFETY, "relaxed")


class BlockedReplyTests(unittest.TestCase):
    def blocked_error(self, reply, **options) -> ModelProviderError:
        # Lane AN3, decision C: a block is sent once more, then raised.
        fake = SequencedGemini(reply, reply, gemini_reply())
        with self.assertRaises(ModelProviderError) as caught:
            call(client(fake, **options))
        self.assertEqual(len(fake.requests), 2, "a block is sent once more, then raised")
        return caught.exception

    def test_a_prompt_block_says_the_filter_and_the_reason_code(self) -> None:
        error = self.blocked_error(prompt_blocked("SAFETY"))
        self.assertEqual(error.kind, "blocked")
        self.assertEqual(error.provider, "google")
        self.assertEqual(error.block_reason, "SAFETY")
        self.assertIsNone(error.status_code)
        self.assertEqual(str(error), "구글 안전 필터에 막힘 · SAFETY")

    def test_every_prompt_block_reason_is_carried_as_its_code(self) -> None:
        for reason in ("SAFETY", "OTHER", "BLOCKLIST", "PROHIBITED_CONTENT", "IMAGE_SAFETY", "JAILBREAK"):
            with self.subTest(reason=reason):
                error = self.blocked_error(prompt_blocked(reason))
                self.assertEqual(error.block_reason, reason)
                self.assertIn(reason, str(error))

    def test_the_blocked_categories_are_carried_as_codes(self) -> None:
        reply = prompt_blocked("SAFETY", [
            rating("HARM_CATEGORY_DANGEROUS_CONTENT", True),
            rating("HARM_CATEGORY_HARASSMENT", False),
            rating("HARM_CATEGORY_HATE_SPEECH", True),
        ])
        error = self.blocked_error(reply)
        self.assertEqual(error.block_categories, ("HARM_CATEGORY_DANGEROUS_CONTENT", "HARM_CATEGORY_HATE_SPEECH"))
        self.assertEqual(
            str(error),
            "구글 안전 필터에 막힘 · SAFETY · HARM_CATEGORY_DANGEROUS_CONTENT, HARM_CATEGORY_HATE_SPEECH")

    def test_no_category_is_listed_when_the_service_names_none(self) -> None:
        error = self.blocked_error(prompt_blocked("SAFETY"))
        self.assertEqual(error.block_categories, ())

    def test_a_candidate_that_ended_in_the_filter_with_nothing_written(self) -> None:
        for finish in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII"):
            with self.subTest(finish=finish):
                reply = gemini_reply("", finish=finish)
                reply.candidates[0].safety_ratings = [rating("HARM_CATEGORY_DANGEROUS_CONTENT", True)]
                error = self.blocked_error(reply)
                self.assertEqual(error.kind, "blocked")
                self.assertEqual(error.finish_reason, finish)
                self.assertIsNone(error.block_reason)
                self.assertEqual(error.block_categories, ("HARM_CATEGORY_DANGEROUS_CONTENT",))
                self.assertEqual(
                    str(error), f"구글 안전 필터에 막힘 · {finish} · HARM_CATEGORY_DANGEROUS_CONTENT")

    def test_neither_the_prompt_nor_the_services_own_sentence_is_in_the_error(self) -> None:
        error = self.blocked_error(prompt_blocked("SAFETY"))
        text = f"{error} {error.args} {vars(error)}"
        self.assertNotIn(SECRET, text)
        self.assertNotIn("The prompt is blocked", text)

    def test_the_asynchronous_client_raises_it_too(self) -> None:
        fake = SequencedGemini(prompt_blocked("SAFETY"), prompt_blocked("SAFETY"), gemini_reply())
        with self.assertRaises(ModelProviderError) as caught:
            asyncio.run(call(client(fake, asynchronous=True)))
        self.assertEqual(caught.exception.kind, "blocked")
        self.assertEqual(len(fake.requests), 2)  # sent once more (lane AN3, decision C)

    def test_the_background_roles_raise_it(self) -> None:
        for role in ("analysis", "translation", "report"):
            with self.subTest(role=role):
                self.assertEqual(self.blocked_error(prompt_blocked(), role=role).kind, "blocked")

    def test_a_voice_turn_role_still_gets_the_empty_reply(self) -> None:
        for role in ("router", "answer", "supplemental"):
            with self.subTest(role=role):
                fake = SequencedGemini(prompt_blocked(), gemini_reply())
                reply = call(client(fake, role=role))
                self.assertEqual(reply.choices[0].message.content, "")
                self.assertEqual(len(fake.requests), 1)

    def test_a_reply_with_text_is_never_a_block_even_when_it_ended_in_the_filter(self) -> None:
        reply = gemini_reply("partial", finish="SAFETY")
        fake = SequencedGemini(reply)
        out = call(client(fake))
        self.assertEqual(out.choices[0].message.content, "partial")
        self.assertEqual(out.choices[0].finish_reason, "stop")

    def test_a_reply_with_a_function_call_is_never_a_block(self) -> None:
        part = SimpleNamespace(thought=False, text=None, function_call=SimpleNamespace(
            id=None, name="answer", args={}))
        out = call(client(SequencedGemini(gemini_reply("", finish="SAFETY", parts=[part]))))
        self.assertEqual(out.choices[0].message.tool_calls[0].function.name, "answer")

    def test_endings_that_are_not_the_filter_are_left_as_they_were(self) -> None:
        for finish in ("RECITATION", "OTHER", "MAX_TOKENS", "LANGUAGE", None):
            with self.subTest(finish=finish):
                out = call(client(SequencedGemini(gemini_reply("", finish=finish))))
                self.assertEqual(out.choices[0].message.content, "")

    def test_no_candidate_and_no_prompt_feedback_is_left_as_it_was(self) -> None:
        reply = SimpleNamespace(
            model_version="gemini-test", candidates=[],
            usage_metadata=SimpleNamespace(
                prompt_token_count=1, candidates_token_count=0, thoughts_token_count=0,
                cached_content_token_count=0))
        out = call(client(SequencedGemini(reply)))
        self.assertEqual(out.choices[0].message.content, "")

    def test_an_unspecified_block_reason_is_not_a_block(self) -> None:
        reply = prompt_blocked("BLOCKED_REASON_UNSPECIFIED")
        out = call(client(SequencedGemini(reply)))
        self.assertEqual(out.choices[0].message.content, "")

    def test_the_analysis_wraps_it_and_keeps_the_codes_in_the_chain(self) -> None:
        from voiney_lab import experiment_protocol_analysis as epa

        blocked = prompt_blocked("SAFETY", [rating("HARM_CATEGORY_DANGEROUS_CONTENT", True)])
        fake = SequencedGemini(blocked, blocked)  # sent once more (lane AN3, decision C)
        model = epa.OpenAICompatibleProtocolAnalysisModel(
            client=client(fake), model="gemini-3.8-flash", reasoning_effort="low")
        with self.assertRaises(epa.ProtocolAnalysisModelError) as caught:
            model.analyze(
                system_prompt="S", input_json="{}", response_schema=epa.ANALYSIS_PROVIDER_RESPONSE_SCHEMA)
        cause = caught.exception.__cause__
        self.assertIsInstance(cause, ModelProviderError)
        self.assertEqual(cause.block_reason, "SAFETY")
        self.assertNotIn("no structured response", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
