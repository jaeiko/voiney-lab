"""A manual capture must not make the current voice connection stale."""
from __future__ import annotations

import unittest

import httpx

from tests.lane_n_support import VoiceNotesHarness
from voiney_lab import server as srv


class CaptureThenControlTests(VoiceNotesHarness, unittest.TestCase):
    def capture_then_control(self, kind):
        async def scenario(socket, listener, say):
            await say(1, "프로토콜 시작해줘")
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url="http://test") as client:
                path = f"/api/workspace/experiments/{listener.session_id}"
                if kind == "note":
                    response = await client.post(path + "/observations", json={
                        "idempotency_key": "manual-note", "content": "실험자가 쓴 메모", "category": "note"})
                else:
                    response = await client.post(path + "/evidence?filename=bench.png&idempotency_key=photo",
                        content=b"\x89PNG\r\n\x1a\nfictional", headers={"content-type": "image/png"})
                self.assertEqual(response.status_code, 201, response.text)
            for command, status in (("workflow.pause", "paused"), ("workflow.resume", "in_progress"), ("session.stop", "stopped")):
                offset = len(socket.sent)
                socket.control({"type": command})
                await socket.wait_for(lambda e: e["type"] in {"experiment.session.state", "error"}
                                      and e in socket.sent[offset:])
                fresh = socket.sent[offset:]
                self.assertFalse([e for e in fresh if e["type"] == "error"], fresh)
                state = next(e["state"] for e in fresh if e["type"] == "experiment.session.state")
                self.assertEqual(state["status"], status)
                self.assertEqual(state["version"], listener.experiment_state_version)
        self.voice(scenario)

    def test_note_then_pause_resume_stop(self):
        self.capture_then_control("note")

    def test_photo_then_pause_resume_stop(self):
        self.capture_then_control("photo")

    def test_old_voice_connection_cannot_pause_after_takeover(self):
        from voiney_lab.workspace_store import WorkspaceSettings, initialize_workspace_store

        async def scenario(socket, listener, say):
            await say(1, "프로토콜 시작해줘")
            store = initialize_workspace_store(WorkspaceSettings(True, self.workspace_dir))
            try:
                state = store.get_experiment(self.principal, listener.session_id)
                store.resume_experiment(self.principal, listener.session_id, expected_version=state["version"],
                    protocol_id=state["protocol_id"], protocol_revision_id=state["protocol_revision_id"],
                    voice_connection_id="replacement-tab", reattach=True)
            finally:
                store.close()
            offset = len(socket.sent)
            socket.control({"type": "workflow.pause"})
            await socket.wait_for(lambda e: e["type"] in {"experiment.session.state", "error"}
                                  and e in socket.sent[offset:])
            self.assertEqual(socket.sent[offset:][0]["type"], "error")
            self.assertEqual(self.experiment(listener.session_id)["status"], "in_progress")
        self.voice(scenario)
