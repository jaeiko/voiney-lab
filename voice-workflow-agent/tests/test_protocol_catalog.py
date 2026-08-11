"""Provider-free multi-Protocol catalog, revision, and visual tests."""

from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from voice_workflow_agent import experiment_protocol as domain
from voice_workflow_agent import server as server_module
from voice_workflow_agent.curated_protocol import (
    CuratedProtocolFixtureError,
    CuratedProtocolSession,
    load_curated_protocol_fixture,
)
from voice_workflow_agent.experiment_protocol_analysis import (
    ProtocolAnalysisDraft,
    ProtocolAnalysisInputTooLargeError,
)
from voice_workflow_agent.experiment_protocol_config import (
    ProtocolFeatureDisabledError,
    ProtocolPersistenceSettings,
)
from voice_workflow_agent.experiment_protocol_pdf import extract_protocol_pdf
from voice_workflow_agent.experiment_protocol_store import initialize_protocol_store
from voice_workflow_agent.protocol_catalog import (
    ProtocolApprovalError,
    ProtocolCatalog,
    ProtocolCatalogNotFoundError,
    ProtocolCatalogUnavailableError,
    ProtocolChunkedAnalysisRequiredError,
    ProtocolOcrRequiredError,
    ProtocolRegistrationError,
    SharedSecretApprovalPolicy,
)


async def _dedicated_to_thread(function, *args, **kwargs):
    """Run a worker assertion without leaking asyncio's process-global pool."""
    result = []
    errors = []

    def run():
        try:
            result.append(function(*args, **kwargs))
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=run)
    worker.start()
    while worker.is_alive():
        await asyncio.sleep(0)
    worker.join()
    if errors:
        raise errors[0]
    return result[0]
from voice_workflow_agent.server import (
    ServerConfig,
    get_protocol_analysis_status,
    get_protocol_source_page,
    get_protocol_visual_asset,
    list_protocol_catalog,
    log_protocol_catalog_runtime_configuration,
    trigger_protocol_analysis,
    voice_socket,
)


def write_text_pdf(path: Path, text: str | None, *, title: str) -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    if text is not None:
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        content = DecodedStreamObject()
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        content.set_data(
            f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("ascii")
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {
                NameObject("/Font"): DictionaryObject(
                    {NameObject("/F1"): writer._add_object(font)}
                )
            }
        )
        page[NameObject("/Contents")] = writer._add_object(content)
    writer.add_metadata({"/Title": title})
    with path.open("wb") as stream:
        writer.write(stream)
    return path.read_bytes()


def analysis_draft(path: Path, protocol_id: str, title: str) -> ProtocolAnalysisDraft:
    extraction = extract_protocol_pdf(path)
    page_text = extraction.pages[0].text
    section_text = "Section preparation"
    instruction = "1. Add solution."
    assert section_text in page_text and instruction in page_text and title in page_text
    evidence = lambda excerpt: domain.SourceEvidence(1, excerpt)
    protocol = domain.ExperimentProtocol(
        protocol_id,
        domain.ProtocolMetadata(
            extraction,
            title,
            "en",
            evidence=evidence(title),
        ),
        sections=(
            domain.ProtocolSection(
                "preparation",
                section_text,
                evidence(section_text),
                (
                    domain.ProtocolSourceStep(
                        "step-1", "1", instruction, evidence(instruction)
                    ),
                ),
            ),
        ),
    )
    domain.validate_protocol(protocol)
    readiness = domain.assess_readiness(protocol)
    assert readiness.status is domain.ReadinessStatus.GUIDANCE_READY
    return ProtocolAnalysisDraft(
        extraction,
        protocol,
        readiness,
        domain.P1_CAPABILITY_POLICY,
        1,
        3,
    )


class ProtocolCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = initialize_protocol_store(
            ProtocolPersistenceSettings(True, self.root / "catalog")
        )
        self.catalog = ProtocolCatalog(self.store)
        self.alpha = self.root / "alpha.pdf"
        self.beta = self.root / "beta.pdf"
        write_text_pdf(
            self.alpha,
            "Protocol Alpha\nSection preparation\n1. Add solution.",
            title="Protocol Alpha",
        )
        write_text_pdf(
            self.beta,
            "Protocol Beta\nSection preparation\n1. Add solution.",
            title="Protocol Beta",
        )

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def _approve(self, path: Path, filename: str):
        registration = self.catalog.register(
            path, source_filename=filename, media_type="application/pdf"
        )
        draft = analysis_draft(
            path, registration.entry.protocol_id, registration.entry.title
        )
        self.store.append_analysis_revision(
            registration.entry.protocol_id,
            1,
            f"analysis-{registration.entry.source_sha256[:24]}",
            draft.protocol,
            draft.readiness,
            draft.capability_policy_id,
        )
        analyzed = self.catalog.get_entry(registration.entry.protocol_id)
        approved = self.catalog.approve(
            analyzed.protocol_id,
            analyzed.revision_id,
            policy=SharedSecretApprovalPolicy("review-secret"),
            presented_secret="review-secret",
        )
        return approved

    def test_two_pdfs_have_distinct_ids_revisions_and_isolated_sessions(self):
        alpha = self._approve(self.alpha, "alpha.pdf")
        beta = self._approve(self.beta, "beta.pdf")

        self.assertNotEqual(alpha.protocol_id, beta.protocol_id)
        self.assertNotEqual(alpha.source_sha256, beta.source_sha256)
        self.assertEqual(alpha.revision_id, "pdf-1-analysis-1")
        self.assertTrue(alpha.available_for_execution)
        first = CuratedProtocolSession(
            self.catalog.load_executable_fixture(alpha.protocol_id)
        )
        second = CuratedProtocolSession(
            self.catalog.load_executable_fixture(beta.protocol_id)
        )
        first.activate_configured()
        second.activate_configured()
        first.plan("프로토콜 종료해줘", turn_id=1, language="ko")
        self.assertFalse(first.active)
        self.assertTrue(second.active)
        self.assertEqual(second.current_index, 0)

    def test_duplicate_sha_is_idempotent_and_filename_cannot_choose_storage(self):
        first = self.catalog.register(
            self.alpha, source_filename="alpha.pdf", media_type="application/pdf"
        )
        alias = self.root / "alias.pdf"
        alias.write_bytes(self.alpha.read_bytes())
        second = self.catalog.register(
            alias, source_filename="alias.pdf", media_type="application/pdf"
        )
        self.assertFalse(first.deduplicated)
        self.assertTrue(second.deduplicated)
        self.assertEqual(first.entry.protocol_id, second.entry.protocol_id)
        with self.assertRaises(Exception):
            self.catalog.register(
                self.beta,
                source_filename="../escape.pdf",
                media_type="application/pdf",
            )
        with self.assertRaises(ProtocolRegistrationError):
            self.catalog.register(
                self.beta,
                source_filename="beta.txt",
                media_type="application/pdf",
            )
        with self.assertRaises(ProtocolRegistrationError):
            self.catalog.register(
                self.beta,
                source_filename="beta.pdf",
                media_type="text/plain",
            )

    def test_unapproved_unknown_and_ocr_sources_fail_closed(self):
        registered = self.catalog.register(
            self.alpha, source_filename="alpha.pdf", media_type="application/pdf"
        )
        with self.assertRaises(ProtocolCatalogUnavailableError):
            self.catalog.load_executable_fixture(registered.entry.protocol_id)
        with self.assertRaises(ProtocolCatalogNotFoundError):
            self.catalog.get_entry("protocol-" + "0" * 32)
        with self.assertRaises(ProtocolApprovalError):
            self.catalog.approve(
                registered.entry.protocol_id,
                registered.entry.revision_id,
                policy=SharedSecretApprovalPolicy("secret"),
                presented_secret="wrong",
            )
        scanned = self.root / "scanned.pdf"
        write_text_pdf(scanned, None, title="Scanned")
        scanned_entry = self.catalog.register(
            scanned, source_filename="scanned.pdf", media_type="application/pdf"
        ).entry
        self.assertEqual(scanned_entry.analysis_status, "ocr_required")
        with self.assertRaises(ProtocolOcrRequiredError):
            self.catalog.analyze(
                scanned_entry.protocol_id, Mock(), analysis_id="analysis-scanned"
            )

    def test_analysis_is_explicit_and_listing_has_zero_model_calls(self):
        entry = self.catalog.register(
            self.alpha, source_filename="alpha.pdf", media_type="application/pdf"
        ).entry
        fake_model = Mock()
        self.catalog.list_entries()
        fake_model.analyze.assert_not_called()
        draft = analysis_draft(self.alpha, entry.protocol_id, "Protocol Alpha")
        with patch(
            "voice_workflow_agent.protocol_catalog.analyze_protocol_extraction",
            return_value=draft,
        ) as analyze:
            self.catalog.analyze(
                entry.protocol_id, fake_model, analysis_id="analysis-explicit"
            )
        analyze.assert_called_once()


    def test_analysis_endpoint_owns_store_in_worker_and_status_is_read_only(self):
        entry = self.catalog.register(
            self.alpha, source_filename="alpha.pdf", media_type="application/pdf"
        ).entry
        draft = analysis_draft(self.alpha, entry.protocol_id, "Protocol Alpha")
        settings = ProtocolPersistenceSettings(True, self.root / "catalog")
        caller_thread = threading.get_ident()
        open_threads: list[int] = []

        def open_catalog():
            open_threads.append(threading.get_ident())
            store = initialize_protocol_store(settings)
            return ProtocolCatalog(store), store

        model = Mock()
        with patch(
            "voice_workflow_agent.server._open_protocol_catalog",
            side_effect=open_catalog,
        ), patch(
            "voice_workflow_agent.server.asyncio.to_thread",
            side_effect=_dedicated_to_thread,
        ), patch(
            "voice_workflow_agent.server._protocol_analysis_model",
            return_value=model,
        ), patch(
            "voice_workflow_agent.protocol_catalog.analyze_protocol_extraction",
            return_value=draft,
        ) as analyze:
            response = asyncio.run(trigger_protocol_analysis(entry.protocol_id))
            status = get_protocol_analysis_status(entry.protocol_id)

        self.assertEqual(response["analysis_status"], "review_required")
        self.assertEqual(status["state"], "review_required")
        self.assertEqual(status["total_chunks"], 0)
        self.assertNotEqual(open_threads[0], caller_thread)
        self.assertEqual(open_threads[-1], caller_thread)
        analyze.assert_called_once()

    def test_analysis_failure_is_persisted_safely_and_remains_unavailable(self):
        entry = self.catalog.register(
            self.alpha, source_filename="alpha.pdf", media_type="application/pdf"
        ).entry
        with patch(
            "voice_workflow_agent.protocol_catalog.analyze_protocol_extraction",
            side_effect=RuntimeError("sensitive fake provider response"),
        ):
            with self.assertRaises(RuntimeError):
                self.catalog.analyze(
                    entry.protocol_id, Mock(), analysis_id="analysis-failure"
                )

        failed = self.catalog.get_entry(entry.protocol_id)
        self.assertEqual(failed.analysis_status, "analysis_failed")
        self.assertFalse(failed.available_for_execution)
        event = self.store.list_events(entry.protocol_id)[-1]
        self.assertEqual(event.event_type, "protocol_analysis_failed")
        self.assertEqual(
            event.payload,
            {"failure_code": "analysis_failed", "status": "failed"},
        )
        self.assertNotIn("sensitive", json.dumps(event.payload))
        with self.assertRaises(ProtocolCatalogUnavailableError):
            self.catalog.load_executable_fixture(entry.protocol_id)

    def test_large_single_pass_input_is_deferred_without_model_call(self):
        model = Mock()
        with patch(
            "voice_workflow_agent.protocol_catalog.prepare_protocol_analysis_request",
            side_effect=ProtocolAnalysisInputTooLargeError("bounded fake input"),
        ):
            entry = self.catalog.register(
                self.alpha,
                source_filename="alpha.pdf",
                media_type="application/pdf",
            ).entry
            self.assertEqual(entry.analysis_status, "chunked_analysis_required")
            with self.assertRaises(ProtocolChunkedAnalysisRequiredError):
                self.catalog.analyze(
                    entry.protocol_id, model, analysis_id="analysis-large"
                )
        model.analyze.assert_not_called()

    def test_missing_original_visual_does_not_create_automatic_schematic(self):
        approved = self._approve(self.alpha, "alpha.pdf")
        fixture = self.catalog.load_executable_fixture(approved.protocol_id)
        self.assertIsNone(fixture.visual_for_step(0))
        with self.assertRaises(CuratedProtocolFixtureError):
            fixture.visual_content(0)
        with self.assertRaises(ProtocolCatalogNotFoundError):
            self.catalog.resolve_asset(
                approved.protocol_id, approved.revision_id, "../source-page-1"
            )
        with self.assertRaises(ProtocolCatalogNotFoundError):
            self.catalog.resolve_asset(
                approved.protocol_id, "pdf-999-analysis-999", "missing-asset"
            )

        source_store_handle = Mock()
        with patch(
            "voice_workflow_agent.server.server_config",
            return_value=SimpleNamespace(),
        ), patch(
            "voice_workflow_agent.server._configured_candidate_fixture",
            return_value=None,
        ), patch(
            "voice_workflow_agent.server._open_protocol_catalog",
            return_value=(self.catalog, source_store_handle),
        ):
            source_page = get_protocol_source_page(
                approved.protocol_id, approved.revision_id, 1
            )
        self.assertIn(b"Source page 1", source_page.body)
        self.assertEqual(
            source_page.headers["x-protocol-source-sha256"],
            approved.source_sha256,
        )
        source_store_handle.close.assert_called_once()
        with patch(
            "voice_workflow_agent.server.server_config",
            return_value=SimpleNamespace(),
        ), patch(
            "voice_workflow_agent.server._configured_candidate_fixture",
            return_value=None,
        ), patch(
            "voice_workflow_agent.server._open_protocol_catalog",
            return_value=(self.catalog, Mock()),
        ):
            with self.assertRaises(Exception) as unknown:
                get_protocol_visual_asset(
                    approved.protocol_id,approved.revision_id,
                    "source-page-999")
        self.assertEqual(getattr(unknown.exception,"status_code",None),404)

    def test_catalog_revision_is_acknowledged_and_frozen_for_one_listener(self):
        approved = self._approve(self.alpha, "alpha.pdf")
        placeholder = self.root / "offline-catalog.sqlite"
        config = ServerConfig(
            placeholder,
            None,
            "test_only",
            frozenset({"ko"}),
            "ko",
        )

        class Socket:
            def __init__(self):
                self.sent = []
                self.messages = iter(
                    (
                        {
                            "text": json.dumps(
                                {
                                    "type": "session.start",
                                    "mode": "cascade",
                                    "language": "ko",
                                    "protocol_id": approved.protocol_id,
                                    "configuration_id": 33,
                                }
                            )
                        },
                        {"type": "websocket.disconnect", "code": 1000},
                    )
                )

            async def accept(self):
                return None

            async def send_text(self, value):
                self.sent.append(json.loads(value))

            async def receive(self):
                return next(self.messages)

        socket = Socket()
        store_handle = Mock()
        with patch(
            "voice_workflow_agent.server.server_config", return_value=config
        ), patch(
            "voice_workflow_agent.server.server_tool_context",
            return_value=SimpleNamespace(language="ko"),
        ), patch(
            "voice_workflow_agent.server._protocol_store_settings",
            return_value=SimpleNamespace(enabled=True),
        ), patch(
            "voice_workflow_agent.server._open_protocol_catalog",
            return_value=(self.catalog, store_handle),
        ), patch(
            "voice_workflow_agent.server.ProcedureStore"
        ) as procedure_store, patch(
            "voice_workflow_agent.server.OpenAICompatibleProtocolAnalysisModel"
        ) as analysis_model:
            asyncio.run(voice_socket(socket))

        ready = next(item for item in socket.sent if item["type"] == "session.ready")
        self.assertEqual(ready["protocol_id"], approved.protocol_id)
        self.assertEqual(ready["revision_id"], approved.revision_id)
        projected = next(
            item for item in socket.sent if item["type"] == "protocol.fixture.state"
        )
        self.assertEqual(projected["state"]["revision_id"], approved.revision_id)
        self.assertFalse(projected["state"]["development_only"])
        self.assertEqual(projected["state"]["source_filename"], "alpha.pdf")
        self.assertEqual(projected["state"]["source_sha256"], approved.source_sha256)
        procedure_store.assert_not_called()
        analysis_model.assert_not_called()
        store_handle.close.assert_called_once()


