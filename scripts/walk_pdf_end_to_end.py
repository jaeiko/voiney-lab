"""Push one PDF through every stage, in an isolated store, with no calls.

This is a plumbing check, not a quality measurement. The claim model is the
deterministic offline fixture, so what the stages receive is a fixed synthetic
analysis; nothing here says anything about what a real provider would produce.
What it does establish is whether the stages are connected at all, and the
in-gel source is chosen because it is the smallest, its extraction already
passes, and a hand-built fixture over the same document exists to compare
against.

The operational store under data/runtime is never opened. Everything runs in a
temporary directory that is discarded, and the source PDF is only read.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import replace
from pathlib import Path

from voice_workflow_agent import experiment_protocol as domain
from voice_workflow_agent.experiment_protocol_pdf import extract_protocol_pdf
from voice_workflow_agent.experiment_protocol_store import (
    ProtocolPersistenceSettings,
    initialize_protocol_store,
)
from voice_workflow_agent.protocol_catalog import (
    AMBIGUITY_SINGLE_AUTHORITATIVE,
    ProtocolCatalog,
)
from voice_workflow_agent.protocol_chunk_analysis import (
    ChunkAnalysisLimits,
    ValidatedChunkResult,
    analyze_protocol_chunk,
    assemble_validated_protocol_claims,
    merge_validated_chunk_results,
    plan_protocol_chunks,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "data/runtime/candidate-a-source/in-gel-digestion.pdf"
_GATE = domain.ReadinessReasonCode.NO_DECLARED_SAFETY_WARNINGS.value


def _reason(error: BaseException) -> dict[str, object]:
    diagnostic = getattr(error, "diagnostic", None)
    return {
        "error": type(error).__name__,
        "message": str(error)[:200],
        "reason_code": getattr(diagnostic, "reason_code", None)
        or getattr(error, "reason_code", None),
        "validation_stage": getattr(diagnostic, "validation_stage", None),
        "page_number": getattr(diagnostic, "page_number", None),
    }


def _probe_9_and_10(draft, stages, record) -> None:
    """Stages 9 and 10 with the readiness wall stepped around, on purpose.

    This is a diagnostic, not a route: it does not change a rule and it does
    not make anything executable.  Stage 8 refuses on reasons no
    acknowledgement can clear, so without this the stages after it stay
    untested and a plumbing fault would be indistinguishable from the policy
    wall.  The fixture is built here exactly as ``replay_turns`` builds its
    own, which is the same constructor ``load_executable_fixture`` uses.
    """

    import hashlib

    from voice_workflow_agent.curated_protocol import (
        CuratedProtocolFixture,
        CuratedProtocolSession,
    )

    labels = tuple(
        step.source_label
        for section in draft.protocol.sections
        for step in section.steps
    )
    try:
        fixture = CuratedProtocolFixture(
            draft=draft,
            status="fictional_non_operational",
            ordered_step_labels=labels,
            fixture_sha256=hashlib.sha256(
                b"walkthrough-diagnostic-not-a-route"
            ).hexdigest(),
            revision_id="walkthrough-diagnostic",
            development_only=True,
            source_filename=draft.extraction.original_filename,
        )
    except Exception as error:  # noqa: BLE001
        record(9, "[diagnostic] build fixture from assembled protocol", False,
               **_reason(error))
        return
    record(
        9,
        "[diagnostic] build fixture from assembled protocol",
        True,
        ordered_step_labels=len(fixture.ordered_step_labels),
    )
    try:
        session = CuratedProtocolSession(fixture)
        session.active = True
        session.current_index = 0
        frame = session.current_step_semantic_frame()
    except Exception as error:  # noqa: BLE001
        record(10, "[diagnostic] first step guidance", False, **_reason(error))
        return
    record(
        10,
        "[diagnostic] first step guidance",
        True,
        step_id=frame.step_id,
        step_label=frame.step_label,
        parameters=len(frame.parameters),
        actions=len(frame.actions),
    )


def main() -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser()
    parser.add_argument("source", nargs="?", type=Path, default=DEFAULT_SOURCE)
    arguments = parser.parse_args()
    SOURCE = arguments.source
    if not SOURCE.is_file():
        raise SystemExit(f"source is not a file: {SOURCE}")

    sys.path.insert(0, str(ROOT / "scripts"))
    from prototype_claim_chunks import (
        ExactNumberedStepClaimModel,
        fixture_scope,
    )

    stages: list[dict[str, object]] = []

    def record(number: int, name: str, ok: bool, **detail) -> None:
        stages.append({"stage": number, "name": name, "ok": ok, **detail})
        flag = "ok  " if ok else "STOP"
        print(f"  {flag} {number:2}. {name}", flush=True)
        for key, value in detail.items():
            print(f"          {key}: {value}", flush=True)

    print(f"source: {SOURCE.name}")
    with tempfile.TemporaryDirectory() as temporary:
        workspace = Path(temporary)

        # 1) extraction
        try:
            extraction = extract_protocol_pdf(SOURCE)
        except Exception as error:  # noqa: BLE001
            record(1, "extraction", False, **_reason(error))
            print(json.dumps(stages, indent=2))
            return 1
        record(
            1,
            "extraction",
            True,
            pages=extraction.page_count,
            verification=extraction.text_verification.value,
            resolved_glyphs=len(extraction.glyph_resolutions),
        )
        scope = fixture_scope(extraction)
        record(
            0,
            "fixture scope (is the offline model entitled to run here)",
            bool(scope["in_scope"]),
            labels=scope["fixture_action_labels"],
            duplicates=scope["duplicate_labels"],
            descents=len(scope["descents"]),
        )

        protocol_id = f"protocol-{extraction.sha256[:32]}"

        # 2) page admission
        try:
            plan = plan_protocol_chunks(
                extraction,
                protocol_id,
                "pdf-1",
                limits=ChunkAnalysisLimits(max_concurrency=1, max_retries=0),
            )
        except Exception as error:  # noqa: BLE001
            record(2, "page admission", False, **_reason(error))
            print(json.dumps(stages, indent=2))
            return 1
        record(2, "page admission", True, chunks=len(plan.chunks))

        # 3) chunk analysis
        model = ExactNumberedStepClaimModel(extraction)
        analyses: list[ValidatedChunkResult] = []
        failures: list[dict[str, object]] = []
        for chunk in plan.chunks:
            try:
                analyses.append(
                    ValidatedChunkResult(
                        chunk, analyze_protocol_chunk(extraction, chunk, model)
                    )
                )
            except Exception as error:  # noqa: BLE001
                failures.append({"chunk": chunk.ordinal, **_reason(error)})
        record(
            3,
            "chunk analysis",
            not failures,
            validated=f"{len(analyses)}/{len(plan.chunks)}",
            failures=failures or None,
        )
        if failures:
            print(json.dumps(stages, indent=2))
            return 1

        # 4) whole-document merge
        try:
            merged = merge_validated_chunk_results(
                extraction, plan, tuple(analyses)
            )
        except Exception as error:  # noqa: BLE001
            record(4, "whole-document merge", False, **_reason(error))
            print(json.dumps(stages, indent=2))
            return 1
        record(
            4,
            "whole-document merge",
            True,
            claims=len(merged.claims),
            markers=len(merged.structure),
        )

        # 5) assembly
        try:
            draft = assemble_validated_protocol_claims(extraction, merged)
        except Exception as error:  # noqa: BLE001
            record(5, "ExperimentProtocol assembly", False, **_reason(error))
            print(json.dumps(stages, indent=2))
            return 1
        step_count = sum(
            len(section.steps) for section in draft.protocol.sections
        )
        record(
            5,
            "ExperimentProtocol assembly",
            True,
            sections=len(draft.protocol.sections),
            steps=step_count,
        )

        # 6) readiness
        readiness = domain.assess_readiness(draft.protocol)
        record(
            6,
            "readiness assessment",
            True,
            status=readiness.status.value,
            reason_codes=list(readiness.reason_codes),
        )

        # 7) audited human safety confirmation
        store = initialize_protocol_store(
            ProtocolPersistenceSettings(True, workspace / "catalog")
        )
        try:
            catalog = ProtocolCatalog(store)
            registration = catalog.register(
                SOURCE,
                source_filename=SOURCE.name,
                media_type="application/pdf",
            )
            registered_id = registration.entry.protocol_id
            stored_protocol = replace(draft.protocol, protocol_id=registered_id)
            stored_protocol = domain.validate_protocol(stored_protocol)
            analysis = store.append_analysis_revision(
                registered_id,
                1,
                "analysis-walkthrough",
                stored_protocol,
                domain.assess_readiness(stored_protocol),
                draft.capability_policy.profile_id,
            )
            revision_id = (
                f"pdf-1-analysis-{analysis.analysis_revision_number}"
            )
            try:
                catalog.acknowledge_readiness_gate(
                    registered_id,
                    revision_id,
                    reason_code=_GATE,
                    actor_principal_id="reviewer@example.org",
                    actor_role="reviewer",
                    comment="Plumbing walkthrough; warnings reviewed.",
                )
            except Exception as error:  # noqa: BLE001
                record(7, "audited safety confirmation", False, **_reason(error))
                print(json.dumps(stages, indent=2))
                return 1
            gate_events = [
                event
                for event in store.list_events(registered_id)
                if event.event_type == "protocol_readiness_gate_acknowledged"
            ]
            record(
                7,
                "audited safety confirmation",
                True,
                ledger_entries=len(gate_events),
                actor=gate_events[-1].payload["actor_principal_id"],
            )

            # 7b) reviewer findings on each ambiguity, through the audited
            # resolution path. No wall is stepped around here.
            ambiguities = [
                construct
                for construct in stored_protocol.constructs
                if isinstance(construct, domain.SourceAmbiguity)
            ]
            for ambiguity in ambiguities:
                catalog.resolve_ambiguity(
                    registered_id,
                    revision_id,
                    ambiguity_id=ambiguity.ambiguity_id,
                    decision=AMBIGUITY_SINGLE_AUTHORITATIVE,
                    evidence_segment_ids=(
                        ambiguity.evidence.evidence_segment_ids
                    ),
                    actor_principal_id="reviewer@example.org",
                    actor_role="reviewer",
                    comment=(
                        "Prose interval and timer literal state the same "
                        "interval."
                    ),
                )
            analysis_now = store.get_analysis_revision(registered_id, 1, 1)
            record(
                7,
                "reviewer findings on ambiguities",
                True,
                resolved=len(ambiguities),
                every_ambiguity_resolved=catalog._every_ambiguity_resolved(
                    registered_id, 1, analysis_now
                ),
                all_gates_cleared=catalog._readiness_gates_cleared(
                    registered_id, 1, analysis_now
                ),
                still_blocking=sorted(
                    set(analysis_now.readiness.reason_codes)
                ),
            )

            # 8) development activation
            try:
                activated = catalog.activate_development(registered_id)
            except Exception as error:  # noqa: BLE001
                record(8, "development activation", False, **_reason(error))
                _probe_9_and_10(draft, stages, record)
                print(json.dumps(stages, indent=2, default=str))
                return 1
            record(
                8,
                "development activation",
                True,
                available_for_execution=activated.available_for_execution,
                approval_status=activated.approval_status,
            )

            # 9) load an executable fixture
            try:
                fixture = catalog.load_executable_fixture(registered_id)
            except Exception as error:  # noqa: BLE001
                record(9, "load_executable_fixture", False, **_reason(error))
                print(json.dumps(stages, indent=2))
                return 1
            record(
                9,
                "load_executable_fixture",
                True,
                ordered_step_labels=len(fixture.ordered_step_labels),
                development_only=fixture.development_only,
            )

            # 10) open a session and ask for the first step
            from voice_workflow_agent.curated_protocol import (
                CuratedProtocolSession,
            )

            try:
                session = CuratedProtocolSession(fixture)
                session.active = True
                frame = session.current_step_semantic_frame()
            except Exception as error:  # noqa: BLE001
                record(10, "first step guidance", False, **_reason(error))
                print(json.dumps(stages, indent=2))
                return 1
            record(
                10,
                "first step guidance",
                True,
                step_id=frame.step_id,
                step_label=frame.step_label,
                parameters=len(frame.parameters),
                actions=len(frame.actions),
            )
        finally:
            store.close()

    print()
    print(json.dumps(stages, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
