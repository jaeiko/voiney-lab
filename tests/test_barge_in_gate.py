"""Acceptance tests for noisy-lab interruption handling.

These describe the product rule that matters at a bench: **acoustic activity is
not an interruption.** A sound only stops the agent when it is loud relative to
this room, sustained, clear of the playback-onset echo window, speech-shaped,
and then independently confirmed by a transcript the admission layer accepts.

Everything here is synthetic and deterministic - constant-amplitude PCM paired
with a scripted voice-activity verdict. That is enough to prove the state
machine, and it is not enough to claim real-lab robustness; see
``barge_in_evaluation`` for exactly what each fixture does and does not model.
"""

from __future__ import annotations

import unittest

from voice_workflow_agent.audio import FRAME_BYTES, pcm16_rms, samples_to_pcm16
from voice_workflow_agent.barge_in import (
    IGNORED_BELOW_NOISE_FLOOR,
    IGNORED_PLAYBACK_ONSET,
    InterruptionGate,
    InterruptionGateSettings,
    InterruptionStage,
    is_priority_stop_command,
    normalize_command_text,
)
from voice_workflow_agent.barge_in_evaluation import (
    SCENARIOS,
    BargeInScenario,
    EvaluationReport,
    ScriptedVoiceActivity,
    SyntheticSegment,
    playback_session,
    run_scenario,
    scenario_vad_config,
)
from voice_workflow_agent.speaker_attribution import (
    MutationOutcome,
    Participant,
    SessionParticipants,
    SpeakerDiarizationSettings,
    TranscriptSegment,
    diarization_diagnostics,
    evaluate_speaker_policy,
    normalize_speaker_label,
    transcript_segments,
)
from voice_workflow_agent.vad import TurnState


def frames(kind: str, count: int, speech_shaped: bool) -> SyntheticSegment:
    return SyntheticSegment(kind, count, speech_shaped)


class SyntheticFixtureTests(unittest.TestCase):
    def test_synthetic_pcm_has_the_level_the_fixture_claims(self):
        for kind in ("silence", "steady_noise", "speech"):
            with self.subTest(kind=kind):
                segment = frames(kind, 1, False)
                measured = pcm16_rms(segment.pcm())
                self.assertAlmostEqual(measured, segment.level, places=3)

    def test_frame_helper_produces_exact_twenty_millisecond_frames(self):
        self.assertEqual(len(frames("speech", 3, True).pcm()), FRAME_BYTES * 3)

    def test_synthetic_ratio_is_labelled_as_synthetic_not_acoustic(self):
        scenario = next(
            item for item in SCENARIOS
            if item.scenario_id == "speech_after_sustained_noise")
        ratio = scenario.synthetic_signal_to_floor_ratio()
        self.assertIsNotNone(ratio)
        self.assertGreater(ratio, 1.0)
        self.assertIn("measurement_basis", EvaluationReport().as_dict())
        self.assertEqual(
            EvaluationReport().as_dict()["measurement_basis"],
            "synthetic_digital_amplitude",
        )
        self.assertIs(EvaluationReport().as_dict()["field_validated"], False)


