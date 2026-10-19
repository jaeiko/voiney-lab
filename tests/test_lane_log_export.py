"""Lane LOG, decision 4: the CSV export and the admin screen.

``scripts/pilot_metrics.py`` writes one CSV per kind -- experiments, analyses,
days, and the rating memos (notes) -- for a period and an account, reading
copies of the pilot data folder so the folder is left as it was. The admin
screen (``static/admin-metrics.html``) shows the day table from
``GET /api/admin/pilot-metrics``, which needs the admin token or an admin
account and carries numbers, codes and times only.
"""

from __future__ import annotations

import csv
import io
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import voiney_lab.server as server_module
from tests.lane_n_support import VoiceNotesHarness
from tests.test_screen_cleanup import PRELUDE, ROOT

SCRIPT = ROOT / "scripts" / "pilot_metrics.py"
PAGE = Path(server_module.__file__).with_name("static") / "admin-metrics.html"
TOKEN = "lane-log-test-admin-token"


def _pilot():
    import voiney_lab.pilot_metrics as pilot

    return pilot


class _Collected(VoiceNotesHarness):
    """Two experiments and one rating with a memo, as a pilot folder holds them."""

    def setUp(self) -> None:
        super().setUp()
        self.turns("프로토콜 시작해줘", "1단계 완료했어")
        first = self.report()["report_id"]
        with patch.dict("os.environ", self.environment, clear=False):
            TestClient(server_module.app).post(
                "/api/pilot/feedback",
                json={"report_id": first, "rating": 5, "note": "타이머 알림이 좋았어요"})
        self.turns("프로토콜 시작해줘")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name) / "pilot"
        (self.folder / "workspace").mkdir(parents=True)
        shutil.copy(self.report_db, self.folder / "experiment_reports.sqlite")
        for item in self.workspace_dir.iterdir():
            shutil.copy(item, self.folder / "workspace" / item.name)

    def export(self, *arguments: str) -> list[dict[str, str]]:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), *arguments, "--data-dir", str(self.folder)],
            cwd=ROOT, text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.header = next(csv.reader(io.StringIO(result.stdout)))
        return list(csv.DictReader(io.StringIO(result.stdout)))


class ExportTests(_Collected, unittest.TestCase):
    def test_each_kind_has_its_columns_and_rows(self) -> None:
        pilot = _pilot()
        experiments = self.export("experiments")
        self.assertEqual(tuple(self.header), pilot.EXPERIMENT_COLUMNS)
        self.assertEqual(len(experiments), 2)
        self.assertEqual(experiments[0]["rating"], "5")
        self.assertEqual(experiments[0]["account_id"], self.principal.principal_id)
        days = self.export("days")
        self.assertEqual(tuple(self.header), pilot.DAY_COLUMNS)
        (day,) = days
        self.assertEqual((day["experiments"], day["steps"], day["ratings"]), ("2", "1", "1"))
        self.assertEqual(float(day["rating_mean"]), 5.0)
        self.export("analyses")
        self.assertEqual(tuple(self.header), pilot.ANALYSIS_COLUMNS)

    def test_the_memos_come_only_as_notes(self) -> None:
        for kind in ("experiments", "days", "analyses"):
            result = subprocess.run(
                [sys.executable, str(SCRIPT), kind, "--data-dir", str(self.folder)],
                cwd=ROOT, text=True, capture_output=True)
            self.assertNotIn("타이머 알림이", result.stdout, kind)
        (note,) = self.export("notes")
        self.assertEqual((note["rating"], note["note"]), ("5", "타이머 알림이 좋았어요"))

    def test_the_period_and_the_account_narrow_the_rows(self) -> None:
        day = self.export("experiments")[0]["day"]
        self.assertEqual(len(self.export("experiments", "--from", day, "--to", day)), 2)
        self.assertEqual(self.export("experiments", "--from", "2099-01-01"), [])
        self.assertEqual(self.export("experiments", "--account", "someone-else"), [])
        self.assertEqual(
            len(self.export("experiments", "--account", self.principal.principal_id)), 2)

    def test_the_folder_is_left_as_it_was(self) -> None:
        before = {path.name: path.stat().st_mtime_ns for path in self.folder.rglob("*")}
        self.export("experiments")
        self.export("days")
        after = {path.name: path.stat().st_mtime_ns for path in self.folder.rglob("*")}
        self.assertEqual(after, before)

    def test_a_file_is_written_for_a_spreadsheet(self) -> None:
        with tempfile.TemporaryDirectory() as out:
            target = Path(out) / "days.csv"
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "days", "--data-dir", str(self.folder),
                 "--out", str(target)], cwd=ROOT, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(target.read_bytes().startswith("﻿".encode("utf-8")))


