"""The real socket cannot bypass account/revision-bound start confirmations."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

from tests.identity_support import development_principal
from tests.test_lane_rv_review_pending import _CatalogCase
from tests.test_voice_pause_resume_persistence import _QueuedSocket
from voiney_lab import server
from voiney_lab.start_check import StartCheck
from voiney_lab.experiment_protocol_store import initialize_protocol_store
from voiney_lab.protocol_catalog import ProtocolCatalog


class StartGateTests(_CatalogCase):
    def setUp(self):
        super().setUp()
        self.analyze(self.answer(pages=(1, 2, 3)))
        self.fixture = self.catalog.load_executable_fixture(self.protocol_id)
        self.principal = development_principal()
        self.target = StartCheck(self.catalog).questions(self.protocol_id)["target"]

    def confirm_values(self, actor=None):
        for q in StartCheck(self.catalog)._built(self.protocol_id)[1]:
            StartCheck(self.catalog).answer(self.protocol_id, q["question_id"], action="accept",
                                            actor_principal_id=actor or self.principal.principal_id)

    def confirm_safety(self, actor=None, target=None):
        return StartCheck(self.catalog).acknowledge_safety(
            self.protocol_id, target=target or self.target,
            actor_principal_id=actor or self.principal.principal_id)

    def skip_changes(self, actor=None):
        """Lane EASY (decision of 2026-10-24): the start passes "오늘 원문과 다르게
        할 값" -- here "바꿀 값 없이 다음" -- and names that choice."""

        from voiney_lab.start_check import RunChanges

        changes = RunChanges(self.catalog)
        return changes.choose(self.protocol_id, [], target=changes.options(self.protocol_id)["target"],
                              actor_principal_id=actor or self.principal.principal_id)["change_set_id"]

    def start_socket(self, *, enabled=True, run_change_set_id=None):
        placeholder = Path("/tmp/offline-start-gate")
        config = server.ServerConfig(placeholder, None, "test_only", frozenset({"ko", "en"}), "ko",
                                     placeholder, placeholder, placeholder)
        def catalog():
            store = initialize_protocol_store(self.settings)
            return ProtocolCatalog(store), store
        async def run():
            socket = _QueuedSocket()
            token = server._REQUEST_PRINCIPAL.set(self.principal)
            try:
                task = asyncio.create_task(server.voice_socket(socket))
                socket.control({"type": "session.start", "configuration_id": 1, "mode": "cascade",
                                "language": "ko", "protocol_id": self.protocol_id,
                                **({"run_change_set_id": run_change_set_id} if run_change_set_id else {})})
                result = await socket.wait_for(lambda e: e["type"] in {
                    "session.ready", "session.configuration_required", "error"})
                socket.disconnect()
                await task
                return result, socket.sent
            finally:
                server._REQUEST_PRINCIPAL.reset(token)
        with patch.dict("os.environ", {
            "VOINEY_LAB_START_CHECK_ENABLED": "true" if enabled else "false",
            "VOINEY_LAB_WORKSPACE_ENABLED": "false", "VOINEY_LAB_EXPERIMENT_REPORTS_ENABLED": "false",
            "VOINEY_LAB_PROTOCOL_ENABLED": "true", "VOINEY_LAB_PROTOCOL_DATA_DIR": str(self.settings.data_dir),
            "VOINEY_LAB_USAGE_SCOPE": "demo",
        }), patch.object(server, "server_config", return_value=config), \
             patch.object(server, "_open_protocol_catalog", side_effect=catalog), \
             patch.object(server, "load_curated_protocol_fixture", return_value=self.fixture), \
             patch.object(server, "_candidate_fixture_execution_state", return_value={"available_for_execution": True}), \
             patch.object(server, "_scope_catalog_resource"):
            return asyncio.run(run())

    def test_start_without_key_value_answers_is_refused(self):
        result, _ = self.start_socket()
        assert result["type"] == "session.configuration_required"
        assert result["reason"] == "start_check_required"
        assert result["message_ko"]

    def test_correct_values_without_safety_acknowledgement_are_refused(self):
        self.confirm_values()
        result, _ = self.start_socket()
        assert result["type"] == "session.configuration_required"
        assert result["reason"] == "safety_acknowledgement_required"

    def test_confirmations_for_another_account_do_not_authorize_start(self):
        self.confirm_values("other-account")
        result, _ = self.start_socket()
        assert result["type"] == "session.configuration_required"
        assert result["reason"] == "start_check_required"

    def test_the_setting_keeps_the_existing_disabled_start_path(self):
        result, _ = self.start_socket(enabled=False)
        assert result["type"] == "session.ready"

    def test_matching_answers_and_safety_record_allow_start(self):
        self.confirm_values()
        self.confirm_safety()
        # Lane EASY (decision of 2026-10-24): the start also names its "오늘 원문과 다르게 할 값" choice.
        result, _ = self.start_socket(run_change_set_id=self.skip_changes())
        assert result["type"] == "session.ready"

    def test_reanalysis_invalidates_previous_confirmation_records(self):
        self.confirm_values()
        self.confirm_safety()
        self.analyze(self.answer(pages=(1, 2, 3)), analysis_id="analysis-" + "b" * 32, reanalysis=True)
        self.fixture = self.catalog.load_executable_fixture(self.protocol_id)
        result, _ = self.start_socket()
        assert result["type"] == "session.configuration_required"
        assert result["reason"] == "start_check_required"

    def test_wrong_latest_answer_cannot_reuse_an_earlier_correct_answer(self):
        self.confirm_values()
        self.confirm_safety()
        checker = StartCheck(self.catalog)
        question = checker._built(self.protocol_id)[1][0]
        wrong = next(choice for choice in question["choices"] if choice["label_ko"] != question["value"])
        checker.answer(self.protocol_id, question["question_id"], choice_id=wrong["choice_id"],
                       actor_principal_id=self.principal.principal_id)
        result, _ = self.start_socket()
        assert result["reason"] == "start_check_required"

    def test_safety_receipt_cannot_be_reused_by_another_account(self):
        self.confirm_values("other-account")
        self.confirm_safety("other-account")
        self.confirm_values()
        result, _ = self.start_socket()
        assert result["reason"] == "safety_acknowledgement_required"

    def test_safety_requires_completed_values_and_the_current_target(self):
        from voiney_lab.start_check import StartCheckError
        checker = StartCheck(self.catalog)
        with self.assertRaises(StartCheckError):
            self.confirm_safety()
        self.confirm_values()
        with self.assertRaises(StartCheckError):
            self.confirm_safety(target={**self.target, "analysis_id": "outdated"})
        assert not checker.readiness(self.protocol_id, actor_principal_id=self.principal.principal_id)["ready"]

    def test_review_version_requires_its_own_confirmations(self):
        self.confirm_values()
        self.confirm_safety()
        self.catalog.register_review(self.protocol_id, {"reviewer_name": "Test reviewer"})
        self.fixture = self.catalog.load_executable_fixture(self.protocol_id)
        result, _ = self.start_socket()
        assert result["reason"] == "start_check_required"

    def test_recovery_reads_the_original_revision_receipt(self):
        self.confirm_values()
        self.confirm_safety()
        original = self.fixture.revision_id
        self.analyze(self.answer(pages=(1, 2, 3)), analysis_id="analysis-" + "b" * 32, reanalysis=True)
        checker = StartCheck(self.catalog)
        assert not checker.readiness(self.protocol_id, actor_principal_id=self.principal.principal_id)["ready"]
        assert checker.readiness(self.protocol_id, actor_principal_id=self.principal.principal_id,
                                 revision_id=original)["ready"]

    def test_safety_http_route_creates_a_receipt_before_socket_start(self):
        import httpx
        self.confirm_values()
        async def request():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://test") as client:
                return await client.post(f"/api/protocols/{self.protocol_id}/start-check/safety",
                                         json={"target": self.target})
        def catalog():
            store = initialize_protocol_store(self.settings)
            return ProtocolCatalog(store), store
        token = server._REQUEST_PRINCIPAL.set(self.principal)
        try:
            with patch.object(server, "_open_protocol_catalog", side_effect=catalog), \
                 patch.object(server, "_scope_catalog_resource"), \
                 patch.dict("os.environ", {"VOINEY_LAB_START_CHECK_ENABLED": "true", "VOINEY_LAB_WORKSPACE_ENABLED": "false"}):
                result = asyncio.run(request())
        finally:
            server._REQUEST_PRINCIPAL.reset(token)
        assert result.status_code == 200, result.text
        assert result.json()["ready"]
        # Lane EASY (decision of 2026-10-24): the start also names its "오늘 원문과 다르게 할 값" choice.
        result, _ = self.start_socket(run_change_set_id=self.skip_changes())
        assert result["type"] == "session.ready"

    def test_materialized_development_fixture_uses_the_same_start_gate(self):
        from tests.protocol_vocabulary_support import SOURCE_PDF, in_gel_fixture
        if not SOURCE_PDF.is_file():
            self.skipTest("requires externally licensed Candidate A source PDF")
        self.fixture = in_gel_fixture()
        self.catalog.bootstrap_development_fixture(self.fixture)
        self.protocol_id = self.fixture.protocol_id
        checker = StartCheck(self.catalog)
        self.target = checker.questions(self.protocol_id)["target"]
        assert self.target["revision_id"] == self.fixture.revision_id
        refused, _ = self.start_socket()
        assert refused["reason"] == "start_check_required"
        self.confirm_values()
        self.confirm_safety()
        # Lane EASY (decision of 2026-10-24): the start also names its "오늘 원문과 다르게 할 값" choice.
        accepted, _ = self.start_socket(run_change_set_id=self.skip_changes())
        assert accepted["type"] == "session.ready"

    def test_safety_target_types_are_validated_at_ingress(self):
        from voiney_lab.start_check import StartCheckError
        self.confirm_values()
        for bad in (True, "1", {}, []):
            with self.assertRaises(StartCheckError):
                self.confirm_safety(target={**self.target, "analysis_revision_number": bad})
