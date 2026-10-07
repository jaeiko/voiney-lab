"""One automatic retry of an analysis whose response broke the structure (lane AN, 1).

Human decision of 2026-10-07, changing lane PA's "no automatic retry": when an
analysis ends in ``protocol_analysis_invalid_response`` (the model's JSON
parsed but broke the structure the domain validation demands) and it was the
revision's first analysis request, the same request is sent once more,
automatically. A failed source-evidence check, a time-out and a missing
provider are not sent again. While the retry runs the progress line says
"다시 시도 중(1/1)"; if the second answer fails too, a person presses
"분석 다시 시도" as before.

Measured 2026-10-06 (lane PX): ANKOM's first analysis after the change ended
in ``protocol_analysis_invalid_response``; the same document passed on other
runs, so the failure was the model's structure, not a trap in the source.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tests.test_lane_pa_analysis_start import SERVER as PAGE_SERVER
from tests.test_protocol_catalog import analysis_draft, write_text_pdf
from tests.test_screen_cleanup import run_page_script
from voiney_lab import server
from voiney_lab.experiment_protocol_analysis import (
    ProtocolAnalysisEvidenceError,
    ProtocolAnalysisModelError,
    ProtocolAnalysisResponseError,
    ProtocolAnalysisTimeoutError,
)
from voiney_lab.experiment_protocol_config import ProtocolPersistenceSettings
from voiney_lab.experiment_protocol_pdf import clear_protocol_pdf_cache
from voiney_lab.experiment_protocol_store import initialize_protocol_store
from voiney_lab.protocol_catalog import ProtocolCatalog

ANALYZE = "voiney_lab.protocol_catalog.analyze_protocol_extraction"
TEXT = "Protocol Alpha\nSection preparation\n1. Add solution.\nWear gloves."


def invalid_response() -> ProtocolAnalysisResponseError:
    return ProtocolAnalysisResponseError(
        "Structured Protocol failed deterministic domain validation.")


class _Case(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        clear_protocol_pdf_cache()
        self.settings = ProtocolPersistenceSettings(True, self.root / "catalog")
        self.store = initialize_protocol_store(self.settings)
        self.ready: list[str] = []
        self.catalog = ProtocolCatalog(
            self.store, on_analysis_ready=lambda _catalog, pid: self.ready.append(pid))
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

    def request_and_analyze(self, side_effect, analysis_id: str = "analysis-" + "a" * 32):
        self.catalog.request_analysis(self.protocol_id, analysis_id)
        with patch(ANALYZE, side_effect=side_effect) as analyze:
            try:
                self.catalog.analyze(self.protocol_id, Mock(), analysis_id=analysis_id)
            except Exception as exc:  # noqa: BLE001 - the caller looks at it
                return analyze, exc
        return analyze, None

    def event_types(self) -> list[str]:
        return [event.event_type for event in self.store.list_events(self.protocol_id)]

    def status(self) -> dict:
        return self.catalog.analysis_run_status(self.protocol_id).public_dict()


class RetryOnceTests(_Case):
    def test_an_invalid_response_on_the_first_request_is_sent_again_and_may_pass(self) -> None:
        analyze, error = self.request_and_analyze([invalid_response(), self.draft])
        self.assertIsNone(error)
        self.assertEqual(analyze.call_count, 2)
        self.assertEqual(self.event_types(), [
            "protocol_registered",
            "protocol_analysis_requested",
            "protocol_analysis_started",
            "protocol_analysis_failed",
            "protocol_analysis_retry_started",
            "protocol_analysis_ready",
            "protocol_review_required",
        ])
        status = self.status()
        self.assertEqual(status["state"], "review_required")
        # The first answer's failure is not the run's result.
        self.assertIsNone(status["failure_code"])
        self.assertEqual(status["automatic_retry"], {
            "attempt": 1, "limit": 1, "state": "passed",
            "reason_code": "protocol_analysis_invalid_response",
        })
        self.assertIsNone(self.catalog.review(self.protocol_id)["analysis_failure"])
        self.assertEqual(self.catalog.get_entry(self.protocol_id).analysis_status, "review_required")
        # The translation starts once, for the analysis that passed.
        self.assertEqual(self.ready, [self.protocol_id])

    def test_the_retry_is_the_same_request(self) -> None:
        analyze, _ = self.request_and_analyze([invalid_response(), self.draft])
        first, second = analyze.call_args_list
        self.assertEqual(first, second)
        self.assertIs(first.args[0], second.args[0])  # the same extraction

    def test_the_retry_is_recorded_as_automatic_and_bounded(self) -> None:
        self.request_and_analyze([invalid_response(), self.draft])
        retry = next(
            event for event in self.store.list_events(self.protocol_id)
            if event.event_type == "protocol_analysis_retry_started")
        self.assertEqual(retry.payload["attempt"], 1)
        self.assertEqual(retry.payload["limit"], 1)
        self.assertEqual(retry.payload["reason_code"], "protocol_analysis_invalid_response")
        self.assertEqual(retry.payload["authority"], "automatic_invalid_response_retry")
        self.assertEqual(retry.payload["retry_of"], "analysis-" + "a" * 32)
        self.assertNotEqual(retry.payload["analysis_id"], retry.payload["retry_of"])

    def test_a_second_invalid_response_waits_for_a_person(self) -> None:
        analyze, error = self.request_and_analyze([invalid_response(), invalid_response()])
        self.assertIsInstance(error, ProtocolAnalysisResponseError)
        self.assertEqual(analyze.call_count, 2)
        status = self.status()
        self.assertEqual(status["state"], "analysis_failed")
        self.assertEqual(status["failure_code"], "protocol_analysis_invalid_response")
        self.assertEqual(status["automatic_retry"]["state"], "failed")
        entry = self.catalog.get_entry(self.protocol_id)
        self.assertEqual((entry.analysis_status, entry.lifecycle_state), ("analysis_failed", "blocked"))
        failure = self.catalog.review(self.protocol_id)["analysis_failure"]
        self.assertEqual(failure["code"], "protocol_analysis_invalid_response")
        self.assertIn("분석 다시 시도", failure["action"])
        line = self.catalog.pipeline_status(self.protocol_id)
        self.assertEqual((line["stage"], line["blocked"]), ("analysis", True))
        self.assertIn("자동 재시도(1/1)", line["message"])
        self.assertIn("분석 다시 시도", line["action"])
        self.assertEqual(self.ready, [])

    def test_a_retry_that_ends_in_another_failure_reports_that_failure(self) -> None:
        evidence = ProtocolAnalysisEvidenceError("Evidence quote was invalid.")
        analyze, error = self.request_and_analyze([invalid_response(), evidence])
        self.assertIs(error, evidence)
        self.assertEqual(analyze.call_count, 2)
        self.assertEqual(self.status()["failure_code"], "protocol_analysis_invalid_evidence")
        self.assertEqual(self.catalog.pipeline_status(self.protocol_id)["stage"], "evidence")

    def test_a_person_pressing_retry_after_that_sends_one_request(self) -> None:
        self.request_and_analyze([invalid_response(), invalid_response()])
        analyze, error = self.request_and_analyze(
            [invalid_response(), self.draft], analysis_id="analysis-" + "b" * 32)
        self.assertIsInstance(error, ProtocolAnalysisResponseError)
        self.assertEqual(analyze.call_count, 1)
        self.assertEqual(self.event_types().count("protocol_analysis_retry_started"), 1)
        self.assertEqual(self.status()["failure_code"], "protocol_analysis_invalid_response")


class NotRetriedTests(_Case):
    def test_only_the_first_request_of_the_revision_is_retried(self) -> None:
        # A person's "분석 다시 시도" after an earlier failure of another kind.
        self.request_and_analyze([ProtocolAnalysisTimeoutError("timed out")])
        analyze, error = self.request_and_analyze(
            [invalid_response(), self.draft], analysis_id="analysis-" + "c" * 32)
        self.assertIsInstance(error, ProtocolAnalysisResponseError)
        self.assertEqual(analyze.call_count, 1)
        self.assertNotIn("protocol_analysis_retry_started", self.event_types())

    def test_other_failures_are_not_sent_again(self) -> None:
        failures = (
            ProtocolAnalysisEvidenceError("Evidence quote was invalid."),
            ProtocolAnalysisTimeoutError("timed out"),
            ProtocolAnalysisModelError("Protocol analysis model request failed."),
            RuntimeError("anything else"),
        )
        for index, failure in enumerate(failures):
            with self.subTest(failure=type(failure).__name__):
                self.tearDown()
                self.setUp()
                analyze, error = self.request_and_analyze([failure, self.draft])
                self.assertIs(error, failure)
                self.assertEqual(analyze.call_count, 1)
                self.assertNotIn("protocol_analysis_retry_started", self.event_types())
                self.assertIsNone(self.status()["automatic_retry"])

    def test_a_missing_provider_is_not_sent_again(self) -> None:
        self.catalog.request_analysis(self.protocol_id, "analysis-" + "d" * 32)
        self.catalog.fail_analysis_request(
            self.protocol_id, "analysis-" + "d" * 32, failure_code="provider_configuration_missing")
        self.assertNotIn("protocol_analysis_retry_started", self.event_types())
        self.assertEqual(self.status()["failure_code"], "provider_configuration_missing")


class PassedAfterAFailureTests(_Case):
    """Found while making decision 1: after a person's "분석 다시 시도" passed,
    the run status still carried the earlier failure code, and the page,
    which reads any failure code as a failed run, said "분석 실패" over a
    passed analysis (the review kept its "분석 실패와 복구" group too). A
    failure that a later passed analysis replaced is not the run's result."""

    def test_a_person_s_retry_that_passes_reads_as_passed(self) -> None:
        self.request_and_analyze([ProtocolAnalysisTimeoutError("timed out")])
        self.assertEqual(self.status()["failure_code"], "protocol_analysis_timeout")
        _, error = self.request_and_analyze([self.draft], analysis_id="analysis-" + "e" * 32)
        self.assertIsNone(error)
        status = self.status()
        self.assertEqual(status["state"], "review_required")
        self.assertIsNone(status["failure_code"])
        self.assertIsNone(status["failure_detail"])
        self.assertIsNone(self.catalog.review(self.protocol_id)["analysis_failure"])
        # The failure stays in the ledger.
        self.assertIn("protocol_analysis_failed", self.event_types())

    def test_a_person_s_retry_that_fails_again_reports_the_new_failure(self) -> None:
        self.request_and_analyze([ProtocolAnalysisTimeoutError("timed out")])
        self.request_and_analyze(
            [ProtocolAnalysisEvidenceError("Evidence quote was invalid.")],
            analysis_id="analysis-" + "f" * 32)
        self.assertEqual(self.status()["failure_code"], "protocol_analysis_invalid_evidence")
        self.assertEqual(
            self.catalog.review(self.protocol_id)["analysis_failure"]["code"],
            "protocol_analysis_invalid_evidence")


