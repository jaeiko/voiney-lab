"""Lane PL, stage 2a: the pilot can start without institution safety documents.

Human decision of 2026-10-13 ("B안"): the pilot launcher may start with the
safety statements of the uploaded PDF alone. The server takes a new usage scope
for that, ``source_protocol_only``. Under it there is no institution safety
document search and no approved-catalog requirement, and the places that show
or speak about safety say so in one server sentence --
"기관 안전 문서 없음 · 원문 안전 주의만": the start screen's review, the
``session.safety_pack`` event, the answer to a safety question, and the start
record in the experiment ledger. Every scope that has an approved catalog is
left as it was.

The sentence is the server's; the screen (lane UI2) shows it and never words
it. Nothing here calls a provider.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from tests.test_pilot_safety import _document, _protocol
from tests.test_protocol_catalog import write_text_pdf
from voiney_lab import server as server_module
from voiney_lab.curated_protocol import CuratedProtocolSession
from voiney_lab.document_store import ingest_manifest
from voiney_lab.experiment_protocol_config import ProtocolPersistenceSettings
from voiney_lab.experiment_protocol_store import initialize_protocol_store
from voiney_lab.experiment_reports import ExperimentReportStore
from voiney_lab.language import Transcription
from voiney_lab.protocol_catalog import ProtocolCatalog
from voiney_lab.replay_turns import _demo_fixture as replay_fixture
from voiney_lab.safety_pack import (
    NO_INSTITUTION_DOCUMENTS_NOTICE,
    SOURCE_PROTOCOL_ONLY_SCOPE,
    resolve_safety_pack,
)
from voiney_lab.server import (
    ListenerSession,
    ServerConfigurationError,
    get_protocol_review,
    list_protocol_catalog,
    run_turn,
    server_config,
)
from voiney_lab.tools import ToolContext
from voiney_lab.vad import TurnState

NOTICE = "기관 안전 문서 없음 · 원문 안전 주의만"
SPEECH_PROVIDERS = {"VOINEY_LAB_STT_PROVIDER": "xai", "VOINEY_LAB_TTS_PROVIDER": "xai"}


def _reference_catalog(path: Path) -> Path:
    ingest_manifest({"documents": [_document(
        "REF-SOP-SPILL", "facility_sop", "Solvent spill response",
        "Contain the spill with absorbent pads and notify the lab manager.",
        topic="spill", usage_scope="reference_only",
    )]}, path)
    return path


class ScopeNameTests(unittest.TestCase):
    def test_the_notice_is_the_sentence_the_decision_wrote(self):
        self.assertEqual(NO_INSTITUTION_DOCUMENTS_NOTICE, NOTICE)
        self.assertEqual(SOURCE_PROTOCOL_ONLY_SCOPE, "source_protocol_only")


class ServerPolicyTests(unittest.TestCase):
    def test_the_scope_needs_no_approved_catalog_and_reads_none(self):
        with tempfile.TemporaryDirectory() as temp:
            catalog = _reference_catalog(Path(temp) / "approved.sqlite")
            for environment in (
                {},
                {"VOINEY_LAB_SAFETY_CATALOG": str(catalog)},
                {"VOINEY_LAB_SAFETY_CATALOG": str(Path(temp) / "missing.sqlite")},
                {"VOINEY_LAB_SAFETY_CATALOG": "relative/path.sqlite"},
            ):
                with self.subTest(environment=sorted(environment)), patch.dict(
                    os.environ,
                    {"VOINEY_LAB_USAGE_SCOPE": "source_protocol_only", **environment},
                    clear=True,
                ):
                    config = server_config()
                self.assertEqual(config.usage_scope, "source_protocol_only")
                # No catalog is carried, so none can be opened by a later step.
                self.assertIsNone(config.catalog_path)

    def test_every_scope_with_a_catalog_still_requires_one(self):
        for scope in ("operational", "demo", "reference_only", "test_only"):
            with self.subTest(scope=scope), patch.dict(
                os.environ, {"VOINEY_LAB_USAGE_SCOPE": scope}, clear=True,
            ), self.assertRaises(ServerConfigurationError) as caught:
                server_config()
            self.assertEqual(caught.exception.field_names, ("VOINEY_LAB_SAFETY_CATALOG",))

    def test_a_catalog_without_an_approved_document_of_its_scope_is_still_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            catalog = _reference_catalog(Path(temp) / "approved.sqlite")
            with patch.dict(os.environ, {
                "VOINEY_LAB_USAGE_SCOPE": "operational",
                "VOINEY_LAB_SAFETY_CATALOG": str(catalog),
            }, clear=True), self.assertRaises(ServerConfigurationError) as caught:
                server_config()
            self.assertIn("VOINEY_LAB_USAGE_SCOPE", caught.exception.field_names)

    def test_a_misspelled_scope_is_still_refused(self):
        with patch.dict(os.environ, {
            "VOINEY_LAB_USAGE_SCOPE": "source_protocol_onl",
        }, clear=True), self.assertRaises(ServerConfigurationError):
            server_config()


class SafetyPackTests(unittest.TestCase):
    WARNING = "Wear gloves when handling the solvent."

    def test_the_pack_holds_no_institution_document_and_says_so(self):
        protocol = _protocol(("Acetonitrile",), self.WARNING)
        step = protocol.sections[0].steps[0]
        with tempfile.TemporaryDirectory() as temp:
            # A catalog that has a document for exactly this protocol: the
            # scope is the statement that there is none, so it is not opened.
            catalog = Path(temp) / "approved.sqlite"
            ingest_manifest({"documents": [_document(
                "REF-SDS-ACN", "supplier_sds", "Acetonitrile SDS", "Flammable liquid.",
                topic="hazards", usage_scope="reference_only", product_name="Acetonitrile",
            )]}, catalog)
            for path in (catalog, Path(temp) / "no-catalog.sqlite", None):
                with self.subTest(catalog=bool(path and path.exists())):
                    pack = resolve_safety_pack(
                        protocol=protocol, catalog_path=path, usage_scope="source_protocol_only",
                    )
                    self.assertEqual(pack.coverage_status, "source_protocol_only")
                    self.assertEqual(pack.total_document_count, 0)
                    self.assertEqual(pack.scope_notice, NOTICE)
                    self.assertEqual(pack.public_dict()["scope_notice"], NOTICE)
                    self.assertEqual(pack.missing_coverage, ("institution_safety_documents",))
                    guidance = pack.guidance_for_step(step)
                    # The protocol's own statement is kept; nothing else is added.
                    self.assertEqual(guidance.warnings, (self.WARNING,))
                    self.assertEqual(guidance.applicable_documents, ())

    def test_an_ordinary_pack_carries_no_notice(self):
        protocol = _protocol(("Acetonitrile",), self.WARNING)
        with tempfile.TemporaryDirectory() as temp:
            for scope in ("reference_only", "operational", "demo"):
                pack = resolve_safety_pack(
                    protocol=protocol, catalog_path=Path(temp) / "none.sqlite", usage_scope=scope,
                )
                with self.subTest(scope=scope):
                    self.assertIsNone(pack.scope_notice)
                    self.assertIsNone(pack.public_dict()["scope_notice"])
                    self.assertNotEqual(pack.coverage_status, "source_protocol_only")


class StartScreenTests(unittest.TestCase):
    """The review the start screen reads, through the real endpoints."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.settings = ProtocolPersistenceSettings(True, root / "catalog")
        store = initialize_protocol_store(self.settings)
        pdf = root / "protocol.pdf"
        write_text_pdf(pdf, "Fictional protocol. 1 Add buffer to the tube.", title="Fictional")
        self.entry = ProtocolCatalog(store).register(
            pdf, source_filename="protocol.pdf", media_type="application/pdf").entry
        store.close()
        self.reference_catalog = _reference_catalog(root / "approved.sqlite")

    def environment(self, **extra: str) -> dict[str, str]:
        return {
            "VOINEY_LAB_PROTOCOL_ENABLED": "true",
            "VOINEY_LAB_PROTOCOL_DATA_DIR": str(self.settings.data_dir),
            "VOINEY_LAB_WORKSPACE_ENABLED": "false",
            **extra,
        }

    def test_the_uploaded_protocol_is_listed_and_reviewed_without_a_catalog(self):
        with patch.dict(os.environ, self.environment(
            VOINEY_LAB_USAGE_SCOPE="source_protocol_only"), clear=True):
            listed = list_protocol_catalog()
            review = get_protocol_review(self.entry.protocol_id)
        self.assertEqual(
            [item["protocol_id"] for item in listed["protocols"]], [self.entry.protocol_id])
        self.assertEqual(review["safety_scope"], {
            "usage_scope": "source_protocol_only",
            "source_protocol_only": True,
            "notice": NOTICE,
        })

    def test_without_the_scope_and_without_a_catalog_the_server_still_refuses(self):
        with patch.dict(os.environ, self.environment(
            VOINEY_LAB_USAGE_SCOPE="reference_only"), clear=True), self.assertRaises(HTTPException):
            list_protocol_catalog()

    def test_a_scope_with_a_catalog_is_listed_as_before_and_carries_no_notice(self):
        with patch.dict(os.environ, self.environment(
            VOINEY_LAB_USAGE_SCOPE="reference_only",
            VOINEY_LAB_SAFETY_CATALOG=str(self.reference_catalog)), clear=True):
            listed = list_protocol_catalog()
            review = get_protocol_review(self.entry.protocol_id)
        self.assertEqual(len(listed["protocols"]), 1)
        self.assertEqual(review["safety_scope"], {
            "usage_scope": "reference_only",
            "source_protocol_only": False,
            "notice": None,
        })


