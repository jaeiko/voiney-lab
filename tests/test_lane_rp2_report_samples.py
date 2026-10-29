"""The three sample reports (lane RP2, decision 4, 2026-10-19).

Human decision: three fake experiment records -- a short run, a run with
deviations, an anomaly and a lost connection, and a run with values left
unconfirmed -- are made into Word and PDF reports and kept as samples under
~/reports/rp2/. This checks the samples are what the report says of those
records; LibreOffice is faked unless installed here.
"""

from __future__ import annotations

import io
import unittest
from pathlib import Path

from voiney_lab.experiment_protocol_pdf import find_office_converter

from tests.lane_rp2_support import SAMPLES, environment, temporary, write_samples
from tests.test_lane_rp2_report_pdf import _FakeOffice


class SampleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = temporary()
        self.addCleanup(self.temporary.cleanup)
        self.env = environment()
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_three_records_each_as_word_pdf_and_markdown(self) -> None:
        from docx import Document

        written = write_samples(Path(self.temporary.name), converter="/usr/bin/soffice", run=_FakeOffice())
        self.assertEqual([path.name for path in written], [
            f"{name}.{suffix}" for name in SAMPLES for suffix in ("docx", "pdf", "md")])
        texts = {path.stem: path.read_text(encoding="utf-8") for path in written if path.suffix == ".md"}
        short, deviation, unconfirmed = (texts[name] for name in SAMPLES)
        # Lane RP3, decision ①: the notebook's header says how far the run got.
        self.assertIn("| 진행 | 끝까지 완료 · 2 / 2단계 |", short)
        self.assertIn("| 진행 | 끝까지 완료 · 5 / 5단계 |", deviation)
        self.assertIn("| 진행 | 중단 — 4단계에서 · 3 / 5단계 |", unconfirmed)
        self.assertIn("> **확인 안 된 값 2개**", unconfirmed)
        self.assertNotIn("확인 안 된 값", short + deviation)
        for path in written:
            if path.suffix == ".docx":
                paragraphs = [p.text for p in Document(io.BytesIO(path.read_bytes())).paragraphs]
                self.assertIn("실험 정보", paragraphs, path.name)

    @unittest.skipUnless(find_office_converter(), "LibreOffice is not installed here")
    def test_the_pdfs_print_here(self) -> None:
        import fitz

        written = write_samples(Path(self.temporary.name))
        for path in written:
            if path.suffix == ".pdf":
                first = fitz.open(path)[0].get_text()
                self.assertIn("실험 노트", first, path.name)
                self.assertIn("실험 정보", first, path.name)


if __name__ == "__main__":
    unittest.main()
