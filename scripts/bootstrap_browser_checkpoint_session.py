"""Create a throwaway, recoverable browser-test session before a checkpoint.

This is a deterministic acceptance-test fixture, not a product state shortcut.
It uses the same immutable executable protocol fixture and WorkspaceStore
transition methods as the server, records every earlier step as completed, and
leaves the selected normal step incomplete for the browser to complete through
the real WebSocket control boundary.
"""

from __future__ import annotations

import argparse
import json
import secrets
from pathlib import Path

from voice_workflow_agent.experiment_protocol_config import (
    ProtocolPersistenceSettings,
)
from voice_workflow_agent.experiment_protocol_store import (
    initialize_protocol_store,
)
from voice_workflow_agent.identity import Principal, Role
from voice_workflow_agent.protocol_catalog import ProtocolCatalog
from voice_workflow_agent.workspace_store import (
    WorkspaceSettings,
    initialize_workspace_store,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol-data-dir", type=Path, required=True)
    parser.add_argument("--workspace-data-dir", type=Path, required=True)
    parser.add_argument("--protocol-id", required=True)
    parser.add_argument("--current-step-label", default="6")
    return parser.parse_args()


def main() -> None:
    args = _arguments()
    protocol_store = initialize_protocol_store(
        ProtocolPersistenceSettings(True, args.protocol_data_dir.resolve())
    )
    try:
        entry = ProtocolCatalog(protocol_store).get_entry(args.protocol_id)
        fixture = ProtocolCatalog(protocol_store).load_executable_fixture(
            args.protocol_id
        )
    finally:
        protocol_store.close()

    target_index = next(
        (
            index
            for index, step in enumerate(fixture.steps)
            if step.source_label == args.current_step_label
        ),
        None,
    )
    if target_index is None or target_index < 1:
        raise RuntimeError("The browser fixture target must follow a normal step.")
    if fixture.human_checkpoints.get(fixture.steps[target_index].step_id):
        raise RuntimeError("The browser fixture must stop before a checkpoint step.")

    principal = Principal(
        principal_id="dev-local-admin",
        subject="dev:local-admin",
        organization_id="tenant-local-demo",
        display_name="Local Lab Admin",
        roles=frozenset({Role.LAB_ADMIN}),
        authentication_method="development",
    )
    workspace = initialize_workspace_store(
        WorkspaceSettings(True, args.workspace_data_dir.resolve())
    )
    try:
        workspace.bootstrap_principal(principal)
        session_id = f"browser-checkpoint-{secrets.token_hex(8)}"
        first = fixture.steps[0]
        state = workspace.start_experiment(
            principal,
            session_id=session_id,
            protocol_id=entry.protocol_id,
            protocol_revision_id=entry.revision_id,
            current_step_id=first.step_id,
            current_step_label=first.source_label,
            voice_connection_id="browser-fixture",
        )
        state = workspace.record_experiment_progress(
            principal,
            session_id,
            expected_version=int(state["version"]),
            event_key="browser-fixture-protocol-started",
            event_type="protocol_started",
            step_id=first.step_id,
            step_label=first.source_label,
            next_step_id=first.step_id,
            next_step_label=first.source_label,
            payload={"authority": "deterministic_browser_fixture"},
        )
        for index, step in enumerate(fixture.steps[:target_index]):
            next_step = fixture.steps[index + 1]
            state = workspace.record_experiment_progress(
                principal,
                session_id,
                expected_version=int(state["version"]),
                event_key=f"browser-fixture-completed-{step.step_id}",
                event_type="step_completed",
                step_id=step.step_id,
                step_label=step.source_label,
                next_step_id=next_step.step_id,
                next_step_label=next_step.source_label,
                mark_completed=True,
                payload={"authority": "deterministic_browser_fixture"},
            )
    finally:
        workspace.close()

    print(json.dumps({
        "session_id": session_id,
        "version": state["version"],
        "protocol_id": entry.protocol_id,
        "revision_id": entry.revision_id,
        "current_step_id": state["current_step_id"],
        "current_step_label": state["current_step_label"],
    }, separators=(",", ":")))


if __name__ == "__main__":
    main()
