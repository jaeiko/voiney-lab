"""Previous notebook, start check and day sequence stay with one person."""
from __future__ import annotations

from voiney_lab import experiment_reports as er


def make(store, monkeypatch, session, owner, at, *, protocol="protocol-one", lab="lab-a"):
    monkeypatch.setattr(er, "_now", lambda: at)
    # A legacy store has no ownership arguments: the same fixture reproduces
    # its cross-account history before the ownership migration exists.
    ownership = {"organization_id": lab, "owner_principal_id": owner}
    import inspect
    if "organization_id" not in inspect.signature(store.open_report).parameters:
        ownership = {}
    return store.open_report(session_id=session, protocol_id=protocol, protocol_title="Fictional source",
        protocol_revision="revision-one", protocol_sha256="a" * 64, readiness_status="ready",
        development_only=True, **ownership)


def test_previous_run_is_same_account_and_protocol(tmp_path, monkeypatch):
    store = er.ExperimentReportStore(tmp_path / "reports.sqlite")
    mine = make(store, monkeypatch, "my-old", "owner", "2026-10-20T01:00:00+00:00")
    store.finalize(mine["report_id"], status="completed", event_key="end")
    other = make(store, monkeypatch, "peer-old", "peer", "2026-10-20T02:00:00+00:00")
    store.finalize(other["report_id"], status="completed", event_key="end")
    now = make(store, monkeypatch, "my-now", "owner", "2026-10-20T03:00:00+00:00")
    assert store.previous_finished_report(now)["report_id"] == mine["report_id"]
    assert store._previous_start(now) == mine["started_at"]


def test_first_run_has_no_other_person_as_previous(tmp_path, monkeypatch):
    store = er.ExperimentReportStore(tmp_path / "reports.sqlite")
    peer = make(store, monkeypatch, "peer", "peer", "2026-10-20T01:00:00+00:00")
    store.finalize(peer["report_id"], status="completed", event_key="end")
    now = make(store, monkeypatch, "mine", "owner", "2026-10-20T02:00:00+00:00")
    assert store.previous_finished_report(now) is None
    assert store._previous_start(now) is None


def test_day_sequence_is_same_account_lab_and_protocol(tmp_path, monkeypatch):
    store = er.ExperimentReportStore(tmp_path / "reports.sqlite")
    make(store, monkeypatch, "peer", "peer", "2026-10-20T01:00:00+00:00")
    make(store, monkeypatch, "other-lab", "owner", "2026-10-20T02:00:00+00:00", lab="lab-b")
    make(store, monkeypatch, "other-protocol", "owner", "2026-10-20T03:00:00+00:00", protocol="protocol-two")
    mine = make(store, monkeypatch, "mine", "owner", "2026-10-20T04:00:00+00:00")
    second = make(store, monkeypatch, "mine-second", "owner", "2026-10-20T05:00:00+00:00")
    assert mine["day_sequence"] == 1
    assert second["day_sequence"] == 2
    summaries = store.list_reports(session_id="mine-second")
    assert summaries[0]["day_sequence"] == 2


def test_start_check_only_uses_owner_answers(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from voiney_lab import server
    from voiney_lab.start_check import START_CHECK_EVENT
    store = er.ExperimentReportStore(tmp_path / "reports.sqlite")
    mine = make(store, monkeypatch, "mine", "owner", "2026-10-20T04:00:00+00:00")
    events = [SimpleNamespace(event_type=START_CHECK_EVENT,
        payload={"recorded_at": "2026-10-20T03:00:00+00:00", "answer": who,
                 "actor_principal_id": who}) for who in ("owner", "peer", None)]
    events.append(SimpleNamespace(event_type="protocol_registered", payload=None))
    ledger = SimpleNamespace(list_events=lambda _: events, close=lambda: None)
    monkeypatch.setattr(server, "_open_protocol_catalog", lambda: (None, ledger))
    answers = er.report_start_check_answers(mine, since=None)
    assert [item["answer"] for item in answers] == ["owner"]
