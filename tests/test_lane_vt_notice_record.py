"""Lane VT, decision 8 (2026-10-09): the timer notices are kept in the experiment record.

Each notice the server gives first about the step timer -- its end (decision
1) and its last minute (decision 2) -- is appended to the experiment record
as an event of its own, never changed after: what it said, when it fell due
and when it was said, at which step and of which timer, and whether it was
said aloud or only shown. The report's timeline (CSV) and the screen's event
list name the events in Korean: "타이머 끝 알림", "타이머 1분 전 알림".
"""

from __future__ import annotations

import csv
import io
import tempfile
import unittest
from pathlib import Path

from tests.test_lane_vt_timer_end import T0, _Server
from tests.test_screen_cleanup import run_page_script
from voiney_lab.experiment_reports import EVENT_NAMES_KO, ExperimentReportStore


class _Recorded(_Server):

    def setUp(self) -> None:
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = ExperimentReportStore(Path(tmp.name) / "reports.sqlite")
        report = self.store.open_report(
            session_id="lane-vt-session", protocol_id="lane-n-wash", protocol_title="Fictional wash",
            protocol_revision="r1", protocol_sha256="0" * 64, readiness_status="guidance_ready",
            development_only=True,
        )
        self.listener.experiment_report_store = self.store
        self.listener.experiment_report_id = report["report_id"]

    def events(self):
        return [
            event for event in self.store.get_report(self.listener.experiment_report_id)["events"]
            if event["event_type"].startswith("timer_")
        ]


class RecordTests(_Recorded):

    def test_a_timer_end_said_is_kept_with_what_when_and_which(self) -> None:
        (notice,) = self.curated.due_timer_notices(now=T0 + 600)
        self.assertTrue(self.deliver(notice))
        (event,) = self.events()
        self.assertEqual(event["event_type"], "timer_end_notice")
        self.assertEqual((event["step_id"], event["step_label"]), ("step-2", "2"))
        self.assertEqual(event["user_wording"], None)
        payload = event["payload"]
        self.assertEqual(payload["notice"]["text"], notice.display_text)
        self.assertEqual(payload["notice"]["notice_kind"], "timer_ended")
        self.assertEqual(payload["notice"]["timer"]["name"], "10분")
        self.assertEqual(payload["notice"]["timer"]["duration_seconds"], 600)
        self.assertTrue(payload["notice"]["due_at"].startswith("1970-01-12T13:56:40"))
        self.assertTrue(payload["spoken"])
        self.assertEqual(payload["proactive_mode"], "all")
        self.assertEqual(payload["next_step_label"], "3")

    def test_the_last_minute_is_its_own_event(self) -> None:
        (notice,) = self.curated.due_timer_notices(now=T0 + 540)
        self.deliver(notice)
        (event,) = self.events()
        self.assertEqual(event["event_type"], "timer_last_minute_notice")

    def test_shown_only_is_kept_as_not_said(self) -> None:
        self.curated.apply_experimenter_settings({"proactive_mode": "off"})
        (notice,) = self.curated.due_timer_notices(now=T0 + 600)
        self.assertFalse(self.deliver(notice))
        (event,) = self.events()
        self.assertFalse(event["payload"]["spoken"])
        self.assertEqual(event["payload"]["proactive_mode"], "off")

    def test_appended_once(self) -> None:
        (notice,) = self.curated.due_timer_notices(now=T0 + 600)
        self.deliver(notice)
        self.deliver(notice)
        self.assertEqual(len(self.events()), 1)

    def test_the_timeline_names_it_in_korean(self) -> None:
        (notice,) = self.curated.due_timer_notices(now=T0 + 600)
        self.deliver(notice)
        rows = list(csv.DictReader(io.StringIO(
            self.store.export_csv(self.listener.experiment_report_id).decode("utf-8-sig"))))
        names = {row["event_type"]: row["event_name_ko"] for row in rows}
        self.assertEqual(names["timer_end_notice"], "타이머 끝 알림")


class WithoutARecordTests(_Server):

    def test_no_report_store_is_no_error(self) -> None:
        (notice,) = self.curated.due_timer_notices(now=T0 + 600)
        self.assertTrue(self.deliver(notice))


class NamesTests(unittest.TestCase):

    def test_the_names(self) -> None:
        self.assertEqual(EVENT_NAMES_KO["timer_end_notice"], "타이머 끝 알림")
        self.assertEqual(EVENT_NAMES_KO["timer_last_minute_notice"], "타이머 1분 전 알림")
        self.assertEqual(EVENT_NAMES_KO["timer_started"], "타이머 시작")

    def test_the_screen_names_them_too(self) -> None:
        result = run_page_script(r"""
assert(serverCodeText("experiment_report_event","timer_end_notice","이벤트")==="타이머 끝 알림","end notice name");
assert(serverCodeText("experiment_report_event","timer_last_minute_notice","이벤트")==="타이머 1분 전 알림","last minute name");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
