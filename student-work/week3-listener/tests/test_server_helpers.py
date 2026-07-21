import json
import unittest
from collections import deque
from unittest.mock import patch

from audio import FRAME_BYTES, samples_to_pcm16
from server import (
    ListenerSession,
    frame_complete_audio,
    run_turn,
    run_turn_safely,
    validate_tts_pcm,
)
from vad import EndpointDetector, TurnState


def frame(number=1):
    return bytes([number % 256]) * FRAME_BYTES


class Decisions:
    def __init__(self, values):
        self.values = deque(values)
        self.calls = 0

    def __call__(self, _frame):
        self.calls += 1
        if not self.values:
            raise AssertionError("unexpected classifier call")
        return self.values.popleft()


TURN = [False, True, True, True, True, False] + [True] * 8 + [False] * 40


class FakeResponse:
    def __init__(self, content=b"", status=200, content_type="audio/pcm", text=""):
        self.content = content
        self.status_code = status
        self.ok = 200 <= status < 300
        self.headers = {"content-type": content_type}
        self.text = text


class FakeWebSocket:
    def __init__(self):
        self.text_messages = []
        self.binary_messages = []

    async def send_text(self, message):
        self.text_messages.append(json.loads(message))

    async def send_bytes(self, message):
        self.binary_messages.append(message)


class ServerHelperTests(unittest.TestCase):
    def test_tts_accepts_successful_pcm_without_exact_content_type(self):
        response = FakeResponse(b"\x00\x00", content_type="application/octet-stream")
        self.assertEqual(validate_tts_pcm(response), b"\x00\x00")

    def test_tts_rejects_errors_json_empty_and_odd_pcm(self):
        cases = [
            FakeResponse(b"bad", status=400, text="bad request"),
            FakeResponse(b'{}', content_type="application/json", text="{}"),
            FakeResponse(),
            FakeResponse(b"x"),
        ]
        for response in cases:
            with self.subTest(response=response), self.assertRaises(RuntimeError):
                validate_tts_pcm(response)

    def test_outbound_frames_are_exact_and_final_frame_is_padded(self):
        frames = frame_complete_audio(b"x" * (FRAME_BYTES + 2))
        self.assertEqual([len(item) for item in frames], [FRAME_BYTES, FRAME_BYTES])
        self.assertEqual(frames[1][:2], b"xx")
        self.assertEqual(frames[1][2:], bytes(FRAME_BYTES - 2))


class ListenerSessionTests(unittest.TestCase):
    def make_session(self, decisions=TURN, clock=lambda: 0.0):
        classifier = Decisions(decisions)
        session = ListenerSession(EndpointDetector(classifier=classifier), clock=clock)
        session.start()
        return session, classifier

    def commit(self, session, start=0):
        events = session.accept_chunk(b"".join(frame(start + i) for i in range(len(TURN))))
        return next(item for item in events if item.kind == "speech.end")

    def test_frames_after_commit_and_during_processing_and_agent_are_ignored(self):
        session, classifier = self.make_session()
        committed = self.commit(session)
        calls = classifier.calls
        self.assertEqual(session.accept_chunk(frame(90) * 4), [])
        self.assertEqual(classifier.calls, calls)
        self.assertTrue(session.start_playback(committed.turn_id))
        self.assertEqual(session.accept_chunk(frame(91) * 4), [])
        self.assertEqual(classifier.calls, calls)

    def test_burst_below_minimum_is_rejected_before_cascade(self):
        burst = [False, True, True, True, True, False] + [True] * 7 + [False] * 40
        session, _ = self.make_session(burst)
        events = session.accept_chunk(b"".join(frame(i) for i in range(len(burst))))
        rejected = next(item for item in events if item.kind == "speech.rejected")
        self.assertFalse(any(item.kind == "speech.end" for item in events))
        self.assertEqual(rejected.result.voiced_frames, 11)
        self.assertEqual(rejected.result.rejection_reason, "minimum_voiced_frames")
        self.assertEqual(session.state, TurnState.IDLE)

    def test_mismatched_playback_is_ignored(self):
        session, _ = self.make_session()
        committed = self.commit(session)
        session.start_playback(committed.turn_id)
        self.assertFalse(session.playback_ended(committed.turn_id + 1))
        self.assertEqual(session.state, TurnState.AGENT_SPEAKING)

    def test_matching_playback_cooldown_then_second_turn_has_new_id(self):
        now = [10.0]
        session, _ = self.make_session(TURN + TURN, clock=lambda: now[0])
        first = self.commit(session)
        self.assertTrue(session.start_playback(first.turn_id))
        self.assertTrue(session.playback_ended(first.turn_id))
        self.assertEqual(session.state, TurnState.COOLDOWN)
        self.assertEqual(session.accept_chunk(frame(80)), [])
        now[0] += 0.299
        self.assertFalse(session.refresh_cooldown())
        now[0] += 0.002
        self.assertTrue(session.refresh_cooldown())
        second = self.commit(session, 100)
        self.assertGreater(second.turn_id, first.turn_id)

    def test_stop_disconnect_and_failure_clear_all_state(self):
        for action in ("stop", "disconnect", "failure"):
            with self.subTest(action=action):
                session, _ = self.make_session()
                committed = self.commit(session)
                if action == "failure":
                    session.cascade_failed(committed.turn_id)
                else:
                    session.stop()  # Disconnect uses the same cleanup path.
                self.assertEqual(session.state, TurnState.IDLE)
                self.assertIsNone(session.active_turn_id)
                self.assertEqual(session.detector.buffered_frames, 0)
                self.assertEqual(session.framer.partial_bytes, 0)


