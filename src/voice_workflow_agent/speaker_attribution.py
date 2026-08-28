"""Provider-neutral speaker diarization and participant-aware mutation policy.

## Diarization is not authentication

Speaker **diarization** answers "how many different voices are in this audio, and
which words belong to which one". Speaker **verification** answers "is this voice
the enrolled person named Kim". They are not the same question, and this module
only ever answers the first.

Nothing here is biometric authentication. A diarized label such as ``speaker_0``
is an *acoustic* label scoped to one transcription: the provider is not
contractually required to keep it stable across a reconnect, and this module
therefore never treats it as durable identity. Application identity keeps coming
from login and lab membership (``identity.py`` / ``workspace_store.py``); a label
is only ever *associated* with a participant after an explicit human
confirmation, and the association dies with the session.

``SPEAKER_VERIFICATION_IMPLEMENTED`` is the extension point for real
verification. It is ``False``, it is not implemented, it is not validated, and
no code path may branch on it being anything else.

## What the policy is for

In a shared lab, the microphone hears more than the person running the
experiment. The rule this module enforces is narrow and conservative:

* a **confirmed participant** may drive the experiment through the existing
  deterministic admission gates — this module adds no new authority;
* an **unknown nearby speaker** may ask questions, but a state-changing command
  from an unknown voice never silently mutates an experiment;
* **stop and pause are fail-safe**. A clearly recognised "멈춰" is honoured
  whoever says it, because refusing to stop is the more dangerous failure;
* **overlapping speakers fail closed**. Single-microphone overlap is not solved,
  this module does not pretend otherwise, and ambiguous attribution asks one
  person to repeat instead of guessing.

Diarization confidence never becomes a scientific authority: the worst outcome
this module can produce is "ask the human again".
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum

from voice_workflow_agent.configuration import bounded_integer


#: Not implemented, not validated, and deliberately unreachable. Kept as a named
#: seam so a future enrolment feature has an obvious home instead of being
#: retrofitted into the diarization path, which would silently turn an acoustic
#: label into an identity claim.
SPEAKER_VERIFICATION_IMPLEMENTED = False

_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")


@dataclass(frozen=True)
class SpeakerDiarizationSettings:
    """Feature-gated diarization request policy.

    ``diarize`` is a documented field of the batch transcription endpoint this
    repository already calls, so enabling it adds a field the provider defines
    rather than one this repository invented. It stays **off by default** for the
    pilot: the participant policy below only tightens what a session accepts, and
    turning it on without a confirmed participant roster would make an unknown
    voice out of a legitimate one.
    """

    enabled: bool = False

    #: A transcription carrying more distinct labels than this is treated as
    #: unattributable crosstalk rather than a conversation to reason about.
    maximum_expected_speakers: int = 4

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None,
    ) -> "SpeakerDiarizationSettings":
        env = os.environ if environment is None else environment
        return cls(
            enabled=bounded_integer(
                env, "XAI_STT_DIARIZE", 0, 0, 1) == 1,
            maximum_expected_speakers=bounded_integer(
                env, "VOICE_WORKFLOW_AGENT_MAX_SESSION_SPEAKERS", 4, 2, 8),
        )


@dataclass(frozen=True)
class TranscriptSegment:
    """One run of consecutive words attributed to a single acoustic label.

    Provider-neutral on purpose: no raw provider event shape reaches workflow
    state, so swapping or losing the provider changes this module and nothing
    downstream.
    """

    text: str
    speaker_label: str | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    final: bool = True
    confidence: float | None = None

    @property
    def duration_ms(self) -> int | None:
        if self.start_ms is None or self.end_ms is None:
            return None
        return max(0, self.end_ms - self.start_ms)


def _word_text(word: Mapping[str, object]) -> str:
    """Read a word regardless of which documented key the provider used.

    The published response uses ``text``; this repository's existing adapter also
    retains ``word``. Accepting both keeps a provider revision from silently
    emptying every segment.
    """

    for key in ("text", "word"):
        value = word.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _word_milliseconds(value: object) -> int | None:
    """Convert a documented word timestamp (seconds, float) to whole ms."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value < 0:
        return None
    return int(round(float(value) * 1000))


def normalize_speaker_label(value: object) -> str | None:
    """Turn a provider speaker value into a bounded, session-scoped label."""

    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return f"speaker_{value}"
    if isinstance(value, float) and float(value).is_integer():
        return f"speaker_{int(value)}"
    if isinstance(value, str):
        candidate = value.strip()
        if candidate and _LABEL.fullmatch(candidate):
            return candidate
    return None


