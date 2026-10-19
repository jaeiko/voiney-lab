"""Lane ON, decision ① (2026-10-18): the records carry the account.

Human decision: experiments, records and review versions carry the account;
the reviewer's name is filled in from the account's name; within one lab the
protocol list is shared while experiment records are, by default, the
person's own (an administrator sees the lab's). The door itself is
``tests/test_lane_on_auth_api.py``; this file reuses its helpers.
"""

from __future__ import annotations

from pathlib import Path

from voiney_lab import server as server_module
from voiney_lab.workspace_store import WorkspaceSettings, initialize_workspace_store

from tests.test_lane_on_auth_api import Device, _configure, _invite, _store


def test_experiments_are_the_persons_own_and_the_administrators_are_the_labs(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    admin = Device()
    admin.sign_up(_invite(tmp_path, admin=True, organization_id="lab-a"), "관리자")
    member = Device()
    member.sign_up(_invite(tmp_path, organization_id="lab-a"), "최수진")
    other_lab = Device()
    other_lab.sign_up(_invite(tmp_path, admin=True, organization_id="lab-b"), "강이수")
    principals = {}
    for device in (admin, member, other_lab):
        body = device.get("/api/workspace/session").json()
        principals[body["display_name"]] = body
    store = initialize_workspace_store(WorkspaceSettings(enabled=True, data_dir=Path(tmp_path)))
    accounts = _store(tmp_path)
    try:
        for name, session_id in (("관리자", "exp-admin"), ("최수진", "exp-member"), ("강이수", "exp-other")):
            principal = accounts.account_by_name(name).principal()
            store.start_experiment(
                principal, protocol_id="protocol-1", protocol_revision_id="pdf-1",
                session_id=session_id, current_step_label="1",
            )
    finally:
        store.close()
        accounts.close()
    own = member.get("/api/workspace/experiments").json()["experiments"]
    assert [item["session_id"] for item in own] == ["exp-member"]
    everyone = admin.get("/api/workspace/experiments").json()["experiments"]
    assert sorted(item["session_id"] for item in everyone) == ["exp-admin", "exp-member"], "the lab's, not another lab's"
    # The administrator reads a member's experiment and its timeline; a member does not read another's.
    assert admin.get("/api/workspace/experiments/exp-member").status_code == 200
    assert admin.get("/api/workspace/experiments/exp-member/timeline").status_code == 200
    assert admin.get("/api/workspace/experiments/exp-other").status_code == 404
    assert member.get("/api/workspace/experiments/exp-admin").status_code == 404
    # Writing stays the owner's: the administrator may not move a member's experiment.
    moved = admin.post("/api/workspace/experiments/exp-member/transition",
                       json={"action": "stop", "expected_version": 1, "event_key": "stop-1"})
    assert moved.status_code == 404
    # The owner is the account.
    mine = member.get("/api/workspace/experiments/exp-member").json()
    assert mine["owner_principal_id"] == principals["최수진"]["principal_id"]


# --- Records carry the account -------------------------------------------------


class _FakeStore:
    def close(self):
        pass


class _FakeCatalog:
    def __init__(self):
        self.bodies = []

    def record_review_decision(self, protocol_id, body):
        self.bodies.append(("decision", protocol_id, dict(body)))
        return {"reviewer_name": body.get("reviewer_name")}

    def register_review(self, protocol_id, body):
        self.bodies.append(("version", protocol_id, dict(body)))
        return {"reviewer_name": body.get("reviewer_name"), "version": 1}


def test_the_reviewers_name_is_the_accounts_name(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    catalog = _FakeCatalog()
    monkeypatch.setattr(server_module, "_open_protocol_catalog", lambda: (catalog, _FakeStore()))
    monkeypatch.setattr(server_module, "_scope_catalog_resource", lambda *args, **kwargs: None)
    device = Device()
    device.sign_up(_invite(tmp_path), "김재준")
    decision = device.post("/api/protocols/p-1/review-decisions", json={"item_id": "step:s1", "action": "confirm"})
    assert decision.status_code == 200 and decision.json()["reviewer_name"] == "김재준"
    version = device.post("/api/protocols/p-1/review-versions", json={"reviewer_name": "다른 이름"})
    assert version.status_code == 201 and version.json()["reviewer_name"] == "김재준"
    assert all(body["reviewer_name"] == "김재준" for _, _, body in catalog.bodies)


def test_in_dev_mode_the_reviewers_name_is_still_the_persons_own_words(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path, mode=None)
    catalog = _FakeCatalog()
    monkeypatch.setattr(server_module, "_open_protocol_catalog", lambda: (catalog, _FakeStore()))
    monkeypatch.setattr(server_module, "_scope_catalog_resource", lambda *args, **kwargs: None)
    version = Device().post("/api/protocols/p-1/review-versions", json={"reviewer_name": "손으로 적은 이름"})
    assert version.status_code == 201 and version.json()["reviewer_name"] == "손으로 적은 이름"


