"""The MVP execution rule (lane DI, human decision of 2026-10-08).

A protocol runs when its analysis passed -- the source-evidence check every
analysis goes through included -- and none of its readiness reasons is an
execution blocker. The one human confirmation is the experimenter pressing
start on the screen that shows the source's safety statements; the server
records that with the experiment report. There is no approval, no reviewer
finding, no development activation and no test mode any more.

Everything here runs on fictional protocols built from text; no licensed PDF.
"""

from __future__ import annotations

import dataclasses
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.lane_cb_support import Turns
from tests.protocol_vocabulary_support import build_fixture
from tests.test_protocol_catalog import analysis_draft, write_text_pdf
from voiney_lab import experiment_protocol as domain
from voiney_lab import server as server_module
from voiney_lab.curated_protocol import CuratedProtocolSession
from voiney_lab.experiment_protocol_config import ProtocolPersistenceSettings
from voiney_lab.experiment_protocol_store import initialize_protocol_store
from voiney_lab.experiment_reports import ExperimentReportStore
from voiney_lab.protocol_catalog import (
    ProtocolCatalog,
    ProtocolCatalogUnavailableError,
)

_SAFETY = domain.ReadinessReasonCode.NO_DECLARED_SAFETY_WARNINGS
_PAGE = "Protocol Rule\nSection preparation\n1. Add solution.\nWear gloves."


def _reasons(*codes: domain.ReadinessReasonCode) -> domain.ReadinessAssessment:
    return domain.ReadinessAssessment(
        status=domain.ReadinessStatus.ANALYSIS_REQUIRED,
        label=domain.ANALYSIS_REQUIRED_LABEL,
        reasons=tuple(
            domain.ReadinessReason(code=code, message=f"{code.value}.") for code in codes
        ),
    )


class TheClassificationTests(unittest.TestCase):
    """Which readiness reasons still block, and which are notices."""

    def test_only_nothing_runnable_an_unread_page_and_a_safety_conflict_block(self) -> None:
        self.assertEqual(
            {code.value for code in domain.EXECUTION_BLOCKING_REASON_CODES},
            {
                "invalid_protocol",
                "source_text_cross_check_failed",
                "no_executable_steps",
                "source_page_requires_ocr",
                "safety_critical_conflict",
            },
        )

    def test_every_other_reason_is_a_notice(self) -> None:
        assessment = _reasons(*domain.ReadinessReasonCode)
        blocking = {r.code for r in domain.execution_blocking_reasons(assessment)}
        notices = {r.code for r in domain.execution_notice_reasons(assessment)}
        self.assertEqual(blocking, domain.EXECUTION_BLOCKING_REASON_CODES)
        self.assertEqual(blocking | notices, set(domain.ReadinessReasonCode))
        self.assertFalse(blocking & notices)
        # The constructs lane CB guides, and the ones this version cannot
        # guide yet, are notices either way; the latter are said once more
        # at the step.
        for code in (
            domain.ReadinessReasonCode.UNSUPPORTED_CONDITIONAL_BRANCH,
            domain.ReadinessReasonCode.UNCONFIRMED_FIXED_REPETITION,
            domain.ReadinessReasonCode.UNRESOLVED_AMBIGUITY,
            domain.ReadinessReasonCode.MISSING_EXECUTION_CRITICAL_VALUE,
            domain.ReadinessReasonCode.UNRESOLVED_EXECUTION_VALUE_CONFLICT,
        ):
            self.assertIn(code, notices, code)
        self.assertEqual(
            {code.value for code in domain.NO_GUIDANCE_YET_REASON_CODES},
            {
                "unsupported_parallel_background_work",
                "unsupported_recurring_reminder",
                "unsupported_recurring_action",
                "unsupported_reusable_subprocedure",
            },
        )

    def test_assess_readiness_itself_still_records_every_reason(self) -> None:
        """The stored assessment is unchanged; the split is read, not written."""

        with tempfile.TemporaryDirectory() as temp:
            pdf = Path(temp) / "rule.pdf"
            write_text_pdf(pdf, _PAGE, title="Protocol Rule")
            draft = analysis_draft(pdf, "protocol-rule", "Protocol Rule")
        self.assertEqual(draft.readiness.reason_codes, (_SAFETY.value,))
        self.assertIs(draft.readiness.status, domain.ReadinessStatus.ANALYSIS_REQUIRED)


class TheCatalogVerdictTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = initialize_protocol_store(
            ProtocolPersistenceSettings(True, self.root / "catalog")
        )
        self.addCleanup(self.store.close)
        self.catalog = ProtocolCatalog(self.store)
        self.pdf = self.root / "rule.pdf"
        write_text_pdf(self.pdf, _PAGE, title="Protocol Rule")
        self.protocol_id = self.catalog.register(
            self.pdf, source_filename="rule.pdf", media_type="application/pdf"
        ).entry.protocol_id
        self.draft = analysis_draft(self.pdf, self.protocol_id, "Protocol Rule")
        self.analyses = 0

    def _store(self, readiness: domain.ReadinessAssessment | None = None) -> None:
        self.analyses += 1
        self.store.append_analysis_revision(
            self.protocol_id, 1, f"analysis-{self.analyses}",
            self.draft.protocol, readiness or self.draft.readiness,
            self.draft.capability_policy_id,
        )

    def test_before_an_analysis_nothing_runs(self) -> None:
        entry = self.catalog.get_entry(self.protocol_id)
        self.assertFalse(entry.available_for_execution)
        self.assertEqual(entry.lifecycle_state, "uploaded")
        with self.assertRaises(ProtocolCatalogUnavailableError):
            self.catalog.load_executable_fixture(self.protocol_id)

    def test_a_passed_analysis_with_notices_only_may_run(self) -> None:
        self._store()
        entry = self.catalog.get_entry(self.protocol_id)
        self.assertTrue(entry.available_for_execution)
        self.assertEqual(entry.lifecycle_state, "ready")
        self.assertEqual(entry.execution_blocker_codes, ())
        self.assertFalse(entry.development_only)
        fixture = self.catalog.load_executable_fixture(self.protocol_id)
        self.assertEqual(fixture.status, "analysis_passed")
        self.assertFalse(fixture.development_only)
        # Nothing was written to make it so: no approval, no activation.
        self.assertEqual(
            {event.event_type for event in self.store.list_events(self.protocol_id)},
            {"protocol_registered"},
        )
        review = self.catalog.review(self.protocol_id)
        self.assertEqual(review["execution_blockers"], [])
        self.assertEqual(
            [(n["code"], n["kind"]) for n in review["execution_notices"]],
            [(_SAFETY.value, "source_note")],
        )
        self.assertEqual(review["pipeline"]["stage"], "ready")
        self.assertIn("이 프로토콜로 시작", review["pipeline"]["action"])

    def test_an_execution_blocker_keeps_it_out(self) -> None:
        for code, action_word in (
            (domain.ReadinessReasonCode.NO_EXECUTABLE_STEPS, "분석 다시 시도"),
            (domain.ReadinessReasonCode.SOURCE_PAGE_REQUIRES_OCR, "OCR"),
            (domain.ReadinessReasonCode.SAFETY_CRITICAL_CONFLICT, "고친 PDF"),
        ):
            with self.subTest(code=code.value):
                self._store(_reasons(_SAFETY, code))
                entry = self.catalog.get_entry(self.protocol_id)
                self.assertFalse(entry.available_for_execution)
                self.assertEqual(entry.lifecycle_state, "blocked")
                self.assertEqual(entry.execution_blocker_codes, (code.value,))
                with self.assertRaises(ProtocolCatalogUnavailableError):
                    self.catalog.load_executable_fixture(self.protocol_id)
                review = self.catalog.review(self.protocol_id)
                self.assertEqual(
                    [b["code"] for b in review["execution_blockers"]], [code.value]
                )
                self.assertEqual(review["execution_blockers"][0]["kind"], "blocking")
                self.assertEqual(
                    [n["code"] for n in review["execution_notices"]], [_SAFETY.value]
                )
                line = review["pipeline"]
                self.assertEqual((line["stage"], line["blocked"]), ("readiness", True))
                self.assertIn(action_word, line["action"])
                self.assertEqual(line["remaining_reason_codes"], [code.value])

    def test_a_construct_without_guidance_is_a_notice_said_as_such(self) -> None:
        self._store(_reasons(
            domain.ReadinessReasonCode.UNSUPPORTED_PARALLEL_BACKGROUND_WORK,
            domain.ReadinessReasonCode.UNRESOLVED_AMBIGUITY,
        ))
        self.assertTrue(self.catalog.get_entry(self.protocol_id).available_for_execution)
        notices = self.catalog.review(self.protocol_id)["execution_notices"]
        self.assertEqual(
            [(n["code"], n["kind"]) for n in notices],
            [
                ("unsupported_parallel_background_work", "no_guidance_yet"),
                ("unresolved_ambiguity", "source_note"),
            ],
        )
        for notice in notices:
            self.assertIn("원문", notice["message_ko"])
            self.assertNotIn("검토자", notice["message_ko"])
            self.assertNotIn("테스트 모드", notice["message_ko"])

    def test_the_review_lists_the_sources_safety_statements_for_the_start_screen(self) -> None:
        self._store()
        review = self.catalog.review(self.protocol_id)
        self.assertEqual(
            review["safety_notice_sources"],
            [{
                "step_id": "step-1", "step_label": "1", "statement_id": "glove-warning",
                "warning_index": 1, "source_page_number": 1, "source_text": "Wear gloves.",
            }],
        )
        for gone in ("gates", "reviewer_actions", "reviewer_findings",
                     "outstanding_blockers", "readiness_gates_cleared",
                     "hazard_review_required"):
            self.assertNotIn(gone, review, gone)

    def test_the_approval_and_finding_methods_are_gone(self) -> None:
        for name in ("approve", "activate_development", "deactivate_development",
                     "acknowledge_readiness_gate", "resolve_ambiguity",
                     "confirm_fixed_repetition", "review_ocr", "approval_context",
                     "development_activation_context", "skip_readiness_gates"):
            self.assertFalse(hasattr(self.catalog, name), name)


