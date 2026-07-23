import unittest

from safebridge_voice.emergency import (
    ENGLISH_EMERGENCY_RESPONSE,
    KOREAN_EMERGENCY_RESPONSE,
    normalize_emergency_text,
    recognize_emergency,
)


class EmergencyRecognizerTests(unittest.TestCase):
    def test_approved_fixed_responses_are_exact(self):
        self.assertEqual(
            KOREAN_EMERGENCY_RESPONSE,
            "즉시 작업을 멈추고 위험 구역에서 벗어나세요. 현장 안전관리자 또는 기존 비상 연락 절차를 통해 "
            "즉시 도움을 요청하세요. 이 응답만을 근거로 작업을 재개하지 마세요.",
        )
        self.assertEqual(
            ENGLISH_EMERGENCY_RESPONSE,
            "Stop work immediately and move away from the hazard. Immediately contact the "
            "on-site safety manager or use the established emergency contact procedure. "
            "Do not resume work based on this response.",
        )

    def assert_match(self, text, language, response):
        match = recognize_emergency(text)
        self.assertIsNotNone(match)
        self.assertEqual((match.language, match.response), (language, response))

    def test_clear_korean_distress_and_current_events(self):
        for text in ("도와줘!", "도와주세요!", "불이 났어요! 도와주세요!",
                     "화재가 발생하고 있습니다.", "폭발이 일어났어요!", "폭발했어요!",
                     "가스 누출이 발생하고 있습니다.", "사람이 크게 다쳤어요.",
                     "지금 즉시 위험한 상황입니다."):
            with self.subTest(text=text):
                self.assert_match(text, "ko", KOREAN_EMERGENCY_RESPONSE)

    def test_clear_english_distress_and_current_events(self):
        for text in ("Emergency!", "Help!", "Help, there is a fire.",
                     "Help! There is a fire!", "There’s a fire!",
                     "There is a fire right now!", "An explosion just happened.",
                     "An explosion is happening.", "There was an explosion just now.",
                     "There is an active gas leak now.",
                     "Someone is seriously injured.", "We are in immediate danger."):
            with self.subTest(text=text):
                self.assert_match(text, "en", ENGLISH_EMERGENCY_RESPONSE)

    def test_case_whitespace_unicode_and_terminal_punctuation(self):
        self.assertEqual(normalize_emergency_text("  ＥＭＥＲＧＥＮＣＹ？！  "), "emergency")
        self.assert_match("  HELP,   THERE IS A FIRE!!! ", "en", ENGLISH_EMERGENCY_RESPONSE)
        self.assert_match("  도와줘？！ ", "ko", KOREAN_EMERGENCY_RESPONSE)

    def test_informational_questions_are_not_emergencies(self):
        cases = (
            "비상 대응 절차를 알려 주세요.",
            "누출이 발생하면 어떻게 해야 하나요?",
            "화재가 발생하면 어떻게 해야 하나요?",
            "누출이 발생할 경우 무엇을 해야 하나요?",
            "비상 샤워를 설명해 주세요.",
            "What is the emergency procedure?",
            "What should I do if a spill occurs?",
            "What should I do if a fire occurs?",
            "How should we respond if there is a spill?",
            "Please explain the emergency shower.",
        )
        for text in cases:
            with self.subTest(text=text):
                self.assertIsNone(recognize_emergency(text))

    def test_current_emergency_may_be_followed_by_help_question(self):
        cases = (
            ("불이 났어요. 어떻게 해야 해요?", "ko", KOREAN_EMERGENCY_RESPONSE),
            ("가스 누출이 발생하고 있습니다. 무엇을 해야 하나요?", "ko",
             KOREAN_EMERGENCY_RESPONSE),
            ("There is a fire. What should I do?", "en", ENGLISH_EMERGENCY_RESPONSE),
            ("There is an active gas leak. How do we get help?", "en",
             ENGLISH_EMERGENCY_RESPONSE),
        )
        for text, language, response in cases:
            with self.subTest(text=text):
                self.assert_match(text, language, response)

    def test_ambiguous_past_explosions_are_not_emergencies(self):
        for text in ("An explosion happened.", "An explosion occurred."):
            with self.subTest(text=text):
                self.assertIsNone(recognize_emergency(text))

    def test_hypothetical_historical_and_identifiers_are_not_emergencies(self):
        cases = (
            "만약 화재가 발생하면 대피합니다.",
            "어제 폭발이 발생했습니다.",
            "If there is a fire, use the exit.",
            "There was a fire yesterday.",
            "Someone was seriously injured.",
            "Acetone", "Emergency Shower 2000", "ABC-FIRE-7", "67-64-1", "SDS-123",
        )
        for text in cases:
            with self.subTest(text=text):
                self.assertIsNone(recognize_emergency(text))

    def test_broad_substrings_do_not_create_false_positives(self):
        for text in ("fire extinguisher", "help menu", "emergency response plan",
                     "누출 방지 키트", "화재 예방 교육", "도와주는 장치"):
            with self.subTest(text=text):
                self.assertIsNone(recognize_emergency(text))


if __name__ == "__main__":
    unittest.main()
