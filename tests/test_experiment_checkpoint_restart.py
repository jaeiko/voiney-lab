"""A stopped experiment is continued from a checkpoint in a new session.

Stopping an experiment ("종료") is final: the session stays stopped and its
report is not reopened. What it leaves behind is a list of checkpoints -- each
place it stood with every earlier step completed -- and choosing one starts a
new session there. The new session carries the earlier completions, each
pointing back at the completion it came from, and waits paused, so the voice
session continues it through the ordinary recovery path, which checks the
carried steps against the exact protocol revision again.

The store, the HTTP route and the WebSocket ``session.start`` boundary are
each exercised; the last one runs on the in-gel fixture and needs its PDF.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from voiney_lab.curated_protocol import CuratedProtocolSession
from voiney_lab.experiment_reports import ExperimentReportSettings
from voiney_lab.identity import AuthorizationDeniedError, Principal, Role
from voiney_lab.server import ServerConfig, app, voice_socket
from voiney_lab.workspace_store import (
    ExperimentCheckpointUnavailableError,
    WorkspaceConflictError,
    WorkspaceNotFoundError,
    WorkspaceSettings,
    initialize_workspace_store,
)

from tests.development_activation import development_activation_recorded
from tests.protocol_vocabulary_support import SOURCE_PDF, build_fixture, in_gel_fixture
from tests.test_candidate_a_websocket_integration import _ScriptedSocket

PROTOCOL = "fictional-four-steps"
REVISION = "fictional-four-steps-v1"
STEPS = tuple(f"step-{index}" for index in range(1, 5))


def _principal(name: str, tenant: str = "tenant-a", role: Role = Role.RESEARCHER) -> Principal:
    return Principal(
        principal_id=f"principal-{name}",
        subject=f"dev:{name}",
        organization_id=tenant,
        display_name=name,
        roles=frozenset({role}),
        authentication_method="development",
    )


def _store(path: Path):
    return initialize_workspace_store(WorkspaceSettings(True, path))


def _stopped_after(store, principal, completed: int, *, steps=STEPS, protocol=PROTOCOL,
                   revision=REVISION, advance_without_completing: int | None = None):
    """An experiment that completed ``completed`` steps in order, then stopped."""

    state = store.start_experiment(
        principal, protocol_id=protocol, protocol_revision_id=revision,
        current_step_id=steps[0], current_step_label="1",
    )
    session_id = state["session_id"]
    state = store.record_experiment_progress(
        principal, session_id, expected_version=state["version"],
        event_key="turn-1-start", event_type="protocol_started",
        step_id=steps[0], step_label="1",
    )
    for index in range(completed):
        skipped = advance_without_completing == index
        state = store.record_experiment_progress(
            principal, session_id, expected_version=state["version"],
            event_key=f"turn-{index + 2}-next",
            event_type="step_advanced" if skipped else "step_completed",
            step_id=steps[index], step_label=str(index + 1),
            next_step_id=steps[index + 1], next_step_label=str(index + 2),
            mark_completed=not skipped,
        )
    return store.transition_experiment(
        principal, session_id, action="stop", expected_version=state["version"],
        event_key="voice-stop", reason="voice_command",
    )


def test_a_stopped_experiment_lists_where_it_can_be_continued(tmp_path):
    researcher = _principal("researcher")
    store = _store(tmp_path)
    try:
        store.bootstrap_principal(researcher)
        stopped = _stopped_after(store, researcher, 2)
        recovery = store.experiment_timeline(researcher, stopped["session_id"])["recovery"]
        assert recovery["eligible"] is False
        assert recovery["next_action"] == "restart_from_checkpoint"
        assert [
            (item["step_id"], item["carried_step_ids"], item["stopped_here"])
            for item in recovery["checkpoints"]
        ] == [
            ("step-2", ["step-1"], False),
            ("step-3", ["step-1", "step-2"], True),
        ]
    finally:
        store.close()


def test_a_checkpoint_starts_a_new_session_and_leaves_the_stopped_one_alone(tmp_path):
    researcher = _principal("researcher")
    store = _store(tmp_path)
    try:
        store.bootstrap_principal(researcher)
        stopped = _stopped_after(store, researcher, 2)
        source_id = stopped["session_id"]
        new = store.restart_experiment_from_checkpoint(
            researcher, source_id, checkpoint_step_id="step-3",
            expected_version=stopped["version"],
        )
        assert new["session_id"] != source_id
        assert new["status"] == "paused"
        assert (new["protocol_id"], new["protocol_revision_id"]) == (PROTOCOL, REVISION)
        assert (new["current_step_id"], new["current_step_label"]) == ("step-3", "3")
        assert new["owner_principal_id"] == researcher.principal_id
        # The carried completions keep the original time and person and point
        # back at the completion they came from.
        originals = {item["step_id"]: item for item in stopped["completed_steps"]}
        assert [item["step_id"] for item in new["completed_steps"]] == ["step-1", "step-2"]
        for item in new["completed_steps"]:
            source = originals[item["step_id"]]
            assert item["completed_at"] == source["completed_at"]
            assert item["completed_by_principal_id"] == source["completed_by_principal_id"]
        assert [event["event_type"] for event in new["events"]] == [
            "session_started",
            "session_restarted_from_checkpoint",
            "step_completion_carried_over",
            "step_completion_carried_over",
        ]
        carried = [e for e in new["events"] if e["event_type"] == "step_completion_carried_over"]
        assert [e["payload"]["source_event_id"] for e in carried] == [
            originals["step-1"]["event_id"], originals["step-2"]["event_id"],
        ]
        assert new["events"][1]["payload"]["source_session_id"] == source_id

        after = store.get_experiment(researcher, source_id)
        assert after["status"] == "stopped"
        assert after["version"] == stopped["version"]
        assert after["completed_steps"] == stopped["completed_steps"]
        link = after["events"][-1]
        assert link["event_type"] == "checkpoint_restart_created"
        assert link["payload"]["new_session_id"] == new["session_id"]
    finally:
        store.close()


def test_the_new_session_resumes_where_the_protocol_revision_accepts_it(tmp_path):
    researcher = _principal("researcher")
    store = _store(tmp_path)
    try:
        store.bootstrap_principal(researcher)
        stopped = _stopped_after(store, researcher, 2)
        for checkpoint, carried in (("step-3", 2), ("step-2", 1)):
            new = store.restart_experiment_from_checkpoint(
                researcher, stopped["session_id"], checkpoint_step_id=checkpoint,
                expected_version=stopped["version"],
            )
            resumed = store.resume_experiment(
                researcher, new["session_id"], expected_version=new["version"],
                protocol_id=PROTOCOL, protocol_revision_id=REVISION,
                voice_connection_id="voice-connection-2",
            )
            assert resumed["status"] == "in_progress"
            assert resumed["events"][-1]["event_type"] == "session_resumed"
            # What server.py hands the protocol session on recovery.
            session = CuratedProtocolSession(build_fixture(
                protocol_id=PROTOCOL, title="Fictional four steps",
                steps=tuple(f"{index} Do part {index}." for index in range(1, 5)),
            ))
            session.restore_experiment_progress(
                current_step_id=str(resumed["current_step_id"]),
                completed_step_ids=tuple(
                    str(item["step_id"]) for item in resumed["completed_steps"]
                ),
            )
            assert session.active
            assert session.current_index == carried
    finally:
        store.close()


def test_a_continued_session_can_itself_be_stopped_and_continued(tmp_path):
    researcher = _principal("researcher")
    store = _store(tmp_path)
    try:
        store.bootstrap_principal(researcher)
        stopped = _stopped_after(store, researcher, 2)
        new = store.restart_experiment_from_checkpoint(
            researcher, stopped["session_id"], checkpoint_step_id="step-3",
            expected_version=stopped["version"],
        )
        running = store.resume_experiment(
            researcher, new["session_id"], expected_version=new["version"],
            protocol_id=PROTOCOL, protocol_revision_id=REVISION,
            voice_connection_id="voice-connection-2",
        )
        running = store.record_experiment_progress(
            researcher, new["session_id"], expected_version=running["version"],
            event_key="turn-9-next", event_type="step_completed",
            step_id="step-3", step_label="3", next_step_id="step-4",
            next_step_label="4", mark_completed=True,
        )
        again = store.transition_experiment(
            researcher, new["session_id"], action="stop",
            expected_version=running["version"], event_key="voice-stop-2",
        )
        checkpoints = store.experiment_timeline(
            researcher, again["session_id"]
        )["recovery"]["checkpoints"]
        assert [(item["step_id"], item["carried_step_count"]) for item in checkpoints] == [
            ("step-2", 1), ("step-3", 2), ("step-4", 3),
        ]
    finally:
        store.close()


def test_a_step_moved_past_without_completing_ends_the_checkpoints(tmp_path):
    researcher = _principal("researcher")
    store = _store(tmp_path)
    try:
        store.bootstrap_principal(researcher)
        stopped = _stopped_after(store, researcher, 3, advance_without_completing=1)
        checkpoints = store.experiment_timeline(
            researcher, stopped["session_id"]
        )["recovery"]["checkpoints"]
        # Step 2 was moved past without being completed, so step 2 is the last
        # place everything before was done.
        assert [(item["step_id"], item["carried_step_ids"]) for item in checkpoints] == [
            ("step-2", ["step-1"]),
        ]
        with pytest.raises(ExperimentCheckpointUnavailableError):
            store.restart_experiment_from_checkpoint(
                researcher, stopped["session_id"], checkpoint_step_id="step-4",
                expected_version=stopped["version"],
            )
    finally:
        store.close()


def test_nothing_completed_means_nothing_to_continue(tmp_path):
    researcher = _principal("researcher")
    store = _store(tmp_path)
    try:
        store.bootstrap_principal(researcher)
        stopped = _stopped_after(store, researcher, 0)
        recovery = store.experiment_timeline(researcher, stopped["session_id"])["recovery"]
        assert recovery["checkpoints"] == []
        assert recovery["next_action"] == "start_new_experiment"
    finally:
        store.close()


def test_only_the_owner_continues_only_a_stopped_experiment_at_an_offered_checkpoint(tmp_path):
    researcher = _principal("researcher")
    colleague = _principal("colleague")
    lab_admin = _principal("lab-admin", role=Role.LAB_ADMIN)
    reviewer = _principal("reviewer", role=Role.REVIEWER)
    outsider = _principal("outsider", tenant="tenant-b")
    store = _store(tmp_path)
    try:
        for principal in (researcher, colleague, lab_admin, reviewer, outsider):
            store.bootstrap_principal(principal)
        stopped = _stopped_after(store, researcher, 2)
        source_id, version = stopped["session_id"], stopped["version"]

        with pytest.raises(WorkspaceConflictError):
            store.restart_experiment_from_checkpoint(
                researcher, source_id, checkpoint_step_id="step-3",
                expected_version=version - 1,
            )
        with pytest.raises(ExperimentCheckpointUnavailableError):
            store.restart_experiment_from_checkpoint(
                researcher, source_id, checkpoint_step_id="step-1",
                expected_version=version,
            )
        # A lab admin may write to the tenant's experiments, but continuing
        # one is the owner's: the carried completions are theirs.
        for other in (colleague, lab_admin, outsider):
            with pytest.raises(WorkspaceNotFoundError):
                store.restart_experiment_from_checkpoint(
                    other, source_id, checkpoint_step_id="step-3",
                    expected_version=version,
                )
        with pytest.raises(AuthorizationDeniedError):
            store.restart_experiment_from_checkpoint(
                reviewer, source_id, checkpoint_step_id="step-3",
                expected_version=version,
            )
        # A reviewer may read the timeline but is not offered the owner's
        # checkpoints.
        assert store.experiment_timeline(reviewer, source_id)["recovery"]["checkpoints"] == []

        open_one = store.start_experiment(
            researcher, protocol_id=PROTOCOL, protocol_revision_id=REVISION,
            current_step_id="step-1", current_step_label="1",
        )
        with pytest.raises(ExperimentCheckpointUnavailableError):
            store.restart_experiment_from_checkpoint(
                researcher, open_one["session_id"], checkpoint_step_id="step-1",
                expected_version=open_one["version"],
            )
    finally:
        store.close()


def _profiles():
    return [{
        "profile_id": "researcher-a",
        "principal_id": "principal-researcher-a",
        "organization_id": "tenant-a",
        "display_name": "Researcher A",
        "roles": ["researcher"],
    }]


def _api_principal() -> Principal:
    return Principal(
        principal_id="principal-researcher-a", subject="dev:researcher-a",
        organization_id="tenant-a", display_name="Researcher A",
        roles=frozenset({Role.RESEARCHER}), authentication_method="development",
    )


async def _post(path: str, body: dict[str, object]):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.post(
            path, json=body, headers={"X-Voice-Dev-Profile": "researcher-a"}
        )


def test_the_http_route_starts_the_new_session(monkeypatch, tmp_path):
    monkeypatch.setenv("VOICE_WORKFLOW_AGENT_WORKSPACE_ENABLED", "true")
    monkeypatch.setenv("VOICE_WORKFLOW_AGENT_WORKSPACE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("VOICE_WORKFLOW_AGENT_USAGE_SCOPE", "demo")
    monkeypatch.setenv("VOICE_WORKFLOW_AGENT_DEV_AUTH_PROFILES", json.dumps(_profiles()))
    for name in (
        "VOICE_WORKFLOW_AGENT_OIDC_ISSUER",
        "VOICE_WORKFLOW_AGENT_OIDC_AUDIENCE",
        "VOICE_WORKFLOW_AGENT_OIDC_JWKS_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    principal = _api_principal()
    store = _store(tmp_path)
    try:
        store.bootstrap_principal(principal)
        stopped = _stopped_after(store, principal, 2)
    finally:
        store.close()
    route = f"/api/workspace/experiments/{stopped['session_id']}/checkpoint-restart"

    created = asyncio.run(_post(route, {
        "checkpoint_step_id": "step-3", "expected_version": stopped["version"],
    }))
    assert created.status_code == 201
    body = created.json()
    assert body["status"] == "paused"
    assert body["current_step_id"] == "step-3"

    stale = asyncio.run(_post(route, {
        "checkpoint_step_id": "step-3", "expected_version": stopped["version"] - 1,
    }))
    assert (stale.status_code, stale.json()["detail"]) == (409, "workspace_conflict")
    unknown = asyncio.run(_post(route, {
        "checkpoint_step_id": "step-9", "expected_version": stopped["version"],
    }))
    assert (unknown.status_code, unknown.json()["detail"]) == (
        409, "experiment_checkpoint_unavailable",
    )
    malformed = asyncio.run(_post(route, {"checkpoint_step_id": "step-3"}))
    assert malformed.status_code == 400


def test_the_cockpit_offers_the_checkpoints_of_a_stopped_experiment():
    html = (
        Path(__file__).resolve().parents[1] / "src/voiney_lab/static/index.html"
    ).read_text(encoding="utf-8")
    for required in (
        'id="experiment-checkpoint-select"',
        'id="experiment-checkpoint-restart"',
        "function renderCheckpointControls",
        "async function restartExperimentFromCheckpoint",
        "/checkpoint-restart`",
        "checkpoint_step_id:stepId,expected_version:currentExperimentSessionVersion",
        'currentExperimentSessionStatus==="stopped"',
        "experiment_checkpoint_unavailable:",
        "session_restarted_from_checkpoint:",
        "step_completion_carried_over:",
    ):
        assert required in html
    block = html.split("function renderCheckpointControls", 1)[1].split(
        "async function loadExperimentTimeline", 1
    )[0]
    assert "innerHTML" not in block
    assert "textContent" in block


@unittest.skipUnless(
    SOURCE_PDF.is_file(),
    f"requires the externally licensed Candidate A source PDF at {SOURCE_PDF}",
)
class CheckpointVoiceRecoveryTests(unittest.TestCase):
    """The voice session continues the new session at the checkpoint."""

    protocol_id = "candidate-a-curated-development-v1"

    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = in_gel_fixture()

    def setUp(self) -> None:
        activation = development_activation_recorded()
        activation.__enter__()
        self.addCleanup(activation.__exit__, None, None, None)

    def _start(self, environment, session_id, version):
        placeholder = Path("/tmp/offline-session-contract")
        config = ServerConfig(
            placeholder, None, "test_only", frozenset({"ko", "en"}), "ko",
            None, None, placeholder, placeholder, placeholder,
        )
        socket = _ScriptedSocket([{
            "type": "session.start", "configuration_id": 1, "mode": "cascade",
            "language": "ko", "protocol_id": self.protocol_id,
            "experiment_session_id": session_id,
            "experiment_session_version": version,
        }])
        with patch.dict("os.environ", environment, clear=False), patch(
            "voiney_lab.server.server_config", return_value=config
        ), patch(
            "voiney_lab.server.ExperimentReportSettings.from_environment",
            return_value=ExperimentReportSettings(False),
        ), patch(
            "voiney_lab.server.load_curated_protocol_fixture",
            return_value=self.fixture,
        ):
            asyncio.run(voice_socket(socket))
        return socket

    def test_session_start_restores_the_checkpoint_step(self) -> None:
        profile = _profiles()[0]
        principal = _api_principal()
        steps = tuple(step.step_id for step in self.fixture.steps)
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            environment = {
                "VOICE_WORKFLOW_AGENT_WORKSPACE_ENABLED": "true",
                "VOICE_WORKFLOW_AGENT_WORKSPACE_DATA_DIR": str(workspace),
                "VOICE_WORKFLOW_AGENT_USAGE_SCOPE": "demo",
                "VOICE_WORKFLOW_AGENT_DEV_AUTH_PROFILES": json.dumps([profile]),
            }
            store = _store(workspace)
            try:
                store.bootstrap_principal(principal)
                stopped = _stopped_after(
                    store, principal, 2, steps=steps, protocol=self.protocol_id,
                    revision=self.fixture.revision_id,
                )
                new = store.restart_experiment_from_checkpoint(
                    principal, stopped["session_id"], checkpoint_step_id=steps[2],
                    expected_version=stopped["version"],
                )
            finally:
                store.close()

            socket = self._start(environment, new["session_id"], new["version"])

            self.assertIsNone(socket.error_message())
            ready = socket.ready_event()
            self.assertEqual(ready["experiment_session_id"], new["session_id"])
            self.assertTrue(ready["experiment_recovered"])
            restored = next(
                item["state"] for item in socket.sent
                if item["type"] == "protocol.fixture.state"
            )
            self.assertTrue(restored["active"])
            self.assertEqual(restored["current_step_id"], steps[2])
            self.assertEqual(restored["current_step_label"], "3")

            store = _store(workspace)
            try:
                continued = store.get_experiment(principal, new["session_id"])
                original = store.get_experiment(principal, stopped["session_id"])
            finally:
                store.close()
            self.assertEqual(continued["status"], "in_progress")
            self.assertEqual(original["status"], "stopped")
