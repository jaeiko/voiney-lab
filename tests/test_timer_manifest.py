"""Step timers come from the source, and cannot leak between documents.

The duration used to come from a dict keyed by step index and hardcoded in the
session module. Nothing tied it to a document, a page or a segment, so it
applied to whatever protocol sat at those positions -- and the previous step's
work changed how many steps a protocol has, which widened the hazard.

Every entry now names a step id, cites the page and the canonical segment that
holds the literal, and is bound to the document and fixture digests. One
unverifiable entry refuses the whole manifest, and no manifest means no timers.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from voice_workflow_agent.curated_protocol import (
    CuratedProtocolFixtureError,
    CuratedProtocolSession,
    _canonical_json_bytes,
    _timer_literal_seconds,
    load_curated_protocol_fixture,
)

DEV = Path("data/development_protocols")
FIXTURE = DEV / "candidate_a_curated_analysis.json"
PROVENANCE = DEV / "candidate_a_curated_analysis.provenance.json"
VISUALS = DEV / "candidate_a_curated_analysis.visuals.json"
TIMERS = DEV / "candidate_a_curated_analysis.timers.json"
IN_GEL = Path("data/runtime/candidate-a-source/in-gel-digestion.pdf")
OTHERS = {
    "ANKOM": Path(
        "data/runtime/candidate-a-live-acceptance/objects/sha256/53"
        "/5367ca6bfae9fe9bbaeac9dab2099276a9c2dccf6c698ee36e59c7552e56d18a.pdf"
    ),
    "headspace": Path("usingdynamicheadspacecollections.pdf"),
    "intracellular": Path("intracellularmetaboliteextraction.pdf"),
}


def _load(root: Path):
    return load_curated_protocol_fixture(
        (root / FIXTURE.name).resolve(),
        (root / PROVENANCE.name).resolve(),
        IN_GEL.resolve(),
    )


def _copy(root: Path, *, timers: bool = True) -> None:
    for path in (FIXTURE, PROVENANCE, VISUALS):
        shutil.copy(path, root / path.name)
    if timers and TIMERS.exists():
        shutil.copy(TIMERS, root / TIMERS.name)


class LiteralReadingTests(unittest.TestCase):
    """Reading a printed value is allowed; inventing one is not."""

    def test_the_two_forms_the_source_uses(self) -> None:
        self.assertEqual(_timer_literal_seconds("00:15:00"), 900)
        self.assertEqual(_timer_literal_seconds("01:00:00"), 3600)
        self.assertEqual(_timer_literal_seconds("16:00:00"), 57600)
        self.assertEqual(_timer_literal_seconds("15min"), 900)
        self.assertEqual(_timer_literal_seconds("10 min"), 600)
        self.assertEqual(_timer_literal_seconds("2h"), 7200)

    def test_anything_else_is_refused_rather_than_guessed(self) -> None:
        for literal in ("overnight", "a while", "15", "min", "", "1:2:3:4"):
            with self.subTest(literal=literal):
                self.assertIsNone(_timer_literal_seconds(literal))


class TheTimerSurvivesTheChangeTests(unittest.TestCase):
    """Task 1-6: the UI behaviour is unchanged."""

    @classmethod
    def setUpClass(cls) -> None:
        for path in (FIXTURE, PROVENANCE, IN_GEL):
            if not path.exists():
                raise unittest.SkipTest(f"{path} is not present.")
        if not TIMERS.exists():
            raise unittest.SkipTest(f"{TIMERS} has not been derived.")

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)
        _copy(self.root)
        self.fixture = _load(self.root)

    def test_all_ten_timers_load(self) -> None:
        self.assertEqual(len(self.fixture.timer_manifest), 10)

    def test_the_same_steps_have_timers_as_before(self) -> None:
        """The step labels the hardcoded table gave a duration to."""

        labelled = {
            self.fixture.steps[index].source_label
            for index in range(len(self.fixture.steps))
            if self.fixture.steps[index].step_id in self.fixture.timer_manifest
        }
        self.assertEqual(
            labelled,
            {"3", "5", "8", "12", "16", "17", "19", "22", "23", "24"},
        )

    def test_the_same_durations_as_before(self) -> None:
        by_label = {
            step.source_label: self.fixture.timer_manifest.get(step.step_id, 0)
            for step in self.fixture.steps
        }
        self.assertEqual(
            {label: by_label[label] for label in sorted(by_label) if by_label[label]},
            {
                "12": 3600, "16": 2700, "17": 600, "19": 900, "22": 600,
                "23": 57600, "24": 1800, "3": 900, "5": 900, "8": 900,
            },
        )

    def test_starting_a_timer_still_works(self) -> None:
        session = CuratedProtocolSession(self.fixture)
        session.active = True
        session.current_index = 2  # step 3
        started, duration, _ = session.start_timer()
        self.assertTrue(started)
        self.assertEqual(duration, 900)

    def test_a_step_without_one_still_reports_none(self) -> None:
        session = CuratedProtocolSession(self.fixture)
        session.active = True
        session.current_index = 0  # step 1
        started, duration, message = session.start_timer()
        self.assertFalse(started)
        self.assertEqual(duration, 0)
        self.assertIn("No timer", message)

    def test_the_status_projection_reports_the_verified_duration(self) -> None:
        session = CuratedProtocolSession(self.fixture)
        session.active = True
        session.current_index = 11  # step 12
        status = session.timer_status()
        self.assertEqual(status["state"], "not_started")
        self.assertEqual(status["duration_seconds"], 3600)


class NoTimerLeaksToAnotherDocumentTests(unittest.TestCase):
    """Task 1-7. The manifest is keyed by step id and bound to two digests."""

    def test_a_fixture_with_no_manifest_has_no_timers(self) -> None:
        for path in (FIXTURE, PROVENANCE, IN_GEL):
            if not path.exists():
                self.skipTest(f"{path} is not present.")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _copy(root, timers=False)
            fixture = _load(root)
            self.assertEqual(fixture.timer_manifest, {})
            session = CuratedProtocolSession(fixture)
            session.active = True
            for index in range(len(fixture.steps)):
                session.current_index = index
                with self.subTest(step=index):
                    self.assertEqual(session.timer_seconds_for_step(index), 0)

    def test_the_manifest_is_refused_against_a_different_document(self) -> None:
        """A digest mismatch is a load failure, not a silent skip."""

        if not TIMERS.exists() or not IN_GEL.exists():
            self.skipTest("in-gel sources are not present.")
        for name, path in OTHERS.items():
            if not path.exists():
                continue
            with self.subTest(document=name):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    _copy(root)
                    with self.assertRaises(Exception):
                        load_curated_protocol_fixture(
                            (root / FIXTURE.name).resolve(),
                            (root / PROVENANCE.name).resolve(),
                            path.resolve(),
                        )

    def test_no_other_document_has_a_timer_manifest(self) -> None:
        self.assertEqual(
            sorted(p.name for p in DEV.glob("*timers*")),
            ["candidate_a_curated_analysis.timers.json"],
        )


class OneBadEntryRefusesTheWholeManifestTests(unittest.TestCase):
    """Fail closed: a timer nobody can point to does not exist."""

    def setUp(self) -> None:
        for path in (FIXTURE, PROVENANCE, IN_GEL, TIMERS):
            if not path.exists():
                self.skipTest(f"{path} is not present.")
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)
        _copy(self.root)
        self.path = self.root / TIMERS.name

    def _rewrite(self, mutate) -> None:
        manifest = json.loads(self.path.read_text())
        mutate(manifest)
        self.path.write_bytes(_canonical_json_bytes(manifest))

    def _refused(self) -> None:
        with self.assertRaises(CuratedProtocolFixtureError):
            _load(self.root)

    def test_a_duration_that_the_literal_does_not_state(self) -> None:
        def mutate(manifest):
            manifest["candidates"][0]["duration_seconds"] = 999
        self._rewrite(mutate)
        self._refused()

    def test_a_literal_the_cited_segment_does_not_contain(self) -> None:
        def mutate(manifest):
            manifest["candidates"][0]["source_literal"] = "00:07:00"
            manifest["candidates"][0]["duration_seconds"] = 420
        self._rewrite(mutate)
        self._refused()

    def test_an_evidence_handle_that_does_not_resolve(self) -> None:
        def mutate(manifest):
            manifest["candidates"][0]["evidence_segment_ids"] = ["seg-nope"]
        self._rewrite(mutate)
        self._refused()

    def test_a_page_outside_the_step(self) -> None:
        def mutate(manifest):
            manifest["candidates"][0]["page_number"] = 1
        self._rewrite(mutate)
        self._refused()

    def test_a_misstated_anchor_page(self) -> None:
        def mutate(manifest):
            manifest["candidates"][0]["step_anchor_page"] = 1
        self._rewrite(mutate)
        self._refused()

    def test_an_unknown_step(self) -> None:
        def mutate(manifest):
            manifest["candidates"][0]["linked_step_id"] = "candidate-a-step-99"
        self._rewrite(mutate)
        self._refused()

    def test_an_unverified_entry(self) -> None:
        def mutate(manifest):
            manifest["candidates"][0]["confidence"] = "ambiguous"
        self._rewrite(mutate)
        self._refused()

    def test_a_duplicated_step(self) -> None:
        def mutate(manifest):
            manifest["candidates"].append(dict(manifest["candidates"][0]))
        self._rewrite(mutate)
        self._refused()

    def test_a_wrong_fixture_digest(self) -> None:
        def mutate(manifest):
            manifest["fixture_sha256"] = "0" * 64
        self._rewrite(mutate)
        self._refused()

    def test_a_byte_of_drift_in_the_committed_file(self) -> None:
        self.path.write_text(
            self.path.read_text().replace('"version":1', '"version": 1')
        )
        self._refused()


if __name__ == "__main__":
    unittest.main()