class WhileRetryingTests(_Case):
    def test_the_run_reads_as_running_with_the_retry_count(self) -> None:
        seen: dict = {}

        def second_call(*_args, **_kwargs):
            seen["status"] = self.status()
            seen["entry"] = self.catalog.get_entry(self.protocol_id)
            seen["pipeline"] = self.catalog.pipeline_status(self.protocol_id)
            seen["review"] = self.catalog.review(self.protocol_id)
            return self.draft

        calls = iter([invalid_response()])

        def side_effect(*args, **kwargs):
            for error in calls:
                raise error
            return second_call(*args, **kwargs)

        self.request_and_analyze(side_effect)
        status = seen["status"]
        self.assertEqual(status["state"], "analyzing")
        self.assertIsNone(status["failure_code"])
        self.assertEqual(status["automatic_retry"], {
            "attempt": 1, "limit": 1, "state": "in_progress",
            "reason_code": "protocol_analysis_invalid_response",
        })
        self.assertEqual(seen["entry"].lifecycle_state, "analyzing")
        self.assertNotEqual(seen["entry"].analysis_status, "analysis_failed")
        self.assertEqual((seen["pipeline"]["stage"], seen["pipeline"]["blocked"]), ("analysis", False))
        self.assertIn("다시 시도 중(1/1)", seen["pipeline"]["message"])
        self.assertIsNone(seen["review"]["analysis_failure"])


