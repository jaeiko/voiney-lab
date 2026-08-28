"""Reproducible, synthetic acceptance harness for the interruption gate.

## What this does and does not prove

It proves that the *state machine* behaves correctly for a fixed catalogue of
acoustic situations: silence, impulses, sustained equipment noise, the agent's
own playback leaking back into the microphone, real speech, an explicit stop
command, an unknown speaker, and two people talking over each other.

It does **not** prove noise robustness in a real wet lab. Every fixture here is
synthesised: a constant-amplitude PCM block paired with a scripted voice-activity
verdict. Real rooms produce spectra, reverberation and microphone AGC behaviour
that no constant block reproduces, and the real WebRTC VAD's verdict on real
noise is exactly the variable this harness holds fixed.

Levels are normalised digital RMS (0.0-1.0 full scale). Where a scenario names a
ratio it is a **synthetic** signal-to-floor ratio computed from those digital
amplitudes. Nothing here is a dBA measurement and nothing here may be reported
as one.

Field validation is a separate, later activity that needs real researchers in a
real lab; see the pilot readiness notes in the handoff document.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from voice_workflow_agent.audio import FRAME_BYTES, samples_to_pcm16


#: Normalised full-scale RMS for each synthetic sound class, with the real-world
#: situation each one stands in for. These are deliberately round numbers: they
#: are fixtures for a state machine, not measurements.
SYNTHETIC_LEVELS: dict[str, float] = {
    # A quiet room with a live microphone: dither and preamp hiss, not silence.
    "silence": 0.0004,
    # A dropped pipette tip or a cabinet door: very loud, very short.
    "impulse": 0.35,
    # A fume hood or centrifuge: moderate, constant, not speech-shaped.
    "steady_noise": 0.02,
    # A loud shared bay: still constant, still not speech-shaped.
    "loud_noise": 0.045,
    # The agent's own TTS returning through the room. Speech-shaped by
    # definition, which is why level and the onset cooldown must catch it.
    "tts_echo": 0.012,
    # A researcher speaking at normal bench distance.
    "speech": 0.15,
    # Someone talking two benches away: speech-shaped but far quieter.
    "distant_speech": 0.01,
}


@dataclass(frozen=True)
class SyntheticSegment:
    """One run of frames with a fixed level and a fixed scripted VAD verdict."""

    kind: str
    frames: int
    #: What WebRTC VAD would report. Scripted rather than computed, because the
    #: real classifier's opinion of synthetic audio is not itself meaningful.
    speech_shaped: bool

    def __post_init__(self) -> None:
        if self.kind not in SYNTHETIC_LEVELS:
            raise ValueError(f"unknown synthetic sound class: {self.kind}")
        if self.frames <= 0:
            raise ValueError("a synthetic segment needs at least one frame")

    @property
    def level(self) -> float:
        return SYNTHETIC_LEVELS[self.kind]

    def pcm(self) -> bytes:
        """Constant-amplitude PCM16 whose RMS equals this segment's level."""

        amplitude = round(self.level * 32768)
        samples = [amplitude] * (FRAME_BYTES // 2)
        return samples_to_pcm16(samples) * self.frames

    def vad_flags(self) -> list[bool]:
        return [self.speech_shaped] * self.frames


@dataclass(frozen=True)
class BargeInScenario:
    """One named acoustic situation and the outcome the product requires."""

    scenario_id: str
    description: str
    segments: tuple[SyntheticSegment, ...]
    #: Should the researcher's answer visibly duck (an announced candidate)?
    expect_candidate: bool
    #: Should playback actually be cancelled (a confirmed barge-in)?
    expect_confirmed: bool
    #: Transcript the STT stage would return, when the scenario reaches it.
    transcript: str | None = None
    limitation: str = ""

    def pcm(self) -> bytes:
        return b"".join(segment.pcm() for segment in self.segments)

    def vad_flags(self) -> list[bool]:
        flags: list[bool] = []
        for segment in self.segments:
            flags.extend(segment.vad_flags())
        return flags

    @property
    def frame_count(self) -> int:
        return sum(segment.frames for segment in self.segments)

    def synthetic_signal_to_floor_ratio(self) -> float | None:
        """Loudest speech-shaped level over the quietest non-speech level.

        Synthetic and dimensionless. Not an acoustic SNR, not a dBA figure.
        """

        speech = [s.level for s in self.segments if s.speech_shaped]
        background = [s.level for s in self.segments if not s.speech_shaped]
        if not speech or not background or min(background) <= 0:
            return None
        return round(max(speech) / min(background), 2)


class ScriptedVoiceActivity:
    """Replays a fixed VAD verdict per frame, then reports silence."""

    def __init__(self, flags: Sequence[bool]) -> None:
        self._flags = list(flags)
        self._index = 0

    def __call__(self, frame: bytes) -> bool:
        if self._index < len(self._flags):
            verdict = self._flags[self._index]
            self._index += 1
            return verdict
        return False


#: Silence long enough to end an utterance under the endpoint configuration the
#: scenarios use, so a scenario that should reach STT actually does.
def _trailing_silence(frames: int = 12) -> SyntheticSegment:
    return SyntheticSegment("silence", frames, False)


SCENARIOS: tuple[BargeInScenario, ...] = (
    BargeInScenario(
        "silence_during_playback",
        "A quiet room while the agent speaks.",
        (SyntheticSegment("silence", 60, False),),
        expect_candidate=False, expect_confirmed=False,
        limitation="A real quiet room still has HVAC and distant speech.",
    ),
    BargeInScenario(
        "impulse_during_playback",
        "A dropped tip: very loud, two frames long.",
        (SyntheticSegment("silence", 20, False),
         SyntheticSegment("impulse", 2, True),
         SyntheticSegment("silence", 40, False)),
        expect_candidate=False, expect_confirmed=False,
        limitation="Real impulses ring; a real VAD may flag more frames.",
    ),
    BargeInScenario(
        "sustained_equipment_noise",
        "A centrifuge running for over a second, never speech-shaped.",
        (SyntheticSegment("steady_noise", 80, False),),
        expect_candidate=False, expect_confirmed=False,
        limitation="Real machine noise is broadband and can fool a real VAD.",
    ),
    BargeInScenario(
        "loud_bay_noise",
        "A loud shared bay. The floor rises; nothing is speech-shaped.",
        (SyntheticSegment("loud_noise", 100, False),),
        expect_candidate=False, expect_confirmed=False,
        limitation="Level alone cannot separate loud noise from loud speech.",
    ),
    BargeInScenario(
        "agent_playback_echo",
        "The agent's own voice returning through the room at playback onset.",
        (SyntheticSegment("tts_echo", 40, True),),
        expect_candidate=False, expect_confirmed=False,
        limitation=(
            "Assumes browser echo cancellation attenuates the return path. "
            "An un-cancelled speaker at high volume is not modelled."),
    ),
    BargeInScenario(
        "distant_colleague_speaking",
        "A colleague two benches away: speech-shaped but far below the floor.",
        (SyntheticSegment("steady_noise", 30, False),
         SyntheticSegment("distant_speech", 40, True),
         _trailing_silence()),
        expect_candidate=False, expect_confirmed=False,
        limitation="Distance attenuation varies enormously by room.",
    ),
    BargeInScenario(
        "participant_speaks",
        "The researcher speaks at the bench while the agent is talking.",
        (SyntheticSegment("silence", 20, False),
         SyntheticSegment("speech", 40, True),
         _trailing_silence()),
        expect_candidate=True, expect_confirmed=True,
        transcript="다음 단계 알려줘",
    ),
    BargeInScenario(
        "participant_says_stop",
        "An explicit stop command over the agent's answer.",
        (SyntheticSegment("silence", 20, False),
         SyntheticSegment("speech", 20, True),
         _trailing_silence()),
        expect_candidate=True, expect_confirmed=True,
        transcript="멈춰",
    ),
    BargeInScenario(
        "speech_after_sustained_noise",
        "Real speech in a room the gate has already measured as loud.",
        (SyntheticSegment("steady_noise", 60, False),
         SyntheticSegment("speech", 40, True),
         _trailing_silence()),
        expect_candidate=True, expect_confirmed=True,
        transcript="완료됐어요",
        limitation="Assumes speech stays well above the adapted floor.",
    ),
)


def scenario_vad_config():
    """Endpointing shortened so a scenario ends inside a bounded fixture.

    Only the endpoint silence is scaled. Every onset threshold keeps its
    production value, because those are exactly what the scenarios test.
    """

    from voice_workflow_agent.vad import VadConfig

    return VadConfig(endpoint_silence_frames=10)


def playback_session(scenario: BargeInScenario, *, settings=None):
    """A live session already playing an answer, ready to receive the scenario.

    ``ListenerSession`` is imported here rather than at module scope so that
    reading the scenario catalogue - which the tests and this module's own
    documentation do - never pays for importing the whole application.
    """

    from voice_workflow_agent.server import ListenerSession
    from voice_workflow_agent.vad import EndpointDetector, TurnState

    session = ListenerSession(
        EndpointDetector(
            scenario_vad_config(),
            classifier=ScriptedVoiceActivity(scenario.vad_flags()),
            listening_onset=True,
        ),
        interruption_settings=settings,
    )
    session.start()
    session.active_turn_id = 1
    session.turn_generations[1] = session.generation
    session.detector.state = TurnState.PROCESSING
    if not session.start_playback(1):
        raise RuntimeError("scenario session could not enter playback")
    return session


def run_scenario(
    scenario: BargeInScenario, *, settings=None,
) -> "ScenarioResult":
    """Drive one scenario through the production session boundary.

    The STT stage is stood in for by the scenario's ``transcript``: a scenario
    with no transcript is one where the provider would have returned nothing,
    which is a rejection, not a confirmation.
    """

    from voice_workflow_agent.barge_in import is_priority_stop_command

    session = playback_session(scenario, settings=settings)
    opening_generation = session.generation
    events = session.accept_chunk(scenario.pcm())
    kinds = [item.kind for item in events]
    result = ScenarioResult(
        scenario.scenario_id,
        candidate="barge_in_candidate" in kinds,
        rejected_reasons=tuple(
            str(item.reason) for item in events
            if item.kind == "barge_in_rejected"
        ),
        events=tuple(kinds),
    )
    for item in events:
        if item.kind != "barge_in_audio_ready":
            continue
        if scenario.transcript is None:
            session.reject_interrupt_candidate(item, "transcription_failed")
            continue
        committed = session.commit_interrupt_candidate(
            item,
            reason=(
                "priority_stop" if is_priority_stop_command(scenario.transcript)
                else "confirmed_speech"),
        )
        if committed:
            result.confirmed = True
    result.workflow_generation_changed = session.generation != opening_generation
    return result


@dataclass
class ScenarioResult:
    scenario_id: str
    candidate: bool = False
    confirmed: bool = False
    rejected_reasons: tuple[str, ...] = ()
    workflow_generation_changed: bool = False
    events: tuple[str, ...] = ()

    def matches(self, scenario: BargeInScenario) -> bool:
        return (self.candidate == scenario.expect_candidate
                and self.confirmed == scenario.expect_confirmed)


@dataclass
class EvaluationReport:
    """Aggregate counters for a scenario sweep. Synthetic figures only."""

    results: list[ScenarioResult] = field(default_factory=list)

    @property
    def false_candidates(self) -> int:
        """Announced interruptions in scenarios that should have stayed quiet."""

        return sum(
            1 for result, scenario in zip(self.results, SCENARIOS)
            if result.candidate and not scenario.expect_candidate
        )

    @property
    def missed_interruptions(self) -> int:
        return sum(
            1 for result, scenario in zip(self.results, SCENARIOS)
            if scenario.expect_confirmed and not result.confirmed
        )

    @property
    def unintended_workflow_mutations(self) -> int:
        return sum(
            1 for result, scenario in zip(self.results, SCENARIOS)
            if result.workflow_generation_changed and not scenario.expect_confirmed
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "measurement_basis": "synthetic_digital_amplitude",
            "field_validated": False,
            "scenarios": len(self.results),
            "false_candidates": self.false_candidates,
            "missed_interruptions": self.missed_interruptions,
            "unintended_workflow_mutations": self.unintended_workflow_mutations,
            "ignored_noise_scenarios": sum(
                1 for result, scenario in zip(self.results, SCENARIOS)
                if not scenario.expect_candidate and not result.candidate
            ),
            "per_scenario": [
                {
                    "scenario_id": result.scenario_id,
                    "candidate": result.candidate,
                    "confirmed": result.confirmed,
                    "expected_candidate": scenario.expect_candidate,
                    "expected_confirmed": scenario.expect_confirmed,
                    "passed": result.matches(scenario),
                    "synthetic_signal_to_floor_ratio": (
                        scenario.synthetic_signal_to_floor_ratio()),
                    "limitation": scenario.limitation,
                }
                for result, scenario in zip(self.results, SCENARIOS)
            ],
        }