class AdminRouteTests(_Collected, unittest.TestCase):
    def _get(self, token: str | None, environment: dict | None = None, query: str = ""):
        headers = {"x-voice-workflow-admin-token": token} if token else {}
        with patch.dict("os.environ", environment or self.environment, clear=False):
            return TestClient(server_module.app).get(
                f"/api/admin/pilot-metrics{query}", headers=headers)

    def test_closed_without_the_token_and_refused_with_a_wrong_one(self) -> None:
        self.assertEqual(self._get(TOKEN).status_code, 503)
        environment = {**self.environment, "VOINEY_LAB_ADMIN_TOKEN": TOKEN}
        self.assertEqual(self._get(None, environment).status_code, 403)
        self.assertEqual(self._get("not-the-token", environment).status_code, 403)

    def test_the_token_gets_the_day_table_of_numbers_only(self) -> None:
        environment = {**self.environment, "VOINEY_LAB_ADMIN_TOKEN": TOKEN}
        response = self._get(TOKEN, environment)
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(tuple(payload["columns"]), _pilot().DAY_COLUMNS)
        (day,) = payload["days"]
        self.assertEqual((day["experiments"], day["ratings"]), (2, 1))
        self.assertEqual(payload["totals"]["experiments"], 2)
        self.assertNotIn("타이머 알림이", response.text)
        self.assertNotIn("Wash", response.text)

    def test_a_period_that_is_not_a_date_is_refused(self) -> None:
        environment = {**self.environment, "VOINEY_LAB_ADMIN_TOKEN": TOKEN}
        self.assertEqual(self._get(TOKEN, environment, "?from=yesterday").status_code, 400)
        self.assertEqual(self._get(TOKEN, environment, "?account=../x").status_code, 400)
        empty = self._get(TOKEN, environment, "?from=2099-01-01").json()
        self.assertEqual(empty["days"], [])

    def test_an_admin_account_needs_no_token(self) -> None:
        admin = SimpleNamespace(roles=(SimpleNamespace(value="admin"),))
        researcher = SimpleNamespace(roles=(SimpleNamespace(value="researcher"),))
        token = server_module._REQUEST_PRINCIPAL.set(admin)
        try:
            server_module._require_pilot_admin(None)
        finally:
            server_module._REQUEST_PRINCIPAL.reset(token)
        token = server_module._REQUEST_PRINCIPAL.set(researcher)
        try:
            with patch.dict("os.environ", {"VOINEY_LAB_ADMIN_TOKEN": TOKEN}):
                with self.assertRaises(server_module.HTTPException) as refused:
                    server_module._require_pilot_admin(None)
        finally:
            server_module._REQUEST_PRINCIPAL.reset(token)
        self.assertEqual(refused.exception.status_code, 403)


def run_admin_page(body: str) -> subprocess.CompletedProcess:
    html = PAGE.read_text(encoding="utf-8")
    script = html.split("<script>", 1)[1].split("</script>", 1)[0]
    harness = (
        PRELUDE
        + "globalThis.URLSearchParams=URLSearchParams;\n"
        + script
        + "\n(async()=>{\n" + body
        + "\n})().catch(error=>{console.error(error);process.exit(1)});\n"
    )
    return subprocess.run(["node", "-"], cwd=ROOT, text=True, input=harness, capture_output=True)


class AdminPageTests(unittest.TestCase):
    def test_the_page_sends_the_token_as_a_header_and_draws_the_days(self) -> None:
        result = run_admin_page(r"""
const calls=[];
globalThis.fetch=async(url,options)=>{calls.push({url,options});return {ok:true,status:200,json:async()=>({
 columns:["day","experiments","hands_free_ratio","alert_answer_median_ms","rating_mean"],
 days:[{day:"2026-10-20",experiments:3,hands_free_ratio:0.875,alert_answer_median_ms:12400,rating_mean:4.5}],
 totals:{experiments:3,hands_free_ratio:0.875,rating_mean:4.5}})}};
node("metrics-token").value="secret";node("metrics-from").value="2026-10-20";node("metrics-to").value="2026-10-26";node("metrics-account").value="";
await loadMetrics();
assert(calls.length===1,"one request");
assert(calls[0].url==="/api/admin/pilot-metrics?from=2026-10-20&to=2026-10-26",calls[0].url);
assert(calls[0].options.headers["X-Voice-Workflow-Admin-Token"]==="secret","token header");
const table=visibleText(node("metrics-body"));
for(const text of ["2026-10-20","3","87.5%","12.4","4.5"])assert(table.includes(text),`${text} missing: ${table}`);
assert(visibleText(node("metrics-head")).includes("손 안 쓴 비율"),"Korean column names");
assert(visibleText(node("metrics-totals")).includes("실험"),"totals");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_closed_route_says_how_to_open_it(self) -> None:
        result = run_admin_page(r"""
globalThis.fetch=async()=>({ok:false,status:503,json:async()=>({})});
node("metrics-token").value="secret";
await loadMetrics();
assert(node("metrics-status").textContent.includes("VOINEY_LAB_ADMIN_TOKEN"),node("metrics-status").textContent);
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_page_is_self_contained_and_writes_text_only(self) -> None:
        html = PAGE.read_text(encoding="utf-8")
        self.assertNotIn("innerHTML", html)
        self.assertNotIn("<script src", html)
        self.assertNotIn("localStorage", html)
        self.assertNotIn("sessionStorage", html)


if __name__ == "__main__":
    unittest.main()
