from pathlib import Path
import unittest

from voice_workflow_agent.curated_protocol import (
    CuratedProtocolSession,
    load_curated_protocol_fixture,
)
from voice_workflow_agent.language import (
    Transcription,
    classify_input_event,
    _is_keyterm_echo,
)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "development_protocols"
SOURCE_PDF = Path("/home/student/protocol-test-files/in-gel-digestion.pdf")


class TranscriptAdmissionGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = load_curated_protocol_fixture(
            DATA / "candidate_a_curated_analysis.json",
            DATA / "candidate_a_curated_analysis.provenance.json",
            SOURCE_PDF,
        )
        self.session = CuratedProtocolSession(self.fixture)
        self.keyterms = self.session.stt_keyterms()

    def test_verbatim_vocabulary_dump_is_rejected(self) -> None:
        raw_dump = (
            "프로토콜 시작. 현재 단계, 다음 단계, 다음 단계 미리보기, 완료, "
            "현재 단계 완료, 완료했어요, 다 했어요, 완료 조건, 네, 아니요, "
            "다시 알려줘, 프로토콜 중단, 이상 사항 기록, 관찰 결과, "
            "완전히 탈색, 투명해요, 투명한가요, 흰색으로 변했어요, 아직 색이 남아 있어요."
        )
        t = Transcription(text=raw_dump, detected_language="ko", duration_seconds=4.2)
        decision = classify_input_event(t, keyterms=self.keyterms)
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason, "keyterm_echo")
        self.assertTrue(_is_keyterm_echo(t, keyterms=self.keyterms))

    def test_short_concatenated_keyterm_echo_is_rejected(self) -> None:
        short_dump = "프로토콜 시작, 현재 단계, 다음 단계, 완료 조건, 다시 알려줘."
        t = Transcription(text=short_dump, detected_language="ko", duration_seconds=2.1)
        decision = classify_input_event(t, keyterms=self.keyterms)
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason, "keyterm_echo")

    def test_english_stt_bias_dumps_are_rejected(self) -> None:
        dump1 = (
            "Pro protocol start, current stage, next stage, next stage preview, complete, "
            "current stage complete, I did it, complete condition, yes, no, tell me again, "
            "protocol interruption, abnormality record, observation result, "
            "completely decontaminated, transparent, transparent? White changed, color is still there."
        )
        t1 = Transcription(text=dump1, detected_language="en", duration_seconds=5.0)
        decision1 = classify_input_event(t1, keyterms=self.keyterms)
        self.assertFalse(decision1.accepted)
        self.assertEqual(decision1.reason, "keyterm_echo")
        self.assertTrue(_is_keyterm_echo(t1, keyterms=self.keyterms))

        dump2 = (
            "Proton start, current step, next step, next step preview, complete, "
            "current step complete, I did it, complete condition, yes, no, tell me again, "
            "protocol interruption, anomaly record, observation result, "
            "completely decontaminated, transparent, transparent? White changed, color is still there."
        )
        t2 = Transcription(text=dump2, detected_language="en", duration_seconds=5.0)
        decision2 = classify_input_event(t2, keyterms=self.keyterms)
        self.assertFalse(decision2.accepted)
        self.assertEqual(decision2.reason, "keyterm_echo")
        self.assertTrue(_is_keyterm_echo(t2, keyterms=self.keyterms))

        dump3 = "protocol start, current step, next step, complete, yes, no, transparent..."
        t3 = Transcription(text=dump3, detected_language="en", duration_seconds=2.0)
        decision3 = classify_input_event(t3, keyterms=self.keyterms)
        self.assertFalse(decision3.accepted)
        self.assertEqual(decision3.reason, "keyterm_echo")
        self.assertTrue(_is_keyterm_echo(t3, keyterms=self.keyterms))

    def test_authentic_natural_commands_are_admitted(self) -> None:
        authentic_commands = (
            "실험 시작해 줘.",
            "현재 단계를 완료했어.",
            "현재 단계 완료했고 다음 단계도 알려줘.",
            "완료했어요.",
            "다음 단계로 가자.",
            "현재 단계 다시 알려줘.",
            "완료 조건이 뭐야?",
            "여기서 AMBIC가 무엇인지 설명해줄 수 있어?",
            "혹시 관련 사진을 보여줄 수 있어?",
            "HPLC water와 acetonitrile의 차이점이 뭐야?",
            "어, 갑자기 용액의 색깔이 지금 변경된 것 같아.",
            "지금 약간 이상 상황이 발생한 것 같아요.",
            "젤이 완전히 탈색되어 투명해요",
            "젤이 완전히 탈색되어 투명해졌어.",
            "2단계부터 7단계까지 설명해줘.",
            "프로토콜을 중단해줘.",
        )
        for cmd in authentic_commands:
            t = Transcription(text=cmd, detected_language="ko", duration_seconds=1.5)
            decision = classify_input_event(t, keyterms=self.keyterms)
            self.assertTrue(
                decision.accepted,
                f"Authentic command was incorrectly rejected: {cmd} (reason: {decision.reason})"
            )
            self.assertFalse(_is_keyterm_echo(t, keyterms=self.keyterms))


if __name__ == "__main__":
    unittest.main()
