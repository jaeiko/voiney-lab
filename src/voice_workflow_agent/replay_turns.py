"""Package-native replay through the production curated-runtime boundary."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from voice_workflow_agent import experiment_protocol as domain
from voice_workflow_agent.curated_protocol import (
    CuratedProtocolFixture,
    CuratedProtocolSession,
    load_curated_protocol_fixture,
)
from voice_workflow_agent.experiment_protocol_analysis import ProtocolAnalysisDraft
from voice_workflow_agent.experiment_protocol_pdf import (
    ProtocolPdfExtraction,
    ProtocolPdfMetadata,
    ProtocolPdfPage,
)
from voice_workflow_agent.runtime_routing import route_curated_runtime_turn


DEFAULT_TURNS = (
    "왜 해야 돼?",
    "현재 프로토콜 버전 알려줘.",
    "어제 실험 이어줘.",
    "이 실험 결과가 성공할까?",
    "이 단계 왜 하는지 알려주고 다음 단계도 알려줘.",
    "주의사항 알려줘.",
    "이 장비 사진을 찾아줘.",
)


def _demo_fixture() -> CuratedProtocolFixture:
    """Build a fictional, source-linked fixture without a private PDF dependency."""

    page_text = (
        "Fictional bench replay protocol.\n"
        "1. Add 1 mL fictional buffer to the sample tube. This prepares the "
        "sample for mixing. Use a clean tube and wear gloves.\n"
        "2. Mix the sample for 30 seconds."
    )
    overview_text = (
        "Abstract\nThis fictional protocol exercises safe bench-side routing.\n"
        "Protocol materials\nfictional buffer and sample tube\n"
        "Safety warnings\nUse a clean tube and wear gloves.\n"
        "Before start\nConfirm this is a non-operational replay."
    )
    source_text = page_text + "\n" + overview_text
    checksum = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    extraction = ProtocolPdfExtraction(
        original_filename="fictional-replay-protocol.pdf",
        byte_size=len(source_text.encode("utf-8")),
        sha256=checksum,
        media_type="application/pdf",
        page_count=2,
        encrypted=False,
        metadata=ProtocolPdfMetadata(
            title="Fictional bench replay protocol",
            author="Voice Workflow Agent",
            subject="Non-operational replay fixture",
            creator="voice-workflow-replay",
            producer="voice-workflow-replay",
            creation_date=None,
            modification_date=None,
        ),
        pages=(
            ProtocolPdfPage(1, page_text, False),
            ProtocolPdfPage(2, overview_text, False),
        ),
    )
    evidence = lambda text: domain.SourceEvidence(1, text)  # noqa: E731
    first_instruction = "1. Add 1 mL fictional buffer to the sample tube."
    second_instruction = "2. Mix the sample for 30 seconds."
    rationale = "This prepares the sample for mixing."
    warning = "Use a clean tube and wear gloves."
    protocol = domain.ExperimentProtocol(
        protocol_id="fictional-replay-protocol",
        metadata=domain.ProtocolMetadata(
            pdf=extraction,
            title="Fictional bench replay protocol",
            original_language="en",
            version="1.0-demo",
            source_status="fictional_non_operational",
            evidence=evidence("Fictional bench replay protocol."),
        ),
        materials=(
            domain.Material(
                "fictional-buffer",
                "fictional buffer",
                evidence("fictional buffer"),
                quantities=(domain.ScientificValue("1 mL", "1", "mL"),),
            ),
        ),
        equipment=(
            domain.Equipment("sample-tube", "sample tube", evidence("sample tube")),
        ),
        sections=(
            domain.ProtocolSection(
                "bench-steps",
                "Fictional bench replay protocol.",
                evidence("Fictional bench replay protocol."),
                steps=(
                    domain.ProtocolSourceStep(
                        "step-1",
                        "1",
                        first_instruction,
                        evidence(first_instruction),
                        notes=(domain.SourceStatement("rationale-1", rationale, evidence(rationale)),),
                        warnings=(domain.SourceStatement("warning-1", warning, evidence(warning)),),
                    ),
                    domain.ProtocolSourceStep(
                        "step-2",
                        "2",
                        second_instruction,
                        evidence(second_instruction),
                    ),
                ),
            ),
        ),
    )
    domain.validate_protocol(protocol)
    readiness = domain.assess_readiness(protocol)
    draft = ProtocolAnalysisDraft(
        extraction=extraction,
        protocol=protocol,
        readiness=readiness,
        capability_policy=domain.P1_CAPABILITY_POLICY,
        analysis_schema_version=1,
        verified_evidence_count=10,
    )
    return CuratedProtocolFixture(
        draft=draft,
        status="fictional_non_operational",
        ordered_step_labels=("1", "2"),
        fixture_sha256=hashlib.sha256(b"fictional-replay-fixture-v1").hexdigest(),
        revision_id="replay-demo-v1",
        development_only=True,
        source_filename=extraction.original_filename,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay text through production curated-protocol arbitration."
    )
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--provenance", type=Path)
    parser.add_argument("--pdf", type=Path)
    parser.add_argument(
        "turn",
        nargs="*",
        help="Transcript(s) to replay. The required A-G set is used when omitted.",
    )
    args = parser.parse_args(argv)
    supplied = (args.fixture, args.provenance, args.pdf)
    if any(supplied) and not all(supplied):
        parser.error("--fixture, --provenance, and --pdf must be supplied together")
    return args


def replay(args: argparse.Namespace) -> list[dict[str, object]]:
    fixture = (
        load_curated_protocol_fixture(
            args.fixture.resolve(), args.provenance.resolve(), args.pdf.resolve()
        )
        if args.fixture is not None
        else _demo_fixture()
    )
    workflow = CuratedProtocolSession(fixture)
    workflow.active = True
    workflow.current_index = 0
    turns = tuple(args.turn) or DEFAULT_TURNS
    output: list[dict[str, object]] = []
    for turn_id, transcript in enumerate(turns, 1):
        before = workflow.state()
        routed = route_curated_runtime_turn(
            workflow,
            transcript,
            turn_id=turn_id,
            language="ko",
            configuration_id=1,
            generation=0,
        )
        after = workflow.state()
        output.append(
            {
                "turn_id": turn_id,
                "transcript": transcript,
                "normalized_text": routed.arbitration.normalized_text,
                "intent": routed.arbitration.intent.value,
                "confidence": routed.arbitration.confidence,
                "runtime_router": routed.runtime_router,
                "action": routed.plan.action.value,
                "intent_kind": routed.plan.intent_kind,
                "answer_origin": routed.answer_origin,
                "state_mutation": routed.state_mutation,
                "step_before": before.get("current_step_label"),
                "step_after": after.get("current_step_label"),
                "speech_text": routed.plan.speech_text,
                "tools_used": [],
                "visual_intent": routed.plan.visual_intent,
            }
        )
    return output


def main(argv: list[str] | None = None) -> int:
    json.dump(replay(parse_args(argv)), sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
