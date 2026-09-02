from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from tests.test_protocol_claim_analysis import write_pages
from voice_workflow_agent import experiment_protocol as domain
from voice_workflow_agent.experiment_protocol_pdf import (
    ProtocolPdfExtraction,
    extract_protocol_pdf,
)
from voice_workflow_agent.protocol_claim_analysis import (
    CLAIM_SCHEMA_VERSION,
    ClaimCategory,
    ClaimSourceEvidence,
    MergedProtocolClaims,
    PageCoverageStatus,
    ProtocolChunkClaimAnalysis,
    ProtocolClaim,
    ProtocolPageClaimCoverage,
    generate_page_evidence_segments,
)
from voice_workflow_agent.protocol_claim_semantic_audit import (
    MAX_AUDIT_FINDINGS,
    SemanticFindingCode,
    SemanticFindingSeverity,
    audit_assembly_preservation,
    audit_chunk_semantics,
    audit_merged_semantics,
    detect_source_value_tokens,
)

REVISION = "rev-1"


def build_extraction(directory: Path, pages: tuple[str, ...]) -> ProtocolPdfExtraction:
    path = directory / "protocol.pdf"
    write_pages(path, pages)
    return extract_protocol_pdf(path)


def evidence(
    extraction: ProtocolPdfExtraction,
    page_number: int,
    excerpt: str,
) -> ClaimSourceEvidence:
    """Build server-shaped evidence for the segments covering ``excerpt``."""

    page_text = extraction.pages[page_number - 1].text
    start = page_text.index(excerpt)
    end = start + len(excerpt)
    segments = generate_page_evidence_segments(
        extraction,
        source_revision=REVISION,
        page_number=page_number,
    )
    selected: list[str] = []
    covered: list[str] = []
    offset = 0
    for segment in segments:
        segment_end = offset + len(segment.text)
        if segment_end > start and offset < end:
            selected.append(segment.segment_id)
            covered.append(segment.text)
        offset = segment_end
    assert selected
    return ClaimSourceEvidence(
        source_revision=REVISION,
        source_sha256=extraction.sha256,
        source_page_number=page_number,
        page_text_sha256=hashlib.sha256(page_text.encode("utf-8")).hexdigest(),
        evidence_segment_ids=tuple(selected),
        source_excerpt="".join(covered),
    )


def claim(
    extraction: ProtocolPdfExtraction,
    *,
    claim_id: str,
    category: ClaimCategory,
    page_number: int,
    excerpt: str,
    step_id: str | None = None,
    source_label: str | None = None,
    target_claim_id: str | None = None,
    source_order: int = 0,
) -> ProtocolClaim:
    item = evidence(extraction, page_number, excerpt)
    return ProtocolClaim(
        claim_id=claim_id,
        category=category,
        source_order=source_order,
        source_text=item.source_excerpt,
        section_id="section-1" if step_id is not None else None,
        step_id=step_id,
        source_label=source_label,
        target_claim_id=target_claim_id,
        required_for_execution=True,
        evidence=item,
    )


def coverage(
    extraction: ProtocolPdfExtraction,
    page_number: int,
) -> ProtocolPageClaimCoverage:
    page_text = extraction.pages[page_number - 1].text
    return ProtocolPageClaimCoverage(
        source_revision=REVISION,
        source_sha256=extraction.sha256,
        source_page_number=page_number,
        page_text_sha256=hashlib.sha256(page_text.encode("utf-8")).hexdigest(),
        status=PageCoverageStatus.COMPLETE,
        evidence_item_ids=(),
    )


def analysis(
    extraction: ProtocolPdfExtraction,
    claims: tuple[ProtocolClaim, ...],
    page_numbers: tuple[int, ...] = (1,),
) -> ProtocolChunkClaimAnalysis:
    return ProtocolChunkClaimAnalysis(
        claim_schema_version=CLAIM_SCHEMA_VERSION,
        capability_policy_id=domain.P1_CAPABILITY_POLICY.profile_id,
        source_revision=REVISION,
        source_sha256=extraction.sha256,
        chunk_id="chunk-1",
        page_coverage=tuple(coverage(extraction, page) for page in page_numbers),
        structure=(),
        claims=claims,
    )


