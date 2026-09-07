"""A repeat ends when a person says so, and the agent never says it.

The source decides what finishing a repeat means -- "until the gel band is
fully destained" -- and a person standing at the bench decides whether that has
happened. The agent's whole part is to say that a repeat interval is open,
which steps it covers, and what the document says about it, read exactly as
written. Then it stops.

That is L1. It is not a weaker version of judging completion; it is the refusal
to judge completion, made usable. Everything the agent could say that would
amount to a completion criterion the source never gave -- how many rounds are
enough, that a round was enough, what the band looks like now -- is absent by
design, and this file asserts the absence.
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IN_GEL = ROOT / "data" / "runtime" / "candidate-a-source" / "in-gel-digestion.pdf"

#: Phrases that would put the agent in the operator's place. Any of these
#: reaching a disclosure is a design failure, not a wording problem.
_FORBIDDEN = (
    "충분",          # "that's enough"
    "넘어가",        # "you may move on"
    "완료되었",      # "it has completed"
    "된 것 같",      # "it looks like it has"
    "보통",          # "usually"
    "회째",          # "round N"
    "번 반복하면",   # "if you repeat N times"
)


def _session():
    from tests.test_merge_completes_on_real_chunks import _merge_from_cache
    from voice_workflow_agent.curated_protocol import (
        CuratedProtocolFixture,
        CuratedProtocolSession,
    )

    built = _merge_from_cache()
    if built is None:
        return None
    extraction, _plan, _merged, draft = built
    labels = tuple(
        step.source_label
        for section in draft.protocol.sections
        for step in section.steps
    )
    fixture = CuratedProtocolFixture(
        draft=draft,
        status="fictional_non_operational",
        ordered_step_labels=labels,
        fixture_sha256="0" * 64,
        revision_id="repeat-interval-test",
        development_only=True,
        source_pdf_path=IN_GEL,
        source_filename=draft.extraction.original_filename,
    )
    session = CuratedProtocolSession(fixture)
    session.active = True
    return session


class TheIntervalIsHandedOverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not IN_GEL.is_file():
            raise unittest.SkipTest(f"{IN_GEL} is not present.")
        session = _session()
        if session is None:
            raise unittest.SkipTest("in-gel is not fully cached on this machine.")
        cls.session = session
        cls.index = next(
            (
                index
                for index in range(len(session.fixture.steps))
                if session.repeat_interval_starting_at(index) is not None
            ),
            None,
        )
        if cls.index is None:
            raise unittest.SkipTest("this Protocol carries no repeat interval.")

    def test_the_disclosure_names_the_range_and_quotes_the_source(self) -> None:
        disclosure = self.session.human_led_repeat_disclosure(self.index)
        self.assertIsNotNone(disclosure)
        self.assertEqual(
            disclosure["repeated_step_labels"], ["2", "3", "4", "5", "6", "7"]
        )
        self.assertEqual(disclosure["who_decides_completion"], "operator")
        self.assertIn("Repeat steps 2-7", disclosure["source_text"])
        self.assertFalse(disclosure["completed"])

    def test_nothing_the_agent_says_is_a_completion_criterion(self) -> None:
        """2-2: read every word the agent would utter."""

        disclosure = self.session.human_led_repeat_disclosure(self.index)
        spoken = " ".join(
            str(disclosure[field])
            for field in ("notice", "source_text")
        )
        for phrase in _FORBIDDEN:
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, spoken)
        # And what it does say hands the decision over explicitly.
        self.assertIn("직접 판단해 주시고", disclosure["notice"])

    def test_the_quoted_sentence_is_the_document_s_own(self) -> None:
        """Verbatim, not a reading of it."""

        disclosure = self.session.human_led_repeat_disclosure(self.index)
        construct = next(
            item
            for item in self.session.fixture.draft.protocol.constructs
            if getattr(item, "repetition_id", None) == disclosure["repetition_id"]
        )
        self.assertEqual(
            disclosure["source_text"], construct.condition_source_text
        )

    def test_no_round_count_is_offered_anywhere(self) -> None:
        """2-3: the agent does not count, so it reports no count."""

        disclosure = self.session.human_led_repeat_disclosure(self.index)
        self.assertNotIn("round", disclosure)
        self.assertNotIn("iteration", disclosure)
        self.assertNotIn("repeat_count", disclosure)
        record = self.session.repeat_interval_record(
            str(disclosure["repetition_id"])
        )
        self.assertNotIn("rounds", record)


class OnlyAPersonClosesItTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not IN_GEL.is_file():
            raise unittest.SkipTest(f"{IN_GEL} is not present.")

    def setUp(self) -> None:
        session = _session()
        if session is None:
            self.skipTest("in-gel is not fully cached on this machine.")
        self.session = session
        self.index = next(
            index
            for index in range(len(session.fixture.steps))
            if session.repeat_interval_starting_at(index) is not None
        )
        self.interval = session.repeat_interval_starting_at(self.index)
        self.identifier = str(self.interval["repetition_id"])

    def _step_after(self):
        order = [step.step_id for step in self.session.fixture.steps]
        last = self.interval["repeated_step_ids"][-1]
        return order[order.index(last) + 1]

    def test_a_step_after_the_interval_will_not_begin_until_declared(self):
        after = self._step_after()
        self.assertFalse(self.session.may_leave_repeat_interval(after))
        self.assertEqual(
            self.session.repeat_intervals_awaiting_completion(),
            (self.identifier,),
        )

        self.session.declare_repeat_interval_complete(
            self.identifier,
            at="2026-09-07T00:00:00Z",
            actor_principal_id="operator-a",
            actor_role="researcher",
        )
        self.assertTrue(self.session.may_leave_repeat_interval(after))
        self.assertEqual(self.session.repeat_intervals_awaiting_completion(), ())

    def test_a_step_inside_the_interval_is_not_blocked_by_it(self) -> None:
        """The repeat is run, not withheld. Only leaving it needs a person."""

        inside = self.interval["repeated_step_ids"][2]
        self.assertTrue(self.session.may_leave_repeat_interval(inside))

    def test_the_record_holds_the_hand_over_the_declaration_and_who(self):
        """2-3, and nothing else: no count, inferred or otherwise."""

        self.session.enter_repeat_interval(
            self.index, at="2026-09-07T00:00:00Z"
        )
        self.session.declare_repeat_interval_complete(
            self.identifier,
            at="2026-09-07T01:23:00Z",
            actor_principal_id="operator-a",
            actor_role="researcher",
        )
        record = self.session.repeat_interval_record(self.identifier)
        self.assertEqual(
            sorted(record),
            [
                "completed_at",
                "declared_by_principal_id",
                "declared_by_role",
                "handed_over_at",
            ],
        )
        self.assertEqual(record["declared_by_principal_id"], "operator-a")

    def test_a_declaration_needs_a_real_repeat_a_time_and_a_person(self) -> None:
        for kwargs in (
            {"repetition_id": "not-a-repetition"},
            {"at": "   "},
            {"actor_principal_id": ""},
            {"actor_role": "  "},
        ):
            with self.subTest(bad=sorted(kwargs)):
                call = {
                    "repetition_id": self.identifier,
                    "at": "2026-09-07T00:00:00Z",
                    "actor_principal_id": "operator-a",
                    "actor_role": "researcher",
                }
                call.update(kwargs)
                identifier = call.pop("repetition_id")
                with self.assertRaises(ValueError):
                    self.session.declare_repeat_interval_complete(
                        identifier, **call
                    )

    def test_a_new_run_re_enters_every_interval(self) -> None:
        self.session.declare_repeat_interval_complete(
            self.identifier,
            at="2026-09-07T00:00:00Z",
            actor_principal_id="operator-a",
            actor_role="researcher",
        )
        self.assertEqual(self.session.repeat_intervals_awaiting_completion(), ())
        self.session.reset()
        self.assertEqual(
            self.session.repeat_intervals_awaiting_completion(),
            (self.identifier,),
        )

    def test_a_declaration_clears_no_readiness_gate(self) -> None:
        from voice_workflow_agent import experiment_protocol as domain

        before = domain.assess_readiness(self.session.fixture.draft.protocol)
        self.session.declare_repeat_interval_complete(
            self.identifier,
            at="2026-09-07T00:00:00Z",
            actor_principal_id="operator-a",
            actor_role="researcher",
        )
        after = domain.assess_readiness(self.session.fixture.draft.protocol)
        self.assertEqual(before.reason_codes, after.reason_codes)


class AnUncapturedRepeatIsNotLedTests(unittest.TestCase):
    """2-6: what the analysis never carried cannot be handed over."""

    @classmethod
    def setUpClass(cls) -> None:
        if not IN_GEL.is_file():
            raise unittest.SkipTest(f"{IN_GEL} is not present.")
        session = _session()
        if session is None:
            raise unittest.SkipTest("in-gel is not fully cached on this machine.")
        cls.session = session

    def test_only_captured_repeats_have_intervals(self) -> None:
        intervals = self.session._repeat_intervals_by_id()
        labels = {
            step.step_id: step.source_label for step in self.session.fixture.steps
        }
        ranges = {
            (
                labels[interval["repeated_step_ids"][0]],
                labels[interval["repeated_step_ids"][-1]],
            )
            for interval in intervals.values()
        }
        self.assertIn(("2", "7"), ranges)
        # in-gel states 8-9 and 17-18 and the analysis carried neither, so
        # there is nothing to lead and the agent offers nothing.
        self.assertNotIn(("8", "9"), ranges)
        self.assertNotIn(("17", "18"), ranges)

    def test_the_document_is_kept_out_of_execution_instead(self) -> None:
        """The uncaptured ones are answered for by a readiness gate."""

        from voice_workflow_agent import experiment_protocol as domain

        self.assertIn(
            domain.ReadinessReasonCode.SOURCE_STATES_AN_UNCAPTURED_REPETITION.value,
            self.session.fixture.draft.readiness.reason_codes,
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
