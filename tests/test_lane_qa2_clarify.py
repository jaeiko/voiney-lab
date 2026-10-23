"""Read-only model candidates cannot become commands on a tap."""
import pytest

from tests.lane_ra_support import agitation_fixture, route, session_at
from tests.router_fakes import FakeRouterClient, tool_reply
from voiney_lab.llm_router import CLARIFY


@pytest.mark.parametrize("candidate", ["다음 단계로 넘어가", "잠깐 멈춰", "메모해 줘, 색이 맑아", "900 rpm", "네"])
def test_model_clarify_does_not_offer_a_mutation_or_invented_value(candidate):
    session = session_at(agitation_fixture(), 0, router_first=True)
    before = session.current_index
    outcome = route(session, "둘 중에 뭘 먼저 넣어?", FakeRouterClient(tool_reply((CLARIFY, {
        "spoken": "어느 것을 말씀하시나요?", "candidates": ["solution A", candidate], "evidence": "뭘 먼저",
    }))))
    assert outcome.handled_by != "llm+tool"
    assert candidate not in session._clarify_candidates
    assert session.current_index == before