def transcript_segments(
    words: Iterable[Mapping[str, object]],
    *,
    fallback_text: str = "",
    final: bool = True,
) -> tuple[TranscriptSegment, ...]:
    """Group provider word objects into speaker-contiguous segments.

    When the provider supplies no usable words — diarization disabled, an older
    response shape, or a provider that dropped the field — this returns one
    unattributed segment carrying the whole transcript. That is the graceful
    fallback: the caller still gets text, and ``speaker_label`` is ``None``,
    which the policy below reads as "identity is unknown", never as "identity is
    confirmed".
    """

    segments: list[TranscriptSegment] = []
    current_label: str | None = None
    current_words: list[str] = []
    current_start: int | None = None
    current_end: int | None = None

    def flush() -> None:
        if not current_words:
            return
        segments.append(TranscriptSegment(
            text=" ".join(current_words),
            speaker_label=current_label,
            start_ms=current_start,
            end_ms=current_end,
            final=final,
        ))

    for word in words or ():
        if not isinstance(word, Mapping):
            continue
        text = _word_text(word)
        if not text:
            continue
        label = normalize_speaker_label(word.get("speaker"))
        if current_words and label != current_label:
            flush()
            current_words = []
            current_start = None
            current_end = None
        current_label = label
        current_words.append(text)
        start = _word_milliseconds(word.get("start"))
        end = _word_milliseconds(word.get("end"))
        if start is not None and (current_start is None or start < current_start):
            current_start = start
        if end is not None and (current_end is None or end > current_end):
            current_end = end
    flush()

    if not segments:
        text = str(fallback_text or "").strip()
        if not text:
            return ()
        return (TranscriptSegment(text=text, speaker_label=None, final=final),)
    return tuple(segments)


def distinct_speaker_labels(
    segments: Sequence[TranscriptSegment],
) -> tuple[str, ...]:
    labels: list[str] = []
    for segment in segments:
        if segment.speaker_label and segment.speaker_label not in labels:
            labels.append(segment.speaker_label)
    return tuple(labels)


@dataclass(frozen=True)
class Participant:
    """A person the lab already knows, taken from login and lab membership."""

    participant_id: str
    display_name: str
    role: str = "researcher"

    def __post_init__(self) -> None:
        if not _LABEL.fullmatch(self.participant_id):
            raise ValueError("participant_id is invalid")
        if not str(self.display_name).strip():
            raise ValueError("participant display_name is required")


class SessionParticipants:
    """Ephemeral, session-scoped map from acoustic label to known participant.

    Everything here dies with the voice session. No voiceprint is derived, no
    audio is retained, and nothing is written to durable storage — a diarized
    label is an artefact of one transcription stream, not a credential.
    """

    def __init__(self) -> None:
        self._roster: dict[str, Participant] = {}
        self._labels: dict[str, str] = {}

    @property
    def roster(self) -> tuple[Participant, ...]:
        return tuple(self._roster.values())

    @property
    def confirmed_labels(self) -> Mapping[str, str]:
        return dict(self._labels)

    @property
    def has_confirmed_labels(self) -> bool:
        return bool(self._labels)

    def enrol(self, participant: Participant) -> None:
        """Add someone to this experiment's roster. Not a voice enrolment."""

        self._roster[participant.participant_id] = participant

    def confirm_label(self, speaker_label: str, participant_id: str) -> bool:
        """Associate an acoustic label with a rostered participant.

        Called only from an explicit human confirmation ("이 목소리가 누구인지
        확인해 주세요"), never inferred from the audio itself.
        """

        label = normalize_speaker_label(speaker_label)
        if label is None or participant_id not in self._roster:
            return False
        self._labels[label] = participant_id
        return True

    def release_label(self, speaker_label: str) -> bool:
        label = normalize_speaker_label(speaker_label)
        if label is None:
            return False
        return self._labels.pop(label, None) is not None

    def forget_labels(self) -> None:
        """Drop every acoustic association.

        Called on reconnect and on stream restart, because the provider does not
        guarantee that ``speaker_0`` is the same human after either.
        """

        self._labels.clear()

    def reset(self) -> None:
        self._roster.clear()
        self._labels.clear()

    def participant_for(self, speaker_label: str | None) -> Participant | None:
        if not speaker_label:
            return None
        participant_id = self._labels.get(speaker_label)
        if participant_id is None:
            return None
        return self._roster.get(participant_id)


class MutationOutcome(str, Enum):
    """What the speaker policy permits for one transcript."""

    #: Continue into the existing deterministic admission gates unchanged.
    ALLOW = "allow"
    #: Do not mutate. Ask the active participant to confirm the command.
    CONFIRM_REQUIRED = "confirm_required"
    #: Do not mutate. Voices overlapped and attribution is not trustworthy.
    OVERLAP_AMBIGUOUS = "overlap_ambiguous"


