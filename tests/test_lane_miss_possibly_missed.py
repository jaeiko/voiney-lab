"""Source lines the analysis may have missed are shown to a person (lane MISS).

Human decision 2026-10-23. The evidence check asks only whether what the
model kept is in the source; an instruction the model never kept raised
nothing -- the 10/23 diagnosis made up an analysis holding one of a source's
four steps and it had no card and no blocker. Without any reference, the
server now sets aside on every page what the analysis's quotes cover, the
page's furniture, cover, references and copyright lines, and keeps the lines
left that read like an instruction (a numbered or bulleted sentence, an
English imperative, a Korean instruction ending). In an analysis that waits
for review each place is a card, "분석에 없는 원문일 수 있어요", with only
[단계가 아니에요] and [다시 분석할게요]; in one that passed they are one
notice before the start, and the start is not held back.
"""

from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path

from voiney_lab import experiment_protocol as domain
from voiney_lab.experiment_protocol_analysis import (
    MISSED_LINES,
    MISSED_PAGE,
    MISSED_PAGES,
    MISSED_SHAPE_IMPERATIVE,
    MISSED_SHAPE_KOREAN,
    MISSED_SHAPE_LABEL,
    MISSED_SHAPE_LIST,
    MISSED_SHAPE_VALUE,
    REVIEW_REASON_POSSIBLY_MISSED,
    PossiblyMissedPlace,
    missed_line_reads_as_instruction,
    missed_line_shapes,
    possibly_missed_flags,
    possibly_missed_places,
)
from voiney_lab.experiment_protocol_pdf import (
    ProtocolPdfExtraction,
    ProtocolPdfMetadata,
    ProtocolPdfPage,
)


def source(*pages: str) -> ProtocolPdfExtraction:
    return ProtocolPdfExtraction(
        original_filename="miss.pdf", byte_size=1, sha256="c" * 64,
        media_type="application/pdf", page_count=len(pages), encrypted=False,
        metadata=ProtocolPdfMetadata(None, None, None, None, None, None, None),
        pages=tuple(ProtocolPdfPage(n, text, False) for n, text in enumerate(pages, start=1)),
    )


def analysis_of(extraction: ProtocolPdfExtraction, *quotes: tuple[int, str],
                title: str = "Plasmid preparation",
                section: tuple[int, str] | None = None) -> domain.ExperimentProtocol:
    """A protocol whose steps quote these (page, text); the title is page 1's,
    and so is its one section's heading unless ``section`` names another."""

    steps = tuple(
        domain.ProtocolSourceStep(
            step_id=f"s{index}", source_label=str(index), instruction_source_text=text,
            evidence=domain.SourceEvidence(page, text))
        for index, (page, text) in enumerate(quotes, start=1))
    return domain.ExperimentProtocol(
        protocol_id="miss",
        metadata=domain.ProtocolMetadata(
            pdf=extraction, title=title, original_language="en",
            evidence=domain.SourceEvidence(1, title)),
        sections=(domain.ProtocolSection(
            "procedure", (section or (1, title))[1],
            domain.SourceEvidence(*(section or (1, title))), steps),),
    )


FOUR_STEPS = (
    "Plasmid preparation\n"
    "1. Thaw the competent cells on ice for 10 min.\n"
    "2. Add 1 µL of plasmid DNA to the cells.\n"
    "3. Incubate the tube at 42 °C for 45 s.\n"
    "4. Spread the cells on an LB agar plate."
)
STEP = FOUR_STEPS.split("\n")


