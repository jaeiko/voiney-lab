"""Provider-free multi-Protocol catalog, revision, and visual tests."""

from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from voice_workflow_agent import experiment_protocol as domain
from voice_workflow_agent.curated_protocol import CuratedProtocolSession
from voice_workflow_agent.experiment_protocol_analysis import (
    ProtocolAnalysisDraft,
    ProtocolAnalysisInputTooLargeError,
)
from voice_workflow_agent.experiment_protocol_config import ProtocolPersistenceSettings
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
from voice_workflow_agent.server import (
    ServerConfig,
    get_protocol_visual_asset,
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

    def test_visual_is_exact_pdf_page_fallback_and_asset_lookup_is_strict(self):
        approved = self._approve(self.alpha, "alpha.pdf")
        fixture = self.catalog.load_executable_fixture(approved.protocol_id)
        asset = fixture.visual_for_step(0)
        self.assertIsNotNone(asset)
        self.assertEqual(asset.kind, "full_source_page_preview")
        self.assertEqual(asset.source_page, 1)
        self.assertEqual(asset.sha256, hashlib.sha256(self.alpha.read_bytes()).hexdigest())
        resolved = self.catalog.resolve_asset(
            approved.protocol_id, approved.revision_id, asset.asset_id
        )
        self.assertEqual(resolved.path.read_bytes(), self.alpha.read_bytes())
        with self.assertRaises(ProtocolCatalogNotFoundError):
            self.catalog.resolve_asset(
                approved.protocol_id, approved.revision_id, "../source-page-1"
            )
        with self.assertRaises(ProtocolCatalogNotFoundError):
            self.catalog.resolve_asset(
                approved.protocol_id, "pdf-999-analysis-999", asset.asset_id
            )

        store_handle = Mock()
        with patch(
            "voice_workflow_agent.server.server_config",
            return_value=SimpleNamespace(),
        ), patch(
            "voice_workflow_agent.server._configured_candidate_fixture",
            return_value=None,
        ), patch(
            "voice_workflow_agent.server._open_protocol_catalog",
            return_value=(self.catalog, store_handle),
        ):
            response = get_protocol_visual_asset(
                approved.protocol_id, approved.revision_id, asset.asset_id
            )
        self.assertEqual(Path(response.path), resolved.path)
        self.assertEqual(
            response.headers["x-protocol-source-sha256"], approved.source_sha256
        )
        self.assertEqual(response.media_type, "application/pdf")
        store_handle.close.assert_called_once()

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


if __name__ == "__main__":
    unittest.main()
