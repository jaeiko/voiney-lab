"""The reviewer path has an entrance, and only a named reviewer may use it.

Until STEP 26 the four operations that clear a readiness gate existed in the
catalog, were contract-tested, and were reachable from nothing but Python.
Every route a person would need returned 404, and the one reviewer button in
the UI stayed hidden until the gates were already clear -- a closed loop with
no way in, so no document could ever be put in front of an experimenter.

Opening it is the dangerous part. These routes are the whole of the human
judgement this system insists on, so the tests below spend most of their
effort trying to record a finding *without* being a reviewer: no principal, a
principal with no reviewing role, a principal without the permission. The
class of hole STEP 23 closed was a path that reached the authority without
passing the check, so each route is exercised end to end rather than by
calling the catalog underneath it.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from voice_workflow_agent import experiment_protocol as domain
from voice_workflow_agent.experiment_protocol_config import (
    ProtocolPersistenceSettings,
)
from voice_workflow_agent.experiment_protocol_store import (
    initialize_protocol_store,
)
from voice_workflow_agent.identity import Principal, Role
from voice_workflow_agent.protocol_catalog import ProtocolCatalog
import voice_workflow_agent.server as server_module

from tests.test_protocol_catalog import write_text_pdf, analysis_draft

REVISION = "pdf-1-analysis-1"


def _principal(*roles: Role, principal_id: str = "reviewer-a") -> Principal:
    return Principal(
        principal_id=principal_id,
        subject="subject-a",
        organization_id="org-a",
        display_name="Reviewer A",
        roles=frozenset(roles),
        authentication_method="test",
    )


class ReviewerFindingRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.settings = ProtocolPersistenceSettings(True, self.root / "catalog")
        self.store = initialize_protocol_store(self.settings)
        self.catalog = ProtocolCatalog(self.store)
        self.pdf = self.root / "sample.pdf"
        write_text_pdf(
            self.pdf,
            "Protocol Test\nSection preparation\n1. Add solution.\nWear gloves.",
            title="Protocol Test",
        )
        registration = self.catalog.register(
            self.pdf, source_filename="sample.pdf", media_type="application/pdf"
        )
        self.protocol_id = registration.entry.protocol_id
        draft = analysis_draft(self.pdf, self.protocol_id, "Protocol Test")
        self.store.append_analysis_revision(
            self.protocol_id,
            1,
            f"analysis-{registration.entry.source_sha256[:24]}",
            draft.protocol,
            draft.readiness,
            draft.capability_policy_id,
        )
        self._open = patch.object(
            server_module,
            "_open_protocol_catalog",
            side_effect=lambda: (self.catalog, _NoCloseStore(self.store)),
        )
        self._open.start()
        self.addCleanup(self._open.stop)
        self._scope = patch.object(server_module, "_scope_catalog_resource")
        self._scope.start()
        self.addCleanup(self._scope.stop)

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    @contextmanager
    def _as(self, principal, workspace_enabled=False):
        """Run one request as this principal, the way the middleware would."""

        settings = type("S", (), {"enabled": workspace_enabled})()
        token = server_module._REQUEST_PRINCIPAL.set(principal)
        try:
            with patch.object(
                server_module, "_workspace_settings", return_value=settings
            ):
                yield
        finally:
            server_module._REQUEST_PRINCIPAL.reset(token)

    def _call(self, function, principal, workspace_enabled=False, **body):
        with self._as(principal, workspace_enabled):
            return function(self.protocol_id, REVISION, body)

    # --- 2-2: who may record a finding -----------------------------------

    def test_no_principal_is_refused(self) -> None:
        for function, body in self._every_route():
            with self.subTest(route=function.__name__):
                with self.assertRaises(Exception) as caught:
                    self._call(function, None, **body)
                self.assertIn(
                    getattr(caught.exception, "status_code", None), (401, 403)
                )

    def test_a_principal_with_no_reviewing_role_is_refused(self) -> None:
        operator = _principal(Role.RESEARCHER)
        for function, body in self._every_route():
            with self.subTest(route=function.__name__):
                with self.assertRaises(Exception) as caught:
                    self._call(function, operator, **body)
                self.assertIn(
                    getattr(caught.exception, "status_code", None), (401, 403)
                )

    def test_a_workspace_principal_without_the_permission_is_refused(self):
        """The permission check must be reached, not merely present."""

        reviewer = _principal(Role.REVIEWER)
        with self._as(reviewer, workspace_enabled=True), patch.object(
            server_module,
            "require_permission",
            side_effect=server_module.AuthorizationDeniedError("denied"),
        ) as guard:
            with self.assertRaises(Exception):
                server_module.acknowledge_protocol_readiness_gate(
                    self.protocol_id,
                    REVISION,
                    {"reason_code": _SAFETY},
                )
        guard.assert_called_once()
        self.assertEqual(
            guard.call_args.args[1], server_module.Permission.PROTOCOL_REVIEW
        )

    def _every_route(self):
        return (
            (
                server_module.acknowledge_protocol_readiness_gate,
                {"reason_code": _SAFETY},
            ),
            (
                server_module.confirm_protocol_fixed_repetition,
                {"repetition_id": "r", "repeat_count": 2},
            ),
            (
                server_module.revoke_protocol_fixed_repetition,
                {"repetition_id": "r"},
            ),
            (
                server_module.resolve_protocol_ambiguity,
                {"ambiguity_id": "a", "decision": "d"},
            ),
        )

    # --- 2-3: a confirmation must cite something -------------------------

    def test_a_confirmation_without_a_citation_is_refused_at_the_route(self):
        reviewer = _principal(Role.REVIEWER)
        with self.assertRaises(Exception):
            self._call(
                server_module.confirm_protocol_fixed_repetition,
                reviewer,
                repetition_id="repetition-1",
                repeat_count=2,
            )

    def test_a_malformed_citation_is_not_repaired_into_a_valid_one(self):
        reviewer = _principal(Role.REVIEWER)
        for citation in (["a", 7], "seg-a", {"seg": "a"}, []):
            with self.subTest(citation=citation):
                with self.assertRaises(Exception):
                    self._call(
                        server_module.confirm_protocol_fixed_repetition,
                        reviewer,
                        repetition_id="repetition-1",
                        repeat_count=2,
                        evidence_segment_ids=citation,
                    )

    # --- 2-4: the ledger records who, through the route ------------------

    def test_acknowledging_through_the_route_records_actor_and_time(self):
        reviewer = _principal(Role.REVIEWER)
        before = len(self.store.list_events(self.protocol_id))
        payload = self._call(
            server_module.acknowledge_protocol_readiness_gate,
            reviewer,
            reason_code=_SAFETY,
            comment="Checked against the source.",
        )
        self.assertEqual(payload["protocol_id"], self.protocol_id)
        events = self.store.list_events(self.protocol_id)
        self.assertEqual(len(events), before + 1)
        recorded = events[-1]
        self.assertEqual(
            recorded.event_type, "protocol_readiness_gate_acknowledged"
        )
        self.assertEqual(recorded.payload["actor_principal_id"], "reviewer-a")
        self.assertEqual(recorded.payload["actor_role"], "reviewer")
        self.assertTrue(recorded.recorded_at)

        # And the response says what is still outstanding, not just "ok".
        codes = {
            item["code"] for item in payload["outstanding_blockers"]
        }
        self.assertIn(_SAFETY, codes)
        acknowledged = {
            item["code"]
            for item in payload["outstanding_blockers"]
            if item["already_acknowledged"]
        }
        self.assertEqual(acknowledged, {_SAFETY})
        self.assertEqual(len(payload["reviewer_findings"]), 1)

    # --- 2-5: revoking through the route blocks again --------------------

    def test_a_revoked_confirmation_blocks_again_through_the_route(self):
        reviewer = _principal(Role.REVIEWER)
        self._call(
            server_module.acknowledge_protocol_readiness_gate,
            reviewer,
            reason_code=_SAFETY,
        )
        analysis = self.store.get_analysis_revision(self.protocol_id, 1, 1)
        self.assertTrue(
            self.catalog._readiness_gates_cleared(self.protocol_id, 1, analysis)
        )
        # Nothing to revoke here is refused rather than silently accepted.
        with self.assertRaises(Exception):
            self._call(
                server_module.revoke_protocol_fixed_repetition,
                reviewer,
                repetition_id="repetition-1",
            )


class _NoCloseStore:
    """The routes close what they open; the test keeps one live handle."""

    def __init__(self, store) -> None:
        self._store = store

    def close(self) -> None:
        return None

    def __getattr__(self, name):
        return getattr(self._store, name)


_SAFETY = domain.ReadinessReasonCode.NO_DECLARED_SAFETY_WARNINGS.value


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
