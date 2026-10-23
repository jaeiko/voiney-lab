"""Startup has no request identity; the configured demo is tenant scoped."""
from types import SimpleNamespace
from unittest.mock import patch

from tests.lane_n_support import notes_fixture
from tests.test_lane_on_auth_api import Device, _configure, _invite
from voiney_lab import server as srv


def test_startup_catalog_does_not_require_a_signed_in_request(monkeypatch, tmp_path, caplog):
    _configure(monkeypatch, tmp_path)
    monkeypatch.setenv("VOINEY_LAB_USAGE_SCOPE", "source_protocol_only")
    monkeypatch.setenv("VOINEY_LAB_PROTOCOL_ENABLED", "true")
    monkeypatch.setenv("VOINEY_LAB_PROTOCOL_DATA_DIR", str(tmp_path / "catalog"))
    with patch.object(srv, "_configured_candidate_fixture", return_value=None):
        srv.log_protocol_catalog_runtime_configuration()
    assert "protocol.catalog.configuration unavailable" not in caplog.text


def test_unbound_development_fixture_is_not_in_an_accounts_catalog(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    device = Device()
    device.sign_up(_invite(tmp_path), "researcher")
    with patch.object(srv, "server_config", return_value=SimpleNamespace()), \
         patch.object(srv, "_configured_candidate_fixture", return_value=notes_fixture()), \
         patch.object(srv, "_candidate_catalog_dict", return_value={"protocol_id": "lane-n-wash"}):
        response = device.get("/api/protocols")
    assert response.status_code == 200
    assert response.json()["protocols"] == []