class BackgroundFlowTests(unittest.TestCase):
    """The upload chain and the analysis endpoint run the analysis off the
    request; the retry happens there too, against the model the server
    configured, with the same request."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        clear_protocol_pdf_cache()
        self.settings = ProtocolPersistenceSettings(True, root / "catalog")
        pdf = root / "alpha.pdf"
        write_text_pdf(pdf, TEXT, title="Protocol Alpha")
        store = initialize_protocol_store(self.settings)
        try:
            self.protocol_id = ProtocolCatalog(store).register(
                pdf, source_filename="alpha.pdf", media_type="application/pdf").entry.protocol_id
        finally:
            store.close()

    def tearDown(self) -> None:
        clear_protocol_pdf_cache()
        self.temp.cleanup()

    def open_catalog(self):
        opened = initialize_protocol_store(self.settings)
        return ProtocolCatalog(opened), opened

    def run_background(self, model) -> dict:
        async def scenario():
            await server.trigger_protocol_analysis(self.protocol_id)
            await server._PROTOCOL_ANALYSIS_TASKS[self.protocol_id]
            await asyncio.sleep(0)

        async def to_thread(function, /, *args, **kwargs):
            return function(*args, **kwargs)

        with patch.object(server, "_open_protocol_catalog", side_effect=self.open_catalog), \
                patch.object(server.asyncio, "to_thread", side_effect=to_thread), \
                patch.object(server, "_protocol_analysis_model", return_value=model):
            asyncio.run(scenario())
            return server.get_protocol_analysis_status(self.protocol_id)

    def test_a_model_answer_that_breaks_the_structure_twice_is_sent_twice_then_waits(self) -> None:
        model = Mock()
        # Valid JSON, not the structure the domain validation demands.
        model.analyze.return_value = "{}"
        status = self.run_background(model)
        self.assertEqual(model.analyze.call_count, 2)
        first, second = model.analyze.call_args_list
        self.assertEqual(first, second)  # the same prompt, input and schema
        self.assertEqual(status["state"], "analysis_failed")
        self.assertEqual(status["failure_code"], "protocol_analysis_invalid_response")
        self.assertEqual(status["automatic_retry"]["state"], "failed")

    def test_a_retry_that_passes_ends_in_review(self) -> None:
        catalog, opened = self.open_catalog()
        try:
            draft = analysis_draft(
                Path(self.temp.name) / "alpha.pdf", self.protocol_id, "Protocol Alpha")
        finally:
            opened.close()
        with patch(ANALYZE, side_effect=[invalid_response(), draft]) as analyze:
            status = self.run_background(Mock())
        self.assertEqual(analyze.call_count, 2)
        self.assertEqual(status["state"], "review_required")
        self.assertIsNone(status["failure_code"])
        self.assertEqual(status["automatic_retry"]["state"], "passed")

    def test_a_time_out_is_sent_once(self) -> None:
        with patch(ANALYZE, side_effect=[ProtocolAnalysisTimeoutError("timed out")]) as analyze:
            status = self.run_background(Mock())
        self.assertEqual(analyze.call_count, 1)
        self.assertEqual(status["failure_code"], "protocol_analysis_timeout")
        self.assertIsNone(status["automatic_retry"])


class RetryOnScreenTests(unittest.TestCase):
    def run_page(self, body: str) -> None:
        result = run_page_script(PAGE_SERVER + body)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_progress_line_says_retrying_one_of_one(self) -> None:
        self.run_page(r"""
