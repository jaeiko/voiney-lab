"""Fictional protocols for the protocol-derived vocabulary tests.

Each one is built in memory from exactly the text a test gives it, the way the
replay harness builds its fixture, so a test can say which words a protocol
contains -- and therefore which of the in-gel document's words it does not.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from voiney_lab import experiment_protocol as domain
from voiney_lab.curated_protocol import (
    CuratedProtocolFixture,
    load_curated_protocol_fixture,
)
from voiney_lab.experiment_protocol_analysis import ProtocolAnalysisDraft
from voiney_lab.experiment_protocol_pdf import (
    ProtocolPdfExtraction,
    ProtocolPdfMetadata,
    ProtocolPdfPage,
)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/fixtures/development_protocols"
SOURCE_PDF = ROOT / "data/runtime/candidate-a-source/in-gel-digestion.pdf"

#: What stt_keyterms() sent for every protocol before this change: the in-gel
#: document's terms, as a literal.
IN_GEL_TERMS = (
    "AMBIC", "ammonium bicarbonate", "HPLC water", "acetonitrile",
    "Solution A", "Solution B", "DTT", "iodoacetamide", "trypsin",
    "formic acid", "LC-MS", "SDS-PAGE", "gel plug",
    "stained protein band", "Thermomixer", "rpm", "incubation",
    "keratin", "contamination", "Evotip",
)

#: A plasmid miniprep with none of the in-gel document's words.
MINIPREP_STEPS = (
    "1 Resuspend the cell pellet in 250 µL Buffer 1 made with Tris-HCl buffer.",
    "2 Add 10 µL lysozyme and mix by inverting the tube five times.",
    "3 Wash the column with 500 µL ethanol in phosphate-buffered saline (PBS) "
    "and spin it in the microcentrifuge for 1 minute.",
    "4 Elute the DNA with 50 µL water.",
    "5 Check the product by RT-PCR.",
)
MINIPREP_MATERIALS = (
    "Tris-HCl buffer Sigma-Aldrich Catalog #T1503",
    "Lysozyme Thermo Fisher Scientific Catalog #89833",
    "Ethanol absolute VWR Catalog #20821",
)
MINIPREP_EQUIPMENT = (
    "Benchtop microcentrifuge Model 5424\nNAME\nmicrocentrifuge TYPE\n"
    "Eppendorf\nBRAND\n5424000010\nSKU",
)


def in_gel_fixture() -> CuratedProtocolFixture:
    return load_curated_protocol_fixture(
        DATA / "candidate_a_curated_analysis.json",
        DATA / "candidate_a_curated_analysis.provenance.json",
        SOURCE_PDF,
    )


def build_fixture(
    *,
    protocol_id: str,
    title: str,
    steps: tuple[str, ...],
    materials: tuple[str, ...] = (),
    equipment: tuple[str, ...] = (),
    abstract: str = "A fictional protocol for vocabulary tests.",
) -> CuratedProtocolFixture:
    """One validated, source-linked fixture made of exactly this text."""

    page_text = "\n".join((title, *steps, *materials, *equipment))
    overview_text = (
        f"Abstract\n{abstract}\n"
        "Protocol materials\nsee page 1\n"
        "Safety warnings\nWear gloves.\n"
        "Before start\nRead every step first."
    )
    source = f"{page_text}\n{overview_text}".encode("utf-8")
    extraction = ProtocolPdfExtraction(
        original_filename=f"{protocol_id}.pdf",
        byte_size=len(source),
        sha256=hashlib.sha256(source).hexdigest(),
        media_type="application/pdf",
        page_count=2,
        encrypted=False,
        metadata=ProtocolPdfMetadata(
            title=title,
            author="Voice Workflow Agent tests",
            subject="Fictional vocabulary fixture",
            creator="tests",
            producer="tests",
            creation_date=None,
            modification_date=None,
        ),
        pages=(
            ProtocolPdfPage(1, page_text, False),
            ProtocolPdfPage(2, overview_text, False),
        ),
    )

    def evidence(text: str) -> domain.SourceEvidence:
        return domain.SourceEvidence(1, text)

    protocol = domain.ExperimentProtocol(
        protocol_id=protocol_id,
        metadata=domain.ProtocolMetadata(
            pdf=extraction,
            title=title,
            original_language="en",
            version="1.0-test",
            source_status="fictional_non_operational",
            evidence=evidence(title),
        ),
        materials=tuple(
            domain.Material(f"material-{index}", name, evidence(name))
            for index, name in enumerate(materials, 1)
        ),
        equipment=tuple(
            domain.Equipment(f"equipment-{index}", name, evidence(name))
            for index, name in enumerate(equipment, 1)
        ),
        sections=(
            domain.ProtocolSection(
                "steps",
                title,
                evidence(title),
                steps=tuple(
                    domain.ProtocolSourceStep(
                        f"step-{index}", str(index), text, evidence(text),
                    )
                    for index, text in enumerate(steps, 1)
                ),
            ),
        ),
    )
    domain.validate_protocol(protocol)
    draft = ProtocolAnalysisDraft(
        extraction=extraction,
        protocol=protocol,
        readiness=domain.assess_readiness(protocol),
        capability_policy=domain.P1_CAPABILITY_POLICY,
        analysis_schema_version=1,
        verified_evidence_count=len(steps) + len(materials) + len(equipment) + 1,
    )
    return CuratedProtocolFixture(
        draft=draft,
        status="fictional_non_operational",
        ordered_step_labels=tuple(str(index) for index in range(1, len(steps) + 1)),
        fixture_sha256=hashlib.sha256(source).hexdigest(),
        revision_id=f"{protocol_id}-v1",
        development_only=True,
        source_filename=extraction.original_filename,
    )


def miniprep_fixture() -> CuratedProtocolFixture:
    return build_fixture(
        protocol_id="fictional-miniprep",
        title="Fictional plasmid miniprep",
        steps=MINIPREP_STEPS,
        materials=MINIPREP_MATERIALS,
        equipment=MINIPREP_EQUIPMENT,
    )
