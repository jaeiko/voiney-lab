"""Product semantics for human-confirmed execution conditions.

These tests describe the intended division of authority:

* the researcher at the bench decides whether a source-defined observation has
  been met, and the server records that answer;
* the server decides, deterministically, what happens next - replay the exact
  source-defined range, or leave the loop;
* a source sentence whose meaning is genuinely unsettled is nobody's bench
  problem: it blocks execution until a reviewer resolves it into a new revision.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from voice_workflow_agent import experiment_protocol as domain
from voice_workflow_agent.curated_protocol import (
    CuratedProtocolSession,
    classify_checkpoint_continuation_reply,
    classify_human_checkpoint_reply,
    load_curated_protocol_fixture,
)
from voice_workflow_agent.experiment_protocol_config import (
    ProtocolPersistenceSettings,
    repeat_confirmation_review_threshold,
)
from voice_workflow_agent.experiment_protocol_store import (
    initialize_protocol_store,
)
from voice_workflow_agent.protocol_catalog import (
    ProtocolCatalog,
    ProtocolResolutionError,
    SharedSecretApprovalPolicy,
)
from voice_workflow_agent.server import _confirm_and_persist_human_checkpoint


REPOSITORY = Path(__file__).resolve().parents[1]
FIXTURE = REPOSITORY / "data/development_protocols/candidate_a_curated_analysis.json"
PROVENANCE = REPOSITORY / (
    "data/development_protocols/candidate_a_curated_analysis.provenance.json"
)
SOURCE_PDF = REPOSITORY / "data/runtime/candidate-a-source/in-gel-digestion.pdf"
AMBIGUITY_ID = "candidate-a-step-20-repeat-range"
RESOLVED_RANGE = (
    "candidate-a-step-17",
    "candidate-a-step-18",
    "candidate-a-step-19",
    "candidate-a-step-20",
)


def load_fixture():
    return load_curated_protocol_fixture(FIXTURE, PROVENANCE, SOURCE_PDF)


class HumanCheckpointReadinessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = load_fixture()
        self.protocol = self.fixture.draft.protocol

    def test_human_observable_repeat_until_does_not_block_readiness(self):
        readiness = domain.assess_readiness(self.protocol)
        self.assertNotIn(
            domain.ReadinessReasonCode.UNSUPPORTED_REPEAT_UNTIL.value,
            readiness.reason_codes,
        )
        # Only the genuine source ambiguity is left.
        self.assertEqual(
            readiness.reason_codes,
            (domain.ReadinessReasonCode.UNRESOLVED_AMBIGUITY.value,),
        )

    def test_checkpoints_copy_the_source_range_and_condition_verbatim(self):
        checkpoints = domain.human_confirmation_checkpoints(self.protocol)
        by_gate = {item.gate_step_id: item for item in checkpoints}
        self.assertEqual(
            set(by_gate), {"candidate-a-step-07", "candidate-a-step-09"}
        )
        constructs = {
            item.repetition_id: item
            for item in self.protocol.constructs
            if isinstance(item, domain.RepeatUntil)
        }
        for checkpoint in checkpoints:
            source = constructs[checkpoint.checkpoint_id]
            self.assertEqual(
                checkpoint.condition_source_text, source.condition_source_text
            )
            self.assertEqual(
                checkpoint.repeated_step_ids, source.repeated_step_ids
            )

    def test_a_protocol_whose_only_gate_is_human_confirmed_is_guidance_ready(self):
        resolved = replace(
            self.protocol,
            constructs=tuple(
                replace(
                    item,
                    resolved=True,
                    resolution_source_text="Reviewer confirmed 17-18.",
                )
                if isinstance(item, domain.SourceAmbiguity)
                else item
                for item in self.protocol.constructs
            ),
        )
        readiness = domain.assess_readiness(resolved)
        self.assertEqual(readiness.status, domain.ReadinessStatus.GUIDANCE_READY)
        self.assertTrue(domain.human_confirmation_checkpoints(resolved))


class HumanCheckpointAdmissionTests(unittest.TestCase):
    def test_only_an_explicit_answer_is_admitted(self):
        for transcript in (
            "젤이 완전히 탈색되어 투명해요",
            "다 됐어요",
            "조건이 충족됐어요",
        ):
            with self.subTest(transcript=transcript, expected="met"):
                self.assertEqual(classify_human_checkpoint_reply(transcript), "met")
        for transcript in ("아직 아니야", "아직 색이 남아 있어요", "아니요"):
            with self.subTest(transcript=transcript, expected="not_met"):
                self.assertEqual(
                    classify_human_checkpoint_reply(transcript), "not_met"
                )

    def test_questions_hypotheticals_and_unrelated_reports_are_not_answers(self):
        for transcript in (
            "완전히 투명한가요?",
            "완전히 탈색됐다고 말하면 어떻게 돼?",
            "타이머 시작했어요",
            "다시 말해줘",
        ):
            with self.subTest(transcript=transcript):
                self.assertIsNone(classify_human_checkpoint_reply(transcript))

    def test_a_bare_yes_answers_only_a_question_the_server_asked(self):
        self.assertEqual(classify_human_checkpoint_reply("네"), "met")
        self.assertIsNone(
            classify_human_checkpoint_reply("네", solicited=False)
        )

    def test_continuation_replies_are_separate_and_explicit(self):
        self.assertEqual(
            classify_checkpoint_continuation_reply("계속 진행"), "continue"
        )
        self.assertEqual(
            classify_checkpoint_continuation_reply("잠깐 멈춰"), "pause"
        )
        self.assertEqual(
            classify_checkpoint_continuation_reply("검토 요청할게"),
            "request_review",
        )
        self.assertIsNone(
            classify_checkpoint_continuation_reply("현재 단계 알려줘")
        )


class HumanCheckpointSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = load_fixture()

    def session(self, label: str = "7") -> CuratedProtocolSession:
        session = CuratedProtocolSession(self.fixture)
        session.active = True
        session._workflow_status = "active"
        session.current_index = next(
            index
            for index, step in enumerate(self.fixture.steps)
            if step.source_label == label
        )
        return session

    def label(self, session: CuratedProtocolSession) -> str:
        return self.fixture.steps[session.current_index].source_label

    def test_next_step_questions_preview_without_mutation(self):
        for transcript in ("다음 단계 알려줘", "다음 단계로 알려줘", "다음 단계가 뭐야?"):
            with self.subTest(transcript=transcript):
                session = self.session("2")
                before = (session.current_index, session._revision)
                plan = session.plan(
                    transcript, turn_id=1, language="ko",
                    configuration_id=1, generation=0,
                )
                self.assertEqual(plan.action.value, "next_information")
                self.assertFalse(plan.state_changed)
                self.assertIn("3단계", plan.speech_text)
                self.assertIn("변경하지 않았", plan.speech_text)
                self.assertEqual((session.current_index, session._revision), before)

    def test_current_step_questions_never_mutate(self):
        for transcript in ("이 단계 조건이 뭐야?", "왜 이걸 해야 해?"):
            with self.subTest(transcript=transcript):
                session = self.session("2")
                before = (session.current_index, session._revision)
                plan = session.plan(
                    transcript, turn_id=1, language="ko",
                    configuration_id=1, generation=0,
                )
                self.assertFalse(plan.state_changed)
                self.assertNotEqual(plan.action.value, "next")
                self.assertEqual((session.current_index, session._revision), before)

    def test_permission_to_advance_requires_explicit_completion(self):
        session = self.session("2")
        before = (session.current_index, session._revision)
        plan = session.plan(
            "다음으로 넘어가도 돼", turn_id=1, language="ko",
            configuration_id=1, generation=0,
        )
        self.assertEqual(plan.action.value, "clarify_completion")
        self.assertFalse(plan.state_changed)
        self.assertIn("아직 상태는 변경하지 않았", plan.speech_text)
        self.assertEqual((session.current_index, session._revision), before)

    def test_natural_current_step_completion_forms_advance_once(self):
        utterances = (
            "현재 단계 완료", "완료했어요", "완료됐어요",
            "이 단계 끝났어요", "이거 끝났어", "지금 단계 끝",
        )
        for transcript in utterances:
            with self.subTest(transcript=transcript):
                session = self.session("2")
                plan = session.plan(
                    transcript, turn_id=1, language="ko",
                    configuration_id=1, generation=0,
                )
                self.assertTrue(plan.state_changed)
                self.assertEqual(self.label(session), "3")
                self.assertIn("2단계를 완료로 저장", plan.speech_text)
                replay = session.plan(
                    transcript, turn_id=1, language="ko",
                    configuration_id=1, generation=0,
                )
                self.assertEqual(replay, plan)
                self.assertEqual(self.label(session), "3")

    def test_ambiguous_completion_does_not_mutate(self):
        for transcript in ("그런 것 같아요", "아마 된 것 같기도 해요"):
            with self.subTest(transcript=transcript):
                session = self.session("2")
                before = (session.current_index, session._revision)
                plan = session.plan(
                    transcript, turn_id=1, language="ko",
                    configuration_id=1, generation=0,
                )
                self.assertFalse(plan.state_changed)
                self.assertEqual(plan.action.value, "clarify_completion")
                self.assertIn("상태는 변경하지 않았", plan.speech_text)
                self.assertEqual((session.current_index, session._revision), before)

    def test_off_checkpoint_not_complete_is_safe_and_actionable(self):
        for transcript in ("아직 안 됐어요", "아직이에요"):
            with self.subTest(transcript=transcript):
                session = self.session("2")
                before = (session.current_index, session._revision)
                plan = session.plan(
                    transcript, turn_id=1, language="ko",
                    configuration_id=1, generation=0,
                )
                self.assertFalse(plan.state_changed)
                # Natural bench guidance: say what this step is, say what to do
                # instead, and state plainly that nothing moved.
                self.assertIn("별도의 완료 조건을 확인하는 단계가 아닙니다", plan.speech_text)
                self.assertIn("이 단계 완료", plan.speech_text)
                self.assertIn("현재 단계는", plan.speech_text)
                self.assertIn("그대로입니다", plan.speech_text)
                self.assertEqual((session.current_index, session._revision), before)

    def test_solicited_checkpoint_natural_answers_drive_exact_state_machine(self):
        for transcript in ("완료됐어요", "충족됐어요", "확인했습니다"):
            with self.subTest(transcript=transcript):
                session = self.session("7")
                plan = session.plan(
                    transcript, turn_id=1, language="ko",
                    configuration_id=1, generation=0,
                )
                self.assertTrue(plan.state_changed)
                self.assertEqual(self.label(session), "8")
                self.assertEqual(plan.intent_kind, "human_checkpoint_confirmed")
        for transcript in ("아직 안 됐어요", "아직이에요"):
            with self.subTest(transcript=transcript):
                session = self.session("7")
                plan = session.plan(
                    transcript, turn_id=1, language="ko",
                    configuration_id=1, generation=0,
                )
                self.assertTrue(plan.state_changed)
                self.assertEqual(self.label(session), "2")
                self.assertEqual(
                    plan.intent_kind, "human_checkpoint_repeat_scheduled"
                )

    def test_persistence_failure_restores_exact_checkpoint_state(self):
        session = self.session("7")
        before = session._checkpoint()
        listener = SimpleNamespace()
        with patch(
            "voice_workflow_agent.server._record_human_checkpoint_decision",
            side_effect=RuntimeError("synthetic persistence failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "synthetic persistence failure"):
                asyncio.run(_confirm_and_persist_human_checkpoint(
                    listener, session, "not_met",
                    pre_transition_index=session.current_index,
                ))
        self.assertEqual(session._checkpoint(), before)

    def test_not_met_replays_only_the_source_defined_range(self):
        session = self.session("7")
        before = len(self.fixture.steps)
        outcome = session.confirm_human_checkpoint("not_met")
        self.assertEqual(outcome.status, "repeat_scheduled")
        self.assertTrue(outcome.state_changed)
        self.assertEqual(outcome.iteration, 1)
        self.assertEqual(self.label(session), "2")
        self.assertEqual(
            outcome.repeated_step_ids[0], "candidate-a-step-02"
        )
        # No step was invented and the source inventory is unchanged.
        self.assertEqual(len(self.fixture.steps), before)
        self.assertEqual(
            outcome.condition_source_text,
            self.fixture.human_checkpoints[
                "candidate-a-step-07"
            ].condition_source_text,
        )

    def test_met_leaves_the_loop_and_advances_one_step(self):
        session = self.session("9")
        outcome = session.confirm_human_checkpoint("met")
        self.assertEqual(outcome.status, "advanced")
        self.assertTrue(outcome.state_changed)
        self.assertEqual(self.label(session), "10")
        self.assertEqual(session.checkpoint_iteration(outcome.checkpoint_id), 0)

    def test_an_unrecognised_answer_changes_nothing(self):
        session = self.session("7")
        opening = session.state()
        outcome = session.confirm_human_checkpoint("maybe")
        self.assertEqual(outcome.status, "clarification_required")
        self.assertFalse(outcome.state_changed)
        self.assertEqual(session.state(), opening)

    def test_an_answer_away_from_a_checkpoint_changes_nothing(self):
        session = self.session("3")
        opening = session.state()
        outcome = session.confirm_human_checkpoint("met")
        self.assertEqual(outcome.status, "not_at_checkpoint")
        self.assertFalse(outcome.state_changed)
        self.assertEqual(session.state(), opening)

    def test_repetition_check_in_is_operational_and_never_a_maximum(self):
        session = self.session("7")
        threshold = repeat_confirmation_review_threshold()
        self.assertEqual(session.repetition_review_threshold, threshold)
        for _ in range(threshold - 1):
            session.confirm_human_checkpoint("not_met")
            session.current_index = next(
                index
                for index, step in enumerate(self.fixture.steps)
                if step.source_label == "7"
            )
        held = session.confirm_human_checkpoint("not_met")
        self.assertEqual(held.status, "continuation_confirmation_required")
        self.assertFalse(held.state_changed)
        self.assertTrue(held.repetition_limit_reached)
        self.assertEqual(self.label(session), "7")
        self.assertEqual(
            session.pending_checkpoint_continuation, held.checkpoint_id
        )
        # The researcher, not the server, decides whether to keep going.
        resumed = session.confirm_human_checkpoint("continue")
        self.assertEqual(resumed.status, "repeat_scheduled")
        self.assertEqual(self.label(session), "2")
        self.assertIsNone(session.pending_checkpoint_continuation)

    def test_review_request_holds_the_step_without_completing_it(self):
        session = self.session("7")
        outcome = session.confirm_human_checkpoint("request_review")
        self.assertEqual(outcome.status, "review_requested")
        self.assertEqual(self.label(session), "7")
        self.assertEqual(
            session.state()["block_reason"],
            "human_checkpoint_review_requested",
        )

    def test_state_projection_quotes_the_source_and_names_the_authority(self):
        session = self.session("7")
        checkpoint = session.state()["human_checkpoint"]
        self.assertEqual(checkpoint["gate_step_label"], "7")
        self.assertEqual(checkpoint["repeated_step_labels"][0], "2")
        self.assertEqual(checkpoint["authority"], "researcher_observation")
        self.assertIn("destained", checkpoint["condition_source_text"])
        self.assertEqual(
            session.state(spoken_summary=None)["human_checkpoint"][
                "confirmed_repetitions"
            ],
            0,
        )

    def test_recovery_accepts_a_step_completed_more_than_once(self):
        session = self.session("7")
        session.confirm_human_checkpoint("not_met")
        completed = tuple(
            step.step_id for step in self.fixture.steps[:7]
        )
        session.restore_experiment_progress(
            current_step_id="candidate-a-step-07",
            completed_step_ids=completed,
        )
        self.assertEqual(self.label(session), "7")


class ReviewerResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = load_fixture()
        self.temp = tempfile.TemporaryDirectory()
        self.settings = ProtocolPersistenceSettings(
            True, Path(self.temp.name) / "catalog"
        )
        self.store = initialize_protocol_store(self.settings)
        self.catalog = ProtocolCatalog(self.store)
        self.catalog.bootstrap_development_fixture(self.fixture)
        self.protocol_id = self.fixture.protocol_id
        self.policy = SharedSecretApprovalPolicy("tenant-rbac-authorized")

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def resolve(self, **overrides):
        payload = {
            "ambiguity_id": AMBIGUITY_ID,
            "interpretation": "원문의 ‘1718’은 17–18단계를 뜻합니다.",
            "rationale": "8페이지 원문과 파싱된 17·18단계를 대조해 확인했습니다.",
            "actor_principal_id": "reviewer-a",
            "actor_role": "reviewer",
            "repeated_step_ids": RESOLVED_RANGE,
        }
        payload.update(overrides)
        return self.catalog.resolve_source_ambiguity(self.protocol_id, **payload)

    def test_review_extracts_the_pdf_only_twice(self):
        """Entry projection and source review each extract once; no hidden repeats."""
        from voice_workflow_agent import protocol_catalog as catalog_module

        real_extract = catalog_module.extract_protocol_pdf
        calls = []

        def counted_extract(*args, **kwargs):
            calls.append(args[0])
            return real_extract(*args, **kwargs)

        with patch.object(
            catalog_module, "extract_protocol_pdf", side_effect=counted_extract
        ):
            self.catalog.review(self.protocol_id)
        self.assertEqual(len(calls), 2)

    def test_unresolved_ambiguity_is_the_only_reviewer_blocker(self):
        review = self.catalog.review(self.protocol_id)
        readiness = review["execution_readiness"]
        self.assertEqual(readiness["state"], "needs_clarification")
        self.assertFalse(readiness["can_approve_for_execution"])
        self.assertEqual(len(review["needs_resolution"]), 1)
        self.assertEqual(review["needs_resolution"][0]["issue_id"], AMBIGUITY_ID)
        self.assertEqual(review["needs_resolution"][0]["source_page_number"], 8)
        # Human checkpoints are shown, but not as blockers.
        self.assertEqual(len(review["human_checkpoints"]), 2)
        self.assertTrue(
            all(
                item["blocks_execution"] is False
                for item in review["human_checkpoints"]
            )
        )
        self.assertNotIn("approve_for_execution", review["reviewer_actions"])
        self.assertIn("resolve_source_interpretation", review["reviewer_actions"])

    def test_resolution_creates_a_traceable_revision_and_recalculates_readiness(self):
        base = self.catalog.get_entry(self.protocol_id)
        entry = self.resolve()

        self.assertNotEqual(entry.revision_id, base.revision_id)
        self.assertEqual(entry.readiness_status, "guidance_ready")
        self.assertFalse(entry.available_for_execution)

        # The original analysis revision is still there, unchanged.
        analyses = self.store.list_analysis_revisions(self.protocol_id, 1)
        self.assertEqual(len(analyses), 2)
        self.assertEqual(analyses[0].protocol, self.fixture.draft.protocol)

        # The source PDF identity never moved.
        self.assertEqual(
            analyses[1].protocol.metadata.file_checksum,
            self.fixture.source_pdf_sha256,
        )

        clarifications = self.store.list_clarifications(self.protocol_id)
        self.assertEqual(len(clarifications), 1)
        self.assertEqual(clarifications[0].researcher, "reviewer-a")
        self.assertEqual(clarifications[0].related_step_id, "candidate-a-step-20")

        events = [
            event
            for event in self.store.list_events(self.protocol_id)
            if event.event_type == "protocol_source_ambiguity_resolved"
        ]
        self.assertEqual(len(events), 1)
        payload = events[0].payload
        self.assertEqual(payload["actor_principal_id"], "reviewer-a")
        self.assertEqual(payload["actor_role"], "reviewer")
        self.assertEqual(payload["base_analysis_revision_number"], 1)
        self.assertEqual(payload["source_page_number"], 8)
        self.assertTrue(events[0].recorded_at)

    def test_the_resolved_range_becomes_a_human_checkpoint(self):
        self.resolve()
        review = self.catalog.review(self.protocol_id)
        gates = {
            item["gate_step_label"] for item in review["human_checkpoints"]
        }
        self.assertEqual(gates, {"7", "9", "20"})
        resolved = next(
            item
            for item in review["human_checkpoints"]
            if item["gate_step_label"] == "20"
        )
        # The condition is the source sentence, not the reviewer's paraphrase.
        self.assertIn(
            "still transparent then repeat steps",
            resolved["condition_source_text"],
        )
        self.assertEqual(
            resolved["condition_source_text"],
            next(
                item["source_text"]
                for item in self.catalog.review(self.protocol_id)[
                    "constructs"
                ]
                if item.get("ambiguity_id") == AMBIGUITY_ID
            ),
        )
        self.assertEqual(
            resolved["repeated_step_labels"], ["17", "18", "19", "20"]
        )

    def test_a_range_the_server_cannot_execute_is_refused(self):
        with self.assertRaises(ProtocolResolutionError):
            self.resolve(
                repeated_step_ids=(
                    "candidate-a-step-17",
                    "candidate-a-step-19",
                )
            )
        self.assertEqual(
            len(self.store.list_analysis_revisions(self.protocol_id, 1)), 1
        )

    def test_a_resolution_without_actor_or_rationale_is_refused(self):
        with self.assertRaises(ProtocolResolutionError):
            self.resolve(rationale="")
        with self.assertRaises(ProtocolResolutionError):
            self.resolve(actor_role="researcher")
        self.assertEqual(
            len(self.store.list_analysis_revisions(self.protocol_id, 1)), 1
        )

    def test_the_same_ambiguity_cannot_be_resolved_twice(self):
        self.resolve()
        with self.assertRaises(ProtocolResolutionError):
            self.resolve()

    def test_approval_requires_readiness_and_then_enables_execution(self):
        base = self.catalog.get_entry(self.protocol_id)
        with self.assertRaises(Exception):
            self.catalog.approve(
                self.protocol_id,
                base.revision_id,
                policy=self.policy,
                presented_secret="tenant-rbac-authorized",
                actor_principal_id="reviewer-a",
                actor_role="reviewer",
            )
        entry = self.resolve()
        approved = self.catalog.approve(
            self.protocol_id,
            entry.revision_id,
            policy=self.policy,
            presented_secret="tenant-rbac-authorized",
            actor_principal_id="reviewer-a",
            actor_role="reviewer",
        )
        self.assertEqual(approved.approval_status, "approved")
        self.assertTrue(approved.available_for_execution)
        review = self.catalog.review(self.protocol_id)
        self.assertEqual(
            review["execution_readiness"]["state"], "approved_for_execution"
        )
        self.assertIn(
            "revoke_execution_approval", review["reviewer_actions"]
        )
        # The researcher-facing executable revision carries the new checkpoint.
        executable = self.catalog.load_executable_fixture(self.protocol_id)
        self.assertFalse(executable.development_only)
        self.assertIn("candidate-a-step-20", executable.human_checkpoints)

    def test_revocation_preserves_history_and_removes_availability(self):
        entry = self.resolve()
        approved = self.catalog.approve(
            self.protocol_id,
            entry.revision_id,
            policy=self.policy,
            presented_secret="tenant-rbac-authorized",
            actor_principal_id="reviewer-a",
            actor_role="reviewer",
        )
        revoked = self.catalog.revoke(
            self.protocol_id,
            approved.revision_id,
            policy=self.policy,
            presented_secret="tenant-rbac-authorized",
            actor_principal_id="reviewer-a",
            actor_role="reviewer",
        )
        self.assertFalse(revoked.available_for_execution)
        context = self.catalog.approval_context(self.protocol_id)
        self.assertEqual(context["status"], "revoked")
        self.assertEqual(context["actor_principal_id"], "reviewer-a")
        approvals = [
            event
            for event in self.store.list_events(self.protocol_id)
            if event.event_type == "protocol_revision_approved"
        ]
        self.assertEqual(
            [event.payload["decision"] for event in approvals],
            ["approved", "revoked"],
        )
        review = self.catalog.review(self.protocol_id)
        self.assertEqual(
            review["execution_readiness"]["state"], "approval_revoked"
        )
        self.assertTrue(
            review["execution_readiness"]["can_approve_for_execution"]
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


class ProductLabelTests(unittest.TestCase):
    """Internal codes stay internal; users read one consistent Korean label."""

    def test_every_readiness_reason_has_product_copy(self):
        from voice_workflow_agent import product_labels

        for code in domain.ReadinessReasonCode:
            label = product_labels.readiness_reason_label(code.value)
            detail = product_labels.readiness_reason_detail(code.value)
            self.assertNotEqual(label, product_labels.UNKNOWN_STATUS_LABEL, code)
            self.assertTrue(detail, code)
            self.assertNotIn("_", label)

    def test_every_readiness_status_has_product_copy(self):
        from voice_workflow_agent import product_labels

        for status in domain.ReadinessStatus:
            label = product_labels.readiness_status_label(status.value)
            self.assertNotEqual(label, product_labels.UNKNOWN_STATUS_LABEL)
            self.assertNotIn("_", label)

    def test_an_unknown_code_degrades_to_a_safe_status(self):
        from voice_workflow_agent import product_labels

        self.assertEqual(
            product_labels.readiness_reason_label("some_future_code"),
            product_labels.UNKNOWN_STATUS_LABEL,
        )
        self.assertEqual(
            product_labels.execution_readiness_label("invented"),
            product_labels.UNKNOWN_STATUS_LABEL,
        )
        self.assertIsNone(product_labels.block_reason_label(None))

    def test_a_true_ambiguity_is_reviewer_work_and_a_checkpoint_is_not(self):
        from voice_workflow_agent import product_labels

        self.assertTrue(
            product_labels.reason_is_reviewer_resolvable("unresolved_ambiguity")
        )
        self.assertFalse(
            product_labels.reason_is_reviewer_resolvable(
                "unsupported_human_confirmed_repeat_until"
            )
        )
        self.assertIn("연구자", product_labels.HUMAN_CHECKPOINT_DETAIL)
