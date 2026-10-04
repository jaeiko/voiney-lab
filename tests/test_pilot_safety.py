"""Pilot safety: no demo safety wording reaches a pilot run, no user words reach its logs.

Kept apart from test_safety_pack.py, which conftest skips whenever the
licensed Candidate A PDF is absent; nothing here needs that PDF, so these
guards also run in CI (condition B).
"""

import asyncio
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from voiney_lab.curated_protocol import CuratedProtocolSession
from voiney_lab.document_store import ingest_manifest
from voiney_lab.experiment_protocol import (
    ExperimentProtocol,
    Material,
    ProtocolMetadata,
    ProtocolSection,
    ProtocolSourceStep,
    SourceEvidence,
    SourceStatement,
)
from voiney_lab.experiment_protocol_pdf import (
    ProtocolPdfExtraction,
    ProtocolPdfMetadata,
    ProtocolPdfPage,
)
from voiney_lab.external_references import ExternalReferenceSettings
from voiney_lab.language import Transcription
from voiney_lab.replay_turns import _demo_fixture as replay_fixture
from voiney_lab.safety_pack import count_catalog_documents, resolve_safety_pack
from voiney_lab.server import ListenerSession, run_turn
from voiney_lab.tools import ToolContext
from voiney_lab.vad import TurnState

ROOT = Path(__file__).resolve().parents[1]
MOSS_DEMO_MANIFEST = ROOT / "data/moss_demo/approved_documents.ko.json"
DEMO_SAFETY_MANUAL = ROOT / "data/fixtures/approved_safety_manual.demo.json"


def _evidence() -> SourceEvidence:
    return SourceEvidence(source_page_number=1, source_excerpt="excerpt")


def _protocol(materials: tuple[str, ...], warning: str) -> ExperimentProtocol:
    pdf = ProtocolPdfExtraction(
        original_filename="pilot.pdf",
        byte_size=1024,
        sha256="0" * 64,
        media_type="application/pdf",
        page_count=1,
        encrypted=False,
        metadata=ProtocolPdfMetadata(
            title="Pilot",
            author=None,
            subject=None,
            creator=None,
            producer=None,
            creation_date=None,
            modification_date=None,
        ),
        pages=(ProtocolPdfPage(source_page_number=1, text="Pilot page", text_empty=False),),
    )
    step = ProtocolSourceStep(
        step_id="step-1",
        source_label="1",
        instruction_source_text=f"Mix {materials[0]} with the buffer.",
        evidence=_evidence(),
        warnings=(SourceStatement(statement_id="warn-1", source_text=warning, evidence=_evidence()),),
    )
    return ExperimentProtocol(
        protocol_id="PILOT-SAFETY-001",
        metadata=ProtocolMetadata(pdf=pdf, title="Pilot Protocol", original_language="en"),
        materials=tuple(
            Material(material_id=f"mat-{i}", name_source_text=name, evidence=_evidence())
            for i, name in enumerate(materials, 1)
        ),
        equipment=(),
        sections=(
            ProtocolSection(
                section_id="sec-1",
                title_source_text="Section 1",
                evidence=_evidence(),
                steps=(step,),
            ),
        ),
    )


def _document(
    document_id: str,
    document_type: str,
    title: str,
    content: str,
    *,
    topic: str,
    usage_scope: str = "operational",
    product_name: str | None = None,
) -> dict:
    return {
        "document_id": document_id,
        "document_family_id": f"{document_id}-FAMILY",
        "canonical_source_id": f"{document_id}-SOURCE",
        "canonical_version": "1.0",
        "document_type": document_type,
        "title": title,
        "issuer": "Main lab safety committee",
        "manufacturer": None,
        "product_name": product_name,
        "product_code": None,
        "cas_numbers": [],
        "version": "1.0",
        "language": "en",
        "facility_id": "MAIN-LAB",
        "source_authority": "supplier" if document_type == "supplier_sds" else "facility",
        "approval_status": "approved",
        "usage_scope": usage_scope,
        "source_uri": f"https://example.invalid/{document_id}",
        "source_checksum": f"sha256:{document_id.lower()}",
        "translation_status": "original",
        "active": True,
        "sections": [{
            "section_code": "01",
            "section_title": title,
            "page_start": 1,
            "page_end": 1,
            "content": content,
            "topic": topic,
        }],
    }


