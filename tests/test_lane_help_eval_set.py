"""Lane HELP, decision 4 (2026-10-24): the problem-help evaluation set.

``data/fixtures/evaluation/problem_help_eval.json`` holds the problems said at
the bench (Korean, English and both mixed, with and without a question mark)
and what the help must and must not say, for the in-gel fixture and the
starter-pack protocols; lane KNOW measures the public tier on the same file.
Here: its shape (always), and on its in-gel rows (the licensed PDF, condition
A) the guarantees that hold for every row -- no workflow state moves, nothing
forbidden is shown or said -- and that the development rows get the help.
The rates (grounded, history, latency) are measured outside the suite (the
lane report) on every protocol of the set.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from tests.protocol_vocabulary_support import SOURCE_PDF, in_gel_fixture
from voiney_lab.curated_protocol import CuratedProtocolAction, CuratedProtocolSession
from voiney_lab.runtime_routing import route_curated_runtime_turn

ROOT = Path(__file__).resolve().parents[1]
SET = ROOT / "data/fixtures/evaluation/problem_help_eval.json"
FIELDS = {"id", "set", "protocol", "step", "utterance", "expect_help", "expect_record",
          "expected_protocol", "history", "expected_history", "forbidden"}
STARTER_PACK = ROOT / "scripts/pilot_starter_pack.csv"


def _cases() -> list[dict]:
    return json.loads(SET.read_text(encoding="utf-8"))["cases"]


class EvaluationSetShapeTests(unittest.TestCase):
    def test_every_case_has_its_fields_and_a_known_protocol(self) -> None:
        pack = STARTER_PACK.read_text(encoding="utf-8")
        ids = set()
        for case in _cases():
            with self.subTest(case=case.get("id")):
                self.assertLessEqual(FIELDS, set(case))
                self.assertNotIn(case["id"], ids)
                ids.add(case["id"])
                self.assertIn(case["set"], {"development", "heldout_1", "heldout_2"})
                self.assertTrue(case["protocol"] == "in_gel" or case["protocol"] in pack)
                for item in case["history"]:
                    self.assertIn(item["owner"], {"same", "other"})

    def test_forty_problems_or_more_in_both_languages(self) -> None:
        helped = [case for case in _cases() if case["expect_help"]]
        self.assertGreaterEqual(len(helped), 40)
        said = [case["utterance"] for case in _cases()]
        self.assertTrue(any(re.search(r"[가-힣]", text) and re.search(r"[A-Za-z]{3}", text) for text in said))
        self.assertTrue(any(not re.search(r"[가-힣]", text) for text in said))
        self.assertTrue(any(not text.rstrip().endswith("?") for text in said))
        self.assertTrue(any(case["expected_history"] for case in _cases()))
        self.assertTrue(any(item["owner"] == "other" for case in _cases() for item in case["history"]))


@unittest.skipUnless(
    SOURCE_PDF.is_file(),
    f"requires the externally licensed Candidate A source PDF at {SOURCE_PDF}",
)
class InGelRowsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = in_gel_fixture()
        data = json.loads(SET.read_text(encoding="utf-8"))
        cls.forbidden = data["forbidden_everywhere"]
        cls.rows = [case for case in data["cases"] if case["protocol"] == "in_gel"]

    def _plan(self, case: dict):
        session = CuratedProtocolSession(self.fixture)
        session.activate_configured()
        session.plan("시작", turn_id=1, language="ko", configuration_id=1, generation=1)
        session.current_index = [step.source_label for step in self.fixture.steps].index(case["step"])
        session._notebook_question = None
        before = (session.active, session.current_index, session._workflow_status,
                  session._timer_started_at, len(session._endpoint_observations))
        plan = route_curated_runtime_turn(
            session, case["utterance"], turn_id=2, language="ko", configuration_id=1, generation=1,
        ).plan
        after = (session.active, session.current_index, session._workflow_status,
                 session._timer_started_at, len(session._endpoint_observations))
        return plan, before, after

    def test_no_row_moves_anything_or_says_what_is_forbidden(self) -> None:
        for case in self.rows:
            with self.subTest(case=case["id"], said=case["utterance"]):
                plan, before, after = self._plan(case)
                self.assertEqual(after, before)
                self.assertFalse(plan.state_changed)
                said = f"{plan.display_text}\n{plan.speech_text}"
                for words in (*self.forbidden, *case["forbidden"]):
                    self.assertNotIn(words, said)

    def test_development_rows_are_helped_and_controls_are_not(self) -> None:
        for case in self.rows:
            if case["set"] != "development":
                continue
            with self.subTest(case=case["id"], said=case["utterance"]):
                plan, _before, _after = self._plan(case)
                helped = plan.problem_help is not None or plan.action is CuratedProtocolAction.PROBLEM_HELP
                # A whole-English sentence is helped on the rules' path; at the
                # voice boundary the Korean session asks it again (the set's note).
                english = not re.search(r"[가-힣]", case["utterance"])
                # QA1 makes the prospective problem C03 read-only grounded
                # help; the rest of this older HELP set keeps its expectation.
                expected = case["expect_help"] or english or case["id"] == "C03"
                self.assertEqual(helped, expected)


if __name__ == "__main__":
    unittest.main()
