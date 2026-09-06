"""The scoring tool, checked without running a score.

Nothing here reaches a provider, and the tool itself cannot: there is no
client in it and no --execute flag. What is pinned is the behaviour that
matters before the first real score exists -- that an incomplete cache is
refused with the arithmetic stated rather than quietly scored on three fifths
of a document, and that the two comparisons the existing accuracy module does
not cover actually work.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IN_GEL = ROOT / "data" / "runtime" / "candidate-a-source" / "in-gel-digestion.pdf"
REFERENCE = (
    ROOT / "data" / "development_protocols" / "candidate_a_curated_analysis.json"
)
PROVENANCE = REFERENCE.with_name(
    "candidate_a_curated_analysis.provenance.json"
)
sys.path.insert(0, str(ROOT / "scripts"))


def _labels(protocol):
    return {
        step.step_id: step.source_label
        for section in protocol.sections
        for step in section.steps
    }


class ScoringToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not IN_GEL.is_file() or not REFERENCE.is_file():
            raise unittest.SkipTest("the local source or reference is absent.")
        from voice_workflow_agent.curated_protocol import (
            load_curated_protocol_fixture,
        )

        from tests.test_pdf_to_session_walkthrough import _pipeline

        cls.extraction, cls.plan, cls.merged, cls.draft = _pipeline()
        cls.reference = load_curated_protocol_fixture(
            REFERENCE, PROVENANCE, IN_GEL
        ).draft.protocol

    def test_the_tool_sends_nothing_and_cannot(self) -> None:
        """No client, no execute flag, no key -- checked as code, not prose.

        Matching the bare word would catch this file's own docstring
        explaining that it has no such flag, so what is checked is the flag
        being *defined* and the client being *imported*.
        """

        body = (ROOT / "scripts" / "score_extraction.py").read_text()
        for forbidden in (
            'add_argument("--execute"',
            "from openai",
            "import openai",
            "OpenAI(",
            "chat.completions",
            "load_dotenv",
            'environ.get("XAI',
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, body)

    def test_an_incomplete_cache_is_refused_with_the_count(self) -> None:
        import json
        import subprocess

        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "score_extraction.py"),
                str(IN_GEL),
                "--reference",
                str(REFERENCE),
                "--provenance",
                str(PROVENANCE),
                "--cache-dir",
                str(ROOT / "data" / "development_cache" / "does-not-exist"),
            ],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
        )
        self.assertEqual(completed.returncode, 1)
        payload = json.loads(completed.stdout)
        self.assertFalse(payload["scored"])
        self.assertEqual(payload["reason"], "cache_incomplete")
        self.assertEqual(payload["chunks_cached"], 0)
        # The arithmetic a person needs, not just a complaint.
        self.assertEqual(
            payload["provider_calls_needed"], len(payload["missing_ordinals"])
        )
        self.assertEqual(payload["missing_ordinals"], [0, 1, 2, 3, 4])

    def test_repetition_comparison_matches_on_the_steps_repeated(self) -> None:
        """Identity is each side's own; the range is what an operator runs."""

        from score_extraction import _repeat_comparison, _repeats

        comparison = _repeat_comparison(
            self.reference,
            self.draft.protocol,
            _labels(self.reference),
            _labels(self.draft.protocol),
        )
        self.assertEqual(comparison["reference_count"], 2)
        matched = {
            tuple(item["reference"]["labels"]) for item in comparison["matched"]
        }
        self.assertIn(("2", "3", "4", "5", "6", "7"), matched)

        # And it reports each side's surplus rather than averaging them away.
        self.assertIn("in_reference_only", comparison)
        self.assertIn("in_candidate_only", comparison)
        self.assertEqual(
            len(_repeats(self.reference, _labels(self.reference))), 2
        )

    def test_a_broken_evidence_address_is_caught(self) -> None:
        from dataclasses import replace

        from score_extraction import _evidence_addresses_resolve

        clean = _evidence_addresses_resolve(
            self.extraction, "pdf-1", self.draft.protocol
        )
        self.assertGreater(clean["addresses_checked"], 0)
        self.assertTrue(clean["all_resolve"], clean["offenders"])

        # Break exactly one address and it must be found and named.
        sections = list(self.draft.protocol.sections)
        steps = list(sections[0].steps)
        steps[0] = replace(
            steps[0],
            evidence=replace(
                steps[0].evidence, evidence_segment_ids=("seg-" + "f" * 64,)
            ),
        )
        sections[0] = replace(sections[0], steps=tuple(steps))
        damaged = replace(self.draft.protocol, sections=tuple(sections))

        found = _evidence_addresses_resolve(self.extraction, "pdf-1", damaged)
        self.assertFalse(found["all_resolve"])
        self.assertEqual(found["addresses_unresolved"], 1)
        self.assertEqual(found["offenders"][0]["segment_id"], "seg-" + "f" * 64)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
