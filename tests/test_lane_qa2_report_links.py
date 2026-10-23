"""Session-to-report discovery uses the OWN HTTP read boundary."""
import pytest

from tests.test_lane_own_reports import records


@pytest.mark.parametrize("who,status", [("owner", 200), ("admin", 200), ("peer", 404), ("outside", 404)])
def test_session_report_links(records, who, status):
    devices, store, rid = records
    before = store.get_report(rid)["events"]
    response = devices[who].get("/api/workspace/experiments/session-owned/reports")
    assert response.status_code == status, response.text
    if status == 200:
        payload = response.json()
        assert payload["session_id"] == "session-owned"
        assert [row["report_id"] for row in payload["reports"]] == [rid]
        assert payload["reports"][0]["status"] == "completed"
    assert store.get_report(rid)["events"] == before


def test_no_report_and_missing_session(records, monkeypatch):
    devices, _, _ = records
    monkeypatch.setenv("VOINEY_LAB_EXPERIMENT_REPORTS_ENABLED", "false")
    response = devices["owner"].get("/api/workspace/experiments/session-owned/reports")
    assert response.status_code == 200
    assert response.json() == {"session_id": "session-owned", "reports": []}
    assert devices["owner"].get("/api/workspace/experiments/missing/reports").status_code == 404
