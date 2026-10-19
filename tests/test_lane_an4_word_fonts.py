"""A Word document is printed in the installed fonts nearest the Office ones it names (lane AN4, 3).

Human decision 2026-10-19 (3): each Word failure was sorted into conversion,
response format or evidence, and what the conversion caused fixed. One was the
conversion's: this server has no Calibri or Cambria, LibreOffice took
fontconfig's default DejaVu, far wider, and zenodo_17293605's page header --
printed on every page -- broke its date "31.07.202" / "5" in a narrow cell,
so the page's running lines were no longer told from its body and its steps
running past a page end were refused. A document without Hangul is now
printed with the Office fonts pointed at Carlito/Caladea when installed and
the Liberation fonts otherwise; a Korean document keeps lane AN's Nanum rules.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_lane_an_source_formats import FakeOffice, docx_bytes
from voiney_lab import experiment_protocol_pdf as pdf_module
from voiney_lab.experiment_protocol_pdf import docx_to_pdf


def _font_installed(family: str) -> bool:
    try:
        found = subprocess.run(["fc-list", ":", "family"], capture_output=True, timeout=10, check=False)
    except OSError:
        return False
    return family.encode() in found.stdout


class OfficeFontTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def convert(self, text: str) -> str:
        office = FakeOffice()
        source = self.root / "d.docx"
        source.write_bytes(docx_bytes(text))
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FONTCONFIG_FILE", None)
            docx_to_pdf(source, self.root / "d.pdf", converter="/usr/bin/soffice", run=office)
        return office.environments[0]["fonts.conf"]

    def test_an_english_document_names_the_nearest_fonts_for_the_office_ones(self) -> None:
        conf = self.convert("1. Add 5 mL buffer.")
        self.assertIn('<include ignore_missing="yes">/etc/fonts/fonts.conf</include>', conf)
        for family in ("Calibri", "Calibri Light", "Aptos", "Segoe UI", "Tahoma"):
            self.assertIn(f"<family>{family}</family><prefer><family>Carlito</family>"
                          "<family>Liberation Sans</family></prefer>", conf)
        self.assertIn("<family>Cambria</family><prefer><family>Caladea</family>"
                      "<family>Liberation Serif</family></prefer>", conf)
        self.assertIn("<family>Consolas</family><prefer><family>Liberation Mono</family></prefer>", conf)
        # Fonts the system already resolves (Arial, Times New Roman to the
        # Liberation fonts) and the generic families are left as they are.
        self.assertNotIn("<family>sans-serif</family>", conf)
        self.assertNotIn("Nanum", conf)

    def test_a_korean_document_keeps_the_korean_fonts_only(self) -> None:
        conf = self.convert("1. 시료 2 g 을 50 mL 원심분리관에 취한다.")
        self.assertIn("<family>sans-serif</family><prefer><family>NanumGothic</family>", conf)
        self.assertNotIn("Calibri", conf)
        self.assertNotIn("Liberation", conf)

    def test_a_fontconfig_already_set_is_included_not_replaced(self) -> None:
        office = FakeOffice()
        source = self.root / "e.docx"
        source.write_bytes(docx_bytes("1. Add 5 mL buffer."))
        with patch.dict(os.environ, {"FONTCONFIG_FILE": "/opt/fonts/site.conf"}):
            docx_to_pdf(source, self.root / "e.pdf", converter="/usr/bin/soffice", run=office)
        self.assertIn('<include ignore_missing="yes">/opt/fonts/site.conf</include>',
                      office.environments[0]["fonts.conf"])

    @unittest.skipUnless(
        pdf_module.find_office_converter() is not None and _font_installed("Liberation Sans"),
        "LibreOffice and the Liberation fonts are not installed here")
    def test_a_calibri_document_is_printed_in_carlito_or_liberation_not_dejavu(self) -> None:
        import docx as python_docx
        import fitz

        document = python_docx.Document()
        document.styles["Normal"].font.name = "Calibri"
        document.add_paragraph("1. Add 5 mL of buffer and incubate for 10 min at 37 °C.", style="List Number")
        source = self.root / "calibri.docx"
        document.save(source)
        docx_to_pdf(source, self.root / "calibri.pdf")
        fonts = {font[3].split("+")[-1] for page in fitz.open(self.root / "calibri.pdf")
                 for font in page.get_fonts()}
        self.assertTrue(any(name.startswith(("Carlito", "LiberationSans")) for name in fonts), fonts)
        self.assertFalse(any(name.startswith("DejaVuSans") for name in fonts), fonts)


if __name__ == "__main__":
    unittest.main()
