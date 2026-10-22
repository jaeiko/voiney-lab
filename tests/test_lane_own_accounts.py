"""Account QA regressions, all with local stores and the real HTTP app."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

from tests.test_lane_on_auth_api import Device, PASSWORD, _configure, _invite

ROOT = Path(__file__).resolve().parents[1]


def test_wifi_neighbours_wrong_password_does_not_lock_my_account(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    owner, peer = Device(), Device()
    owner.sign_up(_invite(tmp_path), "owner")
    peer.sign_up(_invite(tmp_path), "peer")
    attacker = Device(headers={"X-Forwarded-For": "203.0.113.1"})
    for _ in range(5):
        assert attacker.post("/api/auth/login", json={"name": "owner", "password": "incorrect"}).status_code == 401
    assert attacker.post("/api/auth/login", json={"name": "peer", "password": PASSWORD}).status_code == 200
    assert Device(headers={"X-Forwarded-For": "203.0.113.2"}).post(
        "/api/auth/login", json={"name": "owner", "password": PASSWORD}).status_code == 429


def test_current_password_guesses_are_rate_limited(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    device = Device()
    device.sign_up(_invite(tmp_path), "owner")
    for _ in range(5):
        assert device.post("/api/auth/password", json={"current": "incorrect", "new": "different password"}).status_code == 401
    blocked = device.post("/api/auth/password", json={"current": PASSWORD, "new": "different password"})
    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) > 0


def test_invite_default_follows_pilot_folder_not_workspace_shell(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("own_invite_tool", ROOT / "scripts/pilot_invite.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    monkeypatch.setenv("VOINEY_LAB_WORKSPACE_DATA_DIR", str(tmp_path / "misleading-shell-folder"))
    assert tool.data_dir_from(None) == tool.DEFAULT_DATA_DIR
    assert tool.data_dir_from(str(tmp_path / "explicit")) == (tmp_path / "explicit").resolve()


def test_pilot_guide_phone_setup_names_accounts_as_default():
    guide = (ROOT / "docs/PILOT_GUIDE.md").read_text()
    phone = guide.split("4. **휴대폰**", 1)[1].split("5. **멈춤**", 1)[0]
    assert "기본값 `dev`" not in phone
    assert "계정이 기본" in phone


from tests.lane_n_support import VoiceNotesHarness


class EvidenceReadTests(VoiceNotesHarness, unittest.TestCase):
    def test_admin_reads_evidence_but_peer_and_other_lab_cannot(self):
        import dataclasses
        from types import SimpleNamespace
        from unittest.mock import patch
        import httpx
        from voiney_lab import server as srv
        from voiney_lab.workspace_store import WorkspaceSettings, initialize_workspace_store

        async def scenario(socket, listener, say):
            await say(1, "프로토콜 시작해줘")
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url="http://test") as client:
                path = f"/api/workspace/experiments/{listener.session_id}/evidence"
                content = b"\x89PNG\r\n\x1a\nfictional evidence"
                response = await client.post(path + "?filename=bench.png&idempotency_key=photo",
                    content=content, headers={"content-type": "image/png"})
                self.assertEqual(response.status_code, 201, response.text)
                evidence = response.json()
                for admin, lab, status in ((False, self.principal.organization_id, 404),
                                           (True, self.principal.organization_id, 200),
                                           (True, "another-lab", 404)):
                    principal = dataclasses.replace(self.principal, principal_id=f"peer-{lab}",
                        subject=f"peer:{lab}", organization_id=lab)
                    store = initialize_workspace_store(WorkspaceSettings(True, self.workspace_dir))
                    store.bootstrap_principal(principal)
                    store.close()
                    with patch.object(srv, "_identity_resolver", return_value=SimpleNamespace(resolve=lambda _: principal)), \
                         patch.object(srv, "_account_is_admin", return_value=admin):
                        downloaded = await client.get(path + "/" + evidence["evidence_id"])
                    self.assertEqual(downloaded.status_code, status, downloaded.text)
                    if status == 200:
                        self.assertEqual(downloaded.content, content)
        self.voice(scenario)
