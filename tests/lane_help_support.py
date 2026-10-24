"""Lane HELP test support: a fictional wash protocol with notes, results and a repeat.

Built in memory from exactly the text below (no licensed PDF), so the help's
sources can be named line by line: a repeat-until condition at step 3, a note
and an expected result, a warning, a troubleshooting line the analysis kept
nowhere, and reviewed Korean readings of the facts. Nothing here is live.
"""

from __future__ import annotations

import hashlib

from voiney_lab import experiment_protocol as domain
from voiney_lab.curated_protocol import CuratedProtocolFixture, CuratedProtocolSession
from voiney_lab.experiment_protocol_analysis import ProtocolAnalysisDraft
from voiney_lab.experiment_protocol_pdf import (
    ProtocolPdfExtraction,
    ProtocolPdfMetadata,
    ProtocolPdfPage,
)

PROTOCOL_ID = "fictional-gel-wash"
TITLE = "Fictional gel wash protocol"
STEP_1 = "1 Place the stained gel piece in a 1.5 mL tube."
STEP_2 = "2 Wash the gel piece with 500 µL wash buffer for 15 min at 37°C."
STEP_3 = "3 Repeat steps 2-3 until the gel piece is fully destained."
STEP_4 = "4 Dry the gel piece in the vacuum dryer until it looks white."
NOTE_2 = "Note If the gel piece is strongly stained the wash buffer volume can be raised to 1000 µL."
RESULT_3 = "Expected result The gel piece should be clear after two washes."
WARNING_1 = "Wear gloves and keep the gel covered to avoid keratin contamination."
TROUBLE = "If the gel piece stays blue after four washes, the stain was probably fixed too long."
PAGE_2 = "Notes\n" + TROUBLE + "\nKeep the vacuum dryer lid closed during drying."

READINGS = {
    "step-1/current_step": "1단계: 염색된 젤 조각을 1.5 mL 튜브에 넣습니다.",
    "step-1/warning_1": "장갑을 끼고 젤을 덮어 케라틴 오염을 피합니다.",
    "step-2/current_step": "2단계: 젤 조각을 세척 버퍼 500 µL로 37°C에서 15 min 세척합니다.",
    "step-2/note_1": "젤 조각의 염색이 진하면 세척 버퍼 부피를 1000 µL로 올릴 수 있습니다.",
    "step-3/current_step": "3단계: 젤 조각이 완전히 탈색될 때까지 2–3단계를 반복합니다.",
    "step-3/expected_result_1": "예상 결과: 두 번 세척한 뒤 젤 조각이 투명해야 합니다.",
    "step-4/current_step": "4단계: 젤 조각이 흰색으로 보일 때까지 진공 건조기에서 말립니다.",
}


def gel_wash_fixture(protocol_id: str = PROTOCOL_ID) -> CuratedProtocolFixture:
    """The fixture; ``protocol_id`` lets the voice harness (which starts the
    in-gel fixture's id) run it in place of in-gel."""

    page_1 = "\n".join((TITLE, STEP_1, WARNING_1, STEP_2, NOTE_2, STEP_3, RESULT_3, STEP_4))
    source = f"{page_1}\n{PAGE_2}".encode("utf-8")
    extraction = ProtocolPdfExtraction(
        original_filename=f"{protocol_id}.pdf",
        byte_size=len(source),
        sha256=hashlib.sha256(source).hexdigest(),
        media_type="application/pdf",
        page_count=2,
        encrypted=False,
        metadata=ProtocolPdfMetadata(
            title=TITLE, author="Voice Workflow Agent tests", subject="Fictional help fixture",
            creator="tests", producer="tests", creation_date=None, modification_date=None,
        ),
        pages=(ProtocolPdfPage(1, page_1, False), ProtocolPdfPage(2, PAGE_2, False)),
    )

    def evidence(text: str, page: int = 1) -> domain.SourceEvidence:
        return domain.SourceEvidence(page, text)

    def statement(identifier: str, text: str) -> domain.SourceStatement:
        return domain.SourceStatement(identifier, text, evidence(text))

    steps = (
        domain.ProtocolSourceStep("step-1", "1", STEP_1, evidence(STEP_1),
                                  warnings=(statement("w1", WARNING_1),)),
        domain.ProtocolSourceStep("step-2", "2", STEP_2, evidence(STEP_2),
                                  notes=(statement("n2", NOTE_2),)),
        domain.ProtocolSourceStep("step-3", "3", STEP_3, evidence(STEP_3),
                                  expected_results=(statement("r3", RESULT_3),)),
        domain.ProtocolSourceStep("step-4", "4", STEP_4, evidence(STEP_4)),
    )
    protocol = domain.ExperimentProtocol(
        protocol_id=protocol_id,
        metadata=domain.ProtocolMetadata(
            pdf=extraction, title=TITLE, original_language="en", version="1.0-test",
            source_status="fictional_non_operational", evidence=evidence(TITLE),
        ),
        sections=(domain.ProtocolSection("wash", TITLE, evidence(TITLE), steps=steps),),
        constructs=(
            domain.RepeatUntil(
                "repeat-3", STEP_3, ("step-2", "step-3"), evidence(STEP_3),
                section_id="wash", step_id="step-3",
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
        verified_evidence_count=8,
    )
    return CuratedProtocolFixture(
        draft=draft,
        status="fictional_non_operational",
        ordered_step_labels=("1", "2", "3", "4"),
        fixture_sha256=hashlib.sha256(source).hexdigest(),
        revision_id=f"{protocol_id}-v1",
        development_only=True,
        source_filename=extraction.original_filename,
        localizations=dict(READINGS),
    )


def session_at(label: str, fixture: CuratedProtocolFixture | None = None) -> CuratedProtocolSession:
    """A started session at the step labelled ``label``, no question open."""

    fixture = fixture or gel_wash_fixture()
    session = CuratedProtocolSession(fixture)
    session.activate_configured()
    session.plan("시작", turn_id=1, language="ko", configuration_id=1, generation=1)
    session.current_index = [step.source_label for step in fixture.steps].index(label)
    session._notebook_question = None
    return session