class _Socket:
    def __init__(self):
        self.text: list[dict] = []

    async def send_text(self, value: str) -> None:
        self.text.append(json.loads(value))

    async def send_bytes(self, value: bytes) -> None:
        pass


async def _immediate(function, *args, **kwargs):
    return function(*args, **kwargs)


def ask(utterance: str, scope: str):
    """One turn on the replay fixture (no licensed PDF), its events and what was said."""

    fixture = replay_fixture()
    workflow = CuratedProtocolSession(fixture)
    workflow.active = True
    session = ListenerSession(
        tool_context=ToolContext(None, None, "ko", scope), curated_protocol_session=workflow)
    session.active = True
    session.active_turn_id = 1
    session.next_turn_id = 2
    session.turn_generations[1] = session.generation
    session.accept_configuration(41, "cascade", "ko", fixture.protocol_id)
    session.detector.state = TurnState.PROCESSING
    socket = _Socket()
    spoken: list[str] = []

    def synthesize(text, language=None):
        spoken.append(text)
        return b"\0\0"

    with patch.dict(os.environ, SPEECH_PROVIDERS), patch(
        "voiney_lab.server.transcribe", return_value=Transcription(utterance, "ko"),
    ), patch("voiney_lab.server.synthesize", side_effect=synthesize), patch(
        "voiney_lab.server.asyncio.to_thread", side_effect=_immediate,
    ):
        asyncio.run(run_turn(socket, session, b"\0\0", 1, 1))
    shown = [event["text"] for event in socket.text if event["type"] == "reply.complete"]
    return shown, spoken, workflow


