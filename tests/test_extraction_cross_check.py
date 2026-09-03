"""Primary extractor, independent cross-check, and its fail-closed handling."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from voice_workflow_agent import experiment_protocol as domain
from pypdf import PdfReader

from voice_workflow_agent.experiment_protocol_pdf import (
    ProtocolPdfExtraction,
    TextVerification,
    _declared_unicode_values,
    canonical_text_census,
    extract_protocol_pdf,
    resolve_unmapped_page_text,
    unmapped_code_points,
    verify_page_text,
)
from voice_workflow_agent.protocol_chunk_analysis import (
    ProtocolChunkAdmissionError,
    plan_protocol_chunks,
)

ANKOM = Path(
    "data/runtime/candidate-a-live-acceptance/objects/sha256/53"
    "/5367ca6bfae9fe9bbaeac9dab2099276a9c2dccf6c698ee36e59c7552e56d18a.pdf"
)
IN_GEL = Path("data/runtime/candidate-a-source/in-gel-digestion.pdf")


class CanonicalTextCensusTests(unittest.TestCase):
    def test_line_breaking_and_control_padding_do_not_count(self) -> None:
        self.assertEqual(
            canonical_text_census("Add 10 mL\r\nbuffer"),
            canonical_text_census("Add 10 mL buffer\n"),
        )
        self.assertEqual(
            canonical_text_census("Add\x02 10 mL"),
            canonical_text_census("Add 10 mL"),
        )

    def test_hyphen_variants_do_not_count(self) -> None:
        self.assertEqual(
            canonical_text_census("2‐mm screen"),
            canonical_text_census("2-mm screen"),
        )

    def test_unmapped_code_points_are_not_compared_here(self) -> None:
        """Each engine fails at an unmapped glyph in its own way.

        pdfium emits U+FFFE, pypdf emits a private-use code point, poppler
        deletes the character. Comparing placeholders compares the engines'
        defects, not the document, so those positions are decided one at a
        time against the PDF's ToUnicode declaration instead, and admitted
        page text keeps none of them. This census is for the other corruption:
        a real character silently replaced by another.
        """

        self.assertEqual(
            canonical_text_census("alpha￾amylase"),
            canonical_text_census("alphaamylase"),
        )
        self.assertNotEqual(
            canonical_text_census("formic acid (50:49:1)"),
            canonical_text_census("formic acid (50a49b1)"),
        )

    def test_order_does_not_count(self) -> None:
        self.assertEqual(
            canonical_text_census("Ayotte1, Laliberte"),
            canonical_text_census("Ayotte, Laliberte1"),
        )

    def test_private_use_substitution_is_detected(self) -> None:
        """The corruption this census exists to catch."""

        self.assertNotEqual(
            canonical_text_census("formic acid (50:49:1) for 00:30:00"),
            canonical_text_census(
                "formic acid 50491) for 003000"
            ),
        )

    def test_alphanumeric_only_comparison_would_miss_it(self) -> None:
        """Why the census keeps structural characters instead of just alnum."""

        alnum = lambda text: "".join(c for c in text if c.isalnum())
        clean = "formic acid (50:49:1) for 00:30:00"
        corrupt = "formic acid 50491) for 003000"
        self.assertEqual(alnum(clean), alnum(corrupt))
        self.assertNotEqual(
            canonical_text_census(clean), canonical_text_census(corrupt)
        )


class PrimaryExtractorTests(unittest.TestCase):
    def setUp(self) -> None:
        if not ANKOM.exists() or not IN_GEL.exists():
            self.skipTest("Local protocol sources are not present.")

    def test_no_private_use_substitution_remains(self) -> None:
        for source in (ANKOM, IN_GEL):
            with self.subTest(source=source.name):
                text = "".join(
                    page.text for page in extract_protocol_pdf(source).pages
                )
                self.assertFalse(
                    [c for c in text if 0xE000 <= ord(c) <= 0xF8FF],
                    "extracted text still contains private-use glyphs",
                )

    def test_previously_destroyed_values_are_recovered(self) -> None:
        in_gel = extract_protocol_pdf(IN_GEL)
        self.assertIn("(50:49:1)", in_gel.pages[8].text)
        self.assertIn("00:30:00", in_gel.pages[8].text)
        ankom = extract_protocol_pdf(ANKOM)
        self.assertIn("72 h at 65", ankom.pages[2].text)

    def test_a_clean_local_source_passes_the_cross_check(self) -> None:
        extraction = extract_protocol_pdf(IN_GEL)
        self.assertIs(extraction.text_verification, TextVerification.VERIFIED)
        self.assertEqual(extraction.divergent_page_numbers, ())
        self.assertTrue(extraction.text_cross_checked)

    def test_a_source_whose_glyphs_the_document_declares_passes(self) -> None:
        """ANKOM carries nine U+FFFE and the document declares all nine.

        Each one is read from the PDF's own ToUnicode map, so the source is
        admissible and the reagent name reads correctly.
        """

        extraction = extract_protocol_pdf(ANKOM)
        self.assertIs(extraction.text_verification, TextVerification.VERIFIED)
        self.assertEqual(extraction.divergent_page_numbers, ())
        self.assertEqual(len(extraction.glyph_resolutions), 9)
        self.assertEqual(extraction.unresolved_glyph_reasons, ())
        self.assertIn("alpha-amylase and enough", extraction.pages[8].text)
        self.assertNotIn("￾", extraction.pages[8].text)


class CrossCheckOutcomeTests(unittest.TestCase):
    def setUp(self) -> None:
        if not IN_GEL.exists():
            self.skipTest("Local protocol source is not present.")
        self.extraction = extract_protocol_pdf(IN_GEL)

    def test_absent_comparator_is_recorded_never_assumed_clean(self) -> None:
        with patch(
            "voice_workflow_agent.experiment_protocol_pdf.shutil.which",
            return_value=None,
        ):
            verdict, divergent = verify_page_text(
                IN_GEL, self.extraction.pages
            )
        self.assertIs(verdict, TextVerification.COMPARATOR_UNAVAILABLE)
        self.assertEqual(divergent, ())

    def test_failing_comparator_process_is_not_treated_as_agreement(self) -> None:
        for outcome in (
            subprocess.CompletedProcess([], 1, b"", b""),
            OSError("comparator exploded"),
        ):
            with self.subTest(outcome=type(outcome).__name__):
                target = (
                    "voice_workflow_agent.experiment_protocol_pdf"
                    ".subprocess.run"
                )
                kwargs = (
                    {"side_effect": outcome}
                    if isinstance(outcome, Exception)
                    else {"return_value": outcome}
                )
                with patch(target, **kwargs):
                    verdict, _ = verify_page_text(IN_GEL, self.extraction.pages)
                self.assertIs(
                    verdict, TextVerification.COMPARATOR_UNAVAILABLE
                )

    def test_divergent_pages_are_reported(self) -> None:
        with patch(
            "voice_workflow_agent.experiment_protocol_pdf._comparator_pages",
            return_value=tuple(
                "totally different text" if index == 1 else page.text
                for index, page in enumerate(self.extraction.pages)
            ),
        ):
            verdict, divergent = verify_page_text(IN_GEL, self.extraction.pages)
        self.assertIs(verdict, TextVerification.MISMATCH)
        self.assertEqual(divergent, (2,))

    def test_page_count_disagreement_is_a_mismatch(self) -> None:
        with patch(
            "voice_workflow_agent.experiment_protocol_pdf._comparator_pages",
            return_value=("one page only",),
        ):
            verdict, _ = verify_page_text(IN_GEL, self.extraction.pages)
        self.assertIs(verdict, TextVerification.MISMATCH)


class AdmissionAndReadinessTests(unittest.TestCase):
    """Where each verdict stops: admission for proven wrong, readiness for unknown."""

    def setUp(self) -> None:
        if not IN_GEL.exists():
            self.skipTest("Local protocol source is not present.")
        self.extraction = extract_protocol_pdf(IN_GEL)

    def test_mismatch_is_refused_at_canonical_admission(self) -> None:
        from dataclasses import replace

        corrupted = replace(
            self.extraction, text_verification=TextVerification.MISMATCH
        )
        with self.assertRaises(ProtocolChunkAdmissionError):
            plan_protocol_chunks(corrupted, "protocol-x", "pdf-1")

    def test_unavailable_comparator_still_admits_but_blocks_readiness(self) -> None:
        from dataclasses import replace

        unchecked = replace(
            self.extraction,
            text_verification=TextVerification.COMPARATOR_UNAVAILABLE,
        )
        plan = plan_protocol_chunks(unchecked, "protocol-x", "pdf-1")
        self.assertTrue(plan.chunks)

    def _protocol(self, verification: TextVerification):
        from dataclasses import replace

        extraction = replace(self.extraction, text_verification=verification)
        evidence = domain.SourceEvidence(1, extraction.pages[0].text[:12])
        return domain.ExperimentProtocol(
            "protocol-x",
            domain.ProtocolMetadata(
                extraction,
                extraction.pages[0].text[:12],
                "en",
                evidence=evidence,
            ),
        )

    def test_each_verdict_maps_to_its_readiness_reason(self) -> None:
        cases = (
            (
                TextVerification.MISMATCH,
                domain.ReadinessReasonCode.SOURCE_TEXT_CROSS_CHECK_FAILED,
            ),
            (
                TextVerification.COMPARATOR_UNAVAILABLE,
                domain.ReadinessReasonCode.SOURCE_TEXT_CROSS_CHECK_UNAVAILABLE,
            ),
        )
        for verification, expected in cases:
            with self.subTest(verification=verification.value):
                codes = domain.assess_readiness(
                    self._protocol(verification)
                ).reason_codes
                self.assertIn(expected.value, codes)

    def test_verified_source_adds_no_cross_check_reason(self) -> None:
        codes = domain.assess_readiness(
            self._protocol(TextVerification.VERIFIED)
        ).reason_codes
        self.assertNotIn(
            domain.ReadinessReasonCode.SOURCE_TEXT_CROSS_CHECK_FAILED.value,
            codes,
        )
        self.assertNotIn(
            domain.ReadinessReasonCode
            .SOURCE_TEXT_CROSS_CHECK_UNAVAILABLE.value,
            codes,
        )


class UnverifiedSourceAcknowledgementTests(unittest.TestCase):
    """A missing comparator must be visible and need a person, not pass quietly."""

    def setUp(self) -> None:
        if not IN_GEL.exists():
            self.skipTest("Local protocol source is not present.")
        from voice_workflow_agent.experiment_protocol_store import (
            ProtocolPersistenceSettings,
            initialize_protocol_store,
        )
        from voice_workflow_agent.protocol_catalog import ProtocolCatalog

        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        root = Path(self._temp.name)
        self.store = initialize_protocol_store(
            ProtocolPersistenceSettings(True, root / "catalog")
        )
        self.addCleanup(self.store.close)
        self.catalog = ProtocolCatalog(self.store)

    def _register_unverified(self):
        """An otherwise execution-ready Protocol whose source was not checked."""

        from dataclasses import replace

        from tests.test_protocol_catalog import write_text_pdf

        source = Path(self._temp.name) / "unverified.pdf"
        write_text_pdf(
            source,
            "Protocol Unverified\nSection preparation\n1. Add solution."
            "\nWear gloves.",
            title="Protocol Unverified",
        )
        registration = self.catalog.register(
            source,
            source_filename="unverified.pdf",
            media_type="application/pdf",
        )
        protocol_id = registration.entry.protocol_id
        extraction = replace(
            extract_protocol_pdf(source),
            text_verification=TextVerification.COMPARATOR_UNAVAILABLE,
        )
        evidence = lambda excerpt: domain.SourceEvidence(1, excerpt)
        protocol = domain.ExperimentProtocol(
            protocol_id,
            domain.ProtocolMetadata(
                extraction,
                "Protocol Unverified",
                "en",
                evidence=evidence("Protocol Unverified"),
            ),
            sections=(
                domain.ProtocolSection(
                    "preparation",
                    "Section preparation",
                    evidence("Section preparation"),
                    (
                        domain.ProtocolSourceStep(
                            "step-1",
                            "1",
                            "1. Add solution.",
                            evidence("1. Add solution."),
                            warnings=(
                                domain.SourceStatement(
                                    "glove-warning",
                                    "Wear gloves.",
                                    evidence("Wear gloves."),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        )
        readiness = domain.assess_readiness(protocol)
        # The unchecked source and the safety confirmation must be the only
        # things left blocking. Both are acknowledgeable gates, and both are
        # cleared by a person rather than by extraction.
        assert readiness.reason_codes == (
            domain.ReadinessReasonCode
            .SOURCE_TEXT_CROSS_CHECK_UNAVAILABLE.value,
            domain.ReadinessReasonCode.NO_DECLARED_SAFETY_WARNINGS.value,
        ), readiness.reason_codes
        self.store.append_analysis_revision(
            protocol_id,
            1,
            "analysis-unverified",
            protocol,
            readiness,
            domain.P1_CAPABILITY_POLICY.profile_id,
        )
        return self.catalog.get_entry(protocol_id)

    def test_unverified_source_is_visible_in_review(self) -> None:
        entry = self._register_unverified()
        review = self.catalog.review(entry.protocol_id)
        self.assertIn(
            domain.ReadinessReasonCode
            .SOURCE_TEXT_CROSS_CHECK_UNAVAILABLE.value,
            [reason["code"] for reason in review["readiness"]["reasons"]],
        )

    def test_unverified_source_needs_an_audited_acknowledgement(self) -> None:
        from voice_workflow_agent.protocol_catalog import (
            ProtocolApprovalError,
            SharedSecretApprovalPolicy,
        )

        entry = self._register_unverified()
        approve = lambda: self.catalog.approve(
            entry.protocol_id,
            entry.revision_id,
            policy=SharedSecretApprovalPolicy("s"),
            presented_secret="s",
        )
        with self.assertRaises(ProtocolApprovalError):
            approve()

        gate = domain.ReadinessReasonCode.SOURCE_TEXT_CROSS_CHECK_UNAVAILABLE
        self.catalog.acknowledge_readiness_gate(
            entry.protocol_id,
            entry.revision_id,
            reason_code=gate.value,
            actor_principal_id="reviewer@example.org",
            actor_role="reviewer",
            comment="No comparator in this environment; source checked by hand.",
        )
        recorded = [
            event
            for event in self.store.list_events(entry.protocol_id)
            if event.event_type == "protocol_readiness_gate_acknowledged"
        ]
        self.assertEqual(len(recorded), 1)
        self.assertEqual(recorded[0].payload["reason_code"], gate.value)
        self.assertEqual(
            recorded[0].payload["actor_principal_id"], "reviewer@example.org"
        )
        self.assertTrue(recorded[0].recorded_at)
        # Approval still blocks: the safety confirmation is a separate gate and
        # clearing one never clears another.
        with self.assertRaises(ProtocolApprovalError):
            approve()
        self.catalog.acknowledge_readiness_gate(
            entry.protocol_id,
            entry.revision_id,
            reason_code=(
                domain.ReadinessReasonCode.NO_DECLARED_SAFETY_WARNINGS.value
            ),
            actor_principal_id="reviewer@example.org",
            actor_role="reviewer",
            comment="Warnings reviewed against the source.",
        )
        approve()

    def test_a_proven_mismatch_can_never_be_acknowledged(self) -> None:
        from voice_workflow_agent.protocol_catalog import ProtocolApprovalError

        entry = self._register_unverified()
        with self.assertRaises(ProtocolApprovalError):
            self.catalog.acknowledge_readiness_gate(
                entry.protocol_id,
                entry.revision_id,
                reason_code=(
                    domain.ReadinessReasonCode
                    .SOURCE_TEXT_CROSS_CHECK_FAILED.value
                ),
                actor_principal_id="reviewer@example.org",
                actor_role="reviewer",
            )


if __name__ == "__main__":
    unittest.main()


class UnmappedCodePointDecisionTests(unittest.TestCase):
    """What an unmapped code point in extracted text is taken to mean.

    An unmapped code point -- private use, or a noncharacter such as U+FFFE --
    means the primary extractor could not map a glyph. It splits into two
    classes, and the earlier single-class refusal was wrong about the first.

    Class 1: the PDF declares the character in its own ToUnicode map and an
    engine reads it. Reading that is not repair; it is reading the source, and
    the server owning authority over its evidence is exactly this case. On
    ANKOM page 9 font /F57 declares <B6> -> <002D>, so `alpha\ufffeamylase` is
    read as `alpha-amylase` from the document's declaration.

    Class 2: no engine reads a character the document declares. The document
    says nothing, so the position is refused. Every intracellular case is here:
    three where the second engine also emits a private-use placeholder, in a
    part number, a compound name and a DOI, and two where the engines cannot be
    aligned at all, both DOIs.

    What stays forbidden is everything that is not the document speaking: no
    majority vote between placeholders, no "it looks like a hyphen", no
    inference from surrounding words. Deleting the character says nothing about
    it, so an engine that joins the words neither resolves nor conflicts; two
    engines reporting different real characters is a genuine conflict.
    """

    def test_a_noncharacter_is_unmapped(self) -> None:
        self.assertEqual(unmapped_code_points("alpha￾amylase"), {"￾": 1})

    def test_a_private_use_character_is_unmapped(self) -> None:
        """Written as a code point: the glyph itself is invisible in a file."""

        pua = chr(0xE081)
        self.assertEqual(unmapped_code_points(f"50{pua}49"), {pua: 1})

    def test_ordinary_text_holds_nothing_unmapped(self) -> None:
        self.assertEqual(unmapped_code_points("Add 8.0 mL of alpha-amylase"), {})

    def test_an_en_dash_is_not_unmapped(self) -> None:
        self.assertEqual(unmapped_code_points("Chromatography–Mass"), {})

    def test_class_one_is_read_from_the_document(self) -> None:
        extraction = extract_protocol_pdf(ANKOM)
        self.assertEqual(len(extraction.glyph_resolutions), 9)
        for resolution in extraction.glyph_resolutions:
            with self.subTest(page=resolution.source_page_number):
                self.assertEqual(resolution.resolved_character, "-")
                self.assertEqual(resolution.unmapped_code_point, 0xFFFE)
                self.assertTrue(resolution.declared_by_document)
                self.assertTrue(resolution.resolved_by)

    def test_the_document_really_declares_that_character(self) -> None:
        """The mapping the resolution rests on, read from the font itself."""

        page = PdfReader(ANKOM).pages[8]
        self.assertIn("-", _declared_unicode_values(page))

    def test_admitted_text_never_keeps_an_unmapped_code_point(self) -> None:
        """The invariant: resolve every one, or refuse the document."""

        for source in (ANKOM, IN_GEL):
            extraction = extract_protocol_pdf(source)
            with self.subTest(source=source.name):
                self.assertIs(
                    extraction.text_verification, TextVerification.VERIFIED
                )
                self.assertEqual(
                    [
                        page.source_page_number
                        for page in extraction.pages
                        if unmapped_code_points(page.text)
                    ],
                    [],
                )

    def test_a_character_the_document_does_not_declare_is_refused(self) -> None:
        """Not a vote: agreement between engines is not authority."""

        text, resolutions, failures = resolve_unmapped_page_text(
            "Add 8.0 mL of alpha￾amylase now",
            source_page_number=1,
            candidates={
                "one": "Add 8.0 mL of alpha-amylase now",
                "two": "Add 8.0 mL of alpha-amylase now",
            },
            declared=frozenset("abcdefghilmnopqrstuvwxyz .0128"),
        )
        self.assertEqual(resolutions, ())
        self.assertIn("does not declare", failures[0])
        self.assertIn("￾", text)

    def test_two_engines_reading_different_characters_conflict(self) -> None:
        _, resolutions, failures = resolve_unmapped_page_text(
            "Add 8.0 mL of alpha￾amylase now",
            source_page_number=1,
            candidates={
                "one": "Add 8.0 mL of alpha-amylase now",
                "two": "Add 8.0 mL of alpha–amylase now",
            },
            declared=frozenset({"-", "–"}),
        )
        self.assertEqual(resolutions, ())
        self.assertIn("different", failures[0])

    def test_deletion_alone_resolves_nothing(self) -> None:
        """Joining the words says nothing about the character that was there."""

        _, resolutions, failures = resolve_unmapped_page_text(
            "Add 8.0 mL of alpha￾amylase now",
            source_page_number=1,
            candidates={"poppler": "Add 8.0 mL of alphaamylase now"},
            declared=frozenset({"-"}),
        )
        self.assertEqual(resolutions, ())
        self.assertIn("no engine resolved", failures[0])

    def test_a_declared_character_resolves_and_is_substituted(self) -> None:
        text, resolutions, failures = resolve_unmapped_page_text(
            "Add 8.0 mL of alpha￾amylase now",
            source_page_number=1,
            candidates={"reader": "Add 8.0 mL of alpha-amylase now"},
            declared=frozenset({"-"}),
        )
        self.assertEqual(failures, ())
        self.assertEqual(text, "Add 8.0 mL of alpha-amylase now")
        self.assertEqual(len(resolutions), 1)
        self.assertEqual(resolutions[0].resolved_by, "reader")

    def test_no_provenance_means_no_resolution(self) -> None:
        """A position that cannot be recorded is not allowed through."""

        extraction = extract_protocol_pdf(ANKOM)
        recorded = {
            (r.source_page_number, r.text_offset)
            for r in extraction.glyph_resolutions
        }
        self.assertEqual(len(recorded), len(extraction.glyph_resolutions))

    def test_class_two_is_refused_even_with_no_comparator(self) -> None:
        """The hole this closes.

        Deciding an unmapped position only by comparison would leave it
        undetected wherever no comparison engine is installed, and
        comparator_unavailable is a gate a person may acknowledge, so
        unreadable text could be waved through.
        """

        _, resolutions, failures = resolve_unmapped_page_text(
            "Add 8.0 mL of alpha￾amylase now",
            source_page_number=1,
            candidates={},
            declared=frozenset({"-"}),
        )
        self.assertEqual(resolutions, ())
        self.assertTrue(failures)

    def test_the_gate_it_raises_is_not_acknowledgeable(self) -> None:
        """A person may wave through "not cross-checked", never "corrupted"."""

        from voice_workflow_agent.protocol_catalog import _ACKNOWLEDGEABLE_GATES

        self.assertIn(
            domain.ReadinessReasonCode.SOURCE_TEXT_CROSS_CHECK_UNAVAILABLE.value,
            _ACKNOWLEDGEABLE_GATES,
        )
        self.assertNotIn(
            domain.ReadinessReasonCode.SOURCE_TEXT_CROSS_CHECK_FAILED.value,
            _ACKNOWLEDGEABLE_GATES,
        )


# Recorded so absence is loud: a missing source skips with its reason printed,
# and the hash pins which file the measurement was taken from. The files are
# 24 MB and 5.1 MB and are deliberately not committed.
INTRACELLULAR = Path("intracellularmetaboliteextraction.pdf")
INTRACELLULAR_SHA256 = (
    "997d020c11ba915621b9705de9c4a92330f843c8feff4e2d1099dca763fdb9f0"
)
HEADSPACE = Path("usingdynamicheadspacecollections.pdf")
HEADSPACE_SHA256 = (
    "2bf102779364ec2dad517efc5acff7c5b6a5b569465e708f68186d7415c46fa2"
)


def _require(path: Path, digest: str) -> "ProtocolPdfExtraction":
    if not path.is_file():
        raise unittest.SkipTest(
            f"{path.name} is not present in the working tree; it is a "
            f"{'24 MB' if 'intra' in path.name else '5.1 MB'} source that is "
            f"deliberately not committed. Expected sha256 {digest}."
        )
    extraction = extract_protocol_pdf(path)
    if extraction.sha256 != digest:
        raise unittest.SkipTest(
            f"{path.name} is present but is a different file: sha256 "
            f"{extraction.sha256}, expected {digest}."
        )
    return extraction


class UndeclaredGlyphSourceTests(unittest.TestCase):
    """The document that is refused, and why it stays refused."""

    def test_a_source_with_undeclared_glyphs_is_refused(self) -> None:
        extraction = _require(INTRACELLULAR, INTRACELLULAR_SHA256)
        self.assertIs(extraction.text_verification, TextVerification.MISMATCH)
        self.assertEqual(extraction.divergent_page_numbers, (10, 18, 33))
        self.assertEqual(len(extraction.unresolved_glyph_reasons), 5)

    def test_its_refusal_blocks_admission(self) -> None:
        extraction = _require(INTRACELLULAR, INTRACELLULAR_SHA256)
        with self.assertRaises(ProtocolChunkAdmissionError):
            plan_protocol_chunks(extraction, "protocol-x", "pdf-1")

    def test_a_source_with_no_unmapped_glyph_needs_no_resolution(self) -> None:
        extraction = _require(HEADSPACE, HEADSPACE_SHA256)
        self.assertIs(extraction.text_verification, TextVerification.VERIFIED)
        self.assertEqual(extraction.glyph_resolutions, ())