class CandidateDevelopmentBootstrapTests(unittest.TestCase):
    """The controlled Candidate A runtime is explicit and development-only."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        repository = Path(__file__).resolve().parents[1]
        self.fixture = load_curated_protocol_fixture(
            repository
            / "data/development_protocols/candidate_a_curated_analysis.json",
            repository
            / "data/development_protocols/candidate_a_curated_analysis.provenance.json",
            Path("/home/student/protocol-test-files/in-gel-digestion.pdf"),
        )
        self.settings = ProtocolPersistenceSettings(
            True, self.root / "candidate-a-live-acceptance"
        )
        self.store = initialize_protocol_store(self.settings)
        self.catalog = ProtocolCatalog(self.store)

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def test_bootstrap_is_idempotent_provenance_bound_and_never_approved(self):
        first = self.catalog.bootstrap_development_fixture(self.fixture)
        second = self.catalog.bootstrap_development_fixture(self.fixture)

        self.assertFalse(first.deduplicated)
        self.assertTrue(second.deduplicated)
        self.assertEqual(first.entry.protocol_id, self.fixture.protocol_id)
        self.assertEqual(second.entry.revision_id, first.entry.revision_id)
        self.assertEqual(first.entry.readiness_status, "analysis_required")
        self.assertEqual(first.entry.approval_status, "unapproved")
        self.assertFalse(first.entry.available_for_execution)
        self.assertTrue(
            self.catalog.development_fixture_is_materialized(self.fixture)
        )
        self.assertEqual(len(self.store.list_experiments()), 1)
        self.assertEqual(
            len(self.store.list_protocol_revisions(self.fixture.protocol_id)), 1
        )
        self.assertEqual(
            len(self.store.list_analysis_revisions(self.fixture.protocol_id, 1)), 1
        )
        events = self.store.list_events(self.fixture.protocol_id)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "development_fixture_materialized")
        self.assertTrue(events[0].payload["development_only"])
        self.assertFalse(events[0].payload["final_approval"])
        self.assertNotIn("approved", {event.event_type for event in events})

        duplicate = self.catalog.register(
            self.fixture.source_pdf_path,
            source_filename="Candidate A.pdf",
            media_type="application/pdf",
        )
        self.assertTrue(duplicate.deduplicated)
        self.assertEqual(duplicate.entry.protocol_id, self.fixture.protocol_id)
        self.assertEqual(duplicate.entry.revision_id, first.entry.revision_id)

    def test_public_catalog_projects_candidate_exactly_once_and_logs_paths(self):
        self.catalog.bootstrap_development_fixture(self.fixture)
        self.store.close()

        def open_catalog():
            store = initialize_protocol_store(self.settings)
            return ProtocolCatalog(store), store

        with patch.object(
            server_module, "server_config", return_value=SimpleNamespace()
        ), patch.object(
            server_module,
            "_configured_candidate_fixture",
            return_value=self.fixture,
        ), patch.object(
            server_module,
            "_protocol_store_settings",
            return_value=self.settings,
        ), patch.object(
            server_module, "_open_protocol_catalog", side_effect=open_catalog
        ), patch.object(
            server_module, "_protocol_analysis_model"
        ) as provider_factory, patch.object(
            server_module, "ProcedureStore"
        ) as procedure_store:
            payload = list_protocol_catalog()
            with self.assertLogs("voice_workflow_agent", level="INFO") as logs:
                log_protocol_catalog_runtime_configuration()

        matching = [
            item
            for item in payload["protocols"]
            if item["protocol_id"] == self.fixture.protocol_id
        ]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["revision_id"], self.fixture.revision_id)
        self.assertEqual(
            matching[0]["approval_status"],
            "development_only_not_final_acceptance",
        )
        self.assertTrue(matching[0]["development_only"])
        rendered = "\n".join(logs.output)
        self.assertIn(
            str(self.settings.data_dir / "protocol_workspace.sqlite"), rendered
        )
        self.assertIn(
            str(self.settings.data_dir / "objects" / "sha256"), rendered
        )
        self.assertIn("visible_protocols=1", rendered)
        provider_factory.assert_not_called()
        procedure_store.assert_not_called()

        # tearDown owns a live handle; reopen it after the endpoint closed its own.
        self.store = initialize_protocol_store(self.settings)

    def test_candidate_launcher_uses_isolated_catalog_without_reload(self):
        repository = Path(__file__).resolve().parents[1]
        launcher = (repository / "scripts/run_candidate_a.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            'PROTOCOL_DATA_DIR="$ROOT/data/runtime/candidate-a-live-acceptance"',
            launcher,
        )
        self.assertIn(
            'export VOICE_WORKFLOW_AGENT_PROTOCOL_ENABLED="true"', launcher
        )
        self.assertIn(
            'export VOICE_WORKFLOW_AGENT_MOSS_ENABLED="false"', launcher
        )
        self.assertIn("bootstrap_development_fixture(fixture)", launcher)
        self.assertIn("--host 0.0.0.0", launcher)
        self.assertNotIn("--reload", launcher)


class ProtocolRegistrationEndpointTests(unittest.IsolatedAsyncioTestCase):
    """Exercise the browser's real raw-body registration HTTP contract."""

    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data_dir = self.root / "protocol-store"
        self.provider_factory = Mock(
            side_effect=AssertionError("registration must not create a Provider")
        )

    async def asyncTearDown(self) -> None:
        self.temp.cleanup()

    def _open_catalog(self):
        store = initialize_protocol_store(
            ProtocolPersistenceSettings(True, self.data_dir)
        )
        return ProtocolCatalog(store), store

    async def _request(
        self,
        method: str,
        url: str,
        *,
        content: object = None,
        content_type: str = "application/pdf",
        catalog_factory=None,
    ) -> httpx.Response:
        factory = catalog_factory or self._open_catalog
        transport = httpx.ASGITransport(app=server_module.app)
        with (
            patch.object(server_module, "_open_protocol_catalog", factory),
            patch(
                "fastapi.routing.run_in_threadpool",
                side_effect=_dedicated_to_thread,
            ),
            patch.object(
                server_module.asyncio,
                "to_thread",
                side_effect=_dedicated_to_thread,
            ),
            patch.object(
                server_module,
                "_protocol_analysis_model",
                self.provider_factory,
            ),
        ):
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
                return await client.request(
                    method,
                    url,
                    content=content,
                    headers={"Content-Type": content_type},
                )

    def _assert_store_empty(self) -> None:
        store = initialize_protocol_store(
            ProtocolPersistenceSettings(True, self.data_dir)
        )
        try:
            self.assertEqual(store.list_experiments(), ())
        finally:
            store.close()
        objects = tuple(
            (self.data_dir / "objects" / "sha256").glob("*/*.pdf")
        )
        self.assertEqual(objects, ())

    async def test_valid_candidate_registration_status_and_duplicate_are_provider_free(self):
        synthetic = self.root / "synthetic.pdf"
        synthetic_bytes = write_text_pdf(
            synthetic,
            "Protocol Synthetic\nSection preparation\n1. Add solution.",
            title="Protocol Synthetic",
        )

        first = await self._request(
            "POST",
            "/api/protocols?filename=synthetic.pdf",
            content=synthetic_bytes,
        )
        self.assertEqual(first.status_code, 201, first.text)
        first_payload = first.json()
        self.assertFalse(first_payload["deduplicated"])
        protocol_id = first_payload["protocol"]["protocol_id"]

        status = await self._request(
            "GET",
            f"/api/protocols/{protocol_id}/analysis/status",
            content_type="application/json",
        )
        self.assertEqual(status.status_code, 200, status.text)
        self.assertEqual(status.json()["state"], "structured_analysis_ready")

        duplicate = await self._request(
            "POST",
            "/api/protocols?filename=renamed.pdf",
            content=synthetic_bytes,
        )
        self.assertEqual(duplicate.status_code, 201, duplicate.text)
        self.assertTrue(duplicate.json()["deduplicated"])
        self.assertEqual(
            duplicate.json()["protocol"]["protocol_id"], protocol_id
        )

        candidate = Path("/home/student/protocol-test-files/in-gel-digestion.pdf")
        candidate_response = await self._request(
            "POST",
            "/api/protocols?filename=in-gel-digestion.pdf",
            content=candidate.read_bytes(),
        )
        self.assertEqual(candidate_response.status_code, 201, candidate_response.text)
        self.assertEqual(
            candidate_response.json()["protocol"]["source_sha256"],
            "63d81102fb644fca21e1c2296b566987756f2964ece06758fe52c73ba9c00bd9",
        )
        self.provider_factory.assert_not_called()

    async def test_malformed_empty_and_non_pdf_requests_fail_safely_without_assets(self):
        valid_path = self.root / "valid.pdf"
        valid = write_text_pdf(
            valid_path,
            "Enough valid source text for parsing.",
            title="Valid",
        )
        cases = (
            (valid[:-64], 422, "invalid_pdf", "truncated.pdf"),
            (
                b"%PDF-1.4\n1 0 obj\n<< /Length 5 >>\nstream\nabc",
                422,
                "invalid_pdf",
                "missing-trailer.pdf",
            ),
            (b"", 422, "invalid_pdf", "empty.pdf"),
            (
                b"plain text renamed as PDF",
                415,
                "unsupported_pdf_media_type",
                "renamed.pdf",
            ),
        )
        for body, expected_status, expected_code, filename in cases:
            with self.subTest(filename=filename):
                response = await self._request(
                    "POST",
                    f"/api/protocols?filename={filename}",
                    content=body,
                )
                self.assertEqual(response.status_code, expected_status, response.text)
                self.assertEqual(response.json(), {"detail": expected_code})
        self._assert_store_empty()
        self.provider_factory.assert_not_called()

    async def test_streamed_limit_media_feature_and_unexpected_errors_are_stable(self):
        async def oversized_chunks():
            yield b"%PDF-1.4\n"
            yield b"x" * 128

        with patch.object(server_module, "MAX_PROTOCOL_PDF_BYTES", 64):
            oversized = await self._request(
                "POST",
                "/api/protocols?filename=oversized.pdf",
                content=oversized_chunks(),
            )
        self.assertEqual(oversized.status_code, 413, oversized.text)
        self.assertEqual(
            oversized.json(), {"detail": "protocol_pdf_too_large"}
        )

        unsupported = await self._request(
            "POST",
            "/api/protocols?filename=renamed.pdf",
            content=b"not a PDF",
            content_type="text/plain",
        )
        self.assertEqual(unsupported.status_code, 415, unsupported.text)
        self.assertEqual(
            unsupported.json(), {"detail": "unsupported_pdf_media_type"}
        )

        valid_path = self.root / "available.pdf"
        valid = write_text_pdf(
            valid_path,
            "Enough valid source text for registration.",
            title="Available",
        )
        unavailable = await self._request(
            "POST",
            "/api/protocols?filename=available.pdf",
            content=valid,
            catalog_factory=Mock(
                side_effect=ProtocolFeatureDisabledError("disabled")
            ),
        )
        self.assertEqual(unavailable.status_code, 503, unavailable.text)
        self.assertEqual(
            unavailable.json(), {"detail": "protocol_catalog_unavailable"}
        )

        internal = await self._request(
            "POST",
            "/api/protocols?filename=available.pdf",
            content=valid,
            catalog_factory=Mock(side_effect=RuntimeError("sensitive path")),
        )
        self.assertEqual(internal.status_code, 500, internal.text)
        self.assertEqual(internal.json(), {"detail": "protocol_catalog_error"})
        self.assertNotIn("sensitive", internal.text)
        self.provider_factory.assert_not_called()

    async def test_text_empty_valid_pdf_enters_ocr_required_lifecycle(self):
        scanned = self.root / "scanned.pdf"
        scanned_bytes = write_text_pdf(scanned, None, title="Scanned")
        response = await self._request(
            "POST",
            "/api/protocols?filename=scanned.pdf",
            content=scanned_bytes,
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(
            response.json()["protocol"]["analysis_status"], "ocr_required"
        )
        self.provider_factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