class TheDiagnosisOfOctober23Tests(unittest.TestCase):
    """The made-up analysis that holds one of four steps now shows the rest."""

    def test_one_step_of_four_leaves_the_other_three_to_a_person(self) -> None:
        extraction = source(FOUR_STEPS)
        places = possibly_missed_places(analysis_of(extraction, (1, STEP[1])))
        self.assertEqual(
            [(place.page_number, place.first_line, place.last_line) for place in places],
            [(1, 3, 5)])
        self.assertIn(MISSED_SHAPE_LIST, places[0].shapes)
        self.assertIn(MISSED_SHAPE_IMPERATIVE, places[0].shapes)

    def test_an_analysis_that_quotes_every_step_leaves_nothing(self) -> None:
        extraction = source(FOUR_STEPS)
        protocol = analysis_of(extraction, *((1, line) for line in STEP[1:]))
        self.assertEqual(possibly_missed_places(protocol), ())

    def test_any_step_taken_out_is_found_where_it_stands(self) -> None:
        extraction = source(FOUR_STEPS)
        for missing in range(1, 5):
            with self.subTest(missing=missing):
                kept = [(1, line) for index, line in enumerate(STEP[1:], start=1)
                        if index != missing]
                (place,) = possibly_missed_places(analysis_of(extraction, *kept))
                self.assertEqual((place.first_line, place.last_line),
                                 (missing + 1, missing + 1))

    def test_a_quote_that_covers_part_of_a_line_leaves_its_other_sentence(self) -> None:
        extraction = source("Plasmid preparation\nSpin for 1 min. Then add 2 mL of PBS to the pellet.")
        protocol = analysis_of(extraction, (1, "Spin for 1 min."))
        (place,) = possibly_missed_places(protocol)
        self.assertEqual((place.first_line, place.last_line), (2, 2))
        self.assertIn(MISSED_SHAPE_IMPERATIVE, place.shapes)
        whole = analysis_of(extraction, (1, "Spin for 1 min. Then add 2 mL of PBS to the pellet."))
        self.assertEqual(possibly_missed_places(whole), ())

    def test_a_quote_printed_twice_covers_its_own_line_not_a_word_in_a_step(self) -> None:
        # A section's heading quoted as its title also stands, as a word, in
        # the step under it; the step is no less missed for that.
        extraction = source("Plasmid preparation\nIncubate\n18 Incubate at 37 °C in the CO2 incubator.")
        protocol = replace(analysis_of(extraction), sections=(domain.ProtocolSection(
            "incubate", "Incubate", domain.SourceEvidence(1, "Incubate"), ()),))
        (place,) = possibly_missed_places(protocol)
        self.assertEqual((place.first_line, place.last_line), (3, 3))
        self.assertIn(MISSED_SHAPE_IMPERATIVE, place.shapes)

    def test_every_kind_of_quote_covers_its_lines(self) -> None:
        # A material's, a warning's and a note's quotes count, not steps' only.
        text = ("Plasmid preparation\n• Keep the lysis buffer on ice until use.\n"
                "1. Add 200 µL of lysis buffer.\nWear gloves and goggles at all times.")
        extraction = source(text)
        protocol = analysis_of(extraction, (1, "1. Add 200 µL of lysis buffer."))
        self.assertEqual([(place.first_line, place.last_line)
                          for place in possibly_missed_places(protocol)], [(2, 2), (4, 4)])
        material = domain.Material("m1", "lysis buffer", domain.SourceEvidence(
            1, "• Keep the lysis buffer on ice until use."))
        section = protocol.sections[0]
        warned = replace(section.steps[0], warnings=(domain.SourceStatement(
            "w1", "Wear gloves and goggles at all times.",
            domain.SourceEvidence(1, "Wear gloves and goggles at all times.")),))
        covered = replace(protocol, materials=(material,),
                          sections=(replace(section, steps=(warned,)),))
        self.assertEqual(possibly_missed_places(covered), ())


