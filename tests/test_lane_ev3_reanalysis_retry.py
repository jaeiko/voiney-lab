"""A re-analysis whose first answer breaks the structure is sent once more (lane EV3, decision 5).

Human decision 2026-10-12. Lane RN (#67) gave a passed analysis "다시 분석"; its
run had no automatic retry, so a response that did not close as JSON -- a
fifth of Gemini's analysis answers in lane M3 -- had to be pressed again by
hand (the development server's headspace re-analysis ended so on 10/12 after
1 min 06 s). Now the re-analysis's first answer that ends in
``protocol_analysis_invalid_response`` is sent once more by the same rule as a
first analysis (lane AN, decision 1): once, the same request, one reasoning
level lower when the answer was cut at the output limit (lane AQ, decision 4).
Other failures are not sent again, a person's "분석 다시 시도" after a failed
first analysis is still one request, and the pass before the re-analysis
stays executable whatever its run does.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from tests.test_lane_aq_truncated_retry import CUT, WHOLE, FakeClient
from tests.test_lane_rn_reanalysis import FIRST, SECOND, _CatalogCase
from voiney_lab.experiment_protocol_analysis import (
    OpenAICompatibleProtocolAnalysisModel,
    ProtocolAnalysisEvidenceError,
    ProtocolAnalysisResponseError,
    ProtocolAnalysisTimeoutError,
)
from voiney_lab.protocol_catalog import TRUNCATED_FINISH_REASON

PARSE = "voiney_lab.experiment_protocol_analysis.parse_protocol_analysis_response"
THIRD = "analysis-" + "c" * 32


def invalid_response() -> ProtocolAnalysisResponseError:
    return ProtocolAnalysisResponseError("Protocol analysis response is not exactly one valid JSON object.")


class _Case(_CatalogCase):
    def retries(self) -> list:
        return [event for event in self.events()
                if event.event_type == "protocol_analysis_retry_started"]


class ReanalysisRetryTests(_Case):
    def test_an_invalid_first_answer_of_a_re_analysis_is_sent_again_and_may_pass(self) -> None:
        self.pass_once()
        analyze, error = self.analyze([invalid_response(), self.draft], SECOND, reanalysis=True)
        self.assertIsNone(error)
        self.assertEqual(analyze.call_count, 2)
        first, second = analyze.call_args_list
        self.assertEqual(first, second)  # the same request
        (retry,) = self.retries()
        self.assertEqual(retry.payload["retry_of"], SECOND)
        self.assertEqual(retry.payload["attempt"], 1)
        self.assertEqual(retry.payload["authority"], "automatic_invalid_response_retry")
        entry = self.catalog.get_entry(self.protocol_id)
        self.assertEqual(entry.revision_id, "pdf-1-analysis-2")
        self.assertEqual(self.status()["automatic_retry"]["state"], "passed")
        self.assertIsNone(self.catalog.review(self.protocol_id)["reanalysis"])
        self.assertEqual(self.ready, [self.protocol_id, self.protocol_id])

    def test_a_second_invalid_answer_leaves_the_pass_executable_and_says_so(self) -> None:
        self.pass_once()
        analyze, error = self.analyze([invalid_response(), invalid_response()], SECOND, reanalysis=True)
        self.assertIsInstance(error, ProtocolAnalysisResponseError)
        self.assertEqual(analyze.call_count, 2)
        entry = self.catalog.get_entry(self.protocol_id)
        self.assertTrue(entry.available_for_execution)
        self.assertEqual(entry.revision_id, "pdf-1-analysis-1")
        again = self.catalog.review(self.protocol_id)["reanalysis"]
        self.assertEqual(again["state"], "analysis_failed")
        self.assertEqual(again["failure"]["code"], "protocol_analysis_invalid_response")
        self.assertEqual(again["automatic_retry"]["state"], "failed")
        line = self.catalog.pipeline_status(self.protocol_id)
        self.assertEqual((line["stage"], line["blocked"]), ("ready", False))
        self.assertIn("다시 분석이 실패했습니다", line["message"])
        self.assertIn("자동 재시도(1/1)도 실패했습니다", line["message"])
        self.assertIn("계속 실행할 수 있습니다", line["message"])

    def test_the_line_says_retrying_while_the_retry_runs(self) -> None:
        self.pass_once()
        seen = {}

        answers = iter([invalid_response(), self.draft])

        def attempt(*_args, **_kwargs):
            answer = next(answers)
            if isinstance(answer, Exception):
                raise answer
            seen["line"] = self.catalog.pipeline_status(self.protocol_id)
            seen["status"] = self.status()
            return answer

        self.analyze(attempt, SECOND, reanalysis=True)
        line = seen["line"]
        self.assertEqual(line["stage"], "ready")
        self.assertIn("다시 분석 중입니다", line["message"])
        self.assertIn("다시 시도 중(1/1)", line["message"])
        self.assertIn("계속 실행할 수 있습니다", line["message"])
        self.assertEqual(seen["status"]["automatic_retry"]["state"], "in_progress")
        self.assertTrue(seen["status"]["reanalysis"])

    def test_each_re_analysis_gets_its_own_one_retry(self) -> None:
        self.pass_once()
        self.analyze([invalid_response(), invalid_response()], SECOND, reanalysis=True)
        analyze, error = self.analyze([invalid_response(), self.draft], THIRD, reanalysis=True)
        self.assertIsNone(error)
        self.assertEqual(analyze.call_count, 2)
        self.assertEqual(len(self.retries()), 2)

    def test_other_failures_of_a_re_analysis_are_not_sent_again(self) -> None:
        for failure in (
            ProtocolAnalysisEvidenceError("Evidence quote was invalid."),
            ProtocolAnalysisTimeoutError("timed out"),
        ):
            with self.subTest(failure=type(failure).__name__):
                self.tearDown()
                self.setUp()
                self.pass_once()
                analyze, error = self.analyze([failure, self.draft], SECOND, reanalysis=True)
                self.assertIs(error, failure)
                self.assertEqual(analyze.call_count, 1)
                self.assertEqual(self.retries(), [])


class AFirstAnalysisIsAsBeforeTests(_Case):
    def test_a_person_s_retry_after_a_failed_first_analysis_is_still_one_request(self) -> None:
        self.analyze([invalid_response(), invalid_response()], FIRST)
        analyze, error = self.analyze([invalid_response(), self.draft], SECOND)
        self.assertIsInstance(error, ProtocolAnalysisResponseError)
        self.assertEqual(analyze.call_count, 1)
        self.assertEqual(len(self.retries()), 1)

    def test_a_re_analysis_request_before_any_pass_is_a_first_request(self) -> None:
        # Asked with reanalysis=True before anything passed: a plain request,
        # retried as the revision's first.
        analyze, error = self.analyze([invalid_response(), self.draft], FIRST, reanalysis=True)
        self.assertIsNone(error)
        self.assertEqual(analyze.call_count, 2)


class TruncatedReanalysisTests(_Case):
    def test_a_cut_first_answer_of_a_re_analysis_is_asked_one_level_lower(self) -> None:
        self.pass_once()
        client = FakeClient((CUT, TRUNCATED_FINISH_REASON), (WHOLE, "stop"))
        model = OpenAICompatibleProtocolAnalysisModel(client, "gemini-3.8-flash", "medium")
        self.catalog.request_analysis(self.protocol_id, SECOND, reanalysis=True)
        with patch(PARSE, side_effect=[invalid_response(), self.draft]):
            self.catalog.analyze(self.protocol_id, model, analysis_id=SECOND)
        self.assertEqual([r["reasoning_effort"] for r in client.requests], ["medium", "low"])
        (retry,) = self.retries()
        self.assertIs(retry.payload["truncated"], True)
        self.assertEqual(
            (retry.payload["previous_reasoning_effort"], retry.payload["reasoning_effort"]),
            ("medium", "low"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