def codes(report) -> tuple[str, ...]:
    return tuple(finding.code.value for finding in report.findings)


class SemanticAuditDetectorTests(unittest.TestCase):
    """Each detector must fire on a real defect and stay silent otherwise."""

    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.directory = Path(self._directory.name)

    def test_value_token_detection_is_unit_driven_and_case_sensitive(self) -> None:
        tokens = detect_source_value_tokens(
            "Use a 4 mm screen with 25mM buffer at 65 degrees C for 30 min at 800 rpm.",
            source_page_number=1,
        )
        found = {(token.text, token.category) for token in tokens}
        self.assertIn(("4 mm", ClaimCategory.QUANTITY), found)
        self.assertIn(("25mM", ClaimCategory.CONCENTRATION), found)
        self.assertIn(("65 degrees C", ClaimCategory.TEMPERATURE), found)
        self.assertIn(("30 min", ClaimCategory.DURATION), found)
        self.assertIn(("800 rpm", ClaimCategory.AGITATION_SPEED), found)

    def test_value_detection_abstains_on_ambiguous_and_unitless_text(self) -> None:
        tokens = detect_source_value_tokens(
            "Collect 3 bags, add 5 M reagent, review 15m and lot 1050513.",
            source_page_number=1,
        )
        # Domain nouns, bare molar, and bare digit runs are all deliberately
        # out of scope: abstaining keeps the audit's error one-sided.
        self.assertEqual(tokens, ())

    def test_clean_claim_set_reports_no_findings(self) -> None:
        extraction = build_extraction(
            self.directory,
            ("1 Dry the sample at 65 degrees C for 3 h.",),
        )
        claims = (
            claim(
                extraction,
                claim_id="action-1",
                category=ClaimCategory.ACTION,
                page_number=1,
                excerpt="1 Dry the sample",
                step_id="step-1",
                source_label="1",
            ),
            claim(
                extraction,
                claim_id="temperature-1",
                category=ClaimCategory.TEMPERATURE,
                page_number=1,
                excerpt="65 degrees C",
                step_id="step-1",
                target_claim_id="action-1",
            ),
            claim(
                extraction,
                claim_id="duration-1",
                category=ClaimCategory.DURATION,
                page_number=1,
                excerpt="3 h",
                step_id="step-1",
                target_claim_id="action-1",
            ),
        )
        report = audit_chunk_semantics(extraction, analysis(extraction, claims))
        self.assertEqual(report.findings, ())
        self.assertTrue(report.is_semantically_clean)
        self.assertEqual(
            report.value_tokens_detected,
            report.value_tokens_represented,
        )

    def test_dropped_value_is_reported_with_counts(self) -> None:
        extraction = build_extraction(
            self.directory,
            ("1 Dry the sample at 65 degrees C for 3 h.",),
        )
        claims = (
            claim(
                extraction,
                claim_id="action-1",
                category=ClaimCategory.ACTION,
                page_number=1,
                excerpt="1 Dry the sample",
                step_id="step-1",
                source_label="1",
            ),
            claim(
                extraction,
                claim_id="duration-1",
                category=ClaimCategory.DURATION,
                page_number=1,
                excerpt="3 h",
                step_id="step-1",
                target_claim_id="action-1",
            ),
        )
        report = audit_chunk_semantics(extraction, analysis(extraction, claims))
        missing = [
            finding
            for finding in report.findings
            if finding.code is SemanticFindingCode.VALUE_NOT_REPRESENTED
        ]
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0].expected_category, ClaimCategory.TEMPERATURE)
        self.assertEqual(missing[0].expected_count, 1)
        self.assertEqual(missing[0].observed_count, 0)
        self.assertEqual(missing[0].severity, SemanticFindingSeverity.CRITICAL)
        self.assertFalse(report.is_semantically_clean)

    def test_claim_covering_several_values_is_reported_as_not_isolated(self) -> None:
        extraction = build_extraction(
            self.directory,
            ("1 Add 250 ml of water and 758 ml of acid.",),
        )
        claims = (
            claim(
                extraction,
                claim_id="action-1",
                category=ClaimCategory.ACTION,
                page_number=1,
                excerpt="1 Add 250 ml",
                step_id="step-1",
                source_label="1",
            ),
            claim(
                extraction,
                claim_id="quantity-1",
                category=ClaimCategory.QUANTITY,
                page_number=1,
                excerpt="250 ml",
                step_id="step-1",
                target_claim_id="action-1",
            ),
            claim(
                extraction,
                claim_id="quantity-2",
                category=ClaimCategory.QUANTITY,
                page_number=1,
                excerpt="758 ml",
                step_id="step-1",
                target_claim_id="action-1",
            ),
        )
        report = audit_chunk_semantics(extraction, analysis(extraction, claims))
        isolation = [
            finding
            for finding in report.findings
            if finding.code is SemanticFindingCode.VALUE_SPAN_NOT_ISOLATED
        ]
        # Both quantity claims resolve to the same whole-block evidence span,
        # so neither identifies which of the two volumes it asserts.
        self.assertEqual(len(isolation), 2)
        self.assertEqual({item.observed_count for item in isolation}, {2})
        self.assertEqual({item.expected_count for item in isolation}, {1})

    def test_surplus_claims_are_never_reported(self) -> None:
        """A surplus is indistinguishable from limited recall, so stay silent."""

        extraction = build_extraction(
            self.directory,
            ("1 Incubate for 3 h.",),
        )
        claims = (
            claim(
                extraction,
                claim_id="action-1",
                category=ClaimCategory.ACTION,
                page_number=1,
                excerpt="1 Incubate",
                step_id="step-1",
                source_label="1",
            ),
            claim(
                extraction,
                claim_id="duration-1",
                category=ClaimCategory.DURATION,
                page_number=1,
                excerpt="3 h",
                step_id="step-1",
                target_claim_id="action-1",
            ),
            claim(
                extraction,
                claim_id="duration-2",
                category=ClaimCategory.DURATION,
                page_number=1,
                excerpt="3 h",
                step_id="step-1",
                target_claim_id="action-1",
            ),
        )
        report = audit_chunk_semantics(extraction, analysis(extraction, claims))
        self.assertNotIn(
            SemanticFindingCode.VALUE_NOT_REPRESENTED.value,
            codes(report),
        )

    def test_spacing_and_case_variants_count_as_one_value(self) -> None:
        extraction = build_extraction(
            self.directory,
            ("1 Weigh 20 g then weigh 20g again.",),
        )
        claims = (
            claim(
                extraction,
                claim_id="action-1",
                category=ClaimCategory.ACTION,
                page_number=1,
                excerpt="1 Weigh 20 g",
                step_id="step-1",
                source_label="1",
            ),
            claim(
                extraction,
                claim_id="quantity-1",
                category=ClaimCategory.QUANTITY,
                page_number=1,
                excerpt="20 g",
                step_id="step-1",
                target_claim_id="action-1",
            ),
        )
        report = audit_chunk_semantics(extraction, analysis(extraction, claims))
        self.assertEqual(codes(report), ())

    def test_hazard_cue_without_a_warning_claim_is_reported(self) -> None:
        extraction = build_extraction(
            self.directory,
            ("1 Prepare the acid. Danger, highly corrosive.",),
        )
        claims = (
            claim(
                extraction,
                claim_id="action-1",
                category=ClaimCategory.ACTION,
                page_number=1,
                excerpt="1 Prepare the acid",
                step_id="step-1",
                source_label="1",
            ),
        )
        report = audit_chunk_semantics(extraction, analysis(extraction, claims))
        hazards = [
            finding
            for finding in report.findings
            if finding.code is SemanticFindingCode.HAZARD_NOT_REPRESENTED
        ]
        self.assertTrue(hazards)
        self.assertEqual(hazards[0].severity, SemanticFindingSeverity.CRITICAL)

    def test_hazard_cue_with_a_warning_claim_is_clean(self) -> None:
        extraction = build_extraction(
            self.directory,
            ("1 Prepare the acid. Danger, highly corrosive.",),
        )
        claims = (
            claim(
                extraction,
                claim_id="action-1",
                category=ClaimCategory.ACTION,
                page_number=1,
                excerpt="1 Prepare the acid",
                step_id="step-1",
                source_label="1",
            ),
            claim(
                extraction,
                claim_id="warning-1",
                category=ClaimCategory.WARNING_HAZARD,
                page_number=1,
                excerpt="Danger, highly corrosive",
                step_id="step-1",
                target_claim_id="action-1",
            ),
        )
        report = audit_chunk_semantics(extraction, analysis(extraction, claims))
        self.assertNotIn(
            SemanticFindingCode.HAZARD_NOT_REPRESENTED.value,
            codes(report),
        )

    def test_repeat_cue_without_a_repeat_claim_is_reported(self) -> None:
        extraction = build_extraction(
            self.directory,
            ("1 Lift the beaker approximately 30 times.",),
        )
        claims = (
            claim(
                extraction,
                claim_id="action-1",
                category=ClaimCategory.ACTION,
                page_number=1,
                excerpt="1 Lift the beaker",
                step_id="step-1",
                source_label="1",
            ),
        )
        report = audit_chunk_semantics(extraction, analysis(extraction, claims))
        self.assertIn(
            SemanticFindingCode.REPEAT_CONDITION_NOT_REPRESENTED.value,
            codes(report),
        )


class SemanticAuditReportingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.directory = Path(self._directory.name)

    def test_report_is_content_free_unless_excerpts_are_requested(self) -> None:
        extraction = build_extraction(
            self.directory,
            ("1 Dry the sample at 65 degrees C.",),
        )
        report = audit_chunk_semantics(extraction, analysis(extraction, ()))
        default = report.public_dict()
        self.assertTrue(default["findings"])
        for finding in default["findings"]:
            self.assertNotIn("source_excerpt", finding)
        opted_in = report.public_dict(include_source_excerpts=True)
        self.assertTrue(
            any(item["source_excerpt"] for item in opted_in["findings"])
        )

    def test_findings_are_bounded(self) -> None:
        page = "1 Warm the plate. " + " ".join(
            f"Hold at {index} degrees C." for index in range(1, MAX_AUDIT_FINDINGS + 40)
        )
        extraction = build_extraction(self.directory, (page,))
        report = audit_chunk_semantics(extraction, analysis(extraction, ()))
        self.assertEqual(len(report.findings), MAX_AUDIT_FINDINGS)
        self.assertTrue(report.findings_truncated)
        self.assertFalse(report.is_semantically_clean)

    def test_source_identity_mismatch_is_rejected(self) -> None:
        extraction = build_extraction(self.directory, ("1 Dry the sample.",))
        second = self.directory / "second"
        second.mkdir()
        other = build_extraction(second, ("1 Something else entirely.",))
        self.assertNotEqual(extraction.sha256, other.sha256)
        with self.assertRaises(ValueError):
            audit_chunk_semantics(other, analysis(extraction, ()))

    def test_merged_audit_covers_every_source_page(self) -> None:
        extraction = build_extraction(
            self.directory,
            ("1 Dry the sample at 65 degrees C.", "2 Cool the sample for 3 h."),
        )
        merged = MergedProtocolClaims(
            protocol_id="protocol-1",
            source_revision=REVISION,
            source_sha256=extraction.sha256,
            capability_policy_id=domain.P1_CAPABILITY_POLICY.profile_id,
            required_chunk_ids=("chunk-1",),
            page_coverage=tuple(
                coverage(extraction, page) for page in (1, 2)
            ),
            structure=(),
            claims=(),
        )
        report = audit_merged_semantics(extraction, merged)
        self.assertEqual(report.audited_page_numbers, (1, 2))
        self.assertEqual(
            {finding.source_page_number for finding in report.findings},
            {1, 2},
        )


class AssemblyPreservationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.directory = Path(self._directory.name)

    def _merged(self, extraction, claims):
        return MergedProtocolClaims(
            protocol_id="protocol-1",
            source_revision=REVISION,
            source_sha256=extraction.sha256,
            capability_policy_id=domain.P1_CAPABILITY_POLICY.profile_id,
            required_chunk_ids=("chunk-1",),
            page_coverage=(coverage(extraction, 1),),
            structure=(),
            claims=claims,
        )

    def test_claim_absent_from_the_assembled_protocol_is_reported(self) -> None:
        extraction = build_extraction(
            self.directory,
            ("1 Dry the sample at 65 degrees C.",),
        )
        claims = (
            claim(
                extraction,
                claim_id="action-1",
                category=ClaimCategory.ACTION,
                page_number=1,
                excerpt="1 Dry the sample",
                step_id="step-1",
                source_label="1",
            ),
            claim(
                extraction,
                claim_id="temperature-1",
                category=ClaimCategory.TEMPERATURE,
                page_number=1,
                excerpt="65 degrees C",
                step_id="step-1",
                target_claim_id="action-1",
            ),
        )

        class Action:
            action_id = "action-1"
            conditions = ()
            required_observations = ()
            warnings = ()
            missing_execution_values = ()
            process_timer = None

        class Step:
            sub_actions = (Action(),)

        class Section:
            steps = (Step(),)

        class Protocol:
            before_start = ()
            materials = ()
            equipment = ()
            sections = (Section(),)
            constructs = ()

        findings = audit_assembly_preservation(
            self._merged(extraction, claims),
            Protocol(),
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].claim_id, "temperature-1")
        self.assertEqual(
            findings[0].code,
            SemanticFindingCode.CLAIM_LOST_IN_ASSEMBLY,
        )

    def test_prefixed_domain_identifier_counts_as_surfaced(self) -> None:
        extraction = build_extraction(
            self.directory,
            ("1 Dry the sample at 65 degrees C.",),
        )
        claims = (
            claim(
                extraction,
                claim_id="temperature-1",
                category=ClaimCategory.TEMPERATURE,
                page_number=1,
                excerpt="65 degrees C",
                step_id="step-1",
                target_claim_id="action-1",
            ),
        )

        class Statement:
            statement_id = "parameter-temperature-1"

        class Action:
            action_id = "action-1"
            conditions = (Statement(),)
            required_observations = ()
            warnings = ()
            missing_execution_values = ()
            process_timer = None

        class Step:
            sub_actions = (Action(),)

        class Section:
            steps = (Step(),)

        class Protocol:
            before_start = ()
            materials = ()
            equipment = ()
            sections = (Section(),)
            constructs = ()

        self.assertEqual(
            audit_assembly_preservation(
                self._merged(extraction, claims), Protocol()
            ),
            (),
        )