class SafetyAnswerTests(unittest.TestCase):
    def test_a_safety_answer_carries_the_line_on_the_screen_only(self):
        for utterance in ("이 단계 주의사항 알려줘", "장갑 꼭 껴야 돼?", "후드 안에서 해야 돼?"):
            with self.subTest(utterance=utterance):
                shown, spoken, workflow = ask(utterance, "source_protocol_only")
                self.assertEqual(len(shown), 1)
                self.assertTrue(shown[0].endswith(f"\n\n{NOTICE}"), shown[0])
                self.assertEqual(shown[0].count(NOTICE), 1)
                for words in spoken:
                    self.assertNotIn(NOTICE, words)
                # A read-only answer: the step has not moved.
                self.assertEqual(workflow.current_index, 0)

    def test_the_same_answers_carry_no_line_when_an_approved_catalog_stands_behind_them(self):
        for scope in ("reference_only", "operational", "demo", "test_only"):
            with self.subTest(scope=scope):
                shown, _, _ = ask("이 단계 주의사항 알려줘", scope)
                self.assertEqual(len(shown), 1)
                self.assertNotIn(NOTICE, shown[0])

    def test_an_answer_that_is_not_about_safety_carries_no_line(self):
        for utterance in ("지금 몇 단계야?", "이 단계 다시 읽어줘"):
            with self.subTest(utterance=utterance):
                shown, _, _ = ask(utterance, "source_protocol_only")
                self.assertTrue(shown)
                self.assertNotIn(NOTICE, shown[0])

    def test_a_turn_that_changes_state_carries_no_line(self):
        shown, _, workflow = ask("1단계 끝났어, 다음 단계로 가줘", "source_protocol_only")
        for text in shown:
            self.assertNotIn(NOTICE, text)


class StartRecordTests(unittest.TestCase):
    def record(self, scope: str) -> dict:
        with tempfile.TemporaryDirectory() as temp:
            store = ExperimentReportStore(Path(temp) / "reports.sqlite")
            fixture = replay_fixture()
            workflow = CuratedProtocolSession(fixture)
            session = ListenerSession(
                tool_context=ToolContext(None, None, "ko", scope),
                curated_protocol_session=workflow, experiment_report_store=store,
            )
            session.safety_notices_shown = [{
                "step_label": "1", "source_page_number": 1,
                "source_text": "Wear gloves when handling the solvent.",
            }]
            with patch.object(server_module, "_scope_tenant_resource"):
                report = server_module._open_experiment_report(session, workflow)
            events = store.get_report(report["report_id"])["events"]
        return next(
            event["payload"] for event in events
            if event["event_type"] == "safety_notices_acknowledged")

    def test_the_start_record_says_the_experimenter_started_without_institution_documents(self):
        payload = self.record("source_protocol_only")
        self.assertEqual(payload["notice_count"], 1)
        self.assertEqual(payload["confirmed_by"], "experimenter_start")
        self.assertEqual(payload["safety_scope"], "source_protocol_only")
        self.assertEqual(payload["institution_safety_notice"], NOTICE)
        # The source's own statements are recorded as before.
        self.assertEqual(payload["notices"][0]["source_text"], "Wear gloves when handling the solvent.")

    def test_the_start_record_of_any_other_scope_is_what_it_was(self):
        payload = self.record("reference_only")
        self.assertEqual(
            set(payload), {"notice_count", "notices", "confirmed_by"})


if __name__ == "__main__":
    unittest.main()