class WhatReadsLikeAnInstructionTests(unittest.TestCase):
    """Decision 2's shapes, and what decision 5's measurement left out."""

    def shows(self, text: str) -> bool:
        return missed_line_reads_as_instruction(missed_line_shapes(text))

    def test_a_numbered_or_bulleted_sentence_is_shown(self) -> None:
        for text in ("3. Wash the pellet twice.", "(2) Mix gently by inversion.",
                     "Step 3 Spin down the beads.", "• Keep samples on ice at all times",
                     "13 We click on the Begin Run button", "a) Place the tube in the rack",
                     "2.1 The sample is mixed with reagent water."):
            with self.subTest(text=text):
                self.assertIn(MISSED_SHAPE_LIST, missed_line_shapes(text))
                self.assertTrue(self.shows(text))

    def test_an_english_imperative_is_shown_after_its_opening_too(self) -> None:
        for text in ("Add 2 mL of PBS.", "Centrifuge at 12,000 x g for 5 min",
                     "Upon arrival, store all components at -20 °C.",
                     "Using a pipette, remove the medium.", "Then add the second buffer.",
                     "Gently remove the supernatant from the tube", "Note: Keep the beads cold."):
            with self.subTest(text=text):
                self.assertIn(MISSED_SHAPE_IMPERATIVE, missed_line_shapes(text))

    def test_a_korean_instruction_ending_is_shown(self) -> None:
        for text in ("시료를 37℃에서 10분간 둔다.", "상층액을 버린다", "튜브에 넣으세요.",
                     "시약은 사용 직전에 녹일 것.", "용액을 섞으십시오."):
            with self.subTest(text=text):
                self.assertIn(MISSED_SHAPE_KOREAN, missed_line_shapes(text))
        for text in ("이 방법은 간단하다.", "결과는 표 2에 있다.", "다음 단계에서 쓸 것이다."):
            with self.subTest(text=text):
                self.assertNotIn(MISSED_SHAPE_KOREAN, missed_line_shapes(text))

    def test_a_value_with_its_unit_alone_is_recorded_but_not_shown(self) -> None:
        # Decision 5: prose and tables that state values were most of the
        # false alarms of the starter pack; a value alone shows no line.
        for text in ("The cells were incubated for 2 h at 37 °C.", "50 mM Tris-HCl, pH 8.0",
                     "10 min", "RH > 10% at 20 °C", "37도에서 10분간"):
            with self.subTest(text=text):
                self.assertEqual(missed_line_shapes(text), (MISSED_SHAPE_VALUE,))
                self.assertFalse(self.shows(text))

    def test_the_units_are_time_temperature_volume_concentration_and_speed(self) -> None:
        for text in ("for 30 s", "at 4 °C", "add 500 µL", "in 50 mM", "a 1% gel",
                     "at 16,000 g", "at 500 x g", "at 3000 rpm", "10분간", "37도"):
            with self.subTest(text=f"word {text}"):
                self.assertIn(MISSED_SHAPE_VALUE, missed_line_shapes(f"word {text}"))
        for text in ("weigh 5 g", "3 tubes", "Figure 3h", "see Table 2 d", "2024년 3월 1일"):
            with self.subTest(text=text):
                self.assertNotIn(MISSED_SHAPE_VALUE, missed_line_shapes(text))

    def test_a_label_before_words_that_read_as_no_sentence_shows_nothing(self) -> None:
        for text in ("1.0 SCOPE AND APPLICATION", "2.3.1. Analytical Portion Mass",
                     "5 T525 (3 layer) 30 15 60", "1 Department of Biology, Example University",
                     "* author@example.org"):
            with self.subTest(text=text):
                self.assertNotIn(MISSED_SHAPE_LIST, missed_line_shapes(text))
                self.assertFalse(self.shows(text))
        self.assertIn(MISSED_SHAPE_LABEL, missed_line_shapes("1.0 SCOPE AND APPLICATION"))

    def test_a_verb_word_that_names_a_thing_is_no_imperative(self) -> None:
        for text in ("Wash buffer", "Stop solution", "Image J was used to quantify bands.",
                     "Wash buffer is prepared fresh.", "Plate Blank Well OD Mean Std Dev",
                     "Note: All chemicals were used as received.",
                     "To verify the yield, we weighed the pellet.",
                     "and use a pipette to mix it"):
            with self.subTest(text=text):
                self.assertNotIn(MISSED_SHAPE_IMPERATIVE, missed_line_shapes(text))
                self.assertFalse(self.shows(text))

    def test_one_rule_for_every_document_whatever_its_step_count(self) -> None:
        # No document, sentence or step count has a rule of its own: unlike
        # the hollow check (20 steps, 25 %), one step left out of three or of
        # thirty is found the same way.
        for count in (3, 30):
            with self.subTest(count=count):
                lines = [f"{n}. Add {n} µL of the buffer to tube {n}." for n in range(1, count + 1)]
                extraction = source("Plasmid preparation\n" + "\n".join(lines))
                missing = count // 2
                kept = [(1, line) for n, line in enumerate(lines, start=1) if n != missing]
                (place,) = possibly_missed_places(analysis_of(extraction, *kept))
                self.assertEqual((place.first_line, place.last_line), (missing + 1, missing + 1))


