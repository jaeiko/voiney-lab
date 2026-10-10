"""A retry one reasoning level lower when the first answer was cut at the
output limit (lane AQ, decision 4).

Human decisions of 2026-10-10. DS-2 (2026-10-09, Gemini 3.8 Flash at "high")
measured 51 of 195 analysis calls cut at the 65,536 output-token limit with a
median 61k of those tokens spent thinking, and every automatic retry -- the
same request at the same setting (lane AN) -- was cut again. The live server
lost 12m47s on PMC8250384 that way at 02:01 the same night.

Decision 4: when the provider says the answer stopped at its output limit
(finish reason "length"; the Gemini adapter reports MAX_TOKENS so), the one
automatic retry asks one reasoning level lower (xhigh→high→medium→low; low
stays low). An answer that broke the structure for another reason is sent
again unchanged, as before. Where the finish reason is not known, nothing
changes.

The Gemini adapter's finish reason and the decision 5 prompt sentence have
their own test files (test_lane_aq_gemini_finish_reason, test_lane_aq_quote_instruction).
"""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests.test_protocol_catalog import analysis_draft, write_text_pdf
from voiney_lab.experiment_protocol_analysis import (
    OpenAICompatibleProtocolAnalysisModel,
    ProtocolAnalysisResponseError,
)
from voiney_lab.experiment_protocol_config import ProtocolPersistenceSettings
from voiney_lab.experiment_protocol_pdf import clear_protocol_pdf_cache
from voiney_lab.experiment_protocol_store import initialize_protocol_store
from voiney_lab.protocol_catalog import (
    TRUNCATED_FINISH_REASON,
    TRUNCATED_RETRY_REASONING,
    ProtocolCatalog,
)

PARSE = "voiney_lab.experiment_protocol_analysis.parse_protocol_analysis_response"
TEXT = "Protocol Alpha\nSection preparation\n1. Add solution.\nWear gloves."
CUT = '{"protocol": {"metadata": {"title": "Protocol Al'
WHOLE = '{"protocol": {}}'


class FakeClient:
    """An OpenAI-style client: each reply is (content, finish_reason)."""

    def __init__(self, *replies: tuple[str, str | None]) -> None:
        self.replies = list(replies)
        self.requests: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **request):
        self.requests.append(request)
        content, finish_reason = self.replies.pop(0)
        return SimpleNamespace(
            model=request["model"], usage=None,
            choices=[SimpleNamespace(
                index=0, finish_reason=finish_reason,
                message=SimpleNamespace(role="assistant", content=content, tool_calls=None),
            )],
        )


