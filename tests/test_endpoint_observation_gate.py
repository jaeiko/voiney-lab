"""The repeat-step gate reads the document, and writes down what released it.

Two faults, one place. The gate that holds a repeat-until step until the
experimenter reports its endpoint was written as three of in-gel's step labels
-- ``{"7", "9"}`` and ``"20"`` -- in three places: where the gate stands, where
a step is given an endpoint predicate, and where an utterance is allowed to be
read as an endpoint report. A rule made of one document's page numbers is not a
rule (principle 1), and it meant the gate existed for that single PDF and for
no other source. The same labels chose the sentence the agent said when the
endpoint was *not* reached, so a second document's operator would have been
told to repeat steps 17-18 of a procedure that never mentions them -- a source
instruction nobody wrote (principle 8).

And the report that released the gate was a one-turn authorisation. It
satisfied the transition inside the turn that carried it and left nothing
behind, so the gate could only ever rest on readiness calling the construct
unsupported. Written down -- with the protocol, the revision, the step, the
words, the time and the actor, and deliberately without a count of rounds --
the report becomes the gate's own footing.

Everything below is derived from the analysis: a repetition construct is
anchored to the step whose text carries the repeat sentence, and that is where
an operator meets the instruction and where the judgement belongs.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from types import SimpleNamespace

from voice_workflow_agent import experiment_protocol as domain
from voice_workflow_agent.curated_protocol import (
    CuratedProtocolAction,
    CuratedProtocolSession,
    build_step_semantic_frame,
    load_curated_protocol_fixture,
    steps_anchoring_a_repetition,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "data/development_protocols/candidate_a_curated_analysis.json"
PROVENANCE = (
    ROOT / "data/development_protocols/candidate_a_curated_analysis.provenance.json"
)
IN_GEL = ROOT / "data/runtime/candidate-a-source/in-gel-digestion.pdf"
HEADSPACE = ROOT / "usingdynamicheadspacecollections.pdf"
INTRACELLULAR = ROOT / "intracellularmetaboliteextraction.pdf"
CURATED_SOURCE = ROOT / "src/voice_workflow_agent/curated_protocol.py"


def _stub_fixture(*constructs) -> SimpleNamespace:
    """Only what the derivation reads: an analysis with constructs on it."""

    return SimpleNamespace(
        draft=SimpleNamespace(protocol=SimpleNamespace(constructs=constructs))
    )


class TheDerivationIsNotALabelListTests(unittest.TestCase):
    """The anchors come from constructs, at whatever labels a source uses."""

    def test_a_repeat_at_any_step_anchors_the_gate_there(self) -> None:
        for anchor in ("step-3", "s-41", "candidate-a-step-07", "0"):
            with self.subTest(anchor=anchor):
                found = steps_anchoring_a_repetition(
                    _stub_fixture(
                        SimpleNamespace(
                            step_id=anchor,
                            repeated_step_ids=("a", "b"),
                            repetition_id="r",
                        )
                    )
                )
                self.assertEqual(found, frozenset({anchor}))

    def test_a_construct_that_names_no_range_is_not_a_repetition(self) -> None:
        """An ambiguity sits on a step too, and anchors nothing by itself."""

        found = steps_anchoring_a_repetition(
            _stub_fixture(
                SimpleNamespace(
                    step_id="step-3",
                    repeated_step_ids=None,
                    start_step_id=None,
                    ambiguity_id="a",
                )
            )
        )
        self.assertEqual(found, frozenset())

    def test_a_document_with_no_repetition_has_no_gate_anywhere(self) -> None:
        self.assertEqual(steps_anchoring_a_repetition(_stub_fixture()), frozenset())

    def test_an_anchorless_repetition_is_dropped_rather_than_guessed(self) -> None:
        """No step to meet the instruction at means no gate, not gate zero."""

        found = steps_anchoring_a_repetition(
            _stub_fixture(
                SimpleNamespace(
                    step_id=None,
                    repeated_step_ids=("a", "b"),
                    repetition_id="r",
                )
            )
        )
        self.assertEqual(found, frozenset())

    def test_no_source_label_set_decides_the_gate_any_more(self) -> None:
        """The regression this file exists for, read off the module itself.

        Three sites held in-gel's labels. The two remaining ``step_label``
        comparisons in ``_observation_predicate`` are a different thing and
        are meant to stay: they choose between two families of *endpoint
        phrases* -- "transparent/destained" against "white/dehydrated" -- and
        those phrases are in-gel's own wording. Letting either family answer
        for the other step would accept a completion criterion the document
        does not state there. Where the wording is unrecognised the turn falls
        through to a confirmation the operator answers instead, and that path
        reads no labels at all.
        """

        source = CURATED_SOURCE.read_text(encoding="utf-8")
        offenders = [
            line.strip()
            for line in source.splitlines()
            if re.search(r'in \{"(?:7|9|20)"(?:,\s*"(?:7|9|20)")*\}', line)
            and "step_label" not in line
        ]
        self.assertEqual(offenders, [])


class TheGateStandsWhereInGelSaysSoTests(unittest.TestCase):
    """The same three steps as before, for a reason rather than by name."""

    @classmethod
    def setUpClass(cls) -> None:
        if not (FIXTURE.is_file() and PROVENANCE.is_file() and IN_GEL.is_file()):
            raise unittest.SkipTest("The curated in-gel fixture is not present.")
        cls.fixture = load_curated_protocol_fixture(FIXTURE, PROVENANCE, IN_GEL)

    def _session_at(self, label: str) -> CuratedProtocolSession:
        session = CuratedProtocolSession(self.fixture)
        session.active = True
        session._workflow_status = "active"
        session.current_index = next(
            index
            for index, step in enumerate(self.fixture.steps)
            if step.source_label == label
        )
        return session

    def test_the_derived_anchors_are_the_labels_that_used_to_be_written_in(self):
        labels = {
            step.step_id: step.source_label for step in self.fixture.steps
        }
        derived = steps_anchoring_a_repetition(self.fixture)
        self.assertEqual(
            sorted(labels[step_id] for step_id in derived), ["20", "7", "9"]
        )

    def test_exactly_those_steps_carry_an_endpoint_predicate(self) -> None:
        with_predicate = [
            step.source_label
            for index, step in enumerate(self.fixture.steps)
            if build_step_semantic_frame(
                self.fixture, index
            ).observation_predicate_id
        ]
        self.assertEqual(with_predicate, ["7", "9", "20"])

    def test_the_gate_stands_at_each_and_releases_only_on_a_report(self) -> None:
        for label in ("7", "9", "20"):
            with self.subTest(label=label):
                session = self._session_at(label)
                index = session.current_index
                self.assertTrue(session.endpoint_observation_outstanding(index))
                self.assertFalse(
                    session.endpoint_observation_outstanding(index, "positive")
                )

    def test_a_step_the_source_states_no_repeat_at_has_no_gate(self) -> None:
        for label in ("1", "3", "12", "16", "25"):
            with self.subTest(label=label):
                session = self._session_at(label)
                self.assertFalse(
                    session.endpoint_observation_outstanding(
                        session.current_index
                    )
                )

    def test_the_question_quotes_the_source_rather_than_a_written_endpoint(self):
        """Every anchor is asked about its own endpoint, not step 7's or 9's.

        The two hand-written questions were selected by label: step 7 got the
        destaining one and *everything else* got the dehydration one. On a
        source with no gel that second question states an endpoint the
        document does not have.
        """

        for label in ("7", "9", "20"):
            with self.subTest(label=label):
                session = self._session_at(label)
                ask = session.plan(
                    "현재 단계를 완료했어요", turn_id=1, language="ko",
                    configuration_id=7, generation=11,
                )
                self.assertEqual(
                    ask.action, CuratedProtocolAction.CLARIFY_COMPLETION
                )
                self.assertEqual(
                    ask.intent_kind, "observation_confirmation_required"
                )
                interval = session.repetition_anchored_at(
                    self.fixture.steps[session.current_index].step_id
                )
                quoted = " ".join(str(interval["source_text"]).split())
                self.assertIn(quoted, ask.speech_text)
                self.assertIn("관찰 결과", ask.speech_text)

    def test_the_negative_reply_quotes_the_source_instead_of_a_phrase(self) -> None:
        """The range and the endpoint are the document's, not this module's."""

        expected = {
            "7": ("2–7", "Repeat steps 2-7 until the gel band is fully destained"),
            "9": ("8–9", "repeat steps 8-9 until fully dehydrated"),
            "20": ("17–18", "repeat steps 17-18 until fully dehydrated"),
        }
        for label, (span, quoted) in expected.items():
            with self.subTest(label=label):
                session = self._session_at(label)
                session.plan(
                    "현재 단계를 완료했어요", turn_id=1, language="ko",
                    configuration_id=7, generation=11,
                )
                declined = session.plan(
                    "아니요", turn_id=2, language="ko",
                    configuration_id=7, generation=11,
                )
                self.assertFalse(declined.state_changed)
                self.assertIn(f"{span}단계 반복", declined.display_text)
                self.assertIn(quoted, session.repetition_anchored_at(
                    self.fixture.steps[session.current_index].step_id
                )["source_text"].replace("\n", " "))


class TheReleaseIsWrittenDownTests(unittest.TestCase):
    """What the record holds, and what it deliberately does not."""

    @classmethod
    def setUpClass(cls) -> None:
        if not (FIXTURE.is_file() and PROVENANCE.is_file() and IN_GEL.is_file()):
            raise unittest.SkipTest("The curated in-gel fixture is not present.")
        cls.fixture = load_curated_protocol_fixture(FIXTURE, PROVENANCE, IN_GEL)

    def _released(self, label: str, utterance: str) -> CuratedProtocolSession:
        session = CuratedProtocolSession(self.fixture)
        session.active = True
        session._workflow_status = "active"
        session.current_index = next(
            index
            for index, step in enumerate(self.fixture.steps)
            if step.source_label == label
        )
        session.plan(
            "현재 단계를 완료했어요", turn_id=1, language="ko",
            configuration_id=7, generation=11,
        )
        session.plan(
            utterance, turn_id=2, language="ko",
            configuration_id=7, generation=11,
            actor_principal_id="principal-operator-a",
            actor_role="researcher",
        )
        return session

    def test_the_record_names_the_protocol_step_words_time_and_actor(self):
        session = self._released("7", "젤이 완전히 탈색되어 투명해요")
        records = session.endpoint_observations()
        self.assertEqual(list(records), ["candidate-a-step-07"])
        record = records["candidate-a-step-07"]
        self.assertEqual(
            sorted(record),
            [
                "declared_at",
                "declared_by_principal_id",
                "declared_by_role",
                "observation_predicate",
                "protocol_id",
                "protocol_revision_id",
                "step_id",
                "step_label",
                "utterance",
            ],
        )
        self.assertEqual(
            record["protocol_id"], self.fixture.draft.protocol.protocol_id
        )
        self.assertEqual(
            record["protocol_revision_id"], self.fixture.revision_id
        )
        self.assertEqual(record["step_label"], "7")
        self.assertEqual(record["observation_predicate"], "positive")
        # The words, as spoken.
        self.assertEqual(record["utterance"], "젤이 완전히 탈색되어 투명해요")
        self.assertEqual(
            record["declared_by_principal_id"], "principal-operator-a"
        )
        self.assertEqual(record["declared_by_role"], "researcher")
        self.assertTrue(record["declared_at"].endswith("+00:00"))

    def test_no_round_count_is_recorded_or_inferred(self) -> None:
        """The source says "until", never how many times."""

        session = self._released("7", "젤이 완전히 탈색되어 투명해요")
        record = session.endpoint_observations()["candidate-a-step-07"]
        self.assertEqual(
            [key for key in record if "count" in key or "round" in key], []
        )
        self.assertNotIn("repetition_id", record)

    def test_an_unnamed_actor_is_left_unnamed_rather_than_invented(self) -> None:
        session = self._released("9", "젤이 흰색으로 변했고 탈수됐어요")
        # The default path passes no principal: a host with no workspace has
        # none to pass, and "local" is an identity nobody holds.
        plain = CuratedProtocolSession(self.fixture)
        plain.active = True
        plain._workflow_status = "active"
        plain.current_index = session.current_index - 1
        record = plain.record_endpoint_observation(
            plain.current_index,
            predicate="positive",
            utterance="흰색으로 변했어요",
            actor_principal_id=None,
            actor_role="voice_operator",
        )
        self.assertIsNone(record["declared_by_principal_id"])
        self.assertEqual(record["declared_by_role"], "voice_operator")

    def test_only_a_reported_endpoint_may_be_written(self) -> None:
        session = CuratedProtocolSession(self.fixture)
        for predicate, utterance, actor in (
            ("negative", "아직 투명해요", "researcher"),
            ("positive", "   ", "researcher"),
            ("positive", "투명해요", "  "),
        ):
            with self.subTest(predicate=predicate, utterance=utterance):
                with self.assertRaises(ValueError):
                    session.record_endpoint_observation(
                        6,
                        predicate=predicate,
                        utterance=utterance,
                        actor_principal_id="principal-a",
                        actor_role=actor,
                    )
        with self.assertRaises(ValueError):
            session.record_endpoint_observation(
                999, predicate="positive", utterance="투명해요",
                actor_principal_id="a", actor_role="researcher",
            )

    def test_the_gate_does_not_re_close_behind_the_answer(self) -> None:
        session = self._released("7", "젤이 완전히 탈색되어 투명해요")
        index = next(
            position
            for position, step in enumerate(self.fixture.steps)
            if step.source_label == "7"
        )
        self.assertFalse(session.endpoint_observation_outstanding(index))

    def test_a_new_run_owes_its_own_observation(self) -> None:
        session = self._released("7", "젤이 완전히 탈색되어 투명해요")
        session.reset()
        self.assertEqual(session.endpoint_observations(), {})

    def test_a_rolled_back_turn_takes_the_record_with_it(self) -> None:
        """The server restores this checkpoint when persistence fails."""

        session = CuratedProtocolSession(self.fixture)
        session.active = True
        session._workflow_status = "active"
        session.current_index = next(
            index
            for index, step in enumerate(self.fixture.steps)
            if step.source_label == "7"
        )
        checkpoint = session._checkpoint()
        session.plan(
            "현재 단계를 완료했어요", turn_id=1, language="ko",
            configuration_id=7, generation=11,
        )
        session.plan(
            "젤이 완전히 탈색되어 투명해요", turn_id=2, language="ko",
            configuration_id=7, generation=11,
        )
        self.assertTrue(session.endpoint_observations())
        session._restore(checkpoint)
        self.assertEqual(session.endpoint_observations(), {})
        self.assertTrue(
            session.endpoint_observation_outstanding(session.current_index)
        )

    def test_the_record_clears_no_readiness_gate(self) -> None:
        """An experiment-session fact, like every other one on this session."""

        before = domain.assess_readiness(self.fixture.draft.protocol)
        session = self._released("7", "젤이 완전히 탈색되어 투명해요")
        after = domain.assess_readiness(session.fixture.draft.protocol)
        self.assertEqual(before.reason_codes, after.reason_codes)


class TheGateCountIsADocumentPropertyTests(unittest.TestCase):
    """How many gates a source has is read out of the source."""

    def _stated_repeats(self, path: Path) -> int:
        from voice_workflow_agent.experiment_protocol_pdf import (
            extract_protocol_pdf,
        )
        from voice_workflow_agent.protocol_claim_analysis import (
            explicit_repeat_instructions,
        )

        extraction = extract_protocol_pdf(path)
        return len(
            explicit_repeat_instructions(extraction, source_revision="pdf-1")
        )

    def test_each_local_source_states_its_own_number_of_repeats(self) -> None:
        expected = {IN_GEL: 3, HEADSPACE: 5, INTRACELLULAR: 0}
        checked = 0
        for path, count in expected.items():
            if not path.is_file():
                continue
            with self.subTest(source=path.name):
                self.assertEqual(self._stated_repeats(path), count)
                checked += 1
        if not checked:
            self.skipTest("No local source document is present.")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