class TheStartScreenAndTheRecordTests(unittest.TestCase):
    """What the server hands the screen, and what it records on the press of start."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = initialize_protocol_store(
            ProtocolPersistenceSettings(True, self.root / "catalog")
        )
        self.addCleanup(self.store.close)
        self.catalog = ProtocolCatalog(self.store)
        pdf = self.root / "rule.pdf"
        write_text_pdf(pdf, _PAGE, title="Protocol Rule")
        protocol_id = self.catalog.register(
            pdf, source_filename="rule.pdf", media_type="application/pdf"
        ).entry.protocol_id
        draft = analysis_draft(pdf, protocol_id, "Protocol Rule")
        self.store.append_analysis_revision(
            protocol_id, 1, "analysis-1", draft.protocol, draft.readiness,
            draft.capability_policy_id,
        )
        self.fixture = self.catalog.load_executable_fixture(protocol_id)

    def test_the_safety_statements_carry_their_korean_where_one_is_stored(self) -> None:
        without = server_module.fixture_safety_notices(self.fixture)
        self.assertEqual(
            [(n["step_label"], n["source_page_number"], n["source_text"], n["primary_text"],
              n["translation_check"]) for n in without],
            [("1", 1, "Wear gloves.", None, "missing")],
        )
        translated = dataclasses.replace(
            self.fixture, localizations={"step-1/warning_1": "장갑을 착용하세요."}
        )
        with_korean = server_module.fixture_safety_notices(translated)
        self.assertEqual(with_korean[0]["primary_text"], "장갑을 착용하세요.")
        self.assertEqual(with_korean[0]["translation_check"], "passed")
        # A Korean that drops or changes what the source says is not shown.
        bad = dataclasses.replace(
            self.fixture, localizations={"step-1/warning_1": "장갑을 끼지 마세요."}
        )
        self.assertIsNone(server_module.fixture_safety_notices(bad)[0]["primary_text"])

    def test_the_press_of_start_is_recorded_with_the_statements_shown(self) -> None:
        curated = CuratedProtocolSession(self.fixture)
        listener = SimpleNamespace(
            session_id="lane-di-session",
            experiment_report_store=ExperimentReportStore(self.root / "r.sqlite"),
            experiment_report_id=None,
            safety_notices_shown=server_module.fixture_safety_notices(self.fixture),
        )
        with patch.dict(os.environ, {"VOINEY_LAB_WORKSPACE_ENABLED": "false"}):
            report = server_module._open_experiment_report(listener, curated)
        events = {event["event_type"]: event for event in report["events"]}
        self.assertIn("safety_notices_acknowledged", events)
        payload = events["safety_notices_acknowledged"]["payload"]
        self.assertEqual(payload["notice_count"], 1)
        self.assertEqual(payload["confirmed_by"], "experimenter_start")
        self.assertEqual(
            payload["notices"],
            [{"step_label": "1", "source_page_number": 1, "source_text": "Wear gloves."}],
        )
        self.assertFalse(report["development_only"])
        self.assertNotIn("test_mode_readiness_gates_skipped", events)
        # The report's run table says the rule, in the words of decision 7.
        text = listener.experiment_report_store.export_markdown(
            report["report_id"], fixture=self.fixture
        ).decode()
        self.assertIn("| 프로토콜 상태 | 분석 통과 · 실험자가 시작함 |", text)
        self.assertIn("| 안전 주의 확인 | 시작 전 화면에서 원문 안전 주의 1건을 보고 시작함 |", text)
        for gone in ("승인", "검토자"):
            self.assertNotIn(gone, text)

    def test_without_a_press_recorded_nothing_is_claimed(self) -> None:
        curated = CuratedProtocolSession(self.fixture)
        listener = SimpleNamespace(
            session_id="lane-di-session-2",
            experiment_report_store=ExperimentReportStore(self.root / "r2.sqlite"),
            experiment_report_id=None,
            safety_notices_shown=None,
        )
        with patch.dict(os.environ, {"VOINEY_LAB_WORKSPACE_ENABLED": "false"}):
            report = server_module._open_experiment_report(listener, curated)
        self.assertNotIn(
            "safety_notices_acknowledged",
            {event["event_type"] for event in report["events"]},
        )
        text = listener.experiment_report_store.export_markdown(
            report["report_id"], fixture=self.fixture
        ).decode()
        self.assertNotIn("안전 주의 확인", text)


def _parallel_fixture():
    """A fictional protocol whose step 3 states work done alongside the gel."""

    steps = (
        "1 Cut the gel band into pieces.",
        "2 Wash the pieces with buffer.",
        "3 Meanwhile, start the water bath while the gel is washing.",
        "4 Dry the pieces completely.",
        "5 Add the digestion solution.",
    )
    base = build_fixture(
        protocol_id="lane-di-parallel", title="Fictional parallel protocol",
        steps=steps,
    )
    protocol = base.draft.protocol
    step = protocol.sections[0].steps[2]
    construct = domain.ParallelWork(
        parallel_id="parallel-3",
        concurrent_step_ids=("step-3", "step-4"),
        source_text=step.instruction_source_text,
        evidence=domain.SourceEvidence(1, step.instruction_source_text),
        step_id="step-3",
    )
    protocol = dataclasses.replace(protocol, constructs=(construct,))
    domain.validate_protocol(protocol)
    draft = dataclasses.replace(
        base.draft, protocol=protocol, readiness=domain.assess_readiness(protocol),
    )
    return dataclasses.replace(base, draft=draft)


class TheOneTimeNoticeAtTheStepTests(unittest.TestCase):
    """A construct with no guidance yet is said once at the step; the source is read."""

    def test_the_fixture_carries_the_reason_as_a_notice(self) -> None:
        fixture = _parallel_fixture()
        codes = fixture.draft.readiness.reason_codes
        self.assertIn("unsupported_parallel_background_work", codes)
        self.assertEqual(domain.execution_blocking_reasons(fixture.draft.readiness), ())

    def test_arriving_at_the_step_says_it_once_with_the_sources_words(self) -> None:
        turns = Turns()
        turns.open(1, fixture=_parallel_fixture())
        plan = turns.say("2단계 완료했어")
        self.assertTrue(plan.state_changed)
        self.assertEqual(turns.label(), "3")
        self.assertIn("이 단계의 동시 작업은 아직 안내 기능이 없어요. 원문을 읽어 드릴게요.", plan.display_text)
        self.assertIn("Meanwhile, start the water bath while the gel is washing.", plan.display_text)
        self.assertIn("동시 작업은 아직 안내 기능이 없어요", plan.speech_text)
        # The step itself is presented as at any other step.
        self.assertIn("3단계", plan.speech_text)
        # Said once per run: standing on the step again says nothing more.
        self.assertEqual(turns.session._enter_step_notice("ko"), "")
        # A new run owes the notice again.
        turns.session.reset()
        turns.session.activate_configured()
        turns.session.current_index = 2
        self.assertIn("동시 작업", turns.session._enter_step_notice("ko"))

    def test_other_steps_and_other_protocols_say_nothing(self) -> None:
        turns = Turns()
        turns.open(0, fixture=_parallel_fixture())
        self.assertEqual(turns.session._enter_step_notice("ko"), "")
        plan = turns.say("1단계 완료했어")
        self.assertEqual(turns.label(), "2")
        self.assertNotIn("안내 기능이 없어요", plan.display_text)
        headspace = Turns()
        headspace.open(0)
        for index in range(len(headspace.session.fixture.steps)):
            headspace.session.current_index = index
            self.assertEqual(headspace.session._no_guidance_notice(), "", index)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