#: Bench-facing Korean guidance. Kept here beside the policy so the reason a
#: mutation was refused and the sentence the researcher hears cannot drift apart.
UNKNOWN_SPEAKER_MESSAGE = (
    "등록된 참여자의 목소리로 확인되지 않아 실험 상태는 변경하지 않았습니다. "
    "실험을 진행 중인 분이 다시 말씀해 주세요."
)
OVERLAP_MESSAGE = (
    "여러 목소리가 겹쳐 정확히 확인하지 못했습니다. "
    "한 분씩 다시 말씀해 주세요. 실험 상태는 변경하지 않았습니다."
)


@dataclass(frozen=True)
class SpeakerDecision:
    """The policy verdict for one transcript, with the evidence behind it."""

    outcome: MutationOutcome
    reason: str
    speaker_labels: tuple[str, ...] = ()
    participant_id: str | None = None
    message: str | None = None

    @property
    def mutation_allowed(self) -> bool:
        return self.outcome is MutationOutcome.ALLOW

    @property
    def speaker_identified(self) -> bool:
        """Whether a *known participant* was attributed.

        Never means "this voice was biometrically verified" — only that a human
        previously confirmed this session-scoped label.
        """

        return self.participant_id is not None


def evaluate_speaker_policy(
    segments: Sequence[TranscriptSegment],
    participants: SessionParticipants | None,
    *,
    diarization_enabled: bool,
    mutating: bool,
    priority_stop: bool = False,
    settings: SpeakerDiarizationSettings | None = None,
) -> SpeakerDecision:
    """Decide whether this transcript may reach the mutation boundary.

    This never authorises a mutation on its own — an ``ALLOW`` only means the
    speaker policy raised no objection, and every existing deterministic gate
    still runs afterwards.
    """

    policy = settings or SpeakerDiarizationSettings()
    labels = distinct_speaker_labels(segments)

    if priority_stop:
        # Fail-safe: whoever says it, stopping is always the safer outcome.
        return SpeakerDecision(
            MutationOutcome.ALLOW, "priority_stop_fail_safe", labels)
    if not diarization_enabled:
        # No claim of identity is made or implied when diarization is off.
        return SpeakerDecision(
            MutationOutcome.ALLOW, "diarization_disabled", labels)
    if len(labels) > 1 or len(labels) > policy.maximum_expected_speakers:
        if not mutating:
            return SpeakerDecision(
                MutationOutcome.ALLOW, "overlap_read_only", labels)
        return SpeakerDecision(
            MutationOutcome.OVERLAP_AMBIGUOUS, "overlapping_speakers", labels,
            message=OVERLAP_MESSAGE)
    if not mutating:
        return SpeakerDecision(MutationOutcome.ALLOW, "read_only_request", labels)

    if participants is None or not participants.has_confirmed_labels:
        # Diarization is on but nobody has been confirmed yet. Refusing every
        # command here would brick the session, so the roster gate only applies
        # once at least one participant has actually been confirmed.
        return SpeakerDecision(
            MutationOutcome.ALLOW, "no_confirmed_participants", labels)

    if not labels:
        # Diarization is configured but this response carried no labels: an
        # older response shape, a provider degradation, or a reconnect. Degrade
        # gracefully rather than bricking the bench — and never let this path
        # imply the speaker was identified.
        return SpeakerDecision(
            MutationOutcome.ALLOW, "diarization_unavailable", labels)

    participant = participants.participant_for(labels[0])
    if participant is None:
        return SpeakerDecision(
            MutationOutcome.CONFIRM_REQUIRED, "unknown_speaker", labels,
            message=UNKNOWN_SPEAKER_MESSAGE)
    return SpeakerDecision(
        MutationOutcome.ALLOW, "confirmed_participant", labels,
        participant_id=participant.participant_id)


def diarization_diagnostics(
    segments: Sequence[TranscriptSegment],
    decision: SpeakerDecision,
    *,
    diarization_enabled: bool,
) -> dict[str, object]:
    """Bounded, content-free diagnostics for the developer detail panel.

    Deliberately excludes transcript text: this dictionary reaches logs and
    metrics, which the security rules keep free of conversation content.
    """

    return {
        "diarization_enabled": bool(diarization_enabled),
        "diarization_available": bool(distinct_speaker_labels(segments)),
        "segment_count": len(segments),
        "speaker_label_count": len(distinct_speaker_labels(segments)),
        "speaker_policy_outcome": decision.outcome.value,
        "speaker_policy_reason": decision.reason,
        "speaker_identity_verified": False,  # never true: see module docstring
    }
