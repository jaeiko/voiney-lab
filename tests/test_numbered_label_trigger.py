"""The numbered-action trigger that cuts the page evidence segments."""

from __future__ import annotations

import unittest
from pathlib import Path

from voiney_lab.experiment_protocol_pdf import extract_protocol_pdf
from voiney_lab.protocol_claim_analysis import _numbered_action_matches


def _numbered_step_labels(page_text: str) -> tuple[str, ...]:
    """The step labels the trigger reads on a page, each once, in order.

    The body of the helper of this name that went with the claim-chunk path
    (lane AN, 2026-10-13); the trigger it reads still cuts the segments.
    """

    return tuple(
        dict.fromkeys(
            match.group("label") for match in _numbered_action_matches(page_text)
        )
    )

ANKOM = Path(
    "data/runtime/candidate-a-live-acceptance/objects/sha256/53"
    "/5367ca6bfae9fe9bbaeac9dab2099276a9c2dccf6c698ee36e59c7552e56d18a.pdf"
)
INTRACELLULAR = Path("intracellularmetaboliteextraction.pdf")
HEADSPACE = Path("usingdynamicheadspacecollections.pdf")
CANDIDATE_A = Path(
    "data/runtime/candidate-a-source/in-gel-digestion.pdf"
)
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


def _ankom():
    if not ANKOM.is_file():
        raise unittest.SkipTest(
            f"{ANKOM.name} (ANKOM) is not present in the working tree; it is "
            f"a source from a restored store that is deliberately not committed."
        )
    return extract_protocol_pdf(ANKOM)


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


class LocalSourceTriggerTests(unittest.TestCase):
    def test_the_guarantee_still_fires_where_it_caught_a_real_miss(self) -> None:
        """Page 32 is where the whole-document run caught a genuine omission."""

        extraction = _ankom()
        self.assertTrue(_numbered_step_labels(extraction.pages[31].text))

    def test_the_hierarchically_numbered_procedure_is_now_visible(self) -> None:
        """What this document actually numbers, once "3.2" counts as a label.

        Until STEP 24 the trigger read only a bare integer, so the nine labels
        it found here were headings and contents entries and the entire
        procedure was invisible: every instruction begins "3.2", "3.4", "4.1".
        The bare integers are still here -- they are still headings -- and
        twenty hierarchical labels have joined them, each one the start of an
        instruction.
        """

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
                5: ("3.2", "3.3"),
                6: ("3.4",),
                7: ("3.5", "3.6", "3.7"),
                8: ("3.8",),
                9: ("3.9", "3.10"),
                12: ("3.11",),
                13: ("3.12", "4"),
                14: ("4.1", "4.2"),
                18: ("4.3", "1"),
                19: ("2", "4.4"),
                20: ("4.5",),
                22: ("4.6",),
                27: ("4.7",),
                30: ("5", "1", "2"),
                31: ("5.1",),
                32: ("5.2",),
            },
        )
        hierarchical = [
            label
            for labels in remaining.values()
            for label in labels
            if "." in label
        ]
        self.assertEqual(len(hierarchical), 20)

    def test_a_sub_numbered_note_under_its_own_step_is_not_a_step(self) -> None:
        """headspace numbers notes under the step they belong to.

        "6.1 While we use LB as the primary medium..." sits under "6 Transfer
        10 mL of autoclaved LB...", and "18.1 Equation for working out dilution
        volume" under step 18. Both are line-anchored hierarchical numbers and
        neither is an instruction. They are told apart from a real "3.4" by one
        structural fact -- whether the parent number is itself a label on this
        page -- and never by reading the line.
        """

        if not HEADSPACE.is_file():
            raise unittest.SkipTest(
                f"{HEADSPACE.name} is not present in the working tree; it is "
                f"a 5 MB source that is deliberately not committed."
            )
        extraction = extract_protocol_pdf(HEADSPACE)
        labels = {
            p.source_page_number: _numbered_step_labels(p.text)
            for p in extraction.pages
        }
        self.assertIn("6", labels[4])
        self.assertNotIn("6.1", labels[4])
        self.assertIn("18", labels[5])
        self.assertNotIn("18.1", labels[5])
        self.assertEqual(sum(len(v) for v in labels.values()), 61)

    def test_the_documents_with_no_hierarchical_numbering_are_untouched(self):
        """in-gel is the accuracy reference, so its count must not move."""

        for source, expected in ((ANKOM, 67), (CANDIDATE_A, 25)):
            with self.subTest(source=source.name):
                if not source.is_file():
                    self.skipTest(
                        f"{source.name} is not present in the working tree."
                    )
                extraction = extract_protocol_pdf(source)
                labels = [
                    label
                    for page in extraction.pages
                    for label in _numbered_step_labels(page.text)
                ]
                self.assertEqual(len(labels), expected)
                self.assertFalse([label for label in labels if "." in label])


if __name__ == "__main__":
    unittest.main()
