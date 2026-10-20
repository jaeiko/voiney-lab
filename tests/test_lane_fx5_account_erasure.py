"""Lane FX5, decision 2 (2026-10-20): one scope of an account's data, for the tool and the server.

An account that deletes itself (``POST /api/auth/delete-account``) and the
operator's ``scripts/pilot_data_delete.py --account`` remove the same things:
the account's experiment sessions with their events, steps, observations
and evidence files, the experiment records bound to it, its pilot metrics
rows, its settings, access records and ownership rows, and the account with
its sessions and login attempts. What the lab shares stays -- the protocol
list, a review version and the PI confirmations left on a version or on
another person's notebook -- but the name written there becomes
"(삭제된 계정)". The leak scan below reads every table of every store and
lists where the name or the account's ids are still found.
Every store is made by the product's own code in a temporary folder.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tests.test_lane_pl_pilot_data_delete import load_tool
from tests.test_lane_rv_review_pending import (
    PAGES, _RawModel, clear_protocol_pdf_cache, extract_protocol_pdf, gamma_response, write_pages_pdf,
)
from voiney_lab import accounts as acc
from voiney_lab.experiment_protocol_config import ProtocolPersistenceSettings
from voiney_lab.experiment_protocol_store import initialize_protocol_store
from voiney_lab.experiment_reports import ExperimentReportStore
from voiney_lab.pilot_metrics import FeedbackAnswer, PilotMetricsStore, TurnRecord
from voiney_lab.protocol_catalog import ProtocolCatalog
from voiney_lab.workspace_store import WorkspaceSettings, initialize_workspace_store

PASSWORD = "correct horse battery"
GONE = "(삭제된 계정)"


def scan(folder: Path, needles: dict[str, str]) -> dict[str, set[tuple[str, str, str]]]:
    """Where in any SQLite file under ``folder`` each needle is still found: (file, table, column)."""

    found: dict[str, set[tuple[str, str, str]]] = {label: set() for label in needles}
    for path in sorted(folder.rglob("*.sqlite")):
        connection = sqlite3.connect(path)
        try:
            tables = [row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            for table in tables:
                for column in [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')]:
                    for label, text in needles.items():
                        hit = connection.execute(
                            f'SELECT 1 FROM "{table}" WHERE instr(CAST("{column}" AS TEXT), ?)>0 LIMIT 1',
                            (text,)).fetchone()
                        if hit:
                            found[label].add((path.name, table, column))
        finally:
            connection.close()
    return found


class _PilotWithTwoPeople(unittest.TestCase):
    """A pilot folder in the real layout: A (administrator) and B, each with an experiment."""

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.addCleanup(self._temp.cleanup)
        self.tool = load_tool(self.root)
        self.pilot = self.root / "data" / "runtime" / "pilot"
        self.pilot.mkdir(parents=True)
        (self.pilot / self.tool.PILOT_MARKER).write_text("pilot\n")
        self.workspace = self.pilot / "workspace"
        clear_protocol_pdf_cache()
        self.addCleanup(clear_protocol_pdf_cache)
        self.accounts = acc.AccountStore.open(self.workspace / acc.ACCOUNTS_DATABASE_FILENAME)
        self.store = initialize_workspace_store(WorkspaceSettings(enabled=True, data_dir=self.workspace))
        self.reports = ExperimentReportStore(self.pilot / "experiment_reports.sqlite")
        self.protocols = initialize_protocol_store(ProtocolPersistenceSettings(True, self.pilot))
        self.catalog = ProtocolCatalog(self.protocols)
        self.metrics = PilotMetricsStore(self.workspace / "pilot_metrics.sqlite")
        pdf = self.root / "gamma.pdf"
        write_pages_pdf(pdf, PAGES, title="Protocol Gamma")
        sha256 = extract_protocol_pdf(pdf).sha256
        self.protocol_id = self.catalog.register(
            pdf, source_filename="gamma.pdf", media_type="application/pdf").entry.protocol_id
        analysis_id = "analysis-" + "a" * 32
        self.catalog.request_analysis(self.protocol_id, analysis_id)
        self.catalog.analyze(self.protocol_id, _RawModel(gamma_response(sha256, pages=(1, 2, 3))),
                             analysis_id=analysis_id)
        self.a = self._person("김재준", admin=True)
        self.b = self._person("최수진")
        self.report_a = self._experiment(self.a, "exp-a")
        self.report_b = self._experiment(self.b, "exp-b")
        self.store.bind_resource(self.a.principal(), "protocol_catalog", self.protocol_id)
        # A registers a review version of the shared protocol, and leaves a PI confirmation on it
        # and one on B's notebook.
        self.catalog.record_review_decision(self.protocol_id, {
            "item_id": "step:s2", "action": "fix", "text": "2. Incubate for 15 min at 37 C.",
            "reviewer_name": self.a.name})
        self.catalog.register_review(self.protocol_id, {"reviewer_name": self.a.name})
        self.catalog.confirm_review_version(self.protocol_id, 1, name=self.a.name, comment="확인했습니다")
        self.reports.append_event(
            self.report_b, event_key="pi-confirmed-1", event_type="pi_confirmed", user_wording="좋아요",
            payload={"name": self.a.name, "comment": "좋아요", "source": "screen"})

    def tearDown(self) -> None:
        for closer in (self.accounts.close, self.store.close, self.protocols.close):
            try:
                closer()
            except Exception:  # noqa: BLE001 - a test may have closed it already
                pass

    def _person(self, name: str, *, admin: bool = False) -> acc.Account:
        token, _ = self.accounts.create_invite(admin=admin)
        account = self.accounts.sign_up(token, name, PASSWORD)
        self.accounts.open_session(account)
        try:
            self.accounts.log_in(name, "wrong wrong wrong")
        except acc.LoginRefusedError:
            pass
        principal = account.principal()
        self.store.bootstrap_principal(principal)
        self.store.record_workspace_access(principal)
        self.store.record_experimenter_setting(principal, name="confirm_mode", value="quiet", source="screen")
        return account

    def _experiment(self, account: acc.Account, session_id: str) -> str:
        principal = account.principal()
        self.store.start_experiment(
            principal, protocol_id=self.protocol_id, protocol_revision_id="pdf-1",
            session_id=session_id, current_step_id="step-1", current_step_label="1")
        self.store.transition_experiment(
            principal, session_id, action="resume", expected_version=1, event_key=f"{session_id}-go")
        report = self.reports.open_report(
            session_id=session_id, protocol_id=self.protocol_id, protocol_title="Shared",
            protocol_revision="pdf-1", protocol_sha256="a" * 64, readiness_status="guidance_ready",
            development_only=False)
        self.reports.append_event(report["report_id"], event_key="experimenter", event_type="experimenter_recorded",
                                  payload={"display_name": account.name})
        self.store.bind_resource(principal, "experiment_report", report["report_id"])
        self.store.record_observation(
            principal, session_id, event_key=f"{session_id}-obs", content="sample looks cloudy",
            category="appearance", capture_source="manual")
        file = self.workspace / "evidence" / "lab" / session_id / "photo.png"
        file.parent.mkdir(parents=True)
        file.write_bytes(b"\x89PNG not really")
        self.store.record_evidence(
            principal, session_id, event_key=f"{session_id}-ev", evidence_kind="image",
            original_filename="photo.png", media_type="image/png", byte_size=file.stat().st_size,
            sha256="b" * 64, storage_reference=f"evidence/lab/{session_id}/photo.png")
        self.metrics.record_turn(TurnRecord(
            account_id=principal.principal_id, session_id=session_id, report_id=report["report_id"],
            turn_id=1, router="front", reason="none", action="repeat", source="voice", state_changed=False,
            step_advanced=False, clarified=False, first_audio_ms=300, total_ms=900))
        self.metrics.record_feedback(
            account_id=principal.principal_id,
            answer=FeedbackAnswer(report_id=report["report_id"], rating=5, skipped=False, note=None))
        return str(report["report_id"])

    def locations(self) -> acc.AccountDataLocations:
        return acc.AccountDataLocations.pilot_folder(self.pilot)

    def needles(self) -> dict[str, str]:
        return {
            "name": self.a.name,
            "principal": self.a.principal().principal_id,
            "account_id": self.a.account_id,
        }

    def leaks(self) -> dict[str, set[tuple[str, str, str]]]:
        return scan(self.pilot, self.needles())

    def counts(self, path: Path, table: str, where: str = "", parameters=()) -> int:
        connection = sqlite3.connect(path)
        try:
            return connection.execute(f"SELECT COUNT(*) FROM {table} {where}", parameters).fetchone()[0]
        finally:
            connection.close()


#: What of A may stay, as an opaque id and never with the name: the lab's shared protocol list says who owns a
#: protocol, the lab's settings row says who first set them, and the identity row both point at stays, blank.
ALLOWED_PRINCIPAL_PLACES = {
    ("commercial_workspace.sqlite", "principals", "principal_id"),
    ("commercial_workspace.sqlite", "resource_bindings", "owner_principal_id"),
    ("commercial_workspace.sqlite", "organization_settings", "updated_by_principal_id"),
}


class ErasureCoreTests(_PilotWithTwoPeople):
    def test_before_the_erasure_the_scan_finds_the_account_everywhere_it_should(self) -> None:
        found = self.leaks()
        self.assertGreater(len(found["name"]), 3)
        self.assertGreater(len(found["principal"]), 5)
        self.assertIn(("pilot_metrics.sqlite", "pilot_turns", "account_id"), found["principal"])
        self.assertIn(("protocol_workspace.sqlite", "protocol_events", "payload_json"), found["name"])
        self.assertIn(("experiment_reports.sqlite", "experiment_report_events", "payload"), found["name"])

    def test_erasing_one_account_leaves_only_what_the_lab_shares(self) -> None:
        results = acc.erase_account_data(self.locations(), self.a.account_id)
        self.assertTrue(all(item["result"] == "deleted" for item in results), results)
        found = self.leaks()
        self.assertEqual(found["name"], set(), "the name is written nowhere")
        self.assertEqual(found["account_id"] - found["principal"], set(),
                         "the bare account id is written nowhere but inside the principal id")
        self.assertEqual(found["principal"], ALLOWED_PRINCIPAL_PLACES)
        workspace = self.workspace / "commercial_workspace.sqlite"
        connection = sqlite3.connect(workspace)
        try:
            name, subject = connection.execute(
                "SELECT display_name,subject FROM principals WHERE principal_id=?",
                (self.a.principal().principal_id,)).fetchone()
        finally:
            connection.close()
        self.assertEqual(name, GONE)
        self.assertNotIn(self.a.name, subject)
        self.assertFalse((self.workspace / "evidence" / "lab" / "exp-a").exists())

    def test_the_other_account_loses_nothing(self) -> None:
        before = {
            "sessions": self.counts(self.workspace / "commercial_workspace.sqlite", "experiment_sessions",
                                    "WHERE session_id='exp-b'"),
            "evidence": (self.workspace / "evidence" / "lab" / "exp-b" / "photo.png").read_bytes(),
            "turns": self.counts(self.workspace / "pilot_metrics.sqlite", "pilot_turns",
                                 "WHERE account_id=?", (self.b.principal().principal_id,)),
        }
        acc.erase_account_data(self.locations(), self.a.account_id)
        self.assertEqual(before["sessions"], 1)
        self.assertEqual(self.counts(self.workspace / "commercial_workspace.sqlite", "experiment_sessions",
                                     "WHERE session_id='exp-b'"), 1)
        self.assertEqual((self.workspace / "evidence" / "lab" / "exp-b" / "photo.png").read_bytes(),
                         before["evidence"])
        self.assertEqual(self.counts(self.workspace / "pilot_metrics.sqlite", "pilot_turns",
                                     "WHERE account_id=?", (self.b.principal().principal_id,)), before["turns"])
        self.assertEqual(self.counts(self.pilot / "experiment_reports.sqlite", "experiment_reports",
                                     "WHERE report_id=?", (self.report_b,)), 1)
        accounts = acc.AccountStore.open(self.workspace / acc.ACCOUNTS_DATABASE_FILENAME)
        try:
            self.assertEqual([item.name for item in accounts.list_accounts()], ["최수진"])
            self.assertEqual(accounts.log_in("최수진", PASSWORD).name, "최수진")
        finally:
            accounts.close()

    def test_a_shared_review_version_and_pi_confirmations_stay_under_the_deleted_name(self) -> None:
        acc.erase_account_data(self.locations(), self.a.account_id)
        events = [event.payload for event in self.protocols.list_events(self.protocol_id)]
        registered = [item for item in events if item.get("status") == "review_registered"]
        self.assertEqual(len(registered), 1, "the review version stays")
        self.assertEqual(registered[0]["reviewer_name"], GONE)
        self.assertTrue(all(change.get("decided_by") in (None, GONE) for change in registered[0]["changes"]))
        decisions = [item for item in events if item.get("action") == "fix"]
        self.assertEqual([item["reviewer_name"] for item in decisions], [GONE])
        confirmed = [item for item in events if item.get("comment") == "확인했습니다"]
        self.assertEqual([item["name"] for item in confirmed], [GONE])
        self.assertEqual(confirmed[0]["comment"], "확인했습니다", "what was written stays; only the name changes")
        report = self.reports.get_report(self.report_b)
        (pi,) = [event for event in report["events"] if event["event_type"] == "pi_confirmed"]
        self.assertEqual(pi["payload"]["name"], GONE)
        self.assertEqual(pi["payload"]["comment"], "좋아요")

    def test_the_append_only_guards_are_back_after_the_erasure(self) -> None:
        def guards() -> int:
            total = 0
            for path in (self.workspace / "commercial_workspace.sqlite", self.pilot / "protocol_workspace.sqlite"):
                total += self.counts(path, "sqlite_master", "WHERE type='trigger'")
            return total

        before = guards()
        acc.erase_account_data(self.locations(), self.a.account_id)
        self.assertEqual(guards(), before)
        with self.assertRaises(sqlite3.DatabaseError):
            connection = sqlite3.connect(self.pilot / "protocol_workspace.sqlite")
            try:
                connection.execute("UPDATE protocol_events SET payload_json='{}'")
            finally:
                connection.close()

    def test_a_name_in_a_free_sentence_is_left_and_only_the_name_fields_change(self) -> None:
        connection = sqlite3.connect(self.pilot / "experiment_reports.sqlite")
        try:
            connection.execute(
                "UPDATE experiment_report_events SET user_wording=? WHERE event_key='pi-confirmed-1'",
                ("김재준 선생님이 봤어요",))
            connection.commit()
        finally:
            connection.close()
        acc.erase_account_data(self.locations(), self.a.account_id)
        report = self.reports.get_report(self.report_b)
        (pi,) = [event for event in report["events"] if event["event_type"] == "pi_confirmed"]
        self.assertEqual(pi["payload"]["name"], GONE)
        self.assertIn("김재준", pi["user_wording"], "free words are not rewritten; the report says so")

    def test_an_unknown_account_is_refused_and_nothing_changes(self) -> None:
        with self.assertRaises(acc.AccountNotFoundError):
            acc.erase_account_data(self.locations(), "0123456789abcdef")
        self.assertEqual(self.counts(self.workspace / "commercial_workspace.sqlite", "experiment_sessions"), 2)

    def test_the_record_of_a_deletion_holds_categories_and_counts_only(self) -> None:
        results = acc.erase_account_data(self.locations(), self.a.account_id)
        log = self.pilot / acc.DELETION_LOG_NAME
        acc.write_deletion_log(log, word="password", results=results, scope="account", via="app")
        line = log.read_text(encoding="utf-8").strip()
        record = json.loads(line)
        self.assertEqual((record["scope"], record["via"], record["result"]), ("account", "app", "deleted"))
        self.assertEqual(
            sorted(item["category"] for item in record["categories"]),
            ["account_store", "experiment_records", "pilot_metrics", "protocol_catalog_names",
             "report_pi_names", "workspace_evidence_files", "workspace_records"])
        for secret in (self.a.name, self.a.account_id, self.a.principal().principal_id, PASSWORD):
            self.assertNotIn(secret, line)


class ToolAndErasureShareOneScopeTests(_PilotWithTwoPeople):
    def test_the_tool_leaves_the_same_places_as_the_erasure(self) -> None:
        import io
        from contextlib import redirect_stderr, redirect_stdout

        def run(*arguments: str) -> tuple[int, str]:
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                return self.tool.main(["--data-dir", str(self.pilot), *arguments]), out.getvalue()

        self.accounts.close()
        self.store.close()
        self.protocols.close()
        mirror = self.root / "mirror"
        shutil.copytree(self.pilot, mirror)
        code, preview = run("--account", "김재준")
        self.assertEqual(code, 0, preview)
        self.assertIn("이름만 바꿈", preview)
        word = preview.split("--confirm ")[1].split()[0]
        code, out = run("--account", "김재준", "--delete", "--confirm", word)
        self.assertEqual(code, 0, out)
        by_tool = self.leaks()
        acc.erase_account_data(acc.AccountDataLocations.pilot_folder(mirror), self.a.account_id)
        by_core = scan(mirror, self.needles())
        self.assertEqual(by_tool, by_core)
        self.assertEqual(by_tool["name"], set())
        self.assertEqual(by_tool["principal"], ALLOWED_PRINCIPAL_PLACES)


if __name__ == "__main__":
    unittest.main()