class _Case(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        clear_protocol_pdf_cache()
        self.settings = ProtocolPersistenceSettings(True, self.root / "catalog")
        self.store = initialize_protocol_store(self.settings)
        self.catalog = ProtocolCatalog(self.store)
        self.pdf = self.root / "alpha.pdf"
        write_text_pdf(self.pdf, TEXT, title="Protocol Alpha")
        self.protocol_id = self.catalog.register(
            self.pdf, source_filename="alpha.pdf", media_type="application/pdf",
        ).entry.protocol_id
        self.draft = analysis_draft(self.pdf, self.protocol_id, "Protocol Alpha")

    def tearDown(self) -> None:
        self.store.close()
        clear_protocol_pdf_cache()
        self.temp.cleanup()

    def analyze(self, client: FakeClient, reasoning: str | None, *parses, side_effect=None):
        """One analysis request through the real model adapter; the parser is
        the fake: it fails the first answer and returns the draft after."""

        model = OpenAICompatibleProtocolAnalysisModel(client, "gemini-3.8-flash", reasoning)
        analysis_id = "analysis-" + "a" * 32
        self.catalog.request_analysis(self.protocol_id, analysis_id)
        with patch(PARSE, side_effect=side_effect or list(parses)):
            try:
                self.catalog.analyze(self.protocol_id, model, analysis_id=analysis_id)
            except Exception as exc:  # noqa: BLE001 - the test looks at it
                return exc
        return None

    def retry_event(self):
        return next(
            event for event in self.store.list_events(self.protocol_id)
            if event.event_type == "protocol_analysis_retry_started")

    def status(self) -> dict:
        return self.catalog.analysis_run_status(self.protocol_id).public_dict()


def invalid_response() -> ProtocolAnalysisResponseError:
    return ProtocolAnalysisResponseError("Protocol analysis response was not valid JSON.")


class TruncatedFirstAnswerTests(_Case):
    def test_the_retry_asks_one_reasoning_level_lower(self) -> None:
        client = FakeClient((CUT, TRUNCATED_FINISH_REASON), (WHOLE, "stop"))
        error = self.analyze(client, "high", invalid_response(), self.draft)
        self.assertIsNone(error)
        self.assertEqual([r["reasoning_effort"] for r in client.requests], ["high", "medium"])
        # The same request otherwise: the same prompt, document and schema.
        first, second = client.requests
        self.assertEqual(first["messages"], second["messages"])
        self.assertEqual(first["response_format"], second["response_format"])
        self.assertEqual(first["model"], second["model"])
        self.assertEqual(
            [e.event_type for e in self.store.list_events(self.protocol_id)],
            ["protocol_registered", "protocol_analysis_requested", "protocol_analysis_started",
             "protocol_analysis_failed", "protocol_analysis_retry_started",
             "protocol_analysis_ready", "protocol_review_required"])

    def test_the_ledger_says_the_first_answer_was_cut_and_what_the_retry_asked(self) -> None:
        client = FakeClient((CUT, TRUNCATED_FINISH_REASON), (WHOLE, "stop"))
        self.analyze(client, "high", invalid_response(), self.draft)
        payload = self.retry_event().payload
        self.assertEqual(payload["reason_code"], "protocol_analysis_invalid_response")
        self.assertIs(payload["truncated"], True)
        self.assertEqual(payload["reasoning_effort"], "medium")
        self.assertEqual(payload["previous_reasoning_effort"], "high")
        self.assertEqual(payload["authority"], "automatic_invalid_response_retry")
        self.assertEqual(payload["attempt"], 1)
        failed = [e for e in self.store.list_events(self.protocol_id)
                  if e.event_type == "protocol_analysis_failed"]
        self.assertIs(failed[0].payload["truncated"], True)
        self.assertEqual(self.status()["automatic_retry"], {
            "attempt": 1, "limit": 1, "state": "passed",
            "reason_code": "protocol_analysis_invalid_response",
            "truncated": True, "reasoning_effort": "medium", "previous_reasoning_effort": "high",
        })

    def test_every_level_steps_down_once_and_the_lowest_stays(self) -> None:
        self.assertEqual(dict(TRUNCATED_RETRY_REASONING), {
            "xhigh": "high", "high": "medium", "medium": "low", "low": "low",
        })
        for first, second in (("xhigh", "high"), ("medium", "low"), ("low", "low")):
            with self.subTest(first=first):
                self.tearDown()
                self.setUp()
                client = FakeClient((CUT, TRUNCATED_FINISH_REASON), (WHOLE, "stop"))
                self.analyze(client, first, invalid_response(), self.draft)
                self.assertEqual([r["reasoning_effort"] for r in client.requests], [first, second])
                payload = self.retry_event().payload
                self.assertIs(payload["truncated"], True)
                self.assertEqual(payload["reasoning_effort"], second)

    def test_a_retry_cut_again_fails_and_says_so(self) -> None:
        client = FakeClient((CUT, TRUNCATED_FINISH_REASON), (CUT, TRUNCATED_FINISH_REASON))
        error = self.analyze(client, "high", invalid_response(), invalid_response())
        self.assertIsInstance(error, ProtocolAnalysisResponseError)
        self.assertEqual(len(client.requests), 2)
        status = self.status()
        self.assertEqual(status["failure_code"], "protocol_analysis_invalid_response")
        self.assertEqual(status["automatic_retry"]["state"], "failed")
        self.assertEqual(status["automatic_retry"]["reasoning_effort"], "medium")
        failed = [e for e in self.store.list_events(self.protocol_id)
                  if e.event_type == "protocol_analysis_failed"]
        self.assertEqual([e.payload.get("truncated") for e in failed], [True, True])
        line = self.catalog.pipeline_status(self.protocol_id)
        self.assertEqual((line["stage"], line["blocked"]), ("analysis", True))
        self.assertIn("출력 한도", line["message"])
        self.assertIn("high→medium", line["message"])
        self.assertIn("자동 재시도(1/1)", line["message"])
        self.assertIn("분석 다시 시도", line["action"])

    def test_the_progress_line_says_why_while_the_lower_retry_runs(self) -> None:
        seen: dict = {}
        client = FakeClient((CUT, TRUNCATED_FINISH_REASON), (WHOLE, "stop"))
        answers = iter([invalid_response()])

        def parse(*_args, **_kwargs):
            for error in answers:
                raise error
            seen["line"] = self.catalog.pipeline_status(self.protocol_id)
            seen["status"] = self.status()
            return self.draft

        self.analyze(client, "high", side_effect=parse)
        self.assertIn("다시 시도 중(1/1)", seen["line"]["message"])
        self.assertIn("출력 한도", seen["line"]["message"])
        self.assertIn("high→medium", seen["line"]["message"])
        self.assertEqual(seen["status"]["automatic_retry"]["state"], "in_progress")
        self.assertEqual(seen["status"]["automatic_retry"]["reasoning_effort"], "medium")

    def test_a_model_without_a_reasoning_level_is_sent_again_unchanged(self) -> None:
        client = FakeClient((CUT, TRUNCATED_FINISH_REASON), (WHOLE, "stop"))
        self.analyze(client, None, invalid_response(), self.draft)
        self.assertEqual([r.get("reasoning_effort") for r in client.requests], [None, None])
        payload = self.retry_event().payload
        self.assertIs(payload["truncated"], True)
        self.assertNotIn("reasoning_effort", payload)


class NotTruncatedTests(_Case):
    def test_an_answer_that_broke_the_structure_another_way_is_sent_again_unchanged(self) -> None:
        client = FakeClient((WHOLE, "stop"), (WHOLE, "stop"))
        error = self.analyze(client, "high", invalid_response(), self.draft)
        self.assertIsNone(error)
        self.assertEqual([r["reasoning_effort"] for r in client.requests], ["high", "high"])
        payload = self.retry_event().payload
        self.assertNotIn("truncated", payload)
        self.assertNotIn("reasoning_effort", payload)
        # The ledger and the status read exactly as lane AN wrote them.
        self.assertEqual(self.status()["automatic_retry"], {
            "attempt": 1, "limit": 1, "state": "passed",
            "reason_code": "protocol_analysis_invalid_response",
        })
        line = self.catalog.pipeline_status(self.protocol_id)
        self.assertNotIn("출력 한도", line["message"])

    def test_an_unknown_finish_reason_keeps_the_same_setting(self) -> None:
        for reason in (None, "", "other"):
            with self.subTest(reason=reason):
                self.tearDown()
                self.setUp()
                client = FakeClient((CUT, reason), (WHOLE, "stop"))
                self.analyze(client, "high", invalid_response(), self.draft)
                self.assertEqual([r["reasoning_effort"] for r in client.requests], ["high", "high"])
                self.assertNotIn("truncated", self.retry_event().payload)

    def test_a_model_that_is_not_the_adapter_is_passed_through_untouched(self) -> None:
        # A caller's own ProtocolAnalysisModel has no client to observe: the
        # retry is the same object, as before (lane AN's tests use a Mock).
        model = Mock()
        model.analyze.return_value = WHOLE
        analysis_id = "analysis-" + "b" * 32
        self.catalog.request_analysis(self.protocol_id, analysis_id)
        with patch("voiney_lab.protocol_catalog.analyze_protocol_extraction",
                   side_effect=[invalid_response(), self.draft]) as analyze:
            self.catalog.analyze(self.protocol_id, model, analysis_id=analysis_id)
        first, second = analyze.call_args_list
        self.assertIs(first.args[1], model)
        self.assertIs(second.args[1], model)
        self.assertNotIn("truncated", self.retry_event().payload)

    def test_the_adapter_model_is_not_changed_by_the_observation(self) -> None:
        client = FakeClient((WHOLE, "stop"))
        model = OpenAICompatibleProtocolAnalysisModel(client, "gemini-3.8-flash", "high")
        analysis_id = "analysis-" + "c" * 32
        self.catalog.request_analysis(self.protocol_id, analysis_id)
        with patch(PARSE, return_value=self.draft):
            self.catalog.analyze(self.protocol_id, model, analysis_id=analysis_id)
        self.assertIs(model.client, client)
        self.assertEqual(model, replace(model))
        self.assertEqual(len(client.requests), 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
