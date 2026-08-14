"""Regressions derived from Candidate A real-voice research failures."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from voice_workflow_agent.curated_protocol import (
    CuratedProtocolAction,
    CuratedProtocolSession,
    classify_curated_control_intent,
    load_curated_protocol_fixture,
    normalize_scientific_request,
)
from voice_workflow_agent.document_store import ingest_manifest
from voice_workflow_agent.external_references import (
    SupplementalKnowledgeSettings,
    plan_research_query,
    supplemental_knowledge_allowed,
)
from voice_workflow_agent.retrieval import retrieve_approved_lab_documents
from voice_workflow_agent.language import Transcription, classify_input_event


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "data/development_protocols/candidate_a_curated_analysis.json"
PROVENANCE = FIXTURE.with_suffix(".provenance.json")
PDF = Path("/home/student/protocol-test-files/in-gel-digestion.pdf")


class CandidateAResearchRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = load_curated_protocol_fixture(FIXTURE, PROVENANCE, PDF)

    def session(self, index=0):
        value = CuratedProtocolSession(self.fixture)
        value.active = True
        value.current_index = index
        return value

    def test_scientific_terms_are_related_questions_with_adjacent_evidence(self):
        questions = (
            "AMBIC가 뭐야?", "여기서 AMBIC는 어떤 물질이야?",
            "HPLC water가 일반 물하고 뭐가 달라?",
            "왜 HPLC water를 쓰는 거야?",
            "Solution A는 어떤 성분으로 되어 있어?",
            "Solution B의 구성은 뭐야?", "아세토니트릴은 여기서 왜 들어가?",
        )
        for turn, question in enumerate(questions, 1):
            with self.subTest(question=question):
                session = self.session(0)
                plan = session.plan(question, turn_id=turn, language="ko")
                self.assertEqual(plan.action, CuratedProtocolAction.RELATED_QUESTION)
                self.assertFalse(plan.state_changed)
                self.assertTrue(plan.requested_entity)
                self.assertTrue(plan.facts)
                self.assertTrue(any(
                    "ammonium bicarbonate" in fact.text.casefold()
                    or "hplc water" in fact.text.casefold()
                    or "acetonitrile" in fact.text.casefold()
                    for fact in plan.facts
                ))

    def test_new_scientific_followups_are_read_only_before_off_topic_fallback(self):
        questions = (
            ("AMBIC에서 bicarbonate는 왜 중요한 거야?", "ambic", "role"),
            ("HPLC water는 일반 물과 어떤 차이가 있어?", "hplc_water", "difference"),
            ("젤 플러그가 왜 완전히 탈색되어야 해?", "gel_plug", "expected_result"),
            ("염색된 단백질 밴드에서 케라틴 오염이 왜 문제가 돼?", "stained_protein_band", "safety"),
        )
        for turn, (question, entity, dimension) in enumerate(questions, 200):
            with self.subTest(question=question):
                session = self.session(1)
                opening = session.current_index
                plan = session.plan(question, turn_id=turn, language="ko")
                self.assertEqual(plan.action, CuratedProtocolAction.RELATED_QUESTION)
                self.assertIn(entity, plan.requested_entities)
                self.assertIn(dimension, plan.question_dimensions)
                self.assertFalse(plan.state_changed)
                self.assertEqual(session.current_index, opening)

        session = self.session(1)
        first = session.plan(
            "HPLC water는 일반 물과 어떤 차이가 있어?",
            turn_id=210,
            language="ko",
        )
        followup = session.plan(
            "그 물을 왜 사용하는 거야?", turn_id=211, language="ko"
        )
        self.assertEqual(first.requested_entities, ("hplc_water",))
        self.assertEqual(followup.requested_entities, ("hplc_water",))
        self.assertEqual(followup.action, CuratedProtocolAction.RELATED_QUESTION)
        self.assertFalse(followup.state_changed)

        unrelated = self.session(1).plan(
            "다음 여행지는 어디가 좋아?", turn_id=212, language="ko"
        )
        self.assertEqual(unrelated.action, CuratedProtocolAction.OFF_TOPIC)
        self.assertFalse(unrelated.state_changed)

    def test_next_step_confirmation_is_server_owned_bounded_and_one_turn_only(self):
        for language, transcript in (
            ("ko", "다음 단계로 안내해 줘."),
            ("en", "Guide me to the next step."),
        ):
            with self.subTest(language=language):
                session = self.session(1)
                opening = session.current_index
                request = session.plan(
                    transcript,
                    turn_id=1,
                    language=language,
                    configuration_id=7,
                    generation=3,
                )
                self.assertEqual(
                    request.action, CuratedProtocolAction.CLARIFY_COMPLETION
                )
                self.assertFalse(request.state_changed)
                self.assertEqual(session.current_index, opening)
                self.assertIsNotNone(session.pending_completion_confirmation)
                confirmed = session.plan(
                    "네." if language == "ko" else "Yes.",
                    turn_id=2,
                    language=language,
                    configuration_id=7,
                    generation=3,
                )
                self.assertEqual(confirmed.action, CuratedProtocolAction.NEXT)
                self.assertTrue(confirmed.reported_completion)
                self.assertTrue(confirmed.state_changed)
                self.assertEqual(session.current_index, opening + 1)
                self.assertEqual(
                    session.plan(
                        "네." if language == "ko" else "Yes.",
                        turn_id=2,
                        language=language,
                        configuration_id=7,
                        generation=3,
                    ),
                    confirmed,
                )
                self.assertEqual(session.current_index, opening + 1)

        declined = self.session(1)
        declined.plan(
            "다음 단계로 안내해 줘", turn_id=1, language="ko",
            configuration_id=7, generation=1,
        )
        result = declined.plan(
            "아니, 아직 안 끝났어", turn_id=2, language="ko",
            configuration_id=7, generation=2,
        )
        self.assertEqual(result.action, CuratedProtocolAction.DECLINE_COMPLETION)
        self.assertEqual(declined.current_index, 1)
        self.assertIsNone(declined.pending_completion_confirmation)

        stale = self.session(1)
        stale.plan(
            "다음 단계로 안내해 줘", turn_id=1, language="ko",
            configuration_id=7, generation=1,
        )
        stale_yes = stale.plan(
            "네.", turn_id=2, language="ko",
            configuration_id=8, generation=2,
        )
        self.assertNotEqual(stale_yes.action, CuratedProtocolAction.NEXT)
        self.assertEqual(stale.current_index, 1)
        self.assertIsNone(stale.pending_completion_confirmation)

        old_generation = self.session(1)
        old_generation.plan(
            "다음 단계로 안내해 줘", turn_id=1, language="ko",
            configuration_id=7, generation=5,
        )
        rejected_old_yes = old_generation.plan(
            "네.", turn_id=2, language="ko",
            configuration_id=7, generation=4,
        )
        self.assertNotEqual(rejected_old_yes.action, CuratedProtocolAction.NEXT)
        self.assertEqual(old_generation.current_index, 1)

        incompatible = self.session(1)
        incompatible.plan(
            "다음 단계로 안내해 줘", turn_id=1, language="ko",
            configuration_id=7, generation=1,
        )
        current = incompatible.plan(
            "현재 단계 알려줘", turn_id=2, language="ko",
            configuration_id=7, generation=2,
        )
        self.assertEqual(current.action, CuratedProtocolAction.CURRENT)
        self.assertIsNone(incompatible.pending_completion_confirmation)
        later_yes = incompatible.plan(
            "네.", turn_id=3, language="ko",
            configuration_id=7, generation=3,
        )
        self.assertNotEqual(later_yes.action, CuratedProtocolAction.NEXT)
        self.assertEqual(incompatible.current_index, 1)

    def test_navigation_information_completion_criteria_and_language_are_distinct(self):
        session = self.session(1)
        opening = session.state()
        preview = session.plan(
            "What is the next step?", turn_id=1, language="en",
        )
        self.assertEqual(preview.action, CuratedProtocolAction.NEXT_INFORMATION)
        self.assertEqual(preview.intent_kind, "next_step_information")
        self.assertIn("Preview", preview.speech_text)
        self.assertIn("Step 3", preview.speech_text)
        self.assertIsNone(session.pending_completion_confirmation)
        self.assertEqual(session.state(), opening)

        current = session.plan(
            "What is the current step?", turn_id=2, language="en",
        )
        self.assertEqual(current.action, CuratedProtocolAction.CURRENT)
        self.assertIn("current step is 2", current.speech_text)
        criteria = session.plan(
            "완료 조건이 뭐야?", turn_id=3, language="ko",
        )
        self.assertEqual(
            criteria.action, CuratedProtocolAction.COMPLETION_CRITERIA,
        )
        self.assertIn("완료 기준", criteria.speech_text)
        self.assertNotEqual(
            criteria.display_text.strip(),
            self.fixture.steps[1].instruction_source_text.strip(),
        )
        self.assertEqual(session.state(), opening)

    def test_bounded_coreference_answers_requested_dimension_before_source_dump(self):
        session = self.session(1)
        session.plan("HPLC water가 뭐야?", turn_id=1, language="ko")
        difference = session.plan(
            "그거 일반 물이랑 뭐가 다른데?", turn_id=2, language="ko",
        )
        self.assertEqual(difference.requested_entities, ("hplc_water",))
        self.assertIn("difference", difference.question_dimensions)
        self.assertIn("일반 물", difference.speech_text)
        self.assertIn("별도 권위 자료", difference.speech_text)

        session.plan("AMBIC가 뭐야?", turn_id=3, language="ko")
        role = session.plan(
            "그거는 왜 여기서 사용하는 거야?", turn_id=4, language="ko",
        )
        self.assertEqual(role.requested_entities, ("ambic",))
        self.assertIn("role", role.question_dimensions)
        self.assertIn("Solution A와 B", role.speech_text)

        session.plan(
            "Solution A와 Solution B 차이가 뭐야?",
            turn_id=5, language="ko",
        )
        first = session.plan(
            "그중 첫 번째는 왜 여기서 사용해?",
            turn_id=6, language="ko",
        )
        self.assertEqual(first.requested_entities, ("solution_a",))
        self.assertFalse(first.state_changed)

    def test_scientific_scope_deviations_and_anomaly_assertions_are_separated(self):
        session = self.session(2)
        opening = session.state()
        rpm = session.plan(
            "800 rpm이 무엇인지 설명해줄 수 있어?",
            turn_id=1, language="ko",
        )
        self.assertEqual(rpm.action, CuratedProtocolAction.RELATED_QUESTION)
        self.assertEqual(rpm.requested_entities, ("rpm",))
        self.assertIn("기기 설정값", rpm.speech_text)
        deviation = session.plan(
            "37도 대신 35도로 해도 돼?", turn_id=2, language="ko",
        )
        self.assertEqual(
            deviation.action, CuratedProtocolAction.OPERATIONAL_DEVIATION,
        )
        self.assertIn("37°C", deviation.speech_text)
        self.assertIn("승인할 수 없습니다", deviation.speech_text)
        self.assertEqual(session.state(), opening)

        anomaly = session.plan(
            "색깔이 변형됐어.", turn_id=3, language="ko",
        )
        self.assertEqual(anomaly.action, CuratedProtocolAction.REPORT_ANOMALY)
        question = session.plan(
            "색깔이 변하는 건 무슨 의미야?", turn_id=4, language="ko",
        )
        self.assertEqual(question.action, CuratedProtocolAction.RELATED_QUESTION)
        self.assertFalse(question.state_changed)
        self.assertEqual(session.state(), opening)

    def test_supplemental_model_knowledge_is_nonauthoritative_and_nonoperational(self):
        self.assertTrue(supplemental_knowledge_allowed(
            "AMBIC의 일반적인 역할은 뭐야?", ("role",)
        ))
        for query, dimensions in (
            ("HPLC water 대신 일반 증류수를 써도 돼?", ("role",)),
            ("추가 안전 수칙을 알려줘", ("safety",)),
            ("25 mM 대신 50 mM를 써도 돼?", ("difference",)),
            ("완료 조건은 뭐야?", ("related_knowledge",)),
        ):
            with self.subTest(query=query):
                self.assertFalse(supplemental_knowledge_allowed(
                    query, dimensions
                ))
        capability = SupplementalKnowledgeSettings(
            True, "grok-4.6", 8.0
        ).public_capability()
        self.assertEqual(capability["authority"], "supplemental_model_knowledge")
        self.assertNotIn("authoritative", capability["authority"])

    def test_week_five_multi_entity_repair_preserves_order_and_audit_note(self):
        key, entities, note, corrections = normalize_scientific_request(
            "여기서 HPLC water하고 ANBI-C가 뭐야?"
        )
        self.assertEqual(entities, ("hplc_water", "ambic"))
        self.assertIn("hplc water", key)
        self.assertIn("ambic", key)
        self.assertIn("ANBI-C".casefold(), corrections[0][0].casefold())
        self.assertIn("문맥상 해석", note)
        session = self.session(0)
        plan = session.plan(
            "여기서 HPLC water하고 ANBI-C가 뭐야?",
            turn_id=90,
            language="ko",
        )
        self.assertEqual(plan.requested_entities, ("hplc_water", "ambic"))
        self.assertIn("HPLC water", plan.primary_text)
        self.assertIn("AMBIC", plan.primary_text)
        self.assertIn("관계", plan.primary_text)
        self.assertNotIn("Catalog #", plan.display_text)
        self.assertFalse(plan.state_changed)

    def test_week_five_entity_visual_variants_are_first_class_read_only_intents(self):
        cases = (
            ("Jel Tug에 관해서 이미지를 보여줄 수 있어.", "gel_plug"),
            ("제트 플러그와 관련해서 이미지를 보여줄 수 있어?", "gel_plug"),
            ("젤 플러그 이미지를 보여줘.", "gel_plug"),
            ("염색된 단백질 밴드가 어떤 걸 의미해? 혹시 그림을 보여줄 수 있어?", "stained_protein_band"),
        )
        for turn, (transcript, entity) in enumerate(cases, 100):
            with self.subTest(transcript=transcript):
                session = self.session(2 if entity == "gel_plug" else 0)
                opening = session.current_index
                plan = session.plan(transcript, turn_id=turn, language="ko")
                self.assertEqual(plan.action, CuratedProtocolAction.VISUAL_REQUEST)
                self.assertEqual(plan.requested_entities, (entity,))
                self.assertTrue(plan.visual_requested)
                self.assertTrue(plan.primary_text)
                self.assertEqual(session.current_index, opening)

    def test_week_five_completion_is_compositional_and_guarded(self):
        mutating = (
            "현재 단계를 완료했어.",
            "지금 단계 끝났어.",
            "이 단계 다 했어.",
            "현재 단계 완료했으니 다음 단계 알려줘.",
            "다음 단계로 안내해 줘. 현재 단계 완료했어.",
        )
        for turn, transcript in enumerate(mutating, 120):
            with self.subTest(transcript=transcript):
                session = self.session(1)
                plan = session.plan(transcript, turn_id=turn, language="ko")
                self.assertTrue(plan.reported_completion)
                self.assertTrue(plan.state_changed)
                self.assertEqual(session.current_index, 2)
                self.assertEqual(session.plan(
                    transcript, turn_id=turn, language="ko"
                ), plan)
                self.assertEqual(session.current_index, 2)
        guarded = (
            "완료 조건이 뭐야?",
            "아직 완료하지 않았어.",
            "이 단계를 완료했다고 가정하면 다음은 뭐야?",
            '“현재 단계 완료했어”라고 말하면 돼?',
            "다음 단계를 완료했다고 기록해 줘",
        )
        for turn, transcript in enumerate(guarded, 140):
            with self.subTest(transcript=transcript):
                session = self.session(1)
                plan = session.plan(transcript, turn_id=turn, language="ko")
                self.assertFalse(plan.state_changed)
                self.assertEqual(session.current_index, 1)
        session = self.session(1)
        repaired = session.plan("장기를 완료했어.", turn_id=160, language="ko")
        self.assertEqual(repaired.action, CuratedProtocolAction.CLARIFY_COMPLETION)
        self.assertIn("장기", repaired.transcript_correction_note)
        self.assertEqual(session.current_index, 1)

    def test_dynamic_stt_terms_are_bounded_and_protocol_relevant(self):
        session = self.session(1)
        terms = session.stt_keyterms()
        for expected in (
            "AMBIC", "HPLC water", "Solution A", "Solution B",
            "acetonitrile", "gel plug", "현재 단계", "완료",
        ):
            self.assertIn(expected, terms)
        self.assertLessEqual(len(terms), 100)
        self.assertTrue(all(1 <= len(item) <= 50 for item in terms))

    def test_current_step_detail_is_useful_and_read_only(self):
        session = self.session(3)
        plan = session.plan(
            "단계를 좀 더 자세히 설명해 줘.", turn_id=1, language="ko"
        )
        self.assertEqual(plan.action, CuratedProtocolAction.FULL_DETAIL)
        self.assertFalse(plan.state_changed)
        self.assertIn("무엇을 제거하나요", plan.display_text)
        self.assertIn("젤 밴드는 튜브에 남습니다", plan.display_text)
        self.assertIn("명시되어 있지 않습니다", plan.display_text)

    def test_anomaly_is_read_only_and_report_request_is_distinct(self):
        session = self.session(3)
        anomaly = session.plan(
            "예상과 다르게 색이 남아 있어.", turn_id=1, language="ko"
        )
        report = session.plan(
            "현재 실험 기록을 보여줘.", turn_id=2, language="ko"
        )
        self.assertEqual(anomaly.action, CuratedProtocolAction.REPORT_ANOMALY)
        self.assertTrue(anomaly.reported_anomaly)
        self.assertFalse(anomaly.state_changed)
        self.assertEqual(report.action, CuratedProtocolAction.SHOW_REPORT)
        self.assertFalse(report.state_changed)
        self.assertEqual(session.current_index, 3)

    def test_safety_query_planner_includes_active_entities(self):
        query = plan_research_query(
            "여기서 진짜 안전 수칙 있어?",
            protocol_title=self.fixture.title,
            step_label="4",
            step_text=self.fixture.steps[3].instruction_source_text,
            evidence_texts=(self.fixture.steps[1].instruction_source_text,),
            requested_entity="solution_a",
            question_kind="safety",
        )
        for expected in ("Solution A", "acetonitrile", "PPE", "waste"):
            self.assertIn(expected, query)

    def test_whole_protocol_queries_are_deterministic_and_complete(self):
        cases = (
            ("이 실험은 총 몇 단계야?", "protocol_total_steps", "총 25단계"),
            ("현재 몇 번째 단계야?", "protocol_current_position", "6단계"),
            ("몇 단계 남았어?", "protocol_remaining_steps", "19단계"),
            ("전체 흐름을 요약해 줘.", "protocol_overview", "전체 25단계"),
            ("시작 전에 무엇을 준비해야 해?", "protocol_preparation", "시작 전"),
            ("전체 안전수칙을 알려줘.", "protocol_safety", "오염 방지"),
        )
        for turn, (question, intent_kind, expected) in enumerate(cases, 1):
            with self.subTest(question=question):
                session = self.session(5)
                plan = session.plan(question, turn_id=turn, language="ko")
                self.assertEqual(plan.action, CuratedProtocolAction.PROTOCOL_QUERY)
                self.assertEqual(plan.intent_kind, intent_kind)
                self.assertIn(expected, plan.display_text + plan.speech_text)
                self.assertFalse(plan.state_changed)
                self.assertEqual(session.current_index, 5)
                self.assertEqual(len(self.fixture.steps), 25)

    def test_specific_step_lookup_is_exact_and_does_not_move_current_step(self):
        session = self.session(5)
        plan = session.plan("12단계 설명해 줘", turn_id=1, language="ko")
        self.assertEqual(plan.action, CuratedProtocolAction.FULL_DETAIL)
        self.assertEqual(plan.target_step, "12")
        self.assertEqual(plan.step_label, "12")
        self.assertIn(self.fixture.steps[11].instruction_source_text, plan.display_text)
        self.assertEqual(session.current_index, 5)
        self.assertFalse(plan.state_changed)

    def test_natural_stop_variants_are_anchored_and_high_priority(self):
        positives = (
            "프로토콜을 종료할게", "프로토콜 종료할게요", "여기서 끝낼게",
            "그만할래", "중단해 줘", "stop the protocol", "I'll stop here",
        )
        for value in positives:
            with self.subTest(value=value):
                intent = classify_curated_control_intent(value, language="ko")
                self.assertEqual(intent.action, CuratedProtocolAction.STOP)
                self.assertTrue(intent.allows_state_mutation)
        negative = classify_curated_control_intent(
            "종료 조건이 뭐야?", language="ko"
        )
        self.assertIsNot(negative.action, CuratedProtocolAction.STOP)

    def test_shared_input_event_classifier_rejects_only_whole_noise_events(self):
        rejected = (
            "Cough.", "[coughing]", "(throat clearing)", "Sniffing",
            "<keyboard>", "chair movement", "Silence", "기침", "[헛기침]",
            "의자 소리", "음악",
        )
        for value in rejected:
            with self.subTest(value=value):
                self.assertFalse(
                    classify_input_event(Transcription(value, "ko")).accepted
                )
        for value in ("네", "아니요", "다음", "중지", "stop", "7", "AMBIC", "I coughed"):
            with self.subTest(value=value):
                self.assertTrue(
                    classify_input_event(Transcription(value, "ko")).accepted
                )
        self.assertFalse(classify_input_event(Transcription(
            "다음", "ko", no_speech_probability=0.91
        )).accepted)

    def test_checked_in_real_failure_corpus_meets_route_and_mutation_targets(self):
        import json
        corpus = json.loads((
            ROOT / "tests/fixtures/candidate_a_grounded_voice_eval.json"
        ).read_text(encoding="utf-8"))
        failures = []
        for index, case in enumerate(corpus["cases"], 1):
            transcript = case["transcript"]
            if "input_rejected" in case:
                actual = not classify_input_event(
                    Transcription(transcript, "ko")
                ).accepted
                if actual != case["input_rejected"]:
                    failures.append((case["category"], transcript, actual))
                continue
            session = self.session(5)
            opening = session.current_index
            intent = classify_curated_control_intent(transcript, language="ko")
            if intent.action.value != case.get("expected_action", intent.action.value):
                failures.append((case["category"], transcript, intent.action.value))
            if intent.action.value == case.get("forbidden_action"):
                failures.append((case["category"], transcript, "forbidden"))
            if case.get("expected_scope") != intent.protocol_scope:
                failures.append((case["category"], transcript, intent.protocol_scope))
            if not case.get("mutates", False):
                plan = session.plan(transcript, turn_id=index, language="ko")
                if plan.state_changed or session.current_index != opening:
                    failures.append((case["category"], transcript, "mutation"))
        self.assertEqual(failures, [])


class CandidateAEvidenceAdmissionTests(unittest.TestCase):
    def test_demo_records_are_rejected_even_when_ranked(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "catalog.sqlite"
            payload = {
                "documents": [{
                    "document_id": "FICTIONAL-MOSS-DEMO-SDS-KO",
                    "document_family_id": "FICTIONAL-MOSS-DEMO-FAMILY",
                    "canonical_source_id": "FICTIONAL-MOSS-DEMO-SOURCE",
                    "title": "FICTIONAL NON-OPERATIONAL demo",
                    "issuer": "MOSS fictional test fixture",
                    "document_type": "supplier_sds",
                    "manufacturer": "MOSS",
                    "product_name": "MOSS-A100",
                    "product_code": "MOSS-A100",
                    "cas_numbers": [], "aliases": [],
                    "version": "1.0", "canonical_version": "1.0",
                    "language": "ko", "translation_status": "original",
                    "translation_of_document_id": None,
                    "approval_status": "approved", "active": True,
                    "source_authority": "supplier", "facility_id": None,
                    "usage_scope": "demo", "effective_at": "2026-01-01T00:00:00+00:00",
                    "review_due_at": "2028-01-01T00:00:00+00:00",
                    "source_checksum": "sha256:" + "1" * 64,
                    "source_uri": "demo://fictional/moss/sds-ko",
                    "source_path": "data/moss_demo/demo.json",
                    "sections": [{
                        "section_code": "SDS-08", "section_title": "안전 주의",
                        "page_start": 1, "page_end": 1,
                        "content": "Solution A acetonitrile 안전 PPE 모의 기록",
                        "topic": "exposure_ppe", "keywords": ["Solution A"],
                    }],
                }],
            }
            ingest_manifest(payload, db)
            result = retrieve_approved_lab_documents(
                "Solution A acetonitrile 안전 PPE", db,
                filters={
                    "approval_status": "approved", "lab_scope": "demo",
                    "exclude_non_operational": True,
                },
                now=datetime(2027, 1, 1, tzinfo=timezone.utc),
            )
        self.assertEqual(result["status"], "no_admissible_evidence")
        self.assertFalse(result["answerable"])
        self.assertEqual(result["matches"], [])
        self.assertEqual(result["rejections"][0]["reason"], "non_operational_or_demo")


if __name__ == "__main__":
    unittest.main()
