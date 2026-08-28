"""Noise-aware interruption gate: acoustic activity is not an interruption.

A shared wet lab is loud. Glassware, chair scrapes, fume hoods, centrifuges and
a colleague talking two benches away all produce frames that WebRTC VAD mode 3
happily labels "voiced". Before this module, any one of those promoted itself
straight to an ``barge_in_candidate``, which ducks the agent's own answer to 4 %
volume for up to a full endpoint-silence window plus one STT round trip. A bench
user experiences that as "it stopped talking to me".

So this module draws the line the product needs:

    PLAYING_TTS
        |  speech-like local activity, above the *measured* ambient floor,
        |  sustained for a configured minimum, outside the playback-onset
        |  cooldown
        v
    INTERRUPTION_CANDIDATE
        |  independent evidence: an admitted STT transcript, or a priority
        |  stop command, or an allowed participant label
        v
    CONFIRMED_BARGE_IN

This module owns only the first arrow. The second arrow stays where it already
was — STT plus ``language.classify_input_event`` in ``server.py`` — because that
is the deterministic admission layer the engineering contract already names, and
nothing here may become a second one.

Three properties matter and are tested:

* a dismissed candidate is *not* a failed turn. The gate reports "this was
  noise" and the caller resumes playback; canonical workflow state is untouched
  and no phantom user command enters the voice history;
* every threshold is explicit, bounded, environment-overridable and documented
  here next to the reason it exists. There are no unexplained magic numbers;
* the gate never decides what a workflow does. It decides whether a sound is
  worth interrupting playback for.
"""

from __future__ import annotations

import math
import os
import re
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum

from voice_workflow_agent.audio import FRAME_MS
from voice_workflow_agent.configuration import bounded_float, bounded_integer


class InterruptionStage(str, Enum):
    """Where one playback window currently sits in the two-stage model."""

    #: TTS is playing and nothing worth reacting to has been heard.
    PLAYING = "playing_tts"
    #: Speech-like activity cleared the acoustic gate. Playback ducks; it is
    #: not cancelled, and no turn outcome has changed.
    CANDIDATE = "interruption_candidate"
    #: Independent evidence arrived. Playback may be cancelled.
    CONFIRMED = "confirmed_barge_in"
    #: The candidate turned out to be noise. Playback resumes unchanged.
    DISMISSED = "dismissed"


