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

    def test_listening_profile_requires_eight_of_twelve(self):
        old_threshold_pattern = [True] * 4 + [False] * 8
        old_threshold = EndpointDetector(
            classifier=Decisions(old_threshold_pattern),
            listening_onset=True,
        )
        old_results = feed(
            old_threshold,
            [frame(i) for i in range(len(old_threshold_pattern))],
        )
        self.assertFalse(any(result.speech_started for result in old_results))
        self.assertEqual(old_threshold.state, TurnState.IDLE)

        seven_pattern = [True] * 7 + [False] * 5
        seven = EndpointDetector(
            classifier=Decisions(seven_pattern),
            listening_onset=True,
        )
        seven_results = feed(
            seven,[frame(20 + i) for i in range(len(seven_pattern))])
        self.assertFalse(any(result.speech_started for result in seven_results))

        eight_pattern = [True] * 8 + [False] * 4
        eight = EndpointDetector(
            classifier=Decisions(eight_pattern),
            listening_onset=True,
        )
        eight_results = feed(
            eight,[frame(40 + i) for i in range(len(eight_pattern))])
        starts = [result for result in eight_results if result.speech_started]
        self.assertEqual(len(starts),1)
        self.assertEqual(starts[0].voiced_frames,8)
        self.assertEqual(starts[0].total_frames,12)
        self.assertEqual(eight.state,TurnState.USER_SPEAKING)

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

    def test_grace_period_resume_is_qualified_and_preserves_audio_once(self):
        config = VadConfig(
            onset_voiced_frames=2,onset_window_frames=3,prefix_frames=3,
            endpoint_silence_frames=12,minimum_voiced_frames=2,
            maximum_utterance_frames=50,cooldown_ms=0,
            listening_resume_voiced_frames=6,
            listening_resume_window_frames=10,
        )
        decisions = [True] * 3 + [False] * 4 + [True] * 6 + [True] * 2
        detector = EndpointDetector(config,classifier=Decisions(
            decisions + [False] * 12))
        with self.assertLogs(
            "voice_workflow_agent.vad",level="INFO",
        ) as captured:
            results = feed(
                detector,[frame(i) for i in range(len(decisions) + 12)])
        commit = next(result for result in results if result.utterance is not None)
        chunks = [
            commit.utterance[index:index + FRAME_BYTES]
            for index in range(0,len(commit.utterance),FRAME_BYTES)
        ]
        self.assertEqual(chunks,[frame(i) for i in range(len(decisions))])
        self.assertEqual(len(chunks),len(set(chunks)))
        self.assertEqual(
            sum("speech.resumed" in message for message in captured.output),1)
        self.assertEqual(
            sum("silence.started" in message for message in captured.output),2)

    def test_unconfirmed_resume_does_not_restart_endpoint_grace(self):
        config = VadConfig(
            onset_voiced_frames=2,onset_window_frames=3,prefix_frames=3,
            endpoint_silence_frames=12,minimum_voiced_frames=2,
            maximum_utterance_frames=50,cooldown_ms=0,
            listening_resume_voiced_frames=6,
            listening_resume_window_frames=10,
        )
        grace = [False,True,False,True,False,True,False,True,False,True]
        detector = EndpointDetector(
            config,classifier=Decisions([True] * 3 + grace + [False] * 2))
        with self.assertLogs(
            "voice_workflow_agent.vad",level="INFO",
        ) as captured:
            before_endpoint = feed(
                detector,[frame(i) for i in range(3 + len(grace))])
            self.assertFalse(any(result.utterance for result in before_endpoint))
            self.assertEqual(detector.consecutive_silence_frames,10)
            final = feed(detector,[frame(20),frame(21)])
        commits = [result for result in final if result.utterance is not None]
        self.assertEqual(len(commits),1)
        self.assertEqual(commits[0].total_frames,3)
        self.assertEqual(commits[0].voiced_frames,3)
        self.assertEqual(
            sum("silence.started" in message for message in captured.output),1)
        self.assertFalse(any(
            "speech.resumed" in message for message in captured.output))

    def test_one_voiced_resume_candidate_does_not_reset_grace(self):
        config = VadConfig(
            onset_voiced_frames=2,onset_window_frames=3,prefix_frames=3,
            endpoint_silence_frames=12,minimum_voiced_frames=2,
            maximum_utterance_frames=50,cooldown_ms=0,
        )
        detector = EndpointDetector(
            config,classifier=Decisions([True] * 3 + [False] * 3 + [True]))
        feed(detector,[frame(i) for i in range(6)])
        self.assertEqual(detector.consecutive_silence_frames,3)
        detector.process(frame(6))
        self.assertEqual(detector.consecutive_silence_frames,4)

    def test_rejected_resume_candidate_does_not_contaminate_next_utterance(self):
        config = VadConfig(
            onset_voiced_frames=2,onset_window_frames=3,prefix_frames=3,
            endpoint_silence_frames=12,minimum_voiced_frames=6,
            maximum_utterance_frames=60,cooldown_ms=0,
            listening_resume_voiced_frames=6,
            listening_resume_window_frames=10,
        )
        unconfirmed = [False,True,False,True,False,True,False,True,False,True]
        first = [True] * 3 + unconfirmed + [False] * 2
        second = [True] * 6 + [False] * 12
        detector = EndpointDetector(
            config,classifier=Decisions(first + second))
        results = feed(
            detector,[frame(i) for i in range(len(first + second))])
        self.assertEqual(sum(result.rejected for result in results),1)
        commits=[result for result in results if result.utterance is not None]
        self.assertEqual(len(commits),1)
        chunks=[
            commits[0].utterance[index:index + FRAME_BYTES]
            for index in range(0,len(commits[0].utterance),FRAME_BYTES)
        ]
        self.assertEqual(chunks,[
            frame(len(first) + index) for index in range(6)
        ])

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

    def test_default_cascade_keeps_listening_beyond_ten_seconds(self):
        decisions=[False,True,True,True,True,False]+[True]*520
        detector=EndpointDetector(classifier=Decisions(decisions))
        results=feed(detector,[frame(i) for i in range(len(decisions))])
        self.assertFalse(any(result.utterance is not None for result in results))
        self.assertEqual(detector.state,TurnState.USER_SPEAKING)
        self.assertGreater(detector.buffered_frames,500)

    def test_real_adapter_enforces_exact_frame_size(self):
        classifier = WebRtcVadClassifier(3)
        self.assertIsInstance(classifier(bytes(FRAME_BYTES)), bool)
        with self.assertRaises(ValueError):
            classifier(bytes(FRAME_BYTES - 2))


if __name__ == "__main__":
    unittest.main()
