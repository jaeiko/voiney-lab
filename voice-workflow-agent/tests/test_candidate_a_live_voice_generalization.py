"""Generalized regressions derived from the latest Candidate A voice run."""

from __future__ import annotations

import unittest
from pathlib import Path

from voice_workflow_agent.curated_protocol import (
    ClaimAdmissionStatus,
    ClaimTargetType,
    CuratedProtocolAction,
    CuratedProtocolSession,
    DiscourseFocusKind,
    build_step_semantic_frame,
    load_curated_protocol_fixture,
)


ROOT = Path(__file__).resolve().parents[1]


class CandidateALiveVoiceGeneralizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = load_curated_protocol_fixture(
            ROOT / "data/development_protocols/candidate_a_curated_analysis.json",
            ROOT / "data/development_protocols/candidate_a_curated_analysis.provenance.json",
            Path("/home/student/protocol-test-files/in-gel-digestion.pdf"),
        )

    def session(self, label: str = "1") -> CuratedProtocolSession:
        session = CuratedProtocolSession(self.fixture)
        session.plan("프로토콜을 시작해줘", turn_id=1, language="ko")
        session.current_index = int(label) - 1
        return session

    def test_protocol_purpose_generalizes_and_current_step_remains_distinct(self) -> None:
        for utterance in (
            "우선 이 실험이 어떤 실험인지 알려줄 수 있어?",
            "이 프로토콜은 결국 무엇을 하는 과정인가요?",
            "What is this protocol meant to accomplish?",
        ):
            with self.subTest(utterance=utterance):
                session = self.session()
                opening = session.current_index
                plan = session.plan(
                    utterance, turn_id=2,
                    language="en" if utterance.startswith("What") else "ko",
                )
                self.assertEqual(plan.action, CuratedProtocolAction.PROTOCOL_QUERY)
                self.assertEqual(plan.intent_kind, "protocol_purpose")
                self.assertIn("in-gel", " ".join(plan.source_texts).casefold())
                self.assertEqual(session.current_index, opening)
        contrast = self.session().plan(
            "이 실험에서 지금 단계가 뭐야?", turn_id=2, language="ko"
        )
        self.assertEqual(contrast.action, CuratedProtocolAction.CURRENT)
        self.assertNotEqual(contrast.intent_kind, "protocol_purpose")

    def test_admitted_purpose_proposition_owns_one_compatible_followup(self) -> None:
        session = self.session()
        session.plan(
            "이 실험이 어떤 실험인지 알려줘", turn_id=2, language="ko"
        )
        self.assertEqual(
            session._discourse_context.focus_kind,
            DiscourseFocusKind.PROTOCOL_PURPOSE,
        )
        follow = session.plan("그게 왜 유용해?", turn_id=3, language="ko")
        self.assertEqual(follow.intent_kind, "protocol_purpose_followup")
        self.assertIn("질량분석", follow.primary_text or "")
        self.assertFalse(follow.state_changed)

        session.plan("AMBIC가 뭐야?", turn_id=4, language="ko")
        stale = session.plan("그게 왜 유용해?", turn_id=5, language="ko")
        self.assertNotEqual(stale.intent_kind, "protocol_purpose_followup")
        self.assertFalse(stale.state_changed)

    def test_multipart_question_is_decomposed_and_partially_admitted(self) -> None:
        session = self.session("1")
        opening = session.current_index
        plan = session.plan(
            "AMBIC가 뭔지, 1.5 mL 튜브에는 뭘 넣는지, 왜 그 크기 튜브를 쓰는지 알려줘",
            turn_id=2, language="ko",
        )
        admitted = tuple(
            claim for claim in plan.claim_requests
            if claim.admission_status is ClaimAdmissionStatus.LOCAL_SUPPORTED
        )
        unresolved = tuple(
            claim for claim in plan.claim_requests
            if claim.admission_status is ClaimAdmissionStatus.RESEARCH_REQUIRED
        )
        self.assertGreaterEqual(len(admitted), 2)
        self.assertEqual(len(unresolved), 1)
        self.assertEqual(unresolved[0].target_id, "vessel_capacity")
        self.assertIn("AMBIC", plan.primary_text or "")
        self.assertIn("200 µL", plan.primary_text or "")
        self.assertIn("설명하지 않습니다", plan.primary_text or "")
        self.assertEqual(plan.unresolved_claim_ids, (unresolved[0].claim_id,))
        self.assertEqual(session.current_index, opening)

        paraphrase = self.session("2").plan(
            "Solution B가 무엇이고 어떻게 준비하는지, 안전상 더 확인할 점도 알려줘",
            turn_id=2, language="ko",
        )
        self.assertIn("solution_b", paraphrase.requested_entities)
        self.assertFalse(paraphrase.state_changed)

        contrast = self.session("3").plan(
            "37°C 대신 35°C로 바꿔도 돼?", turn_id=2, language="ko"
        )
        self.assertEqual(contrast.action, CuratedProtocolAction.OPERATIONAL_DEVIATION)
        self.assertFalse(contrast.state_changed)

    def test_current_step_frame_binds_parameters_actions_and_ratios(self) -> None:
        frame = build_step_semantic_frame(self.fixture, 2)
        self.assertTrue(any(item.role == "incubation_temperature" for item in frame.parameters))
        self.assertTrue(any(item.role == "incubation_duration" for item in frame.parameters))
        self.assertTrue(any(item.role == "agitation_speed" for item in frame.parameters))

        plan = self.session("3").plan(
            "37°C와 15min, 800 rpm은 여기서 무엇을 위한 거야?",
            turn_id=2, language="ko",
        )
        parameter_claims = tuple(
            claim for claim in plan.claim_requests
            if claim.target_type is ClaimTargetType.PARAMETER
        )
        self.assertGreaterEqual(len(parameter_claims), 3)
        self.assertIn("배양 온도", plan.primary_text or "")
        self.assertIn("배양 시간", plan.primary_text or "")
        self.assertIn("교반 속도", plan.primary_text or "")
        self.assertFalse(plan.state_changed)

        unseen = self.session("1").plan(
            "200 µL는 이 단계에서 어떤 값이야?", turn_id=2, language="ko"
        )
        self.assertEqual(unseen.action, CuratedProtocolAction.RELATED_QUESTION)
        self.assertIn("200 µL", unseen.primary_text or "")

        ambiguous = self.session("3").plan(
            "이 단계의 용액 부피는 얼마야?", turn_id=2, language="ko"
        )
        self.assertEqual(ambiguous.action, CuratedProtocolAction.CLARIFY_PARAMETER)
        self.assertFalse(ambiguous.state_changed)

    def test_action_rationale_ratio_and_suspicious_decimal_stay_distinct(self) -> None:
        action = self.session("4").plan(
            "Why do we discard Solution A here?", turn_id=2, language="en"
        )
        self.assertTrue(any(
            claim.target_type is ClaimTargetType.ACTION
            and claim.dimension == "value" for claim in action.claim_requests
        ))
        self.assertTrue(any(
            claim.target_type is ClaimTargetType.ACTION
            and claim.dimension == "rationale"
            and claim.admission_status is ClaimAdmissionStatus.RESEARCH_REQUIRED
            for claim in action.claim_requests
        ))
        self.assertIn("remove and discard", action.primary_text or "")

        definition = self.session("4").plan(
            "What is Solution A?", turn_id=2, language="en"
        )
        self.assertFalse(any(
            claim.target_type is ClaimTargetType.ACTION
            for claim in definition.claim_requests
        ))

        ratio = self.session("2").plan(
            "아세토니트릴과 AMBIC 용액을 몇 대 몇으로 섞어?",
            turn_id=2, language="ko",
        )
        self.assertTrue(any(
            claim.target_type is ClaimTargetType.RATIO
            for claim in ratio.claim_requests
        ))
        self.assertIn("2 parts", ratio.primary_text or "")
        suspicious = self.session("2").plan(
            "2.2로 섞으면 돼?", turn_id=2, language="ko"
        )
        self.assertEqual(suspicious.action, CuratedProtocolAction.CLARIFY_PARAMETER)
        self.assertEqual(suspicious.plausibility_status, "incompatible_suspicious")
        self.assertFalse(suspicious.state_changed)

    def test_owned_binary_frames_accept_natural_answers_but_never_unowned_or_so(self) -> None:
        session = self.session("1")
        session.plan("다음 단계로 안내해 줘", turn_id=2, language="ko")
        accepted = session.plan("어, 다 했어", turn_id=3, language="ko")
        self.assertEqual(accepted.intent_kind, "pending_completion_confirmed")
        self.assertTrue(accepted.state_changed)

        paraphrase = self.session("1")
        paraphrase.plan("다음 단계로 안내해 줘", turn_id=2, language="ko")
        accepted = paraphrase.plan("응, 모두 마쳤어요", turn_id=3, language="ko")
        self.assertTrue(accepted.state_changed)

        unowned = self.session("1").plan(
            "응, 모두 마쳤어요", turn_id=2, language="ko"
        )
        self.assertFalse(unowned.state_changed)

        unclear = self.session("1")
        unclear.plan("다음 단계로 안내해 줘", turn_id=2, language="ko")
        retry = unclear.plan("So", turn_id=3, language="ko")
        self.assertEqual(retry.action, CuratedProtocolAction.TRANSCRIPT_UNRELIABLE)
        self.assertFalse(retry.state_changed)
        self.assertIsNotNone(unclear.pending_completion_confirmation)

    def test_observation_yes_no_inherits_only_owned_source_predicate(self) -> None:
        for label in ("7", "9", "20"):
            with self.subTest(label=label, answer="yes"):
                session = self.session(label)
                opening = session.current_index
                session.plan("현재 단계를 완료했어", turn_id=2, language="ko")
                accepted = session.plan("네", turn_id=3, language="ko")
                self.assertTrue(accepted.reported_observation)
                self.assertEqual(accepted.observation_predicate, "positive")
                self.assertEqual(session.current_index, opening + 1)
            with self.subTest(label=label, answer="no"):
                session = self.session(label)
                opening = session.current_index
                session.plan("현재 단계를 완료했어", turn_id=2, language="ko")
                declined = session.plan("아니요", turn_id=3, language="ko")
                self.assertTrue(declined.reported_observation)
                self.assertEqual(declined.observation_predicate, "negative")
                self.assertEqual(session.current_index, opening)
                if label == "20":
                    self.assertIn("미해결", declined.display_text or "")
        unowned = self.session("7").plan("네", turn_id=2, language="ko")
        self.assertFalse(unowned.state_changed)


if __name__ == "__main__":
    unittest.main()
