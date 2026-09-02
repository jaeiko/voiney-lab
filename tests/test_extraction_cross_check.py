"""Primary extractor, independent cross-check, and its fail-closed handling."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from voice_workflow_agent import experiment_protocol as domain
from voice_workflow_agent.experiment_protocol_pdf import (
    TextVerification,
    canonical_text_census,
    extract_protocol_pdf,
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

    def test_a_noncharacter_counts(self) -> None:
        """It stands in place of a character, so it is not normalized away.

        This census used to drop category Cn alongside the control characters.
        Combined with dropping the hyphens, that made an engine emitting
        alpha-amylase and one emitting alpha\ufffeamylase produce identical
        censuses, so a real disagreement inside a reagent dosing sentence was
        reported as agreement.
        """

        self.assertNotEqual(
            canonical_text_census("alpha￾amylase"),
            canonical_text_census("alpha-amylase"),
        )
        self.assertNotEqual(
            canonical_text_census("Dry￾ the bags"),
            canonical_text_census("Dry the bags"),
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

    def test_a_source_with_unmapped_glyphs_is_refused(self) -> None:
        """ANKOM carries nine U+FFFE, one per affected page.

        It used to pass, because the census dropped the noncharacter and the
        hyphen the other engine emitted in the same position. It does not pass
        now, and the pages it names are the pages the comparison independently
        finds divergent.
        """

        extraction = extract_protocol_pdf(ANKOM)
        self.assertIs(extraction.text_verification, TextVerification.MISMATCH)
        self.assertEqual(
            extraction.divergent_page_numbers,
            (9, 17, 25, 33, 35, 36, 37, 38, 39),
        )


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
        # The unchecked source must be the *only* thing left blocking.
        assert readiness.reason_codes == (
            domain.ReadinessReasonCode
            .SOURCE_TEXT_CROSS_CHECK_UNAVAILABLE.value,
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

    The decision, stated once here and enforced in verify_page_text: a
    private-use or noncharacter code point means the extractor could not map a
    glyph, so the character at that position is unknown and the page is
    refused. It is never substituted, because what it stood for is not
    recoverable.

    The evidence for "not recoverable" is measured, not assumed. On ANKOM page
    9 the primary engine yields ``alpha\ufffeamylase`` and the comparison
    engine yields ``alphaamylase`` -- the hyphen deleted outright -- so neither
    engine recovers the character. In a reference DOI the primary engine yields
    ``978-1\ufffe4939`` where the comparison engine yields ``978-14939``,
    which is a wrong DOI, so the comparator is not an oracle either. And the
    right character is not even the same everywhere: ``alpha-amylase`` takes a
    hyphen while ``Liquid Chromatography--Mass Spectrometry`` conventionally
    takes an en dash. Any single substitution would be wrong somewhere.

    The harm a substitution would do is specific to this design. The affected
    text on ANKOM page 9 is inside numbered step 11's evidence segment, in a
    reagent dosing sentence. A guessed character there would be quoted as
    canonical source_text and then confirmed by exact-evidence validation, so
    the pipeline would certify a character we invented.
    """

    def test_a_noncharacter_is_unmapped(self) -> None:
        self.assertEqual(unmapped_code_points("alpha￾amylase"), {"￾": 1})

    def test_a_private_use_character_is_unmapped(self) -> None:
        self.assertEqual(unmapped_code_points("5049"), {"": 1})

    def test_ordinary_text_holds_nothing_unmapped(self) -> None:
        self.assertEqual(unmapped_code_points("Add 8.0 mL of alpha-amylase"), {})

    def test_an_en_dash_is_not_unmapped(self) -> None:
        """It is a real character, and one of the shapes U+FFFE replaces."""

        self.assertEqual(unmapped_code_points("Chromatography–Mass"), {})

    def test_extraction_does_not_substitute_the_character(self) -> None:
        """Refusal, not repair: the noncharacter is still there afterwards."""

        extraction = extract_protocol_pdf(ANKOM)
        self.assertIn("￾", extraction.pages[8].text)
        self.assertNotIn("alpha-amylase and enough", extraction.pages[8].text)

    def test_it_is_refused_even_with_no_comparator(self) -> None:
        """The hole this closes.

        Deciding it only by comparison would leave it undetected wherever no
        comparison engine is installed, and comparator_unavailable is a gate a
        person may acknowledge, so genuinely unmapped text could be waved
        through.
        """

        extraction = extract_protocol_pdf(ANKOM)
        with patch(
            "voice_workflow_agent.experiment_protocol_pdf.shutil.which",
            return_value=None,
        ):
            verdict, divergent = verify_page_text(ANKOM, extraction.pages)
        self.assertIs(verdict, TextVerification.MISMATCH)
        self.assertIn(9, divergent)

    def test_a_clean_source_is_unaffected_by_that_check(self) -> None:
        extraction = extract_protocol_pdf(IN_GEL)
        self.assertEqual(
            [p.source_page_number for p in extraction.pages
             if unmapped_code_points(p.text)],
            [],
        )

    def test_refusal_blocks_admission(self) -> None:
        extraction = extract_protocol_pdf(ANKOM)
        with self.assertRaises(ProtocolChunkAdmissionError):
            plan_protocol_chunks(extraction, "protocol-x", "pdf-1")

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
