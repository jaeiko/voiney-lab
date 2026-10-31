"""Lane SAFE2, item 3: a problem said as having happened, then asked about, is recorded and answered.

"침전이 생겼는데 이러면 어떡해?", "거품이 났어 어떻게 해?": the thing happened
(a past predicate before the question), so it is an anomaly of the current
step -- recorded and read back the way "침전이 생겼어" is (lane HELP's record,
lane SAFE1's read-back) -- and the same turn carries the read-only grounded
answer of lane QA1: the exact current-revision excerpt with its step and
page, or "원문에 없음". A problem supposed ("침전이 생기면 어떡해?") stays lane
GEN1's: answered, never recorded. A problem only asked about ("왜 아직
파래?", "왜 안 되지?") stays lane HELP's: helped, not recorded. Every public
protocol of four fields is held to the same reading; nothing here names a
protocol's words.
"""

from __future__ import annotations

import asyncio
import unittest
from argparse import Namespace

from scripts.sim.corpus import Case, load_corpus
from scripts.sim.runner import run_case
from voiney_lab.intent_arbitration import RequestIntent, arbitrate_request, problem_reading, supposes

PROTOCOLS = load_corpus()

#: Development wording; the SIM's holdout side is scored, never read here.
HAPPENED_THEN_ASKED = (
    "침전이 생겼는데 이러면 어떡해?",
    "거품이 났어 어떻게 해?",
    "용액이 탁해졌어 어떻게 해?",
    "막이 뿌옇게 됐는데 어떡해?",
    "상층액이 탁해졌는데 어떡해?",
    "세포가 다 떨어져 나갔는데 이러면 어떡하지?",
)
ONLY_ASKED = ("왜 아직 파래?", "왜 안 되지?", "침전이 생기면 어떡해?", "혹시 거품이 나면 어떡해?")
ONLY_SAID = ("침전이 생겼어", "용액이 탁해졌어", "젤이 아직 파래")
#: The recognizer cut the last word before its ending (the error axis's tail cut of
#: a supposition): a report or a supposition, nobody can tell -- asked once.
CUT_SHORT = ("여기서 거품이 생기", "혹시 침전이 생기", "용액이 탁해지", "용액이 탁해져서", "막이 뿌예지")
#: Finished words that the cut-short reading must leave alone: sentence endings
#: ("-지?", "-나", "-져"), reports, and English.
FINISHED = ("왜 안 되지?", "이거 맞지?", "원심분리기에서 이상한 소리가 나", "색이 안 빠져", "침전이 생겼지")


def heard(protocol, text: str, *, index: int = 1) -> dict:
    # The SIM family whose grounding the runner measures (the reply's source texts).
    return asyncio.run(run_case(protocol, Case(f"{protocol.code}.safe2", "happened_question", text, index, "active")))


class ReadingTests(unittest.TestCase):
    def test_a_past_report_before_the_question_is_a_statement_asked_about(self) -> None:
        for text in HAPPENED_THEN_ASKED:
            with self.subTest(text=text):
                reading = problem_reading(text)
                self.assertIsNotNone(reading, text)
                self.assertEqual(reading.kind, "statement_question")
                self.assertFalse(supposes(text))
                decision = arbitrate_request(text)
                self.assertIs(decision.intent, RequestIntent.TROUBLESHOOTING)
                self.assertEqual(decision.reason_code, "problem_statement_question")

    def test_a_question_alone_and_a_supposition_keep_their_readings(self) -> None:
        for text in ONLY_ASKED[:2]:
            with self.subTest(text=text):
                self.assertEqual(problem_reading(text).kind, "question")
        for text in ONLY_ASKED[2:]:
            with self.subTest(text=text):
                self.assertIsNone(problem_reading(text))
                self.assertTrue(supposes(text))
                self.assertEqual(arbitrate_request(text).reason_code, "problem_contingency_question")
        for text in ONLY_SAID:
            with self.subTest(text=text):
                self.assertEqual(problem_reading(text).kind, "statement")


class CutShortTests(unittest.TestCase):
    def test_a_problem_cut_before_its_ending_is_unclear_and_no_statement(self) -> None:
        from voiney_lab.intent_arbitration import supposed_problem_reading, unfinished_problem_statement

        for text in CUT_SHORT:
            with self.subTest(text=text):
                self.assertTrue(unfinished_problem_statement(text))
                self.assertIsNone(problem_reading(text))
                self.assertEqual(supposed_problem_reading(text), "unclear")
                self.assertEqual(arbitrate_request(text).reason_code, "problem_supposed_or_happened")
        for text in (*ONLY_SAID, *HAPPENED_THEN_ASKED, *FINISHED, "침전이 생겼는데", "the gel is still blue", "pH 7.2"):
            with self.subTest(text=text):
                self.assertFalse(unfinished_problem_statement(text))

    def test_a_cut_problem_is_asked_about_once_and_records_nothing(self) -> None:
        from voiney_lab.curated_protocol import SUPPOSED_OR_HAPPENED_QUESTION

        for protocol in PROTOCOLS:
            for text in CUT_SHORT[:3]:
                with self.subTest(protocol=protocol.code, field=protocol.field, text=text):
                    result = heard(protocol, text)
                    self.assertNotIn("anomaly", result["changed_fields"], result)
                    self.assertEqual(result["intent_kind"], "problem_supposed_or_happened", result)
                    self.assertIn(SUPPOSED_OR_HAPPENED_QUESTION, result["spoken"])
                    self.assertEqual(result["step_after"], result["step_before"])


