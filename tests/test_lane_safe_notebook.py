"""An unanswered notebook question survives a pause."""
from __future__ import annotations
import unittest
from tests.lane_n_support import VoiceNotesHarness, shown

class NotebookTests(VoiceNotesHarness, unittest.TestCase):
    def test_notebook_question_survives_pause_and_resume(self):
        socket = self.turns("프로토콜 시작해줘", "잠깐", "재개", "넘어가")
        assert "오늘 실험 목적이나 시료" in shown(socket)[3]
        assert self.listener.curated_protocol_session.current_index == 0
        assert not self.experiment()["completed_steps"]
        assert not [e for e in self.report()["events"] if e["event_type"] == "step_completed"]
