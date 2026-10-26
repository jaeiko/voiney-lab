"""Lane KNOW2 test support: a fictional coin cell assembly protocol (materials, batteries).

Rule 10 of the lane rules (person's decision 2026-10-26): a changed behaviour
is checked on protocols of at least three fields, one of them chemistry or
materials/batteries. No materials or battery protocol in the catalogs at
hand has a passed analysis (the starter pack's perovskite protocol and lane
MISS's carbon-dot and nanosheet protocols are not executable), so this
protocol -- written for the tests, no one's lab document, not operational --
stands in for one. Built in memory from exactly the text below, like lane
HELP's wash protocol (``tests/lane_help_support.py``).
"""

from __future__ import annotations

import hashlib

from voiney_lab import experiment_protocol as domain
from voiney_lab.curated_protocol import CuratedProtocolFixture
from voiney_lab.experiment_protocol_analysis import ProtocolAnalysisDraft
from voiney_lab.experiment_protocol_pdf import ProtocolPdfExtraction, ProtocolPdfMetadata, ProtocolPdfPage

PROTOCOL_ID = "fictional-coin-cell"
TITLE = "Fictional coin cell assembly (CR2032)"
STEPS = (
    "1 Mix 80 wt% LiFePO4, 10 wt% carbon black and 10 wt% PVDF in NMP and stir the slurry for 12 h.",
    "2 Cast the slurry on aluminium foil with a doctor blade at 150 µm and dry it at 80 °C in a vacuum oven for 12 h.",
    "3 Punch 12 mm electrodes and move them into the argon glovebox through the antechamber.",
    "4 Wet the separator with 40 µL of 1 M LiPF6 in EC/DMC electrolyte.",
    "5 Stack the lithium metal chip, separator and cathode in the CR2032 case and crimp the cell.",
    "6 Rest the cell for 6 h and run two formation cycles at C/10 on the battery tester.",
)
WARNINGS = {
    "step-1": "NMP is a reproductive toxicant; handle it in a fume hood.",
    "step-4": "LiPF6 reacts with moisture and releases HF; keep it inside the glovebox.",
}


def coin_cell_fixture(protocol_id: str = PROTOCOL_ID) -> CuratedProtocolFixture:
    page = "\n".join((TITLE, *[f"{step}\n{WARNINGS.get(f'step-{n}', '')}".strip()
                              for n, step in enumerate(STEPS, 1)]))
    source = page.encode("utf-8")
    extraction = ProtocolPdfExtraction(
        original_filename=f"{protocol_id}.pdf", byte_size=len(source), sha256=hashlib.sha256(source).hexdigest(),
        media_type="application/pdf", page_count=1, encrypted=False,
        metadata=ProtocolPdfMetadata(
            title=TITLE, author="Voice Workflow Agent tests", subject="Fictional materials fixture",
            creator="tests", producer="tests", creation_date=None, modification_date=None,
        ),
        pages=(ProtocolPdfPage(1, page, False),),
    )

    def evidence(text: str) -> domain.SourceEvidence:
        return domain.SourceEvidence(1, text)

    steps = tuple(
        domain.ProtocolSourceStep(
            f"step-{n}", str(n), text, evidence(text),
            warnings=((domain.SourceStatement(f"w{n}", WARNINGS[f"step-{n}"], evidence(WARNINGS[f"step-{n}"])),)
                      if f"step-{n}" in WARNINGS else ()),
        )
        for n, text in enumerate(STEPS, 1)
    )
    protocol = domain.ExperimentProtocol(
        protocol_id=protocol_id,
        metadata=domain.ProtocolMetadata(
            pdf=extraction, title=TITLE, original_language="en", version="1.0-test",
            source_status="fictional_non_operational", evidence=evidence(TITLE),
        ),
        sections=(domain.ProtocolSection("assembly", TITLE, evidence(TITLE), steps=steps),),
    )
    domain.validate_protocol(protocol)
    draft = ProtocolAnalysisDraft(
        extraction=extraction, protocol=protocol, readiness=domain.assess_readiness(protocol),
        capability_policy=domain.P1_CAPABILITY_POLICY, analysis_schema_version=1,
        verified_evidence_count=len(STEPS),
    )
    return CuratedProtocolFixture(
        draft=draft, status="fictional_non_operational",
        ordered_step_labels=tuple(str(n) for n in range(1, len(STEPS) + 1)),
        fixture_sha256=hashlib.sha256(source).hexdigest(), revision_id=f"{protocol_id}-v1",
        development_only=True, source_filename=extraction.original_filename, localizations={},
    )
