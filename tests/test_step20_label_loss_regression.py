"""The six-step loss from the first real use, fixed as input.

STEP 20, headspace chunk 2, pages 6-8: a model returned page coverage
disposing of labels 22, 30, 31, 32, 33, 34 as "not execution steps". Every one
is an imperative carrying a temperature, a time or a piece of equipment:

    p6 22  Incubate the Petri dishes in total darkness (48 h at 28 C).
    p7 30  Attach the charcoal filter tube to a supply of purified nitrogen
    p7 31  Check for a flow of nitrogen by attaching a Teflon tube
    p7 32  Place the charcoal filter into a modified heating oven at 170 C
    p8 33  After 2 hours, remove the charcoal filter from the oven
    p8 34  Once the charcoal filters have cooled, turn off the nitrogen supply

Had that response been accepted and approved, the protocol would have been
missing six steps with nothing in the extraction saying so. The tests that
asserted the accident is no longer expressible went with the claim-chunk path
(lane AN, 2026-10-13); what is left checks that the trigger still reads each of
the six labels on its page.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from voiney_lab.experiment_protocol_pdf import extract_protocol_pdf
from voiney_lab.protocol_claim_analysis import _numbered_action_matches

HEADSPACE = Path("usingdynamicheadspacecollections.pdf")
HEADSPACE_SHA256 = (
    "2bf102779364ec2dad517efc5acff7c5b6a5b569465e708f68186d7415c46fa2"
)
# The labels the model disposed of, with the page each is printed on.
LOST_LABELS = {6: ("22",), 7: ("30", "31", "32"), 8: ("33", "34")}


def _headspace():
    if not HEADSPACE.is_file():
        raise unittest.SkipTest(
            f"{HEADSPACE.name} is not present in the working tree; it is a "
            f"5.1 MB source deliberately not committed. Expected sha256 "
            f"{HEADSPACE_SHA256}."
        )
    extraction = extract_protocol_pdf(HEADSPACE)
    if extraction.sha256 != HEADSPACE_SHA256:
        raise unittest.SkipTest(
            f"{HEADSPACE.name} is a different file: {extraction.sha256}."
        )
    return extraction


class TheLostLabelsAreRealStepsTests(unittest.TestCase):
    """First, that the input is what the report said it was."""

    def test_every_lost_label_is_printed_on_its_page(self) -> None:
        extraction = _headspace()
        for page, labels in LOST_LABELS.items():
            printed = tuple(
                dict.fromkeys(
                    match.group("label")
                    for match in _numbered_action_matches(
                        extraction.pages[page - 1].text
                    )
                )
            )
            for label in labels:
                with self.subTest(page=page, label=label):
                    self.assertIn(label, printed)


if __name__ == "__main__":
    unittest.main()
