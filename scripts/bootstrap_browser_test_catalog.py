"""Materialize and tenant-bind a browser-test curated protocol fixture.

Used by both the browser-test and documented Candidate A launchers. It creates
immutable source and analysis records and
binds the protocol to the development tenant so the reviewer queue can see it.
It never appends an approval event: making the protocol executable is exactly
what the browser journey test has to drive through the product.

When the licensed Candidate A source is unavailable, browser CI builds an
explicitly fictional, non-operational PDF and matching typed analysis in its
throwaway directory. No synthetic source is used by the documented Candidate A
launcher, which always supplies the configured fixture paths.
"""

from __future__ import annotations

import os
import hashlib
from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from voice_workflow_agent import experiment_protocol as domain
from voice_workflow_agent.curated_protocol import (
    CuratedProtocolFixture,
    load_curated_protocol_fixture,
)
from voice_workflow_agent.experiment_protocol_analysis import ProtocolAnalysisDraft
from voice_workflow_agent.experiment_protocol_config import (
    ProtocolPersistenceSettings,
)
from voice_workflow_agent.experiment_protocol_pdf import extract_protocol_pdf
from voice_workflow_agent.experiment_protocol_store import (
    initialize_protocol_store,
)
from voice_workflow_agent.identity import DevIdentityProvider
from voice_workflow_agent.protocol_catalog import ProtocolCatalog
from voice_workflow_agent.workspace_store import (
    WorkspaceSettings,
    initialize_workspace_store,
)


def _write_fictional_pdf(path: Path, lines: tuple[str, ...]) -> None:
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    content = DecodedStreamObject()
    commands = ["BT /F1 10 Tf"]
    for index, line in enumerate(lines):
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        commands.append(f"1 0 0 1 54 {740-index*22} Tm ({escaped}) Tj")
    commands.append("ET")
    content.set_data("\n".join(commands).encode("ascii"))
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({
            NameObject("/F1"): writer._add_object(font)
        })
    })
    page[NameObject("/Contents")] = writer._add_object(content)
    writer.add_metadata({"/Title": lines[0]})
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as stream:
        writer.write(stream)


def _fictional_browser_fixture(root: Path) -> CuratedProtocolFixture:
    """Build a source-grounded, non-operational protocol for CI browsers."""

    title = "FICTIONAL NON-OPERATIONAL browser checkpoint protocol"
    section_title = "FICTIONAL browser steps"
    instructions = (
        "1. Mark the fictional test card as started.",
        "2. Move the fictional marker to slot two.",
        "3. Move the fictional marker to slot three.",
        "4. Move the fictional marker to slot four.",
        "5. Move the fictional marker to slot five.",
        "6. Move the fictional marker to slot six.",
        "7. Check whether the fictional marker is clear.",
        "8. Hold the fictional marker for review.",
    )
    repeat_text = (
        "Repeat steps 2-7 until the fictional marker is reported clear."
    )
    ambiguity_text = "Repeat steps 68."
    lines = (title, section_title, *instructions, repeat_text, ambiguity_text)
    source = root / "fictional-browser-checkpoint.pdf"
    _write_fictional_pdf(source, lines)
    extraction = extract_protocol_pdf(source)
    page_text = extraction.pages[0].text
    for line in lines:
        if line not in page_text:
            raise RuntimeError("The fictional browser PDF lost source evidence.")
    evidence = lambda text: domain.SourceEvidence(1, text)
    steps = tuple(
        domain.ProtocolSourceStep(
            f"fictional-browser-step-{index}", str(index), instruction,
            evidence(instruction),
        )
        for index, instruction in enumerate(instructions, start=1)
    )
    protocol = domain.ExperimentProtocol(
        "fictional-browser-checkpoint-v1",
        domain.ProtocolMetadata(
            extraction, title, "en",
            license="Test fixture only",
            source_status="FICTIONAL NON-OPERATIONAL",
            evidence=evidence(title),
        ),
        sections=(domain.ProtocolSection(
            "fictional-browser-section", section_title, evidence(section_title),
            steps,
        ),),
        constructs=(
            domain.RepeatUntil(
                "fictional-browser-repeat-2-7", repeat_text,
                tuple(step.step_id for step in steps[1:7]),
                evidence(repeat_text),
                section_id="fictional-browser-section",
                step_id=steps[6].step_id,
            ),
            domain.SourceAmbiguity(
                "fictional-browser-ambiguity-6-8", ambiguity_text,
                evidence(ambiguity_text),
                section_id="fictional-browser-section",
                step_id=steps[7].step_id,
            ),
        ),
    )
    domain.validate_protocol(protocol)
    readiness = domain.assess_readiness(protocol)
    if readiness.reason_codes != (
        domain.ReadinessReasonCode.UNRESOLVED_AMBIGUITY.value,
    ):
        raise RuntimeError("The fictional browser fixture readiness is invalid.")
    draft = ProtocolAnalysisDraft(
        extraction, protocol, readiness, domain.P1_CAPABILITY_POLICY, 1,
        len(lines),
    )
    identity = hashlib.sha256(source.read_bytes()).hexdigest()
    return CuratedProtocolFixture(
        draft=draft,
        status="development_only_not_final_acceptance",
        ordered_step_labels=tuple(step.source_label for step in steps),
        fixture_sha256=identity,
        development_only=True,
        source_pdf_path=source,
        source_pdf_sha256=extraction.sha256,
        source_filename=source.name,
    )


def main() -> None:
    fixture_path = os.environ.get(
        "VOICE_WORKFLOW_AGENT_CURATED_PROTOCOL_FIXTURE", ""
    ).strip()
    if fixture_path:
        fixture = load_curated_protocol_fixture(
            Path(fixture_path),
            Path(os.environ["VOICE_WORKFLOW_AGENT_CURATED_PROTOCOL_PROVENANCE"]),
            Path(os.environ["VOICE_WORKFLOW_AGENT_CURATED_PROTOCOL_SOURCE_PDF"]),
        )
    else:
        settings = ProtocolPersistenceSettings.from_environment()
        assert settings.data_dir is not None
        fixture = _fictional_browser_fixture(settings.data_dir / "fixture")
    store = initialize_protocol_store(ProtocolPersistenceSettings.from_environment())
    try:
        ProtocolCatalog(store).bootstrap_development_fixture(fixture)
    finally:
        store.close()

    workspace_settings = WorkspaceSettings.from_environment()
    if workspace_settings.enabled:
        principal = DevIdentityProvider.from_environment().authenticate()
        workspace = initialize_workspace_store(workspace_settings)
        try:
            workspace.bootstrap_principal(principal)
            workspace.bind_resource(
                principal, "protocol_catalog", fixture.protocol_id
            )
        finally:
            workspace.close()
    print(f"[OK] curated fixture materialized: {fixture.protocol_id}")


if __name__ == "__main__":
    main()
