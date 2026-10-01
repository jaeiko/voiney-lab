"""Development test mode: run an analysed protocol with its readiness gates open.

``VOICE_WORKFLOW_AGENT_TEST_MODE_SKIP_READINESS_GATES`` exists so the voice
guide can be exercised on protocols whose gates cannot be settled yet. These
tests pin what it may and may not do:

* off (the default), a gated protocol is blocked exactly as before;
* on, an analysed protocol can be activated and a session started, while the
  readiness verdict and the list of outstanding gates stay what they were;
* an operational usage scope ignores it and says so in the log;
* a protocol with no analysis, or a failed one, still cannot run;
* a session started in test mode says so in its experiment report.

Every server-side path here goes through the real ``_open_protocol_catalog``,
so the switch is read where production reads it.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from voiney_lab import experiment_protocol as domain
from voiney_lab.document_store import ingest_manifest
from voiney_lab.experiment_protocol_config import ProtocolPersistenceSettings
from voiney_lab.experiment_protocol_store import initialize_protocol_store
from voiney_lab.experiment_reports import (
    ExperimentReportSettings,
    ExperimentReportStore,
)
from voiney_lab.protocol_catalog import (
    ProtocolCatalog,
    ProtocolCatalogUnavailableError,
)
from voiney_lab.server import (
    READINESS_GATE_TEST_MODE_ENV,
    READINESS_GATE_TEST_MODE_LABEL,
    ServerConfig,
    _open_protocol_catalog,
    _test_mode_skips_readiness_gates,
    log_readiness_gate_test_mode,
    voice_socket,
)
import voiney_lab.server as server_module

from tests.test_protocol_catalog import (
    _dedicated_to_thread,
    analysis_draft,
    write_text_pdf,
)
from tests.test_retrieval import operational_document

ROOT = Path(__file__).resolve().parents[1]
GATE = domain.ReadinessReasonCode.NO_DECLARED_SAFETY_WARNINGS.value


def _environment(*, test_mode: bool, scope: str = "demo") -> dict[str, str]:
    return {
        READINESS_GATE_TEST_MODE_ENV: "true" if test_mode else "false",
        "VOICE_WORKFLOW_AGENT_USAGE_SCOPE": scope,
        "VOICE_WORKFLOW_AGENT_WORKSPACE_ENABLED": "false",
    }


class _TestModeFixture(unittest.TestCase):
    """One registered PDF whose analysis is held by one readiness gate."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = ProtocolPersistenceSettings(True, self.root / "catalog")
        settings_patch = patch.object(
            server_module, "_protocol_store_settings", return_value=self.settings
        )
        settings_patch.start()
        self.addCleanup(settings_patch.stop)
        self.sample_pdf = self.root / "sample.pdf"
        write_text_pdf(
            self.sample_pdf,
            "Protocol Test\nSection preparation\n1. Add solution.\nWear gloves.",
            title="Protocol Test",
        )

    def _register(self, *, analysed: bool) -> str:
        store = initialize_protocol_store(self.settings)
        try:
            registration = ProtocolCatalog(store).register(
                self.sample_pdf,
                source_filename="sample.pdf",
                media_type="application/pdf",
            )
            protocol_id = registration.entry.protocol_id
            if analysed:
                draft = analysis_draft(
                    self.sample_pdf, protocol_id, "Protocol Test"
                )
                store.append_analysis_revision(
                    protocol_id,
                    1,
                    f"analysis-{registration.entry.source_sha256[:24]}",
                    draft.protocol,
                    draft.readiness,
                    draft.capability_policy_id,
                )
            return protocol_id
        finally:
            store.close()

    def _activate_in_test_mode(self, protocol_id: str) -> None:
        with patch.dict(os.environ, _environment(test_mode=True)):
            catalog, store = _open_protocol_catalog()
            try:
                catalog.activate_development(protocol_id)
            finally:
                store.close()