class VoiceBoundaryTests(unittest.TestCase):
    """Through ListenerSession.accept_chunk -> run_turn on the ten public protocols; no provider."""

    def test_the_event_is_recorded_and_the_grounded_answer_is_in_the_same_reply(self) -> None:
        for protocol in PROTOCOLS:
            for text in HAPPENED_THEN_ASKED:
                with self.subTest(protocol=protocol.code, field=protocol.field, text=text):
                    result = heard(protocol, text)
                    self.assertEqual(result["action"], "report_anomaly", result)
                    self.assertEqual(result["intent_kind"], "problem_report_question")
                    self.assertIn("anomaly", result["changed_fields"])
                    self.assertFalse(set(result["changed_fields"]) - {"anomaly", "confirmation", "record", "revision"}, result)
                    self.assertEqual(result["step_after"], result["step_before"])
                    grounding = result["grounding"]
                    self.assertIsNotNone(grounding, "the grounded answer was not measured")
                    self.assertTrue(grounding["grounded"], result["spoken"])
                    self.assertFalse(grounding["invented_values"])
                    self.assertFalse(grounding["generated_safety"])

    def test_a_supposition_and_a_bare_question_record_nothing(self) -> None:
        for protocol in PROTOCOLS[:4]:
            for text in ONLY_ASKED:
                with self.subTest(protocol=protocol.code, text=text):
                    result = heard(protocol, text)
                    self.assertNotIn("anomaly", result["changed_fields"], result)
                    self.assertNotEqual(result["action"], "report_anomaly")


class SimFamilyTests(unittest.TestCase):
    def test_every_field_has_its_happened_questions_on_both_sides_and_the_partition_is_unchanged(self) -> None:
        from scripts.sim.corpus import HAPPENED_QUESTIONS, generate_cases, generate_happened_cases
        from scripts.sim.split import partition_cases, select_cases

        self.assertLessEqual({p.field for p in PROTOCOLS}, set(HAPPENED_QUESTIONS))
        for protocol in PROTOCOLS:
            development = generate_happened_cases(protocol, "development")
            holdout = generate_happened_cases(protocol, "holdout")
            self.assertTrue(development and holdout)
            self.assertTrue({c.text for c in development}.isdisjoint(c.text for c in holdout))
            for case in development + holdout:
                self.assertEqual(case.family, "happened_question")
                self.assertEqual(case.actions, ("report_anomaly",))
                self.assertIn("anomaly", case.allowed)
        common = dict(split_seed=1027, holdout_fraction=.3, emergency_matrix=False, protocol=None, family=None, limit=None)
        base = partition_cases(tuple((p, c) for p in PROTOCOLS for c in generate_cases(p)))
        development, dev_meta = select_cases(PROTOCOLS, Namespace(split="development", **common))
        holdout, hold_meta = select_cases(PROTOCOLS, Namespace(split="holdout", **common))
        dev_codes = {c.code for _, c in development if c.family == "happened_question"}
        hold_codes = {c.code for _, c in holdout if c.family == "happened_question"}
        self.assertTrue(dev_codes and hold_codes and dev_codes.isdisjoint(hold_codes))
        self.assertEqual(dev_meta["manifest_sha256"], base.manifest)
        self.assertEqual(hold_meta["manifest_sha256"], base.manifest)

    def test_an_event_left_unrecorded_or_unanswered_is_counted(self) -> None:
        from scripts.sim.corpus import generate_happened_cases
        from scripts.sim.metrics import aggregate, score_case

        case = generate_happened_cases(PROTOCOLS[0], "development")[0]
        base = {"intent_kind": "problem_question", "route": "rules", "terminal": True, "drop_reasons": [],
                "spoken": "", "transcript": case.text, "stt_calls": 1, "step_before": case.index,
                "step_after": case.index, "timings_ms": {}}
        helped_only = score_case(case, {**base, "action": "problem_help", "changed_fields": [],
                                        "grounding": {"grounded": True, "invented_values": False, "generated_safety": False}})
        self.assertTrue(helped_only["happened_unrecorded"])
        self.assertFalse(helped_only["understood"])
        recorded_unanswered = score_case(case, {**base, "action": "report_anomaly", "changed_fields": ["anomaly"],
                                                "grounding": {"grounded": False, "invented_values": False, "generated_safety": False}})
        self.assertFalse(recorded_unanswered["happened_unrecorded"])
        self.assertFalse(recorded_unanswered["understood"])
        both = score_case(case, {**base, "action": "report_anomaly", "changed_fields": ["anomaly"],
                                 "grounding": {"grounded": True, "invented_values": False, "generated_safety": False}})
        self.assertTrue(both["understood"] and not both["wrong_state"])
        self.assertEqual(aggregate([helped_only, both])["overall"]["happened_unrecorded"], 1)


if __name__ == "__main__":
    unittest.main()
