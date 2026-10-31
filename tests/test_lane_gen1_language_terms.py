"""GEN1: with no protocol, terms are admitted by their form, not in-gel's list.

Before a protocol is attached the transcript's Latin words were checked
against in-gel's reagents ("ambic", "acetonitrile", "evotip", "thermomixer").
Now any acronym written in capitals is a term, as are general laboratory
nouns, units and technique names; one document's reagents are not.
"""

from __future__ import annotations

import unittest

from voiney_lab.language import classify_korean_admission


class NoProtocolTermTests(unittest.TestCase):
    def test_any_acronym_is_a_term(self) -> None:
        for text in ("BCA", "PBS", "HPLC water", "TEMED", "MOF-5"):
            with self.subTest(text=text):
                admission = classify_korean_admission(text, "en")
                self.assertIsNone(admission.mismatch_status)
                self.assertFalse(admission.clarification_required)

    def test_one_documents_reagents_are_no_longer_listed(self) -> None:
        # Two words of in-gel's old list: neither a general word nor an
        # acronym by its form, so they are read as any other Latin words.
        admission = classify_korean_admission("evotip thermomixer", "en")
        self.assertIsNotNone(admission.mismatch_status)


if __name__ == "__main__":
    unittest.main()