class CatalogTestModeTests(_TestModeFixture):
    def test_switch_off_a_gated_protocol_is_blocked_as_before(self) -> None:
        protocol_id = self._register(analysed=True)
        with patch.dict(os.environ, _environment(test_mode=False)):
            self.assertFalse(_test_mode_skips_readiness_gates())
            catalog, store = _open_protocol_catalog()
            try:
                self.assertFalse(catalog.skip_readiness_gates)
                with self.assertRaises(ProtocolCatalogUnavailableError):
                    catalog.activate_development(protocol_id)
                entry = catalog.get_entry(protocol_id)
                self.assertFalse(entry.available_for_execution)
                self.assertEqual(entry.lifecycle_state, "blocked")
            finally:
                store.close()

    def test_switch_on_an_analysed_protocol_runs_with_its_gates_still_listed(
        self,
    ) -> None:
        protocol_id = self._register(analysed=True)
        with patch.dict(os.environ, _environment(test_mode=True)):
            self.assertTrue(_test_mode_skips_readiness_gates())
            catalog, store = _open_protocol_catalog()
            try:
                before = catalog.get_entry(protocol_id)
                self.assertFalse(before.available_for_execution)
                # The lifecycle label still reports the gate, not the switch.
                self.assertEqual(before.lifecycle_state, "blocked")

                activated = catalog.activate_development(protocol_id)
                self.assertTrue(activated.available_for_execution)
                self.assertEqual(activated.approval_status, "development_only")
                self.assertEqual(
                    activated.readiness_status, before.readiness_status
                )
                # The session-start path loads it.
                fixture = catalog.load_executable_fixture(protocol_id)
                self.assertEqual(fixture.revision_id, activated.revision_id)

                review = catalog.review(protocol_id)
                self.assertFalse(review["readiness_gates_cleared"])
                self.assertEqual(
                    review["gates"]["structural_readiness"], "blocked"
                )
                blockers = {
                    item["code"]: item for item in review["outstanding_blockers"]
                }
                self.assertIn(GATE, blockers)
                self.assertFalse(blockers[GATE]["already_acknowledged"])

                activation = [
                    event
                    for event in store.list_events(protocol_id)
                    if event.event_type == "protocol_development_activated"
                ]
                self.assertEqual(len(activation), 1)
                self.assertIs(
                    activation[0].payload["test_mode_readiness_gates_skipped"],
                    True,
                )
            finally:
                store.close()

        # Turning the switch off takes the permission back: the activation
        # stands in the ledger, the gate still blocks.
        with patch.dict(os.environ, _environment(test_mode=False)):
            catalog, store = _open_protocol_catalog()
            try:
                self.assertFalse(
                    catalog.get_entry(protocol_id).available_for_execution
                )
            finally:
                store.close()

    def test_operational_scope_ignores_the_switch_and_logs_a_warning(
        self,
    ) -> None:
        protocol_id = self._register(analysed=True)
        with patch.dict(
            os.environ, _environment(test_mode=True, scope="operational")
        ):
            self.assertFalse(_test_mode_skips_readiness_gates())
            catalog, store = _open_protocol_catalog()
            try:
                self.assertFalse(catalog.skip_readiness_gates)
                with self.assertRaises(ProtocolCatalogUnavailableError):
                    catalog.activate_development(protocol_id)
                self.assertFalse(
                    catalog.get_entry(protocol_id).available_for_execution
                )
            finally:
                store.close()
            with self.assertLogs("voiney_lab", level="WARNING") as logged:
                log_readiness_gate_test_mode()
        output = "\n".join(logged.output)
        self.assertIn("readiness_gate_test_mode.ignored", output)
        self.assertIn("reason=operational_usage_scope", output)
        self.assertNotIn(READINESS_GATE_TEST_MODE_LABEL, output)

    def test_a_protocol_without_a_successful_analysis_still_cannot_run(
        self,
    ) -> None:
        protocol_id = self._register(analysed=False)
        with patch.dict(os.environ, _environment(test_mode=True)):
            catalog, store = _open_protocol_catalog()
            try:
                self.assertTrue(catalog.skip_readiness_gates)
                with self.assertRaises(ProtocolCatalogUnavailableError):
                    catalog.activate_development(protocol_id)
                self.assertFalse(
                    catalog.get_entry(protocol_id).available_for_execution
                )

                catalog.request_analysis(protocol_id, "analysis-test-mode")
                failed = catalog.fail_analysis_request(
                    protocol_id,
                    "analysis-test-mode",
                    failure_code="provider_configuration_missing",
                )
                self.assertEqual(failed.analysis_status, "analysis_failed")
                self.assertFalse(failed.available_for_execution)
                with self.assertRaises(ProtocolCatalogUnavailableError):
                    catalog.activate_development(protocol_id)
                with self.assertRaises(ProtocolCatalogUnavailableError):
                    catalog.load_executable_fixture(protocol_id)
            finally:
                store.close()

    def test_startup_log_announces_test_mode(self) -> None:
        with patch.dict(os.environ, _environment(test_mode=True)):
            with self.assertLogs("voiney_lab", level="WARNING") as logged:
                log_readiness_gate_test_mode()
        self.assertIn(READINESS_GATE_TEST_MODE_LABEL, "\n".join(logged.output))

    def test_startup_log_is_silent_when_the_switch_is_off(self) -> None:
        with patch.dict(os.environ, _environment(test_mode=False)):
            with patch.object(server_module.log, "warning") as warning:
                log_readiness_gate_test_mode()
        warning.assert_not_called()


