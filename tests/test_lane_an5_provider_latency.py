"""Every analysis and translation send says how long it took (lane AN5, decision 5).

Human decision 2026-10-20 (the recommended option): lane AN4 saw the
provider write 22 tokens a second in one half hour and 150-170 in the next,
with nothing on the server to tell the slow hour from a slow document. Each
send of an analysis or translation request now writes one server log line --
provider, role, model, how it ended, seconds, output tokens and tokens a
second -- and never a word of the request or the reply. The measurement
ledgers read the same record from the line (``provider_call``).

Contract-tested against fake SDK clients; nothing here is "live-tested".
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import unittest
from unittest import mock

from tests.lane_an2_support import (
    Clock,
    SequencedGemini,
    StatusError,
    call,
    client,
    gemini_reply,
)
from voiney_lab import model_providers
from voiney_lab.model_providers import ModelProviderError

LOGGER = "voiney_lab.model_providers"


class _Ticks:
    """The adapter's clock: each reading ``step`` seconds after the last."""

    def __init__(self, step: float) -> None:
        self._values = itertools.count(start=100.0, step=step)

    def __call__(self) -> float:
        return next(self._values)


def _lines(logs) -> list[logging.LogRecord]:
    return [record for record in logs.records if record.getMessage().startswith("provider.call ")]


class ProviderLatencyLineTests(unittest.TestCase):
    def test_an_analysis_send_writes_one_line_with_its_time_tokens_and_rate(self) -> None:
        reply = gemini_reply('{"ok": true}')
        reply.usage_metadata.candidates_token_count = 300
        reply.usage_metadata.thoughts_token_count = 100
        with mock.patch.object(model_providers, "_monotonic", _Ticks(8.0)), \
                self.assertLogs(LOGGER, logging.INFO) as logs:
            call(client(SequencedGemini(reply)))
        (line,) = _lines(logs)
        self.assertEqual(line.provider_call, {
            "provider": "google", "role": "analysis", "model": "gemini-3.8-flash",
            "status": "ok", "seconds": 8.0, "output_tokens": 400, "tokens_per_second": 50.0,
            "finish": "STOP",
        })
        self.assertEqual(
            line.getMessage(),
            "provider.call provider=google role=analysis model=gemini-3.8-flash status=ok "
            "seconds=8.00 output_tokens=400 tokens_per_second=50.0 finish=STOP")

    def test_a_translation_send_writes_its_line_too(self) -> None:
        with self.assertLogs(LOGGER, logging.INFO) as logs:
            call(client(SequencedGemini(gemini_reply()), role="translation"))
        (line,) = _lines(logs)
        self.assertEqual(line.provider_call["role"], "translation")

    def test_a_voice_turn_and_the_report_write_no_line(self) -> None:
        for role in ("router", "answer", "report"):
            with self.subTest(role=role):
                with self.assertLogs(LOGGER, logging.DEBUG) as logs:
                    logging.getLogger(LOGGER).debug("start")
                    call(client(SequencedGemini(gemini_reply()), role=role))
                self.assertEqual(_lines(logs), [])

    def test_each_send_of_a_request_sent_again_has_its_own_line(self) -> None:
        fake = SequencedGemini(StatusError(429), gemini_reply())
        with Clock(), self.assertLogs(LOGGER, logging.INFO) as logs:
            call(client(fake))
        first, second = _lines(logs)
        self.assertEqual(first.provider_call["status"], "rate_limited:429")
        self.assertIsNone(first.provider_call["output_tokens"])
        self.assertIsNone(first.provider_call["tokens_per_second"])
        self.assertEqual(second.provider_call["status"], "ok")

    def test_a_failed_send_says_how_it_ended_and_the_error_still_reaches_the_caller(self) -> None:
        with self.assertLogs(LOGGER, logging.INFO) as logs:
            with self.assertRaises(ModelProviderError):
                call(client(SequencedGemini(StatusError(400))))
        (line,) = _lines(logs)
        self.assertTrue(line.provider_call["status"].endswith(":400"))

    def test_no_word_of_the_request_or_the_reply_is_written(self) -> None:
        with self.assertLogs(LOGGER, logging.INFO) as logs:
            call(client(SequencedGemini(gemini_reply('{"secret": "REPLY WORDS"}'))))
        written = " ".join(
            f"{record.getMessage()} {getattr(record, 'provider_call', '')}" for record in logs.records)
        for words in ("PRIVATE PAGE TEXT", "STANDING INSTRUCTIONS", "REPLY WORDS", "secret"):
            self.assertNotIn(words, written)

    def test_a_send_that_took_no_measurable_time_has_no_rate(self) -> None:
        with mock.patch.object(model_providers, "_monotonic", _Ticks(0.0)), \
                self.assertLogs(LOGGER, logging.INFO) as logs:
            call(client(SequencedGemini(gemini_reply())))
        (line,) = _lines(logs)
        self.assertEqual(line.provider_call["seconds"], 0.0)
        self.assertIsNone(line.provider_call["tokens_per_second"])

    def test_the_asynchronous_client_writes_the_line_too(self) -> None:
        with self.assertLogs(LOGGER, logging.INFO) as logs:
            asyncio.run(call(client(SequencedGemini(gemini_reply()), asynchronous=True)))
        (line,) = _lines(logs)
        self.assertEqual(line.provider_call["status"], "ok")
        self.assertEqual(line.provider_call["output_tokens"], 5)


if __name__ == "__main__":
    unittest.main()
