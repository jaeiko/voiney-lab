"""Deterministic latency filler scheduling without Provider access."""

from __future__ import annotations

import asyncio
import threading
import unittest

from voice_workflow_agent.cascade_filler import CascadeFiller
from voice_workflow_agent.configuration import cascade_filler_delay_ms


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


class CascadeFillerTests(unittest.IsolatedAsyncioTestCase):
    def make_filler(self, *, fail=False, current=True):
        clock = FakeClock()
        gate = asyncio.Event()
        events = []
        audio = []
        clears = []
        calls = []

        async def sleep(_):
            await gate.wait()

        async def synthesize(text, language):
            calls.append((text, language))
            if fail:
                raise RuntimeError("fake failure")
            return b"\x00\x00" * 320

        async def send_audio(turn, generation, pcm):
            audio.append((turn, generation, pcm))

        async def send_event(kind, **fields):
            events.append((kind, fields))

        async def send_clear(turn, generation):
            clears.append((turn, generation))

        filler = CascadeFiller(
            turn_id=3,
            generation=7,
            language="ko",
            delay_ms=700,
            synthesize=synthesize,
            send_audio=send_audio,
            send_event=send_event,
            send_clear=send_clear,
            is_current=lambda turn, generation: current,
            clock=clock,
            sleep=sleep,
        )
        return filler, gate, clock, events, audio, clears, calls

    async def test_primary_before_threshold_skips_filler(self):
        filler, _, _, events, audio, clears, calls = self.make_filler()
        filler.start()
        await asyncio.sleep(0)
        await filler.primary_ready()
        self.assertFalse(audio)
        self.assertFalse(calls)
        self.assertFalse(clears)
        self.assertEqual(filler.snapshot.outcome, "cancelled")
        self.assertEqual(events[-1][1]["outcome"], "cancelled")

    async def test_delayed_turn_plays_once_and_primary_clears_without_overlap(self):
        filler, gate, clock, events, audio, clears, calls = self.make_filler()
        filler.start()
        await asyncio.sleep(0)
        clock.value = 0.7
        gate.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(audio), 1)
        self.assertEqual(filler.snapshot.outcome, "played")
        await filler.primary_ready()
        await filler.primary_ready()
        self.assertEqual(clears, [(3, 7)])
        self.assertEqual(len(audio), 1)
        self.assertEqual(events[-1][1]["outcome"], "played")

    async def test_failure_and_stale_generation_never_fail_primary(self):
        filler, gate, _, _, audio, _, _ = self.make_filler(fail=True)
        filler.start()
        await asyncio.sleep(0)
        gate.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.assertEqual(filler.snapshot.outcome, "failed")
        self.assertFalse(audio)
        await filler.primary_ready()

        stale, gate, _, events, audio, clears, _ = self.make_filler(current=False)
        stale.start()
        gate.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.assertFalse(audio)
        self.assertFalse(clears)
        self.assertFalse(events)

    async def test_threaded_synchronous_tts_cannot_block_primary_scheduler(self):
        started = threading.Event()
        release = threading.Event()
        gate = asyncio.Event()
        events = []
        audio = []
        clears = []

        def blocking_tts() -> bytes:
            started.set()
            release.wait(timeout=2)
            return b"\x00\x00" * 320

        async def synthesize(_text, _language):
            return await asyncio.to_thread(blocking_tts)

        async def send_event(kind, **fields):
            events.append((kind, fields))

        filler = CascadeFiller(
            turn_id=4,
            generation=8,
            language="ko",
            delay_ms=700,
            synthesize=synthesize,
            send_audio=lambda turn, generation, pcm: self._append_async(
                audio, (turn, generation, pcm)
            ),
            send_event=send_event,
            send_clear=lambda turn, generation: self._append_async(
                clears, (turn, generation)
            ),
            is_current=lambda turn, generation: True,
            clock=FakeClock(),
            sleep=lambda _: gate.wait(),
        )
        filler.start()
        await asyncio.sleep(0)
        gate.set()
        await asyncio.wait_for(asyncio.to_thread(started.wait, 1), timeout=1.5)
        try:
            await asyncio.wait_for(filler.primary_ready(), timeout=0.25)
        finally:
            release.set()
        self.assertFalse(audio)
        self.assertFalse(clears)
        self.assertIn(filler.snapshot.outcome, {"cancelled", "skipped"})
        self.assertTrue(any(item[1]["outcome"] in {"cancelled", "skipped"}
                            for item in events))

    @staticmethod
    async def _append_async(target, value):
        target.append(value)

    def test_delay_configuration_is_bounded(self):
        self.assertEqual(cascade_filler_delay_ms({}), 700)
        self.assertEqual(
            cascade_filler_delay_ms({"CASCADE_FILLER_DELAY_MS": "900"}),
            900,
        )
        for value in ("99", "5001", "not-a-number"):
            with self.assertRaises(ValueError):
                cascade_filler_delay_ms({"CASCADE_FILLER_DELAY_MS": value})


if __name__ == "__main__":
    unittest.main()
