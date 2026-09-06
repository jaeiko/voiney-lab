"""End-of-line sentence boundaries, and the detector for when they find none."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.test_protocol_claim_analysis import write_lined_pages, write_pages
from voice_workflow_agent.experiment_protocol_pdf import extract_protocol_pdf
from voice_workflow_agent.protocol_claim_analysis import (
    EVIDENCE_SEGMENT_VERSION,
    MIN_LINES_FOR_SEGMENTATION,
    _MAX_PROVIDER_SEGMENT_CHARS,
    _numbered_action_matches,
    _bounded_action_block_boundaries,
    degraded_segmentation_pages,
    generate_page_evidence_segments,
)

REVISION = "rev-1"

_HAZARD_PAGE = (
    "50 Prepare a 72% H2SO4 solution.",
    "Safety information",
    "Danger, highly corrosive.",
    "Wear gloves, labcoat, safety glasses.",
    "Note",
    "Use a cylinder to measure 242 ml of dH2O.",
    "Use a glass cylinder to measure 758 ml of H2SO4.",
    "Wait at least 1 h for the solution to cool down.",
    "51 Place the bags into a 2 l beaker.",
)


def legacy_boundaries(page_text: str) -> set[int]:
    """The label-only boundary set, before end-of-line punctuation was added."""

    coarse = sorted(
        {
            0,
            len(page_text),
            *(m.start("label") for m in _numbered_action_matches(page_text)),
        }
    )
    bounded = [coarse[0]]
    for end in coarse[1:]:
        start = bounded[-1]
        while end - start > _MAX_PROVIDER_SEGMENT_CHARS:
            hard = start + _MAX_PROVIDER_SEGMENT_CHARS
            newline = page_text.rfind("\n", start + 1, hard + 1)
            split = newline + 1 if newline >= start + 1 else hard
            bounded.append(split)
            start = split
        if bounded[-1] != end:
            bounded.append(end)
    return set(bounded)


class SentenceLineBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)

    def _segments(self, pages, page_number=1):
        path = self.root / f"lined-{page_number}.pdf"
        write_lined_pages(path, pages)
        extraction = extract_protocol_pdf(path)
        return extraction, generate_page_evidence_segments(
            extraction, source_revision=REVISION, page_number=page_number
        )

    def test_evidence_segment_version_records_the_new_derivation(self) -> None:
        """6 since the running footer is a segment of its own.

        Version 5 was the glyph fix: unmapped characters read from the
        document's own declaration, so ANKOM page 9 says alpha-amylase and the
        page hash moved with it. Version 6 is STEP 30's bottom band, which
        changed where the last segment of a page ends on 35 of the 99 local
        pages.

        Bumping this number is not free and is not meant to be. It is inside
        every canonical segment id, so every recorded citation in the
        repository stops resolving and has to be recomputed -- the ten timer
        candidates in the development manifest were migrated with this bump.
        That is the intended cost: a citation made under one segmentation
        scheme must fail loudly under another rather than quietly point
        somewhere else.
        """

        self.assertEqual(EVIDENCE_SEGMENT_VERSION, 6)

    _COMPOSITION_CORPUS = (
        _HAZARD_PAGE,
        (
            "Protocol for in-gel digestion",
            "1. Excise the band with a clean scalpel.",
            "1.1 A note that belongs to step 1, not beside it.",
            "2. Add 100 ul of buffer and vortex briefly.",
            "Note: keep the tube on ice.",
            "3. Incubate 15 min at room temperature.",
            "Page 2 of 9",
        ),
        (
            "4 Centrifuge at 800 rpm",
            "for 10 min, then discard the supernatant.",
            "Critical: do not disturb the pellet",
            "5 Repeat steps 2 to 4 twice.",
        ),
        # The band's own case: the last instruction ends without a terminator,
        # so without the band rule the running footer is absorbed into it and
        # the footer's page number becomes part of a step's text.
        (
            "7 Transfer the supernatant to a fresh tube",
            "and store it at minus 20 C",
            "In-gel digestion protocol v3   Page 4 of 9",
        ),
    )

    def _composition_fingerprint(self, extraction) -> str:
        """Every segment's page, index and exact text, hashed in source order.

        Text, not ids: ids move whenever the version constant moves, so a
        fingerprint over ids would agree with itself for the wrong reason.
        """

        import hashlib

        parts = []
        for number in range(1, extraction.page_count + 1):
            for segment in generate_page_evidence_segments(
                extraction, source_revision=REVISION, page_number=number
            ):
                parts.append(
                    f"{number}:{segment.segment_index}:{segment.text}"
                )
        return hashlib.sha256(
            "\u0000".join(parts).encode("utf-8")
        ).hexdigest()

    def test_a_changed_segment_composition_forces_the_version_to_move(self):
        """The check the last two bumps were made without.

        Both were noticed by hand. STEP 30 changed segment composition and
        left this constant alone, and nothing failed -- the leading segments
        of every page kept their ids, so the damage was invisible until
        someone counted. This fingerprint is what counting looks like as a
        test: it is over segment *text*, which is what composition means, and
        it is pinned beside the version number so the two can only move
        together.

        The bottom band is applied here explicitly rather than left to the
        page geometry. A synthetic PDF has no running footer, so a corpus
        built only from one would be blind to exactly the change that made
        version 6 necessary.

        If this fails, the question is not how to update the fingerprint. It
        is whether the segmentation was meant to change; if it was, the
        version constant moves too and every stored citation is migrated with
        it.
        """

        from dataclasses import replace

        extraction, _ = self._segments(self._COMPOSITION_CORPUS, page_number=1)
        banded = replace(
            extraction,
            pages=tuple(
                # No band on the first page, a band on the rest: both sides of
                # the rule are inside the one fingerprint.
                replace(
                    page,
                    bottom_band_offset=(
                        None
                        if index == 0
                        else page.text.rfind("\n", 0, len(page.text) - 1) + 1
                    ),
                )
                for index, page in enumerate(extraction.pages)
            ),
        )
        self.assertEqual(
            (EVIDENCE_SEGMENT_VERSION, self._composition_fingerprint(banded)),
            (
                6,
                "f51173d64140c3d47bddd40dc77fcaf891190db607bfcb0a1370dd9b63b04e8c",
            ),
            "segment composition changed; see this test's docstring",
        )

    def test_the_real_document_composition_is_pinned_too(self) -> None:
        """The synthetic corpus cannot see the band being *measured* wrongly.

        The fingerprint above fixes the band by hand, which pins how a band is
        used but not how one is found. In-gel has real running footers, so its
        composition also covers the worker's geometry -- the 0.08 fraction, the
        character boxes, the snap to a line start.
        """

        source = (
            Path(__file__).resolve().parents[1]
            / "data" / "runtime" / "candidate-a-source" / "in-gel-digestion.pdf"
        )
        if not source.is_file():
            self.skipTest(f"{source} is not present.")
        extraction = extract_protocol_pdf(source)
        self.assertEqual(extraction.page_count, 9)
        self.assertEqual(
            (EVIDENCE_SEGMENT_VERSION, self._composition_fingerprint(extraction)),
            (
                6,
                "e1aa0a4c2498e376d19f0e0a71be30b65118a1eedefe9f0aac6f26e23ab4854c",
            ),
            "segment composition changed; see the previous test's docstring",
        )

    def test_an_unnumbered_hazard_block_becomes_its_own_segment(self) -> None:
        """The hazard leaves the numbered step it used to be absorbed into.

        A heading with no terminator stays attached to the line it introduces,
        which is wanted: "Safety information" belongs with the warning it
        labels.
        """

        _, segments = self._segments((_HAZARD_PAGE,))
        texts = [segment.text.strip() for segment in segments]
        self.assertIn("Safety information\nDanger, highly corrosive.", texts)
        self.assertIn("Wear gloves, labcoat, safety glasses.", texts)
        step = next(s for s in texts if s.startswith("50 "))
        self.assertNotIn("Danger", step)
        self.assertNotIn("Wear gloves", step)

    def test_each_preparation_value_lands_in_its_own_segment(self) -> None:
        _, segments = self._segments((_HAZARD_PAGE,))
        for value in ("242 ml", "758 ml", "1 h"):
            with self.subTest(value=value):
                holding = [s for s in segments if value in s.text]
                self.assertEqual(len(holding), 1)
                others = {"242 ml", "758 ml", "1 h"} - {value}
                self.assertFalse(
                    [other for other in others if other in holding[0].text],
                    f"{value} shares a segment with another value",
                )

    def test_a_wrapped_sentence_is_not_split(self) -> None:
        """The rule keys on the line break, not on the sentence."""

        _, segments = self._segments(
            (("1 Mix the acid and water. ALWAYS ADD ACID TO WATER",
              "(slowly) AND NOT THE OPPOSITE!",
              "2 Cool the solution."),)
        )
        joined = [s.text for s in segments if "ALWAYS ADD ACID" in s.text]
        self.assertEqual(len(joined), 1)
        self.assertIn("(slowly) AND NOT THE OPPOSITE!", joined[0])

    def test_boundaries_are_only_added_never_removed(self) -> None:
        pages = (
            _HAZARD_PAGE,
            ("1 Weigh the sample.", "2 Heat it to 65 degrees C.", "Done."),
        )
        path = self.root / "monotonic.pdf"
        write_lined_pages(path, pages)
        extraction = extract_protocol_pdf(path)
        for page_number in (1, 2):
            with self.subTest(page=page_number):
                text = extraction.pages[page_number - 1].text
                new = set(_bounded_action_block_boundaries(text))
                self.assertTrue(legacy_boundaries(text) <= new)

    def test_segments_still_reconstruct_the_page_exactly(self) -> None:
        extraction, segments = self._segments((_HAZARD_PAGE,))
        self.assertEqual(
            "".join(s.text for s in segments), extraction.pages[0].text
        )

    def test_the_size_ceiling_still_guards_a_newline_free_page(self) -> None:
        """A page with no line breaks is exactly the degenerate case."""

        long_line = "1 " + ("Add reagent and mix thoroughly. " * 400)
        path = self.root / "flat.pdf"
        write_pages(path, (long_line,))
        extraction = extract_protocol_pdf(path)
        self.assertEqual(extraction.pages[0].text.count("\n"), 0)
        segments = generate_page_evidence_segments(
            extraction, source_revision=REVISION, page_number=1
        )
        self.assertGreater(len(segments), 1)
        for segment in segments:
            self.assertLessEqual(len(segment.text), _MAX_PROVIDER_SEGMENT_CHARS)


class SegmentationDegradationDetectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)

    def _detect(self, pages):
        path = self.root / "detect.pdf"
        write_lined_pages(path, pages)
        extraction = extract_protocol_pdf(path)
        return degraded_segmentation_pages(extraction, source_revision=REVISION)

    def test_a_page_with_no_sentence_punctuation_is_flagged(self) -> None:
        """An equipment metadata list, the real ANKOM p17 shape."""

        self.assertEqual(
            self._detect(
                (
                    (
                        "Oven NAME",
                        "Oven forced-air convection TYPE",
                        "Fisher Isotemp BRAND",
                        "151030510 SKU",
                        "Equipment",
                        "LINK",
                    ),
                )
            ),
            (1,),
        )

    def test_a_bullet_list_without_terminators_is_flagged(self) -> None:
        self.assertEqual(
            self._detect(
                (
                    (
                        "Materials",
                        "- acetonitrile",
                        "- ammonium bicarbonate",
                        "- formic acid",
                        "- trypsin",
                    ),
                )
            ),
            (1,),
        )

    def test_a_normally_segmented_page_is_not_flagged(self) -> None:
        self.assertEqual(self._detect((_HAZARD_PAGE,)), ())

    def test_a_short_page_is_not_flagged(self) -> None:
        """Below the line threshold there is nothing to segment."""

        self.assertLess(3, MIN_LINES_FOR_SEGMENTATION)
        self.assertEqual(self._detect((("Title", "Author", "DOI"),)), ())

    def test_only_the_degraded_page_is_reported(self) -> None:
        self.assertEqual(
            self._detect(
                (
                    _HAZARD_PAGE,
                    ("Oven NAME", "TYPE", "BRAND", "SKU", "LINK", "Equipment"),
                )
            ),
            (2,),
        )


if __name__ == "__main__":
    unittest.main()
