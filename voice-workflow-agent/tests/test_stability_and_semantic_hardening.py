"""Deterministic regression tests for voice workflow agent stability and semantic hardening."""

from pathlib import Path
import unittest

from voice_workflow_agent.curated_protocol import (
    CuratedProtocolAction,
    CuratedProtocolSession,
    CuratedProtocolSpeechMode,
    load_curated_protocol_fixture,
    _observation_predicate,
    _extract_step_range,
    classify_curated_control_intent,
    resolve_question_focus,
)
from voice_workflow_agent.external_references import _canonical_url
from voice_workflow_agent.language import (
    Transcription,
    classify_input_event,
    _is_keyterm_echo,
)
from voice_workflow_agent.server import ListenerSession
from voice_workflow_agent.vad import (
    EndpointDetector,
    TurnState,
    VadConfig,
)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "development_protocols"
SOURCE_PDF = Path("/home/student/protocol-test-files/in-gel-digestion.pdf")


FRAME_BYTES = 640


def frame(n: int = 1) -> bytes:
    return bytes([n % 256]) * FRAME_BYTES


class Decisions:
    def __init__(self, outcomes: list[bool]) -> None:
        self.outcomes = list(outcomes)
        self.index = 0

    def __call__(self, _: bytes) -> bool:
        if self.index < len(self.outcomes):
            res = self.outcomes[self.index]
            self.index += 1
            return res
        return False


class StabilityAndSemanticHardeningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = load_curated_protocol_fixture(
            DATA / "candidate_a_curated_analysis.json",
            DATA / "candidate_a_curated_analysis.provenance.json",
            SOURCE_PDF,
        )
        self.session = CuratedProtocolSession(self.fixture)

    def test_barge_in_preserves_speech_when_stale_playback_ended_arrives(self) -> None:
        """Barge-in speech must not be wiped out by an interrupted turn's playback.ended."""
        config = VadConfig(
            playback_onset_voiced_frames=6,
            playback_onset_window_frames=8,
            endpoint_silence_frames=3,
            minimum_voiced_frames=4,
            prefix_frames=5,
            cooldown_ms=0,
        )
        decisions = [True] * 12 + [False] * 4
        speech = ListenerSession(
            EndpointDetector(config, classifier=Decisions(decisions))
        )
        speech.start()
        speech.active_turn_id = 1
        speech.turn_generations[1] = speech.generation
        speech.detector.state = TurnState.PROCESSING
        self.assertTrue(speech.start_playback(1))

        # 1. Deliver voiced frames during playback -> emits barge_in_candidate
        events1 = speech.accept_chunk(b"".join(frame(20 + i) for i in range(12)))
        self.assertEqual([item.kind for item in events1], ["barge_in_candidate"])
        self.assertIsNotNone(speech._interrupt_candidate_identity)
        self.assertEqual(speech._interrupt_candidate_identity, (1, 1))

        # 2. Client delivers delayed playback.ended for Turn 1 while candidate is active in flight
        # This MUST return False and NOT reset the interrupt detector or candidate!
        ended_result = speech.playback_ended(1)
        self.assertFalse(ended_result)
        self.assertIsNotNone(speech._interrupt_candidate_identity)
        self.assertEqual(speech._interrupt_candidate_identity, (1, 1))

        # 3. Trailing silence frames finish the utterance and emit barge_in_audio_ready
        events2 = speech.accept_chunk(b"".join(frame(40 + i) for i in range(4)))
        self.assertEqual([item.kind for item in events2], ["barge_in_audio_ready"])

        # 4. Commit candidate emits assistant.interrupted and the new turn's speech events
        committed = speech.commit_interrupt_candidate(events2[0])
        self.assertTrue(any(item.kind == "assistant.interrupted" for item in committed))
        self.assertTrue(any(item.kind == "speech.start" for item in committed))
        self.assertTrue(any(item.kind == "speech.end" for item in committed))
        self.assertEqual(speech.state, TurnState.PROCESSING)

    def test_stt_bias_dumps_are_rejected_and_pruned_keyterms_contain_no_commands(self) -> None:
        """Verify stt_keyterms contains only domain technical nouns, and catalogs are rejected."""
        keyterms = self.session.stt_keyterms()
        # Verify no workflow command sentences in keyterms
        for forbidden in ("프로토콜 시작", "현재 단계", "완료", "투명해요", "흰색으로 변했어요"):
            self.assertNotIn(forbidden, keyterms)
        for expected in ("AMBIC", "HPLC water", "DTT", "iodoacetamide", "trypsin"):
            self.assertIn(expected, keyterms)

        # Test rejection of English translation dumps
        dump_en = (
            "Proton start, current step, next step, next step preview, complete, "
            "current step complete, I did it, complete condition, yes, no, tell me again, "
            "protocol interruption, anomaly record, observation result, "
            "completely decontaminated, transparent, transparent? White changed, color is still there."
        )
        t_en = Transcription(text=dump_en, detected_language="en", duration_seconds=4.5)
        self.assertFalse(classify_input_event(t_en, keyterms=keyterms).accepted)
        self.assertTrue(_is_keyterm_echo(t_en, keyterms=keyterms))

    def test_general_lab_qa_tube_question_preserves_state(self) -> None:
        """Questions about tube or general labware should provide grounded answers without mutating step."""
        # Fast-forward to step 4
        self.session.plan("프로토콜 시작", turn_id=1, language="ko")
        self.session.plan("현재 단계를 완료했어", turn_id=2, language="ko")
        self.session.plan("현재 단계를 완료했어", turn_id=3, language="ko")
        self.session.plan("현재 단계를 완료했어", turn_id=4, language="ko")
        self.assertEqual(self.fixture.steps[self.session.current_index].source_label, "4")

        # 1. Ask about tube: "튜브가 무엇인지 설명해줄 수 있어?"
        plan1 = self.session.plan("튜브가 무엇인지 설명해줄 수 있어?", turn_id=10, language="ko")
        self.assertEqual(plan1.action, CuratedProtocolAction.LAB_DOMAIN_QA)
        self.assertIn("1.5 mL", plan1.display_text)
        self.assertIn("튜브", plan1.display_text)
        self.assertFalse(plan1.state_changed)
        self.assertEqual(self.fixture.steps[self.session.current_index].source_label, "4")

        # 2. Ask: "젤 밴드가 들어있는 튜브에 대해서 설명해 줄 수 있어?"
        plan2 = self.session.plan("젤 밴드가 들어있는 튜브에 대해서 설명해 줄 수 있어?", turn_id=11, language="ko")
        self.assertEqual(plan2.step_label, "4")
        self.assertIn("튜브", plan2.display_text)
        self.assertIn("1.5 mL", plan2.display_text)
        self.assertNotIn("염색된 단백질 밴드는 SDS-PAGE", plan2.display_text)
        self.assertFalse(plan2.state_changed)
        self.assertEqual(self.fixture.steps[self.session.current_index].source_label, "4")

    def test_step_range_queries_summarize_ordered_steps_without_mutation(self) -> None:
        """Step range queries like '2단계부터 7단계까지' must summarize sequentially without state changes."""
        self.session.plan("프로토콜 시작", turn_id=1, language="ko")  # At step 1
        self.session.plan("현재 단계를 완료했어", turn_id=2, language="ko")  # At step 2
        self.assertEqual(self.fixture.steps[self.session.current_index].source_label, "2")

        # Test extraction
        self.assertEqual(_extract_step_range("2단계부터 7단계까지 설명해줘", current_step=2, max_step=25), (2, 7))
        self.assertEqual(_extract_step_range("이 단계부터 7단계까지 알려줘", current_step=2, max_step=25), (2, 7))
        self.assertEqual(_extract_step_range("3단계부터 마지막 단계까지 설명해줘", current_step=2, max_step=25), (3, 25))

        # Test session planning
        plan = self.session.plan("2단계부터 7단계까지 설명해줘", turn_id=12, language="ko")
        self.assertEqual(plan.action, CuratedProtocolAction.STEP_RANGE)
        self.assertFalse(plan.state_changed)
        self.assertIn("2단계부터 7단계까지", plan.display_text)
        self.assertIn("• 2단계:", plan.display_text)
        self.assertIn("• 7단계:", plan.display_text)
        self.assertEqual(self.fixture.steps[self.session.current_index].source_label, "2")

    def test_step_7_observation_criterion_survives_intervening_qa(self) -> None:
        """At Step 7, an assertive observation must satisfy completion even after intervening QA."""
        # Fast-forward to step 7
        self.session.plan("프로토콜 시작", turn_id=1, language="ko")
        for tid in range(2, 8):
            self.session.plan("현재 단계를 완료했어", turn_id=tid, language="ko")
        self.assertEqual(self.fixture.steps[self.session.current_index].source_label, "7")

        # 1. Ask intervening QA
        plan_qa = self.session.plan("AMBIC가 무엇인지 설명해줘", turn_id=20, language="ko")
        self.assertFalse(plan_qa.state_changed)
        self.assertEqual(self.fixture.steps[self.session.current_index].source_label, "7")

        # 2. Ask question about criterion (guard check: should not advance)
        self.assertIsNone(_observation_predicate("7", "완전히 투명한가요?"))
        plan_q = self.session.plan("완전히 투명한가요?", turn_id=21, language="ko")
        self.assertFalse(plan_q.state_changed)
        self.assertEqual(self.fixture.steps[self.session.current_index].source_label, "7")

        # 3. Negative observation: "아직 색이 남아 있어요"
        self.assertEqual(_observation_predicate("7", "아직 색이 남아 있어요"), "negative")
        plan_neg = self.session.plan("아직 색이 남아 있어요", turn_id=22, language="ko")
        self.assertFalse(plan_neg.state_changed)
        self.assertEqual(plan_neg.speech_mode, CuratedProtocolSpeechMode.BLOCKED)
        self.assertEqual(self.fixture.steps[self.session.current_index].source_label, "7")

        # 4. Positive observation: "어, 젤이 이제 완전히 투명해졌어" / "젤이 완전히 탈색되어 투명해요"
        self.assertEqual(_observation_predicate("7", "어, 젤이 이제 완전히 투명해졌어"), "positive")
        self.assertEqual(_observation_predicate("7", "젤이 완전히 탈색되어 투명해요"), "positive")
        plan_pos = self.session.plan("젤이 완전히 탈색되어 투명해요", turn_id=23, language="ko")
        self.assertTrue(plan_pos.state_changed)
        self.assertEqual(self.fixture.steps[self.session.current_index].source_label, "8")

    def test_open_external_search_canonical_url_admission(self) -> None:
        """In open mode (empty allowed_domains), any valid HTTPS URL is admitted."""
        open_domains: tuple[str, ...] = ()
        url1 = _canonical_url("https://en.wikipedia.org/wiki/Ammonium_bicarbonate", open_domains)
        self.assertEqual(url1, "https://en.wikipedia.org/wiki/Ammonium_bicarbonate")

    def test_agent_meta_intent_works_in_all_states_including_compound_queries(self) -> None:
        """Agent meta questions must work before protocol start and during active workflow without mutation."""
        meta_utterances = [
            "너는 뭐 하는 애이고, 그리고 어떤 기능을 수행해?",
            "너의 목표와 기능이 뭐야?",
            "너는 누구고 뭘 할 수 있어?",
            "이 에이전트 목적이랑 주요 기능을 설명해줘.",
            "무슨 역할을 하는 시스템이야?",
            "기능을 설명해줘.",
        ]
        # 1. Test before protocol start (inactive state)
        self.assertFalse(self.session.active)
        for idx, utterance in enumerate(meta_utterances, start=1):
            intent = classify_curated_control_intent(utterance, language="ko")
            self.assertEqual(intent.action, CuratedProtocolAction.AGENT_META, f"Failed for {utterance}")
            plan = self.session.plan(utterance, turn_id=idx, language="ko")
            self.assertEqual(plan.action, CuratedProtocolAction.AGENT_META)
            self.assertFalse(plan.state_changed)
            self.assertFalse(self.session.active)
            self.assertIn("보이스 워크플로 에이전트", plan.display_text)

        # 2. Test during active protocol
        self.session.plan("프로토콜 시작", turn_id=10, language="ko")
        self.assertTrue(self.session.active)
        plan_active = self.session.plan("너는 뭐 하는 애이고, 그리고 어떤 기능을 수행해?", turn_id=11, language="ko")
        self.assertEqual(plan_active.action, CuratedProtocolAction.AGENT_META)
        self.assertFalse(plan_active.state_changed)
        self.assertTrue(self.session.active)
        self.assertEqual(self.fixture.steps[self.session.current_index].source_label, "1")

    def test_question_focus_resolution_distinguishes_head_noun_from_relative_modifier(self) -> None:
        """Noun phrase modifiers ('젤 밴드가 들어있는 튜브') must isolate the head noun as focus."""
        # 1. Modifier clause: focus is tube, context is stained_protein_band
        focus, context = resolve_question_focus(
            "여기서 젤 밴드가 들어있는 튜브가 뭐야?",
            ("stained_protein_band", "tube"),
        )
        self.assertEqual(focus, ("tube",))
        self.assertEqual(context, ("stained_protein_band",))

        # 2. Coordination clause: both are focus
        focus_coord, context_coord = resolve_question_focus(
            "AMBIC와 HPLC water의 차이가 뭐야?",
            ("ambic", "hplc_water"),
        )
        self.assertEqual(focus_coord, ("ambic", "hplc_water"))
        self.assertEqual(context_coord, ())

        # 3. Execution plan test: query for tube in Step 1
        self.session.plan("프로토콜 시작", turn_id=1, language="ko")
        plan = self.session.plan("여기서 젤 밴드가 들어있는 튜브가 뭐야?", turn_id=2, language="ko")
        self.assertEqual(plan.action, CuratedProtocolAction.LAB_DOMAIN_QA)
        self.assertFalse(plan.state_changed)
        self.assertIn("마이크로센트리퓨지 튜브", plan.display_text)
        self.assertNotIn("염색된 단백질 밴드는 SDS-PAGE", plan.display_text)

    def test_completion_claims_with_adverbs_and_guards(self) -> None:
        """Adverbial completion claims must advance step, while future/hypothetical/negated must be blocked."""
        self.session.plan("프로토콜 시작", turn_id=1, language="ko")
        self.assertEqual(self.fixture.steps[self.session.current_index].source_label, "1")

        # 1. Guards: future, hypothetical, negative must NOT advance
        plan_future = self.session.plan("이번 단계 미리 완료할게", turn_id=2, language="ko")
        self.assertFalse(plan_future.state_changed)
        self.assertEqual(self.fixture.steps[self.session.current_index].source_label, "1")

        plan_hypo = self.session.plan("미리 완료하면 어떻게 돼?", turn_id=3, language="ko")
        self.assertFalse(plan_hypo.state_changed)
        self.assertEqual(self.fixture.steps[self.session.current_index].source_label, "1")

        plan_neg = self.session.plan("아직 완료 안 했어", turn_id=4, language="ko")
        self.assertFalse(plan_neg.state_changed)
        self.assertEqual(self.fixture.steps[self.session.current_index].source_label, "1")

        # 2. Positive completion with adverb: "응, 이번 단계도 미리 완료했어."
        plan_done = self.session.plan("응, 이번 단계도 미리 완료했어.", turn_id=5, language="ko")
        self.assertTrue(plan_done.state_changed)
        self.assertEqual(self.fixture.steps[self.session.current_index].source_label, "2")

        # 3. Next step with "벌써 다 했어"
        plan_done2 = self.session.plan("이번 단계 벌써 다 했어", turn_id=6, language="ko")
        self.assertTrue(plan_done2.state_changed)
        self.assertEqual(self.fixture.steps[self.session.current_index].source_label, "3")

    def test_pause_and_resume_workflow_integration(self) -> None:
        """Pause stops guidance and guards normal commands; resume restores procedure context."""
        self.session.plan("프로토콜 시작", turn_id=1, language="ko")
        self.assertEqual(self.fixture.steps[self.session.current_index].source_label, "1")
        self.assertEqual(self.session._pause_state, "active")

        # 1. Pause via voice command
        plan_pause = self.session.plan("잠시 일시정지할게", turn_id=2, language="ko")
        self.assertEqual(plan_pause.action, CuratedProtocolAction.PAUSE)
        self.assertEqual(self.session._pause_state, "paused")
        self.assertIn("일시 중지", plan_pause.display_text)

        # 2. Utterance while paused: returns pause prompt without mutating procedure
        plan_during = self.session.plan("AMBIC가 뭐야?", turn_id=3, language="ko")
        self.assertEqual(plan_during.action, CuratedProtocolAction.PAUSE)
        self.assertFalse(plan_during.state_changed)
        self.assertIn("일시정지 상태입니다", plan_during.display_text)
        self.assertEqual(self.fixture.steps[self.session.current_index].source_label, "1")

        # 3. Resume via voice command
        plan_resume = self.session.plan("다시 실험 재개할게", turn_id=4, language="ko")
        self.assertEqual(plan_resume.action, CuratedProtocolAction.RESUME)
        self.assertEqual(self.session._pause_state, "active")
        self.assertIn("재개", plan_resume.speech_text)
    def test_numbered_step_completion_matching_and_mismatch_clarification(self) -> None:
        """Explicit numbered step completion must advance on match and clarify on mismatch without state mutation."""
        self.session.plan("프로토콜 시작", turn_id=1, language="ko")
        self.assertEqual(self.fixture.steps[self.session.current_index].source_label, "1")

        # 1. Matching numbered completion: Step 1 -> Step 2
        plan_match1 = self.session.plan("1단계 완료했어", turn_id=2, language="ko")
        self.assertTrue(plan_match1.state_changed)
        self.assertEqual(self.fixture.steps[self.session.current_index].source_label, "2")

        # 2. Matching numbered completion with adverb: "이번 2단계도 완료했어" -> Step 3
        plan_match2 = self.session.plan("이번 2단계도 완료했어", turn_id=3, language="ko")
        self.assertTrue(plan_match2.state_changed)
        self.assertEqual(self.fixture.steps[self.session.current_index].source_label, "3")

        # 3. Mismatched numbered completion: at Step 3, user says "4단계 완료했어"
        # Server authority MUST guard this and require clarification before mutating.
        plan_mismatch = self.session.plan("4단계 완료했어", turn_id=4, language="ko")
        self.assertFalse(plan_mismatch.state_changed)
        self.assertEqual(self.session.current_index, 2)
        self.assertEqual(self.fixture.steps[self.session.current_index].source_label, "3")
        self.assertEqual(plan_mismatch.action, CuratedProtocolAction.CLARIFY_COMPLETION)
        self.assertIn("현재 진행 중인 단계는 3단계입니다", plan_mismatch.speech_text)
        self.assertIn("3단계를 완료하셨다는 뜻인가요?", plan_mismatch.speech_text)

        # 4. User confirms affirmative: "응" -> advances to Step 4
        plan_confirm = self.session.plan("응", turn_id=5, language="ko")
        self.assertTrue(plan_confirm.state_changed)
        self.assertEqual(self.fixture.steps[self.session.current_index].source_label, "4")

        # 5. Mismatched numbered completion: at Step 4, user says "2단계 완료했어"
        plan_mismatch2 = self.session.plan("2단계 완료했어", turn_id=6, language="ko")
        self.assertFalse(plan_mismatch2.state_changed)
        self.assertEqual(plan_mismatch2.action, CuratedProtocolAction.CLARIFY_COMPLETION)
        self.assertIn("현재 진행 중인 단계는 4단계입니다", plan_mismatch2.speech_text)

        # 6. User declines negative: "아니" -> declines without mutation
        plan_decline = self.session.plan("아니", turn_id=7, language="ko")
        self.assertFalse(plan_decline.state_changed)
        self.assertEqual(self.fixture.steps[self.session.current_index].source_label, "4")
        self.assertEqual(plan_decline.action, CuratedProtocolAction.DECLINE_COMPLETION)

    def test_contextual_stt_repair_confirmation_flow(self) -> None:
        """Corrupted STT for completion commands must trigger clarification without mutating state."""
        self.session.plan("프로토콜 시작", turn_id=1, language="ko")
        self.session.plan("1단계 완료했어", turn_id=2, language="ko")
        self.session.plan("2단계 완료했어", turn_id=3, language="ko")
        self.assertEqual(self.fixture.steps[self.session.current_index].source_label, "3")

        # At Step 3, corrupted STT transcript "탐방대도 완료했어"
        plan_corrupted = self.session.plan("탐방대도 완료했어", turn_id=4, language="ko")
        self.assertFalse(plan_corrupted.state_changed)
        self.assertEqual(self.session.current_index, 2)
        self.assertEqual(plan_corrupted.action, CuratedProtocolAction.CLARIFY_COMPLETION)
        self.assertIn("3단계도 완료했어", plan_corrupted.speech_text)
        self.assertIn("말씀하신 건가요?", plan_corrupted.speech_text)

        # Confirm repair -> advances to Step 4
        plan_confirm = self.session.plan("응 맞아", turn_id=5, language="ko")
        self.assertTrue(plan_confirm.state_changed)
        self.assertEqual(self.fixture.steps[self.session.current_index].source_label, "4")

    def test_qa_speech_brevity_and_display_detail(self) -> None:
        """Grounded QA speech must be concise (<200 chars) while display document contains full rich detail."""
        self.session.plan("프로토콜 시작", turn_id=1, language="ko")

        # Single entity query
        plan1 = self.session.plan("AMBIC가 뭐야?", turn_id=2, language="ko")
        self.assertIn(plan1.action, {CuratedProtocolAction.LAB_DOMAIN_QA, CuratedProtocolAction.RELATED_QUESTION, CuratedProtocolAction.QUESTION})
        self.assertLessEqual(len(plan1.speech_text), 150)
        self.assertIn("중탄산암모늄", plan1.speech_text)
        self.assertIn("화면에 정리했습니다", plan1.speech_text)
        self.assertNotIn("http", plan1.speech_text)
        self.assertNotIn(".pdf", plan1.speech_text)

        # Multi-entity query
        plan2 = self.session.plan("AMBIC와 HPLC water의 차이가 뭐야?", turn_id=3, language="ko")
        self.assertIn(plan2.action, {CuratedProtocolAction.LAB_DOMAIN_QA, CuratedProtocolAction.RELATED_QUESTION, CuratedProtocolAction.QUESTION})
        self.assertLessEqual(len(plan2.speech_text), 200)
        self.assertIn("AMBIC", plan2.speech_text)
        self.assertIn("HPLC water", plan2.speech_text)
        self.assertIn("화면에 정리했습니다", plan2.speech_text)

    def test_paused_workflow_complete_voice_muting(self) -> None:
        """During paused workflow, speech_text must be completely empty to prevent TTS playback."""
        self.session.plan("프로토콜 시작", turn_id=1, language="ko")
        self.session.plan("잠시 일시정지", turn_id=2, language="ko")
        self.assertEqual(self.session._pause_state, "paused")

        # Utterance while paused
        plan_mute = self.session.plan("Solution A가 뭐야?", turn_id=3, language="ko")
        self.assertEqual(plan_mute.action, CuratedProtocolAction.PAUSE)
        self.assertFalse(plan_mute.state_changed)
        self.assertEqual(plan_mute.speech_text, "")
        self.assertIn("일시정지 상태입니다", plan_mute.display_text)

        # Resume restores speech
        plan_resume = self.session.plan("실험 재개", turn_id=4, language="ko")
        self.assertEqual(plan_resume.action, CuratedProtocolAction.RESUME)
        self.assertEqual(self.session._pause_state, "active")
        self.assertNotEqual(plan_resume.speech_text, "")
        self.assertIn("재개", plan_resume.speech_text)

    def test_web_visual_asset_registry(self) -> None:
        """WebVisualAssetRegistry stores and retrieves validated assets with same-origin IDs."""
        from voice_workflow_agent.web_visuals import WebVisualAsset, WebVisualAssetRegistry

        registry = WebVisualAssetRegistry()
        dummy_content = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x40\x00\x00\x00\x40\x08\x06\x00\x00\x00\xaa"
        sha = "a" * 64
        asset = WebVisualAsset(
            asset_id=sha,
            mime_type="image/png",
            content=dummy_content,
            width=64,
            height=64,
            content_sha256=sha,
            source_url="https://example.com/image.png",
            publisher_domain="example.com",
            title="Example Test Image",
        )
        registry._assets[sha] = asset
        self.assertIsNotNone(registry.get(sha))
        self.assertEqual(registry.get(sha).asset_id, sha)
        self.assertIsNone(registry.get("invalid_id"))


if __name__ == "__main__":
    unittest.main()
