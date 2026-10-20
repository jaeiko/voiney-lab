"""A reviewer's value is said as the reviewer's, and the report lists them (lane RV, decision g).

Human decision 2026-10-12: a value a person entered or fixed in a registered
review version is said to be the reviewer's the first time the run speaks it
-- "검토자가 넣은 값이에요" -- once; the report carries a table "원문 대비
검토자 수정 n건" with each value against the source.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.lane_cb_support import Turns
from tests.test_lane_rv_review_api import STEP_TWO
from tests.test_lane_rv_review_pending import MADE_UP, _CatalogCase
from voiney_lab import server as server_module
from voiney_lab.curated_protocol import CuratedProtocolSession
from voiney_lab.experiment_reports import ExperimentReportStore, report_protocol_fixture

NOTICE = "검토자가 넣은 값이에요"


class _ReviewedCase(_CatalogCase):
    def setUp(self) -> None:
        super().setUp()
        self.analyze(self.answer(step_two=MADE_UP, pages=(1, 2, 3)))
        decide = lambda **request: self.catalog.record_review_decision(self.protocol_id, request)
        decide(item_id="step:s2", action="fix", text=STEP_TWO,
               source_lines={"page": 1, "first_line": 4, "last_line": 4})
        decide(item_id="step:s1", action="fix", text="1. Add 2.5 mL of buffer A.")
        # The AI's reading of step 2 printed no time: the reviewer enters one.
        decide(item_id="timer:s2", action="enter", seconds=900)
        decide(item_id="statement:gloves", action="memo", memo="니트릴 장갑")
        self.catalog.register_review(self.protocol_id, {"reviewer_name": "김재준"})
        self.fixture = self.catalog.load_executable_fixture(self.protocol_id)


class TheRunSaysItOnceTests(_ReviewedCase):
    def test_arriving_at_a_step_says_the_reviewer_s_values_once(self) -> None:
        turns = Turns()
        turns.open(None, fixture=self.fixture)
        start = turns.say("프로토콜 시작해줘")
        self.assertEqual(turns.label(), "1")
        self.assertIn(f"{NOTICE}: 2.5 mL.", start.speech_text)
        # Step 2's warning is read out before it begins, as the server does.
        turns.session.record_safety_warning_disclosure(
            1, actor_principal_id="tester", actor_role="experimenter")
        second = turns.say("1단계 완료했어")
        self.assertEqual(turns.label(), "2")
        # The fixed sentence's new number and the reviewer's timer.
        self.assertIn(f"{NOTICE}: 10 min · 37 C · 15분.", second.speech_text)
        self.assertIn(NOTICE, second.display_text)
        # Said once per run: standing on the step again says nothing more.
        self.assertNotIn(NOTICE, turns.session._enter_step_notice("ko"))
        third = turns.say("2단계 완료했어")
        self.assertNotIn(NOTICE, third.speech_text)

    def test_an_analysis_as_it_came_says_nothing_of_a_reviewer(self) -> None:
        self.analyze(self.answer(pages=(1, 2, 3)), analysis_id="analysis-" + "z" * 32,
                     reanalysis=True)
        (analysis,) = [item for item in self.store.list_analysis_revisions(self.protocol_id, 1)
                       if not item.analysis_id.startswith("review-v")]
        plain = self.catalog.load_fixture_for_revision(
            self.protocol_id, f"pdf-1-analysis-{analysis.analysis_revision_number}")
        self.assertIsNone(getattr(plain.draft, "review_version", None))
        session = CuratedProtocolSession(plain)
        session.activate_configured()
        for index in range(len(plain.steps)):
            session.current_index = index
            self.assertNotIn(NOTICE, session._enter_step_notice("ko"))


class TheReportListsTheChangesTests(_ReviewedCase):
    def test_the_report_has_the_table_against_the_source(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        curated = CuratedProtocolSession(self.fixture)
        listener = SimpleNamespace(
            session_id="lane-rv-session",
            experiment_report_store=ExperimentReportStore(Path(temp.name) / "r.sqlite"),
            experiment_report_id=None,
            safety_notices_shown=None,
        )
        with patch.dict(os.environ, {"VOINEY_LAB_WORKSPACE_ENABLED": "false"}):
            report = server_module._open_experiment_report(listener, curated)
        text = listener.experiment_report_store.export_markdown(
            report["report_id"], fixture=self.fixture).decode()
        # Lane RP3, decision ①: the version in the header, the table in the appendix.
        self.assertRegex(text, r"\| 프로토콜 \| [^|]+ · 검토본 v1 \(김재준, \d{4}-\d{2}-\d{2}\) \|")
        self.assertRegex(text, r"### 검토본 v1 · 김재준 · \d{4}-\d{2}-\d{2}")
        self.assertIn("| 단계 | 항목 | 원문 · AI 제안 | 검토본 | 출처 | 검토자 |", text)
        self.assertIn(f"| 1 | 단계 | 1. Add 2 mL of buffer A. | 1. Add 2.5 mL of buffer A. | 검토자 입력 | 김재준 |", text)
        self.assertIn(f"| 2 | 단계 | {MADE_UP} | {STEP_TWO} | 검토자 입력 | 김재준 |", text)
        self.assertIn("| 2 | 단계 타이머 | — | 15분 | 검토자 입력 | 김재준 |", text)

    def test_the_report_reads_the_version_the_run_followed(self) -> None:
        # Registered again after the run: the run's own version is read.
        self.catalog.record_review_decision(self.protocol_id, {
            "item_id": "timer:s2", "action": "fix", "seconds": 1200})
        self.catalog.register_review(self.protocol_id, {"reviewer_name": "최수진"})
        self.store.close()

        def open_catalog():
            from voiney_lab.experiment_protocol_store import initialize_protocol_store
            from voiney_lab.protocol_catalog import ProtocolCatalog
            opened = initialize_protocol_store(self.settings)
            return ProtocolCatalog(opened), opened

        try:
            with patch.object(server_module, "_open_protocol_catalog", side_effect=open_catalog), \
                    patch.object(server_module, "_configured_candidate_fixture", return_value=None), \
                    patch.object(server_module, "server_config", return_value=None), \
                    patch.object(server_module, "_with_revision_translations",
                                 side_effect=lambda fixture, **_: fixture):
                fixture, problem = report_protocol_fixture({
                    "protocol_id": self.protocol_id, "protocol_revision": "pdf-1-review-1"})
        finally:
            from voiney_lab.experiment_protocol_store import initialize_protocol_store
            self.store = initialize_protocol_store(self.settings)
        self.assertEqual(problem, "")
        self.assertEqual(fixture.revision_id, "pdf-1-review-1")
        self.assertEqual(fixture.timer_manifest["s2"], 900)


if __name__ == "__main__":
    unittest.main()