class HttpTestModeTests(_TestModeFixture):
    """The review, activation and catalog endpoints the browser calls."""

    def setUp(self) -> None:
        super().setUp()
        # GET /api/protocols reads server_config(), which refuses to run
        # without an approved safety catalog for the usage scope. Only a
        # maintainer's .env used to supply one, so give the test its own
        # fictional demo-scope catalog instead of depending on that file.
        catalog = self.root / "approved.sqlite"
        ingest_manifest(
            {"documents": [operational_document(usage_scope="demo")]}, catalog
        )
        catalog_patch = patch.dict(
            os.environ, {"VOICE_WORKFLOW_AGENT_SAFETY_CATALOG": str(catalog)}
        )
        catalog_patch.start()
        self.addCleanup(catalog_patch.stop)

    def _request(self, method: str, url: str) -> httpx.Response:
        async def send() -> httpx.Response:
            transport = httpx.ASGITransport(app=server_module.app)
            with patch(
                "fastapi.routing.run_in_threadpool",
                side_effect=_dedicated_to_thread,
            ):
                async with httpx.AsyncClient(
                    transport=transport, base_url="http://testserver"
                ) as client:
                    return await client.request(
                        method, url, headers={"Accept": "application/json"}
                    )

        return asyncio.run(send())

    def test_switch_off_the_endpoints_behave_as_before(self) -> None:
        protocol_id = self._register(analysed=True)
        with patch.dict(os.environ, _environment(test_mode=False)):
            review = self._request("GET", f"/api/protocols/{protocol_id}/review")
            self.assertEqual(review.status_code, 200, review.text)
            self.assertFalse(review.json()["development_activation_allowed"])
            activated = self._request(
                "POST", f"/api/protocols/{protocol_id}/activate-development"
            )
            self.assertEqual(activated.status_code, 503, activated.text)
            listing = self._request("GET", "/api/protocols")
        self.assertEqual(listing.status_code, 200, listing.text)
        self.assertNotIn("test_mode", listing.json())
        entry = next(
            item for item in listing.json()["protocols"]
            if item["protocol_id"] == protocol_id
        )
        self.assertFalse(entry["available_for_execution"])

    def test_switch_on_the_browser_can_activate_and_select_the_protocol(
        self,
    ) -> None:
        protocol_id = self._register(analysed=True)
        with patch.dict(os.environ, _environment(test_mode=True)):
            review = self._request("GET", f"/api/protocols/{protocol_id}/review")
            self.assertEqual(review.status_code, 200, review.text)
            self.assertTrue(review.json()["development_activation_allowed"])
            self.assertFalse(review.json()["readiness_gates_cleared"])
            self.assertIn(
                GATE,
                [item["code"] for item in review.json()["outstanding_blockers"]],
            )
            activated = self._request(
                "POST", f"/api/protocols/{protocol_id}/activate-development"
            )
            self.assertEqual(activated.status_code, 200, activated.text)
            self.assertTrue(activated.json()["available_for_execution"])
            listing = self._request("GET", "/api/protocols")
            after = self._request("GET", f"/api/protocols/{protocol_id}/review")
        self.assertEqual(listing.status_code, 200, listing.text)
        self.assertEqual(
            listing.json()["test_mode"], {"readiness_gates_skipped": True}
        )
        entry = next(
            item for item in listing.json()["protocols"]
            if item["protocol_id"] == protocol_id
        )
        self.assertTrue(entry["available_for_execution"])
        # Executable now, and the gate is still on screen.
        self.assertTrue(after.json()["available_for_execution"])
        self.assertIn(
            GATE, [item["code"] for item in after.json()["outstanding_blockers"]]
        )


