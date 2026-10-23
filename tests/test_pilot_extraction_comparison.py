"""Offline comparisons exercise the current parser and the command boundary."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from tests.test_extraction_accuracy import _protocol
from tests.test_lane_rv_review_pending import write_pages_pdf
from voiney_lab import experiment_protocol as domain
from voiney_lab.experiment_protocol_pdf import extract_protocol_pdf
from voiney_lab.protocol_extraction_accuracy import (
    normalize, score_extraction, source_record_differences,
)

ROOT = Path(__file__).resolve().parents[1]


class ComparisonIdentityTests(unittest.TestCase):
    def test_hangul_negation_is_not_removed_from_the_comparison(self) -> None:
        self.assertNotEqual(normalize("가열하지 않는다."), normalize("가열한다."))
        left = _protocol((("1", "가열하지 않는다."),))
        right = _protocol((("1", "가열한다."),))
        self.assertLess(score_extraction(left, right).mean_text_similarity, 1)

    def test_an_omitted_unnumbered_step_does_not_shift_the_remaining_matches(self) -> None:
        left = _protocol((("", "Open the tube."), ("", "Close the tube.")))
        right = _protocol((("", "Close the tube."),))
        report = score_extraction(left, right)
        self.assertEqual(len(report.missing_labels), 1)
        self.assertEqual(len(report.compared), 1)
        self.assertEqual(report.mean_text_similarity, 1)

    def test_unnumbered_reordering_is_detected_from_source_quotes(self) -> None:
        steps = (("", "Open the tube."), ("", "Close the tube."))
        report = score_extraction(_protocol(steps), _protocol(tuple(reversed(steps))))
        self.assertFalse(report.order_matches)
        self.assertEqual(report.missing_labels, ())

    def test_repeated_labels_and_repeated_quotes_are_never_collapsed(self) -> None:
        steps = (("1", "Read the label."), ("1", "Read the label."))
        report = score_extraction(_protocol(steps), _protocol(steps[:1]))
        self.assertEqual(len(report.missing_labels), 1)
        self.assertEqual(len(report.compared), 1)
        self.assertEqual(report.mean_text_similarity, 1)

    def test_repeated_labels_on_different_pages_have_separate_addresses(self) -> None:
        first = _protocol((("1", "Open the tube."),))
        next_section = _protocol((("1", "Open the tube."),), page=2).sections[0]
        reference = replace(first, sections=(*first.sections, next_section))
        report = score_extraction(reference, _protocol((("1", "Open the tube."),), page=2))
        self.assertEqual(len(report.missing_labels), 1)
        self.assertTrue(report.missing_labels[0].startswith("p1:"))
        self.assertTrue(report.compared[0].source_key.startswith("p2:"))

    def test_source_record_counts_ignore_model_assigned_ids(self) -> None:
        left = _protocol((("1", "Open the tube."),))
        section = left.sections[0]
        right = replace(left, protocol_id="other", sections=(replace(
            section, section_id="other-section", steps=(replace(section.steps[0], step_id="other-step"),)),))
        differences = source_record_differences(left, right)
        self.assertEqual(differences["in_reference_only"], [])
        self.assertEqual(differences["in_candidate_only"], [])

    def test_omitted_source_statements_and_values_are_counted_from_the_typed_structure(self) -> None:
        plain = _protocol((("1", "Add water."),))
        section = plain.sections[0]
        step = section.steps[0]
        warning = domain.SourceStatement("w", "Synthetic caution.", step.evidence)
        action = domain.ProtocolSubAction(
            "action", "Add water.", step.evidence,
            quantities=(domain.ScientificValue("500 µL"),))
        left = replace(plain, sections=(replace(section, steps=(replace(
            step, warnings=(warning,), sub_actions=(action,)),)),))
        differences = source_record_differences(left, plain)
        missing = {item["kind"] for item in differences["in_reference_only"]}
        self.assertTrue({"SourceStatement", "ScientificValue", "ProtocolSubAction"} <= missing)


class SavedAnalysisCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source.pdf"
        self.lines = ["Synthetic protocol", "1. Add 500 uL water.", "2. Close the tube.",
                      "3. Label the tube.", "4. Record the label."]
        write_pages_pdf(self.source, [self.lines], title="Synthetic protocol")
        self.sha = extract_protocol_pdf(self.source).sha256
        self.reference = self.root / "reference.json"
        self.candidate = self.root / "candidate.json"
        self.reference.write_text(json.dumps(self.response(4)), encoding="utf-8")

    def response(self, count: int) -> dict:
        evidence = lambda text: {"source_page_number": 1, "source_excerpt": text}
        return {
            "analysis_schema_version": 1, "pdf_sha256": self.sha,
            "capability_policy_id": "p1-conservative",
            "protocol": {
                "protocol_id": "synthetic", "metadata": {
                    "title": self.lines[0], "original_language": "en", "evidence": evidence(self.lines[0])},
                "sections": [{"section_id": "section", "title_source_text": self.lines[0],
                              "evidence": evidence(self.lines[0]), "steps": [
                                  {"step_id": f"s{i}", "source_label": str(i),
                                   "instruction_source_text": self.lines[i], "evidence": evidence(self.lines[i])}
                                  for i in range(1, count + 1)]}],
            },
        }

    def run_score(self, payload: dict, *extra: str) -> tuple[int, dict]:
        self.candidate.write_text(json.dumps(payload), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/score_extraction.py"), str(self.source),
             "--reference", str(self.reference), "--candidate", str(self.candidate), *extra],
            cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertTrue(result.stdout.strip(), result.stderr)
        return result.returncode, json.loads(result.stdout)

    def test_short_document_omissions_are_visible_without_an_execution_verdict(self) -> None:
        code, result = self.run_score(self.response(1))
        self.assertEqual(code, 0)
        self.assertEqual(result["missing_labels"], ["2", "3", "4"])
        self.assertEqual(result["source_checks"]["candidate_items_for_review"], 0)
        self.assertEqual(result["standing"], "regression_baseline_not_absolute_accuracy")
        self.assertTrue(result["source_records"]["in_reference_only"])
        for forbidden in ("readiness", "available_for_execution", "passed"):
            self.assertNotIn(forbidden, result)

    def test_source_identity_mismatch_fails_without_echoing_document_content(self) -> None:
        payload = self.response(4)
        payload["pdf_sha256"] = "0" * 64
        code, result = self.run_score(payload)
        self.assertEqual(code, 2)
        self.assertFalse(result["scored"])
        self.assertEqual(set(result), {"scored", "reason"})

    def test_wrong_value_has_a_separate_evidence_finding_and_value_difference(self) -> None:
        payload = self.response(4)
        payload["protocol"]["sections"][0]["steps"][0]["instruction_source_text"] = "1. Add 50 uL water."
        code, result = self.run_score(payload)
        self.assertEqual(code, 0)
        self.assertGreater(result["source_checks"]["candidate_items_for_review"], 0)
        self.assertEqual(result["steps_with_contradicted_values"], 1)

    def test_output_never_overwrites_a_saved_analysis(self) -> None:
        original = self.reference.read_bytes()
        code, result = self.run_score(self.response(4), "--output", str(self.reference))
        self.assertEqual(code, 2)
        self.assertFalse(result["scored"])
        self.assertEqual(self.reference.read_bytes(), original)

    def test_corrupt_source_is_reported_before_scoring(self) -> None:
        self.source.write_bytes(b"not a PDF")
        code, result = self.run_score(self.response(4))
        self.assertEqual(code, 2)
        self.assertFalse(result["scored"])
