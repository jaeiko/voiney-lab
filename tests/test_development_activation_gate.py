"""A blocked protocol is not runnable, and nothing quietly says otherwise.

Until STEP 23 the configured curated fixture reported
``available_for_execution: True`` unconditionally.  Being the configured
fixture is a statement about which file a launcher pointed at; it was standing
in for a statement about whether anyone had judged the protocol fit to run.
Every readiness gate could be blocked -- an unresolved ambiguity, no declared
safety warning, two unsupported repeat-untils -- and both the catalog listing
and the voice session still offered it.

These tests pin the three things that replaced it: the field is derived from
readiness, the only way to say yes is a recorded development activation, and
withdrawing that activation takes the yes back.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from voice_workflow_agent import experiment_protocol as domain
from voice_workflow_agent.experiment_protocol_config import (
    ProtocolPersistenceSettings,
)
from voice_workflow_agent.experiment_protocol_store import (
    initialize_protocol_store,
)
from voice_workflow_agent.protocol_catalog import (
    ProtocolApprovalError,
    ProtocolCatalog,
    ProtocolCatalogUnavailableError,
)
from voice_workflow_agent.server import (
    ServerConfig,
    _candidate_fixture_execution_state,
    voice_socket,
)
import voice_workflow_agent.server as server_module

from tests.test_protocol_catalog import write_text_pdf, analysis_draft

PROTOCOL_ID = "candidate-a-curated-development-v1"


class _ConfiguredFixture:
    """A configured development fixture, as the launcher hands one over."""

    def __init__(self, *, protocol_id: str = PROTOCOL_ID) -> None:
        self.protocol_id = protocol_id
        self.revision_id = "fixture-test-revision"
        self.development_only = True
        self.title = "Development fixture"
        self.source_pdf_path = Path("/tmp/offline-session-contract")
        self.source_pdf_sha256 = "0" * 64
        self.fixture_sha256 = "1" * 64
        self.timer_manifest: dict[str, int] = {}
        self.steps = (
            SimpleNamespace(
                source_label="1",
                step_id="step-1",
                instruction_source_text="Exact source instruction.",
                evidence=SimpleNamespace(source_page_number=1),
                warnings=(),
            ),
        )
        self.draft = SimpleNamespace(
            readiness=SimpleNamespace(
                status=SimpleNamespace(value="analysis_required")
            )
        )

    def visual_for_step(self, index):  # noqa: ARG002 - fixture stub
        return None


class DevelopmentActivationGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.settings = ProtocolPersistenceSettings(True, self.root / "catalog")
        self.store = initialize_protocol_store(self.settings)
        self.catalog = ProtocolCatalog(self.store)
        self.sample_pdf = self.root / "sample.pdf"
        write_text_pdf(
            self.sample_pdf,
            "Protocol Test\nSection preparation\n1. Add solution.\nWear gloves.",
            title="Protocol Test",
        )

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def _registered_with_analysis(self) -> str:
        registration = self.catalog.register(
            self.sample_pdf,
            source_filename="sample.pdf",
            media_type="application/pdf",
        )
        protocol_id = registration.entry.protocol_id
        draft = analysis_draft(self.sample_pdf, protocol_id, "Protocol Test")
        self.store.append_analysis_revision(
            protocol_id,
            1,
            f"analysis-{registration.entry.source_sha256[:24]}",
            draft.protocol,
            draft.readiness,
            draft.capability_policy_id,
        )
        return protocol_id

    def _clear_the_only_gate(self, protocol_id: str) -> None:
        self.catalog.acknowledge_readiness_gate(
            protocol_id,
            "pdf-1-analysis-1",
            reason_code=(
                domain.ReadinessReasonCode.NO_DECLARED_SAFETY_WARNINGS.value
            ),
            actor_principal_id="reviewer@example.org",
            actor_role="reviewer",
            comment="Warnings reviewed against the source.",
        )

    def test_a_blocked_protocol_never_becomes_executable(self) -> None:
        protocol_id = self._registered_with_analysis()

        # The gate is standing: no acknowledgement has been recorded.
        entry = self.catalog.get_entry(protocol_id)
        self.assertFalse(entry.available_for_execution)
        self.assertEqual(entry.approval_status, "unapproved")
        with self.assertRaises(ProtocolCatalogUnavailableError):
            self.catalog.activate_development(protocol_id)
        self.assertFalse(
            self.catalog.get_entry(protocol_id).available_for_execution
        )
        self.assertFalse(
            self.catalog.development_activation_context(protocol_id)["activated"]
        )

    def test_execution_needs_an_activation_and_the_ledger_records_who(self) -> None:
        protocol_id = self._registered_with_analysis()
        self._clear_the_only_gate(protocol_id)

        # Gates cleared is not yet authority to run.
        self.assertFalse(
            self.catalog.get_entry(protocol_id).available_for_execution
        )

        activated = self.catalog.activate_development(
            protocol_id,
            actor_principal_id="developer@example.org",
            actor_role="lab_admin",
        )
        self.assertTrue(activated.available_for_execution)
        context = self.catalog.development_activation_context(protocol_id)
        self.assertTrue(context["activated"])
        self.assertEqual(context["actor_principal_id"], "developer@example.org")
        self.assertEqual(context["actor_role"], "lab_admin")
        self.assertEqual(context["authority"], "development_policy")
        self.assertIsNotNone(context["recorded_at"])

        events = [
            event
            for event in self.store.list_events(protocol_id)
            if event.event_type == "protocol_development_activated"
        ]
        self.assertEqual(len(events), 1)
        self.assertEqual(
            events[0].payload["actor_principal_id"], "developer@example.org"
        )

    def test_withdrawing_the_activation_blocks_execution_again(self) -> None:
        protocol_id = self._registered_with_analysis()
        self._clear_the_only_gate(protocol_id)
        self.catalog.activate_development(
            protocol_id,
            actor_principal_id="developer@example.org",
            actor_role="lab_admin",
        )
        self.assertTrue(
            self.catalog.get_entry(protocol_id).available_for_execution
        )

        withdrawn = self.catalog.deactivate_development(
            protocol_id,
            actor_principal_id="reviewer@example.org",
            actor_role="reviewer",
        )
        self.assertFalse(withdrawn.available_for_execution)
        self.assertEqual(withdrawn.approval_status, "unapproved")
        context = self.catalog.development_activation_context(protocol_id)
        self.assertFalse(context["activated"])

        # Nothing to withdraw twice, and re-activating is allowed and recorded
        # under a fresh identifier rather than colliding with the first.
        with self.assertRaises(ProtocolApprovalError):
            self.catalog.deactivate_development(protocol_id)
        self.catalog.activate_development(
            protocol_id,
            actor_principal_id="developer@example.org",
            actor_role="lab_admin",
        )
        self.assertTrue(
            self.catalog.get_entry(protocol_id).available_for_execution
        )
        decisions = [
            event.event_type
            for event in self.store.list_events(protocol_id)
            if event.event_type
            in {
                "protocol_development_activated",
                "protocol_development_deactivated",
            }
        ]
        self.assertEqual(
            decisions,
            [
                "protocol_development_activated",
                "protocol_development_deactivated",
                "protocol_development_activated",
            ],
        )


class ConfiguredFixtureExecutionStateTests(unittest.TestCase):
    """What the server answers for the fixture a launcher configured."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.settings = ProtocolPersistenceSettings(True, self.root / "catalog")
        self.fixture = _ConfiguredFixture()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _state(self, *, scope: str = "demo", materialized: bool = False):
        store = initialize_protocol_store(self.settings)
        catalog = ProtocolCatalog(store)

        def open_catalog():
            return catalog, store

        with patch.dict(
            os.environ, {"VOICE_WORKFLOW_AGENT_USAGE_SCOPE": scope}, clear=False
        ), patch.object(
            server_module, "_protocol_store_settings", return_value=self.settings
        ), patch.object(
            server_module, "_open_protocol_catalog", side_effect=open_catalog
        ), patch.object(
            ProtocolCatalog,
            "development_fixture_is_materialized",
            return_value=materialized,
        ):
            try:
                return _candidate_fixture_execution_state(self.fixture)
            finally:
                pass

    def test_an_unmaterialized_fixture_is_not_executable(self) -> None:
        state = self._state(materialized=False)
        self.assertFalse(state["available_for_execution"])
        self.assertEqual(
            state["blocked_reason"], "development_fixture_not_materialized"
        )

    def test_an_operational_scope_refuses_before_anything_is_read(self) -> None:
        state = self._state(scope="operational", materialized=True)
        self.assertFalse(state["available_for_execution"])
        self.assertEqual(state["blocked_reason"], "usage_scope_not_development")

    def test_a_disabled_protocol_store_is_a_no(self) -> None:
        disabled = ProtocolPersistenceSettings(False, None)
        with patch.dict(
            os.environ,
            {"VOICE_WORKFLOW_AGENT_USAGE_SCOPE": "demo"},
            clear=False,
        ), patch.object(
            server_module, "_protocol_store_settings", return_value=disabled
        ):
            state = _candidate_fixture_execution_state(self.fixture)
        self.assertFalse(state["available_for_execution"])
        self.assertEqual(state["blocked_reason"], "protocol_store_disabled")


