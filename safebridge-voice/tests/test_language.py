import unittest

from safebridge_voice.language import (
    Transcription,
    normalize_provider_language,
    resolve_turn_language,
)


class LanguageTests(unittest.TestCase):
    def test_provider_language_normalization_is_explicit(self):
        for value, expected in (
            ("Korean", "ko"), ("English", "en"), ("Vietnamese", "vi"),
            ("ko-KR", "ko"), ("en_US", "en"), ("vi_VN", "vi"),
        ):
            self.assertEqual(normalize_provider_language(value), expected)
        for value in (None, "", 7, {}, "Japanese", "Klingon"):
            self.assertIsNone(normalize_provider_language(value))

    def test_transcription_has_no_fabricated_confidence(self):
        result = Transcription("hello", "en")
        self.assertFalse(hasattr(result, "confidence"))
        self.assertFalse(hasattr(result, "language_confidence"))

    def test_manual_mode_is_authoritative(self):
        result = resolve_turn_language(
            "Please show the approved spill procedure.", "en",
            mode="manual", manual_language="ko",
        )
        self.assertEqual(result.language, "ko")

    def test_product_labels_do_not_override_provider_dominant_language(self):
        korean = resolve_turn_language(
            "Acetone 누출 절차를 알려 주세요.", "ko", mode="auto")
        english = resolve_turn_language(
            "How should I handle 가상용제 safely?", "en", mode="auto")
        self.assertEqual(korean.language, "ko")
        self.assertEqual(english.language, "en")

    def test_uncertain_results_require_confirmation(self):
        cases = (
            ("ABC-123", "en", "insufficient_language_content"),
            ("Please help with this spill.", None, "language_unresolved"),
            ("Please help with this spill.", "ja", "language_unresolved"),
            ("누출됐어요. How should I clean this spill?", "en", "ambiguous_mixed_language"),
            ("Please show the approved procedure.", "ko", "provider_transcript_contradiction"),
        )
        for text, detected, reason in cases:
            with self.subTest(text=text, detected=detected):
                result = resolve_turn_language(text, detected, mode="auto")
                self.assertFalse(result.resolved)
                self.assertEqual(result.reason, reason)

    def test_single_chemical_product_code_acronym_and_cas_are_neutral(self):
        for text, detected in (
            ("아세톤", "ko"), ("메탄올", "ko"), ("Acetone", "en"),
            ("ABC-123", "en"), ("111-11-1", "en"), ("SDS", "en"),
        ):
            with self.subTest(text=text):
                result = resolve_turn_language(text, detected, mode="auto")
                self.assertFalse(result.resolved)
                self.assertEqual(result.reason, "insufficient_language_content")

    def test_complete_requests_still_resolve(self):
        for text, detected in (
            ("아세톤 취급 절차를 알려 주세요.", "ko"),
            ("Please show the approved Acetone procedure.", "en"),
            ("Xin cho biết quy trình xử lý hóa chất.", "vi"),
        ):
            with self.subTest(detected=detected):
                self.assertEqual(
                    resolve_turn_language(text, detected, mode="auto").language,
                    detected,
                )


if __name__ == "__main__":
    unittest.main()
