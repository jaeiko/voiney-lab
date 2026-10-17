"""Lane AN3, decision C: a reply the safety filter blocked is sent once more.

Human decision 2026-10-16. Lane GM measured that the same request is blocked
on one send and answered on another (mfds_foodcode piece 22 flipped between
the two under one setting; pieces 13 and 19 were blocked in lane AN2 and not
in lane GM). A blocked attempt costs no tokens and about 5-10 s. So a
background role's request (analysis, translation, report) that comes back
``blocked`` is sent once more, at once -- no wait, the provider refused
nothing that waiting changes -- and a second block is raised as before, which
the analysis turns into lane RV2's "분석 못 한 쪽" item. Voice-turn roles are
left as they were (a blocked reply there is the empty answer it always was).

Contract-tested against fake SDK clients; nothing here is "live-tested".
"""

from __future__ import annotations

import asyncio
import unittest

from tests.lane_an2_support import ENV, Clock, SequencedGemini, StatusError, call, client, gemini_reply
from tests.test_lane_gm_safety import prompt_blocked
from tests.test_lane_rv_review_pending import _CatalogCase
from voiney_lab.experiment_protocol_analysis import (
    OpenAICompatibleProtocolAnalysisModel,
    ProtocolAnalysisModelError,
    safety_block_provider,
)
from voiney_lab.model_providers import ModelProviderError, observe_provider_retries


class TheAdapterSendsABlockOnceMoreTests(unittest.TestCase):
    def test_a_block_answered_on_the_second_send_returns_that_answer_without_waiting(self) -> None:
        for role in ("analysis", "translation", "report"):
            with self.subTest(role=role):
                fake = SequencedGemini(prompt_blocked("SAFETY"), gemini_reply("answer"))
                with Clock() as clock:
                    reply = call(client(fake, role=role))
                self.assertEqual(reply.choices[0].message.content, "answer")
                self.assertEqual(len(fake.requests), 2)
                self.assertEqual(clock.waits, [])
                self.assertEqual(fake.requests[0], fake.requests[1])

    def test_a_second_block_is_raised_and_not_sent_a_third_time(self) -> None:
        fake = SequencedGemini(prompt_blocked("SAFETY"), prompt_blocked("SAFETY"), gemini_reply())
        with Clock() as clock, self.assertRaises(ModelProviderError) as caught:
            call(client(fake))
        self.assertEqual(caught.exception.kind, "blocked")
        self.assertEqual(caught.exception.block_reason, "SAFETY")
        self.assertEqual(len(fake.requests), 2)
        self.assertEqual(clock.waits, [])

    def test_a_candidate_that_ended_in_the_filter_is_sent_once_more_too(self) -> None:
        fake = SequencedGemini(gemini_reply("", finish="SAFETY"), gemini_reply("answer"))
        with Clock():
            reply = call(client(fake))
        self.assertEqual(reply.choices[0].message.content, "answer")
        self.assertEqual(len(fake.requests), 2)

    def test_the_resend_is_reported_to_the_observer_as_a_block(self) -> None:
        events = []
        fake = SequencedGemini(prompt_blocked("SAFETY"), prompt_blocked("SAFETY"))
        with Clock(), observe_provider_retries(events.append), self.assertRaises(ModelProviderError):
            call(client(fake))
        self.assertEqual(
            [(event.reason, event.attempt, event.will_retry, event.wait_seconds) for event in events],
            [("blocked", 1, True, 0.0), ("blocked", 2, False, 0.0)])
        self.assertEqual([event.status_code for event in events], [None, None])

    def test_the_asynchronous_client_sends_it_once_more_too(self) -> None:
        fake = SequencedGemini(prompt_blocked("SAFETY"), gemini_reply("answer"))
        with Clock() as clock:
            reply = asyncio.run(call(client(fake, asynchronous=True)))
        self.assertEqual(reply.choices[0].message.content, "answer")
        self.assertEqual(len(fake.requests), 2)
        self.assertEqual(clock.waits, [])

    def test_a_voice_turn_role_is_not_sent_again(self) -> None:
        for role in ("router", "answer", "supplemental"):
            with self.subTest(role=role):
                fake = SequencedGemini(prompt_blocked(), gemini_reply("answer"))
                reply = call(client(fake, role=role))
                self.assertEqual(reply.choices[0].message.content, "")
                self.assertEqual(len(fake.requests), 1)

    def test_turning_resends_off_turns_this_one_off_too(self) -> None:
        fake = SequencedGemini(prompt_blocked("SAFETY"), gemini_reply("answer"))
        with self.assertRaises(ModelProviderError) as caught:
            call(client(fake, environment={**ENV, "VOINEY_LAB_PROVIDER_RETRY_MAX": "0"}))
        self.assertEqual(caught.exception.kind, "blocked")
        self.assertEqual(len(fake.requests), 1)

    def test_the_block_resend_is_its_own_and_leaves_the_429_resends_alone(self) -> None:
        # One resend for a block, whatever 429s came before or after it.
        fake = SequencedGemini(
            StatusError(429), prompt_blocked("SAFETY"), StatusError(429), gemini_reply("answer"))
        with Clock():
            reply = call(client(fake))
        self.assertEqual(reply.choices[0].message.content, "answer")
        self.assertEqual(len(fake.requests), 4)
        fake = SequencedGemini(prompt_blocked("SAFETY"), StatusError(429), prompt_blocked("SAFETY"),
                               gemini_reply("answer"))
        with Clock(), self.assertRaises(ModelProviderError) as caught:
            call(client(fake))
        self.assertEqual(caught.exception.kind, "blocked")
        self.assertEqual(len(fake.requests), 3)


class TheAnalysisSeesOnlyWhatTheResendLeavesTests(_CatalogCase):
    def model(self, *outcomes) -> tuple[OpenAICompatibleProtocolAnalysisModel, SequencedGemini]:
        fake = SequencedGemini(*outcomes)
        return OpenAICompatibleProtocolAnalysisModel(
            client(fake), "gemini-3.8-flash", "low"), fake

    def test_a_block_the_resend_gets_through_is_an_analysis_like_any_other(self) -> None:
        model, fake = self.model(prompt_blocked("SAFETY"), gemini_reply(self.answer()))
        self.catalog.request_analysis(self.protocol_id, "analysis-" + "a" * 32)
        with Clock():
            entry = self.catalog.analyze(self.protocol_id, model, analysis_id="analysis-" + "a" * 32)
        self.assertEqual(len(fake.requests), 2)
        self.assertTrue(entry.available_for_execution)
        self.assertEqual(self.events("protocol_analysis_failed"), [])

    def test_a_block_that_stays_fails_the_analysis_by_name_after_two_sends(self) -> None:
        model, fake = self.model(prompt_blocked("SAFETY"), prompt_blocked("SAFETY"),
                                 gemini_reply(self.answer()))
        self.catalog.request_analysis(self.protocol_id, "analysis-" + "a" * 32)
        with Clock(), self.assertRaises(ProtocolAnalysisModelError) as caught:
            self.catalog.analyze(self.protocol_id, model, analysis_id="analysis-" + "a" * 32)
        self.assertEqual(safety_block_provider(caught.exception), "google")
        self.assertEqual(len(fake.requests), 2)
        (failed,) = self.events("protocol_analysis_failed")
        self.assertEqual(failed["failure_code"], "protocol_analysis_blocked")


if __name__ == "__main__":
    unittest.main()
