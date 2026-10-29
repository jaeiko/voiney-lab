"""Lane HEAR (2026-10-25): the listening bench's four numbers after the fixes.

``scripts/hear_listening_bench.py`` drives the real voice socket the way the
current screen does during a run, with synthetic microphone frames, a fake
speech-to-text and a fake speech output (no provider is called). Before
lane HEAR it measured, on the same script: 3 of 9 wearer utterances
dropped, 4 of 4 sentences with a pause split, 3 answers with sound while
paused and 1 noise question (lane report, section 3). These are the
numbers after. Lane HEAR2 added the timer stage, a noise chain and a
speech-to-text outage to the same script (its report, section 4).
"""

from __future__ import annotations

import importlib.util
import asyncio
import logging
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_bench():
    spec = importlib.util.spec_from_file_location(
        "hear_listening_bench", ROOT / "scripts" / "hear_listening_bench.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module  # its dataclasses look their module up
    spec.loader.exec_module(module)
    return module


class ListeningBenchTests(unittest.TestCase):
    def test_bench_accepts_cancelled_playback_clear_as_a_terminal_signal(self) -> None:
        bench = load_bench()

        async def run():
            clock = bench.AudioClock()
            session = bench.make_session(clock)
            socket = bench.BenchSocket(bench.Script(), session, clock)
            await socket.send_text('{"type":"speech.start","turn_id":6}')

            # ``cancel_cascade_generation`` always sends this event, while a
            # following terminal turn.state exists only when progress was
            # still registered for the superseded turn.
            await socket.send_text(
                '{"type":"cascade.playback.clear","turn_id":6,'
                '"state":"cancelled","reason":"continued_speech"}')

            self.assertEqual(socket.pending_turns, set())

        asyncio.run(run())

    def test_bench_waits_for_a_terminal_signal_after_the_session_enters_cooldown(self) -> None:
        bench = load_bench()
        from voiney_lab.vad import TurnState

        async def run():
            clock = bench.AudioClock()
            session = bench.make_session(clock)
            socket = bench.BenchSocket(bench.Script(), session, clock)
            await socket.send_text('{"type":"speech.start","turn_id":7}')
            session.detector.state = TurnState.COOLDOWN

            async def late_terminal_signal():
                await asyncio.sleep(.06)
                await socket.send_text(
                    '{"type":"speech.dropped","turn_id":7,"reason":"stt_empty"}')

            worker = asyncio.create_task(late_terminal_signal())
            try:
                message = await socket.receive()
                self.assertEqual(message['type'], 'websocket.disconnect')
                self.assertTrue(
                    worker.done(),
                    'the bench disconnected while the turn still lacked a terminal signal',
                )
            finally:
                await worker

        asyncio.run(run())

    def test_bench_waits_for_the_last_provider_failure_before_disconnect(self) -> None:
        bench = load_bench()
        bench.SPEED = 100.0
        from voiney_lab.vad import TurnState

        async def run():
            clock = bench.AudioClock()
            session = bench.make_session(clock)
            session.detector.state = TurnState.PROCESSING
            socket = bench.BenchSocket(bench.Script(), session, clock)
            await socket.send_text('{"type":"speech.start","turn_id":8}')
            async def late_provider_failure():
                await asyncio.sleep(.06)
                await socket.send_text(
                    '{"type":"speech.dropped","turn_id":8,"reason":"stt_failed"}')
                session.detector.state = TurnState.IDLE
            worker = asyncio.create_task(late_provider_failure())
            message = await socket.receive()
            self.assertEqual(message['type'], 'websocket.disconnect')
            self.assertTrue(worker.done(), 'the bench disconnected before the pending STT result')
            self.assertEqual(len(socket.sent), 2)
            await worker
        asyncio.run(run())

    def test_no_wearer_word_dropped_no_sentence_split_nothing_said_in_a_pause_no_noise_question(self) -> None:
        bench = load_bench()
        logging.disable(logging.CRITICAL)
        try:
            result, _ = bench.run_bench()
        finally:
            logging.disable(logging.NOTSET)
        self.assertEqual(result["wearer_utterances"], 9)
        self.assertEqual(result["wearer_dropped"], 0, result)
        self.assertEqual(result["split_sentences"], 4)
        self.assertEqual(result["split"], 0, result)
        self.assertEqual(result["paused_replies"], 0, result)
        self.assertEqual(result["noise_questions"], 0, result)
        self.assertTrue(result["resume_heard"], result)
        self.assertFalse(result["calibration_measured"], "a run measured the voice")
        # Lane HEAR2 (2026-10-26). Before (fe87ae5): 1 of 6 timer-stage
        # utterances unanswered ("타이머 일시정지해 줘" paused the run, and the
        # next words met the pause); the word said inside 30 s of room noise
        # never reached a transcript; the provider outage was 3 turn errors,
        # nothing shown or said.
        self.assertEqual(result["timer_stage_utterances"], 6)
        self.assertEqual(result["timer_stage_unanswered"], 0, result)
        self.assertTrue(result["timer_paused_the_timer"], result)
        self.assertFalse(result["timer_paused_the_run"], result)
        self.assertIsNotNone(result["noise_chain_answer_s"], "the word inside the noise was never heard")
        self.assertLess(result["noise_chain_answer_s"], 12.0, result)
        self.assertEqual(result["stt_failure_shown"], 3, result)
        self.assertEqual(result["stt_failure_said"], 1, result)
        self.assertEqual(result["stt_failure_turn_errors"], 0, result)


if __name__ == "__main__":
    unittest.main()
