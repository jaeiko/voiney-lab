import asyncio
from pathlib import Path
import unittest

from voice_workflow_agent.curated_protocol import (
    CuratedProtocolAction,
    CuratedProtocolSession,
    load_curated_protocol_fixture,
)
from voice_workflow_agent.language import (
    Transcription,
    classify_input_event,
)
from voice_workflow_agent.web_visuals import PubChemChemistryAdapter

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "development_protocols"
SOURCE_PDF = (Path(__file__).resolve().parents[1] / "data" / "runtime" / "candidate-a-source" / "in-gel-digestion.pdf")


class CandidateAAcceptancePhase2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = load_curated_protocol_fixture(
            DATA / "candidate_a_curated_analysis.json",
            DATA / "candidate_a_curated_analysis.provenance.json",
            SOURCE_PDF,
        )

    def session(self) -> CuratedProtocolSession:
        return CuratedProtocolSession(self.fixture)

    def test_turn_4_vocabulary_dump_rejection(self) -> None:
        """Turn 4: STT vocabulary dump is rejected by admission gate without mutating workflow."""
        session = self.session()
        # Turn 3: Start experiment
        plan_start = session.plan("실험 시작해 줘.", language="ko", turn_id=3, generation=1)
        self.assertEqual(plan_start.action, CuratedProtocolAction.START)
        self.assertTrue(session.active)
        self.assertEqual(session.current_index, 0)
        state_before = session.state()

        # Turn 4: STT echoes vocabulary prompt
        dump_text = (
            "프로토콜 시작. 현재 단계, 다음 단계, 다음 단계 미리보기, 완료, "
            "현재 단계 완료, 완료했어요, 다 했어요, 완료 조건, 네, 아니요, "
            "다시 알려줘, 프로토콜 중단, 이상 사항 기록, 관찰 결과, "
            "완전히 탈색, 투명해요, 투명한가요, 흰색으로 변했어요, 아직 색이 남아 있어요."
        )
        transcription = Transcription(text=dump_text, detected_language="ko", duration_seconds=4.0)
        decision = classify_input_event(transcription, keyterms=session.stt_keyterms())
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason, "keyterm_echo")

        # Verify state is untouched
        state_after = session.state()
        self.assertEqual(state_after["current_step_label"], state_before["current_step_label"])
        self.assertEqual(state_after["revision"], state_before["revision"])
        self.assertEqual(session.current_index, 0)

    def test_turn_5_and_6_ambic_coreference_and_visual_retrieval(self) -> None:
        """Turn 5 (explain AMBIC) -> Turn 6 (show related picture) resolves AMBIC and retrieves PubChem structure."""
        session = self.session()
        session.plan("실험 시작해 줘.", language="ko", turn_id=3, generation=1)
        self.assertEqual(session.current_index, 0)

        # Turn 5: Ask about AMBIC
        plan_5 = session.plan(
            "여기서 AMBIC가 무엇인지 설명해줄 수 있어?",
            language="ko",
            turn_id=5,
            generation=1,
        )
        self.assertEqual(plan_5.action, CuratedProtocolAction.RELATED_QUESTION)
        self.assertIn("ambic", plan_5.requested_entities)
        self.assertIn("AMBIC", plan_5.display_text)
        self.assertFalse(plan_5.state_changed)
        self.assertEqual(session.current_index, 0)

        # Turn 6: Ask for related picture without saying "AMBIC"
        plan_6 = session.plan(
            "혹시 관련 사진을 보여줄 수 있어?",
            language="ko",
            turn_id=6,
            generation=1,
        )
        self.assertEqual(plan_6.action, CuratedProtocolAction.VISUAL_REQUEST)
        self.assertIn("ambic", plan_6.requested_entities)
        # Speech and display text must NOT be Step 1 generic instructions!
        self.assertNotIn("작은 1 mm³ 플러그를 자르거나", plan_6.speech_text)
        self.assertIn("AMBIC", plan_6.display_text)
        self.assertFalse(plan_6.state_changed)
        self.assertEqual(session.current_index, 0)

        # Verify PubChem chemistry adapter resolves AMBIC 2D structure
        adapter = PubChemChemistryAdapter()
        match = asyncio.run(adapter.lookup("ambic"))
        self.assertIsNotNone(match)
        self.assertEqual(match["cid"], 14013)
        self.assertIn("pubchem.ncbi.nlm.nih.gov", match["image_url"])
        self.assertEqual(match["display_mode"], "structure_image")

    def test_turn_7_and_8_hplc_water_explanation_and_visual(self) -> None:
        """Turn 7 (advance to Step 2) -> Turn 8 (HPLC water + visual) resolves water structure."""
        session = self.session()
        session.plan("실험 시작해 줘.", language="ko", turn_id=3, generation=1)
        session.plan("현재 단계를 완료했어.", language="ko", turn_id=7, generation=1)
        self.assertEqual(session.current_index, 1)  # Step 2

        # Turn 8: Ask about HPLC water and visual
        plan_8 = session.plan(
            "여기서 HPLC water가 무엇이며, 혹시 관련 사진을 보여줄 수가 있어?",
            language="ko",
            turn_id=8,
            generation=1,
        )
        self.assertEqual(plan_8.action, CuratedProtocolAction.VISUAL_REQUEST)
        self.assertIn("hplc_water", plan_8.requested_entities)
        self.assertFalse(plan_8.state_changed)
        self.assertEqual(session.current_index, 1)

        # Verify PubChem chemistry adapter resolves Water 2D structure
        adapter = PubChemChemistryAdapter()
        match = asyncio.run(adapter.lookup("hplc_water"))
        self.assertIsNotNone(match)
        self.assertEqual(match["cid"], 962)
        self.assertEqual(match["formula"], "H2O")

    def test_turn_10_and_11_anomaly_triage_choke_point(self) -> None:
        """Turn 10 and 11: Anomaly utterances are caught by high-recall triage rather than general QA or off-topic fallback."""
        session = self.session()
        session.plan("실험 시작해 줘.", language="ko", turn_id=3, generation=1)
        session.plan("현재 단계를 완료했어.", language="ko", turn_id=7, generation=1)
        session.plan("현재 단계를 완료했어.", language="ko", turn_id=9, generation=1)
        self.assertEqual(session.current_index, 2)  # Step 3

        # Turn 10: "어, 갑자기 용액의 색깔이 지금 변경된 것 같아."
        plan_10 = session.plan(
            "어, 갑자기 용액의 색깔이 지금 변경된 것 같아.",
            language="ko",
            turn_id=10,
            generation=1,
        )
        self.assertEqual(plan_10.action, CuratedProtocolAction.REPORT_ANOMALY)
        self.assertTrue(plan_10.reported_anomaly)
        self.assertEqual(plan_10.anomaly_category, "protocol_block")
        self.assertIn("이상 사항", plan_10.speech_text)
        self.assertIn("색", plan_10.speech_text)
        self.assertEqual(session.current_index, 2)
        self.assertIsNotNone(session._pending_anomaly)

        # Turn 11: "지금 약간 이상 상황이 발생한 것 같아요."
        plan_11 = session.plan(
            "지금 약간 이상 상황이 발생한 것 같아요.",
            language="ko",
            turn_id=11,
            generation=1,
        )
        self.assertEqual(plan_11.action, CuratedProtocolAction.REPORT_ANOMALY)
        self.assertTrue(plan_11.reported_anomaly)
        self.assertIn("이상 사항", plan_11.speech_text)
        self.assertEqual(session.current_index, 2)

    def test_procedure_state_single_run_consistency(self) -> None:
        """Procedure state remains attached across all turns, never unattached."""
        session = self.session()
        init_state = session.state()
        self.assertTrue(init_state["attached"])
        self.assertFalse(init_state["active"])

        session.plan("실험 시작해 줘.", language="ko", turn_id=1, generation=1)
        start_state = session.state()
        self.assertTrue(start_state["attached"])
        self.assertTrue(start_state["active"])
        self.assertEqual(start_state["current_step_label"], "1")

        # Read-only query does not detach or corrupt state
        session.plan("AMBIC가 뭐야?", language="ko", turn_id=2, generation=1)
        query_state = session.state()
        self.assertTrue(query_state["attached"])
        self.assertTrue(query_state["active"])
        self.assertEqual(query_state["current_step_label"], "1")
        self.assertEqual(query_state["revision"], start_state["revision"])


if __name__ == "__main__":
    unittest.main()
