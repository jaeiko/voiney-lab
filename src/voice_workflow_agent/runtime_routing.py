"""Production turn-routing boundary shared by WebSocket and transcript replay."""

from __future__ import annotations

from dataclasses import dataclass

from voice_workflow_agent.curated_protocol import (
    CuratedProtocolSession,
    CuratedProtocolTurnPlan,
)
from voice_workflow_agent.intent_arbitration import (
    RequestArbitration,
    arbitrate_request,
)


@dataclass(frozen=True)
class CuratedRuntimeRoute:
    """Observable result of routing one transcript through production policy."""

    arbitration: RequestArbitration
    runtime_router: str
    plan: CuratedProtocolTurnPlan

    @property
    def state_mutation(self) -> bool:
        return bool(self.plan.state_changed)

    @property
    def answer_origin(self) -> str:
        return self.plan.answer_origin


def route_curated_runtime_turn(
    session: CuratedProtocolSession,
    transcript: str,
    *,
    turn_id: int,
    language: str,
    transcript_quality: str | None = None,
    configuration_id: int | None = None,
    generation: int | None = None,
    arbitration: RequestArbitration | None = None,
) -> CuratedRuntimeRoute:
    """Use the exact arbitration/planning boundary called by Cascade runtime."""

    arbitration = arbitration or arbitrate_request(transcript)
    plan = session.plan(
        transcript,
        turn_id=turn_id,
        language=language,
        transcript_quality=transcript_quality,
        configuration_id=configuration_id,
        generation=generation,
        arbitration=arbitration,
    )
    return CuratedRuntimeRoute(
        arbitration=arbitration,
        runtime_router="curated_protocol",
        plan=plan,
    )