class SemanticAuditSoundnessTests(unittest.TestCase):
    """The audit must measure, never fabricate, and never gate admission."""

    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.directory = Path(self._directory.name)

    def test_findings_never_quote_text_absent_from_the_source(self) -> None:
        page = (
            "1 Add 250 ml of water at 65 degrees C for 3 h at 800 rpm. "
            "Danger, corrosive. Repeat 3 times."
        )
        extraction = build_extraction(self.directory, (page,))
        report = audit_chunk_semantics(extraction, analysis(extraction, ()))
        self.assertTrue(report.findings)
        source = extraction.pages[0].text
        for finding in report.findings:
            for quoted in finding.source_excerpt.split(", "):
                self.assertIn(quoted, source)

    def test_audit_leaves_the_claim_set_untouched(self) -> None:
        extraction = build_extraction(
            self.directory,
            ("1 Dry the sample at 65 degrees C.",),
        )
        claims = (
            claim(
                extraction,
                claim_id="action-1",
                category=ClaimCategory.ACTION,
                page_number=1,
                excerpt="1 Dry the sample",
                step_id="step-1",
                source_label="1",
            ),
        )
        subject = analysis(extraction, claims)
        before = subject.public_dict()
        report = audit_chunk_semantics(extraction, subject)
        self.assertTrue(report.findings)
        self.assertEqual(subject.public_dict(), before)

    def test_semantic_findings_do_not_block_canonical_admission(self) -> None:
        """Canonical validation stays the only admission authority."""

        extraction = build_extraction(
            self.directory,
            ("1 Prepare the acid at 65 degrees C. Danger, corrosive.",),
        )
        claims = (
            claim(
                extraction,
                claim_id="action-1",
                category=ClaimCategory.ACTION,
                page_number=1,
                excerpt="1 Prepare the acid",
                step_id="step-1",
                source_label="1",
            ),
        )
        subject = analysis(extraction, claims)
        report = audit_chunk_semantics(extraction, subject)
        self.assertTrue(report.critical_findings)
        # The audited object is unchanged and still canonically well-formed:
        # nothing in this module participates in admission.
        self.assertEqual(subject.claims, claims)
        self.assertEqual(subject.claim_schema_version, CLAIM_SCHEMA_VERSION)


