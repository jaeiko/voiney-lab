"""Pilot safety: no demo safety wording reaches a pilot run.

Kept apart from test_safety_pack.py, which conftest skips whenever the
licensed Candidate A PDF is absent; nothing here needs that PDF, so these
guards also run in CI (condition B).
"""

import json
import tempfile
import unittest
from pathlib import Path

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
from voiney_lab.safety_pack import resolve_safety_pack

ROOT = Path(__file__).resolve().parents[1]
MOSS_DEMO_MANIFEST = ROOT / "data/moss_demo/approved_documents.ko.json"


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


if __name__ == "__main__":
    unittest.main()
