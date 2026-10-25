"""Every start passes "오늘 원문과 다르게 할 값" (lane EASY, human decision 2026-10-24, item 2).

A start opens only through the step ("바꿀 값 없이 다음" included): every way
around it, the "바로 시작" card included, is gone, and so is the earlier
choice applied silently within 12 hours -- the start names the choice it
uses (session.start ``run_change_set_id``). With the start check on, a start
that names none is refused. The step stays after the safety check (item 3).
"""


from __future__ import annotations

import asyncio
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from tests.identity_support import development_principal
from tests.lane_ra_support import agitation_fixture, session_at
from tests.test_lane_easy_run_changes_screen import CHANGES
from tests.test_lane_on_wizard_screen import run
from tests.test_lane_rv_review_pending import _CatalogCase
from tests.test_voice_pause_resume_persistence import _QueuedSocket
from voiney_lab import server
from voiney_lab.curated_protocol import CuratedProtocolAction
from voiney_lab.experiment_protocol_store import initialize_protocol_store
from voiney_lab.protocol import ProtocolError, parse_control
from voiney_lab.protocol_catalog import ProtocolCatalog
from voiney_lab.start_check import (
    RUN_CHANGES_APPLIED_EVENT,
    RunChangeError,
    RunChanges,
    StartCheck,
    check_run_change,
    run_change_is_large,
    step_changeable_values,
)


def timed_session(confirm_mode: str = "readback"):
    fixture = agitation_fixture()
    fixture = replace(fixture, timer_manifest={fixture.steps[0].step_id: 900})
    session = session_at(fixture, step_index=0)
    session.confirm_mode = confirm_mode
    return session


class TheStartNamesItsChoiceTests(_CatalogCase):
    """With the start check on, a new run opens only with the choice it names."""

    def setUp(self) -> None:
        super().setUp()
        self.analyze(self.answer(pages=(1, 2, 3)))
        self.fixture = self.catalog.load_executable_fixture(self.protocol_id)
        self.principal = development_principal()
        checker = StartCheck(self.catalog)
        for question in checker._built(self.protocol_id)[1]:
            checker.answer(self.protocol_id, question["question_id"], action="accept",
                           actor_principal_id=self.principal.principal_id)
        checker.acknowledge_safety(self.protocol_id, target=checker.questions(self.protocol_id)["target"],
                                   actor_principal_id=self.principal.principal_id)
        self.options = RunChanges(self.catalog).options(self.protocol_id)

    def choose(self, changes, actor=None) -> str:
        return RunChanges(self.catalog).choose(
            self.protocol_id, changes, target=self.options["target"],
            actor_principal_id=actor or self.principal.principal_id)["change_set_id"]

    def start_socket(self, run_change_set_id=None):
        placeholder = Path("/tmp/offline-easy-start")
        config = server.ServerConfig(placeholder, None, "test_only", frozenset({"ko", "en"}), "ko",
                                     placeholder, placeholder, placeholder)

        def catalog():
            store = initialize_protocol_store(self.settings)
            return ProtocolCatalog(store), store

        async def run_it():
            socket = _QueuedSocket()
            token = server._REQUEST_PRINCIPAL.set(self.principal)
            try:
                task = asyncio.create_task(server.voice_socket(socket))
                control = {"type": "session.start", "configuration_id": 1, "mode": "cascade",
                           "language": "ko", "protocol_id": self.protocol_id}
                if run_change_set_id is not None:
                    control["run_change_set_id"] = run_change_set_id
                socket.control(control)
                result = await socket.wait_for(lambda e: e["type"] in {
                    "session.ready", "session.configuration_required", "error"})
                socket.disconnect()
                await task
                return result, socket.sent
            finally:
                server._REQUEST_PRINCIPAL.reset(token)

        with patch.dict("os.environ", {
            "VOINEY_LAB_START_CHECK_ENABLED": "true",
            "VOINEY_LAB_WORKSPACE_ENABLED": "false", "VOINEY_LAB_EXPERIMENT_REPORTS_ENABLED": "false",
            "VOINEY_LAB_PROTOCOL_ENABLED": "true", "VOINEY_LAB_PROTOCOL_DATA_DIR": str(self.settings.data_dir),
            "VOINEY_LAB_USAGE_SCOPE": "demo",
        }), patch.object(server, "server_config", return_value=config), \
                patch.object(server, "_open_protocol_catalog", side_effect=catalog), \
                patch.object(server, "load_curated_protocol_fixture", return_value=self.fixture), \
                patch.object(server, "_candidate_fixture_execution_state",
                             return_value={"available_for_execution": True}), \
                patch.object(server, "_scope_catalog_resource"):
            return asyncio.run(run_it())

    @staticmethod
    def state(sent) -> dict:
        states = [item for item in sent if item.get("type") == "protocol.fixture.state"]
        return states[-1]["state"] if states else {}

    def test_a_start_that_names_no_choice_is_refused_with_the_step_to_go_back_to(self) -> None:
        result, _ = self.start_socket()
        self.assertEqual(result["type"], "session.configuration_required")
        self.assertEqual(result["reason"], "run_changes_required")
        self.assertIn("오늘 원문과 다르게 할 값", result["message_ko"])

    def test_an_earlier_choice_the_start_does_not_name_is_never_applied(self) -> None:
        item = next(value["item_id"] for value in self.options["values"] if value["kind"] == "time")
        self.choose([{"item_id": item, "value": "15분"}])
        skipped = self.choose([])
        result, sent = self.start_socket(skipped)
        self.assertEqual(result["type"], "session.ready")
        self.assertEqual(self.state(sent).get("run_changes"), [])
        result, _ = self.start_socket()
        self.assertEqual(result["reason"], "run_changes_required")

    def test_the_named_choice_is_applied_once(self) -> None:
        item = next(value["item_id"] for value in self.options["values"] if value["kind"] == "time")
        chosen = self.choose([{"item_id": item, "value": "15분"}])
        result, sent = self.start_socket(chosen)
        self.assertEqual(result["type"], "session.ready")
        self.assertEqual([change["shown_ko"] for change in self.state(sent)["run_changes"]], ["15분"])
        self.assertEqual(len(self.events(RUN_CHANGES_APPLIED_EVENT)), 1)
        again, _ = self.start_socket(chosen)
        self.assertEqual(again["reason"], "run_changes_stale")

    def test_another_accounts_choice_is_refused(self) -> None:
        theirs = self.choose([], actor="person-b")
        result, _ = self.start_socket(theirs)
        self.assertEqual(result["reason"], "run_changes_stale")


