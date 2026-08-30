"""A Korean question gets a Korean answer, and the approved source is preserved.

The product rule these tests describe: the approved protocol revision is
authoritative and often English; a Korean-speaking researcher should not be read
English at a bench; and neither of those may be resolved by paraphrasing an
approved protocol. So the primary answer is Korean when a trustworthy Korean
exists, the exact source is always one disclosure away, and anything that cannot
be shown to preserve the source falls back to the source itself.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from voice_workflow_agent.curated_protocol import (
    CuratedProtocolSession,
    load_curated_protocol_fixture,
)
from voice_workflow_agent.source_presentation import (
    PRESENTATION_LABELS,
    SOURCE_DISCLOSURE_LABEL,
    PresentationTranslationCache,
    SourcePresentationStatus,
    TranslationCacheKey,
    TranslationSettings,
    check_source_preservation,
    looks_korean,
    present_source,
    source_identifiers,
    source_measurements,
    source_ratios,
)


REPOSITORY = Path(__file__).resolve().parents[1]
FIXTURE = REPOSITORY / "data/development_protocols/candidate_a_curated_analysis.json"
PROVENANCE = REPOSITORY / (
    "data/development_protocols/candidate_a_curated_analysis.provenance.json"
)
SOURCE_PDF = REPOSITORY / "data/runtime/candidate-a-source/in-gel-digestion.pdf"

SOURCE = (
    "Prepare two wash solutions: Solution A is 100 mM AMBIC, and Solution B is "
    "50% acetonitrile in 100 mM AMBIC. Incubate for 15 min at 37 C."
)
FAITHFUL_KOREAN = (
    "세척 용액 두 가지를 준비합니다. 용액 A는 100 mM AMBIC이고, 용액 B는 "
    "100 mM AMBIC에 아세토나이트릴 50%를 섞은 것입니다. 37 C에서 15 min 배양합니다."
)


class SourcePreservationTests(unittest.TestCase):
    def test_numbers_units_and_identifiers_are_extracted_from_the_source(self):
        self.assertIn("100 mM", source_measurements(SOURCE))
        self.assertIn("15 min", source_measurements(SOURCE))
        self.assertIn("50 %", source_measurements(SOURCE))
        self.assertIn("AMBIC", source_identifiers(SOURCE))

    def test_a_faithful_translation_passes(self):
        self.assertTrue(check_source_preservation(SOURCE, FAITHFUL_KOREAN).preserved)

    def test_a_changed_concentration_is_caught(self):
        altered = FAITHFUL_KOREAN.replace("50%", "5%")
        result = check_source_preservation(SOURCE, altered)
        self.assertFalse(result.preserved)
        self.assertEqual(result.reason, "measurement_dropped")

    def test_a_dropped_duration_is_caught(self):
        altered = FAITHFUL_KOREAN.replace("15 min ", "")
        self.assertFalse(check_source_preservation(SOURCE, altered).preserved)

    def test_a_dropped_reagent_name_is_caught(self):
        altered = FAITHFUL_KOREAN.replace("AMBIC", "완충액")
        result = check_source_preservation(SOURCE, altered)
        self.assertFalse(result.preserved)
        self.assertEqual(result.reason, "identifier_dropped")
        self.assertIn("AMBIC", result.missing_identifiers)

    def test_altering_one_of_two_identical_measurements_is_caught(self):
        """Presence is not enough: the source states 100 mM twice."""

        altered = FAITHFUL_KOREAN.replace("100 mM AMBIC이고", "100 M AMBIC이고")
        result = check_source_preservation(SOURCE, altered)
        self.assertFalse(result.preserved)
        self.assertEqual(result.reason, "measurement_dropped")

    def test_spacing_and_letter_case_may_differ_but_the_unit_may_not(self):
        self.assertTrue(check_source_preservation("Add 5 mL.", "5mL 넣습니다.").preserved)
        self.assertTrue(check_source_preservation("Add 5 mL.", "5 ML 넣습니다.").preserved)
        self.assertFalse(check_source_preservation("Add 5 mL.", "5 L 넣습니다.").preserved)

    def test_an_explicit_ratio_must_keep_its_order(self):
        source = "Mix Solution A at 50:49:1 and Solution B at 2 parts to 1 part."
        self.assertEqual(source_ratios(source), (("50", "49", "1"), ("2", "1")))
        faithful = "Solution A를 50:49:1로, Solution B를 2 대 1로 혼합합니다."
        self.assertTrue(check_source_preservation(source, faithful).preserved)
        altered = "Solution A를 50:49:1로, Solution B를 1 대 2로 혼합합니다."
        self.assertEqual(
            check_source_preservation(source, altered).reason,
            "ratio_dropped",
        )

    def test_protocol_material_and_equipment_tokens_can_be_required(self):
        source = "Add acetonitrile to the Eppendorf Thermomixer."
        result = check_source_preservation(
            source,
            "acetonitrile을 장비에 넣습니다.",
            stable_tokens=("acetonitrile", "Eppendorf", "Thermomixer"),
        )
        self.assertEqual(result.reason, "stable_token_dropped")
        self.assertIn("Eppendorf", result.missing_stable_tokens)

    def test_korean_detection_catches_an_echoed_english_answer(self):
        self.assertTrue(looks_korean(FAITHFUL_KOREAN))
        self.assertFalse(looks_korean(SOURCE))


class PresentationBoundaryTests(unittest.TestCase):
    def test_a_reviewer_approved_sidecar_is_used_and_labelled_as_verified(self):
        presentation = present_source(
            language="ko", source_text=SOURCE,
            verified_translation=FAITHFUL_KOREAN)
        self.assertIs(
            presentation.status, SourcePresentationStatus.VERIFIED_SIDECAR)
        self.assertTrue(presentation.reviewer_approved_translation)
        self.assertIn("검증된", presentation.label)

    def test_without_a_translator_the_exact_source_is_the_answer(self):
        presentation = present_source(language="ko", source_text=SOURCE)
        self.assertIs(presentation.status, SourcePresentationStatus.SOURCE_ONLY)
        self.assertEqual(presentation.primary_text, SOURCE)
        self.assertEqual(presentation.rejection_reason, "translator_unavailable")
        self.assertIn("안전한 자동 한국어 번역", presentation.notice)
        self.assertNotIn(SOURCE, presentation.speech_text())

    def test_a_runtime_translation_is_never_called_verified(self):
        presentation = present_source(
            language="ko", source_text=SOURCE,
            translator=lambda _text: FAITHFUL_KOREAN,
            settings=TranslationSettings(enabled=True))
        self.assertIs(
            presentation.status, SourcePresentationStatus.AUTOMATIC_TRANSLATION)
        self.assertFalse(presentation.reviewer_approved_translation)
        self.assertIn("자동 번역", presentation.label)
        self.assertNotIn("검증된", presentation.label)
        self.assertIn("검토를 거치지 않은 자동 번역", presentation.notice)

    def test_a_development_sidecar_never_claims_reviewer_approval(self):
        presentation = present_source(
            language="ko", source_text=SOURCE,
            development_translation=FAITHFUL_KOREAN,
        )
        self.assertIs(
            presentation.status, SourcePresentationStatus.DEVELOPMENT_SIDECAR,
        )
        self.assertFalse(presentation.reviewer_approved_translation)
        self.assertIn("개발용", presentation.label)
        self.assertNotIn("검증된", presentation.label)

    def test_no_label_except_the_reviewed_one_claims_verification(self):
        for status, label in PRESENTATION_LABELS.items():
            with self.subTest(status=status):
                if status != SourcePresentationStatus.VERIFIED_SIDECAR.value:
                    self.assertNotIn("검증", label)

    def test_a_translation_that_alters_a_number_is_discarded(self):
        presentation = present_source(
            language="ko", source_text=SOURCE,
            translator=lambda _text: FAITHFUL_KOREAN.replace("15 min", "50 min"),
            settings=TranslationSettings(enabled=True))
        self.assertIs(presentation.status, SourcePresentationStatus.SOURCE_ONLY)
        self.assertEqual(presentation.rejection_reason, "measurement_dropped")
        self.assertEqual(presentation.primary_text, SOURCE)

    def test_a_translator_that_echoes_english_is_discarded(self):
        presentation = present_source(
            language="ko", source_text=SOURCE,
            translator=lambda text: text,
            settings=TranslationSettings(enabled=True))
        self.assertEqual(
            presentation.rejection_reason, "translation_not_korean")

    def test_a_translator_that_returns_nothing_is_discarded(self):
        presentation = present_source(
            language="ko", source_text=SOURCE, translator=lambda _text: "",
            settings=TranslationSettings(enabled=True))
        self.assertEqual(presentation.rejection_reason, "empty_translation")

    def test_safety_critical_ambiguity_never_reaches_a_translator(self):
        def forbidden(_text: str) -> str:
            raise AssertionError(
                "a safety-critical answer must never be generated")

        presentation = present_source(
            language="ko", source_text=SOURCE, translator=forbidden,
            settings=TranslationSettings(enabled=True), safety_critical=True)
        self.assertIs(presentation.status, SourcePresentationStatus.SOURCE_ONLY)
        self.assertEqual(
            presentation.rejection_reason, "safety_critical_fail_closed")

    def test_oversized_source_is_refused_rather_than_summarised(self):
        presentation = present_source(
            language="ko", source_text="A" * 5000,
            translator=lambda _text: "요약",
            settings=TranslationSettings(enabled=True))
        self.assertEqual(presentation.rejection_reason, "source_too_long")

    def test_the_exact_source_is_always_available_behind_a_disclosure(self):
        for verified in (FAITHFUL_KOREAN, None):
            with self.subTest(verified=bool(verified)):
                presentation = present_source(
                    language="ko", source_text=SOURCE,
                    verified_translation=verified)
                display = presentation.display_text()
                self.assertIn(SOURCE, display)
                if verified:
                    self.assertIn(SOURCE_DISCLOSURE_LABEL, display)

    def test_spoken_text_carries_the_answer_not_the_source_block(self):
        presentation = present_source(
            language="ko", source_text=SOURCE,
            verified_translation=FAITHFUL_KOREAN)
        speech = presentation.speech_text("현재 단계는 변경하지 않았습니다.")
        self.assertIn(FAITHFUL_KOREAN, speech)
        self.assertNotIn(SOURCE_DISCLOSURE_LABEL, speech)

    def test_an_english_answer_is_left_alone(self):
        presentation = present_source(language="en", source_text=SOURCE)
        self.assertIs(
            presentation.status, SourcePresentationStatus.SOURCE_LANGUAGE)
        self.assertEqual(presentation.primary_text, SOURCE)

    def test_translation_is_on_by_default_and_can_be_explicitly_disabled(self):
        self.assertTrue(TranslationSettings.from_environment({}).enabled)
        self.assertFalse(
            TranslationSettings.from_environment(
                {"VOICE_WORKFLOW_AGENT_PRESENTATION_TRANSLATION_ENABLED": "0"}
            ).enabled)

    def test_successful_immutable_source_translation_is_cached(self):
        calls = 0

        def translate(_source: str) -> str:
            nonlocal calls
            calls += 1
            return FAITHFUL_KOREAN

        cache = PresentationTranslationCache(maximum_entries=4)
        key = TranslationCacheKey.for_source(
            protocol_revision_id="revision-4",
            source_document_sha256="a" * 64,
            step_id="step-2/current_step",
            source_text=SOURCE,
            target_language="ko",
            model="fake-translator",
        )
        for _ in range(2):
            presentation = present_source(
                language="ko", source_text=SOURCE, translator=translate,
                settings=TranslationSettings(enabled=True), cache_key=key,
                cache=cache,
            )
            self.assertIs(
                presentation.status,
                SourcePresentationStatus.AUTOMATIC_TRANSLATION,
            )
        self.assertEqual(calls, 1)
        self.assertEqual(len(cache), 1)

    def test_provider_failure_is_visible_and_non_mutating(self):
        def fail(_source: str) -> str:
            raise RuntimeError("synthetic provider failure")

        presentation = present_source(
            language="ko", source_text=SOURCE, translator=fail,
            settings=TranslationSettings(enabled=True),
        )
        self.assertEqual(presentation.rejection_reason, "translation_failed")
        self.assertEqual(presentation.source_text, SOURCE)
        self.assertNotIn(SOURCE, presentation.speech_text())


class KoreanNextStepPreviewTests(unittest.TestCase):
    """Phase 4A: "다음 단계 알려줘" previews in Korean and changes nothing."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = load_curated_protocol_fixture(
            FIXTURE, PROVENANCE, SOURCE_PDF)

    def session(self, label: str = "2", **overrides) -> CuratedProtocolSession:
        session = CuratedProtocolSession(self.fixture, **overrides)
        session.active = True
        session._workflow_status = "active"
        session.current_index = next(
            index for index, step in enumerate(self.fixture.steps)
            if step.source_label == label
        )
        return session

    def preview(self, session: CuratedProtocolSession):
        return session.plan(
            "다음 단계 알려줘", turn_id=1, language="ko",
            configuration_id=1, generation=0)

    def test_the_spoken_answer_is_korean_and_states_that_nothing_moved(self):
        session = self.session("2")
        before = (session.current_index, session._revision)
        plan = self.preview(session)
        self.assertFalse(plan.state_changed)
        self.assertTrue(looks_korean(plan.speech_text))
        self.assertIn("다음 단계는 3단계입니다", plan.speech_text)
        self.assertIn("실험 상태는 변경하지 않았습니다", plan.speech_text)
        self.assertEqual((session.current_index, session._revision), before)

    def test_the_approved_source_stays_available_on_screen(self):
        session = self.session("2")
        plan = self.preview(session)
        source = self.fixture.steps[session.current_index + 1]
        self.assertIn(SOURCE_DISCLOSURE_LABEL, plan.display_text)
        self.assertIn(source.instruction_source_text[:40], plan.display_text)

    def test_the_development_sidecar_is_used_without_claiming_review(self):
        session = self.session("2")
        plan = self.preview(session)
        self.assertEqual(plan.translation_status, "development_sidecar")
        self.assertIn("개발용 한국어 번역", plan.display_text)
        self.assertNotIn("검증된 한국어 번역", plan.display_text)

    def test_a_step_without_a_sidecar_shows_the_source_and_says_so(self):
        """The gap this pass closes: English no longer masquerades as Korean."""

        session = self.session("2")
        session._localized_fact = lambda *_args, **_kwargs: None
        plan = self.preview(session)
        self.assertEqual(plan.translation_status, "source_only")
        self.assertIn("안전한 자동 한국어 번역", plan.display_text)
        self.assertIn("다음 단계는 3단계입니다", plan.speech_text)
        self.assertNotIn(
            self.fixture.steps[session.current_index + 1].instruction_source_text,
            plan.speech_text,
        )
        self.assertFalse(plan.state_changed)

    def test_an_enabled_translator_preserves_every_number_in_the_source(self):
        session = self.session("2", translation_settings=TranslationSettings(
            enabled=True))
        source = self.fixture.steps[session.current_index + 1]
        session._localized_fact = lambda *_args, **_kwargs: None
        session.presentation_translator = (
            lambda text: "다음 작업을 수행합니다. " + " ".join(
                source_measurements(text)) + " "
            + " ".join(source_identifiers(text)))
        plan = self.preview(session)
        self.assertEqual(plan.translation_status, "automatic_translation")
        for measurement in source_measurements(source.instruction_source_text):
            self.assertIn(measurement.split(" ")[0], plan.display_text)
        self.assertFalse(plan.state_changed)

    def test_a_translator_that_invents_content_is_rejected(self):
        session = self.session("2", translation_settings=TranslationSettings(
            enabled=True))
        session._localized_fact = lambda *_args, **_kwargs: None
        session.presentation_translator = (
            lambda _text: "완료 조건은 용액이 완전히 투명해질 때까지입니다.")
        plan = self.preview(session)
        # Invented completion criteria drop the source's own measurements, so
        # the preservation check discards the answer before anyone hears it.
        self.assertEqual(plan.translation_status, "source_only")
        self.assertNotIn("완전히 투명해질 때까지", plan.display_text)

    def test_normal_completion_advances_once_and_presents_the_new_step_in_korean(self):
        session = self.session("1", translation_settings=TranslationSettings(
            enabled=True,
        ))
        target = self.fixture.steps[1]
        automatic = self.fixture.localized_fact(target.step_id, "current_step")
        self.assertIsNotNone(automatic)
        session._localized_fact = lambda *_args, **_kwargs: None
        session.presentation_translator = lambda _source: automatic
        before_revision = session._revision
        plan = session.plan(
            "완료됐어요", turn_id=1, language="ko",
            configuration_id=1, generation=0,
        )
        self.assertTrue(plan.state_changed)
        self.assertEqual(session.current_index, 1)
        self.assertEqual(session._revision, before_revision + 1)
        self.assertEqual(plan.translation_status, "automatic_translation")
        self.assertIn("1단계를 완료했습니다", plan.speech_text)
        self.assertIn("2단계", plan.speech_text)
        self.assertIn("자동 번역", plan.display_text)
        self.assertIn(SOURCE_DISCLOSURE_LABEL, plan.display_text)

    def test_the_final_step_preview_still_changes_nothing(self):
        last = self.fixture.steps[-1].source_label
        session = self.session(last)
        before = (session.current_index, session._revision)
        plan = self.preview(session)
        self.assertFalse(plan.state_changed)
        self.assertIn("마지막 단계", plan.speech_text)
        self.assertEqual((session.current_index, session._revision), before)


if __name__ == "__main__":  # pragma: no cover - manual runs only
    unittest.main()
