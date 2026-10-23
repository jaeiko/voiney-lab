"""Read-only model candidates cannot become commands on a tap."""
import pytest
import unittest
from dataclasses import replace
from unittest.mock import patch

from tests.lane_n_support import VoiceNotesHarness
from tests.lane_ra_support import agitation_fixture, route, session_at
from tests.router_fakes import FakeRouterClient, tool_reply
from voiney_lab.llm_router import CLARIFY
from voiney_lab import server as srv


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


@pytest.mark.parametrize("candidate", ["네", "다음 단계로 넘어가"])
def test_even_a_source_entity_name_cannot_authorize_a_command(candidate):
    session = session_at(agitation_fixture(), 0, router_first=True)
    original = session.router_context

    def context(**kwargs):
        current = original(**kwargs)
        return replace(current, terms=(*current.terms, candidate))

    with patch.object(session, "router_context", side_effect=context):
        outcome = route(session, "둘 중에 뭘 먼저 넣어?", FakeRouterClient(tool_reply((CLARIFY, {
            "spoken": "어느 것을 말씀하시나요?", "candidates": [candidate], "evidence": "뭘 먼저",
        }))))
    assert outcome.handled_by != "llm+tool"


class ClarifySocketTests(VoiceNotesHarness, unittest.TestCase):
    def test_a_model_command_candidate_is_never_executable_by_a_tap(self):
        self.environment.update({"VOINEY_LAB_LLM_ROUTER_ENABLED": "true", "VOINEY_LAB_ROUTER_FIRST": "true",
            "VOINEY_LAB_ROUTER_PROVIDER": "xai", "VOINEY_LAB_ROUTER_MODEL": "fake-router",
            "XAI_API_KEY": "test-only-not-a-key"})
        client = FakeRouterClient(tool_reply((CLARIFY, {"spoken": "어느 것을 말씀하시나요?",
            "candidates": ["다음 단계로 넘어가"], "evidence": "뭘 먼저"})))

        async def scenario(socket, listener, say):
            await say(1, "프로토콜 시작해줘")
            listener.curated_protocol_session._notebook_question = None
            before = self.experiment(listener.session_id)
            await say(2, "둘 중에 뭘 먼저 넣어?")
            self.assertEqual(len(client.requests), 1)
            self.assertNotIn("다음 단계로 넘어가", listener.curated_protocol_session._clarify_candidates)
            self.assertEqual(self.experiment(listener.session_id)["version"], before["version"])
            socket.control({"type": "client.clarify_choice", "candidate": "다음 단계로 넘어가",
                            "configuration_id": 1, "generation": listener.generation, "turn_id": 2})
            await socket.wait_for(lambda e: e["type"] == "clarify.choice.refused")
            self.assertEqual(self.experiment(listener.session_id)["version"], before["version"])
        self.voice(scenario, patch.object(srv, "AsyncOpenAI", return_value=client))