class WhatIsSetAsideTests(unittest.TestCase):
    """Decision 1: furniture, cover, references and copyright lines."""

    def test_running_lines_and_page_numbers_are_never_shown(self) -> None:
        pages = [
            f"Add the journal header line to every page\n{body}\nPage {n} of 4"
            for n, body in enumerate(("Plasmid preparation\n1. Thaw the cells on ice.",
                                      "2. Add the plasmid to the cells.",
                                      "3. Incubate the tube at 42 °C.",
                                      "4. Spread the cells on a plate."), start=1)]
        extraction = source(*pages)
        protocol = analysis_of(
            extraction, (1, "1. Thaw the cells on ice."), (2, "2. Add the plasmid to the cells."),
            (3, "3. Incubate the tube at 42 °C."), (4, "4. Spread the cells on a plate."))
        # The header opens with "Add" and would read as an instruction were
        # it not printed alike at the top of every page.
        self.assertTrue(missed_line_reads_as_instruction(
            missed_line_shapes("Add the journal header line to every page")))
        self.assertEqual(possibly_missed_places(protocol), ())

    def test_a_references_list_is_set_aside_to_the_next_quoted_line(self) -> None:
        text = ("Plasmid preparation\n1. Thaw the cells on ice.\nReferences\n"
                "1. Doe J. Add a method to the list. J Methods. 2019.\n"
                "2. Roe R. Mix the tubes well. Lab Notes. 2020.")
        extraction = source(text)
        protocol = analysis_of(extraction, (1, "1. Thaw the cells on ice."))
        self.assertEqual(possibly_missed_places(protocol), ())
        numbered = source(text.replace("References", "10.0 REFERENCES"))
        self.assertEqual(possibly_missed_places(
            analysis_of(numbered, (1, "1. Thaw the cells on ice."))), ())

    def test_a_procedure_after_a_front_references_list_is_read_again(self) -> None:
        # CDC's SOP lists its references on page 1 and its procedure follows.
        text = ("Plasmid preparation\nReferences\n1. Use the manual of methods, 10th edition.\n"
                "Procedure\n2. Thaw the cells on ice.\n3. Add the plasmid to the cells.")
        extraction = source(text)
        (place,) = possibly_missed_places(analysis_of(extraction, (1, "2. Thaw the cells on ice.")))
        self.assertEqual((place.first_line, place.last_line), (6, 6))

    def test_copyright_licence_doi_and_contents_lines_are_never_shown(self) -> None:
        text = ("Plasmid preparation\n1. Thaw the cells on ice.\n"
                "© 2024 The Authors. Distribute under CC BY 4.0 and keep this notice.\n"
                "Open access funding: Use of this article is free.\n"
                "Cite as doi:10.1000/xyz - Add it to your list.\n"
                "2. Prepare the buffers ........................ 4")
        extraction = source(text)
        self.assertEqual(possibly_missed_places(
            analysis_of(extraction, (1, "1. Thaw the cells on ice."))), ())

    def test_a_cover_page_with_no_step_quoted_and_no_numbered_line_is_set_aside(self) -> None:
        cover = ("Plasmid preparation\nJane Doe, Richard Roe\nHighlights\n"
                 "• Prepare plasmid DNA in under one hour from small cultures")
        body = "Procedure\n1. Thaw the cells on ice.\n2. Add the plasmid to the cells."
        extraction = source(cover, body)
        protocol = analysis_of(extraction, (2, "1. Thaw the cells on ice."),
                               (2, "2. Add the plasmid to the cells."), section=(2, "Procedure"))
        self.assertEqual(possibly_missed_places(protocol), ())
        # A first page with a numbered step left out of the analysis is no
        # cover, so the step it prints is shown.
        stepped = source(cover + "\n1. Thaw the cells on ice.", "Procedure\n2. Add the plasmid to the cells.")
        (place,) = possibly_missed_places(analysis_of(
            stepped, (2, "2. Add the plasmid to the cells."), section=(2, "Procedure")))
        self.assertEqual((place.page_number, place.first_line, place.last_line), (1, 4, 5))
        # The same page with a step quoted on it is no cover: its bullet shows.
        quoted = source(cover + "\n1. Thaw the cells on ice.", "2. Add the plasmid to the cells.")
        (place,) = possibly_missed_places(analysis_of(
            quoted, (1, "1. Thaw the cells on ice."), (2, "2. Add the plasmid to the cells.")))
        self.assertEqual((place.page_number, place.first_line), (1, 4))


