"""Generation-scoped latency filler scheduling for Cascade turns.

The cue is chosen by ``CASCADE_FILLER_MODE``:

* ``tone`` (the default) -- the browser plays one short, quiet tone. Nothing is
  synthesized and nothing is said, so the cue costs no TTS call and cannot
  arrive later than the answer because a sentence took long to make.
* ``phrase`` -- the earlier behaviour, kept so a deployment can go back to it:
  one synthesized sentence from ``FILLER_PHRASES``.

Either cue carries no protocol content, starts only once ``delay_ms`` has passed
with the primary turn still pending, and is cleared the moment the primary
audio is admitted.

In tone mode a turn that is still pending at ``CASCADE_FILLER_STATUS_DELAY_MS``
(1.5 s by default) also hears, once, what the server is doing at that moment
(``FILLER_STATUS_PHRASES``) -- read from the turn's own progress state, the same
state its Turn card shows, and only for states that name real work. A session
never hears the same status sentence twice in a row.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field

from voiney_lab.configuration import ConfigurationError


FILLER_MODE_ENV = "CASCADE_FILLER_MODE"
FILLER_MODE_TONE = "tone"
FILLER_MODE_PHRASE = "phrase"
FILLER_MODES = frozenset({FILLER_MODE_TONE, FILLER_MODE_PHRASE})
DEFAULT_FILLER_MODE = FILLER_MODE_TONE


def cascade_filler_mode(environment: Mapping[str, str] | None = None) -> str:
    """Return the configured cue: ``tone`` unless ``phrase`` is asked for."""

    env = os.environ if environment is None else environment
    raw = env.get(FILLER_MODE_ENV, "").strip().casefold() or DEFAULT_FILLER_MODE
    if raw not in FILLER_MODES:
        raise ConfigurationError(
            f"{FILLER_MODE_ENV} must be one of: "
            + ", ".join(sorted(FILLER_MODES))
        )
    return raw


FILLER_STATUS_DELAY_ENV = "CASCADE_FILLER_STATUS_DELAY_MS"
DEFAULT_FILLER_STATUS_DELAY_MS = 1500


def cascade_filler_status_delay_ms(
    environment: Mapping[str, str] | None = None,
) -> int:
    """Return how long a turn may stay quiet before it says what it is doing."""

    env = os.environ if environment is None else environment
    raw = env.get(
        FILLER_STATUS_DELAY_ENV, str(DEFAULT_FILLER_STATUS_DELAY_MS)
    ).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(
            f"{FILLER_STATUS_DELAY_ENV} must be an integer"
        ) from exc
    if not 200 <= value <= 10000:
        raise ConfigurationError(
            f"{FILLER_STATUS_DELAY_ENV} must be between 200 and 10000"
        )
    return value


#: What a still-pending turn may say about itself, keyed by its progress state.
#: Each sentence is the spoken form of the Turn card's label for that state
#: ("절차 확인 중…" -> "절차를 확인하고 있습니다."), so it claims nothing the
#: screen does not already show. A state that is absent here -- ``listening``,
#: ``synthesizing`` (the answer's own voice is being made), ``playing`` and every
#: terminal state -- says nothing.
FILLER_STATUS_PHRASES = {
    "ko": {
        "transcribing": "음성을 인식하고 있습니다.",
        "routing": "요청을 확인하고 있습니다.",
        "checking_protocol": "절차를 확인하고 있습니다.",
        "checking_approved_information": "승인된 정보를 확인하고 있습니다.",
        "composing": "답변을 작성하고 있습니다.",
    },
    "en": {
        "transcribing": "Recognizing your speech.",
        "routing": "Checking your request.",
        "checking_protocol": "Checking the procedure.",
        "checking_approved_information": "Checking approved information.",
        "composing": "Writing the answer.",
    },
    "vi": {
        "transcribing": "Đang nhận dạng giọng nói.",
        "routing": "Đang kiểm tra yêu cầu của bạn.",
        "checking_protocol": "Đang kiểm tra quy trình.",
        "checking_approved_information": "Đang kiểm tra thông tin đã được phê duyệt.",
        "composing": "Đang soạn câu trả lời.",
    },
}


@dataclass
class FillerSessionMemory:
    """What one listening session has already said while waiting.

    ``last_status_phrase`` keeps a session from saying the same status sentence
    twice in a row; ``status_audio`` keeps each sentence's synthesized audio so
    it is made once per session, not once per turn.
    """

    last_status_phrase: str | None = None
    status_audio: dict[tuple[str, str], bytes] = field(default_factory=dict)


FILLER_PHRASES = {
    "ko": "요청을 확인하고 있습니다.",
    "en": "I’m checking your request.",
    "vi": "Tôi đang kiểm tra yêu cầu của bạn.",
}


@dataclass(frozen=True)
class FillerSnapshot:
    outcome: str
    scheduled_ms: int
    started_ms: int | None
    finished_ms: int | None


class CascadeFiller:
    """Play one non-content cue only while the primary turn is still pending."""

    def __init__(
        self,
        *,
        turn_id: int,
        generation: int,
        language: str,
        delay_ms: int,
        synthesize: Callable[[str, str], Awaitable[bytes]],
        send_audio: Callable[[int, int, bytes], Awaitable[None]],
        send_event: Callable[..., Awaitable[None]],
        send_clear: Callable[[int, int], Awaitable[None]],
        is_current: Callable[[int, int], bool],
        clock: Callable[[], float],
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        mode: str = FILLER_MODE_PHRASE,
        send_tone: Callable[[int, int], Awaitable[None]] | None = None,
        status_delay_ms: int | None = None,
        activity: Callable[[], str | None] | None = None,
        memory: FillerSessionMemory | None = None,
    ) -> None:
        if mode not in FILLER_MODES:
            raise ValueError(f"unknown filler mode: {mode!r}")
        if mode == FILLER_MODE_TONE and send_tone is None:
            raise ValueError("a tone filler needs send_tone")
        self.mode = mode
        self._send_tone = send_tone
        # The status sentence needs all three: when, what is happening, and
        # what this session last said. Without them a tone turn stays a tone.
        self.status_delay_ms = status_delay_ms
        self._activity = activity
        self._memory = memory
        self.turn_id = turn_id
        self.generation = generation
        self.language = language if language in FILLER_PHRASES else "ko"
        self.delay_ms = delay_ms
        self._synthesize = synthesize
        self._send_audio = send_audio
        self._send_event = send_event
        self._send_clear = send_clear
        self._is_current = is_current
        self._clock = clock
        self._sleep = sleep
        self._origin = clock()
        self._task: asyncio.Task[None] | None = None
        self._primary_ready = False
        self._started = False
        self._cleared = False
        self._outcome = "scheduled"
        self._started_ms: int | None = None
        self._finished_ms: int | None = None

    def _elapsed_ms(self) -> int:
        return max(0, round((self._clock() - self._origin) * 1000))

    @property
    def snapshot(self) -> FillerSnapshot:
        return FillerSnapshot(
            self._outcome, 0, self._started_ms, self._finished_ms
        )

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def _outcome_event(self, outcome: str) -> None:
        self._outcome = outcome
        self._finished_ms = self._elapsed_ms()
        if self._is_current(self.turn_id, self.generation):
            await self._send_event(
                "turn.filler",
                turn_id=self.turn_id,
                generation=self.generation,
                outcome=outcome,
                cue=self.mode,
                scheduled_ms=0,
                started_ms=self._started_ms,
                finished_ms=self._finished_ms,
            )

    async def _run(self) -> None:
        try:
            if not self._is_current(self.turn_id, self.generation):
                self._outcome = "cancelled"
                self._finished_ms = self._elapsed_ms()
                return
            await self._send_event(
                "turn.filler",
                turn_id=self.turn_id,
                generation=self.generation,
                outcome="scheduled",
                cue=self.mode,
                scheduled_ms=0,
                delay_ms=self.delay_ms,
            )
            await self._sleep(self.delay_ms / 1000)
            if self._primary_ready or not self._is_current(
                self.turn_id, self.generation
            ):
                await self._outcome_event("skipped")
                return
            if self.mode == FILLER_MODE_TONE:
                await self._play_tone()
                await self._say_status_if_still_waiting()
                return
            try:
                pcm = await self._synthesize(
                    FILLER_PHRASES[self.language], self.language
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                await self._outcome_event("failed")
                return
            if (
                self._primary_ready
                or not pcm
                or not self._is_current(self.turn_id, self.generation)
            ):
                await self._outcome_event("skipped")
                return
            self._started = True
            self._started_ms = self._elapsed_ms()
            await self._send_audio(self.turn_id, self.generation, pcm)
            await self._outcome_event("played")
        except asyncio.CancelledError:
            if self._outcome not in {"played", "skipped", "failed"}:
                self._outcome = "cancelled"
                self._finished_ms = self._elapsed_ms()
            raise

    async def _play_tone(self) -> None:
        """Ask the browser for the tone; the server sends no audio for it."""

        self._started = True
        self._started_ms = self._elapsed_ms()
        await self._send_tone(self.turn_id, self.generation)
        await self._outcome_event("played")

    async def _say_status_if_still_waiting(self) -> None:
        """Past the status delay, say once what the server is doing right now."""

        if (
            self.status_delay_ms is None
            or self._activity is None
            or self._memory is None
        ):
            return
        remaining_ms = self.status_delay_ms - self.delay_ms
        if remaining_ms > 0:
            await self._sleep(remaining_ms / 1000)
        if self._primary_ready or not self._is_current(
            self.turn_id, self.generation
        ):
            return
        activity = self._activity()
        phrase = FILLER_STATUS_PHRASES[self.language].get(activity or "")
        if phrase is None or phrase == self._memory.last_status_phrase:
            return
        key = (self.language, phrase)
        pcm = self._memory.status_audio.get(key)
        if pcm is None:
            try:
                pcm = await self._synthesize(phrase, self.language)
            except asyncio.CancelledError:
                raise
            except Exception:
                await self._status_event("failed", activity)
                return
            if pcm:
                self._memory.status_audio[key] = pcm
        # Making the audio took time: say it only if it is still true.
        if (
            self._primary_ready
            or not pcm
            or not self._is_current(self.turn_id, self.generation)
            or self._activity() != activity
        ):
            return
        self._memory.last_status_phrase = phrase
        await self._send_audio(self.turn_id, self.generation, pcm)
        await self._status_event("played", activity, text=phrase)

    async def _status_event(
        self, outcome: str, activity: str | None, **fields: object
    ) -> None:
        if self._is_current(self.turn_id, self.generation):
            await self._send_event(
                "turn.filler",
                turn_id=self.turn_id,
                generation=self.generation,
                outcome=outcome,
                cue="status",
                activity=activity,
                finished_ms=self._elapsed_ms(),
                **fields,
            )

    async def primary_ready(self) -> None:
        """Cancel/clear filler before the primary segment is admitted."""

        if self._primary_ready:
            return
        self._primary_ready = True
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if (
            self._started
            and not self._cleared
            and self._is_current(self.turn_id, self.generation)
        ):
            await self._send_clear(self.turn_id, self.generation)
            self._cleared = True
        if self._outcome in {"scheduled", "cancelled"}:
            await self._outcome_event(
                "cancelled" if self._outcome == "cancelled" else "skipped"
            )

    async def cancel(self) -> None:
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if (
            self._started
            and not self._cleared
            and self._is_current(self.turn_id, self.generation)
        ):
            await self._send_clear(self.turn_id, self.generation)
            self._cleared = True
        if self._outcome not in {"played", "skipped", "failed", "cancelled"}:
            await self._outcome_event("cancelled")
