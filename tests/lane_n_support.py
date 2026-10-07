"""Shared helpers for the lane N tests: a fictional protocol and a real voice session.

The protocol is lane R7's fictional wash (a repeat of steps 2-4 stated at
step 5) with a 10-minute source timer on step 2, built from text alone, so
these tests need no licensed PDF and run in both pytest baselines.

``VoiceNotesHarness`` opens one real ``voice_socket`` session over a queued
socket with the workspace and the experiment report store both enabled, as a
pilot runs them, and hands each turn to ``run_turn`` the way the browser's
audio and the STT provider would have produced it. No model is called: the
report writer is absent and AsyncOpenAI refuses.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from tests.development_activation import development_activation_recorded
from tests.protocol_vocabulary_support import build_fixture
from tests.test_voice_pause_resume_persistence import _QueuedSocket
from voiney_lab import experiment_protocol as domain
import voiney_lab.server as server_module
from voiney_lab.experiment_reports import ExperimentReportStore
from voiney_lab.identity import Principal, Role
from voiney_lab.server import (
    ListenerSession,
    ServerConfig,
    Transcription,
    TurnState,
    run_turn,
    voice_socket,
)
from voiney_lab.workspace_store import WorkspaceSettings, initialize_workspace_store

PROTOCOL_ID = "lane-n-wash"
STEPS = (
    "1 Cut the stained band into pieces and place them in a 1.5 mL tube.",
    "2 Wash the pieces with 500 µL of Solution A for 10 min.",
    "3 Remove and discard Solution A.",
    "4 Wash the pieces with 500 µL of Solution B.",
    "5 Repeat steps 2-4 until the band is clear.",
    "6 Dry the pieces in the speedvac.",
)
PROFILE = {
    "profile_id": "researcher-a",
    "principal_id": "principal-researcher-a",
    "organization_id": "tenant-a",
    "display_name": "Researcher A",
    "roles": ["researcher"],
}


def notes_fixture(*, timer_seconds: int = 600):
    """Lane R7's fictional wash with a repeat at step 5 and a timer on step 2."""

    base = build_fixture(
        protocol_id=PROTOCOL_ID, title="Fictional wash", steps=STEPS,
        materials=("Solution A", "Solution B"), equipment=("Speedvac",),
    )
    protocol = base.draft.protocol
    anchor = protocol.sections[0].steps[4]
    protocol = dataclasses.replace(protocol, constructs=(
        domain.RepeatUntil(
            repetition_id="repeat-2-4",
            condition_source_text="until the band is clear",
            repeated_step_ids=("step-2", "step-3", "step-4"),
            evidence=domain.SourceEvidence(1, anchor.instruction_source_text),
            step_id="step-5",
        ),
    ))
    domain.validate_protocol(protocol)
    draft = dataclasses.replace(
        base.draft, protocol=protocol, readiness=domain.assess_readiness(protocol),
    )
    return dataclasses.replace(
        base, draft=draft,
        timer_manifest={"step-2": timer_seconds} if timer_seconds else None,
    )


