"""HTTP cannot diverge the workflow, and recovery cannot release a pause."""
import asyncio
import unittest

import httpx

from tests.lane_n_support import VoiceNotesHarness
from voiney_lab import server as srv


class WorkflowAuthorityTests(VoiceNotesHarness, unittest.TestCase):
    def test_http_transitions_cannot_change_the_live_workflow(self):
        async def scenario(socket, listener, say):
            await say(1, "프로토콜 시작해줘")
            before = self.experiment(listener.session_id)
            checkpoint = listener.curated_protocol_session._checkpoint()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url="http://test") as client:
                for action in ("pause", "resume", "stop", "block"):
                    response = await client.post(f"/api/workspace/experiments/{listener.session_id}/transition", json={
                        "action": action, "expected_version": before["version"], "event_key": "direct-" + action})
                    self.assertEqual(response.status_code, 409, response.text)
                    self.assertEqual(response.json()["detail"], "voice_control_required")
            self.assertEqual(self.experiment(listener.session_id), before)
            self.assertEqual(listener.curated_protocol_session._checkpoint(), checkpoint)
        self.voice(scenario)

    def test_recovery_without_reconnect_does_not_release_a_pause(self):
        self.turns("프로토콜 시작해줘", "넘어가", "잠깐 멈춰")
        previous = self.experiment()
        self.assertEqual(previous["status"], "paused")

        async def scenario(socket, listener, say):
            self.assertEqual(self.experiment(listener.session_id)["status"], "paused")
            self.assertEqual(listener.curated_protocol_session.workflow_status, "paused")
            socket.control({"type": "workflow.resume", "configuration_id": 1, "generation": listener.generation})
            await socket.wait_for(lambda e: e["type"] == "protocol.fixture.state" and e.get("action") == "resume")
            self.assertEqual(self.experiment(listener.session_id)["status"], "in_progress")
        self.voice(scenario, start={"experiment_session_id": previous["session_id"],
                                   "experiment_session_version": previous["version"]})
