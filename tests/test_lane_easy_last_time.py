""""지난번처럼": the last run's values chosen again in one press (lane EASY, decision 3).

Human decision 2026-10-24: when the same account's last experiment of the
same protocol changed values, the step "오늘 원문과 다르게 할 값" offers them
again at once. Only the record's own owner (the records' ownership rule);
a value comes back only where today's analysis states the same value at a
step of the same label and kind, and the start checks it again like any
typed value.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import patch

from tests.test_lane_easy_run_changes_screen import CHANGES
from tests.test_lane_on_wizard_screen import run
from tests.test_lane_rv_review_pending import _CatalogCase
from voiney_lab import experiment_reports as er
from voiney_lab import server
from voiney_lab.experiment_protocol_store import initialize_protocol_store
from dataclasses import replace

from tests.identity_support import development_principal
from voiney_lab.protocol_catalog import ProtocolCatalog
from voiney_lab.start_check import RunChange, RunChanges

def person(principal_id: str, organization_id: str):
    return replace(development_principal(), principal_id=principal_id, organization_id=organization_id)


TIME = RunChange("s2#1", "s2", "2", "time", "10", "min", "10 min", "20", "min").public_dict()
WARM = RunChange("s2#2", "s2", "2", "temperature", "37", "°C", "37 C", "40", "°C").public_dict()


def write_run(store: er.ExperimentReportStore, *, session: str, owner: str | None, organization: str | None,
              started: str, events: list[tuple[str, dict]]) -> str:
    with mock.patch.object(er, "_now", lambda: started):
        report_id = store.open_report(
            session_id=session, protocol_id="protocol-easy", protocol_title="Gamma", protocol_revision="rev-1",
            protocol_sha256="9" * 64, readiness_status="analysis_passed", development_only=False,
            organization_id=organization, owner_principal_id=owner)["report_id"]
        for index, (kind, payload) in enumerate(events):
            store.append_event(report_id, event_key=f"{session}-{index}", event_type=kind,
                               step_id="s2", step_label="2", payload=payload)
    return report_id


class TheLastRunsValuesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = er.ExperimentReportStore(Path(self.temporary.name) / "records.sqlite")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_the_values_are_the_records_at_its_end(self) -> None:
        events = [
            {"event_type": "session_started", "event_key": "a", "payload": {"run_changes": [TIME]}},
            {"event_type": "run_value_changed", "event_key": "b",
             "payload": {"step_record": {"kind": "run_value_changed", "change": WARM}}},
            {"event_type": "run_value_reverted", "event_key": "c",
             "payload": {"step_record": {"kind": "run_value_reverted", "reverted": [TIME]}}},
        ]
        self.assertEqual([item["item_id"] for item in er.run_values(events)], ["s2#2"])

    def test_only_the_owners_latest_run_is_read(self) -> None:
        write_run(self.store, session="older", owner="person-a", organization="lab-1",
                  started="2026-10-20T01:00:00+00:00", events=[("session_started", {"run_changes": [WARM]})])
        write_run(self.store, session="latest", owner="person-a", organization="lab-1",
                  started="2026-10-22T01:00:00+00:00", events=[("session_started", {"run_changes": [TIME]})])
        write_run(self.store, session="someone", owner="person-b", organization="lab-1",
                  started="2026-10-23T01:00:00+00:00", events=[("session_started", {"run_changes": [WARM]})])
        mine = self.store.last_run_values(protocol_id="protocol-easy", organization_id="lab-1",
                                          owner_principal_id="person-a")
        self.assertEqual([item["item_id"] for item in mine["changes"]], ["s2#1"])
        theirs = self.store.last_run_values(protocol_id="protocol-easy", organization_id="lab-1",
                                            owner_principal_id="person-b")
        self.assertEqual([item["item_id"] for item in theirs["changes"]], ["s2#2"])
        self.assertIsNone(self.store.last_run_values(
            protocol_id="protocol-easy", organization_id="lab-2", owner_principal_id="person-a"))
        self.assertIsNone(self.store.last_run_values(
            protocol_id="protocol-easy", organization_id="lab-1", owner_principal_id=None))


class OnTodaysListTests(unittest.TestCase):
    VALUES = [
        {"item_id": "t2#1", "step_id": "t2", "step_label": "2", "kind": "time", "number": "10", "unit": "min"},
        {"item_id": "t2#2", "step_id": "t2", "step_label": "2", "kind": "temperature", "number": "37", "unit": "°C"},
    ]

    def test_a_value_comes_back_where_today_states_the_same_value_at_that_step(self) -> None:
        matched = RunChanges.last_time(self.VALUES, [TIME, WARM])
        self.assertEqual([(item["item_id"], item["shown_ko"]) for item in matched],
                         [("t2#1", "20분"), ("t2#2", "40°C")])
        self.assertEqual(matched[0]["step_id"], "t2")

    def test_a_value_the_analysis_now_states_otherwise_does_not_come_back(self) -> None:
        values = [dict(self.VALUES[0], number="30"), dict(self.VALUES[1], step_label="3")]
        self.assertEqual(RunChanges.last_time(values, [TIME, WARM]), [])


class TheRouteOffersItTests(_CatalogCase):
    def setUp(self) -> None:
        super().setUp()
        self.analyze(self.answer(pages=(1, 2, 3)))
        self.records = er.ExperimentReportStore(Path(self.temp.name) / "records.sqlite")
        catalog = RunChanges(self.catalog)
        options = catalog.options(self.protocol_id)
        item = next(value for value in options["values"] if value["step_label"] == "2" and value["kind"] == "time")
        change = RunChange(item["item_id"], item["step_id"], "2", "time", "10", "min", "10 min", "25", "min")
        with mock.patch.object(er, "_now", lambda: "2026-10-22T01:00:00+00:00"):
            report_id = self.records.open_report(
                session_id="last", protocol_id=self.protocol_id, protocol_title="Gamma", protocol_revision="r",
                protocol_sha256="9" * 64, readiness_status="analysis_passed", development_only=False,
                organization_id="lab-1", owner_principal_id="person-a")["report_id"]
            self.records.append_event(report_id, event_key="start", event_type="session_started",
                                      step_id=item["step_id"], step_label="2",
                                      payload={"run_changes": [change.public_dict()]})

    def open_catalog(self):
        opened = initialize_protocol_store(self.settings)
        return ProtocolCatalog(opened), opened

    def options_for(self, principal, workspace: bool = True):
        token = server._REQUEST_PRINCIPAL.set(principal)
        try:
            with patch.object(server, "_open_protocol_catalog", side_effect=self.open_catalog), \
                    patch.object(server, "_scope_catalog_resource"), \
                    patch.dict("os.environ", {
                        "VOINEY_LAB_EXPERIMENT_REPORTS_ENABLED": "true",
                        "VOINEY_LAB_EXPERIMENT_REPORT_DB": str(Path(self.temp.name) / "records.sqlite"),
                        "VOINEY_LAB_WORKSPACE_ENABLED": "true" if workspace else "false",
                        "VOINEY_LAB_WORKSPACE_DATA_DIR": str(Path(self.temp.name) / "workspace")}):
                return server.get_protocol_run_changes(self.protocol_id)
        finally:
            server._REQUEST_PRINCIPAL.reset(token)

    def test_the_owner_is_offered_the_last_runs_value_and_nobody_else_is(self) -> None:
        mine = self.options_for(person("person-a", "lab-1"))
        self.assertEqual([item["shown_ko"] for item in mine["last_time"]["changes"]], ["25분"])
        self.assertEqual(mine["last_time"]["when_ko"], "10월 22일")
        self.assertIsNone(self.options_for(person("person-b", "lab-1"))["last_time"])
        self.assertIsNone(self.options_for(person("person-a", "lab-2"))["last_time"])
        self.assertIsNone(self.options_for(person("person-a", "lab-1"), workspace=False)["last_time"])

    def test_choosing_it_again_is_checked_like_a_typed_value(self) -> None:
        mine = self.options_for(person("person-a", "lab-1"))
        item = mine["last_time"]["changes"][0]
        chosen = RunChanges(self.catalog).choose(
            self.protocol_id, [{"item_id": item["item_id"], "value": item["shown_ko"]}],
            target=mine["target"], actor_principal_id="person-a", source="last_time")
        self.assertEqual(chosen["source"], "last_time")
        self.assertEqual(chosen["changes"][0]["words_ko"], "25분(오늘 바꾼 값 · 원문 10분)")


class TheButtonTests(unittest.TestCase):
    def test_the_last_time_button_fills_the_boxes_and_the_choice_says_where_it_came_from(self) -> None:
        result = run(CHANGES + r"""