#: Reasons the gate refuses to promote acoustic activity to a candidate. These
#: are diagnostic codes, never user-facing copy.
IGNORED_PLAYBACK_ONSET = "playback_onset_cooldown"
IGNORED_BELOW_NOISE_FLOOR = "below_adaptive_noise_floor"
IGNORED_TOO_SHORT = "shorter_than_minimum_speech"
IGNORED_DISABLED = "gate_disabled"


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass(frozen=True)
class InterruptionGateSettings:
    """Every acoustic threshold, with the reason it exists.

    Levels are normalised RMS in ``0.0 .. 1.0`` (PCM16 divided by 32768), so a
    threshold means the same thing regardless of capture hardware. They are
    deliberately *not* described in dBA: this pipeline measures digital
    amplitude, and converting that to a sound-pressure claim would be fiction.
    """

    #: Master switch. Off restores the pre-existing "any VAD onset is a
    #: candidate" behaviour, which is kept reachable so a field problem can be
    #: bisected without redeploying.
    enabled: bool = True

    #: A candidate frame must be this many times louder than the measured
    #: ambient floor. Steady lab noise raises the floor, so the same voice stays
    #: detectable in a loud room while the room itself never qualifies.
    onset_snr_ratio: float = 3.0

    #: Absolute lower bound on a candidate frame. In a near-silent room the
    #: adaptive floor collapses toward zero, and without this a fan tick would
    #: clear an SNR test by arithmetic alone.
    onset_absolute_rms: float = 0.006

    #: Where the floor starts before any audio has been measured. Chosen at the
    #: quiet end so the gate errs toward *hearing* the user on the first frames.
    noise_floor_initial_rms: float = 0.004

    #: The floor is clamped into this band. The lower bound keeps a digitally
    #: silent stream (a muted or dead microphone) from making every tick look
    #: like speech; the upper bound stops sustained loud noise from raising the
    #: floor so far that real speech can no longer clear it. At the maximum the
    #: onset threshold is 0.09 full-scale RMS, which normal close-mic speech
    #: still clears comfortably.
    noise_floor_minimum_rms: float = 0.0015
    noise_floor_maximum_rms: float = 0.03

    #: Asymmetric tracking rates for the minimum-statistics floor estimator
    #: below. ``rise`` is a per-frame multiplicative creep: at 0.02 the floor
    #: needs roughly 1.6 s to climb from a quiet room to a noisy one, which is
    #: fast enough to adapt to a centrifuge starting up and far too slow for one
    #: utterance to raise the bar against the person speaking it. ``fall``
    #: follows the level down quickly, so a room going quiet restores
    #: sensitivity within a few hundred milliseconds.
    noise_floor_rise: float = 0.02
    noise_floor_fall: float = 0.25

    #: Immediately after TTS starts, the loudest thing in the room is usually
    #: the agent itself. Browser echo cancellation is requested but not
    #: guaranteed, so the gate stays deaf for this long to avoid self-barge-in.
    #: Short on purpose: it only has to cover the playback-onset transient,
    #: because the endpoint detector's own sustained-voiced requirement already
    #: covers everything after it. Making it long would delay every legitimate
    #: barge-in to close a hole that is already closed.
    playback_onset_cooldown_ms: int = 120

    #: Speech-like activity must last this long inside the window below. Shorter
    #: than a syllable is an impulse, not an interruption. This *composes* with
    #: the endpoint detector's 240 ms sustained-voiced onset rather than
    #: duplicating it: the detector answers "speech-shaped for long enough",
    #: this answers "above this room's floor for long enough".
    minimum_candidate_speech_ms: int = 120

    #: The sliding window the minimum is counted inside, which tolerates the
    #: unvoiced gaps that occur inside real words.
    candidate_window_ms: int = 400

    def __post_init__(self) -> None:
        if self.onset_snr_ratio < 1.0:
            raise ValueError("onset_snr_ratio must be at least 1.0")
        for name in ("onset_absolute_rms", "noise_floor_initial_rms",
                     "noise_floor_minimum_rms", "noise_floor_maximum_rms"):
            value = getattr(self, name)
            if not 0.0 < value <= 1.0 or not math.isfinite(value):
                raise ValueError(f"{name} must be inside (0.0, 1.0]")
        if self.noise_floor_minimum_rms > self.noise_floor_maximum_rms:
            raise ValueError("noise floor minimum cannot exceed its maximum")
        for name in ("noise_floor_rise", "noise_floor_fall"):
            value = getattr(self, name)
            if not 0.0 < value <= 1.0:
                raise ValueError(f"{name} must be inside (0.0, 1.0]")
        if self.playback_onset_cooldown_ms < 0:
            raise ValueError("playback_onset_cooldown_ms cannot be negative")
        if self.minimum_candidate_speech_ms <= 0:
            raise ValueError("minimum_candidate_speech_ms must be positive")
        if self.candidate_window_ms < self.minimum_candidate_speech_ms:
            raise ValueError(
                "candidate_window_ms cannot be shorter than "
                "minimum_candidate_speech_ms")

    @property
    def candidate_window_frames(self) -> int:
        return max(1, self.candidate_window_ms // FRAME_MS)

    @property
    def minimum_candidate_speech_frames(self) -> int:
        return max(1, self.minimum_candidate_speech_ms // FRAME_MS)

    @property
    def playback_onset_cooldown_frames(self) -> int:
        return max(0, self.playback_onset_cooldown_ms // FRAME_MS)

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None,
    ) -> "InterruptionGateSettings":
        env = os.environ if environment is None else environment
        return cls(
            enabled=bounded_integer(env, "CASCADE_BARGE_IN_GATE_ENABLED", 1, 0, 1) == 1,
            onset_snr_ratio=bounded_float(
                env, "CASCADE_BARGE_IN_ONSET_SNR_RATIO", 3.0, 1.0, 20.0),
            onset_absolute_rms=bounded_float(
                env, "CASCADE_BARGE_IN_ONSET_ABSOLUTE_RMS", 0.006, 0.0005, 0.5),
            noise_floor_initial_rms=bounded_float(
                env, "CASCADE_BARGE_IN_NOISE_FLOOR_INITIAL_RMS",
                0.004, 0.0005, 0.5),
            noise_floor_minimum_rms=bounded_float(
                env, "CASCADE_BARGE_IN_NOISE_FLOOR_MIN_RMS",
                0.0015, 0.0001, 0.5),
            noise_floor_maximum_rms=bounded_float(
                env, "CASCADE_BARGE_IN_NOISE_FLOOR_MAX_RMS", 0.03, 0.001, 1.0),
            noise_floor_rise=bounded_float(
                env, "CASCADE_BARGE_IN_NOISE_FLOOR_RISE", 0.02, 0.001, 1.0),
            noise_floor_fall=bounded_float(
                env, "CASCADE_BARGE_IN_NOISE_FLOOR_FALL", 0.25, 0.001, 1.0),
            playback_onset_cooldown_ms=bounded_integer(
                env, "CASCADE_BARGE_IN_PLAYBACK_COOLDOWN_MS", 120, 0, 5000),
            minimum_candidate_speech_ms=bounded_integer(
                env, "CASCADE_BARGE_IN_MIN_SPEECH_MS", 120, 20, 5000),
            candidate_window_ms=bounded_integer(
                env, "CASCADE_BARGE_IN_WINDOW_MS", 400, 20, 5000),
        )


@dataclass(frozen=True)
class FrameAssessment:
    """What the gate concluded about exactly one 20 ms frame."""

    stage: InterruptionStage
    #: True when the acoustic evidence *would* support a candidate right now.
    #: The caller still requires its own VAD onset before announcing one, so a
    #: loud non-speech burst alone can never duck the agent.
    ready: bool = False
    reason: str | None = None
    noise_floor_rms: float = 0.0
    frame_rms: float = 0.0
    speech_like_frames: int = 0

    @property
    def signal_to_floor_ratio(self) -> float:
        """Synthetic ratio of this frame to the measured ambient floor.

        This is a digital-amplitude ratio, not a measured acoustic SNR and not a
        sound-pressure level.
        """

        if self.noise_floor_rms <= 0:
            return 0.0
        return self.frame_rms / self.noise_floor_rms


class InterruptionGate:
    """Decide whether local audio *level* deserves to interrupt the agent.

    The gate deliberately does **not** re-run voice activity detection. The
    Cascade endpoint detector already votes on whether frames are speech-shaped;
    duplicating that would mean two classifiers with two opinions, and in a
    pipeline where a test may inject a scripted classifier it would also mean
    consuming that script twice. So the responsibilities split cleanly:

    * the endpoint detector answers "is this speech-shaped?";
    * this gate answers "is it loud enough relative to *this room*, sustained
      long enough, and not in the echo window right after playback started?".

    A candidate requires both. ``ready`` is a live property of the current
    window, never a sticky flag, so a loud clatter cannot leave the gate open
    for a later unrelated onset.
    """

    def __init__(self, settings: InterruptionGateSettings | None = None) -> None:
        self.settings = settings or InterruptionGateSettings()
        self._noise_floor = self.settings.noise_floor_initial_rms
        self._window: deque[bool] = deque(
            maxlen=self.settings.candidate_window_frames)
        self._frames_since_playback_start = 0
        self._stage = InterruptionStage.PLAYING

    @property
    def stage(self) -> InterruptionStage:
        return self._stage

    @property
    def noise_floor_rms(self) -> float:
        return self._noise_floor

    @property
    def speech_like_frames(self) -> int:
        return sum(self._window)

    @property
    def onset_threshold_rms(self) -> float:
        """The level a frame must clear right now to count as speech-like."""

        return max(
            self._noise_floor * self.settings.onset_snr_ratio,
            self.settings.onset_absolute_rms,
        )

    def playback_started(self) -> None:
        """Arm the gate for a fresh playback window.

        The measured noise floor deliberately survives this call: ambient level
        is a property of the room, not of one answer, and re-learning it from
        scratch on every turn would reopen the transient hole this gate closes.
        """

        self._frames_since_playback_start = 0
        self._window.clear()
        self._stage = InterruptionStage.PLAYING

    def playback_ended(self) -> None:
        self._window.clear()
        self._stage = InterruptionStage.PLAYING

    def mark_candidate(self) -> None:
        """Record that the caller's VAD onset and this gate agreed."""

        self._stage = InterruptionStage.CANDIDATE

    def dismiss(self) -> None:
        """Record that a candidate turned out to be noise.

        Playback continues and the workflow is untouched; only the gate's own
        window resets, and the post-playback cooldown is re-applied so a burst
        of the same noise cannot immediately re-trigger.
        """

        self._window.clear()
        self._frames_since_playback_start = 0
        self._stage = InterruptionStage.PLAYING

    def confirm(self) -> None:
        self._stage = InterruptionStage.CONFIRMED
        self._window.clear()

    def observe_frame(self, *, rms: float, voiced: bool = True) -> FrameAssessment:
        """Measure one frame and report whether the acoustic evidence suffices.

        ``voiced`` exists for direct unit tests and the offline evaluation
        harness, where a VAD verdict is available in the same loop. The
        production caller leaves it at its default and supplies the VAD verdict
        by only acting on ``ready`` when its own detector reports an onset.
        """

        settings = self.settings
        rms = _clamp(float(rms), 0.0, 1.0)
        if not settings.enabled:
            return FrameAssessment(
                stage=self._stage, ready=True, reason=IGNORED_DISABLED,
                noise_floor_rms=self._noise_floor, frame_rms=rms)

        self._frames_since_playback_start += 1
        # Track the floor from every frame, not only the ones a classifier
        # called silence. Gating floor updates on "not speech" is circular: in a
        # continuously loud room no frame is ever quiet enough to update the
        # floor, so the floor stays at its quiet-room value and the whole
        # adaptive mechanism does nothing exactly when it is needed.
        self._update_noise_floor(rms)
        speech_like = bool(voiced) and rms >= self.onset_threshold_rms
        self._window.append(speech_like)

        if self._stage is InterruptionStage.CANDIDATE:
            return self._assessment(rms, True, None)
        if (self._frames_since_playback_start
                <= settings.playback_onset_cooldown_frames):
            return self._assessment(rms, False, IGNORED_PLAYBACK_ONSET)
        if self.speech_like_frames < settings.minimum_candidate_speech_frames:
            return self._assessment(
                rms, False,
                IGNORED_BELOW_NOISE_FLOOR if not speech_like
                else IGNORED_TOO_SHORT)
        return self._assessment(rms, True, None)

    def _assessment(
        self, rms: float, ready: bool, reason: str | None,
    ) -> FrameAssessment:
        return FrameAssessment(
            stage=self._stage, ready=ready, reason=reason,
            noise_floor_rms=self._noise_floor, frame_rms=rms,
            speech_like_frames=self.speech_like_frames)

    def _update_noise_floor(self, rms: float) -> None:
        """Minimum-statistics ambient estimate: follow down fast, creep up slow.

        Classical noise-floor tracking, and it needs no voice-activity verdict
        of its own. Anything quieter than the current estimate is by definition
        part of the background, so the floor follows it down immediately.
        Anything louder might be background or might be speech, so the floor
        only creeps toward it - fast enough that sustained room noise raises the
        bar within a couple of seconds, slow enough that one person talking
        never raises the bar against themselves.
        """

        settings = self.settings
        if rms <= self._noise_floor:
            updated = (
                (1.0 - settings.noise_floor_fall) * self._noise_floor
                + settings.noise_floor_fall * rms
            )
        else:
            updated = min(
                self._noise_floor * (1.0 + settings.noise_floor_rise), rms)
        self._noise_floor = _clamp(
            updated,
            settings.noise_floor_minimum_rms,
            settings.noise_floor_maximum_rms,
        )

    def diagnostics(self) -> dict[str, float | int | bool | str]:
        """Bounded, content-free numbers for the developer detail panel."""

        return {
            "gate_enabled": self.settings.enabled,
            "gate_stage": self._stage.value,
            "noise_floor_rms": round(self._noise_floor, 6),
            "onset_threshold_rms": round(self.onset_threshold_rms, 6),
            "onset_snr_ratio": self.settings.onset_snr_ratio,
            "speech_like_frames": self.speech_like_frames,
            "minimum_candidate_speech_ms": (
                self.settings.minimum_candidate_speech_ms),
            "playback_onset_cooldown_ms": (
                self.settings.playback_onset_cooldown_ms),
        }


#: Deterministic fail-safe lexicon. A stop or pause is the one command a person
#: standing at a bench with both hands occupied must never have to repeat, so it
#: is matched here rather than inferred by a model, and it is honoured even when
#: the speaker cannot be attributed (see ``speaker_attribution``).
#:
#: These are the recognised *roots*; the matcher below anchors them to the whole
#: utterance so a stop word buried inside a longer sentence — "그만두지 말고 계속
#: 알려줘" — can never silently halt the agent.
PRIORITY_STOP_TERMS: tuple[str, ...] = (
    "멈춰", "멈춤", "멈추", "그만", "정지", "일시정지", "중지",
    "잠깐", "잠시만", "스톱",
    "stop", "pause", "wait", "hold on",
)

#: A real stop command is short. This bounds the matcher before the anchored
#: pattern even runs.
PRIORITY_STOP_MAX_CHARACTERS = 24

_PUNCTUATION = re.compile(r"[^0-9A-Za-z가-힣\s]+")

#: Anchored to the entire compacted utterance. Korean politeness endings are
#: enumerated rather than stripped heuristically, because a heuristic stripper
#: is exactly how "그만두지" becomes "그만".
_PRIORITY_STOP_PATTERN = re.compile(
    r"^(?:"
    r"잠깐(?:만)?(?:요)?|잠시(?:만)?(?:요)?|"
    r"(?:일시)?정지(?:요|해|해줘|해주세요|하세요)?|"
    r"멈춰(?:요|주세요|봐)?|멈춤|멈추(?:어|어요|세요)|"
    r"그만(?:요|해|해줘|해주세요|하세요)?|"
    r"중지(?:요|해|해줘|해주세요|하세요)?|"
    r"스톱|"
    r"stop(?:it|now|please|talking|speaking)?|"
    r"pause|wait|holdon"
    r")$"
)


def normalize_command_text(text: str) -> str:
    """Fold punctuation and spacing so the lexicon matches spoken variants."""

    return " ".join(_PUNCTUATION.sub(" ", str(text or "")).lower().split())


def is_priority_stop_command(text: str) -> bool:
    """Report whether a transcript is an explicit stop/pause request.

    Deliberately conservative and anchored: only a short utterance that *is* a
    stop command matches. A stop word appearing inside a longer sentence does
    not, so asking "그만두지 말고 계속 알려줘" keeps the agent talking.
    """

    normalized = normalize_command_text(text)
    if not normalized or len(normalized) > PRIORITY_STOP_MAX_CHARACTERS:
        return False
    return bool(_PRIORITY_STOP_PATTERN.fullmatch(normalized.replace(" ", "")))


@dataclass
class InterruptionMetrics:
    """Counters for the offline evaluation harness.

    These describe *synthetic* fixtures. They are not, and must not be reported
    as, measurements from a real laboratory.
    """

    candidates: int = 0
    confirmed: int = 0
    dismissed: int = 0
    ignored_frames: dict[str, int] = field(default_factory=dict)
    confirmation_latencies_ms: list[int] = field(default_factory=list)

    def record_candidate(self) -> None:
        self.candidates += 1

    def record_frame(self, assessment: FrameAssessment) -> None:
        if assessment.reason:
            self.ignored_frames[assessment.reason] = (
                self.ignored_frames.get(assessment.reason, 0) + 1)

    def record_confirmation(self, latency_ms: int) -> None:
        self.confirmed += 1
        self.confirmation_latencies_ms.append(max(0, int(latency_ms)))

    def record_dismissal(self) -> None:
        self.dismissed += 1

    @property
    def false_candidate_count(self) -> int:
        """Candidates that never earned confirmation — the field-risk number."""

        return max(0, self.candidates - self.confirmed)

    def as_dict(self) -> dict[str, object]:
        latencies = sorted(self.confirmation_latencies_ms)
        return {
            "candidates": self.candidates,
            "confirmed_barge_ins": self.confirmed,
            "dismissed_candidates": self.dismissed,
            "false_candidates": self.false_candidate_count,
            "ignored_frames": dict(sorted(self.ignored_frames.items())),
            "median_confirmation_latency_ms": (
                latencies[len(latencies) // 2] if latencies else None),
            "max_confirmation_latency_ms": latencies[-1] if latencies else None,
        }
