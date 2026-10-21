"""QA record seams, exercised through voice turns and HTTP endpoints."""
from __future__ import annotations

import asyncio
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from tests.lane_n_support import VoiceNotesHarness
from tests.lane_rp2_support import unconfirmed_run
from tests import test_lane_r6_voice_followups as research_support
from voiney_lab import server
from voiney_lab import experiment_reports as er


class ObservationTests(VoiceNotesHarness, unittest.TestCase):
    def test_notebook_answer_is_bound_to_the_completed_step_in_both_ledgers(self):
        self.turns("프로토콜 시작해줘", "넘어가", "1단계 완료했어", "2단계 완료했어",
                   "네", "3단계 완료했어", "4단계 완료했어", "5단계 완료했어", "네",
                   "밴드가 투명하고 깨끗해")
        observed = [entry for entry in [e["observation"] for e in self.timeline()["timeline"] if "observation" in e]
                    if entry["content"] == "밴드가 투명하고 깨끗해"]
        assert len(observed) == 1
        assert observed[0]["protocol_step_id"] == "step-5"


class ReportRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.record = unconfirmed_run(self.tmp.name)
        for item in (patch.object(server, "_report_prose_store", return_value=self.record.store),
                     patch.object(server, "_prepare_report_prose", return_value="disabled")):
            item.start()
            self.addCleanup(item.stop)

    async def request_async(self, method, path, payload=None):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://test") as client:
            return await client.request(method, path, json=payload)

    def request(self, method, path, payload=None):
        return asyncio.run(self.request_async(method, path, payload))

    def test_repeated_confirmation_writes_one_event(self):
        item = er.report_review_items(self.record.doc()["events"])[0]
        path = f'/api/experiment-reports/{self.record.report_id}/review/{item["item_id"]}'
        for _ in range(2):
            response = self.request("POST", path, {"action": "confirm"})
            assert response.status_code == 200, response.text
        events = [e for e in self.record.doc()["events"] if e["event_type"] == er.REVIEW_CONFIRMED
                  and e["payload"].get("item_id") == item["item_id"]]
        assert len(events) == 1

    def test_repeated_correction_and_confirmation_write_once(self):
        item = er.report_review_items(self.record.doc()["events"])[0]
        path = f'/api/experiment-reports/{self.record.report_id}/review/{item["item_id"]}'
        for _ in range(2):
            response = self.request("POST", path, {"action": "correct", "text": "세척액 460 µL"})
            assert response.status_code == 200, response.text
        events = self.record.doc()["events"]
        assert len([e for e in events if e["event_type"] == "record_corrected"]) == 1
        assert len([e for e in events if e["event_type"] == er.REVIEW_CONFIRMED]) == 1

    def test_meeting_exports_keep_openable_filename_extensions(self):
        with patch.object(server, "_scope_tenant_resource"), \
             patch.object(server.ExperimentReportSettings, "from_environment", return_value=er.ExperimentReportSettings(True, self.record.store.path)), \
             patch.object(er.ExperimentReportStore, "export_meeting_docx", return_value=b"docx"), \
             patch.object(er.ExperimentReportStore, "export_meeting_pdf", return_value=b"pdf"):
            for kind, filename in (("meeting-docx", "meeting.docx"), ("meeting-pdf", "meeting.pdf")):
                response = self.request("GET", f"/api/experiment-reports/{self.record.report_id}.{kind}")
                assert response.status_code == 200, response.text
                assert response.headers["content-disposition"].endswith(filename + '"')

    def test_concurrent_pi_confirmations_are_both_kept(self):
        initial = self.record.doc()
        real_get = self.record.store.get_report
        barrier = threading.Barrier(2)
        local = threading.local()
        def same_snapshot(report_id):
            if not getattr(local, "read", False):
                local.read = True
                barrier.wait(timeout=10)
                return initial
            return real_get(report_id)
        def confirm(comment):
            token = server._REQUEST_ACCOUNT.set(SimpleNamespace(admin=True, name="Test PI"))
            try:
                return server.post_experiment_report_pi_confirmation(self.record.report_id, {"comment": comment})
            finally:
                server._REQUEST_ACCOUNT.reset(token)
        with patch.object(self.record.store, "get_report", side_effect=same_snapshot), ThreadPoolExecutor(2) as executor:
            results = list(executor.map(confirm, ("first", "second")))
        assert len(results) == 2
        confirmations = er.pi_confirmations(self.record.doc()["events"])
        assert {entry["comment"] for entry in confirmations} == {"first", "second"}


class ResearchEventTests(unittest.TestCase):
    def test_research_report_events_include_the_current_configuration_fence(self):
        harness = research_support.ServerOutsidePdfExplanationTests()
        session = harness._session(2, supplemental=True)
        with tempfile.TemporaryDirectory() as tmp:
            session.experiment_report_store = er.ExperimentReportStore(tmp + "/reports.sqlite")
            session.experiment_report_id = None
            socket = harness._turn(session, "acetonitrile이 뭐야?", harness._explainer("유기 용매로, 젤 조각의 물을 빼는 데 흔히 쓰여요."))
        events = harness._of(socket, "experiment.report.state")
        assert events
        assert all(event["configuration_id"] == 41 and event["generation"] == session.generation
                   and event["turn_id"] == 1 for event in events)