changesAnswer={...VALUES,last_time:{when_ko:"10월 22일",report_id:"r-1",changes:[{item_id:"s2#1",step_label:"2",kind:"time",kind_ko:"시간",shown_ko:"25분"}]}};
await toChanges();
const button=walk(body()).find(el=>el.id==="wizard-changes-last-time");
assert(button&&button.textContent==="지난번처럼 (1개)",button&&button.textContent);
const text=walk(body()).map(el=>el.textContent).join(" ");
assert(text.includes("지난번(10월 22일): 2단계 시간 25분"),text);
button.dispatch("click");await settle();
assert(changeInput("s2#1").value==="25분",changeInput("s2#1").value);
assert(primary().textContent==="이 값으로 할게요 (1개)",primary().textContent);
primary().dispatch("click");await settle(8);
const post=posts().find(call=>call.url.endsWith("/start-check/changes"));
assert(post.body.source==="last_time"&&JSON.stringify(post.body.changes)===JSON.stringify([{item_id:"s2#1",value:"25분"}]),JSON.stringify(post.body));
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_no_last_run_values_no_button(self) -> None:
        result = run(CHANGES + r"""
await toChanges();
assert(!walk(body()).some(el=>el.id==="wizard-changes-last-time"),"a button with nothing behind it");
""")
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
