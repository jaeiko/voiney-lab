"""Values the screen needs from the server (lane R6, decision 5).

Lane U (PR #34) section 7 asked for them, and the screen code is left for
lane U2:

* the experiment record state and each /api/workspace/experiments item carry
  ``protocol_title`` and ``day_sequence`` (the Nth started that UTC day,
  ``day_sequence_date``);
* an approval record carries ``actor_display_name`` where one was recorded;
* a finding refused for its content says why, with 400 or 422 -- only a real
  permission refusal is 403;
* a turn whose playback was cut by a barge-in candidate still gets its end
  state: "complete" when the candidate is rejected, "cancelled" when the
  next turn is committed.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tests import test_ambiguity_resolution as _ambiguity
from voiney_lab.experiment_reports import ExperimentReportStore
from voiney_lab.identity import Principal, Role
from voiney_lab.protocol_catalog import (
    AMBIGUITY_SINGLE_AUTHORITATIVE,
    ProtocolApprovalError,
    ProtocolFindingRejectedError,
    SharedSecretApprovalPolicy,
)
from voiney_lab.server import (
    ListenerSession,
    _catalog_http_error,
    _public_experiment_report_state,
    _send_playback_terminal,
    cancel_cascade_generation,
)
from voiney_lab.vad import TurnState
from voiney_lab.workspace_store import WorkspaceSettings, initialize_workspace_store

SHA = "a" * 64


class ExperimentRecordNameTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.store = ExperimentReportStore(Path(self._temp.name) / "reports.sqlite")

    def _open(self, session_id: str) -> dict:
        return self.store.open_report(
            session_id=session_id, protocol_id="in-gel", protocol_title="In-gel digestion",
            protocol_revision="rev-1", protocol_sha256=SHA,
            readiness_status="guidance_ready", development_only=True,
        )

    def test_the_record_state_names_its_protocol_and_its_place_that_day(self) -> None:
        first = self._open("session-1")
        second = self._open("session-2")
        public = _public_experiment_report_state(second)
        self.assertEqual(public["protocol_title"], "In-gel digestion")
        self.assertEqual(public["day_sequence"], 2)
        self.assertEqual(public["day_sequence_date"], second["started_at"][:10])
        self.assertEqual(public["timezone"], "UTC")
        self.assertEqual(_public_experiment_report_state(first)["day_sequence"], 1)
        listed = {item["session_id"]: item for item in self.store.list_reports()}
        self.assertEqual(listed["session-2"]["day_sequence"], 2)
        self.assertEqual(listed["session-1"]["protocol_title"], "In-gel digestion")


def _principal(name: str, role: Role = Role.RESEARCHER) -> Principal:
    return Principal(
        principal_id=f"principal-{name}", subject=f"test:{name}",
        organization_id="tenant-a", display_name=f"Dr. {name}",
        roles=frozenset({role}), authentication_method="test",
    )


class ExperimentListNameTests(unittest.TestCase):
    def test_each_item_names_its_protocol_and_its_place_that_day(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = initialize_workspace_store(WorkspaceSettings(True, Path(temp)))
            first, second = _principal("a"), _principal("b")
            reviewer = _principal("c", Role.REVIEWER)
            for principal in (first, second, reviewer):
                store.bootstrap_principal(principal)
            store.start_experiment(
                first, session_id="experiment-1", protocol_id="in-gel",
                protocol_revision_id="rev-1")
            store.start_experiment(
                second, session_id="experiment-2", protocol_id="in-gel",
                protocol_revision_id="rev-1")
            items = {
                item["session_id"]: item for item in store.list_experiments(reviewer)
            }
            store.close()
        self.assertEqual(items["experiment-1"]["day_sequence"], 1)
        self.assertEqual(items["experiment-2"]["day_sequence"], 2)
        self.assertEqual(
            items["experiment-2"]["day_sequence_date"],
            items["experiment-2"]["started_at"][:10],
        )
        # Not a workspace revision: the server fills the title from the catalog.
        self.assertIn("protocol_title", items["experiment-1"])
        self.assertIsNone(items["experiment-1"]["protocol_title"])


class FindingRejectionTests(unittest.TestCase):
    def setUp(self) -> None:
        # The ambiguity tests' own catalog, one revision with one ambiguity.
        _ambiguity.AmbiguityResolutionTests.setUp(self)

    _protocol = _ambiguity.AmbiguityResolutionTests._protocol
    _resolve = _ambiguity.AmbiguityResolutionTests._resolve

    def _refused(self, **call) -> ProtocolFindingRejectedError:
        with self.assertRaises(ProtocolFindingRejectedError) as raised:
            self._resolve(**call)
        # Still an approval error to every caller that catches one.
        self.assertIsInstance(raised.exception, ProtocolApprovalError)
        return raised.exception

    def test_each_content_refusal_has_its_own_code_and_no_403(self) -> None:
        for call, code, status in (
            ({"decision": AMBIGUITY_SINGLE_AUTHORITATIVE, "evidence_segment_ids": ()},
             "finding_evidence_missing", 422),
            ({"decision": AMBIGUITY_SINGLE_AUTHORITATIVE,
              "evidence_segment_ids": ("s-not-a-real-handle",)},
             "finding_evidence_span_mismatch", 422),
            ({"decision": AMBIGUITY_SINGLE_AUTHORITATIVE, "ambiguity_id": "ambiguity-404"},
             "ambiguity_not_found", 422),
            ({"decision": "looks_the_same_to_me"}, "finding_unsupported", 400),
        ):
            with self.subTest(code=code):
                error = self._refused(**call)
                self.assertEqual(error.code, code)
                http = _catalog_http_error(error)
                self.assertEqual((http.status_code, http.detail), (status, code))

    def test_a_missing_analysis_revision_is_its_own_code(self) -> None:
        self.revision_id = "pdf-1"
        error = self._refused(decision=AMBIGUITY_SINGLE_AUTHORITATIVE)
        self.assertEqual(error.code, "analysis_revision_missing")
        self.assertEqual(_catalog_http_error(error).status_code, 422)

    def test_an_authorization_failure_is_still_403(self) -> None:
        http = _catalog_http_error(ProtocolApprovalError("Protocol approval authorization failed."))
        self.assertEqual((http.status_code, http.detail), (403, "protocol_approval_denied"))

    def test_an_approval_names_its_approver_when_one_was_recorded(self) -> None:
        self._resolve(decision=AMBIGUITY_SINGLE_AUTHORITATIVE)
        self.catalog.approve(
            self.protocol_id, self.revision_id,
            policy=SharedSecretApprovalPolicy("secret"), presented_secret="secret",
            actor_principal_id="reviewer@example.org", actor_role="reviewer",
            actor_display_name="  Dr.  Kim  ",
        )
        approval = self.catalog.approval_context(self.protocol_id)
        self.assertEqual(approval["actor_display_name"], "Dr. Kim")
        self.assertEqual(approval["actor_principal_id"], "reviewer@example.org")


class Socket:
    def __init__(self) -> None:
        self.text: list[dict] = []

    async def send_text(self, value: str) -> None:
        self.text.append(json.loads(value))


class InterruptedPlaybackEndStateTests(unittest.TestCase):
    def _playing(self) -> ListenerSession:
        session = ListenerSession()
        session.start()
        session.active_turn_id = 1
        session.turn_generations[1] = session.generation
        session.detector.state = TurnState.PROCESSING
        self.assertTrue(session.start_playback(1))
        for state in ("listening", "transcribing", "routing", "synthesizing", "playing"):
            self.assertIsNotNone(session.advance_turn_progress(1, session.generation, state))
        return session

    def test_a_rejected_candidate_still_ends_the_turn_complete(self) -> None:
        session = self._playing()
        generation = session.generation
        session._interrupt_candidate_identity = (1, generation)
        self.assertFalse(session.playback_ended(1))
        # The candidate is rejected; the playback.ended it held back is taken.
        session._reset_interrupt_input()
        self.assertTrue(session.resume_deferred_playback_end(1, generation))
        self.assertFalse(session.resume_deferred_playback_end(1, generation))
        socket = Socket()
        asyncio.run(_send_playback_terminal(socket, session, 1, cooldown_ms=0))
        state, changed = socket.text
        self.assertEqual((state["type"], state["state"]), ("turn.state", "complete"))
        self.assertEqual(changed["type"], "state.changed")

    def test_a_committed_next_turn_ends_the_old_turn_cancelled(self) -> None:
        session = self._playing()
        socket = Socket()
        interruption = SimpleNamespace(
            kind="assistant.interrupted", turn_id=1, generation=session.generation,
            superseding_turn_id=2, superseding_generation=session.generation + 1,
            reason="confirmed_speech", latency_ms=None,
        )
        asyncio.run(cancel_cascade_generation(socket, session, None, interruption))
        kinds = [(item["type"], item.get("state")) for item in socket.text]
        self.assertEqual(
            kinds, [("cascade.playback.clear", "cancelled"), ("turn.state", "cancelled")],
        )


if __name__ == "__main__":
    unittest.main()
