"""Cookie sessions reject foreign origins; API docs share the account door."""
import pytest

from tests.test_lane_on_auth_api import Device, _configure, _invite
from tests.test_lane_app1_app_origins import _open


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"])
def test_docs_require_a_signed_in_account(monkeypatch, tmp_path, path):
    _configure(monkeypatch, tmp_path)
    device = Device()
    assert device.get(path).status_code == 401
    device.sign_up(_invite(tmp_path), "researcher")
    assert device.get(path).status_code == 200


@pytest.mark.parametrize("origin", ["https://other.test", "http://test.evil.test", "null"])
def test_foreign_origin_cannot_log_out_a_cookie_session(monkeypatch, tmp_path, origin):
    _configure(monkeypatch, tmp_path)
    device = Device()
    device.sign_up(_invite(tmp_path), "researcher")
    assert device.post("/api/auth/logout", headers={"origin": origin}).status_code == 403
    assert device.get("/api/auth/session").json()["signed_in"]
    assert device.post("/api/auth/logout", headers={"origin": "http://test"}).status_code == 200


def test_ws_checks_origin_even_without_an_app_allowlist(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path, mode="dev")
    monkeypatch.delenv("VOINEY_LAB_APP_ORIGINS", raising=False)
    refused = _open({"origin": "https://other.test", "host": "test"})
    assert refused.closed == (1008, "origin not allowed")
    assert refused.sent == [{"type": "error", "message": "origin_not_allowed"}]
    assert _open({"origin": "https://test", "host": "test"}).sent[0]["type"] == "ready"
