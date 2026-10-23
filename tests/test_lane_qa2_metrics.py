"""A lab administrator's aggregate includes only accounts in that lab."""
import pytest

from tests.test_lane_own_reports import records
from voiney_lab import server as srv


@pytest.mark.parametrize("query", ["", "?account=unrelated-account"])
def test_another_lab_admin_sees_no_experiments(records, query):
    devices, _, _ = records
    response = devices["outside"].get("/api/admin/pilot-metrics" + query)
    assert response.status_code == 200
    assert response.json()["days"] == []
    assert devices["admin"].get("/api/admin/pilot-metrics").json()["totals"]["experiments"] == 1


def test_lab_owned_legacy_report_without_a_session_is_counted_only_in_its_lab(records):
    devices, store, _ = records
    owner = srv._account_for_cookie(devices["owner"].session_cookie)
    store.open_report(session_id="orphan", protocol_id="legacy-protocol", protocol_title="Fictional",
        protocol_revision="revision-one", protocol_sha256="b" * 64, readiness_status="ready",
        development_only=True, organization_id=owner.organization_id)
    assert devices["admin"].get("/api/admin/pilot-metrics").json()["totals"]["experiments"] == 2
    assert devices["outside"].get("/api/admin/pilot-metrics").json()["days"] == []