class CascadeTests(unittest.IsolatedAsyncioTestCase):
    def processing_session(self):
        session = ListenerSession()
        session.start()
        session.active_turn_id = 1
        session.detector.state = TurnState.PROCESSING
        return session

    async def test_valid_utterance_runs_each_external_stage_once(self):
        websocket = FakeWebSocket()
        session = self.processing_session()
        source_pcm = samples_to_pcm16([1000, -1000] * 160)
        with (
            patch("server.transcribe", return_value="hello") as mock_stt,
            patch("server.chat", return_value="one reply") as mock_chat,
            patch("server.synthesize", return_value=source_pcm) as mock_tts,
        ):
            await run_turn(websocket, session, source_pcm, 1, 1)
        mock_stt.assert_called_once_with(source_pcm)
        mock_chat.assert_called_once_with("hello")
        mock_tts.assert_called_once_with("one reply")
        self.assertEqual(session.state, TurnState.AGENT_SPEAKING)
        self.assertTrue(all(len(item) == FRAME_BYTES for item in websocket.binary_messages))
        for message in websocket.text_messages:
            if message["type"] in {"turn.processing", "transcript", "reply",
                                   "state.changed", "audio.start", "audio.end", "turn.done"}:
                self.assertEqual(message["turn_id"], 1)

    async def test_exception_clears_state_without_more_api_calls(self):
        websocket = FakeWebSocket()
        session = self.processing_session()
        with (
            patch("server.transcribe", side_effect=RuntimeError("offline failure")) as mock_stt,
            patch("server.chat") as mock_chat,
            patch("server.synthesize") as mock_tts,
        ):
            await run_turn_safely(websocket, session, frame(), 1, 1)
        mock_stt.assert_called_once()
        mock_chat.assert_not_called()
        mock_tts.assert_not_called()
        self.assertEqual(session.state, TurnState.IDLE)
        self.assertIsNone(session.active_turn_id)
        self.assertEqual([item["type"] for item in websocket.text_messages][-2:],
                         ["error", "state.changed"])

    async def test_whitespace_transcript_is_rejected_without_grok_or_tts(self):
        now = [10.0]
        websocket = FakeWebSocket()
        session = self.processing_session()
        session.clock = lambda: now[0]
        with (
            patch("server.transcribe", return_value="  \n ") as mock_stt,
            patch("server.chat") as mock_chat,
            patch("server.synthesize") as mock_tts,
        ):
            await run_turn_safely(websocket, session, frame(), 1, 14, 12)
        mock_stt.assert_called_once()
        mock_chat.assert_not_called()
        mock_tts.assert_not_called()
        self.assertEqual([item["type"] for item in websocket.text_messages],
                         ["turn.processing", "speech.rejected", "state.changed"])
        rejected = websocket.text_messages[1]
        self.assertEqual(rejected["reason"], "empty_transcript")
        self.assertEqual(rejected["voiced_frames"], 12)
        self.assertEqual(rejected["duration_ms"], 280)
        self.assertEqual(session.state, TurnState.COOLDOWN)
        self.assertIsNone(session.active_turn_id)
        now[0] += 0.301
        self.assertTrue(session.refresh_cooldown())
        self.assertEqual(session.state, TurnState.IDLE)

    async def test_valid_turn_after_empty_transcript_gets_new_turn_id(self):
        now = [10.0]
        session = ListenerSession(EndpointDetector(classifier=Decisions(TURN + TURN)),
                                  clock=lambda: now[0])
        session.start()
        first = next(item for item in session.accept_chunk(
            b"".join(frame(i) for i in range(len(TURN)))) if item.kind == "speech.end")
        websocket = FakeWebSocket()
        with patch("server.transcribe", return_value=""):
            await run_turn(websocket, session, first.result.utterance,
                           first.turn_id, first.result.total_frames)
        now[0] += 0.301
        session.refresh_cooldown()
        second = next(item for item in session.accept_chunk(
            b"".join(frame(100 + i) for i in range(len(TURN)))) if item.kind == "speech.end")
        self.assertGreater(second.turn_id, first.turn_id)

    async def test_one_commit_runs_one_cascade_despite_extra_frames(self):
        websocket = FakeWebSocket()
        session = ListenerSession(EndpointDetector(classifier=Decisions(TURN)))
        session.start()
        events = session.accept_chunk(b"".join(frame(i) for i in range(len(TURN))))
        commit = next(item for item in events if item.kind == "speech.end")
        self.assertEqual(session.accept_chunk(frame(99) * 3), [])
        output = samples_to_pcm16([0] * 320)
        with (
            patch("server.transcribe", return_value="once") as mock_stt,
            patch("server.chat", return_value="once") as mock_chat,
            patch("server.synthesize", return_value=output) as mock_tts,
        ):
            await run_turn(websocket, session, commit.result.utterance,
                           commit.turn_id, commit.result.total_frames)
        self.assertEqual((mock_stt.call_count, mock_chat.call_count, mock_tts.call_count),
                         (1, 1, 1))


if __name__ == "__main__":
    unittest.main()