class SessionTestModeTests(_TestModeFixture):
    """A voice session on the gated protocol, and what its report records."""

    def _start_session(self, protocol_id: str, reports_db: Path) -> list[dict]:
        config = ServerConfig(
            self.root / "offline-catalog.sqlite",
            None,
            "test_only",
            frozenset({"ko"}),
            "ko",
        )

        class Socket:
            def __init__(self):
                self.sent = []
                self.messages = iter((
                    {"text": json.dumps({
                        "type": "session.start",
                        "mode": "cascade",
                        "language": "ko",
                        "protocol_id": protocol_id,
                        "configuration_id": 41,
                    })},
                    {"text": json.dumps({
                        "type": "experiment.report.get",
                        "configuration_id": 41,
                    })},
                    {"type": "websocket.disconnect", "code": 1000},
                ))

            async def accept(self):
                return None

            async def send_text(self, value):
                self.sent.append(json.loads(value))

            async def send_bytes(self, value):
                return None

            async def receive(self):
                return next(self.messages)

        socket = Socket()
        with patch(
            "voiney_lab.server.server_config", return_value=config
        ), patch(
            "voiney_lab.server.server_tool_context",
            return_value=SimpleNamespace(language="ko"),
        ), patch(
            "voiney_lab.server.ExperimentReportSettings.from_environment",
            return_value=ExperimentReportSettings(True, reports_db),
        ), patch(
            "voiney_lab.server.ProcedureStore"
        ), patch(
            "voiney_lab.server.OpenAICompatibleProtocolAnalysisModel"
        ):
            asyncio.run(voice_socket(socket))
        return socket.sent

    def test_switch_on_the_session_starts_and_its_report_is_marked(
        self,
    ) -> None:
        protocol_id = self._register(analysed=True)
        self._activate_in_test_mode(protocol_id)
        reports_db = self.root / "reports.sqlite"
        with patch.dict(os.environ, _environment(test_mode=True)):
            sent = self._start_session(protocol_id, reports_db)

        ready = next(item for item in sent if item["type"] == "session.ready")
        self.assertEqual(ready["protocol_id"], protocol_id)
        store = ExperimentReportStore(reports_db)
        (summary,) = store.list_reports()
        marks = [
            event for event in store.get_report(summary["report_id"])["events"]
            if event["event_type"] == "test_mode_readiness_gates_skipped"
        ]
        self.assertEqual(len(marks), 1)
        self.assertEqual(
            marks[0]["payload"]["switch"], READINESS_GATE_TEST_MODE_ENV
        )
        self.assertIn(GATE, marks[0]["payload"]["outstanding_reason_codes"])
        self.assertIn(
            "test_mode_readiness_gates_skipped",
            store.export_markdown(summary["report_id"]).decode(),
        )

    def test_switch_off_the_same_protocol_is_refused_and_nothing_is_marked(
        self,
    ) -> None:
        protocol_id = self._register(analysed=True)
        self._activate_in_test_mode(protocol_id)
        reports_db = self.root / "reports.sqlite"
        with patch.dict(os.environ, _environment(test_mode=False)):
            sent = self._start_session(protocol_id, reports_db)

        self.assertFalse(any(item["type"] == "session.ready" for item in sent))
        required = next(
            item for item in sent
            if item["type"] == "session.configuration_required"
        )
        self.assertEqual(required["reason"], "protocol_selection_unavailable")
        self.assertEqual(ExperimentReportStore(reports_db).list_reports(), [])


class BannerTestModeTests(unittest.TestCase):
    """The page-top banner follows the catalog response and nothing else."""

    def test_catalog_response_shows_and_hides_the_banner(self) -> None:
        html = (
            ROOT / "src" / "voiney_lab" / "static" / "index.html"
        ).read_text(encoding="utf-8")
        self.assertIn(
            '<div id="test-mode-banner" class="test-mode-banner" '
            'role="alert" hidden>테스트 모드: 실행 준비 확인 조건을 건너뜀',
            html,
        )
        loader = "async function loadProtocolCatalog("
        body = loader + html.split(loader, 1)[1].split(
            "\nconst PROTOCOL_UPLOAD_ERROR_MESSAGES", 1
        )[0]
        harness = r"""
const assert=(ok,message)=>{if(!ok)throw new Error(message)};
class Element{constructor(){this.children=[];this.options=[];this.dataset={};this.textContent="";this.value="";this.hidden=false;}
 appendChild(child){this.children.push(child);this.options.push(child);return child}
 replaceChildren(){this.children=[];this.options=[]}setAttribute(){}}
const ids={"protocol-id":new Element(),"protocol-readiness":new Element(),"protocol-upload-status":new Element(),"protocol-revision":new Element(),"test-mode-banner":new Element()};
ids["test-mode-banner"].hidden=true;
const $=id=>ids[id]||null;
globalThis.document={createElement:()=>new Element()};
const protocolCatalog=new Map();
const catalogStatus=()=>({label:"상태"}),renderCatalogAnalysisProgress=()=>{},renderSelectedProtocolContext=()=>{};
""" + body + r"""
(async()=>{
 const reply=payload=>async()=>({ok:true,json:async()=>payload});
 globalThis.fetch=reply({protocols:[],test_mode:{readiness_gates_skipped:true}});
 await loadProtocolCatalog();
 assert(ids["test-mode-banner"].hidden===false,"test mode did not show the banner");
 globalThis.fetch=reply({protocols:[]});
 await loadProtocolCatalog();
 assert(ids["test-mode-banner"].hidden===true,"banner stayed up without test mode");
})().catch(error=>{console.error(error.message);process.exit(1)});
"""
        result = subprocess.run(
            ["node", "-"], cwd=ROOT, text=True, input=harness,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
