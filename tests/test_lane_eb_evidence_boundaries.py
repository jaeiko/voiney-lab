"""Lane EB: where a match stands on its page (human decision 2026-10-09).

Lane EV found that the existing exact comparison reads a claim or a quote as
a substring of its page: the claim "5 mL buffer" was supported by "Add 0.5 mL
buffer.", "20 °C" by "Store at −20 °C.", and the quote "allow sample to go to
dryness." by "Do not allow sample to go to dryness.". A match is now evidence
only where it stands on its page as what it says: not part of a longer number
(a), not starting or ending inside an English word or a number, nor ending
inside a Korean word right before a negative ending (b), and not right after a
negation or right before a Korean negative ending the claim leaves out (c).
Page text, its hash, stored excerpts and evidence identities are untouched.
"""

from __future__ import annotations

import json
import unittest

from voiney_lab import experiment_protocol as domain
from voiney_lab import experiment_protocol_analysis as analysis_module
from voiney_lab.experiment_protocol_analysis import ProtocolAnalysisEvidenceError
from voiney_lab.experiment_protocol_pdf import (
    ProtocolPdfExtraction,
    ProtocolPdfMetadata,
    ProtocolPdfPage,
)

TITLE = "Protocol Alpha"
STEP = "1. Add water."


def extraction(*pages: str) -> ProtocolPdfExtraction:
    return ProtocolPdfExtraction(
        original_filename="synthetic.pdf",
        byte_size=1,
        sha256="e" * 64,
        media_type="application/pdf",
        page_count=len(pages),
        encrypted=False,
        metadata=ProtocolPdfMetadata(None, None, None, None, None, None, None),
        pages=tuple(
            ProtocolPdfPage(number, text, False)
            for number, text in enumerate(pages, start=1)
        ),
    )


def evidence(page: int, excerpt: str) -> dict:
    return {"source_page_number": page, "source_excerpt": excerpt}


def step(label: str, text: str, excerpt: str | None = None, **parts) -> dict:
    return {
        "step_id": f"step-{label or 'unlabelled'}",
        "source_label": label,
        "instruction_source_text": text,
        "evidence": evidence(1, text if excerpt is None else excerpt),
        "sub_actions": [],
        **parts,
    }


def material(name: str, excerpt: str, *quantities: str) -> dict:
    return {
        "material_id": "material-1",
        "name_source_text": name,
        "evidence": evidence(1, excerpt),
        "quantities": [{"source_text": quantity} for quantity in quantities],
    }


def parse(source: ProtocolPdfExtraction, *, steps=None, materials=None, metadata=None):
    protocol: dict = {
        "protocol_id": "protocol-alpha",
        "metadata": {
            "title": TITLE,
            "original_language": "en",
            "evidence": evidence(1, TITLE),
            **(metadata or {}),
        },
        "sections": [
            {
                "section_id": "section-1",
                "title_source_text": TITLE,
                "evidence": evidence(1, TITLE),
                "steps": steps if steps is not None else [step("1", STEP)],
            }
        ],
    }
    if materials is not None:
        protocol["materials"] = materials
    return analysis_module.parse_protocol_analysis_response(
        json.dumps(
            {
                "analysis_schema_version": 1,
                "pdf_sha256": source.sha256,
                "capability_policy_id": "p1-conservative",
                "protocol": protocol,
            }
        ),
        source,
    )


def page(*lines: str) -> ProtocolPdfExtraction:
    return extraction("\n".join((TITLE, *lines, STEP)))


class BoundaryAssertions(unittest.TestCase):
    def assert_refused(self, source, reason_code: str, **parts) -> None:
        with self.assertRaises(ProtocolAnalysisEvidenceError) as raised:
            parse(source, **parts)
        self.assertEqual(raised.exception.diagnostic.reason_code, reason_code)


class LaneEvExamplesTests(BoundaryAssertions):
    """The three cases lane EV found the existing exact comparison accepts."""

    def test_a_claim_inside_a_decimal_is_refused(self):
        line = "Add 0.5 mL buffer."
        self.assert_refused(
            page(line), "claim_not_found", materials=[material("buffer", line, "5 mL buffer")]
        )

    def test_a_claim_without_its_minus_sign_is_refused(self):
        line = "Antibody stock: store at −20 °C."
        self.assert_refused(
            page(line), "claim_not_found", materials=[material("Antibody stock", line, "20 °C")]
        )

    def test_a_quote_without_its_negation_is_refused(self):
        source = extraction(f"{TITLE}\nDo not allow sample to go to dryness.")
        self.assert_refused(
            source, "quote_not_found", steps=[step("", "allow sample to go to dryness.")]
        )


if __name__ == "__main__":
    unittest.main()
