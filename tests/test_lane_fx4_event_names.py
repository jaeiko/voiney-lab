"""Lane FX4, decision 2 (2026-10-19): every event the record keeps has its Korean name.

The experiment record's CSV carries each event's Korean name
(``EVENT_NAMES_KO``) in its last column; an event without one left that
column empty -- lane NET's "connection_restored" among them. The name is
"연결 끊김 뒤 다시 연결", and every other event type the server writes to
the record now has its name too. The scan below reads the event types from
the server's writes to the record (``append_event`` on the report store), so
a new event type without a name fails here.
"""

from __future__ import annotations

import ast
import csv
import io
import tempfile
import unittest
from pathlib import Path

import voiney_lab.experiment_reports as reports
import voiney_lab.server as server_module
from voiney_lab.experiment_reports import EVENT_NAMES_KO, ExperimentReportStore

SERVER = Path(server_module.__file__)

#: The event types this lane named (decision 2), with their names.
NAMED_HERE = {
    "connection_restored": "연결 끊김 뒤 다시 연결",
    "photo_attached": "사진 첨부",
    "web_reference": "웹 참고 링크",
    "experimenter_recorded": "실험자 기록",
    "protocol_basis_recorded": "따른 원문 파일 기록",
    "steps_skipped": "단계 건너뜀",
    "step_reverted": "이전 단계로 되돌림",
    "repeat_returned": "반복 구간으로 돌아감",
    "repeat_registered": "반복 횟수 등록",
    "repeat_rounds_declined": "반복 더 하지 않음",
    "repeat_closed": "반복 마침",
    "branch_answered": "조건 답변",
    "branch_steps_skipped": "조건에 따라 단계 건너뜀",
}


def _constants(node: ast.AST) -> set[str]:
    """The string constants an expression can take (an ``a if c else b``
    takes either branch; the condition's own words are not event types)."""

    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, ast.IfExp):
        return _constants(node.body) | _constants(node.orelse)
    return set()


def _assigned(function: ast.AST, name: str) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(function):
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == name for target in node.targets):
            found |= _constants(node.value)
        if isinstance(node, ast.For) and isinstance(node.target, ast.Tuple) and any(
                isinstance(item, ast.Name) and item.id == name for item in node.target.elts):
            # ``for kind,item,label in answered``: the kinds of the list's tuples.
            for inner in ast.walk(function):
                if isinstance(inner, ast.Tuple) and inner.elts and isinstance(
                        inner.elts[0], ast.Constant) and isinstance(inner.elts[0].value, str) \
                        and len(inner.elts) == len(node.target.elts):
                    found.add(inner.elts[0].value)
    return found


def recorded_event_types() -> set[str]:
    """Every event type server.py writes to the experiment record."""

    tree = ast.parse(SERVER.read_text(encoding="utf-8"))
    found: set[str] = {"report_finalized"}  # ExperimentReportStore.finalize
    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for call in ast.walk(function):
            if not isinstance(call, ast.Call):
                continue
            # store.append_event(...), or asyncio.to_thread(store.append_event, ...).
            target = call.func if isinstance(call.func, ast.Attribute) and \
                call.func.attr == "append_event" else next(
                    (arg for arg in call.args if isinstance(arg, ast.Attribute)
                     and arg.attr == "append_event"), None)
            if target is None or isinstance(target.value, ast.Attribute):
                continue  # catalog.store.append_event: the protocol catalog's ledger
            for keyword in call.keywords:
                if keyword.arg != "event_type":
                    continue
                value = keyword.value
                if isinstance(value, ast.Name) and value.id.isupper():
                    found.add(getattr(reports, value.id))
                elif isinstance(value, ast.Name):
                    found |= _assigned(function, value.id)
                else:
                    found |= _constants(value)
    return found


class EventNameTests(unittest.TestCase):
    def test_connection_restored_has_its_name(self) -> None:
        self.assertEqual(EVENT_NAMES_KO["connection_restored"], "연결 끊김 뒤 다시 연결")

    def test_the_names_this_lane_filled(self) -> None:
        for event_type, name in NAMED_HERE.items():
            with self.subTest(event_type=event_type):
                self.assertEqual(EVENT_NAMES_KO.get(event_type), name)

    def test_every_event_the_server_records_has_a_name(self) -> None:
        recorded = recorded_event_types()
        # The scan finds the writes it should (a broken scan finds nothing).
        self.assertLessEqual(
            {"connection_restored", "timer_end_notice", "report_value_confirmed",
             "record_retracted", "branch_steps_skipped", "repeat_registered",
             "system_anomaly"}, recorded)
        self.assertEqual(sorted(recorded - set(EVENT_NAMES_KO)), [])

    def test_names_are_one_per_event(self) -> None:
        names = list(EVENT_NAMES_KO.values())
        self.assertEqual(len(names), len(set(names)))
        self.assertTrue(all(name.strip() for name in names))

    def test_the_csv_carries_the_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperimentReportStore(Path(tmp) / "reports.sqlite")
            report = store.open_report(
                session_id="session-fx4", protocol_id="p", protocol_title="t",
                protocol_revision="1", protocol_sha256="0" * 64, readiness_status="ready",
                development_only=True,
            )
            store.append_event(
                report["report_id"], event_key="connection-restored-1",
                event_type="connection_restored", payload={"gap_seconds": 12.0})
            rows = list(csv.DictReader(io.StringIO(
                store.export_csv(report["report_id"]).decode("utf-8-sig"))))
        self.assertEqual(rows[-1]["event_name_ko"], "연결 끊김 뒤 다시 연결")


if __name__ == "__main__":
    unittest.main()
