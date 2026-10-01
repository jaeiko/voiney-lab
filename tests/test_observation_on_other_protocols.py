"""A repeat step of a protocol that is not in-gel still moves on its observation.

The observation judgement keeps in-gel's endpoint words ("완전히 탈색", "투명",
"흰색"); that is unchanged by decision. What a protocol without those words
must still do is reach the yes/no question and act on it: the gate stands
where the analysis anchors a repetition, an observation the judgement cannot
read is asked again as yes/no, "네" records the endpoint and moves on, and
"아니요" keeps the step and quotes the repeat this protocol states.
"""

from __future__ import annotations

import dataclasses
import unittest

from voiney_lab import experiment_protocol as domain
from voiney_lab.curated_protocol import (
    CuratedProtocolAction,
    CuratedProtocolSession,
    steps_anchoring_a_repetition,
)
from voiney_lab.runtime_routing import route_curated_runtime_turn

from tests.protocol_vocabulary_support import build_fixture

STEPS = (
    "1 Load 200 µL lysate onto the column.",
    "2 Wash the column with 500 µL Buffer W.",
    "3 Repeat steps 1-2 until the column is clear.",
    "4 Elute with 50 µL water.",
)
#: Says what was seen, in words the in-gel judgement has no rule for.
UNREAD_OBSERVATION = "컬럼이 맑아졌어"


def _column_fixture():
    base = build_fixture(
        protocol_id="fictional-column", title="Fictional column clearing", steps=STEPS,
    )
    protocol = base.draft.protocol
    repeat = protocol.sections[0].steps[2]
    protocol = dataclasses.replace(protocol, constructs=(
        domain.RepeatUntil(
            repetition_id="repeat-1",
            condition_source_text="until the column is clear",
            repeated_step_ids=("step-1", "step-2"),
            evidence=domain.SourceEvidence(1, repeat.instruction_source_text),
            step_id="step-3",
        ),
    ))
    domain.validate_protocol(protocol)
    draft = dataclasses.replace(
        base.draft, protocol=protocol, readiness=domain.assess_readiness(protocol),
    )
    return dataclasses.replace(base, draft=draft)


class ObservationOnAnotherProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = _column_fixture()
        self.session = CuratedProtocolSession(self.fixture)
        self.session.active = True
        self.session.current_index = 2
        self.turn = 0

    def say(self, utterance: str):
        self.turn += 1
        return route_curated_runtime_turn(
            self.session, utterance, turn_id=self.turn, language="ko").plan

    def reach_the_yes_no_question(self) -> None:
        self.assertEqual(self.say("다음").intent_kind, "next_step_confirmation_required")
        prompt = self.say("네")
        self.assertEqual(prompt.intent_kind, "observation_confirmation_required")
        self.assertIn("until the column is clear", prompt.speech_text)
        reasked = self.say(UNREAD_OBSERVATION)
        self.assertEqual(reasked.intent_kind, "observation_confirmation_reasked")
        self.assertIn("네 또는 아니요로 답해 주세요", reasked.speech_text)
        self.assertFalse(reasked.state_changed)
        self.assertEqual(self.session.current_index, 2)

    def test_the_gate_stands_where_this_protocol_states_its_repeat(self) -> None:
        self.assertEqual(steps_anchoring_a_repetition(self.fixture), frozenset({"step-3"}))

    def test_yes_records_the_endpoint_and_moves_on(self) -> None:
        self.reach_the_yes_no_question()
        moved = self.say("네")
        self.assertEqual(moved.action, CuratedProtocolAction.NEXT)
        self.assertTrue(moved.state_changed)
        self.assertTrue(moved.reported_observation)
        self.assertEqual(moved.observation_predicate, "positive")
        self.assertEqual(self.session.current_index, 3)

    def test_no_keeps_the_step_and_quotes_this_protocols_repeat(self) -> None:
        self.reach_the_yes_no_question()
        kept = self.say("아니요")
        self.assertFalse(kept.state_changed)
        self.assertEqual(kept.observation_predicate, "negative")
        self.assertEqual(self.session.current_index, 2)
        self.assertIn("1–2단계 반복", kept.speech_text)
        # And the step is not stuck: the next round can still move on.
        self.reach_the_yes_no_question()
        self.assertTrue(self.say("네").state_changed)
        self.assertEqual(self.session.current_index, 3)


if __name__ == "__main__":
    unittest.main()
