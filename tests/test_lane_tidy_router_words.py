"""Lane TIDY, item 4 (fake-user test F7): the router's words about "next".

Since lane VX, decision 1, words that move on ("다음", "넘어가") are the front
rules' ``forward_step`` and move on with no question (``test_lane_vx_forward``);
a ``next`` the model proposes still opens the completion or endpoint question
(``test_llm_router``, ``test_next_opens_the_completion_question_and_only_yes_moves_on``).
The module's description and the tool description the model reads say both,
and no longer that every "next" asks.
"""

from __future__ import annotations

from voiney_lab import llm_router
from voiney_lab.curated_protocol import forward_step_request


def test_the_router_describes_the_front_rules_next_and_its_own() -> None:
    module = llm_router.__doc__ or ""
    assert "* ``next`` never moves on by itself" not in module
    assert "``forward_step``, lane VX decision 1" in module
    assert ("A ``next`` the model proposes for other words never moves on by itself (D2)"
            in " ".join(module.split()))
    tool = llm_router.CHANGE_STATE_TOOL["function"]["description"]
    assert "'next' never moves on by itself" not in tool
    assert "Plain words that move on ('다음', '넘어가') are the server's own" in tool
    assert "a 'next' you propose never moves on by itself" in tool
    # The words the descriptions name are the front rule's.
    assert forward_step_request("다음") and forward_step_request("넘어가")
