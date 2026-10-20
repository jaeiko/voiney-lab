"""A PI's "확인함" on a lab notebook and on a review version (lane RP3, decision ④, 2026-10-20).

Human decision: an administrator account (lane ON) may leave "확인함" -- the
account's name, the time, one line -- on an experiment's notebook and on a
registered review version. It is an append-only event that neither blocks
the start of an experiment nor changes what was recorded; the notebook's
"PI 확인" box and the review version's record show it. Server paths and
permission only; the screen's button is for the screen's lane.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fastapi import HTTPException

from voiney_lab import experiment_reports as er
from voiney_lab import server as server_module

from tests.lane_rp2_support import START_CHECK, deviation_run, environment, protocol_double, temporary
from tests.test_lane_rv_voice_and_report import _ReviewedCase

ADMIN = SimpleNamespace(admin=True, name="김교수")
EXPERIMENTER = SimpleNamespace(admin=False, name="이실험")


class _Accounts:
    def as_account(self, account):
        token = server_module._REQUEST_ACCOUNT.set(account)
        self.addCleanup(server_module._REQUEST_ACCOUNT.reset, token)


class NotebookConfirmationTests(_Accounts, unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = temporary()
        self.addCleanup(self.temporary.cleanup)
        self.env = environment()
        self.env.start()
        self.addCleanup(self.env.stop)
        self.record = deviation_run(self.temporary.name)
        settings = mock.patch(
            "voiney_lab.server.ExperimentReportSettings.from_environment",
            return_value=er.ExperimentReportSettings(True, self.record.store.path))
        settings.start()
        self.addCleanup(settings.stop)
        scope = mock.patch.object(server_module, "_scope_tenant_resource")
        scope.start()
        self.addCleanup(scope.stop)

    def test_an_admin_leaves_a_confirmation_the_notebook_shows(self) -> None:
        self.as_account(ADMIN)
        with mock.patch.object(er, "_now", lambda: "2026-10-19T02:00:00+00:00"):
            answer = server_module.post_experiment_report_pi_confirmation(
                self.record.report_id, {"comment": "온도 편차 확인함, 다음엔 항온조 먼저"})
        self.assertEqual(answer["report_id"], self.record.report_id)
        self.assertEqual(answer["confirmations"], [
            {"name": "김교수", "comment": "온도 편차 확인함, 다음엔 항온조 먼저", "at": "2026-10-19T02:00:00+00:00"}])
        events = [e for e in self.record.doc()["events"] if e["event_type"] == er.PI_CONFIRMED]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["payload"], {"name": "김교수", "comment": "온도 편차 확인함, 다음엔 항온조 먼저",
                                                "source": "screen"})
        text = self.record.store.export_markdown(
            self.record.report_id, fixture=protocol_double(), start_check=START_CHECK, previous=None).decode()
        self.assertIn("| PI | 김교수 | 2026년 10월 19일 11:00 | 온도 편차 확인함, 다음엔 항온조 먼저 |", text)
        listed = server_module.get_experiment_report_pi_confirmations(self.record.report_id)
        self.assertEqual(listed["confirmations"], answer["confirmations"])
        # The screen's report state carries it too (the button's place).
        public = server_module._public_experiment_report_state(self.record.doc())
        self.assertEqual([item["name"] for item in public["pi_confirmations"]], ["김교수"])

    def test_a_second_confirmation_is_appended_and_the_record_unchanged(self) -> None:
        self.as_account(ADMIN)
        before = [e["event_key"] for e in self.record.doc()["events"]]
        server_module.post_experiment_report_pi_confirmation(self.record.report_id, {"comment": "확인"})
        server_module.post_experiment_report_pi_confirmation(self.record.report_id, {})
        events = self.record.doc()["events"]
        self.assertEqual([e["event_key"] for e in events][:len(before)], before)
        confirmations = [e for e in events if e["event_type"] == er.PI_CONFIRMED]
        self.assertEqual([e["payload"]["comment"] for e in confirmations], ["확인", ""])
        self.assertEqual(self.record.doc()["status"], "completed")

    def test_an_experimenter_or_nobody_is_refused(self) -> None:
        for account in (EXPERIMENTER, None):
            with self.subTest(account=account):
                self.as_account(account)
                with self.assertRaises(HTTPException) as refused:
                    server_module.post_experiment_report_pi_confirmation(self.record.report_id, {"comment": "x"})
                self.assertEqual(refused.exception.status_code, 403)
                self.assertEqual(refused.exception.detail, "pi_confirmation_admin_only")
        self.assertEqual([e for e in self.record.doc()["events"] if e["event_type"] == er.PI_CONFIRMED], [])

    def test_a_long_comment_or_an_unknown_report(self) -> None:
        self.as_account(ADMIN)
        with self.assertRaises(HTTPException) as refused:
            server_module.post_experiment_report_pi_confirmation(self.record.report_id, {"comment": "가" * 301})
        self.assertEqual(refused.exception.status_code, 422)
        with self.assertRaises(HTTPException) as missing:
            server_module.post_experiment_report_pi_confirmation("ER-NOSUCHREPORT0000000", {"comment": "x"})
        self.assertEqual(missing.exception.status_code, 404)


class ReviewVersionConfirmationTests(_Accounts, _ReviewedCase):
    def test_an_admin_confirms_a_review_version_and_the_record_shows_it(self) -> None:
        self.as_account(ADMIN)
        confirmed = self.catalog.confirm_review_version(
            self.protocol_id, 1, name="김교수", comment="검토본 확인함")
        self.assertEqual(confirmed["version"], 1)
        self.assertEqual([(item["name"], item["comment"]) for item in confirmed["pi_confirmations"]],
                         [("김교수", "검토본 확인함")])
        self.assertTrue(confirmed["pi_confirmations"][0]["at"])
        again = self.catalog.review_version(self.protocol_id, 1)
        self.assertEqual(len(again["pi_confirmations"]), 1)
        # The versions list carries it, and the start is not held back by it.
        listed = self.catalog.get_entry(self.protocol_id).review_versions[-1]
        self.assertEqual([item["name"] for item in listed["pi_confirmations"]], ["김교수"])
        self.assertEqual(self.catalog.load_executable_fixture(self.protocol_id).revision_id, self.fixture.revision_id)

    def test_an_unknown_version_is_refused(self) -> None:
        from voiney_lab.protocol_catalog import ProtocolCatalogNotFoundError

        with self.assertRaises(ProtocolCatalogNotFoundError):
            self.catalog.confirm_review_version(self.protocol_id, 9, name="김교수", comment="")

    def test_the_route_needs_an_admin_account(self) -> None:
        self.as_account(EXPERIMENTER)
        with mock.patch.object(server_module, "_open_protocol_catalog", return_value=(self.catalog, self.store)), \
                mock.patch.object(server_module, "_scope_catalog_resource"), \
                mock.patch.object(self.store, "close"):
            with self.assertRaises(HTTPException) as refused:
                server_module.post_protocol_review_version_pi_confirmation(self.protocol_id, 1, {"comment": "x"})
            self.assertEqual(refused.exception.status_code, 403)
            self.as_account(ADMIN)
            answer = server_module.post_protocol_review_version_pi_confirmation(self.protocol_id, 1, {"comment": "확인"})
        self.assertEqual([item["name"] for item in answer["pi_confirmations"]], ["김교수"])


if __name__ == "__main__":
    unittest.main()
