#!/usr/bin/env python3
"""Replay transcripts through the same curated router used by Cascade WebSocket."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from voice_workflow_agent.curated_protocol import (
    CuratedProtocolSession,
    load_curated_protocol_fixture,
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay text through production curated-protocol arbitration."
    )
    root = Path(__file__).resolve().parents[1]
    parser.add_argument(
        "--fixture",
        type=Path,
        default=root / "data/development_protocols/candidate_a_curated_analysis.json",
    )
    parser.add_argument(
        "--provenance",
        type=Path,
        default=root / "data/development_protocols/candidate_a_curated_analysis.provenance.json",
    )
    parser.add_argument(
        "--pdf",
        type=Path,
        default=Path("/home/student/protocol-test-files/in-gel-digestion.pdf"),
    )
    parser.add_argument(
        "turn",
        nargs="*",
        help="Transcript(s) to replay. The required acceptance set is used when omitted.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    fixture = load_curated_protocol_fixture(
        args.fixture.resolve(),
        args.provenance.resolve(),
        args.pdf.resolve(),
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
    json.dump(output, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
