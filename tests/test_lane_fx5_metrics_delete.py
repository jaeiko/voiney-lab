"""Lane FX5, decision 1 (2026-10-20): ``--account`` deletes the account's pilot metrics too.

``scripts/pilot_data_delete.py --account NAME`` used to leave
``pilot_metrics.sqlite`` alone, so one account's turn, mark, protocol-event
and rating rows (the rating's free-text line included) stayed until the whole
folder was deleted. They are now listed in the preview, bound into the
confirmation word, deleted with the rest, and counted -- by table, without
the name or the id -- in the deletion record. Another account's rows stay.
Every store is made by the product's own code in a temporary folder.
"""

from __future__ import annotations

import json
import sqlite3
import unittest
from pathlib import Path

from tests import test_lane_on_account_delete as base
from voiney_lab.pilot_metrics import (
    PILOT_METRICS_FILENAME, FeedbackAnswer, PilotMetricsStore, TurnRecord,
)

NOTE = "다음에는 시약 순서를 바꿔 주세요 5511"


def _turn(account_id: str, turn_id: int) -> TurnRecord:
    return TurnRecord(
        account_id=account_id, session_id="exp-a", report_id="report-1", turn_id=turn_id,
        router="front", reason="none", action="repeat", source="voice", state_changed=False,
        step_advanced=False, clarified=False, first_audio_ms=300, total_ms=900)


class MetricsDeleteTests(unittest.TestCase):
    setUp = base.AccountDeleteTests.setUp
    _person = base.AccountDeleteTests._person
    _experiment = base.AccountDeleteTests._experiment
    run_tool = base.AccountDeleteTests.run_tool
    _counts = base.AccountDeleteTests._counts
    _triggers = base.AccountDeleteTests._triggers

    def _fill_metrics(self) -> Path:
        path = self.workspace / PILOT_METRICS_FILENAME
        store = PilotMetricsStore(path)
        for account, turns in ((self.a, 2), (self.b, 1)):
            account_id = account.principal().principal_id
            for number in range(turns):
                store.record_turn(_turn(account_id, number + 1))
            store.record_mark(account_id=account_id, kind="experiment_opened", session_id="exp-a")
            store.record_protocol_event(
                account_id=account_id, protocol_id="protocol-shared", kind="analysis", outcome="passed",
                duration_ms=1200, card_count=0)
            store.record_feedback(
                account_id=account_id,
                answer=FeedbackAnswer(report_id="report-1", rating=4, skipped=False, note=NOTE))
        return path

    def _rows(self, path: Path, table: str, account: object) -> int:
        return self._counts(path, table, "WHERE account_id=?", (account.principal().principal_id,))

    def _word(self, preview: str) -> str:
        return preview.split("--confirm ")[1].split()[0]

    def test_the_preview_counts_the_accounts_metrics_and_changes_nothing(self):
        path = self._fill_metrics()
        before = path.stat().st_mtime_ns
        code, out, err = self.run_tool("--account", "김재준")
        self.assertEqual(code, 0, err)
        self.assertIn("파일럿 지표", out)
        for label in ("지표 턴 2", "지표 표시 1", "지표 프로토콜 사건 1", "지표 평점 1"):
            self.assertIn(label, out)
        self.assertEqual(path.stat().st_mtime_ns, before)
        self.assertEqual(self._counts(path, "pilot_turns"), 3)
        self.assertFalse((self.pilot / self.tool.LOG_NAME).exists())

    def test_the_confirmation_word_covers_the_metrics_rows(self):
        path = self._fill_metrics()
        _, preview, _ = self.run_tool("--account", "김재준")
        word = self._word(preview)
        PilotMetricsStore(path).record_mark(
            account_id=self.a.principal().principal_id, kind="experiment_opened", session_id="exp-a")
        code, _, err = self.run_tool("--account", "김재준", "--delete", "--confirm", word)
        self.assertEqual(code, 2)
        self.assertIn("바뀌었", err)
        self.assertEqual(self._rows(path, "pilot_marks", self.a), 2, "nothing was deleted")

    def test_deleting_the_account_deletes_its_metrics_and_only_its_metrics(self):
        path = self._fill_metrics()
        _, preview, _ = self.run_tool("--account", "김재준")
        code, out, err = self.run_tool(
            "--account", "김재준", "--delete", "--confirm", self._word(preview))
        self.assertEqual(code, 0, err + out)
        self.assertIn(f"deleted: workspace/{PILOT_METRICS_FILENAME}", out)
        for table in ("pilot_turns", "pilot_marks", "pilot_protocol_events", "pilot_feedback"):
            self.assertEqual(self._rows(path, table, self.a), 0, table)
        self.assertEqual(self._rows(path, "pilot_turns", self.b), 1)
        self.assertEqual(self._rows(path, "pilot_marks", self.b), 1)
        self.assertEqual(self._rows(path, "pilot_protocol_events", self.b), 1)
        self.assertEqual(self._rows(path, "pilot_feedback", self.b), 1)
        connection = sqlite3.connect(path)
        try:
            notes = [row[0] for row in connection.execute("SELECT note FROM pilot_feedback")]
        finally:
            connection.close()
        self.assertEqual(notes, [NOTE], "B's own note stays; A's is gone")
        self.assertEqual(path.read_bytes().count(NOTE.encode("utf-8")), 1, "A's note is not left in free pages")

    def test_the_record_counts_the_metrics_by_table_without_a_name_or_id(self):
        self._fill_metrics()
        _, preview, _ = self.run_tool("--account", "김재준")
        self.run_tool("--account", "김재준", "--delete", "--confirm", self._word(preview))
        line = (self.pilot / self.tool.LOG_NAME).read_text(encoding="utf-8").strip()
        record = json.loads(line)
        metrics = [item for item in record["categories"] if item["category"] == "pilot_metrics"]
        self.assertEqual(len(metrics), 1)
        self.assertEqual(metrics[0]["result"], "deleted")
        self.assertEqual(
            metrics[0]["rows"],
            {"pilot_turns": 2, "pilot_marks": 1, "pilot_protocol_events": 1, "pilot_feedback": 1})
        for secret in ("김재준", self.a.account_id, NOTE):
            self.assertNotIn(secret, line)

    def test_an_account_with_no_metrics_file_is_deleted_as_before(self):
        self.assertFalse((self.workspace / PILOT_METRICS_FILENAME).exists())
        code, preview, err = self.run_tool("--account", "김재준")
        self.assertEqual(code, 0, err)
        self.assertNotIn("파일럿 지표", preview)
        code, out, err = self.run_tool(
            "--account", "김재준", "--delete", "--confirm", self._word(preview))
        self.assertEqual(code, 0, err + out)
        self.assertFalse((self.workspace / PILOT_METRICS_FILENAME).exists(), "no file is made for nothing")

    def test_metrics_of_an_unrelated_account_id_stay_when_the_account_has_none(self):
        path = self.workspace / PILOT_METRICS_FILENAME
        PilotMetricsStore(path).record_turn(_turn(self.b.principal().principal_id, 1))
        _, preview, _ = self.run_tool("--account", "김재준")
        self.assertNotIn("지표 턴", preview)
        self.run_tool("--account", "김재준", "--delete", "--confirm", self._word(preview))
        self.assertEqual(self._counts(path, "pilot_turns"), 1)


if __name__ == "__main__":
    unittest.main()
