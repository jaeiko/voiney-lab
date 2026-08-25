import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from voice_workflow_agent.curated_protocol import load_curated_protocol_fixture
from voice_workflow_agent.document_store import SCHEMA, CATALOG_SCHEMA_VERSION
from voice_workflow_agent.experiment_protocol import (
    Equipment,
    ExperimentProtocol,
    Material,
    ProtocolMetadata,
    ProtocolSection,
    ProtocolSourceStep,
    ProtocolSubAction,
    SourceEvidence,
    SourceStatement,
)
from voice_workflow_agent.experiment_protocol_pdf import (
    ProtocolPdfExtraction,
    ProtocolPdfMetadata,
    ProtocolPdfPage,
)
from voice_workflow_agent.safety_pack import (
    ProtocolSafetySubjects,
    SafetyDocumentRef,
    SafetyPack,
    StepSafetyGuidance,
    collect_protocol_safety_subjects,
    resolve_safety_pack,
    resolve_step_safety_context,
    unavailable_safety_pack,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "data/development_protocols/candidate_a_curated_analysis.json"
PROVENANCE = ROOT / "data/development_protocols/candidate_a_curated_analysis.provenance.json"
SOURCE_PDF = ROOT / "data/development_protocols/candidate_a_source_in_gel_digestion.pdf"
if not SOURCE_PDF.exists():
    SOURCE_PDF = (Path(__file__).resolve().parents[1] / "data" / "runtime" / "candidate-a-source" / "in-gel-digestion.pdf")


def _dummy_evidence() -> SourceEvidence:
    return SourceEvidence(source_page_number=1, source_excerpt="dummy excerpt")


def _create_test_protocol(
    protocol_id: str = "PROTO-TEST-001",
    materials: tuple[str, ...] = ("Acetone", "Ethanol"),
    equipment: tuple[str, ...] = ("Centrifuge 5424 R",),
    warnings: tuple[str, ...] = ("Flammable solvent",),
) -> ExperimentProtocol:
    pdf = ProtocolPdfExtraction(
        original_filename="test.pdf",
        byte_size=1024,
        sha256="0" * 64,
        media_type="application/pdf",
        page_count=1,
        encrypted=False,
        metadata=ProtocolPdfMetadata(
            title="Test",
            author=None,
            subject=None,
            creator=None,
            producer=None,
            creation_date=None,
            modification_date=None,
        ),
        pages=(ProtocolPdfPage(source_page_number=1, text="Test page", text_empty=False),),
    )
    meta = ProtocolMetadata(
        pdf=pdf,
        title="Test Protocol",
        original_language="en",
    )
    mat_objs = tuple(
        Material(
            material_id=f"mat-{i}",
            name_source_text=m,
            evidence=_dummy_evidence(),
        )
        for i, m in enumerate(materials, 1)
    )
    eq_objs = tuple(
        Equipment(
            equipment_id=f"eq-{i}",
            name_source_text=e,
            evidence=_dummy_evidence(),
        )
        for i, e in enumerate(equipment, 1)
    )
    step1 = ProtocolSourceStep(
        step_id="step-1",
        source_label="1",
        instruction_source_text=f"Mix {materials[0] if materials else 'sample'} carefully.",
        evidence=_dummy_evidence(),
        warnings=tuple(SourceStatement(statement_id=f"warn-{i}", source_text=w, evidence=_dummy_evidence()) for i, w in enumerate(warnings, 1)),
    )
    return ExperimentProtocol(
        protocol_id=protocol_id,
        metadata=meta,
        materials=mat_objs,
        equipment=eq_objs,
        sections=(
            ProtocolSection(
                section_id="sec-1",
                title_source_text="Section 1",
                evidence=_dummy_evidence(),
                steps=(step1,),
            ),
        ),
    )


class SafetyPackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = load_curated_protocol_fixture(FIXTURE, PROVENANCE, SOURCE_PDF)
        cls.candidate_a_protocol = cls.fixture.draft.protocol

    def test_17a_domain_contract_no_step_materials_or_equipment_expected(self):
        """Domain Contract: Ensure SafetyPack resolver works strictly with ExperimentProtocol model without step.materials."""
        protocol = self.candidate_a_protocol
        # Verify domain contract: ProtocolSourceStep has no materials or equipment
        first_step = protocol.sections[0].steps[0]
        self.assertFalse(hasattr(first_step, "materials"))
        self.assertFalse(hasattr(first_step, "equipment"))

        # collect_protocol_safety_subjects extracts from protocol level
        subjects = collect_protocol_safety_subjects(protocol)
        self.assertIsInstance(subjects, ProtocolSafetySubjects)
        self.assertIn("candidate-a", subjects.protocol_id)

        # resolve_safety_pack resolves without any attribute errors
        pack = resolve_safety_pack(
            protocol=protocol,
            catalog_path=None,
            facility_id="MAIN-LAB",
            usage_scope="demo",
        )
        self.assertIsInstance(pack, SafetyPack)

    def test_17b_candidate_a_no_safety_pack_operational_mode(self):
        """Candidate A with safety disabled/unconfigured in operational mode."""
        pack = resolve_safety_pack(
            protocol=self.candidate_a_protocol,
            catalog_path=None,
            facility_id="MAIN-LAB",
            usage_scope="operational",
        )
        self.assertEqual(pack.coverage_status, "unavailable")
        self.assertEqual(pack.total_document_count, 0)
        self.assertEqual(len(pack.sop_documents), 0)
        self.assertEqual(len(pack.sds_documents), 0)

        # Step safety guidance still preserves protocol PDF warnings
        first_step = self.candidate_a_protocol.sections[0].steps[0]
        guidance = pack.guidance_for_step(first_step)
        self.assertIsInstance(guidance, StepSafetyGuidance)
        self.assertEqual(guidance.applicable_documents, ())
        self.assertEqual(guidance.ppe_requirements, ())
        # Protocol warnings from step are preserved
        self.assertTrue(len(guidance.warnings) > 0)
        self.assertIn("실험 PDF p.", guidance.citation_label)

    def test_17c_candidate_a_demo_safety_pack(self):
        """Candidate A with demo safety pack enabled."""
        pack = resolve_safety_pack(
            protocol=self.candidate_a_protocol,
            catalog_path=None,
            facility_id="MAIN-LAB",
            usage_scope="demo",
        )
        self.assertEqual(pack.coverage_status, "demo_only")
        self.assertGreater(pack.total_document_count, 0)

        first_step = self.candidate_a_protocol.sections[0].steps[0]
        guidance = pack.guidance_for_step(first_step)
        self.assertIsInstance(guidance, StepSafetyGuidance)
        self.assertIn("p.", guidance.citation_label)

    def test_17d_no_match_in_catalog(self):
        """Valid protocol, valid catalog, but no matching materials/equipment in DB."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "safety_catalog.sqlite"
            conn = sqlite3.connect(db_path)
            conn.executescript(SCHEMA)
            conn.execute("INSERT INTO catalog_metadata VALUES (2)")
            # Insert unrelated document
            conn.execute(
                """
                INSERT INTO documents (
                    document_id, document_family_id, canonical_source_id, canonical_version,
                    document_type, title, issuer, manufacturer, product_name, product_code,
                    cas_numbers, version, language, facility_id, source_authority,
                    approval_status, usage_scope, source_checksum, translation_status, active
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "SDS-UNRELATED", "FAM-1", "SRC-1", "1", "supplier_sds", "Unrelated Chemical SDS",
                    "OSHA", "Acme", "Xenon Difluoride", "XF2", "[]", "1.0", "en",
                    None, "facility_safety_committee", "approved", "operational", "a" * 64, "completed", 1,
                ),
            )
            conn.commit()
            conn.close()

            proto = _create_test_protocol(materials=("Acetone",), equipment=("Centrifuge",))
            pack = resolve_safety_pack(
                protocol=proto,
                catalog_path=db_path,
                facility_id="MAIN-LAB",
                usage_scope="operational",
            )
            self.assertEqual(pack.coverage_status, "unavailable")
            self.assertEqual(pack.total_document_count, 0)

    def test_17e_partial_match_in_catalog(self):
        """Protocol has multiple materials, DB only has one matching material."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "safety_catalog.sqlite"
            conn = sqlite3.connect(db_path)
            conn.executescript(SCHEMA)
            conn.execute("INSERT INTO catalog_metadata VALUES (2)")
            # Insert matching Acetone SDS
            conn.execute(
                """
                INSERT INTO documents (
                    document_id, document_family_id, canonical_source_id, canonical_version,
                    document_type, title, issuer, manufacturer, product_name, product_code,
                    cas_numbers, version, language, facility_id, source_authority,
                    approval_status, usage_scope, source_checksum, translation_status, active
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "SDS-ACETONE", "FAM-1", "SRC-1", "1", "supplier_sds", "Acetone Safety Data Sheet",
                    "OSHA", "Acme", "Acetone", "ACT-01", "[]", "1.0", "en",
                    None, "facility_safety_committee", "approved", "operational", "b" * 64, "completed", 1,
                ),
            )
            conn.commit()
            conn.close()

            proto = _create_test_protocol(materials=("Acetone", "DTT", "Acetonitrile"), equipment=("Incubator",))
            pack = resolve_safety_pack(
                protocol=proto,
                catalog_path=db_path,
                facility_id="MAIN-LAB",
                usage_scope="operational",
            )
            self.assertEqual(pack.coverage_status, "partial")
            self.assertEqual(len(pack.sds_documents), 1)
            self.assertEqual(pack.sds_documents[0].document_id, "SDS-ACETONE")

    def test_17f_safety_resolver_failure_isolation(self):
        """Controlled failure returns unavailable SafetyPack without crashing."""
        fallback = unavailable_safety_pack(
            protocol_id="candidate-a-test",
            status="unavailable",
            error_reason="database_corrupt",
        )
        self.assertEqual(fallback.coverage_status, "unavailable")
        self.assertEqual(fallback.total_document_count, 0)
        self.assertEqual(fallback.missing_coverage, ("all_safety_documents",))

    def test_17g_pdf_warnings_without_sop(self):
        """Protocol has explicit PDF warnings; when SafetyPack is unavailable, PDF warnings remain."""
        proto = _create_test_protocol(warnings=("Always wear clean gloves and eye protection.",))
        pack = unavailable_safety_pack(protocol_id="PROTO-TEST-001")
        step = proto.sections[0].steps[0]
        guidance = pack.guidance_for_step(step)
        self.assertEqual(len(guidance.applicable_documents), 0)
        self.assertEqual(guidance.warnings, ("Always wear clean gloves and eye protection.",))


if __name__ == "__main__":
    unittest.main()