class ConfiguredFixtureSessionGateTests(unittest.TestCase):
    """A voice session refuses a configured fixture nobody activated."""

    def test_session_start_refuses_an_unactivated_configured_fixture(self) -> None:
        placeholder = Path("/tmp/offline-session-contract")
        config = ServerConfig(
            placeholder, None, "test_only", frozenset({"ko"}), "ko",
            None, None, placeholder, placeholder, placeholder,
        )

        class Socket:
            def __init__(self):
                self.sent = []
                self.messages = iter((
                    {"text": json.dumps({
                        "type": "session.start", "mode": "cascade",
                        "language": "ko", "protocol_id": PROTOCOL_ID,
                        "configuration_id": 11,
                    })},
                    {"type": "websocket.disconnect", "code": 1000},
                ))

            async def accept(self):
                pass

            async def send_text(self, value):
                self.sent.append(json.loads(value))

            async def receive(self):
                return next(self.messages)

        socket = Socket()
        blocked = {
            "available_for_execution": False,
            "blocked_reason": "development_activation_not_recorded",
            "development_activation": {"activated": False},
            "approval": {},
        }
        with patch.object(
            server_module, "server_config", return_value=config
        ), patch.object(
            server_module,
            "load_curated_protocol_fixture",
            return_value=_ConfiguredFixture(),
        ), patch.object(
            server_module,
            "_candidate_fixture_execution_state",
            return_value=blocked,
        ), patch.object(
            server_module, "ProcedureStore"
        ), patch.object(
            server_module, "load_procedure_definitions"
        ):
            asyncio.run(voice_socket(socket))

        self.assertFalse(
            any(item["type"] == "session.ready" for item in socket.sent)
        )
        required = next(
            item for item in socket.sent
            if item["type"] == "session.configuration_required"
        )
        self.assertEqual(required["reason"], "protocol_selection_unavailable")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