class InterruptionGateUnitTests(unittest.TestCase):
    def test_digital_silence_never_clears_the_gate(self):
        gate = InterruptionGate()
        gate.playback_started()
        for _ in range(100):
            assessment = gate.observe_frame(rms=0.0004)
        self.assertFalse(assessment.ready)
        self.assertEqual(assessment.reason, IGNORED_BELOW_NOISE_FLOOR)

    def test_the_playback_onset_window_is_deaf_by_design(self):
        gate = InterruptionGate()
        gate.playback_started()
        cooldown = gate.settings.playback_onset_cooldown_frames
        for _ in range(cooldown):
            assessment = gate.observe_frame(rms=0.2)
        self.assertFalse(assessment.ready)
        self.assertEqual(assessment.reason, IGNORED_PLAYBACK_ONSET)

    def test_an_impulse_is_too_short_to_be_an_interruption(self):
        gate = InterruptionGate()
        gate.playback_started()
        for _ in range(gate.settings.playback_onset_cooldown_frames + 1):
            gate.observe_frame(rms=0.0004)
        for _ in range(2):
            assessment = gate.observe_frame(rms=0.35)
        self.assertFalse(assessment.ready)

    def test_the_floor_rises_under_sustained_noise_and_falls_again(self):
        gate = InterruptionGate()
        gate.playback_started()
        for _ in range(200):
            gate.observe_frame(rms=0.02)
        raised = gate.noise_floor_rms
        self.assertGreater(raised, 0.01)
        # Quiet returns faster than noise raised the bar, so the next real
        # utterance is not penalised by noise that has already stopped.
        for _ in range(60):
            gate.observe_frame(rms=0.0004)
        self.assertLess(gate.noise_floor_rms, raised / 2)

    def test_speech_still_clears_the_gate_in_a_loud_room(self):
        gate = InterruptionGate()
        gate.playback_started()
        for _ in range(200):
            gate.observe_frame(rms=0.02)
        for _ in range(gate.settings.minimum_candidate_speech_frames):
            assessment = gate.observe_frame(rms=0.15)
        self.assertTrue(assessment.ready)

    def test_a_dismissed_candidate_re_arms_the_cooldown(self):
        gate = InterruptionGate()
        gate.playback_started()
        for _ in range(40):
            gate.observe_frame(rms=0.15)
        gate.mark_candidate()
        self.assertIs(gate.stage, InterruptionStage.CANDIDATE)
        gate.dismiss()
        self.assertIs(gate.stage, InterruptionStage.PLAYING)
        assessment = gate.observe_frame(rms=0.15)
        self.assertFalse(assessment.ready)
        self.assertEqual(assessment.reason, IGNORED_PLAYBACK_ONSET)

    def test_disabling_the_gate_restores_the_previous_behaviour(self):
        gate = InterruptionGate(InterruptionGateSettings(enabled=False))
        gate.playback_started()
        self.assertTrue(gate.observe_frame(rms=0.0).ready)

    def test_every_threshold_is_validated(self):
        for invalid in (
            {"onset_snr_ratio": 0.5},
            {"onset_absolute_rms": 0.0},
            {"noise_floor_minimum_rms": 0.9, "noise_floor_maximum_rms": 0.1},
            {"minimum_candidate_speech_ms": 0},
            {"candidate_window_ms": 20, "minimum_candidate_speech_ms": 200},
            {"noise_floor_rise": 0.0},
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    InterruptionGateSettings(**invalid)

    def test_settings_come_from_bounded_environment_values(self):
        settings = InterruptionGateSettings.from_environment(
            {"CASCADE_BARGE_IN_ONSET_SNR_RATIO": "4.5",
             "CASCADE_BARGE_IN_MIN_SPEECH_MS": "200"})
        self.assertEqual(settings.onset_snr_ratio, 4.5)
        self.assertEqual(settings.minimum_candidate_speech_ms, 200)
        with self.assertRaises(Exception):
            InterruptionGateSettings.from_environment(
                {"CASCADE_BARGE_IN_ONSET_SNR_RATIO": "99999"})


class PriorityStopCommandTests(unittest.TestCase):
    def test_explicit_stop_and_pause_commands_are_recognised(self):
        for transcript in (
            "멈춰", "멈춰요", "그만", "그만해 주세요", "잠깐", "잠시만요",
            "일시정지", "정지해", "중지", "스톱", "Stop.", "pause", "wait",
        ):
            with self.subTest(transcript=transcript):
                self.assertTrue(is_priority_stop_command(transcript))

    def test_a_stop_word_inside_a_longer_sentence_never_halts_the_agent(self):
        for transcript in (
            "그만두지 말고 계속 알려줘",
            "잠시 후에 다시 알려줘",
            "다음 단계 알려줘",
            "완료됐어요",
            "이 단계 멈추지 않고 계속 진행할게요",
        ):
            with self.subTest(transcript=transcript):
                self.assertFalse(is_priority_stop_command(transcript))

    def test_normalisation_folds_punctuation_not_meaning(self):
        self.assertEqual(normalize_command_text("  멈춰!! "), "멈춰")
        self.assertEqual(normalize_command_text("Stop, please."), "stop please")


class InterruptionScenarioTests(unittest.TestCase):
    """Scenarios 1-9 of the field-risk catalogue, end to end in a session."""

    def test_every_catalogued_scenario_reaches_its_required_outcome(self):
        for scenario in SCENARIOS:
            with self.subTest(scenario=scenario.scenario_id):
                result = run_scenario(scenario)
                self.assertEqual(
                    result.candidate, scenario.expect_candidate,
                    f"{scenario.scenario_id}: candidate={result.candidate} "
                    f"events={result.events}")
                self.assertEqual(
                    result.confirmed, scenario.expect_confirmed,
                    f"{scenario.scenario_id}: confirmed={result.confirmed} "
                    f"events={result.events}")

    def test_noise_never_advances_a_workflow_generation(self):
        for scenario in SCENARIOS:
            if scenario.expect_confirmed:
                continue
            with self.subTest(scenario=scenario.scenario_id):
                result = run_scenario(scenario)
                self.assertFalse(result.workflow_generation_changed)

    def test_ignored_noise_leaves_no_trace_in_the_voice_history(self):
        """A dismissed candidate must not look like a user command."""

        scenario = next(
            item for item in SCENARIOS
            if item.scenario_id == "distant_colleague_speaking")
        result = run_scenario(scenario)
        self.assertNotIn("barge_in_candidate", result.events)
        self.assertNotIn("barge_in_audio_ready", result.events)
        self.assertNotIn("speech.start", result.events)

    def test_ambient_noise_cannot_strand_a_turn_that_finished_speaking(self):
        """Sustained noise must not hold the session in AGENT_SPEAKING."""

        scenario = next(
            item for item in SCENARIOS
            if item.scenario_id == "sustained_equipment_noise")
        session = playback_session(scenario)
        session.accept_chunk(scenario.pcm())
        self.assertTrue(session.playback_ended(1))
        self.assertEqual(session.state, TurnState.COOLDOWN)

    def test_a_confirmed_interruption_still_defers_playback_completion(self):
        scenario = next(
            item for item in SCENARIOS
            if item.scenario_id == "participant_speaks")
        session = playback_session(scenario)
        events = session.accept_chunk(scenario.pcm())
        self.assertIn("barge_in_candidate", [item.kind for item in events])
        self.assertFalse(session.playback_ended(1))

    def test_disabling_the_gate_reproduces_the_pre_hardening_false_positive(self):
        """The regression this pass fixes, demonstrated against the old path."""

        scenario = next(
            item for item in SCENARIOS
            if item.scenario_id == "agent_playback_echo")
        self.assertFalse(run_scenario(scenario).candidate)
        permissive = run_scenario(
            scenario, settings=InterruptionGateSettings(enabled=False))
        self.assertTrue(permissive.candidate)

    def test_an_evaluation_report_counts_false_candidates_and_misses(self):
        report = EvaluationReport(
            results=[run_scenario(scenario) for scenario in SCENARIOS])
        summary = report.as_dict()
        self.assertEqual(summary["false_candidates"], 0)
        self.assertEqual(summary["missed_interruptions"], 0)
        self.assertEqual(summary["unintended_workflow_mutations"], 0)
        self.assertTrue(all(
            row["passed"] for row in summary["per_scenario"]))
        # The report must keep saying what it is not.
        self.assertIs(summary["field_validated"], False)


class CommittedWorkFlowSurvivesInterruptionTests(unittest.TestCase):
    """Scenario 11: stopping audio never rolls back a committed transaction."""

    def test_confirming_an_interruption_touches_no_workflow_state(self):
        scenario = next(
            item for item in SCENARIOS
            if item.scenario_id == "participant_speaks")
        session = playback_session(scenario)

        class RecordingWorkflow:
            """Stands in for the curated session; any call here is a bug."""

            def __init__(self) -> None:
                self.calls: list[str] = []

            def __getattr__(self, name: str):
                self.calls.append(name)
                raise AssertionError(
                    f"interruption handling reached workflow state via {name}")

        session.curated_protocol_session = RecordingWorkflow()
        events = session.accept_chunk(scenario.pcm())
        ready = next(
            item for item in events if item.kind == "barge_in_audio_ready")
        committed = session.commit_interrupt_candidate(ready)
        self.assertTrue(committed)
        self.assertEqual(session.curated_protocol_session.calls, [])

    def test_a_rejected_candidate_leaves_the_turn_and_generation_intact(self):
        scenario = BargeInScenario(
            "noise_that_transcribes_to_nothing",
            "Speech-shaped and loud, but the provider returns no text.",
            (SyntheticSegment("silence", 20, False),
             SyntheticSegment("speech", 30, True),
             SyntheticSegment("silence", 12, False)),
            expect_candidate=True, expect_confirmed=False,
            transcript=None,
        )
        session = playback_session(scenario)
        opening = (session.generation, session.active_turn_id)
        events = session.accept_chunk(scenario.pcm())
        ready = next(
            item for item in events if item.kind == "barge_in_audio_ready")
        rejected = session.reject_interrupt_candidate(
            ready, "transcription_failed")
        self.assertIsNotNone(rejected)
        self.assertEqual((session.generation, session.active_turn_id), opening)
        self.assertEqual(session.state, TurnState.AGENT_SPEAKING)
        # Playback may now finish normally: the noise did not fail the Turn.
        self.assertTrue(session.playback_ended(1))


class SpeakerPolicyTests(unittest.TestCase):
    """Scenarios 8, 10 and 12: who spoke, and what that does and does not mean."""

    def participants(self) -> SessionParticipants:
        roster = SessionParticipants()
        roster.enrol(Participant("researcher-1", "김연구"))
        roster.confirm_label("speaker_0", "researcher-1")
        return roster

    def test_provider_words_become_provider_neutral_segments(self):
        segments = transcript_segments(
            [
                {"text": "완료", "start": 0.0, "end": 0.4, "speaker": 0},
                {"text": "됐어요", "start": 0.4, "end": 0.9, "speaker": 0},
                {"text": "잠깐", "start": 1.0, "end": 1.4, "speaker": 1},
            ],
            fallback_text="완료 됐어요 잠깐",
        )
        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0].speaker_label, "speaker_0")
        self.assertEqual(segments[0].text, "완료 됐어요")
        self.assertEqual(segments[0].start_ms, 0)
        self.assertEqual(segments[0].end_ms, 900)
        self.assertEqual(segments[1].speaker_label, "speaker_1")

    def test_the_documented_word_key_and_the_legacy_one_both_work(self):
        for key in ("text", "word"):
            with self.subTest(key=key):
                segments = transcript_segments([{key: "완료", "speaker": 0}])
                self.assertEqual(segments[0].text, "완료")

    def test_a_speaker_label_is_normalised_and_bounded(self):
        self.assertEqual(normalize_speaker_label(0), "speaker_0")
        self.assertEqual(normalize_speaker_label("speaker_2"), "speaker_2")
        self.assertIsNone(normalize_speaker_label(None))
        self.assertIsNone(normalize_speaker_label(True))
        self.assertIsNone(normalize_speaker_label("x" * 200))

    def test_an_unknown_speaker_cannot_silently_complete_a_step(self):
        decision = evaluate_speaker_policy(
            (TranscriptSegment("완료됐어요", "speaker_9"),),
            self.participants(),
            diarization_enabled=True, mutating=True,
        )
        self.assertIs(decision.outcome, MutationOutcome.CONFIRM_REQUIRED)
        self.assertFalse(decision.mutation_allowed)
        self.assertIn("실험 상태는 변경하지 않았습니다", decision.message)

    def test_a_confirmed_participant_completes_a_step_normally(self):
        decision = evaluate_speaker_policy(
            (TranscriptSegment("완료됐어요", "speaker_0"),),
            self.participants(),
            diarization_enabled=True, mutating=True,
        )
        self.assertIs(decision.outcome, MutationOutcome.ALLOW)
        self.assertEqual(decision.participant_id, "researcher-1")

    def test_an_unknown_speaker_may_still_ask_a_question(self):
        decision = evaluate_speaker_policy(
            (TranscriptSegment("다음 단계 알려줘", "speaker_9"),),
            self.participants(),
            diarization_enabled=True, mutating=False,
        )
        self.assertTrue(decision.mutation_allowed)

    def test_stop_is_fail_safe_whoever_says_it(self):
        decision = evaluate_speaker_policy(
            (TranscriptSegment("멈춰", "speaker_9"),),
            self.participants(),
            diarization_enabled=True, mutating=True, priority_stop=True,
        )
        self.assertTrue(decision.mutation_allowed)
        self.assertEqual(decision.reason, "priority_stop_fail_safe")

    def test_overlapping_voices_fail_closed_and_ask_for_one_speaker(self):
        decision = evaluate_speaker_policy(
            (TranscriptSegment("완료", "speaker_0"),
             TranscriptSegment("됐어요", "speaker_1")),
            self.participants(),
            diarization_enabled=True, mutating=True,
        )
        self.assertIs(decision.outcome, MutationOutcome.OVERLAP_AMBIGUOUS)
        self.assertIn("여러 목소리가 겹쳐", decision.message)
        self.assertIn("한 분씩 다시 말씀해", decision.message)

    def test_diarization_off_makes_no_claim_about_identity(self):
        decision = evaluate_speaker_policy(
            (TranscriptSegment("완료됐어요", None),),
            self.participants(),
            diarization_enabled=False, mutating=True,
        )
        self.assertTrue(decision.mutation_allowed)
        self.assertEqual(decision.reason, "diarization_disabled")
        self.assertFalse(decision.speaker_identified)

    def test_a_provider_that_drops_labels_degrades_without_claiming_identity(self):
        decision = evaluate_speaker_policy(
            (TranscriptSegment("완료됐어요", None),),
            self.participants(),
            diarization_enabled=True, mutating=True,
        )
        self.assertTrue(decision.mutation_allowed)
        self.assertEqual(decision.reason, "diarization_unavailable")
        self.assertFalse(decision.speaker_identified)

    def test_labels_are_dropped_on_reconnect_because_they_are_not_identity(self):
        roster = self.participants()
        self.assertTrue(roster.has_confirmed_labels)
        roster.forget_labels()
        self.assertFalse(roster.has_confirmed_labels)
        self.assertEqual(len(roster.roster), 1)

    def test_diagnostics_never_claim_verification_and_carry_no_transcript(self):
        segments = (TranscriptSegment("완료됐어요", "speaker_0"),)
        decision = evaluate_speaker_policy(
            segments, self.participants(),
            diarization_enabled=True, mutating=True)
        diagnostics = diarization_diagnostics(
            segments, decision, diarization_enabled=True)
        self.assertIs(diagnostics["speaker_identity_verified"], False)
        self.assertNotIn("완료됐어요", repr(diagnostics))

    def test_diarization_stays_off_unless_explicitly_enabled(self):
        self.assertFalse(SpeakerDiarizationSettings.from_environment({}).enabled)
        self.assertTrue(
            SpeakerDiarizationSettings.from_environment(
                {"XAI_STT_DIARIZE": "1"}).enabled)


if __name__ == "__main__":  # pragma: no cover - manual runs only
    unittest.main()
