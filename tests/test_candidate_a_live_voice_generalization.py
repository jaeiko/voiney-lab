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
            (Path(__file__).resolve().parents[1] / "data" / "runtime" / "candidate-a-source" / "in-gel-digestion.pdf"),
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

    def test_agent_meta_intent_and_capabilities(self) -> None:
        for utterance, lang in (
            ("이 에이전트의 목적이 뭐야?", "ko"),
            ("하는 기능이 뭐야?", "ko"),
            ("이 에이전트의 주요 기능과 역할을 설명해줘", "ko"),
            ("그러면 네가 하는 기능이 뭐야?", "ko"),
            ("너 뭐 하는 에이전트야?", "ko"),
            ("네 역할이 뭐야?", "ko"),
            ("무슨 기능이 있어?", "ko"),
            ("어떤 기능이 있어?", "ko"),
            ("너는 무슨 일 해?", "ko"),
            ("너는 어떤 역할을 해?", "ko"),
            ("네 기능 설명해줘", "ko"),
            ("너 뭐 할 수 있어?", "ko"),
            ("What can this agent do?", "en"),
            ("What are your capabilities?", "en"),
            ("Then what's your function?", "en"),
            ("What do you do?", "en"),
            ("What are your functions?", "en"),
            ("What does this agent do?", "en"),
            ("Who are you and what do you do?", "en"),
        ):
            with self.subTest(utterance=utterance):
                session = CuratedProtocolSession(self.fixture)
                plan = session.plan(utterance, turn_id=1, language=lang)
                self.assertEqual(plan.action, CuratedProtocolAction.AGENT_META)
                self.assertEqual(plan.intent_kind, "agent_meta")
                self.assertFalse(plan.state_changed)
                self.assertIn("보이스 워크플로" if lang == "ko" else "voice workflow assistant", plan.primary_text or "")

        # In Manual Korean session mode, English STT capability queries must return Korean response
        session = CuratedProtocolSession(self.fixture)
        ko_en_plan = session.plan("Then what's your function?", turn_id=1, language="ko")
        self.assertEqual(ko_en_plan.action, CuratedProtocolAction.AGENT_META)
        self.assertIn("보이스 워크플로", ko_en_plan.primary_text or "")

    def test_pre_start_preview_and_preview_step(self) -> None:
        for start_utterance in (
            "프로토콜 시작해줘", "시작해", "시작해줘", "응 시작하자", "start", "start it",
            "1단계부터 하자", "진행하자", "실험을 시작해줘.", "실험 시작해줘", "실험 시작",
            "실험을 시작해 주세요", "실험을 시작해 줘", "실험을 시작해줄 수 있어",
            "프로토콜을 시작해줘", "프로토콜 시작", "절차를 시작해줘", "절차 시작",
            "1단계부터 시작해줘", "1단계 시작해줘", "1단계 시작", "시작하자", "시작할게",
            "시작할게요", "시작하겠습니다", "시작 부탁해", "진행해줘", "이제 실험 시작하자",
            "start protocol", "start the protocol", "start experiment", "start the experiment",
            "let's start", "lets start", "yes, start", "begin protocol",
        ):
            with self.subTest(utterance=start_utterance):
                session = CuratedProtocolSession(self.fixture)
                session.configure_ready()
                self.assertFalse(session.active)
                self.assertEqual(session.workflow_status, "preview")
                started = session.plan(start_utterance, turn_id=1, language="en" if any(w in start_utterance for w in ("start", "begin", "let")) else "ko")
                self.assertEqual(started.action, CuratedProtocolAction.START)
                self.assertTrue(session.active)
                self.assertEqual(session.workflow_status, "active")
                self.assertEqual(session.current_index, 0)
                self.assertEqual(session.experiment_timer_status()["state"], "running")

        session = CuratedProtocolSession(self.fixture)
        session.configure_ready()
        # Pre-start CURRENT previews step 1 without mutating the session.
        curr = session.plan("현재 단계가 뭐야?", turn_id=1, language="ko")
        self.assertEqual(curr.action, CuratedProtocolAction.CURRENT)
        self.assertFalse(session.active)
        self.assertEqual(session.state()["current_step_label"], "1")
        self.assertIn(
            "아직 실험 시작 전입니다",
            curr.speech_text or curr.display_text or "",
        )

        # Explicit preview of step 1
        prev = session.plan("1단계 미리 알려줘", turn_id=2, language="ko")
        self.assertEqual(prev.action, CuratedProtocolAction.PREVIEW_STEP)
        self.assertFalse(session.active)
        self.assertIn("1단계 미리보기", prev.primary_text or "")

    def test_conversational_stutter_and_particles_completion(self) -> None:
        for stutter_utterance in (
            "Okay, 현재 현재 단계로 완료했어",
            "어 음 지금 지금 단계를 완료했어",
            "네 현재 단계는 완료했습니다",
            "좋아 completed 했어",
            "This step is done, let's move on",
            "다 했으니까 다음으로 넘어가줘",
        ):
            with self.subTest(utterance=stutter_utterance):
                session = self.session("1")
                self.assertEqual(session.current_index, 0)
                plan = session.plan(
                    stutter_utterance, turn_id=2,
                    language="en" if "done" in stutter_utterance else "ko",
                )
                self.assertTrue(plan.state_changed)
                self.assertEqual(session.current_index, 1)

    def test_multilingual_underspecified_result_query_parity(self) -> None:
        for utterance, lang in (
            ("실험 결과 알려줘", "ko"),
            ("결과가 어떻게 돼?", "ko"),
            ("지금 나온 결과 보여줘", "ko"),
            ("Tell me the result", "en"),
            ("What was the result?", "en"),
        ):
            with self.subTest(utterance=utterance):
                session = self.session("1")
                plan = session.plan(utterance, turn_id=2, language=lang)
                self.assertEqual(plan.action, CuratedProtocolAction.CLARIFY_REFERENCE)
                self.assertEqual(plan.intent_kind, "underspecified_result_request")
                self.assertFalse(plan.state_changed)
                if lang == "ko":
                    self.assertIn("어떤 결과를 말씀하시나요", plan.primary_text or "")
                else:
                    self.assertIn("Which result do you mean", plan.primary_text or "")

    def test_solution_a_disposal_limitation_and_sources(self) -> None:
        session = self.session("4")
        plan = session.plan("솔루션 A가 무엇이며 어떻게 폐기하는 거야?", turn_id=2, language="ko")
        self.assertIn("solution_a", plan.requested_entities)
        disposal_claims = tuple(
            claim for claim in plan.claim_requests
            if claim.dimension == "disposal_method"
        )
        self.assertGreaterEqual(len(disposal_claims), 1)
        for claim in disposal_claims:
            self.assertEqual(claim.admission_status, ClaimAdmissionStatus.RESEARCH_REQUIRED)
        self.assertIn("Solution A", plan.primary_text or "")
        self.assertIn("명시되어 있지 않습니다", plan.primary_text or "")
        self.assertFalse(plan.state_changed)

    def test_pause_and_resume_with_timer_continuity(self) -> None:
        session = self.session("3")
        self.assertEqual(session.workflow_status, "active")

        # Start timer at Step 3
        timer_start = session.plan("타이머 시작해", turn_id=2, language="ko")
        self.assertEqual(timer_start.action, CuratedProtocolAction.START_TIMER)
        self.assertIn("타이머를 시작했습니다", timer_start.primary_text or "")

        # Check timer status
        timer_stat = session.plan("타이머 얼마나 남았어?", turn_id=3, language="ko")
        self.assertEqual(timer_stat.action, CuratedProtocolAction.TIMER_STATUS)
        self.assertIn("타이머", timer_stat.primary_text or "")

        # Pause workflow
        paused = session.plan("잠깐 일시 중지할게", turn_id=4, language="ko")
        self.assertEqual(paused.action, CuratedProtocolAction.PAUSE)
        self.assertEqual(session.workflow_status, "paused")
        self.assertIn("일시 중지", paused.primary_text or "")

        # Resume workflow
        resumed = session.plan("다시 시작할게", turn_id=5, language="ko")
        self.assertEqual(resumed.action, CuratedProtocolAction.RESUME)
        self.assertEqual(session.workflow_status, "active")
        self.assertIn("재개", resumed.speech_text or resumed.display_text or "")

    def test_parameter_rationale_unresolved_boundary(self) -> None:
        session = self.session("3")
        plan = session.plan("800 rpm 하고 37도로 기준선을 잡은 근거가 어디 있어?", turn_id=2, language="ko")
        rationale_claims = tuple(
            claim for claim in plan.claim_requests
            if claim.target_type is ClaimTargetType.PARAMETER and claim.dimension == "rationale"
        )
        self.assertGreaterEqual(len(rationale_claims), 1)
        for claim in rationale_claims:
            self.assertEqual(claim.admission_status, ClaimAdmissionStatus.RESEARCH_REQUIRED)
            self.assertEqual(claim.unresolved_reason, "rationale_absent_from_active_protocol")

    def test_compositional_completion_and_next_step_order_invariance(self) -> None:
        # Order 1: completion then next
        s1 = self.session("1")
        p1 = s1.plan("1단계 다 했어 다음 단계로 넘어가 줘", turn_id=2, language="ko")
        self.assertTrue(p1.state_changed)
        self.assertEqual(s1.current_index, 1)

        # Order 2: next then completion
        s2 = self.session("1")
        p2 = s2.plan("다음 단계로 넘어가 줘 1단계 다 했어", turn_id=2, language="ko")
        self.assertTrue(p2.state_changed)
        self.assertEqual(s2.current_index, 1)

    def test_solution_b_disposal_at_step_6_dynamic_binding(self) -> None:
        session = self.session("6")
        plan = session.plan("이 용액 폐기는 어떻게 해?", turn_id=2, language="ko")
        self.assertTrue(any(
            claim.target_id == "solution_b" for claim in plan.claim_requests
        ))
        self.assertIn("Solution B", plan.primary_text or "")

    def test_underspecified_result_query_clarification(self) -> None:
        session = self.session("1")
        plan = session.plan("결과 알려줘", turn_id=2, language="ko")
        self.assertEqual(plan.action, CuratedProtocolAction.CLARIFY_REFERENCE)
        self.assertIn("어떤 결과", plan.primary_text or "")

    def test_new_scientific_entities_normalization_and_claims(self) -> None:
        for entity_query, entity_name in (
            ("DTT가 뭐야?", "dtt"),
            ("Iodoacetamide의 역할이 뭐야?", "iodoacetamide"),
            ("Trypsin에 대해 설명해줘", "trypsin"),
            ("Formic acid는 왜 넣어?", "formic_acid"),
        ):
            with self.subTest(query=entity_query):
                session = self.session("10")
                plan = session.plan(entity_query, turn_id=2, language="ko")
                self.assertIn(entity_name, plan.requested_entities)
                self.assertFalse(plan.state_changed)

    def test_multi_entity_near_miss_and_completeness_gate(self) -> None:
        session = self.session("1")
        # Real microphone STT variant: "뱀드" instead of "밴드" + AMBIC
        real_turn7 = session.plan(
            "여기서 염색된 단백질 뱀드가 무엇이며 그리고 AMBIC가 무엇인지 설명해 줄 수 있어?",
            turn_id=2, language="ko",
        )
        self.assertIn("stained_protein_band", real_turn7.requested_entities)
        self.assertIn("ambic", real_turn7.requested_entities)
        self.assertIn("단백질 밴드", real_turn7.primary_text or "")
        self.assertIn("AMBIC", real_turn7.primary_text or "")
        self.assertFalse(real_turn7.state_changed)

        # Other multi-entity near-misses and combinations
        for query, expected_entities in (
            ("염색된 단백질 밴드와 AMBIC가 뭐야?", ("stained_protein_band", "ambic")),
            ("젤 플러그와 HPLC water가 뭐야?", ("gel_plug", "hplc_water")),
            ("제트 플러그와 에이치피엘씨 워터 설명해줘", ("gel_plug", "hplc_water")),
            ("Solution A and Solution B difference", ("solution_a", "solution_b")),
            ("DTT와 iodoacetamide 그리고 trypsin 설명해줘", ("dtt", "iodoacetamide", "trypsin")),
        ):
            with self.subTest(query=query):
                s = self.session("1")
                is_en = "difference" in query
                p = s.plan(query, turn_id=2, language="en" if is_en else "ko")
                for ent in expected_entities:
                    self.assertIn(ent, p.requested_entities)
                # Verify completeness: every requested entity has a mention in primary_text
                primary = p.primary_text or ""
                labels_en = {
                    "stained_protein_band": "Stained protein band",
                    "ambic": "AMBIC",
                    "gel_plug": "Gel plug",
                    "hplc_water": "HPLC water",
                    "solution_a": "Solution A",
                    "solution_b": "Solution B",
                    "dtt": "DTT",
                    "iodoacetamide": "Iodoacetamide",
                    "trypsin": "Trypsin",
                }
                labels_ko = {
                    "stained_protein_band": "단백질 밴드",
                    "ambic": "AMBIC",
                    "gel_plug": "플러그",
                    "hplc_water": "HPLC water",
                    "solution_a": "Solution A",
                    "solution_b": "Solution B",
                    "dtt": "DTT",
                    "iodoacetamide": "Iodoacetamide",
                    "trypsin": "트립신",
                }
                labels = labels_en if is_en else labels_ko
                for ent in expected_entities:
                    self.assertIn(labels[ent], primary)


if __name__ == "__main__":
    unittest.main()