class PlacesAndItemsTests(unittest.TestCase):
    """Lines close together are one place; many places are put together."""

    def test_lines_close_together_are_one_place_and_a_quoted_line_splits_them(self) -> None:
        text = ("Plasmid preparation\n1. Thaw the cells on ice.\n2. Add the plasmid to the cells.\n"
                "3. Incubate the tube at 42 °C.\n4. Spread the cells on a plate.\n"
                "5. Keep the plate at 37 °C overnight.")
        extraction = source(text)
        places = possibly_missed_places(analysis_of(extraction, (1, "3. Incubate the tube at 42 °C.")))
        self.assertEqual([(place.first_line, place.last_line) for place in places], [(2, 3), (5, 6)])

    def test_an_other_line_between_two_is_still_one_place_two_are_not(self) -> None:
        one = source("Plasmid preparation\n1. Thaw the cells on ice.\nthe cells stay cold here\n"
                     "2. Add the plasmid to the cells.")
        self.assertEqual(len(possibly_missed_places(analysis_of(one))), 1)
        two = source("Plasmid preparation\n1. Thaw the cells on ice.\nthe cells stay cold\n"
                     "and are kept so\n2. Add the plasmid to the cells.")
        self.assertEqual(len(possibly_missed_places(analysis_of(two))), 2)

    def place(self, page: int, line: int) -> PossiblyMissedPlace:
        return PossiblyMissedPlace(page, line, line, (MISSED_SHAPE_LIST,))

    def test_one_item_per_place_while_they_are_no_more_than_the_limit(self) -> None:
        places = [self.place(1, 2), self.place(1, 9), self.place(3, 4)]
        flags = possibly_missed_flags(places, limit=3)
        self.assertEqual([flag.item_id for flag in flags],
                         ["missed:1:2-2", "missed:1:9-9", "missed:3:4-4"])
        self.assertTrue(all(flag.reason == REVIEW_REASON_POSSIBLY_MISSED for flag in flags))
        self.assertEqual({flag.reason_code for flag in flags}, {MISSED_LINES})
        self.assertEqual(flags[0].reason_ko, "분석에 없는 원문일 수 있어요")
        # Not a failed piece's pages: the summary must not say "분석 실패".
        self.assertIsNone(flags[0].first_page)

    def test_more_places_than_the_limit_are_put_together_by_page(self) -> None:
        places = [self.place(1, 2), self.place(1, 9), self.place(3, 4), self.place(3, 8)]
        flags = possibly_missed_flags(places, limit=3)
        self.assertEqual([flag.item_id for flag in flags], ["missed:1", "missed:3"])
        self.assertEqual({flag.reason_code for flag in flags}, {MISSED_PAGE})
        self.assertEqual(len(flags[0].extra["places"]), 2)

    def test_more_pages_than_the_limit_are_put_together_in_runs_of_pages(self) -> None:
        places = [self.place(page, 2) for page in range(1, 31)]
        flags = possibly_missed_flags(places, limit=12)
        self.assertEqual(len(flags), 12)
        self.assertEqual({flag.reason_code for flag in flags}, {MISSED_PAGES})
        self.assertEqual(flags[0].item_id, "missed:1-2")
        self.assertEqual(sum(len(flag.extra["places"]) for flag in flags), 30)
        self.assertEqual([place["page"] for flag in flags for place in flag.extra["places"]],
                         list(range(1, 31)))


@unittest.skipUnless(
    (Path(__file__).resolve().parents[1] / "data/runtime/candidate-a-source/in-gel-digestion.pdf").is_file(),
    "the externally licensed in-gel PDF is not present (condition B)")
class TheDevelopmentFixtureTests(unittest.TestCase):
    def test_the_curated_in_gel_analysis_leaves_no_line(self) -> None:
        from tests.protocol_vocabulary_support import SOURCE_PDF
        from voiney_lab.curated_protocol import load_curated_protocol_fixture

        data = Path(__file__).resolve().parents[1] / "data/fixtures/development_protocols"
        fixture = load_curated_protocol_fixture(
            data / "candidate_a_curated_analysis.json",
            data / "candidate_a_curated_analysis.provenance.json", SOURCE_PDF)
        self.assertEqual(possibly_missed_places(fixture.draft.protocol), ())


if __name__ == "__main__":
    unittest.main()