class TheStartControlTests(unittest.TestCase):
    BASE = '{"type":"session.start","configuration_id":1,"mode":"cascade","language":"ko","protocol_id":"p-1"'

    def test_the_control_carries_the_named_choice(self) -> None:
        parsed = parse_control(self.BASE + ',"run_change_set_id":"run-changes-0123456789abcdef01234567"}')
        self.assertEqual(parsed["run_change_set_id"], "run-changes-0123456789abcdef01234567")
        self.assertNotIn("run_change_set_id", parse_control(self.BASE + "}"))

    def test_a_malformed_choice_id_is_refused(self) -> None:
        for value in ('"x"', "12", '"run-changes-../../etc"'):
            with self.subTest(value=value), self.assertRaises(ProtocolError):
                parse_control(self.BASE + f',"run_change_set_id":{value}}}')


class TheScreenTests(unittest.TestCase):
    def test_the_start_names_the_choice_and_forgets_it_after(self) -> None:
        result = run(CHANGES + r"""
await toChanges();
primary().dispatch("click");await settle(8);
assert(wizard.step==="start"&&wizard.chosenChanges?.changeSetId==="run-changes-1",JSON.stringify(wizard.chosenChanges));
let sent=null;sendControl=(type,body)=>{if(type==="session.start")sent=body;};
init=async()=>{};globalThis.socket={readyState:1};
await startSession();await settle();
assert(sent&&sent.run_change_set_id==="run-changes-1",JSON.stringify(sent));
assert(wizard.chosenChanges===null,"the choice was kept for another start");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_cards_quick_start_needs_the_step_passed(self) -> None:
        result = run(CHANGES + r"""
await toChanges();
let started=0;startReviewedProtocol=async()=>{started+=1;};
// The safety check was read; the sheet is closed at the step without passing it.
closeWizardSheet();
assert(experimentStartPlan().checked===false,"a start without the step is offered at once");
await pressExperimentStart();await settle(8);
assert(started===0&&wizard.open===true&&wizard.step==="changes",`started ${started} open ${wizard.open} step ${wizard.step}`);
primary().dispatch("click");await settle(8);
closeWizardSheet();
assert(experimentStartPlan().checked===true,"the step passed and still not offered");
await pressExperimentStart();await settle(8);
assert(started===1,"the passed step did not let the card start");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_refusal_for_the_step_returns_to_it(self) -> None:
        result = run(CHANGES + r"""
await toChanges();
closeWizardSheet({silent:true});
returnToStartStep("run_changes_required","오늘 원문과 다르게 할 값 단계를 지나야 시작할 수 있어요.");await settle(8);
assert(wizard.open===true&&wizard.step==="changes",`step: ${wizard.step}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
