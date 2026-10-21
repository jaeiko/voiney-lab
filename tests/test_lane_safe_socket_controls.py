"""Bench resume and repeated starts stay within the accepted server session."""
from __future__ import annotations

import unittest
from tests.lane_n_support import VoiceNotesHarness, PROTOCOL_ID
from tests.test_lane_safe_questions import workflow_checkpoint


class ResumeTests(VoiceNotesHarness, unittest.TestCase):
    def test_resume_cannot_start_an_unstarted_experiment(self):
        async def scenario(socket, listener, say):
            before = workflow_checkpoint(listener.curated_protocol_session)
            socket.control({"type": "workflow.resume", "configuration_id": 1,
                            "generation": listener.generation})
            refused = await socket.wait_for(lambda e: e["type"] == "workflow.control.refused")
            assert refused["reason"] == "experiment_not_started"
            assert refused["message_ko"]
            assert workflow_checkpoint(listener.curated_protocol_session) == before
            assert self.experiment(listener.session_id)["status"] == "ready"
        self.voice(scenario)

    def test_resume_requires_the_current_configuration_and_generation(self):
        async def scenario(socket, listener, say):
            await say(1, "프로토콜 시작해줘")
            await say(2, "넘어가")
            await say(3, "잠깐")
            for config, generation in ((None, None), (2, listener.generation),
                                       (1, listener.generation + 1), (True, listener.generation)):
                before = workflow_checkpoint(listener.curated_protocol_session)
                sent = len(socket.sent)
                socket.control({"type": "workflow.resume", "configuration_id": config,
                                "generation": generation})
                await socket.wait_for(lambda e: e in socket.sent[sent:] and e["type"] == "workflow.control.refused")
                assert workflow_checkpoint(listener.curated_protocol_session) == before
                assert self.experiment(listener.session_id)["status"] == "paused"
            socket.control({"type": "workflow.resume", "configuration_id": 1,
                            "generation": listener.generation})
            await socket.wait_for(lambda e: e["type"] == "protocol.fixture.state" and e.get("action") == "resume")
            assert self.experiment(listener.session_id)["status"] == "in_progress"
        self.voice(scenario)


class RepeatedStartTests(VoiceNotesHarness, unittest.TestCase):
    def test_same_start_reports_current_session_without_restarting(self):
        async def scenario(socket, listener, say):
            await say(1, "프로토콜 시작해줘")
            before = workflow_checkpoint(listener.curated_protocol_session)
            state = self.experiment(listener.session_id)
            sent = len(socket.sent)
            socket.control({"type": "session.start", "configuration_id": 1, "mode": "cascade",
                            "language": "ko", "protocol_id": PROTOCOL_ID})
            await socket.wait_for(lambda e: e in socket.sent[sent:] and e["type"] == "session.ready")
            assert workflow_checkpoint(listener.curated_protocol_session) == before
            assert self.experiment(listener.session_id) == state
        self.voice(scenario)

    def test_different_start_errors_without_closing_or_replacing_session(self):
        async def scenario(socket, listener, say):
            await say(1, "프로토콜 시작해줘")
            before = workflow_checkpoint(listener.curated_protocol_session)
            socket.control({"type": "session.start", "configuration_id": 2, "mode": "cascade",
                            "language": "ko", "protocol_id": PROTOCOL_ID})
            await socket.wait_for(lambda e: e["type"] == "error")
            assert workflow_checkpoint(listener.curated_protocol_session) == before
            assert listener.active
            await say(2, "넘어가")
            assert listener.curated_protocol_session.current_index == 0
        self.voice(scenario)
