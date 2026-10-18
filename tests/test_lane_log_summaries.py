"""Lane LOG, decision 2: one summary row per experiment and per analysis.

An experiment's row joins what the experiment record already keeps (start and
end, steps, records, deviations, anomalies, timer notices, the report's prose)
with what the pilot metrics store adds (turns, the router's results, steps
passed by voice or by a tapped candidate, the time from a timer notice to the
experimenter's next words, connections lost, reports exported). An analysis's
row: its result (passed, review pending, a notice, failed), how long it took,
how many review cards it left, and how long until the review was registered.

Counts, codes and times only. Records written before this lane (no metrics
store) still give their experiment rows from the record alone.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import voiney_lab.server as server_module
from tests.lane_n_support import VoiceNotesHarness
from voiney_lab.experiment_reports import ExperimentReportStore
from voiney_lab.server import LockedSender


def _pilot():
    import voiney_lab.pilot_metrics as pilot

    return pilot


class ExperimentRowTests(VoiceNotesHarness, unittest.TestCase):
    def _sources(self):
        pilot = _pilot()
        return pilot.PilotSources(
            metrics_db=self.workspace_dir / pilot.PILOT_METRICS_FILENAME,
            reports_db=self.report_db,
            workspace_db=self.workspace_dir / "commercial_workspace.sqlite",
        )

    def test_a_voice_experiment_gives_one_row_of_counts(self) -> None:
        self.turns(
            "프로토콜 시작해줘", "1단계 완료했어", "메모해 줘 튜브 라벨 A-170",
            "시약을 흘렸다고 기록해 줘",
        )
        (row,) = _pilot().experiment_rows(self._sources())
        self.assertEqual(row["report_id"], self.report()["report_id"])
        self.assertEqual(row["account_id"], self.principal.principal_id)
        self.assertEqual(row["protocol_id"], "lane-n-wash")
        self.assertEqual(row["status"], "in_progress")
        self.assertEqual((row["steps"], row["steps_by_voice"], row["steps_by_button"]), (1, 1, 0))
        self.assertEqual(row["hands_free_ratio"], 1.0)
        self.assertEqual(row["turns"], 4)
        self.assertEqual(row["router_fallbacks"], 0)
        self.assertEqual(row["records"], 1)
        self.assertEqual(row["deviations"], 0)
        self.assertEqual(row["anomalies"], 1)
        self.assertEqual(row["report_made"], 0)
        # The socket closed with the experiment still running.
        self.assertEqual(row["disconnects"], 1)
        self.assertEqual(row["account_experiment_number"], 1)
        self.assertEqual(row["protocol_run_number"], 1)
        for value in row.values():
            self.assertNotIsInstance(value, (list, dict))
            if isinstance(value, str):
                for word in ("튜브", "A-170", "시약", "Wash", "Cut"):
                    self.assertNotIn(word, value)

    def test_a_connection_lost_counts_until_the_next_connection(self) -> None:
        self.turns("프로토콜 시작해줘", "1단계 완료했어")
        time.sleep(0.05)
        self.turns("프로토콜 시작해줘")
        rows = _pilot().experiment_rows(self._sources())
        self.assertEqual(len(rows), 2)
        first = rows[0]
        self.assertEqual(first["disconnects"], 1)
        self.assertIsInstance(first["disconnected_ms"], int)
        self.assertGreaterEqual(first["disconnected_ms"], 40)
        self.assertEqual(rows[1]["account_experiment_number"], 2)
        self.assertEqual(rows[1]["protocol_run_number"], 2)

    def test_a_timer_notice_and_the_next_words_are_timed(self) -> None:
        async def scenario(socket, listener, say):
            await say(1, "프로토콜 시작해줘")
            await say(2, "1단계 완료했어")
            await say(3, "타이머 시작해줘")
            curated = listener.curated_protocol_session
            (notice,) = curated.due_timer_notices(now=time.time() + 600)

            async def at_once(_seconds: float) -> None:
                return None

            await server_module._deliver_timer_notice(
                listener, LockedSender(socket), curated, notice,
                sleep=at_once, playback_sleep=at_once)
            listener.playback_ended(listener.active_turn_id)
            listener.cooldown_until = 0
            await say(4, "남은 시간 알려줘")

        self.voice(scenario)
        (row,) = _pilot().experiment_rows(self._sources())
        self.assertEqual(row["timer_end_alerts"], 1)
        self.assertEqual(row["timer_last_minute_alerts"], 0)
        self.assertIsInstance(row["alert_answer_median_ms"], int)

    def test_a_report_exported_counts_as_made(self) -> None:
        self.turns("프로토콜 시작해줘", "1단계 완료했어")
        report_id = self.report()["report_id"]
        with patch.dict("os.environ", self.environment, clear=False), \
                patch("voiney_lab.server._report_writer_brain", return_value=None):
            client = TestClient(server_module.app)
            timeline = client.get(f"/api/experiment-reports/{report_id}.csv")
            (row,) = _pilot().experiment_rows(self._sources())
            self.assertEqual(row["report_made"], 0, "the timeline CSV is not the report")
            response = client.get(f"/api/experiment-reports/{report_id}.md")
        self.assertEqual((timeline.status_code, response.status_code), (200, 200))
        (row,) = _pilot().experiment_rows(self._sources())
        self.assertEqual(row["report_made"], 1)

    def test_records_from_before_the_metrics_store_still_give_rows(self) -> None:
        self.turns("프로토콜 시작해줘", "1단계 완료했어")
        pilot = _pilot()
        (self.workspace_dir / pilot.PILOT_METRICS_FILENAME).unlink()
        (row,) = pilot.experiment_rows(self._sources())
        self.assertEqual(row["account_id"], self.principal.principal_id)
        self.assertEqual((row["steps"], row["steps_by_voice"]), (1, 1))
        self.assertIsNone(row["turns"])

    def test_the_summary_never_writes_to_the_folder_it_reads(self) -> None:
        self.turns("프로토콜 시작해줘", "1단계 완료했어")
        with tempfile.TemporaryDirectory() as copy:
            folder = Path(copy) / "pilot"
            (folder / "workspace").mkdir(parents=True)
            shutil.copy(self.report_db, folder / "experiment_reports.sqlite")
            for item in self.workspace_dir.iterdir():
                shutil.copy(item, folder / "workspace" / item.name)
            before = {path.name: path.stat().st_mtime_ns for path in folder.rglob("*")}
            rows = _pilot().experiment_rows(_pilot().PilotSources.from_data_dir(folder))
            after = {path.name: path.stat().st_mtime_ns for path in folder.rglob("*")}
        self.assertEqual(len(rows), 1)
        self.assertEqual(after, before)


class LedgerCountTests(unittest.TestCase):
    def test_records_deviations_and_anomalies_are_counted_by_kind_only(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            store = ExperimentReportStore(Path(folder) / "reports.sqlite")
            report = store.open_report(
                session_id="session-lane-log", protocol_id="lane-n-wash",
                protocol_title="Fictional wash", protocol_revision="r1",
                protocol_sha256="0" * 64, readiness_status="guidance_ready",
                development_only=True,
            )
            report_id = report["report_id"]
            for key, kind, category in (
                ("a", "observation", "measurement"), ("b", "observation", "note"),
                ("c", "observation", "deviation"), ("d", "observation", "deviation"),
                ("e", "anomaly", "spill"), ("f", "system_anomaly", None),
                ("g", "record_retracted", None), ("h", "step_completed", None),
                ("i", "step_advanced", None), ("j", "step_reverted", None),
                ("k", "timer_last_minute_notice", None), ("l", "timer_end_notice", None),
            ):
                store.append_event(
                    report_id, event_key=key, event_type=kind, category=category,
                    user_wording="말한 그대로의 글" if kind == "observation" else None,
                )
            (row,) = store.summary_rows()
        self.assertEqual(
            {name: row[name] for name in (
                "records", "deviations", "anomalies", "system_anomalies", "corrections",
                "steps", "reverts", "timer_last_minute_alerts", "timer_end_alerts",
                "report_prose")},
            {"records": 2, "deviations": 2, "anomalies": 1, "system_anomalies": 1,
             "corrections": 1, "steps": 2, "reverts": 1, "timer_last_minute_alerts": 1,
             "timer_end_alerts": 1, "report_prose": False},
        )
        self.assertNotIn("말한 그대로의 글", repr(row))


class AnalysisRowTests(unittest.TestCase):
    PROTOCOL = "protocol-lane-log"

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.workspace_dir = Path(tmp.name) / "workspace"
        self.report_db = Path(tmp.name) / "reports.sqlite"
        self.environment = {
            "VOINEY_LAB_WORKSPACE_ENABLED": "true",
            "VOINEY_LAB_WORKSPACE_DATA_DIR": str(self.workspace_dir),
            "VOINEY_LAB_EXPERIMENT_REPORTS_ENABLED": "false",
            "VOINEY_LAB_USAGE_SCOPE": "demo",
        }

    def _catalog(self, public: dict | Exception):
        def analyze(*_args, **_kwargs):
            if isinstance(public, Exception):
                raise public
            return SimpleNamespace(public_dict=lambda: dict(public))

        catalog = SimpleNamespace(
            request_analysis=lambda *args, **kwargs: None,
            analyze=analyze,
            analysis_run_status=lambda *_args: SimpleNamespace(public_dict=lambda: {}),
            fail_analysis_request=lambda *args, **kwargs: None,
            register_review=lambda protocol_id, body: {"version": 1},
        )
        return catalog, SimpleNamespace(close=lambda: None)

    def _analyse(self, public: dict | Exception) -> None:
        async def immediate(function, *args, **kwargs):
            return function(*args, **kwargs)

        with patch.dict("os.environ", self.environment, clear=False), \
                patch("voiney_lab.server._open_protocol_catalog", return_value=self._catalog(public)), \
                patch("voiney_lab.server._protocol_analysis_model", return_value=object()), \
                patch("voiney_lab.server._scope_catalog_resource"), \
                patch("voiney_lab.server.asyncio.to_thread", side_effect=immediate):
            try:
                asyncio.run(server_module.trigger_protocol_analysis(self.PROTOCOL, background=False))
            except Exception:  # noqa: BLE001 -- the failure is what is recorded
                pass

    def _rows(self):
        pilot = _pilot()
        return pilot.analysis_rows(pilot.PilotSources(
            metrics_db=self.workspace_dir / pilot.PILOT_METRICS_FILENAME,
            reports_db=None, workspace_db=None,
        ))

    def test_each_result_is_one_row_with_its_time_and_cards(self) -> None:
        self._analyse({
            "protocol_id": self.PROTOCOL, "revision_id": "rev-1",
            "analysis_status": "review_pending", "available_for_execution": False,
            "review_pending": {"needs_review_count": 7},
        })
        self._analyse({
            "protocol_id": self.PROTOCOL, "revision_id": "rev-1",
            "analysis_status": "review_required", "available_for_execution": True,
        })
        self._analyse({
            "protocol_id": self.PROTOCOL, "revision_id": "rev-1",
            "analysis_status": "review_required", "available_for_execution": False,
            "execution_blocker_codes": ["source_evidence_missing"],
        })
        self._analyse(RuntimeError("provider down"))
        rows = self._rows()
        self.assertEqual(
            [(row["outcome"], row["card_count"]) for row in rows],
            [("review_pending", 7), ("passed", 0), ("notice", 0), ("failed", 0)],
        )
        for row in rows:
            self.assertEqual(row["protocol_id"], self.PROTOCOL)
            self.assertIsInstance(row["duration_ms"], int)

    def test_the_time_until_the_review_is_registered(self) -> None:
        self._analyse({
            "protocol_id": self.PROTOCOL, "revision_id": "rev-1",
            "analysis_status": "review_pending", "available_for_execution": False,
            "review_pending": {"needs_review_count": 2},
        })
        with patch.dict("os.environ", self.environment, clear=False), \
                patch("voiney_lab.server._open_protocol_catalog",
                      return_value=self._catalog({})), \
                patch("voiney_lab.server._scope_catalog_resource"):
            server_module.post_protocol_review_version(self.PROTOCOL, {"reviewer": "x"})
        (row,) = self._rows()
        self.assertIsInstance(row["review_registered_after_s"], int)
        self.assertEqual(row["executed"], 0)


class OutcomeTests(unittest.TestCase):
    def test_the_outcome_codes(self) -> None:
        outcome = _pilot().analysis_outcome
        self.assertEqual(outcome({"analysis_status": "review_pending"}), "review_pending")
        self.assertEqual(outcome({"available_for_execution": True}), "passed")
        self.assertEqual(outcome({"available_for_execution": False}), "notice")


if __name__ == "__main__":
    unittest.main()
