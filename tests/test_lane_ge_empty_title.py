"""Lane GE, decision 5 (2026-10-11): an analysis with no title is named by its PDF file.

Lane EV2 (decision 2) lets an analysis pass with its title cleared when the
source did not confirm it. The voice answer about the protocol's identity
("현재 프로토콜은 …, 실행 버전 …") and the experiment report's protocol title
then carried an empty name. Now ``CuratedProtocolFixture.title`` -- the one
place both read -- is the PDF file's name without its extension when the
analysis title is empty. A titled analysis is unchanged.
"""

from __future__ import annotations

import dataclasses
import unittest

from voiney_lab import experiment_protocol as domain
from voiney_lab.curated_protocol import CuratedProtocolSession
from voiney_lab.runtime_routing import route_curated_runtime_turn

from tests.protocol_vocabulary_support import build_fixture

STEPS = (
    "1 Weigh 10 g of dried soil into a flask.",
    "2 Add 100 mL of water and shake for 1 hour.",
)


def _untitled_fixture(filename: str):
    base = build_fixture(protocol_id="fictional-untitled", title="Soil extraction", steps=STEPS)
    protocol = base.draft.protocol
    # As lane EV2 clears an unconfirmed title: the field named with its
    # "metadata." prefix, the value empty, and its evidence gone.
    metadata = dataclasses.replace(protocol.metadata, title="", title_evidence=None)
    cleared = tuple(dict.fromkeys((*getattr(protocol, "cleared_fields", ()), "metadata.title")))
    protocol = dataclasses.replace(protocol, metadata=metadata, cleared_fields=cleared)
    domain.validate_protocol(protocol)
    draft = dataclasses.replace(base.draft, protocol=protocol)
    return dataclasses.replace(base, draft=draft, source_filename=filename)


class EmptyTitleTests(unittest.TestCase):
    def test_the_title_is_the_file_name_without_its_extension(self) -> None:
        fixture = _untitled_fixture("soil_nutrient_analysis.pdf")
        self.assertEqual(fixture.draft.protocol.metadata.title, "")
        self.assertEqual(fixture.title, "soil nutrient analysis")

    def test_a_titled_analysis_keeps_its_title(self) -> None:
        fixture = build_fixture(protocol_id="fictional-titled", title="Soil extraction", steps=STEPS)
        self.assertEqual(fixture.title, "Soil extraction")

    def test_the_voice_answer_names_the_file(self) -> None:
        fixture = _untitled_fixture("zenodo_13275242.pdf")
        session = CuratedProtocolSession(fixture)
        session.active = True
        session.current_index = 0
        plan = route_curated_runtime_turn(
            session, "프로토콜 번호와 버전 알려줘", turn_id=1, language="ko",
        ).plan
        self.assertIn("현재 프로토콜은 zenodo 13275242,", plan.speech_text or "")
        self.assertNotIn("현재 프로토콜은 ,", plan.speech_text or "")
        self.assertFalse(plan.state_changed)

    def test_the_file_name_falls_back_to_the_source_path(self) -> None:
        fixture = _untitled_fixture("")
        fixture = dataclasses.replace(fixture, source_filename=None)
        self.assertEqual(fixture.title, fixture.source_pdf_path.stem.replace("_", " ") if fixture.source_pdf_path else "")


if __name__ == "__main__":
    unittest.main()