def _moss_demo_documents() -> list[dict]:
    return json.loads(MOSS_DEMO_MANIFEST.read_text(encoding="utf-8"))["documents"]


class OperationalDemoFilterTests(unittest.TestCase):
    """Operational resolution never shows a demo document from a mixed catalog."""

    def _mixed_catalog(self, directory: str) -> Path:
        operational = [
            _document(
                "OPS-SOP-SPILL", "facility_sop", "Solvent spill response",
                "Contain the spill with absorbent pads and notify the lab manager.",
                topic="spill",
            ),
            _document(
                "OPS-SDS-ACN", "supplier_sds", "Acetonitrile safety data sheet",
                "Acetonitrile is highly flammable; keep it away from ignition sources.",
                topic="sds", product_name="Acetonitrile",
            ),
            # Scoped operational, but its title marks it fictional.
            _document(
                "OPS-FICTIONAL-DRILL", "facility_sop", "Fictional general drill SOP",
                "Fictional drill wording that is not a facility procedure.",
                topic="general",
            ),
        ]
        catalog = Path(directory) / "mixed_catalog.sqlite"
        ingest_manifest({"documents": _moss_demo_documents() + operational}, catalog)
        return catalog

    def test_operational_resolution_of_a_mixed_catalog_shows_no_demo_wording(self):
        # The protocol names the demo SDS product, and its step carries the
        # words that attach spill SOPs and SDS, so every leak path is open.
        demo_product = "가상 용제 MOSS-A100"
        protocol = _protocol(
            ("Acetonitrile", demo_product),
            "Toxic solvent: clean any spill immediately.",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            catalog = self._mixed_catalog(temp_dir)
            pack = resolve_safety_pack(
                protocol=protocol,
                catalog_path=catalog,
                facility_id="MAIN-LAB",
                usage_scope="operational",
            )
            # The same catalog outside operational still carries the demo
            # documents, so the operational result below is a real filter.
            reference_pack = resolve_safety_pack(
                protocol=protocol,
                catalog_path=catalog,
                facility_id="MAIN-LAB",
                usage_scope="reference_only",
            )

        self.assertIn(
            "FICTIONAL-MOSS-DEMO-SOP-KO",
            {d.document_id for d in reference_pack.sop_documents},
        )
        step = protocol.sections[0].steps[0]
        guidance = pack.guidance_for_step(step)
        documents = pack.sop_documents + pack.sds_documents + pack.equipment_documents
        self.assertEqual(
            {d.document_id for d in documents}, {"OPS-SOP-SPILL", "OPS-SDS-ACN"}
        )
        self.assertFalse(any(d.is_demo for d in documents))
        self.assertEqual(
            {d.document_id for d in guidance.applicable_documents},
            {"OPS-SOP-SPILL", "OPS-SDS-ACN"},
        )
        rendered = json.dumps(
            {"pack": pack.public_dict(), "step": guidance.public_dict()},
            ensure_ascii=False,
            indent=1,
        )
        for line in rendered.splitlines() + list(guidance.display_bullets) + list(
            guidance.localized_display_bullets
        ):
            self.assertNotIn("fictional", line.casefold())
            self.assertNotIn("데모", line)
            self.assertNotIn(demo_product, line)


class DemoFixtureFallbackTests(unittest.TestCase):
    """Without a catalog, only a demo or test runtime shows the demo records."""

    def test_the_pilot_scope_shows_no_demo_record_when_the_catalog_is_missing(self):
        demo_guidance = [
            record["translations"]["ko"]["guidance"]
            for record in json.loads(DEMO_SAFETY_MANUAL.read_text(encoding="utf-8"))
        ]
        warning = "Wear gloves when handling the solvent."
        protocol = _protocol(("Acetonitrile",), warning)
        step = protocol.sections[0].steps[0]
        expected = {
            "reference_only": "unavailable",
            "operational": "unavailable",
            "demo": "demo_only",
            "test_only": "demo_only",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = Path(temp_dir) / "no-catalog.sqlite"
            for scope, coverage in expected.items():
                with self.subTest(scope=scope):
                    pack = resolve_safety_pack(
                        protocol=protocol, catalog_path=missing, usage_scope=scope
                    )
                    self.assertEqual(pack.coverage_status, coverage)
                    if coverage == "demo_only":
                        continue
                    guidance = pack.guidance_for_step(step)
                    self.assertEqual(pack.total_document_count, 0)
                    self.assertEqual(guidance.applicable_documents, ())
                    # The protocol's own warning is all the panel keeps.
                    self.assertEqual(guidance.warnings, (warning,))
                    shown = "\n".join(
                        guidance.display_bullets + guidance.localized_display_bullets
                    )
                    for text in demo_guidance:
                        self.assertNotIn(text, shown)


class CatalogDocumentCountTests(unittest.TestCase):
    def test_a_demo_document_counts_in_any_approval_state(self):
        draft_demo = _moss_demo_documents()[0] | {"approval_status": "draft", "active": False}
        documents = [
            draft_demo,
            _document(
                "REF-SOP", "facility_sop", "Spill response", "Notify the lab manager.",
                topic="spill", usage_scope="reference_only",
            ),
            _document(
                "REF-DRAFT", "facility_sop", "Draft SOP", "Draft wording.",
                topic="spill", usage_scope="reference_only",
            ) | {"approval_status": "draft"},
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            catalog = Path(temp_dir) / "catalog.sqlite"
            ingest_manifest({"documents": documents}, catalog)
            before = catalog.read_bytes()
            self.assertEqual(count_catalog_documents(catalog, "reference_only"), (1, 1))
            self.assertEqual(catalog.read_bytes(), before)
            with self.assertRaises(sqlite3.Error):
                count_catalog_documents(Path(temp_dir) / "missing.sqlite", "reference_only")


class PilotLauncherTests(unittest.TestCase):
    """Run scripts/run_pilot.sh from a copy whose data root is a temp dir.

    ROOT is the script's parent directory, so every pilot path, the safety
    catalog included, lands in the temp tree. A stand-in .venv puts the test
    interpreter first on PATH, and refuses to run uvicorn, so a launcher that
    wrongly goes on to serve fails here instead of starting a server.
    """

    SERVE_MARKER = "stand-in python: the launcher tried to serve"

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        (self.root / "scripts").mkdir()
        launcher = self.root / "scripts" / "run_pilot.sh"
        shutil.copy2(ROOT / "scripts" / "run_pilot.sh", launcher)
        bin_dir = self.root / ".venv" / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "activate").write_text(f'export PATH="{bin_dir}:$PATH"\n', encoding="utf-8")
        python = bin_dir / "python"
        python.write_text(
            "#!/bin/sh\n"
            f'case " $* " in *" uvicorn "*) echo "{self.SERVE_MARKER}" >&2; exit 97;; esac\n'
            f'exec "{sys.executable}" "$@"\n',
            encoding="utf-8",
        )
        python.chmod(0o755)
        self.pilot_dir = self.root / "data" / "runtime" / "pilot"
        self.catalog = self.pilot_dir / "approved_safety_catalog.sqlite"

    def tearDown(self):
        self._temp.cleanup()

    def _write_catalog(self, documents: list[dict], path: Path | None = None) -> Path:
        path = path or self.catalog
        path.parent.mkdir(parents=True, exist_ok=True)
        ingest_manifest({"documents": documents}, path)
        return path

    def _reference_documents(self) -> list[dict]:
        return [_document(
            "REF-SOP-SPILL", "facility_sop", "Solvent spill response",
            "Contain the spill with absorbent pads and notify the lab manager.",
            topic="spill", usage_scope="reference_only",
        )]

    def _run(self, *args: str, environment: dict[str, str] | None = None):
        env = {
            key: value for key, value in os.environ.items()
            if key not in (
                "VOINEY_LAB_USAGE_SCOPE",
                "VOINEY_LAB_SAFETY_USAGE_SCOPE",
                "VOINEY_LAB_SAFETY_CATALOG",
            )
        }
        env.update({"PYTHONDONTWRITEBYTECODE": "1", "HOST": "127.0.0.1", "PORT": "0"})
        env.update(environment or {})
        return subprocess.run(
            ["bash", str(self.root / "scripts" / "run_pilot.sh"), *args],
            cwd=self.root, env=env, text=True, capture_output=True,
            timeout=60, check=False,
        )

    def test_check_only_prints_the_fixed_catalog_scope_and_demo_count(self):
        self._write_catalog(self._reference_documents())
        result = self._run("--check-only")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"SAFETY_CATALOG = {self.catalog}\n", result.stdout)
        self.assertIn("USAGE_SCOPE    = reference_only\n", result.stdout)
        self.assertIn("demo documents = 0\n", result.stdout)
        self.assertIn("approved active reference_only documents = 1\n", result.stdout)
        self.assertIn("[OK] --check-only", result.stdout)
        self.assertNotIn("[ERROR]", result.stdout)

    def test_a_catalog_holding_a_demo_document_is_refused_before_serving(self):
        self._write_catalog(_moss_demo_documents() + self._reference_documents())
        before = sorted(p.name for p in self.pilot_dir.iterdir())
        for args in (("--check-only",), ()):
            with self.subTest(args=args):
                result = self._run(*args)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("demo documents = 2\n", result.stdout)
                self.assertIn(
                    "[ERROR] the safety catalog holds 2 demo document(s)", result.stdout
                )
                self.assertIn("[ERROR] Refusing to start the pilot.", result.stdout)
                self.assertNotIn("Starting Voiney Lab", result.stdout)
                self.assertNotIn(self.SERVE_MARKER, result.stderr)
                self.assertNotIn("[OK]", result.stdout)
        self.assertEqual(sorted(p.name for p in self.pilot_dir.iterdir()), before)

    def test_a_missing_catalog_or_one_without_the_pilot_scope_is_refused(self):
        result = self._run("--check-only")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(f"[ERROR] no approved safety catalog at {self.catalog}", result.stdout)
        self.assertFalse(self.pilot_dir.exists())

        self._write_catalog([_document(
            "OPS-SOP", "facility_sop", "Spill response", "Notify the lab manager.",
            topic="spill",
        )])
        result = self._run("--check-only")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("demo documents = 0\n", result.stdout)
        self.assertIn(
            "[ERROR] the safety catalog has no approved active reference_only document",
            result.stdout,
        )

    def test_environment_values_for_the_scope_and_catalog_are_ignored(self):
        self._write_catalog(self._reference_documents())
        elsewhere = self._write_catalog(
            _moss_demo_documents(), self.root / "moss" / "moss_demo_catalog.sqlite"
        )
        result = self._run("--check-only", environment={
            "VOINEY_LAB_USAGE_SCOPE": "demo",
            "VOINEY_LAB_SAFETY_CATALOG": str(elsewhere),
        })
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(
            "[WARN] VOINEY_LAB_USAGE_SCOPE was set in the environment.",
            result.stdout,
        )
        self.assertIn(
            "[WARN] VOINEY_LAB_SAFETY_CATALOG was set in the environment.",
            result.stdout,
        )
        self.assertIn(f"SAFETY_CATALOG = {self.catalog}\n", result.stdout)
        self.assertIn("USAGE_SCOPE    = reference_only\n", result.stdout)
        self.assertIn("demo documents = 0\n", result.stdout)


class _Socket:
    def __init__(self):
        self.text: list[dict] = []

    async def send_text(self, value: str) -> None:
        self.text.append(json.loads(value))

    async def send_bytes(self, value: bytes) -> None:
        pass


class UtteranceFreeLogTests(unittest.TestCase):
    """A turn that escalates to web search logs no words the user said."""

    UTTERANCE = "fictional buffer 농도는 왜 그렇게 정해졌어?"

    def _session(self) -> ListenerSession:
        # The replay fixture needs no licensed PDF.
        fixture = replay_fixture()
        workflow = CuratedProtocolSession(fixture)
        workflow.active = True
        session = ListenerSession(
            tool_context=ToolContext(Path("/unused/offline-catalog"), None, "ko", "test_only"),
            curated_protocol_session=workflow,
        )
        session.active = True
        session.active_turn_id = 1
        session.next_turn_id = 2
        session.turn_generations[1] = session.generation
        session.accept_configuration(41, "cascade", "ko", fixture.protocol_id)
        session.detector.state = TurnState.PROCESSING
        session.external_reference_settings = ExternalReferenceSettings(
            True, ("pubchem.ncbi.nlm.nih.gov",), "offline-web", 2.0, 3, "candidate_a",
        )
        return session

    def test_the_external_search_log_keeps_only_the_query_size_and_digest(self):
        session = self._session()
        queries: list[str] = []

        async def immediate(function, *args, **kwargs):
            return function(*args, **kwargs)

        async def unsupported(*args, **kwargs):
            return SimpleNamespace(
                intent="unsupported", primary_text="", evidence_ids=(),
                inference_labels=(), unsupported_parts=("definition",),
            )

        class Web:
            def __init__(self, *args):
                pass

            async def search(self, query, *, language):
                queries.append(query)
                return {"status": "success", "answer": "", "matches": [],
                        "backend": "xai_responses_web_search"}

        with patch(
            "voiney_lab.server.transcribe",
            return_value=Transcription(self.UTTERANCE, "ko"),
        ), patch(
            "voiney_lab.server.synthesize", return_value=b"\0\0",
        ), patch(
            "voiney_lab.server.answer_curated_protocol_question", side_effect=unsupported,
        ), patch(
            "voiney_lab.server.search_approved_lab_references",
            return_value={"status": "no_admissible_evidence", "answerable": False,
                          "matches": [], "retrieval": {"backend": "sqlite"}},
        ), patch(
            "voiney_lab.server.XaiAuthoritativeWebSearch", Web,
        ), patch(
            "voiney_lab.server.AsyncOpenAI", return_value=SimpleNamespace(),
        ), patch(
            "voiney_lab.server.require_env", return_value="offline",
        ), patch(
            "voiney_lab.server.asyncio.to_thread", side_effect=immediate,
        ), self.assertLogs("voiney_lab", level="DEBUG") as captured:
            asyncio.run(run_turn(_Socket(), session, b"\0\0", 1, 1))

        # The provider really was handed the user's question ...
        self.assertEqual(len(queries), 1)
        spoken = self.UTTERANCE.rstrip("?")
        self.assertIn(spoken, queries[0])
        # ... and the log line names only its size and digest.
        started = [
            line for line in captured.output
            if "external_search.provider_started" in line
        ]
        self.assertEqual(len(started), 1)
        digest = hashlib.sha256(queries[0].encode("utf-8")).hexdigest()[:16]
        self.assertIn(f"query_chars={len(queries[0])} query_sha256={digest}", started[0])
        for line in captured.output:
            self.assertNotIn(spoken, line)
            self.assertNotIn("농도", line)
            self.assertNotIn("Question:", line)


if __name__ == "__main__":
    unittest.main()
