"""The numbered-action trigger, and the count that keeps it honest."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from voice_workflow_agent.experiment_protocol_pdf import extract_protocol_pdf
from voice_workflow_agent.protocol_claim_analysis import (
    _numbered_step_labels,
    mid_line_numbered_labels,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

ANKOM = Path(
    "data/runtime/candidate-a-live-acceptance/objects/sha256/53"
    "/5367ca6bfae9fe9bbaeac9dab2099276a9c2dccf6c698ee36e59c7552e56d18a.pdf"
)
INTRACELLULAR = Path("intracellularmetaboliteextraction.pdf")
INTRACELLULAR_SHA256 = (
    "997d020c11ba915621b9705de9c4a92330f843c8feff4e2d1099dca763fdb9f0"
)


def _intracellular():
    if not INTRACELLULAR.is_file():
        raise unittest.SkipTest(
            f"{INTRACELLULAR.name} is not present in the working tree; it is a "
            f"24 MB source that is deliberately not committed. Expected "
            f"sha256 {INTRACELLULAR_SHA256}."
        )
    extraction = extract_protocol_pdf(INTRACELLULAR)
    if extraction.sha256 != INTRACELLULAR_SHA256:
        raise unittest.SkipTest(
            f"{INTRACELLULAR.name} is present but is a different file: "
            f"sha256 {extraction.sha256}."
        )
    return extraction


class LineAnchoredTriggerTests(unittest.TestCase):
    """A numbered step begins its own line.

    A number in the middle of a sentence is a cross-reference or a citation,
    not an instruction, and treating it as one demanded an action claim for a
    figure caption.
    """

    def test_a_label_beginning_a_line_is_a_step(self) -> None:
        self.assertEqual(
            _numbered_step_labels("11 Add 50 ml of tap water.\n"), ("11",)
        )

    def test_a_caption_label_is_not_a_step(self) -> None:
        self.assertEqual(
            _numbered_step_labels("Figure 18. Example of incorrect peaks.\n"),
            (),
        )

    def test_a_mid_sentence_cross_reference_is_not_a_step(self) -> None:
        self.assertEqual(
            _numbered_step_labels("of your choice, as shown in Figure 22.\n"),
            (),
        )

    def test_a_reference_volume_and_page_is_not_a_step(self) -> None:
        self.assertEqual(
            _numbered_step_labels("Biofuels and Bioproducts, 17(1), 146.\n"),
            (),
        )

    def test_a_section_number_beginning_a_line_still_counts(self) -> None:
        """Honest about what the narrowing does not fix.

        These survive, and they are the remaining false triggers on a
        near-unnumbered document.
        """

        self.assertEqual(
            _numbered_step_labels("1. Automated Peak Detection\n"), ("1",)
        )


class DroppedLabelCountTests(unittest.TestCase):
    """A trigger that silently drops candidates is a blocklist.

    The count has to be available per page, or nobody can tell how much a
    document depends on the narrowing.
    """

    def test_a_dropped_label_is_counted(self) -> None:
        self.assertEqual(
            mid_line_numbered_labels("Figure 18. Example of bad peaks.\n"),
            ("18",),
        )

    def test_an_anchored_label_is_not_counted_as_dropped(self) -> None:
        self.assertEqual(
            mid_line_numbered_labels("18. Wash the pellet twice.\n"), ()
        )

    def test_a_number_followed_by_a_unit_was_never_a_candidate(self) -> None:
        self.assertEqual(mid_line_numbered_labels("volume 5. mL added\n"), ())


class LocalSourceTriggerTests(unittest.TestCase):
    def test_the_properly_numbered_source_loses_nothing(self) -> None:
        """ANKOM: every label already began its own line."""

        extraction = extract_protocol_pdf(ANKOM)
        self.assertEqual(
            sum(len(mid_line_numbered_labels(p.text)) for p in extraction.pages),
            0,
        )
        self.assertEqual(
            sum(len(_numbered_step_labels(p.text)) for p in extraction.pages),
            67,
        )

    def test_the_guarantee_still_fires_where_it_caught_a_real_miss(self) -> None:
        """Page 32 is where the whole-document run caught a genuine omission."""

        extraction = extract_protocol_pdf(ANKOM)
        self.assertTrue(_numbered_step_labels(extraction.pages[31].text))

    def test_the_near_unnumbered_source_drops_every_false_trigger(self) -> None:
        extraction = _intracellular()
        dropped = sum(
            len(mid_line_numbered_labels(p.text)) for p in extraction.pages
        )
        self.assertEqual(dropped, 24)

    def test_what_the_narrowing_leaves_behind(self) -> None:
        """Nine line-anchored labels remain, and none is an execution step."""

        extraction = _intracellular()
        remaining = {
            p.source_page_number: _numbered_step_labels(p.text)
            for p in extraction.pages
            if _numbered_step_labels(p.text)
        }
        self.assertEqual(
            remaining,
            {
                4: ("1", "2", "3"),
                13: ("4",),
                18: ("1",),
                19: ("2",),
                30: ("5", "1", "2"),
            },
        )


class FixtureScopeTests(unittest.TestCase):
    """The offline scorer must not manufacture execution steps.

    It asserts an ACTION claim for every numbered line it matches, so it
    satisfies the numbered-action obligation by construction and cannot
    measure it. On a document whose numbered lines are section headings or a
    table of contents it invents steps that a real model does not, and
    validation cannot tell the two apart.
    """

    def test_a_properly_numbered_source_is_in_scope(self) -> None:
        from prototype_claim_chunks import fixture_scope

        scope = fixture_scope(extract_protocol_pdf(ANKOM))
        self.assertTrue(scope["in_scope"])
        self.assertEqual(scope["duplicate_labels"], 0)
        self.assertEqual(scope["descents"], [])

    def test_a_near_unnumbered_source_is_out_of_scope(self) -> None:
        from prototype_claim_chunks import fixture_scope

        scope = fixture_scope(_intracellular())
        self.assertFalse(scope["in_scope"])
        self.assertEqual(scope["duplicate_labels"], 7)
        self.assertEqual(len(scope["descents"]), 4)

    def test_out_of_scope_yields_no_score_at_all(self) -> None:
        """Not a score with a caveat: a number gets quoted without its note."""

        from prototype_claim_chunks import run_source

        result = run_source(INTRACELLULAR, 1) if INTRACELLULAR.is_file() else None
        if result is None:
            self.skipTest(
                f"{INTRACELLULAR.name} is not present; expected sha256 "
                f"{INTRACELLULAR_SHA256}."
            )
        self.assertFalse(result["scored"])
        self.assertEqual(result["status"], "fixture out of scope")
        for key in ("action_count", "marker_count", "chunk_count"):
            self.assertNotIn(key, result)


class FixtureScopeKnownLimitationTests(unittest.TestCase):
    """The hole in the scope check, kept as a failing-shape record.

    Monotonicity catches interleaving. It cannot catch a single clean ascending
    run that is not a procedure, so a document whose steps are all prose and
    whose reference list is numbered 1. 2. 3. is scored, and the fake execution
    steps built from the bibliography go into the score.

    This is asserted as the *current* behaviour, not as desired behaviour. It
    is here so the limitation cannot be forgotten, and so that a future rule
    which closes it makes this test fail loudly rather than passing silently.
    """

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)

    def test_a_prose_body_with_numbered_references_is_wrongly_in_scope(self):
        from prototype_claim_chunks import fixture_scope
        from tests.test_protocol_claim_analysis import write_lined_pages

        path = self.root / "prose-with-references.pdf"
        write_lined_pages(
            path,
            (
                ("Protocol Prose", "Abstract", "This protocol is described in"),
                (
                    "We resuspend the pellet in cold solvent and vortex until",
                    "no visible particles remain, then transfer the supernatant",
                    "to a clean tube and dry it under nitrogen.",
                ),
                (
                    "References",
                    "1. Ayotte P and Laliberte C. Analytical chemistry review.",
                    "2. Bennett B D and Rabinowitz J D. Metabolite profiling.",
                    "3. Sharma B D and Hon S. Fermentation titers and yields.",
                ),
            ),
        )
        scope = fixture_scope(extract_protocol_pdf(path))
        self.assertEqual(scope["duplicate_labels"], 0)
        self.assertEqual(scope["descents"], [])
        # Wrongly in scope: these three labels are a bibliography.
        self.assertTrue(scope["in_scope"])
        self.assertEqual(scope["fixture_action_labels"], 3)

    def test_the_four_local_sources_do_not_show_that_shape(self):
        """Labels are spread through the body, not confined to the tail."""

        extraction = extract_protocol_pdf(ANKOM)
        from prototype_claim_chunks import fixture_step_labels

        pages = {page for page, _ in fixture_step_labels(extraction)}
        tail_start = extraction.page_count - extraction.page_count // 3 + 1
        self.assertLess(min(pages), tail_start)
        self.assertGreater(len(pages), extraction.page_count // 2)


if __name__ == "__main__":
    unittest.main()