await upload({analysis_status:"structured_analysis_ready",available_for_execution:false},false);
currentProtocolReviewId="p-ankom";
statusQueue=[{state:"analyzing",lifecycle_state:"analyzing",requested_at:new Date(Date.now()-125000).toISOString(),
 automatic_retry:{attempt:1,limit:1,state:"in_progress",reason_code:"protocol_analysis_invalid_response"}}];
await pollProtocolAnalysisStatus("p-ankom");
const progress=node("protocol-analysis-progress").textContent,status=node("protocol-upload-status").textContent;
assert(progress.includes("다시 시도 중(1/1)")&&/걸린 시간 2분 0[5-7]초/.test(progress),`no retry count on the progress line: ${progress}`);
assert(status.includes("다시 시도 중(1/1)")&&status.includes("형식"),`the status line does not say why it is retrying: ${status}`);
assert(!/in_progress|automatic_retry|protocol_analysis/.test(progress+status),`a code is on screen: ${progress} ${status}`);
""")

    def test_a_first_run_says_no_retry(self) -> None:
        self.run_page(r"""
await upload({analysis_status:"structured_analysis_ready",available_for_execution:false},false);
currentProtocolReviewId="p-ankom";
statusQueue=[{state:"analyzing",lifecycle_state:"analyzing",requested_at:new Date(Date.now()-5000).toISOString(),automatic_retry:null}];
await pollProtocolAnalysisStatus("p-ankom");
const progress=node("protocol-analysis-progress").textContent;
assert(progress.includes("분석 중")&&!progress.includes("다시 시도"),`a first run reads as a retry: ${progress}`);
""")

    def test_after_a_failed_retry_a_person_presses_retry(self) -> None:
        self.run_page(r"""
