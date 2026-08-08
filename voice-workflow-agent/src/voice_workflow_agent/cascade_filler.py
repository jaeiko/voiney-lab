"""Generation-scoped latency filler scheduling for Cascade turns."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass


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
    ) -> None:
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
                scheduled_ms=0,
                delay_ms=self.delay_ms,
            )
            await self._sleep(self.delay_ms / 1000)
            if self._primary_ready or not self._is_current(
                self.turn_id, self.generation
            ):
                await self._outcome_event("skipped")
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
