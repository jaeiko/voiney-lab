"""Offline evidence check for three short, publicly licensed Methods excerpts.

The excerpts are each under 25 words and keep the publisher's wording. This is
contract testing only: it exercises deterministic text-to-PDF conversion and
source offsets, not a live analysis provider.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from voiney_lab.experiment_protocol_pdf import convert_source_to_pdf, extract_protocol_pdf
from voiney_lab.start_check import step_changeable_values


PUBLIC_METHODS = (
    {
        "field": "bio",
        "doi": "10.1371/journal.pone.0047623",
        "license": "CC BY",
        "source": "https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0047623",
        "text": "Sperm were incubated for 10 min at 37°C in 5% CO2 to allow them to swim out and disperse into the media.",
        "conditions": (("time", "10 min"), ("temperature", "37°C"), ("concentration", "5%")),
    },
    {
        "field": "chemistry",
        "doi": "10.1038/s41467-023-38741-w",
        "license": "CC BY 4.0",
        "source": "https://www.nature.com/articles/s41467-023-38741-w",
        "text": "After EtOH (0.5 mL) was injected, the reaction mixture was stirred at 80 °C for 12 h (600 rpm).",
        "conditions": (("volume", "0.5 mL"), ("temperature", "80 °C"), ("time", "12 h"), ("speed", "600 rpm")),
    },
    {
        "field": "materials",
        "doi": "10.1038/s41535-020-0222-8",
        "license": "CC BY 4.0",
        "source": "https://www.nature.com/articles/s41535-020-0222-8",
        "text": "It was held at 80 °C for 24 h before being cooled to room temperature over 12 h.",
        "conditions": (("temperature", "80 °C"), ("time", "24 h"), ("time", "12 h")),
    },
)


class PublicMethodsEvidenceTests(unittest.TestCase):
    def test_every_detected_condition_is_linked_to_its_exact_source_span_and_none_is_invented(self) -> None:
        with tempfile.TemporaryDirectory(prefix="voiney-paper1-") as directory:
            root = Path(directory)
            for index, case in enumerate(PUBLIC_METHODS, start=1):
                with self.subTest(field=case["field"], doi=case["doi"]):
                    source = root / f"methods-{index}.txt"
                    pdf = root / f"methods-{index}.pdf"
                    source.write_text(case["text"], encoding="utf-8")
                    conversion = convert_source_to_pdf(source, "text/plain", pdf)
                    extraction = extract_protocol_pdf(pdf)

                    self.assertEqual(conversion.converter, "voiney-methods-text-v1")
                    self.assertEqual(extraction.page_count, 1)
                    self.assertFalse(extraction.pages[0].ocr_required)
                    page = extraction.pages[0].text
                    self.assertIn(case["text"], " ".join(page.split()))

                    found = step_changeable_values("paper-step", "1", page)
                    self.assertEqual(tuple((item.kind, item.written) for item in found), case["conditions"])
                    for item in found:
                        self.assertEqual(page[item.start:item.end], item.written)


if __name__ == "__main__":
    unittest.main()