await upload({analysis_status:"structured_analysis_ready",available_for_execution:false},false);
currentProtocolReviewId="p-ankom";
statusQueue=[{state:"analysis_failed",failure_code:"protocol_analysis_invalid_response",requested_at:new Date(Date.now()-200000).toISOString(),
 automatic_retry:{attempt:1,limit:1,state:"failed",reason_code:"protocol_analysis_invalid_response"}}];
await pollProtocolAnalysisStatus("p-ankom");
const status=node("protocol-upload-status").textContent;
assert(status.includes("분석 실패")&&status.includes("자동으로 한 번 다시 시도")&&status.includes("분석 다시 시도"),`failure after the retry: ${status}`);
""")

    def test_a_retry_that_passed_reads_as_passed(self) -> None:
        self.run_page(r"""
await upload({analysis_status:"structured_analysis_ready",available_for_execution:false},false);
reviewExtra={analysis_status:"review_required",analysis_failure:null,development_activation_allowed:true};
currentProtocolReviewId="p-ankom";
statusQueue=[{state:"review_required",failure_code:null,requested_at:new Date(Date.now()-200000).toISOString(),
 automatic_retry:{attempt:1,limit:1,state:"passed",reason_code:"protocol_analysis_invalid_response"}}];
await pollProtocolAnalysisStatus("p-ankom");
const status=node("protocol-upload-status").textContent,progress=node("protocol-analysis-progress").textContent;
assert(status.includes("분석 통과")&&!status.includes("실패"),`a passed retry reads as failed: ${status}`);
assert(progress.includes("다시 시도 1/1 뒤 통과"),`the end line does not say a retry was used: ${progress}`);
""")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
