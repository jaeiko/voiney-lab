"""Lane HEAR2, decisions 1-2 (2026-10-26): a speech-to-text provider that fails is heard about.

The person's test: from 03:13:02 every one of 44 speech-to-text requests
failed (ElevenLabs answered 401 ``quota_exceeded``: no credits left), and
the person saw "요청 이해 중" turn back into "듣는 중" with no answer, over
and over, until they stopped the test. The server logged "voice turn
failed" with a traceback and sent the screen a turn ``error`` the new screen
shows as a small line ("방금 말을 처리하지 못했어요. 다시 말해 주세요.") --
wrong advice during an outage -- and said nothing aloud.

Now a provider failure is one more dropped sound (``speech.dropped``,
reason ``stt_failed``) with a bounded code and plain words for the screen,
said aloud once per outage ("받아쓰기 서비스가 답하지 않아요. 관리자에게 알려
주세요."), recorded as a metric by its code, and never a traceback or the
provider's message. A response that comes back ends the outage: the next
failure is said again.
"""

from __future__ import annotations

import unittest

import requests

from tests.test_lane_hear_drops import DropHarness, utterance
from voiney_lab.voice_providers import SpeechProviderError

QUOTA = SpeechProviderError(
    'elevenlabs STT failed (401): {"detail":{"type":"invalid_request","code":"quota_exceeded",'
    '"message":"This request exceeds your quota of 10000. You have 0 credits remaining"}}',
    status=401, code="quota_exceeded",
)


class SttFailureTests(DropHarness):
    def test_a_failed_provider_is_shown_recorded_and_said_once_per_outage(self) -> None:
        session, clock = self.listener()
        metrics: list[dict] = []
        outcomes = iter([QUOTA, QUOTA, "다음", QUOTA])

        def heard(pcm):
            outcome = next(outcomes, QUOTA)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        sent = self.run_socket(session, clock, [
            {"bytes": utterance(0.1)}, {"sleep": 0.3},
            {"advance": 5.0}, {"bytes": utterance(0.1)}, {"sleep": 0.3},
            {"advance": 5.0}, {"bytes": utterance(0.1)}, {"sleep": 0.8},
            {"advance": 5.0}, {"bytes": utterance(0.1)}, {"sleep": 0.5},
        ], heard=heard, metrics=metrics)
        dropped = [m for m in self.of(sent, "speech.dropped") if m["reason"] == "stt_failed"]
        self.assertEqual(len(dropped), 3, [m["type"] for m in sent])
        for message in dropped:
            self.assertEqual(message["code"], "quota_exceeded")
            self.assertEqual(message["notice"], "받아쓰기 서비스가 답하지 않아요")
            self.assertIn("한도", message["reason_ko"])
            self.assertGreater(message["duration_ms"], 0)
        said = [m for m in self.of(sent, "server.words") if m["words_kind"] == "stt_failure"]
        self.assertEqual(
            [m["text"] for m in said],
            ["받아쓰기 서비스가 답하지 않아요. 관리자에게 알려 주세요."] * 2,
            "once for the first outage, once again after a response came back")
        self.assertEqual(
            [m for m in self.of(sent, "error") if m.get("message") == "voice turn processing failed"], [],
            "a provider failure is not a turn processing failure")
        codes = [m["dimensions"].get("reason_code") for m in metrics if m.get("metric_name") == "speech_dropped"]
        self.assertEqual(codes.count("stt_failed"), 3)
        self.assertIn("speech.dropped reason=stt_failed", self.logs)
        self.assertIn("code=quota_exceeded status=401", self.logs)
        self.assertNotIn("credits", self.logs, "the provider's message is never logged")
        self.assertNotIn("Traceback", self.logs)

    def test_a_timeout_and_an_unreachable_provider_are_named(self) -> None:
        for raised, code in ((requests.Timeout("slow"), "timeout"), (requests.ConnectionError("down"), "unreachable")):
            with self.subTest(code=code):
                session, clock = self.listener()

                def heard(pcm, raised=raised):
                    raise raised

                sent = self.run_socket(session, clock, [{"bytes": utterance(0.1)}, {"sleep": 0.3}], heard=heard)
                dropped = [m for m in self.of(sent, "speech.dropped") if m["reason"] == "stt_failed"]
                self.assertEqual([m["code"] for m in dropped], [code])


if __name__ == "__main__":
    unittest.main()
