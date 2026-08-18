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
        self.assertEqual(plan2.action, CuratedProtocolAction.RELATED_QUESTION)
        self.assertIn("4단계", plan2.display_text)
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

        url2 = _canonical_url("https://pubchem.ncbi.nlm.nih.gov/compound/14013", open_domains)
        self.assertEqual(url2, "https://pubchem.ncbi.nlm.nih.gov/compound/14013")


if __name__ == "__main__":
    unittest.main()
