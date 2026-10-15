"""Lane RA, decisions (a) and (e): the router first, behind VOINEY_LAB_ROUTER_FIRST (off by default).

Decisions of the people running the pilot (2026-10-12/13), after the advice of
2026-10-02 and the human test of 2026-10-10: with the switch on, the front
rules keep only system information (the timer, the steps, the settings), the
re-asking of an ambiguous reference, the short clear commands and the replies
to the server's own questions; everything else is the router's, which chooses
answer, change_state, record_log, show_visual, clarify or not_for_me, and the
server rules on it. Two requests in one breath ("설명해 주고 그림 보여 줘") are
both done (a); words not addressed to the assistant are neither answered nor
recorded (e). Off, every turn is routed exactly as before. Every model here is
a fake (tests/router_fakes.py); nothing is live.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import patch

from tests.lane_ra_support import (
    AGITATION_STEPS,
    FIRST,
    ON,
    ROLE_ENV,
    FakeRoles,
    RouterFirstVoiceMixin,
    agitation_fixture,
    route,
    session_at,
    terminal,
)
from tests.protocol_vocabulary_support import SOURCE_PDF
from tests.router_fakes import FakeRouterClient, answer_call_reply, tool_reply
from voiney_lab.curated_protocol import (
    FRONT_RULES,
    NO_FIGURE_WORDS,
    ROUTER_FIRST_HANDED_ON,
    CuratedProtocolAction,
)
from voiney_lab.llm_router import (
    CLARIFY,
    NOT_FOR_ME,
    ROUTER_CALL_TOOLS,
    ROUTER_FIRST_CALL_TOOLS,
    SHOW_VISUAL,
    LlmRouterSettings,
    parse_read_only_call,
)


class SwitchTests(unittest.TestCase):
    """The switch: off by default, on only with the router."""

    def test_off_by_default_and_off_means_the_old_tools(self) -> None:
        settings = LlmRouterSettings.from_environment(ROLE_ENV)
        self.assertTrue(settings.enabled)
        self.assertFalse(settings.router_first)
        self.assertEqual(LlmRouterSettings().router_first, False)
        self.assertEqual([t["function"]["name"] for t in ROUTER_CALL_TOOLS],
                         ["answer", "change_state", "record_log"])
        self.assertEqual([t["function"]["name"] for t in ROUTER_FIRST_CALL_TOOLS],
                         ["answer", "change_state", "record_log", SHOW_VISUAL, CLARIFY, NOT_FOR_ME])
    def test_the_switch_reads_true_and_needs_the_router(self) -> None:
        on = LlmRouterSettings.from_environment({**ROLE_ENV, "VOINEY_LAB_ROUTER_FIRST": "true"})
        self.assertTrue(on.router_first)
        self.assertEqual(on.public_capability()["router_first"], True)
        with self.assertRaises(ValueError):
            LlmRouterSettings.from_environment({"VOINEY_LAB_ROUTER_FIRST": "true"})
        with self.assertRaises(ValueError):
            LlmRouterSettings.from_environment({**ROLE_ENV, "VOINEY_LAB_ROUTER_FIRST": "maybe"})


class FrontRuleTests(unittest.TestCase):
    """Decision (a): what the front rules keep with the router first."""

    def test_the_handed_on_rules_are_named_and_the_rest_stay(self) -> None:
        self.assertEqual(ROUTER_FIRST_HANDED_ON, frozenset({"note_record", "step_homophone", "visual_request"}))
        self.assertTrue(ROUTER_FIRST_HANDED_ON <= set(FRONT_RULES))
        for kept in ("pause", "end_command", "yes_no_open_question", "observation_reply",
                     "timer_remaining", "step_time", "coreference_clarify", "reference_value",
                     "repeat_last_reply", "targeted_completion", "forward_step", "start_command",
                     "server_value_query", "step_lookup", "quantity_target", "step_revert",
                     "experimenter_setting", "anomaly_report"):
            self.assertIn(kept, FRONT_RULES)
            self.assertNotIn(kept, ROUTER_FIRST_HANDED_ON)

    def test_a_note_with_words_is_handed_on_only_with_the_switch(self) -> None:
        said = "37.2도에서 790 rpm 으로 돌렸어, 특이사항으로 메모해 줘"
        plain = session_at(agitation_fixture(), 0)
        self.assertIsNotNone(plain.front_plan(said, turn_id=2, language="ko", configuration_id=1, generation=1))
        self.assertEqual(plain.last_front_rule, "note_record")
        first = session_at(agitation_fixture(), 0, router_first=True)
        self.assertIsNone(first.front_plan(
            said, turn_id=2, language="ko", configuration_id=1, generation=1, router_first=True,
        ))
        self.assertIsNone(first.last_front_rule)
        self.assertEqual(first.current_index, 0)
        # The rules' own path still records it: the fallback must not lose a note.
        plan = first.plan(said, turn_id=2, language="ko", configuration_id=1, generation=1)
        self.assertIs(plan.action, CuratedProtocolAction.RECORD_OBSERVATION)
        self.assertTrue(plan.reported_observation)

    def test_a_bare_note_request_and_a_picture_and_a_command(self) -> None:
        first = session_at(agitation_fixture(), 0, router_first=True)
        # "메모해 줘" with nothing to write stays the rules': it asks what.
        held = first.front_plan("메모해 줘", turn_id=2, language="ko", configuration_id=1,
                                generation=1, router_first=True)
        self.assertIsNotNone(held)
        self.assertEqual(first.last_front_rule, "note_record")
        for said, rule in (("잠깐", "pause"), ("1단계 완료했어", "targeted_completion"),
                           ("다음", "forward_step"), ("몇 분 남았어?", "timer_remaining")):
            with self.subTest(said):
                session = session_at(agitation_fixture(), 0, router_first=True)
                plan = session.front_plan(said, turn_id=2, language="ko", configuration_id=1,
                                          generation=1, router_first=True)
                self.assertIsNotNone(plan)
                self.assertEqual(session.last_front_rule, rule)
        session = session_at(agitation_fixture(), 0, router_first=True)
        self.assertIsNone(session.front_plan("그림 보여줘", turn_id=2, language="ko",
                                             configuration_id=1, generation=1, router_first=True))
        plain = session_at(agitation_fixture(), 0)
        self.assertIsNotNone(plain.front_plan("그림 보여줘", turn_id=2, language="ko",
                                              configuration_id=1, generation=1))
        self.assertEqual(plain.last_front_rule, "visual_request")

    def test_the_router_line_hands_the_note_to_the_model(self) -> None:
        said = "37.2도에서 790 rpm 으로 돌렸어, 특이사항으로 메모해 줘"
        session = session_at(agitation_fixture(), 0, router_first=True)
        client = FakeRouterClient(tool_reply(("record_log", {
            "type": "deviation", "value": "37.2도에서 790 rpm 으로 돌렸어", "evidence": said,
        })))
        outcome = route(session, said, client)
        self.assertEqual(outcome.handled_by, "llm+tool")
        self.assertIs(outcome.plan.action, CuratedProtocolAction.RECORD_OBSERVATION)
        self.assertEqual([t["function"]["name"] for t in client.requests[0]["tools"]],
                         [t["function"]["name"] for t in ROUTER_FIRST_CALL_TOOLS])
        # Off, the same words never reach the model.
        plain = session_at(agitation_fixture(), 0)
        outcome = route(plain, said, FakeRouterClient(), settings=ON)
        self.assertEqual(outcome.handled_by, "front:note_record")


class ReadOnlyToolTests(unittest.TestCase):
    """Decisions (a) and (e): show_visual, clarify, not_for_me, and answer + show_visual."""

    def test_the_calls_are_parsed_closed(self) -> None:
        self.assertEqual(
            parse_read_only_call(SHOW_VISUAL, {"kind": "source_figure", "evidence": "그림 보여 줘"}).kind,
            "source_figure")
        self.assertEqual(parse_read_only_call(SHOW_VISUAL, {"kind": "poster", "evidence": "x"}), "arguments_invalid")
        self.assertEqual(parse_read_only_call(CLARIFY, {"spoken": "", "candidates": [], "evidence": "x"}),
                         "arguments_invalid")
        proposal = parse_read_only_call(CLARIFY, json.dumps(
            {"spoken": "어느 용액이요?", "candidates": ["Solution A", "Solution B"], "evidence": "그거"}))
        self.assertEqual(proposal.candidates, ("Solution A", "Solution B"))
        self.assertEqual(parse_read_only_call(NOT_FOR_ME, {"evidence": "민수야"}).tool, NOT_FOR_ME)
        self.assertEqual(parse_read_only_call("change_state", {"action": "next", "evidence": "다음"}), "tool_unknown")

    def test_a_picture_asked_in_other_words_is_shown(self) -> None:
        session = session_at(agitation_fixture(), 0, router_first=True)
        client = FakeRouterClient(tool_reply((SHOW_VISUAL, {"kind": "source_figure", "evidence": "원문 그림 띄워 줘"})))
        outcome = route(session, "원문 그림 띄워 줘", client)
        self.assertEqual(outcome.handled_by, "llm+tool")
        self.assertEqual(outcome.read_only.tool, SHOW_VISUAL)
        plan = outcome.plan
        self.assertIs(plan.action, CuratedProtocolAction.VISUAL_REQUEST)
        self.assertEqual(plan.visual_kind, "source_figure")
        self.assertTrue(plan.visual_requested)
        self.assertIn(NO_FIGURE_WORDS, plan.speech_text)
        self.assertFalse(plan.state_changed)

    def test_a_picture_proposal_without_a_picture_word_is_refused(self) -> None:
        session = session_at(agitation_fixture(), 0, router_first=True)
        client = FakeRouterClient(tool_reply((SHOW_VISUAL, {"kind": "source_figure", "evidence": "이 단계 뭐야"})))
        outcome = route(session, "이 단계 뭐야", client)
        self.assertEqual(outcome.handled_by, "fallback_rules")
        self.assertEqual(outcome.fallback_reason, "refused:no_picture_word")

    def test_an_explanation_and_a_picture_in_one_breath_are_both_done(self) -> None:
        session = session_at(agitation_fixture(), 0, router_first=True)
        both = tool_reply(
            ("answer", {"spoken": "solution A는 1단계에서 밴드를 씻는 용액이에요.",
                        "source_kind": "pdf", "evidence_ids": ["S1.current_step"]}),
            (SHOW_VISUAL, {"kind": "source_figure", "evidence": "그림 보여 줘"}),
        )
        outcome = route(session, "solution A 설명해 주고 그림 보여 줘", FakeRouterClient(both))
        self.assertEqual(outcome.handled_by, "llm")
        plan = outcome.plan
        self.assertIs(plan.action, CuratedProtocolAction.VISUAL_REQUEST)
        self.assertTrue(plan.speech_text.startswith("solution A는 1단계에서 밴드를 씻는 용액이에요."))
        self.assertIn(NO_FIGURE_WORDS, plan.speech_text)
        self.assertEqual(plan.evidence_ids, ("S1.current_step",))
        self.assertEqual(plan.intent_kind, "llm_router_answer_with_visual")
        self.assertFalse(plan.state_changed)

    def test_a_picture_asked_beside_the_question_is_shown_after_the_answer(self) -> None:
        # The live model answered "AMBIC 설명해 주고 그림 보여 줘" with answer alone
        # (RA0, 2026-10-13); the server completes the two-request turn itself.
        session = session_at(agitation_fixture(), 0, router_first=True)
        outcome = route(session, "solution A 설명해 주고 그림 보여 줘", FakeRouterClient(answer_call_reply(
            "solution A는 1단계에서 밴드를 씻는 용액이에요.", evidence_ids=("S1.current_step",))))
        self.assertEqual(outcome.handled_by, "llm")
        self.assertEqual(outcome.read_only.tool, SHOW_VISUAL)
        plan = outcome.plan
        self.assertIs(plan.action, CuratedProtocolAction.VISUAL_REQUEST)
        self.assertEqual(plan.intent_kind, "llm_router_answer_with_visual")
        self.assertTrue(plan.speech_text.startswith("solution A는 1단계에서 밴드를 씻는 용액이에요."))
        self.assertIn(NO_FIGURE_WORDS, plan.speech_text)
        # A question with no picture word stays an answer.
        session = session_at(agitation_fixture(), 0, router_first=True)
        outcome = route(session, "solution A 설명해 줘", FakeRouterClient(answer_call_reply(
            "solution A는 1단계에서 밴드를 씻는 용액이에요.", evidence_ids=("S1.current_step",))))
        self.assertIsNone(outcome.read_only)
        self.assertIs(outcome.plan.action, CuratedProtocolAction.QUESTION)
        # Off, the picture request is the front rule's turn, as before (lane WV).
        session = session_at(agitation_fixture(), 0)
        outcome = route(session, "solution A 설명해 주고 그림 보여 줘", FakeRouterClient(answer_call_reply(
            "solution A는 1단계에서 밴드를 씻는 용액이에요.", evidence_ids=("S1.current_step",))), settings=ON)
        self.assertEqual(outcome.handled_by, "front:visual_request")
        self.assertIsNone(outcome.read_only)

    def test_a_note_asked_for_in_words_but_typed_as_an_anomaly_is_a_note(self) -> None:
        # The live model typed "메모 좀 해 줘 온도가 조금 높았어" as an anomaly
        # (RA0 switch-on run); asked for in words, it is the note the rules read.
        said = "어 저기 그 뭐냐 메모 좀 해 줘 온도가 조금 높았어"
        session = session_at(agitation_fixture(), 0, router_first=True)
        outcome = route(session, said, FakeRouterClient(tool_reply(("record_log", {
            "type": "anomaly", "value": "온도가 조금 높았어", "evidence": "메모 좀 해 줘 온도가 조금 높았어",
        }))))
        self.assertEqual(outcome.handled_by, "llm+tool")
        self.assertIs(outcome.plan.action, CuratedProtocolAction.RECORD_OBSERVATION)
        self.assertTrue(outcome.plan.reported_observation)
        self.assertEqual(outcome.plan.note_record["category"], "note")
        self.assertEqual(outcome.verdict.proposal.log_type, "memo")
        # A problem said as one is still an anomaly; off, an anomaly is asked about as before.
        session = session_at(agitation_fixture(), 0, router_first=True)
        outcome = route(session, "튜브가 터졌어 메모해 줘", FakeRouterClient(tool_reply(("record_log", {
            "type": "anomaly", "value": "튜브가 터졌어", "evidence": "튜브가 터졌어 메모해 줘",
        }))))
        self.assertIs(outcome.plan.action, CuratedProtocolAction.REPORT_ANOMALY)
        session = session_at(agitation_fixture(), 0)
        outcome = route(session, said, FakeRouterClient(tool_reply(("record_log", {
            "type": "anomaly", "value": "온도가 조금 높았어", "evidence": "메모 좀 해 줘 온도가 조금 높았어",
        }))), settings=ON)
        self.assertEqual(outcome.verdict.reason_code, "anomaly_needs_confirmation")

    def test_a_clarifying_question_is_asked_and_its_candidates_kept_for_the_screen(self) -> None:
        session = session_at(agitation_fixture(), 0, router_first=True)
        client = FakeRouterClient(tool_reply((CLARIFY, {
            "spoken": "어느 것을 말씀하시나요? solution A요, lysozyme이요?",
            "candidates": ["solution A", "lysozyme"], "evidence": "뭘 먼저",
        })))
        outcome = route(session, "둘 중에 뭘 먼저 넣어?", client)
        self.assertEqual(outcome.handled_by, "llm+tool")
        plan = outcome.plan
        self.assertIs(plan.action, CuratedProtocolAction.CLARIFY_REFERENCE)
        self.assertEqual(plan.speech_text, "어느 것을 말씀하시나요? solution A요, lysozyme이요?")
        self.assertEqual(list(session._clarify_candidates), ["solution A", "lysozyme"])
        self.assertFalse(plan.state_changed)
        # The next turn clears them.
        route(session, "solution A", FakeRouterClient(answer_call_reply(
            "solution A는 500 µL 써요.", evidence_ids=("S1.current_step",))), turn_id=3)
        self.assertEqual(list(session._clarify_candidates), [])

    def test_a_clarifying_question_that_is_the_servers_is_refused(self) -> None:
        for spoken, reason in (
            ("1단계 완료하셨나요?", "refused:clarify_rejected:server_question"),
            ("1단계로 넘어갔어요. 어느 거요?", "refused:clarify_rejected:claims_state_change"),
            ("900 rpm 말씀이세요?", "refused:clarify_rejected:number_not_in_source"),
        ):
            with self.subTest(spoken):
                session = session_at(agitation_fixture(), 0, router_first=True)
                client = FakeRouterClient(tool_reply((CLARIFY, {
                    "spoken": spoken, "candidates": [], "evidence": "뭘 먼저",
                })))
                outcome = route(session, "둘 중에 뭘 먼저 넣어?", client)
                self.assertEqual(outcome.handled_by, "fallback_rules")
                self.assertEqual(outcome.fallback_reason, reason)

    def test_words_not_for_the_assistant_are_passed_over_silently(self) -> None:
        session = session_at(agitation_fixture(), 0, router_first=True)
        client = FakeRouterClient(tool_reply((NOT_FOR_ME, {"evidence": "야 민수야 이거 좀 잡아줘"})))
        outcome = route(session, "야 민수야 이거 좀 잡아줘", client)
        self.assertEqual(outcome.handled_by, "llm+tool")
        plan = outcome.plan
        self.assertIs(plan.action, CuratedProtocolAction.OFF_TOPIC)
        self.assertEqual(plan.intent_kind, "not_addressed")
        self.assertEqual(plan.speech_policy, "silent")
        self.assertFalse(plan.state_changed)
        self.assertFalse(plan.reported_observation)
        self.assertEqual(session.current_index, 0)

    def test_words_that_carry_a_command_record_or_term_are_for_the_assistant(self) -> None:
        for said in ("민수야 다음 단계로 넘어가자", "이거 메모해 줘 민수야", "lysozyme 얼마나 넣지"):
            with self.subTest(said):
                session = session_at(agitation_fixture(), 0, router_first=True)
                client = FakeRouterClient(tool_reply((NOT_FOR_ME, {"evidence": said})))
                outcome = route(session, said, client)
                self.assertEqual(outcome.handled_by, "fallback_rules")
                self.assertEqual(outcome.fallback_reason, "refused:addressed_to_assistant")

    def test_a_read_only_call_beside_a_state_tool_is_the_state_tool(self) -> None:
        session = session_at(agitation_fixture(), 0, router_first=True)
        both = tool_reply(
            (CLARIFY, {"spoken": "어느 거요?", "candidates": [], "evidence": "메모"}),
            ("record_log", {"type": "measurement", "value": "pH 7.2 나왔어", "evidence": "pH 7.2 나왔어 메모해 줘"}),
        )
        outcome = route(session, "pH 7.2 나왔어 메모해 줘", FakeRouterClient(both))
        self.assertEqual(outcome.handled_by, "llm+tool")
        self.assertIs(outcome.plan.action, CuratedProtocolAction.RECORD_OBSERVATION)
        self.assertTrue(outcome.plan.reported_observation)

    def test_the_old_mode_does_not_offer_or_take_the_new_tools(self) -> None:
        session = session_at(agitation_fixture(), 0)
        client = FakeRouterClient(tool_reply((NOT_FOR_ME, {"evidence": "점심 뭐 먹을래"})))
        outcome = route(session, "점심 뭐 먹을래", client, settings=ON)
        self.assertEqual([t["function"]["name"] for t in client.requests[0]["tools"]],
                         ["answer", "change_state", "record_log"])
        self.assertEqual(outcome.handled_by, "fallback_rules")
        self.assertEqual(outcome.fallback_reason, "refused:tool_unknown")


@unittest.skipUnless(
    SOURCE_PDF.is_file(),
    f"requires the externally licensed Candidate A source PDF at {SOURCE_PDF}",
)
class RouterFirstVoiceTests(RouterFirstVoiceMixin, unittest.TestCase):
    """Over the WebSocket: off, the switch changes nothing."""

    def test_off_the_switch_changes_nothing(self) -> None:
        said = "젤 플러그를 37.2도에서 790 rpm 으로 교반했어, 특이사항으로 메모해 줘"

        async def steps(socket, listener, say, seen):
            await say(1, "프로토콜 시작해줘")
            await say(2, "1단계 완료했어")
            await say(3, "2단계 완료했어")
            await say(4, said)

        socket, seen, client = self._run(steps, first=False)
        self.assertEqual(client.requests, [])
        self.assertEqual(seen["history"][-1]["handled_by"], "front:note_record")
        state = socket.for_turn(4, "protocol.fixture.state")[-1]
        self.assertIsNone(state["screen"].get("record_deviation"))


if __name__ == "__main__":
    unittest.main()
