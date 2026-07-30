import unittest
from collections import deque

from voice_workflow_agent.audio import FRAME_BYTES
from voice_workflow_agent.vad import EndpointDetector, TurnState, VadConfig, WebRtcVadClassifier


def frame(number):
    return bytes([number % 256]) * FRAME_BYTES


class Decisions:
    def __init__(self, values):
        self.values = deque(values)
        self.calls = 0

    def __call__(self, _frame):
        self.calls += 1
        if not self.values:
            raise AssertionError("classifier received an unexpected frame")
        return self.values.popleft()


def feed(detector, frames):
    return [detector.process(item) for item in frames]


class EndpointDetectorTests(unittest.TestCase):
    def test_silence_only_remains_idle(self):
        detector = EndpointDetector(classifier=Decisions([False] * 100))
        results = feed(detector, [frame(i) for i in range(100)])
        self.assertEqual(detector.state, TurnState.IDLE)
        self.assertFalse(any(result.speech_started or result.utterance for result in results))
        self.assertEqual(detector.buffered_frames, 0)

    def test_four_of_six_causes_exactly_one_start(self):
        detector = EndpointDetector(classifier=Decisions(
            [False, True, True, True, True, False] + [True] * 5))
        results = feed(detector, [frame(i) for i in range(11)])
        self.assertEqual(sum(result.speech_started for result in results), 1)
        self.assertEqual(detector.state, TurnState.USER_SPEAKING)

    def test_three_of_six_does_not_start_speech(self):
        decisions = [False, True, True, True, False, False]
        detector = EndpointDetector(classifier=Decisions(decisions))
        results = feed(detector, [frame(i) for i in range(len(decisions))])
        self.assertFalse(any(result.speech_started for result in results))
        self.assertEqual(detector.state, TurnState.IDLE)

    def test_prefix_order_and_onset_frames_are_not_duplicated(self):
        decisions = [False] * 9 + [False, True, True, True, True, False] + [True] * 8 + [False] * 50
        detector = EndpointDetector(classifier=Decisions(decisions))
        results = feed(detector, [frame(i) for i in range(len(decisions))])
        utterance = next(result.utterance for result in results if result.utterance is not None)
        chunks = [utterance[i:i + FRAME_BYTES] for i in range(0, len(utterance), FRAME_BYTES)]
        self.assertEqual(chunks, [frame(i) for i in range(23)])
        self.assertEqual(len(chunks), len(set(chunks)))

    def test_grace_period_resume_is_preserved_and_does_not_end_turn(self):
        before = [False, True, True, True, True, False] + [True] * 8 + [False] * 49
        detector = EndpointDetector(classifier=Decisions(before + [True] + [False] * 50))
        early = feed(detector, [frame(i) for i in range(len(before))])
        self.assertFalse(any(result.utterance for result in early))
        self.assertEqual(detector.state, TurnState.USER_SPEAKING)
        resumed = frame(200)
        later = feed(detector, [resumed] + [frame(201 + i) for i in range(50)])
        utterance = next(result.utterance for result in later if result.utterance is not None)
        self.assertIn(b"".join(frame(14 + i) for i in range(49)), utterance)
        self.assertTrue(utterance.endswith(resumed))

    def test_fifty_silent_frames_end_once_and_are_trimmed(self):
        decisions = [False, True, True, True, True, False] + [True] * 8 + [False] * 51
        detector = EndpointDetector(classifier=Decisions(decisions))
        results = feed(detector, [frame(i) for i in range(len(decisions))])
        commits = [result for result in results if result.utterance is not None]
        self.assertEqual(len(commits), 1)
        self.assertEqual(commits[0].total_frames, 14)
        self.assertEqual(commits[0].voiced_frames, 12)
        self.assertTrue(commits[0].utterance.endswith(frame(13)))
        self.assertEqual(detector.state, TurnState.PROCESSING)

    def test_too_short_burst_is_rejected(self):
        decisions = [False, True, True, True, True, False] + [True] * 7 + [False] * 50
        detector = EndpointDetector(classifier=Decisions(decisions))
        results = feed(detector, [frame(i) for i in range(len(decisions))])
        self.assertEqual(sum(result.rejected for result in results), 1)
        self.assertFalse(any(result.utterance for result in results))
        self.assertEqual(detector.state, TurnState.IDLE)
        self.assertEqual(detector.buffered_frames, 0)
        rejection = next(result for result in results if result.rejected)
        self.assertEqual(rejection.voiced_frames, 11)
        self.assertEqual(rejection.rejection_reason, "minimum_voiced_frames")

    def test_maximum_utterance_forces_one_endpoint(self):
        config = VadConfig(prefix_frames=5, minimum_voiced_frames=3, maximum_utterance_frames=8)
        classifier = Decisions([False, True, True, True, True, False] + [True] * 3)
        detector = EndpointDetector(config, classifier)
        results = feed(detector, [frame(i) for i in range(9)])
        commits = [result for result in results if result.utterance is not None]
        self.assertEqual(len(commits), 1)
        self.assertTrue(commits[0].forced)
        calls = classifier.calls
        self.assertIsNone(detector.process(frame(99)).utterance)
        self.assertEqual(classifier.calls, calls)

    def test_real_adapter_enforces_exact_frame_size(self):
        classifier = WebRtcVadClassifier(3)
        self.assertIsInstance(classifier(bytes(FRAME_BYTES)), bool)
        with self.assertRaises(ValueError):
            classifier(bytes(FRAME_BYTES - 2))


if __name__ == "__main__":
    unittest.main()
