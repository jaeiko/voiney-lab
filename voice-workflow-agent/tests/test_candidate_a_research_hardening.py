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
from voice_workflow_agent.external_references import plan_research_query
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
        self.assertLessEqual(len(terms), 24)
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
