"""Readiness checks local safety configuration, without provider calls."""
import sqlite3

import pytest

from tests.test_lane_on_auth_api import Device
from tests.test_lane_app1_app_origins import _open
from voiney_lab import server as srv
from voiney_lab.document_store import CATALOG_SCHEMA_VERSION
from voiney_lab.start_check import start_check_enabled
from voiney_lab.workspace_store import WorkspaceSettings


@pytest.fixture
def safety(monkeypatch, tmp_path):
    for name in ("VOINEY_LAB_WORKSPACE_ENABLED", "VOINEY_LAB_PROTOCOL_ENABLED", "VOINEY_LAB_EXPERIMENT_REPORTS_ENABLED"):
        monkeypatch.setenv(name, "false")
    monkeypatch.setenv("VOINEY_LAB_AUTH_MODE", "dev")
    monkeypatch.setenv("VOINEY_LAB_USAGE_SCOPE", "demo")
    path = tmp_path / "safety.sqlite"
    monkeypatch.setenv("VOINEY_LAB_SAFETY_CATALOG", str(path))
    return path


@pytest.mark.parametrize("kind", ["missing", "corrupt", "wrong_scope", "wrong_schema"])
def test_safety_catalog_failures_are_not_ready(safety, kind):
    if kind == "corrupt":
        safety.write_bytes(b"not sqlite")
    elif kind != "missing":
        with sqlite3.connect(safety) as connection:
            connection.execute("CREATE TABLE catalog_metadata(schema_version INTEGER)")
            connection.execute("INSERT INTO catalog_metadata VALUES(?)", (CATALOG_SCHEMA_VERSION if kind == "wrong_scope" else -1,))
            connection.execute("CREATE TABLE documents(usage_scope TEXT,approval_status TEXT,active INTEGER)")
            connection.execute("INSERT INTO documents VALUES('reference_only','approved',1)")
    response = Device().get("/readyz")
    assert response.status_code == 503
    assert response.json()["reason"] == "safety_configuration_invalid"
    assert "VOINEY_LAB_SAFETY_CATALOG" in response.json()["fields"]
    assert str(safety) not in response.text


def test_source_only_needs_no_catalog_and_empty_scope_is_refused(safety, monkeypatch):
    monkeypatch.setenv("VOINEY_LAB_USAGE_SCOPE", "source_protocol_only")
    assert Device().get("/readyz").status_code == 200
    monkeypatch.setenv("VOINEY_LAB_USAGE_SCOPE", "   ")
    with pytest.raises(srv.ServerConfigurationError) as failure:
        srv._runtime_usage_scope()
    assert failure.value.field_names == ("VOINEY_LAB_USAGE_SCOPE",)
    response = Device().get("/readyz")
    assert response.status_code == 503
    assert response.json()["fields"] == ["VOINEY_LAB_USAGE_SCOPE"]


def test_an_approved_catalog_is_ready(safety):
    with sqlite3.connect(safety) as connection:
        connection.execute("CREATE TABLE catalog_metadata(schema_version INTEGER)")
        connection.execute("INSERT INTO catalog_metadata VALUES(?)", (CATALOG_SCHEMA_VERSION,))
        connection.execute("CREATE TABLE documents(usage_scope TEXT,approval_status TEXT,active INTEGER)")
        connection.execute("INSERT INTO documents VALUES('demo','approved',1)")
    assert Device().get("/readyz").status_code == 200


def test_an_unset_scope_is_refused_by_name(safety, monkeypatch):
    monkeypatch.delenv("VOINEY_LAB_USAGE_SCOPE", raising=False)
    with pytest.raises(srv.ServerConfigurationError) as failure:
        srv._runtime_usage_scope()
    assert failure.value.field_names == ("VOINEY_LAB_USAGE_SCOPE",)
    response = Device().get("/readyz")
    assert response.status_code == 503
    assert response.json()["fields"] == ["VOINEY_LAB_USAGE_SCOPE"]


@pytest.mark.parametrize("scope", [None, " "])
def test_missing_scope_names_the_setting_at_http_and_socket_boundaries(safety, monkeypatch, scope):
    if scope is None:
        monkeypatch.delenv("VOINEY_LAB_USAGE_SCOPE", raising=False)
    else:
        monkeypatch.setenv("VOINEY_LAB_USAGE_SCOPE", scope)
    response = Device().get("/api/auth/session")
    assert response.status_code == 503
    assert response.json() == {"detail": "configuration_invalid", "fields": ["VOINEY_LAB_USAGE_SCOPE"]}
    socket = _open({})
    assert socket.sent == [{"type": "error", "message": "configuration_invalid", "fields": ["VOINEY_LAB_USAGE_SCOPE"]}]
    assert socket.closed == (1008, "invalid configuration")


@pytest.mark.parametrize("read,name", [(start_check_enabled, "VOINEY_LAB_START_CHECK_ENABLED"),
                                       (WorkspaceSettings.from_environment, "VOINEY_LAB_WORKSPACE_ENABLED")])
def test_boolean_typos_are_refused_by_setting_name(read, name):
    with pytest.raises((ValueError, RuntimeError), match=name):
        read({name: "ture"})