class VoiceNotesHarness:
    """One real voice session per scenario, workspace and report store on.

    A mixin: ``setUp`` makes a fresh tenant; ``voice(scenario)`` runs one
    session; ``turns(*said)`` is the short form for a list of utterances.
    """

    fixture = None

    def setUp(self) -> None:
        activation = development_activation_recorded()
        activation.__enter__()
        self.addCleanup(activation.__exit__, None, None, None)
        if self.fixture is None:
            type(self).fixture = notes_fixture()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.workspace_dir = Path(tmp.name) / "workspace"
        self.report_db = Path(tmp.name) / "reports.sqlite"
        self.principal = Principal(
            principal_id=PROFILE["principal_id"],
            subject="dev:researcher-a",
            organization_id=PROFILE["organization_id"],
            display_name=PROFILE["display_name"],
            roles=frozenset({Role.RESEARCHER}),
            authentication_method="development",
        )
        store = initialize_workspace_store(WorkspaceSettings(True, self.workspace_dir))
        store.bootstrap_principal(self.principal)
        store.bind_resource(self.principal, "protocol_catalog", PROTOCOL_ID)
        store.close()
        self.environment = {
            "VOINEY_LAB_WORKSPACE_ENABLED": "true",
            "VOINEY_LAB_WORKSPACE_DATA_DIR": str(self.workspace_dir),
            "VOINEY_LAB_EXPERIMENT_REPORTS_ENABLED": "true",
            "VOINEY_LAB_EXPERIMENT_REPORT_DB": str(self.report_db),
            "VOINEY_LAB_USAGE_SCOPE": "demo",
            "VOINEY_LAB_DEV_AUTH_PROFILES": json.dumps([PROFILE]),
            "VOINEY_LAB_LLM_ROUTER_ENABLED": "false",
        }

    def voice(self, scenario, *extra_patches, start: dict | None = None):
        """Open one voice session, run ``scenario(socket, listener, say)``, close it."""

        placeholder = Path("/tmp/offline-session-contract")
        config = ServerConfig(
            placeholder, None, "test_only", frozenset({"ko", "en"}), "ko",
            None, None, placeholder, placeholder, placeholder,
        )
        captured: list[ListenerSession] = []

        def listener_factory(*args, **kwargs):
            listener = ListenerSession(*args, **kwargs)
            captured.append(listener)
            return listener

        async def immediate(function, *args, **kwargs):
            return function(*args, **kwargs)

        self.spoken: dict[int | None, list[str]] = {}

        def synthesize(text, *_args, **_kwargs):
            turn = captured[0].active_turn_id if captured else None
            self.spoken.setdefault(turn, []).append(text)
            return b"\0\0"

        async def run() -> _QueuedSocket:
            socket = _QueuedSocket()
            token = server_module._REQUEST_PRINCIPAL.set(self.principal)
            try:
                connection = asyncio.create_task(voice_socket(socket))
                socket.control({
                    "type": "session.start", "configuration_id": 1,
                    "mode": "cascade", "language": "ko",
                    "protocol_id": PROTOCOL_ID, **(start or {}),
                })
                await socket.wait_for(
                    lambda item: item["type"] in {"session.ready", "error"})
                if socket.for_turn(None, "error") or not captured:
                    socket.disconnect()
                    await connection
                    return socket
                listener = captured[0]

                async def say(turn_id: int, text: str) -> None:
                    listener.active_turn_id = turn_id
                    listener.detector.state = TurnState.PROCESSING
                    await run_turn(
                        socket, listener, b"\0\0", turn_id, listener.generation,
                        accepted_transcription=Transcription(text, "ko"),
                        accepted_stt_ms=1,
                    )
                    listener.playback_ended(turn_id)

                await scenario(socket, listener, say)
                socket.disconnect()
                await connection
            finally:
                server_module._REQUEST_PRINCIPAL.reset(token)
            return socket

        patches = [
            patch.dict("os.environ", self.environment, clear=False),
            patch("voiney_lab.server.server_config", return_value=config),
            patch(
                "voiney_lab.server.load_curated_protocol_fixture",
                return_value=self.fixture,
            ),
            patch("voiney_lab.server.ListenerSession", side_effect=listener_factory),
            patch("voiney_lab.server.synthesize", side_effect=synthesize),
            patch("voiney_lab.server.asyncio.to_thread", side_effect=immediate),
            patch("voiney_lab.server._report_writer_brain", return_value=None),
            patch(
                "voiney_lab.server.AsyncOpenAI",
                side_effect=AssertionError("no model call for these turns"),
            ),
            *extra_patches,
        ]
        for item in patches:
            item.start()
        try:
            socket = asyncio.run(run())
        finally:
            for item in reversed(patches):
                item.stop()
        self.listener = captured[0] if captured else None
        return socket

    def turns(self, *said: str, start: dict | None = None, snapshot=None):
        """Say each line in turn; ``snapshot(listener)`` runs before the socket closes."""

        seen: dict = {}

        async def scenario(socket, listener, say):
            for turn_id, text in enumerate(said, 1):
                await say(turn_id, text)
            if snapshot is not None:
                seen["value"] = snapshot(listener)

        socket = self.voice(scenario, start=start)
        self.snapshot = seen.get("value")
        return socket

    # --- What the records hold --------------------------------------------

    def report(self) -> dict:
        store = ExperimentReportStore(self.report_db)
        reports = store.list_reports()
        assert reports, "no experiment report was opened"
        return store.get_report(reports[-1]["report_id"])

    def report_markdown(self) -> str:
        store = ExperimentReportStore(self.report_db)
        return store.export_markdown(self.report()["report_id"], fixture=self.fixture).decode()

    def experiment(self, session_id: str | None = None) -> dict:
        store = initialize_workspace_store(WorkspaceSettings(True, self.workspace_dir))
        try:
            return store.get_experiment(
                self.principal, session_id or self.listener.session_id)
        finally:
            store.close()

    def timeline(self) -> dict:
        store = initialize_workspace_store(WorkspaceSettings(True, self.workspace_dir))
        try:
            return store.experiment_timeline(self.principal, self.listener.session_id)
        finally:
            store.close()


def shown(socket) -> dict[int, str]:
    """turn id -> the reply shown on the screen (its last reply.complete)."""

    out: dict[int, str] = {}
    for item in socket.sent:
        if item.get("type") == "reply.complete" and isinstance(item.get("turn_id"), int):
            out[item["turn_id"]] = item.get("text")
    return out
