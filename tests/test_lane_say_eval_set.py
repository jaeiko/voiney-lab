"""Lane SAY's evaluation rows (``data/fixtures/evaluation/korean_first_voice_eval.json``).

Each row is said through the rules' route on lane N's fictional wash (no
licensed PDF, so it runs in CI) and held to what the person's decisions of
2026-10-25 expect: what the reply does, whether the step moves, whether
anything is recorded, and -- Korean first -- that no source sentence is
said in English.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from tests.lane_n_support import notes_fixture
from voiney_lab.curated_protocol import CuratedProtocolSession
from voiney_lab.runtime_routing import route_curated_runtime_turn

ROOT = Path(__file__).resolve().parents[1]
SET = ROOT / "data/fixtures/evaluation/korean_first_voice_eval.json"


def _cases() -> list[dict]:
    return json.loads(SET.read_text(encoding="utf-8"))["cases"]


def _said(session: CuratedProtocolSession, words: str, turn_id: int):
    return route_curated_runtime_turn(
        session, words, turn_id=turn_id, language="ko", configuration_id=1, generation=1).plan


def _session(case: dict) -> CuratedProtocolSession:
    fixture = notes_fixture()
    session = CuratedProtocolSession(fixture)
    session.activate_configured()
    _said(session, "프로토콜 시작해줘", 1)
    index = next(i for i, step in enumerate(fixture.steps) if step.source_label == case["step"])
    if case["setup"] == "purpose_question":
        assert session.notebook_question_open() == "purpose"
        session.current_index = index
    else:
        _said(session, "넘어가", 2)
        session.current_index = index
        if case["setup"] == "observation_question":
            session.open_notebook_question("observation", completed_index=index - 1)
    return session


class EvaluationSetTests(unittest.TestCase):
    def test_the_set_has_its_fields_and_fifty_rows(self) -> None:
        ids = set()
        for case in _cases():
            with self.subTest(case=case.get("id")):
                self.assertLessEqual({"id", "fixture", "step", "setup", "utterance", "expect", "why"}, set(case))
                self.assertNotIn(case["id"], ids)
                ids.add(case["id"])
                self.assertIn(case["setup"], {"none", "purpose_question", "observation_question"})
        self.assertGreaterEqual(len(ids), 50)

    def test_every_row_does_what_the_decisions_expect(self) -> None:
        source = [step.instruction_source_text for step in notes_fixture().steps]
        for case in _cases():
            expect = case["expect"]
            with self.subTest(case=case["id"], words=case["utterance"]):
                session = _session(case)
                before = session.current_index
                plan = _said(session, case["utterance"], 10)
                if expect["action"] is not None:
                    self.assertEqual(plan.action.value, expect["action"])
                if expect["intent"] is not None:
                    self.assertEqual(plan.intent_kind, expect["intent"])
                self.assertEqual(session.current_index != before, expect["moved"])
                recorded = bool(
                    plan.notebook_record or plan.note_record or plan.reported_observation
                    or plan.reported_anomaly or plan.step_record)
                self.assertEqual(recorded, expect["record"])
                if expect["no_english_source"]:
                    said = session.korean_first(plan, language="ko").plan.speech_text
                    for text in source:
                        body = re.sub(r"^\d+\s+", "", text)
                        self.assertNotIn(body[:20], said)


if __name__ == "__main__":
    unittest.main()
