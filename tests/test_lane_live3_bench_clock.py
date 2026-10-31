"""LIVE3: the listening bench keeps audio time, however the machine is loaded.

``tests/test_lane_hear_bench.py`` failed in CI now and then with
``wearer_dropped=1`` while passing locally. The bench's clock is the frames
the fake page has sent, but its fake speech-to-text slept on the wall clock
and the page's playback receipt was a wall-clock sleep too, so how many
frames passed during a transcription or an answer depended on machine
speed. On a fast runner the calibration turn's answer and cooldown ended
late enough in audio time that the first wearer utterance began inside the
cooldown and lost its first frames. These tests pin the fake providers and
the page to the audio clock.
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load_bench():
    spec = importlib.util.spec_from_file_location(
        "hear_listening_bench_live3", ROOT / "scripts" / "hear_listening_bench.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class BenchClockTests(unittest.TestCase):
    def test_fake_transcription_returns_when_its_frames_are_up_not_after_wall_time(self) -> None:
        bench = load_bench()
        clock = bench.AudioClock()
        returned = threading.Event()

        def worker() -> None:
            bench.fake_transcribe(bench.tone(bench.WEARER, 0) * 25)
            returned.set()

        with patch.object(bench, "_CLOCK", clock), patch.object(bench, "PUMP_STALL_SECONDS", 5.0):
            thread = threading.Thread(target=worker, daemon=True)
            thread.start()
            time.sleep(0.15)
            self.assertFalse(returned.is_set(), "the worker returned on the wall clock")
            self.assertTrue(bench.stt_due(clock.frames + bench.STT_FRAMES))
            clock.frames += bench.STT_FRAMES - 1
            time.sleep(0.05)
            self.assertFalse(returned.is_set(), "the worker returned a frame early")
            clock.frames += 1
            self.assertTrue(returned.wait(1.0), "the worker did not return when its frames were up")
            self.assertFalse(bench.stt_due(clock.frames))

    def test_a_stalled_frame_pump_releases_the_worker(self) -> None:
        # The server validates a barge-in's words inside its frame loop, so
        # no frame arrives meanwhile; the worker must not wait for one.
        bench = load_bench()
        clock = bench.AudioClock()
        with patch.object(bench, "_CLOCK", clock), patch.object(bench, "PUMP_STALL_SECONDS", 0.05):
            started = time.monotonic()
            bench.fake_transcribe(bench.tone(bench.WEARER, 0) * 25)
            self.assertLess(time.monotonic() - started, 1.0)

    def test_frame_pump_holds_the_next_frame_until_a_due_transcription_returns(self) -> None:
        bench = load_bench()

        async def run():
            clock = bench.AudioClock()
            session = bench.make_session(clock)
            script = bench.Script()
            script.silence(1.0)
            socket = bench.BenchSocket(script, session, clock)
            for _ in range(10):
                message = await socket.receive()
                self.assertIn("bytes", message)
            with bench._PENDING_LOCK:
                bench._PENDING_STT.append(10)
            try:
                with self.assertRaises(asyncio.TimeoutError):
                    await asyncio.wait_for(socket.receive(), 0.1)
                self.assertEqual(socket.frames_sent, 10)
            finally:
                with bench._PENDING_LOCK:
                    bench._PENDING_STT.remove(10)
            message = await asyncio.wait_for(socket.receive(), 1.0)
            self.assertIn("bytes", message)
            self.assertEqual(socket.frames_sent, 11)

        asyncio.run(run())

    def test_playback_receipt_falls_due_on_the_audio_clock(self) -> None:
        bench = load_bench()

        async def run():
            clock = bench.AudioClock()
            session = bench.make_session(clock)
            script = bench.Script()
            script.silence(1.0)
            socket = bench.BenchSocket(script, session, clock)
            for _ in range(5):
                await socket.receive()
            await socket.send_text('{"type":"audio.segment.start","turn_id":3,"frame_count":10}')
            delivered = []
            for _ in range(12):
                message = await socket.receive()
                if "text" in message:
                    delivered.append((socket.frames_sent, message["text"]))
            self.assertEqual(len(delivered), 1, delivered)
            self.assertEqual(delivered[0][0], 15)
            self.assertIn('"playback.ended"', delivered[0][1])
            self.assertIn('"turn_id": 3', delivered[0][1])

        asyncio.run(run())

    def test_a_slow_worker_does_not_change_what_the_bench_hears(self) -> None:
        bench = load_bench()

        def short_script():
            script = bench.Script()
            script.silence(1.0)
            script.control({"type": "client.voice_calibration", "action": "start"})
            script.silence(3.0)
            script.say(bench.CALIBRATION_SENTENCE_FALLBACK, kind="calibration")
            script.silence(5.5)
            for _ in range(2):
                script.say("현재 단계 알려줘", trial="normal")
                script.silence(6.0)
            return script

        logging.disable(logging.CRITICAL)
        try:
            _, plain_sent = bench.run_bench(short_script(), measured=False)
            with patch.object(bench, "_WORKER_WALL_DELAY_SECONDS", 0.3):
                _, slow_sent = bench.run_bench(short_script(), measured=False)
        finally:
            logging.disable(logging.NOTSET)
        plain_at = [(at, m.get("text")) for at, m in plain_sent if m.get("type") == "transcript"]
        slow_at = [(at, m.get("text")) for at, m in slow_sent if m.get("type") == "transcript"]
        # The calibration sentence is written down too (no voice is measured
        # in a run), then the two wearer utterances -- the same words at the
        # same frames, however late the worker thread started.
        self.assertEqual([text for _, text in plain_at][-2:], ["현재 단계 알려줘"] * 2, plain_at)
        self.assertEqual([text for _, text in plain_at], [text for _, text in slow_at])
        for (first, _), (second, _) in zip(plain_at, slow_at):
            self.assertLessEqual(abs(first - second), 2, (plain_at, slow_at))


if __name__ == "__main__":
    unittest.main()