class ClockDurationCensusTests(unittest.TestCase):
    """protocols.io renders step timers as HH:MM:SS, which carry no unit word."""

    def test_clock_durations_are_counted_as_values(self) -> None:
        tokens = detect_source_value_tokens(
            "Incubate for 00:15:00 then dry for 03:00:00.",
            source_page_number=1,
        )
        self.assertEqual(
            {(token.text, token.category) for token in tokens},
            {
                ("00:15:00", ClaimCategory.DURATION),
                ("03:00:00", ClaimCategory.DURATION),
            },
        )

    def test_a_ratio_is_not_mistaken_for_a_timer(self) -> None:
        """(50:49:1) is a solvent ratio; its last group is a single digit."""

        tokens = detect_source_value_tokens(
            "water:Acetonitrile:formic acid (50:49:1) at 37 degrees C",
            source_page_number=1,
        )
        self.assertNotIn(
            ClaimCategory.DURATION, {token.category for token in tokens}
        )

    def test_a_doi_url_yields_no_values(self) -> None:
        self.assertEqual(
            detect_source_value_tokens(
                "protocols.io | https://dx.doi.org/10.17504/protocols.io.yinfude",
                source_page_number=1,
            ),
            (),
        )

    def test_a_dropped_timer_is_now_visible_to_the_audit(self) -> None:
        """The blind spot: 8 duration claims vanished while the count held."""

        page = "1 Incubate the plate. 00:15:00"
        self.assertTrue(
            [
                token
                for token in detect_source_value_tokens(page, source_page_number=1)
                if token.category is ClaimCategory.DURATION
            ]
        )


class NoParserCorruptionInSourcesTests(unittest.TestCase):
    """A hardcoded private-use glyph can only have come from a broken parser.

    It matches text that no document contains, so any pattern carrying one is
    scoring corrupted extraction rather than the source.  This is a guard
    against new occurrences plus an explicit register of the ones that remain.
    """

    # Files permitted to contain a private-use glyph, and why.
    KNOWN = {
        # Deliberate: these assert that the cross-check census *detects*
        # corruption, so they must embed a corrupted sample.
        "tests/test_extraction_cross_check.py",
        # Deliberate: asserts the browser normalizer repairs legacy text.
        "tests/test_frontend.py",
        # DEBT, pypdf-era corruption compensation, to be removed in STEP 3:
        # three timer patterns accept pypdf's colon substitute alongside a real
        # colon, and one presentation helper rewrites that glyph into a colon.
        # With the current extractor they are dead paths at best; if corrupted
        # text ever reappeared they would quietly normalize it and defeat the
        # extraction cross-check.
        "src/voice_workflow_agent/brain.py",
        "src/voice_workflow_agent/multi_brain.py",
        "src/voice_workflow_agent/curated_protocol.py",
    }

    def test_only_known_files_contain_private_use_glyphs(self) -> None:
        found = set()
        for root in (Path("src"), Path("scripts"), Path("tests")):
            if not root.exists():
                continue
            for path in sorted(root.rglob("*.py")):
                text = path.read_text(encoding="utf-8")
                if any(0xE000 <= ord(character) <= 0xF8FF for character in text):
                    found.add(path.as_posix())
        self.assertEqual(
            found - self.KNOWN,
            set(),
            "a new hardcoded private-use glyph appeared; it can only have come "
            "from a broken extraction",
        )
        self.assertEqual(
            self.KNOWN - found,
            set(),
            "a known private-use glyph is gone; remove it from KNOWN so the "
            "register stays accurate",
        )

    def test_the_scoring_tools_are_clean(self) -> None:
        """The scorer must model a faithful provider on canonical text."""

        for path in sorted(Path("scripts").rglob("*.py")):
            with self.subTest(path=path.as_posix()):
                text = path.read_text(encoding="utf-8")
                self.assertEqual(
                    [c for c in text if 0xE000 <= ord(c) <= 0xF8FF], []
                )



if __name__ == "__main__":
    unittest.main()
