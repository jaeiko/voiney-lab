"""What must stay true if P1 declares REPEAT_UNTIL supported.

Five existing tests assert that the P1 profile does *not* support
repeat-until. Declaring it invalidates their premise, and the premise is not
the same thing as the property each one was protecting. This file pins the
properties first, so the premise can be updated without any of them going
unwatched.

Every test here passes both before and after the declaration. The declared
state is produced honestly, without touching the global constant: readiness is
simply assessed with a policy that supports the feature, which is what
``assess_readiness(protocol, capability_policy=...)`` exists for. Each test
that depends on the declared state asserts it is really in it -- that
``unsupported_repeat_until`` is absent -- before asserting anything else.

The properties, and where they now live:

* A repeat step's last step does not pass without the operator's observation
  -- ``DeclaredStateGateTests``. Previously visible only because readiness
  called the construct unsupported.
* An observation opens the gate only if it reached an experiment record
  -- ``ObservationWithoutARecordTests``.
* Activation needs *every* blocking reason cleared by a person, not a subset
  -- ``TheWallNeedsEveryReasonClearedTests``. The half where the safety gate
  alone is acknowledged is already held by
  ``test_activation_still_refuses_on_reasons_nobody_may_clear``; the half
  where every ambiguity is resolved instead was only held by
  ``test_resolving_the_ambiguities_narrows_the_wall``, whose premise changes,
  so it is pinned here.
* The operator is still told a repeat step is held, in the preview and in the
  completion-criteria answer -- ``TheOperatorIsStillToldTests``. Both notices
  used to be selected by the readiness reason.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from voice_workflow_agent import experiment_protocol as domain
from voice_workflow_agent.curated_protocol import (
    CuratedProtocolAction,
    CuratedProtocolSession,
    load_curated_protocol_fixture,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "data/development_protocols/candidate_a_curated_analysis.json"
PROVENANCE = (
    ROOT / "data/development_protocols/candidate_a_curated_analysis.provenance.json"
)
IN_GEL_PDF = ROOT / "data/runtime/candidate-a-source/in-gel-digestion.pdf"

#: The same profile id. Renaming it would discard every cached claim payload,
#: which is validated against this exact string; the point here is the
#: supported set, not the name.
REPEAT_UNTIL_CAPABLE = domain.CapabilityPolicy(
    "p1-conservative",
    domain.P1_CAPABILITY_POLICY.supported_features
    | {domain.FeatureCode.REPEAT_UNTIL},
)
_UNSUPPORTED = domain.ReadinessReasonCode.UNSUPPORTED_REPEAT_UNTIL.value


def _curated_fixture():
    return load_curated_protocol_fixture(FIXTURE, PROVENANCE, IN_GEL_PDF)


def _declared(fixture):
    """The same fixture, with readiness assessed under the capable policy."""

    return replace(
        fixture,
        draft=replace(
            fixture.draft,
            readiness=domain.assess_readiness(
                fixture.draft.protocol,
                capability_policy=REPEAT_UNTIL_CAPABLE,
            ),
            capability_policy=REPEAT_UNTIL_CAPABLE,
        ),
    )


class DeclaredStateGateTests(unittest.TestCase):
    """The gate stands on the document once the reason is gone."""

    @classmethod
    def setUpClass(cls) -> None:
        if not (FIXTURE.is_file() and PROVENANCE.is_file() and IN_GEL_PDF.is_file()):
            raise unittest.SkipTest("The curated in-gel fixture is not present.")
        cls.fixture = _declared(_curated_fixture())

    def setUp(self) -> None:
        # Assert the premise of every test in this class, not just its effect.
        self.assertNotIn(_UNSUPPORTED, self.fixture.draft.readiness.reason_codes)

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

    def test_the_repeat_step_is_asked_for_its_endpoint_not_advanced(self) -> None:
        """Steps 7 and 9 are the last step of their own repeat range."""

        for label in ("7", "9", "20"):
            with self.subTest(label=label):
                session = self._session_at(label)
                opening = session.current_index
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
                self.assertFalse(ask.state_changed)
                self.assertEqual(session.current_index, opening)

    def test_a_route_around_the_question_is_refused_by_the_gate_itself(self):
        """The backstop, and the reason it is not dead code.

        A stale reply to the endpoint question skips the conversion that turns
        a completion claim into that question. Before the declaration the
        transition was then stopped by ``unsupported_repeat_until``. With the
        reason gone this branch is all that is left between a completion claim
        and an advance, so it is asserted directly.
        """

        for label in ("7", "9", "20"):
            with self.subTest(label=label):
                session = self._session_at(label)
                opening = session.current_index
                session.plan(
                    "현재 단계를 완료했어요", turn_id=1, language="ko",
                    configuration_id=7, generation=11,
                )
                refused = session.plan(
                    "현재 단계를 완료했어요", turn_id=3, language="ko",
                    configuration_id=7, generation=11,
                )
                self.assertFalse(refused.state_changed)
                self.assertEqual(session.current_index, opening)
                self.assertEqual(
                    session._block_reason, "endpoint_observation_not_reported"
                )
                # The document's own sentence, not a phrase chosen by label.
                interval = session.repetition_anchored_at(
                    self.fixture.steps[opening].step_id
                )
                self.assertIn(
                    " ".join(str(interval["source_text"]).split()),
                    refused.display_text,
                )

    def test_a_reported_endpoint_releases_that_step_and_no_other(self) -> None:
        session = self._session_at("7")
        opening = session.current_index
        session.plan(
            "현재 단계를 완료했어요", turn_id=1, language="ko",
            configuration_id=7, generation=11,
        )
        accepted = session.plan(
            "젤이 완전히 탈색되어 투명해요", turn_id=2, language="ko",
            configuration_id=7, generation=11,
        )
        self.assertTrue(accepted.state_changed)
        self.assertEqual(session.current_index, opening + 1)
        self.assertEqual(list(session.endpoint_observations()), ["candidate-a-step-07"])
        # Step 9's gate is untouched by step 7's answer.
        ninth = next(
            index
            for index, step in enumerate(self.fixture.steps)
            if step.source_label == "9"
        )
        self.assertTrue(session.endpoint_observation_outstanding(ninth))

    def test_the_gate_sits_at_the_step_that_carries_the_sentence(self) -> None:
        """Range 17-18, sentence in step 20's block: the gate is at 20.

        Two of in-gel's three repeats end at their own anchor, so "the last
        step of the range" and "the step the gate stands at" coincide. The
        third does not: page 8 writes "repeat steps 17-18" inside step 20.
        Step 18 therefore has no gate, deliberately -- an operator running
        17, 18, 19, 20 has not yet been shown the instruction, and meets it at
        20, which is where they are held. Asserted so the choice stays visible
        rather than looking like an oversight.
        """

        eighteen = self._session_at("18")
        self.assertFalse(
            eighteen.endpoint_observation_outstanding(eighteen.current_index)
        )
        twenty = self._session_at("20")
        self.assertTrue(
            twenty.endpoint_observation_outstanding(twenty.current_index)
        )
        interval = twenty.repetition_anchored_at(
            self.fixture.steps[twenty.current_index].step_id
        )
        labels = {
            step.step_id: step.source_label for step in self.fixture.steps
        }
        self.assertEqual(
            [labels[step_id] for step_id in interval["repeated_step_ids"]],
            ["17", "18"],
        )


class TheOperatorIsStillToldTests(unittest.TestCase):
    """Two notices that used to be selected by the readiness reason."""

    @classmethod
    def setUpClass(cls) -> None:
        if not (FIXTURE.is_file() and PROVENANCE.is_file() and IN_GEL_PDF.is_file()):
            raise unittest.SkipTest("The curated in-gel fixture is not present.")
        cls.fixture = _declared(_curated_fixture())

    def setUp(self) -> None:
        self.assertNotIn(_UNSUPPORTED, self.fixture.draft.readiness.reason_codes)

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

    def test_the_next_step_preview_still_says_this_step_is_held(self) -> None:
        for label in ("7", "9"):
            with self.subTest(label=label):
                plan = self._session_at(label).plan(
                    "다음 단계 미리 알려 줘", turn_id=1, language="ko",
                    configuration_id=7, generation=11,
                )
                self.assertEqual(
                    plan.action, CuratedProtocolAction.NEXT_INFORMATION
                )
                self.assertFalse(plan.state_changed)
                self.assertIn("진입 승인이 아닙니다", plan.display_text)

    def test_the_completion_criteria_answer_still_refuses_completion(self) -> None:
        for label in ("7", "9"):
            with self.subTest(label=label):
                session = self._session_at(label)
                plan = session.plan(
                    "이 단계의 완료 조건은 뭐야?", turn_id=1, language="ko",
                    configuration_id=7, generation=11,
                )
                self.assertEqual(
                    plan.action, CuratedProtocolAction.COMPLETION_CRITERIA
                )
                self.assertFalse(plan.state_changed)
                self.assertIn("완료 처리하지 마세요", plan.display_text)
                self.assertIn("관찰 결과가 충족될 때까지", plan.display_text)
                interval = session.repetition_anchored_at(
                    self.fixture.steps[session.current_index].step_id
                )
                self.assertIn(
                    " ".join(str(interval["source_text"]).split()),
                    plan.display_text,
                )

    def test_a_step_with_no_repeat_keeps_its_ordinary_answer(self) -> None:
        plan = self._session_at("3").plan(
            "이 단계의 완료 조건은 뭐야?", turn_id=1, language="ko",
            configuration_id=7, generation=11,
        )
        self.assertNotIn("완료 처리하지 마세요", plan.display_text)


class ObservationWithoutARecordTests(unittest.TestCase):
    """A release the experiment record did not receive is not a release."""

    @classmethod
    def setUpClass(cls) -> None:
        if not (FIXTURE.is_file() and PROVENANCE.is_file() and IN_GEL_PDF.is_file()):
            raise unittest.SkipTest("The curated in-gel fixture is not present.")
        cls.fixture = _curated_fixture()

    def test_a_release_with_no_experiment_record_is_refused_and_rolled_back(self):
        from voice_workflow_agent.experiment_reports import ExperimentReportStore
        from voice_workflow_agent.language import Transcription
        from voice_workflow_agent.server import ListenerSession, run_turn
        from voice_workflow_agent.tools import ToolContext
        from voice_workflow_agent.vad import TurnState

        from tests.test_curated_protocol_cascade import Socket

        async def immediate(function, *args, **kwargs):
            return function(*args, **kwargs)

        cases = (
            ("7", "젤이 완전히 탈색되어 투명해요"),
            ("9", "젤이 흰색으로 변했고 탈수됐어요"),
            ("20", "젤이 흰색으로 변했고 탈수됐어요"),
        )
        for label, observation in cases:
            index = next(
                position
                for position, step in enumerate(self.fixture.steps)
                if step.source_label == label
            )
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                workflow = CuratedProtocolSession(self.fixture)
                workflow.active = True
                workflow.current_index = index
                session = ListenerSession(
                    tool_context=ToolContext(
                        Path("/unused/offline-catalog"), None, "ko", "test_only"
                    ),
                    curated_protocol_session=workflow,
                )
                session.active = True
                session.accept_configuration(
                    41, "cascade", "ko", self.fixture.protocol_id
                )
                session.experiment_report_store = ExperimentReportStore(
                    Path(directory) / "reports.sqlite"
                )
                # No experiment-session record is open. That is the whole case:
                # the observation has nowhere durable to go.
                self.assertIsNone(session.experiment_state_version)
                workflow.plan(
                    "현재 단계를 완료했어요", turn_id=1, language="ko",
                    configuration_id=41, generation=session.generation,
                )
                session.active_turn_id = 2
                session.turn_generations[2] = session.generation
                session.detector.state = TurnState.PROCESSING
                socket = Socket()
                with patch(
                    "voice_workflow_agent.server.transcribe",
                    return_value=Transcription(observation, "ko"),
                ), patch(
                    "voice_workflow_agent.server.synthesize",
                    return_value=b"\0\0",
                ) as tts, patch(
                    "voice_workflow_agent.server.asyncio.to_thread",
                    side_effect=immediate,
                ):
                    asyncio.run(run_turn(socket, session, b"\0\0", 2, 1))
                self.assertEqual(workflow.current_index, index)
                self.assertEqual(workflow.endpoint_observations(), {})
                self.assertIn(
                    "실험 세션 기록이 활성화되지 않아", tts.call_args.args[0]
                )
                self.assertTrue(
                    workflow.endpoint_observation_outstanding(index)
                )


class TheWallNeedsEveryReasonClearedTests(unittest.TestCase):
    """Activation needs every blocking reason cleared, not a subset.

    The companion half -- the safety gate acknowledged and the ambiguities
    left alone -- is held by
    ``test_pdf_to_session_walkthrough.test_activation_still_refuses_on_reasons_nobody_may_clear``.
    """

    def setUp(self) -> None:
        import sys

        sys.path.insert(0, str(ROOT / "scripts"))
        from tests.test_pdf_to_session_walkthrough import IN_GEL, _pipeline

        if not IN_GEL.is_file():
            self.skipTest(f"{IN_GEL} is not present.")
        from voice_workflow_agent.experiment_protocol_store import (
            ProtocolPersistenceSettings,
            initialize_protocol_store,
        )
        from voice_workflow_agent.protocol_catalog import ProtocolCatalog

        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        _, _, _, self.draft = _pipeline()
        self.store = initialize_protocol_store(
            ProtocolPersistenceSettings(True, Path(self._temp.name) / "catalog")
        )
        self.addCleanup(self.store.close)
        self.catalog = ProtocolCatalog(self.store)
        registration = self.catalog.register(
            IN_GEL, source_filename=IN_GEL.name, media_type="application/pdf"
        )
        self.protocol_id = registration.entry.protocol_id
        protocol = domain.validate_protocol(
            replace(self.draft.protocol, protocol_id=self.protocol_id)
        )
        # Stored under the capable policy, so the only reasons left are the two
        # a person can clear. Nothing global is patched.
        readiness = domain.assess_readiness(
            protocol, capability_policy=REPEAT_UNTIL_CAPABLE
        )
        self.assertNotIn(_UNSUPPORTED, readiness.reason_codes)
        self.store.append_analysis_revision(
            self.protocol_id, 1, "analysis-declaration-properties",
            protocol, readiness, REPEAT_UNTIL_CAPABLE.profile_id,
        )
        self.revision_id = "pdf-1-analysis-1"

    def test_resolving_every_ambiguity_alone_does_not_clear_the_wall(self) -> None:
        from voice_workflow_agent.protocol_catalog import (
            AMBIGUITY_SINGLE_AUTHORITATIVE,
            ProtocolCatalogUnavailableError,
        )

        ambiguities = [
            construct
            for construct in self.draft.protocol.constructs
            if isinstance(construct, domain.SourceAmbiguity)
        ]
        self.assertEqual(len(ambiguities), 4)
        for ambiguity in ambiguities:
            self.catalog.resolve_ambiguity(
                self.protocol_id,
                self.revision_id,
                ambiguity_id=ambiguity.ambiguity_id,
                decision=AMBIGUITY_SINGLE_AUTHORITATIVE,
                evidence_segment_ids=ambiguity.evidence.evidence_segment_ids,
                actor_principal_id="reviewer@example.org",
                actor_role="reviewer",
                comment="Prose interval and timer literal agree.",
            )
        analysis = self.store.get_analysis_revision(self.protocol_id, 1, 1)
        self.assertTrue(
            self.catalog._every_ambiguity_resolved(
                self.protocol_id, 1, analysis
            )
        )
        # The safety gate is untouched, so the wall stands.
        self.assertFalse(
            self.catalog._readiness_gates_cleared(self.protocol_id, 1, analysis)
        )
        with self.assertRaises(ProtocolCatalogUnavailableError):
            self.catalog.activate_development(self.protocol_id)
        with self.assertRaises(ProtocolCatalogUnavailableError):
            self.catalog.load_executable_fixture(self.protocol_id)

    def test_the_two_remaining_reasons_are_both_reviewer_clearable(self) -> None:
        from voice_workflow_agent.protocol_catalog import ProtocolCatalog

        analysis = self.store.get_analysis_revision(self.protocol_id, 1, 1)
        remaining = sorted(set(analysis.readiness.reason_codes))
        self.assertEqual(
            remaining,
            [
                domain.ReadinessReasonCode.NO_DECLARED_SAFETY_WARNINGS.value,
                domain.ReadinessReasonCode.UNRESOLVED_AMBIGUITY.value,
            ],
        )
        for code in remaining:
            with self.subTest(code=code):
                self.assertIn(code, set(ProtocolCatalog._BLOCKER_RESOLUTION))


class TheAnalysisIdentitySeesTheAnalysisTests(unittest.TestCase):
    """A catalog already materialized under the old policy must survive.

    The declaration changes the fixture's readiness without changing one byte
    of the fixture, and the analysis id named only the fixture. The store then
    found an id it already held whose payload had changed and refused --
    at server start, from ``scripts/run_candidate_a.sh``, on any catalog that
    had already materialized this fixture. The pilot catalog is one: it holds
    ``curated-69517f0f...`` with payload ``47df9633...`` while the declared
    policy produces ``824e9b54...``.
    """

    @classmethod
    def setUpClass(cls) -> None:
        if not (FIXTURE.is_file() and PROVENANCE.is_file() and IN_GEL_PDF.is_file()):
            raise unittest.SkipTest("The curated in-gel fixture is not present.")
        cls.fixture = _curated_fixture()

    def _as_of(self, policy):
        return replace(
            self.fixture,
            draft=replace(
                self.fixture.draft,
                readiness=domain.assess_readiness(
                    self.fixture.draft.protocol, capability_policy=policy
                ),
                capability_policy=policy,
            ),
        )

    def _catalog(self, directory):
        from voice_workflow_agent.experiment_protocol_store import (
            ProtocolPersistenceSettings,
            initialize_protocol_store,
        )
        from voice_workflow_agent.protocol_catalog import ProtocolCatalog

        store = initialize_protocol_store(
            ProtocolPersistenceSettings(True, Path(directory) / "catalog")
        )
        self.addCleanup(store.close)
        return ProtocolCatalog(store), store

    def test_a_changed_analysis_is_appended_rather_than_refused(self) -> None:
        before = self._as_of(
            domain.CapabilityPolicy(
                "p1-conservative",
                domain.P1_CAPABILITY_POLICY.supported_features
                - {domain.FeatureCode.REPEAT_UNTIL},
            )
        )
        after = self._as_of(domain.P1_CAPABILITY_POLICY)
        self.assertIn(_UNSUPPORTED, before.draft.readiness.reason_codes)
        self.assertEqual(before.fixture_sha256, after.fixture_sha256)
        self.assertEqual(before.revision_id, after.revision_id)

        with tempfile.TemporaryDirectory() as directory:
            catalog, store = self._catalog(directory)
            catalog.bootstrap_development_fixture(before)
            self.assertTrue(catalog.development_fixture_is_materialized(before))
            self.assertFalse(catalog.development_fixture_is_materialized(after))

            # This is what used to raise DuplicateProtocolIdentifierError.
            catalog.bootstrap_development_fixture(after)
            self.assertTrue(catalog.development_fixture_is_materialized(after))

            revisions = store.list_protocol_revisions(after.protocol_id)
            self.assertEqual(len(revisions), 1)
            # Append-only: the earlier analysis is still there.
            first = store.get_analysis_revision(after.protocol_id, 1, 1)
            second = store.get_analysis_revision(after.protocol_id, 1, 2)
            self.assertIn(_UNSUPPORTED, first.readiness.reason_codes)
            self.assertNotIn(_UNSUPPORTED, second.readiness.reason_codes)
            self.assertNotEqual(first.analysis_id, second.analysis_id)

    def test_a_finding_on_the_earlier_analysis_does_not_clear_the_later_one(self):
        """Findings belong to the analysis they were recorded against.

        The same trap in a second shape: a reviewer who cleared this fixture
        before the declaration has cleared the analysis that existed then, and
        the screen must ask again rather than treat the old finding as
        standing.
        """

        from voice_workflow_agent.protocol_catalog import (
            ProtocolCatalogUnavailableError,
        )

        before = self._as_of(
            domain.CapabilityPolicy(
                "p1-conservative",
                domain.P1_CAPABILITY_POLICY.supported_features
                - {domain.FeatureCode.REPEAT_UNTIL},
            )
        )
        after = self._as_of(domain.P1_CAPABILITY_POLICY)
        with tempfile.TemporaryDirectory() as directory:
            catalog, store = self._catalog(directory)
            catalog.bootstrap_development_fixture(before)
            catalog.acknowledge_readiness_gate(
                before.protocol_id,
                "pdf-1-analysis-1",
                reason_code=(
                    domain.ReadinessReasonCode
                    .NO_DECLARED_SAFETY_WARNINGS.value
                ),
                actor_principal_id="reviewer@example.org",
                actor_role="reviewer",
            )
            catalog.bootstrap_development_fixture(after)
            later = store.get_analysis_revision(after.protocol_id, 1, 2)
            self.assertFalse(
                catalog._readiness_gates_cleared(after.protocol_id, 1, later)
            )
            with self.assertRaises(ProtocolCatalogUnavailableError):
                catalog.activate_development(after.protocol_id)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
