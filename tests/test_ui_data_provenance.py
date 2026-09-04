"""Where the working UI behaviour actually comes from, pinned.

Two things worked when a person ran the in-gel protocol through the UI: a
timer on the steps that have one, and a photograph from the PDF appearing at
step 7. These tests fix the source of each, because the answer was not what it
looked like and each source has a different standing.

Structure and images come from hand-built files. The timer does not: it comes
from a table hardcoded in the session module, keyed by step *index*, with no
link to source evidence. That is recorded here as current behaviour, not
endorsed -- it is document-specific data in code, which this project's own
constraints forbid.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

FIXTURE = Path("data/development_protocols/candidate_a_curated_analysis.json")
VISUALS = Path("data/development_protocols/candidate_a_curated_analysis.visuals.json")
LAUNCHER = Path("scripts/run_candidate_a.sh")


class TheUiIsServedTheHandBuiltFixtureTests(unittest.TestCase):
    def test_the_launcher_points_the_server_at_the_hand_built_file(self) -> None:
        script = LAUNCHER.read_text()
        self.assertIn("VOICE_WORKFLOW_AGENT_CURATED_PROTOCOL_FIXTURE", script)
        self.assertIn("candidate_a_curated_analysis.json", script)

    def test_that_branch_is_consulted_before_the_catalog(self) -> None:
        """Why the provider path could not have supplied it.

        The session selection tries the configured curated fixture first and
        only falls through to the protocol catalog if its protocol_id does not
        match the request.
        """

        server = Path("src/voice_workflow_agent/server.py").read_text()
        block_start = server.index("selected_curated_fixture=None")
        block = server[block_start : server.index(
            "session.set_curated_protocol_fixture(", block_start
        )]
        curated_at = block.index(
            "trusted_config.curated_protocol_fixture_path is not None"
        )
        catalog_at = block.index("catalog.load_executable_fixture(")
        self.assertLess(curated_at, catalog_at)
        # And the catalog is only reached when the curated match failed.
        self.assertIn("if selected_curated_fixture is None:", block)

    def test_the_fixture_is_the_protocol_the_ui_names(self) -> None:
        payload = json.loads(FIXTURE.read_text())
        self.assertEqual(
            payload["protocol"]["protocol_id"],
            "candidate-a-curated-development-v1",
        )
        steps = [
            step
            for section in payload["protocol"]["sections"]
            for step in section["steps"]
        ]
        self.assertEqual(len(steps), 25)


class TheTimerIsHardcodedNotExtractedTests(unittest.TestCase):
    """Recorded as-is. The fixture carries no timer for the session to read."""

    def test_the_hand_built_fixture_declares_no_process_timer(self) -> None:
        raw = FIXTURE.read_text()
        self.assertNotIn("process_timer", raw)

    def test_the_duration_comes_from_a_table_keyed_by_step_index(self) -> None:
        from voice_workflow_agent.curated_protocol import (
            _CANDIDATE_A_STEP_TIMERS,
        )

        self.assertEqual(sorted(_CANDIDATE_A_STEP_TIMERS), list(range(25)))
        self.assertEqual(
            sorted(
                index
                for index, seconds in _CANDIDATE_A_STEP_TIMERS.items()
                if seconds
            ),
            [2, 4, 7, 11, 15, 16, 18, 21, 22, 23],
        )

    def test_the_table_is_not_bound_to_any_source_evidence(self) -> None:
        """The gap this pins: an index, not a page or a segment.

        A table keyed by position applies to whatever protocol is loaded at
        that index, and nothing ties its numbers to text in a document.
        """

        from voice_workflow_agent.curated_protocol import (
            _CANDIDATE_A_STEP_TIMERS,
        )

        for index, seconds in _CANDIDATE_A_STEP_TIMERS.items():
            with self.subTest(index=index):
                self.assertIsInstance(index, int)
                self.assertIsInstance(seconds, int)


class TheImageStepLinkTests(unittest.TestCase):
    """The link key, and what it is verified against."""

    def setUp(self) -> None:
        if not VISUALS.exists():
            self.skipTest(f"{VISUALS} is not present.")
        self.manifest = json.loads(VISUALS.read_text())

    def test_the_manifest_is_bound_to_both_the_pdf_and_the_fixture(self) -> None:
        self.assertEqual(self.manifest["version"], 1)
        for key in ("document_sha256", "fixture_sha256", "status"):
            self.assertIn(key, self.manifest)

    def test_the_link_key_is_the_step_id(self) -> None:
        selected = [c for c in self.manifest["candidates"] if c["selected"]]
        self.assertEqual(
            [c["linked_step_id"] for c in selected],
            ["candidate-a-step-07", "candidate-a-step-09"],
        )

    def test_the_page_is_cross_checked_against_the_step_evidence(self) -> None:
        """Task 4-2: the link is bound to source evidence, not free-floating."""

        payload = json.loads(FIXTURE.read_text())
        pages = {
            step["step_id"]: step["evidence"]["source_page_number"]
            for section in payload["protocol"]["sections"]
            for step in section["steps"]
        }
        for candidate in self.manifest["candidates"]:
            if not candidate["selected"]:
                continue
            with self.subTest(step=candidate["linked_step_id"]):
                self.assertEqual(
                    candidate["page_number"],
                    pages[candidate["linked_step_id"]],
                )

    def test_the_image_bytes_are_hash_verified(self) -> None:
        for candidate in self.manifest["candidates"]:
            with self.subTest(step=candidate["linked_step_id"]):
                self.assertRegex(candidate["source_region_hash"], r"^[0-9a-f]{64}$")
                self.assertRegex(candidate["object_name"], r"^/X[1-9][0-9]*$")

    def test_the_loader_refuses_a_page_that_disagrees(self) -> None:
        """The cross-check is enforced, not merely present in the data."""

        loader = Path("src/voice_workflow_agent/curated_protocol.py").read_text()
        self.assertIn(
            'item["page_number"] == step.evidence.source_page_number', loader
        )

    def test_only_selected_candidates_are_served(self) -> None:
        loader = Path("src/voice_workflow_agent/curated_protocol.py").read_text()
        self.assertIn('candidate.get("selected") is True', loader)

    def test_no_other_document_has_a_manifest(self) -> None:
        """Task 4-3: measured, the feature is in-gel only.

        Embedded images per source: in-gel 18, ANKOM 97, headspace 13,
        intracellular 54. Step-linked: 2, 0, 0, 0. The link needs a hand-built
        manifest and only in-gel has one.
        """

        manifests = sorted(
            path.name
            for path in Path("data/development_protocols").glob("*visuals*")
        )
        self.assertEqual(
            manifests, ["candidate_a_curated_analysis.visuals.json"]
        )


class CompletionIsPositionalOnlyTests(unittest.TestCase):
    """Task 3: the model cannot judge completion, and does not.

    A semantic proposal to complete a step maps to an explicit confirmation
    request, not to a transition, so the semantic path holds no mutation
    authority. And the only basis for calling a step final is its position.
    """

    def test_completing_a_step_grants_no_mutation(self) -> None:
        from voice_workflow_agent.curated_protocol import (
            _SEMANTIC_INTENT_PROJECTION,
            SemanticIntent,
        )

        projection = _SEMANTIC_INTENT_PROJECTION[
            SemanticIntent.COMPLETE_CURRENT_STEP
        ]
        self.assertIs(projection["allows_state_mutation"], False)
        self.assertIs(projection["requires_confirmation"], True)
        self.assertEqual(
            projection["requested_followup"], "confirm_current_step_completion"
        )

    def test_only_resuming_may_mutate_from_a_semantic_proposal(self) -> None:
        from voice_workflow_agent.curated_protocol import (
            _SEMANTIC_INTENT_PROJECTION,
            SemanticIntent,
        )

        mutating = {
            intent
            for intent, projection in _SEMANTIC_INTENT_PROJECTION.items()
            if projection.get("allows_state_mutation")
        }
        self.assertEqual(mutating, {SemanticIntent.RESUME})

    def test_final_step_is_a_position_not_a_judgement(self) -> None:
        session = Path("src/voice_workflow_agent/curated_protocol.py").read_text()
        self.assertIn("self.current_index == len(steps) - 1", session)
        self.assertIn(
            "self.current_index == len(self.fixture.steps) - 1", session
        )


if __name__ == "__main__":
    unittest.main()
