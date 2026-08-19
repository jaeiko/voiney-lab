import unittest
from voice_workflow_agent.completion_intent import classify_korean_completion_command
from voice_workflow_agent.curated_protocol import classify_curated_control_intent, CuratedProtocolAction


class CompletionIntentTests(unittest.TestCase):
    def test_parameterized_positive_completion_variants(self):
        """Natural Korean completion statements must be recognized and mutate state."""
        positive_utterances = [
            "현재 단계를 완료했어",
            "현재 단계를 완료했어요",
            "현재 단계를 완료했습니다",
            "현재 단계 완료했어",
            "현재 단계 완료했어요",
            "현재 단계 완료했습니다",
            "현재 단계도 완료했어",
            "현재 단계도 완료했어요",
            "현재 단계도 완료했습니다",
            "이번 단계를 완료했어",
            "이번 단계를 완료했어요",
            "이번 단계를 완료했습니다",
            "이번 단계 완료했어",
            "이번 단계 완료했어요",
            "이번 단계 완료했습니다",
            "이번 단계도 완료했어",
            "이번 단계도 완료했어요",
            "이번 단계도 완료했습니다",
            "이 단계를 완료했어",
            "이 단계 완료했어",
            "이 단계도 완료했어",
            "이번 단계 끝냈어",
            "현재 단계 끝냈어요",
            "이번 단계 마쳤습니다",
            "현재 단계 다 했어",
            "현재 단계 완료",
            "이번 단계 완료",
            "현재 단계 완료했어.",
            "이번 단계도 완료했어!",
            "현재 단계도 완료했습니다~",
        ]

        for text in positive_utterances:
            with self.subTest(text=text):
                self.assertTrue(
                    classify_korean_completion_command(text, language="ko"),
                    f"Expected positive completion classification for: {text!r}",
                )

    def test_parameterized_negative_and_question_guards(self):
        """Questions, criteria inquiries, negations, and hypotheticals must NOT mutate."""
        negative_utterances = [
            "현재 단계 완료 조건이 뭐야?",
            "현재 단계 완료했어?",
            "현재 단계 완료했나요?",
            "이번 단계 완료해야 해?",
            "현재 단계를 완료하지 않았어",
            "아직 이번 단계 완료 안 했어",
            "완료하면 다음 단계 알려줘",
            "다음 단계는 완료했어?",
            "2단계 완료 기준 알려줘",
            "현재 단계가 완료된 상태야?",
            "완료라는 게 무슨 뜻이야?",
            "완료 조건 설명해줘",
            "완료하면 어떻게 돼?",
            "아직 안 끝났어",
            "아직 완료 못 했어",
            "완료했다고 치면 다음 단계는 뭐야?",
        ]

        for text in negative_utterances:
            with self.subTest(text=text):
                self.assertFalse(
                    classify_korean_completion_command(text, language="ko"),
                    f"Expected negative/question guard to reject: {text!r}",
                )

    def test_curated_route_classifies_completion_and_advances_intent(self):
        """Curated protocol router must produce NEXT action with reported_completion=True."""
        variants = [
            "현재 단계를 완료했어.",
            "현재 단계도 완료했어.",
            "이번 단계를 완료했어.",
            "이번 단계도 완료했어.",
            "이 단계도 완료했어.",
        ]
        for v in variants:
            with self.subTest(variant=v):
                intent = classify_curated_control_intent(v, language="ko")
                self.assertEqual(intent.action, CuratedProtocolAction.NEXT)
                self.assertTrue(intent.reported_completion)
                self.assertTrue(intent.allows_state_mutation)

    def test_curated_route_guards_questions_from_mutating(self):
        """Curated protocol router must NOT treat questions as completion."""
        intent = classify_curated_control_intent("현재 단계 완료 조건이 뭐야?", language="ko")
        self.assertNotEqual(intent.action, CuratedProtocolAction.NEXT)
        self.assertFalse(intent.allows_state_mutation)


if __name__ == "__main__":
    unittest.main()
