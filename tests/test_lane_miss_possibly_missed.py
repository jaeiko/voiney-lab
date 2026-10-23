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

import json
import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi import HTTPException

from tests.test_lane_an4_requote import STEP_TWO, _AskingModel, _RequoteCase, answer_with, first_item, found
from tests.test_lane_an5_requote_after_cards import elsewhere
from tests.test_lane_rv_review_pending import MADE_UP, _CatalogCase, write_pages_pdf
from voiney_lab import experiment_protocol as domain
from voiney_lab import server
from voiney_lab.experiment_protocol_analysis import (
    AnalysisPiece,
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
    extract_protocol_pdf,
)
from voiney_lab.experiment_protocol_store import initialize_protocol_store
from voiney_lab.protocol_catalog import (
    POSSIBLY_MISSED_NOTICE_CODE,
    READINESS_REASON_KO,
    ProtocolCatalog,
    ProtocolReviewError,
    ProtocolReviewItemsPendingError,
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


# --- the catalog: cards, the cap, the start notice ---------------------------


class _MissCase(_CatalogCase):
    """Gamma's PDF (three pages, six steps); the default answer reads page 1."""

    def draft(self) -> dict:
        return self.catalog.review_draft(self.protocol_id)

    def missed_cards(self, draft: dict | None = None) -> list[dict]:
        return [card for card in (draft or self.draft())["cards"]
                if card["type"] == "possibly_missed"]


class AReviewPendingAnalysisAsksAboutTheLinesTests(_MissCase):
    def setUp(self) -> None:
        super().setUp()
        self.entry, self.model = self.analyze(self.answer(step_two=MADE_UP))

    def test_each_place_is_a_card_beside_the_refused_item(self) -> None:
        self.assertEqual(self.entry.analysis_status, "review_pending")
        cards = self.missed_cards()
        # Step two's real line (made up in the answer), pages 2 and 3's steps.
        self.assertEqual([card["item_id"] for card in cards],
                         ["missed:1:4-4", "missed:2:2-3", "missed:3:2-3"])
        card = cards[1]
        self.assertEqual(card["kind_ko"], "분석에 없는 원문")
        self.assertEqual(card["reasons_ko"], ["분석에 없는 원문일 수 있어요"])
        self.assertEqual(card["question_ko"], "2쪽의 이 줄들이 실험할 단계인가요?")
        self.assertEqual(card["shown"], "3. Wash the pellet twice.\n4. Spin at 500 x g for 5 min.")
        self.assertEqual(card["source"]["lines"], {"page": 2, "first_line": 2, "last_line": 3})
        self.assertEqual(self.entry.review_pending["needs_review_count"], 4)
        self.assertIn("분석에 없는 원문일 수 있어요 3", self.entry.review_pending["message_ko"])
        self.assertEqual(self.model.calls, 1)

    def test_its_only_buttons_are_no_step_and_read_it_again(self) -> None:
        for card in self.missed_cards():
            with self.subTest(card=card["item_id"]):
                self.assertEqual([(choice["choice_id"], choice["label_ko"]) for choice in card["choices"]],
                                 [("confirm", "단계가 아니에요"), ("reanalysis", "다시 분석할게요")])
                confirm, again = card["choices"]
                self.assertEqual(confirm["request"], {"item_id": card["item_id"], "action": "confirm"})
                self.assertIsNone(confirm["needs"])
                self.assertEqual((again["route"], again["request"]), ("reanalysis", None))
        item = next(item for item in self.draft()["items"] if item["item_id"] == "missed:2:2-3")
        self.assertEqual(item["allowed_actions"], ["confirm", "memo", "reset"])

    def test_no_decision_writes_the_lines_in_as_a_step(self) -> None:
        for action, extra in (("fix", {"text": "3. Wash the pellet twice."}),
                              ("enter", {"text": "3. Wash"}), ("clear", {}),
                              ("confirm", {"source_lines": {"page": 2, "first_line": 2}})):
            with self.subTest(action=action), self.assertRaises(ProtocolReviewError):
                self.catalog.record_review_decision(
                    self.protocol_id, {"item_id": "missed:2:2-3", "action": action, **extra})

    def test_the_cards_must_be_answered_and_change_nothing_when_registered(self) -> None:
        self.catalog.record_review_decision(self.protocol_id, {
            "item_id": "step:s2", "action": "confirm",
            "source_lines": {"page": 1, "first_line": 4, "last_line": 4}})
        with self.assertRaises(ProtocolReviewItemsPendingError) as pending:
            self.catalog.register_review(self.protocol_id, {"reviewer_name": "검토자"})
        self.assertEqual(sorted(pending.exception.item_ids),
                         ["missed:1:4-4", "missed:2:2-3", "missed:3:2-3"])
        for card in self.missed_cards():
            result = self.catalog.record_review_decision(self.protocol_id, card["choices"][0]["request"])
        self.assertTrue(result["registrable"])
        self.assertTrue(all(card["decided"] for card in self.missed_cards()))
        version = self.catalog.register_review(self.protocol_id, {"reviewer_name": "검토자"})
        # "단계가 아니에요" changes nothing in the version; the ledger's
        # decision events keep the answers.
        self.assertEqual([change["item_id"] for change in version["review_version"]["changes"]],
                         ["step:s2"])
        (registered,) = self.events("protocol_review_version_registered")
        self.assertEqual(len(registered["decision_event_ids"]), 4)
        self.assertNotIn("missed:2:2-3", registered["origins"])
        fixture = self.catalog.load_executable_fixture(self.protocol_id)
        self.assertEqual([step.step_id for step in fixture.draft.protocol.sections[0].steps],
                         ["s1", "s2"])
        review = self.catalog.review(self.protocol_id)
        # A person answered every place: the version tells nothing more.
        self.assertIsNone(review["possibly_missed"])
        self.assertNotIn(POSSIBLY_MISSED_NOTICE_CODE,
                         [notice["code"] for notice in review["execution_notices"]])

    def test_the_route_takes_the_card_s_decision_and_refuses_another(self) -> None:
        self.store.close()

        def open_catalog():
            opened = initialize_protocol_store(self.settings)
            return ProtocolCatalog(opened), opened

        try:
            with patch.object(server, "_open_protocol_catalog", side_effect=open_catalog):
                draft = server.get_protocol_review_draft(self.protocol_id)
                card = next(card for card in draft["cards"] if card["item_id"] == "missed:3:2-3")
                result = server.post_protocol_review_decision(
                    self.protocol_id, {**card["choices"][0]["request"], "target": draft["target"]})
                self.assertEqual(result["item"]["decision"]["action"], "confirm")
                with self.assertRaises(HTTPException) as refused:
                    server.post_protocol_review_decision(
                        self.protocol_id, {"item_id": "missed:2:2-3", "action": "fix",
                                           "text": "3. Wash the pellet twice."})
                self.assertEqual((refused.exception.status_code, refused.exception.detail),
                                 (422, "review_decision_invalid"))
        finally:
            self.store = initialize_protocol_store(self.settings)
            self.catalog = ProtocolCatalog(self.store)


class AFailedPieceTests(_MissCase):
    """A failed piece's pages are asked about whole on their own card; the
    lines no quote covers there are not asked again (decision 3)."""

    def setUp(self) -> None:
        super().setUp()
        pieces = (AnalysisPiece(1, 3, 1, 1), AnalysisPiece(2, 3, 2, 2), AnalysisPiece(3, 3, 3, 3))
        broken = self.answer(pages=(2,)).replace('"steps":', '"stepz":', 1)
        answers = {1: [self.answer(pages=(1,))], 2: [broken, broken], 3: [self.answer(pages=(3,))]}

        def model_answer(*, system_prompt: str, input_json: str, response_schema: dict) -> str:
            return answers[json.loads(input_json)["pages"][0]["source_page_number"]].pop(0)

        model = Mock()
        model.analyze.side_effect = model_answer
        self.catalog.request_analysis(self.protocol_id, "analysis-" + "e" * 32)
        with patch("voiney_lab.protocol_catalog.analysis_pieces", return_value=pieces):
            self.catalog.analyze(self.protocol_id, model, analysis_id="analysis-" + "e" * 32)

    def test_its_pages_are_one_card_and_no_possibly_missed_one(self) -> None:
        self.assertEqual([card["item_id"] for card in self.draft()["cards"]], ["pages:2-2"])

    def test_answered_no_step_its_pages_are_no_notice_of_the_version(self) -> None:
        self.catalog.record_review_decision(self.protocol_id, {"item_id": "pages:2-2", "action": "confirm"})
        self.catalog.register_review(self.protocol_id, {"reviewer_name": "검토자"})
        self.assertIsNone(self.catalog.review(self.protocol_id)["possibly_missed"])


class TheCardsAndTheLimitTests(_MissCase):
    def setUp(self) -> None:
        super().setUp()
        root = Path(self.temp.name)
        self.pdf = root / "many.pdf"
        # Twelve pages, each with a heading, a quoted step and two steps the
        # answer leaves out, apart from each other.
        # Each page its own words: lines printed alike on every page are
        # running lines (furniture), not body.
        words = ("amber", "basil", "cedar", "delta", "ember", "fjord", "garnet", "hazel",
                 "indigo", "juniper", "kelp", "lilac")
        self.many = [[f"Section {word}", f"{2 * n - 1}. Add {n} mL of the {word} buffer.",
                      f"the {word} buffer is kept at room temperature",
                      f"and the {word} one is mixed before use",
                      f"{2 * n}. Wash the {word} pellet twice.", f"it stays {word} in the tube",
                      f"as the {word} one did before", f"Transfer the {word} pellet to a new tube."]
                     for n, word in enumerate(words, start=1)]
        self.words = words
        write_pages_pdf(self.pdf, self.many, title="Many")
        self.sha256 = extract_protocol_pdf(self.pdf).sha256
        self.protocol_id = self.catalog.register(
            self.pdf, source_filename="many.pdf", media_type="application/pdf").entry.protocol_id

    def many_answer(self, *, made_up: bool) -> str:
        ev = lambda text, page: {"source_page_number": page, "source_excerpt": text}
        steps = [{"step_id": f"s{n}", "source_label": str(2 * n - 1),
                  "instruction_source_text": f"{2 * n - 1}. Add {n} mL of the {word} buffer.",
                  "evidence": ev(f"{2 * n - 1}. Add {n} mL of the {word} buffer.", n)}
                 for n, word in enumerate(self.words, start=1)]
        if made_up:
            steps[0] = {**steps[0], "instruction_source_text": MADE_UP, "evidence": ev(MADE_UP, 1)}
        return json.dumps({
            "analysis_schema_version": 1, "pdf_sha256": self.sha256,
            "capability_policy_id": "p1-conservative",
            "protocol": {
                "protocol_id": "many",
                "metadata": {"title": "Section amber", "original_language": "en",
                             "evidence": ev("Section amber", 1)},
                "before_start": [], "materials": [], "equipment": [],
                "sections": [{"section_id": "all", "title_source_text": "Section amber",
                              "evidence": ev("Section amber", 1), "steps": steps}],
                "constructs": [], "description": None,
            },
        })

    def test_the_places_never_count_toward_the_limit_or_ask_for_another_analysis(self) -> None:
        # 24 places over 12 pages, with a limit of 12 cards: one card a page.
        entry, model = self.analyze(self.many_answer(made_up=True))
        self.assertEqual(model.calls, 1)
        self.assertEqual(entry.analysis_status, "review_pending")
        cards = self.missed_cards()
        self.assertEqual(len(cards), 12)
        # 3 places on page 1 (its step is made up), 2 on each other page: 25.
        self.assertEqual(cards[0]["item_id"], "missed:1")
        item = next(item for item in self.draft()["items"] if item["item_id"] == "missed:1")
        self.assertEqual(len(item["places"]), 3)
        self.assertEqual(cards[0]["question_ko"], "1쪽의 이 줄들이 실험할 단계인가요?")
        self.assertEqual(self.events("protocol_analysis_auto_reanalysis"), [])

    def test_a_smaller_limit_puts_pages_together(self) -> None:
        with patch.dict(os.environ, {"VOINEY_LAB_REVIEW_MAX_ITEMS": "4"}):
            entry, model = self.analyze(self.many_answer(made_up=True))
        self.assertEqual(model.calls, 1)
        cards = self.missed_cards()
        self.assertEqual([card["item_id"] for card in cards],
                         ["missed:1-3", "missed:4-6", "missed:7-9", "missed:10-12"])
        self.assertEqual(cards[0]["question_ko"], "1–3쪽의 이 줄들이 실험할 단계인가요?")
        self.assertIsNone(cards[0]["source"]["lines"])

    def test_a_passed_analysis_with_many_places_still_passes(self) -> None:
        entry, model = self.analyze(self.many_answer(made_up=False))
        self.assertEqual(model.calls, 1)
        self.assertTrue(entry.available_for_execution)
        self.assertEqual(self.missed_cards(), [])
        review = self.catalog.review(self.protocol_id)
        self.assertEqual(review["possibly_missed"]["count"], 24)
        self.assertNotIn("possibly_missed", [card["type"] for card in self.draft()["cards"]])
        self.assertEqual(review["possibly_missed"]["pages"], list(range(1, 13)))


class APassedAnalysisTellsItBeforeTheStartTests(_MissCase):
    def setUp(self) -> None:
        super().setUp()
        self.entry, self.model = self.analyze(self.answer())

    def test_it_is_not_sent_to_review_and_may_start(self) -> None:
        self.assertTrue(self.entry.available_for_execution)
        self.assertIsNone(self.entry.review_pending)
        self.assertEqual(self.missed_cards(), [])
        self.catalog.load_executable_fixture(self.protocol_id)
        review = self.catalog.review(self.protocol_id)
        self.assertEqual(review["execution_blockers"], [])

    def test_the_notice_names_how_many_places_and_their_pages(self) -> None:
        review = self.catalog.review(self.protocol_id)
        notice = next(notice for notice in review["execution_notices"]
                      if notice["code"] == POSSIBLY_MISSED_NOTICE_CODE)
        self.assertEqual(notice["message_ko"],
                         "분석에 없을 수 있는 원문이 2곳 있어요(2·3쪽). 시작 전에 원문을 확인해 주세요.")
        self.assertEqual(notice["kind"], "source_note")
        self.assertEqual(notice["pages"], [2, 3])
        self.assertEqual([(place["page"], place["first_line"], place["last_line"])
                          for place in notice["places"]], [(2, 2, 3), (3, 2, 3)])
        self.assertEqual(review["possibly_missed"]["count"], 2)
        (ready,) = self.events("protocol_analysis_ready")
        self.assertEqual(ready["possibly_missed"]["pages"], [2, 3])
        # Numbers and places only: the ledger keeps no copy of the lines.
        self.assertNotIn("Wash", json.dumps(ready["possibly_missed"]))

    def test_the_code_is_a_notice_with_a_korean_line_and_never_a_blocker(self) -> None:
        code = domain.ReadinessReasonCode(POSSIBLY_MISSED_NOTICE_CODE)
        self.assertNotIn(code, domain.EXECUTION_BLOCKING_REASON_CODES)
        self.assertIn(POSSIBLY_MISSED_NOTICE_CODE, READINESS_REASON_KO)

    def test_an_analysis_of_every_page_tells_nothing(self) -> None:
        entry, _ = self.analyze(self.answer(pages=(1, 2, 3)), analysis_id="analysis-" + "b" * 32,
                                reanalysis=True)
        review = self.catalog.review(self.protocol_id)
        self.assertIsNone(review["possibly_missed"])
        self.assertNotIn(POSSIBLY_MISSED_NOTICE_CODE,
                         [notice["code"] for notice in review["execution_notices"]])

    def test_a_version_reviewed_from_it_keeps_telling_it(self) -> None:
        self.catalog.register_review(self.protocol_id, {"reviewer_name": "검토자"})
        review = self.catalog.review(self.protocol_id)
        self.assertEqual(review["review_version"]["version"], 1)
        self.assertEqual(review["possibly_missed"]["pages"], [2, 3])
        self.catalog.register_review(self.protocol_id, {"reviewer_name": "검토자"})
        self.assertEqual(self.catalog.review(self.protocol_id)["possibly_missed"]["pages"], [2, 3])

    def test_the_review_route_carries_the_notice(self) -> None:
        self.store.close()

        def open_catalog():
            opened = initialize_protocol_store(self.settings)
            return ProtocolCatalog(opened), opened

        try:
            with patch.object(server, "_open_protocol_catalog", side_effect=open_catalog):
                review = server.get_protocol_review(self.protocol_id)
        finally:
            self.store = initialize_protocol_store(self.settings)
            self.catalog = ProtocolCatalog(self.store)
        self.assertIn(POSSIBLY_MISSED_NOTICE_CODE,
                      [notice["code"] for notice in review["execution_notices"]])


class TheReaskThatClosesEveryOtherCardTests(_RequoteCase):
    """Lane AN5's re-ask closes the refused item's card; the possibly-missed
    cards are no reason to keep the analysis waiting (decision 4)."""

    def test_lines_left_alone_pass_the_analysis_with_its_notice(self) -> None:
        model = _AskingModel(
            self.answer(step_two="2. Incubate 10 minutes at 37 C."),
            requote=lambda request: answer_with(found(first_item(request)["id"], 1, STEP_TWO)))
        entry = self.run_analysis(model)
        self.assertTrue(entry.available_for_execution)
        self.assertIsNone(entry.review_pending)
        review = self.catalog.review(self.protocol_id)
        self.assertEqual(review["possibly_missed"]["pages"], [2, 3])
        # The paraphrased step's own line was its card's, never a second one.
        (pending,) = self.events("protocol_analysis_review_pending")
        self.assertEqual([flag["item_id"] for flag in pending["flags"]],
                         ["step:s2", "missed:2:2-3", "missed:3:2-3"])

    def test_a_possibly_missed_card_a_person_answered_keeps_it_waiting(self) -> None:
        def requote(request: dict) -> dict:
            with elsewhere(self) as catalog:
                catalog.record_review_decision(
                    self.protocol_id, {"item_id": "missed:2:2-3", "action": "confirm"})
            return answer_with(found(first_item(request)["id"], 1, STEP_TWO))

        entry = self.run_analysis(_AskingModel(
            self.answer(step_two="2. Incubate 10 minutes at 37 C."), requote=requote))
        self.assertEqual(entry.analysis_status, "review_pending")
        cards = {card["item_id"]: card for card in self.catalog.review_draft(self.protocol_id)["cards"]}
        self.assertTrue(cards["missed:2:2-3"]["decided"])
        self.assertFalse(cards["missed:3:2-3"]["decided"])


class TheCardOnTheScreenTests(_MissCase):
    """The review screen draws the server's card from its fields: the page's
    script is not changed for it (lane RV2's one card function). Fake models,
    the page's own script under node; nothing is "live-tested"."""

    def setUp(self) -> None:
        super().setUp()
        self.analyze(self.answer(step_two=MADE_UP))
        draft = self.catalog.review_draft(self.protocol_id)
        at = next(index for index, card in enumerate(draft["cards"])
                  if card["item_id"] == "missed:2:2-3")
        self.draft = {**draft, "next_card": at}
        self.page = self.catalog.review_source_page(self.protocol_id, 2)

    def run_screen(self, body: str):
        from tests.test_lane_an3_card_screen import screen

        page = json.dumps(self.page, ensure_ascii=False)
        return screen(self.draft, f"const serverPage={page};\n" + body)

    def test_it_shows_the_question_the_lines_and_only_its_two_buttons(self) -> None:
        result = self.run_screen(r"""
serve([["/review-draft/source-pages/2",serverPage],["/review-draft",serverDraft]]);
await loadReviewDraft(serverDraft.protocol_id);await tick();
assert(node("review-card-progress").textContent==="3 / 4",node("review-card-progress").textContent);
const text=visibleText(card());
for(const words of ["분석에 없는 원문","2쪽의 이 줄들이 실험할 단계인가요?","분석에 없는 원문일 수 있어요","3. Wash the pellet twice."])assert(text.includes(words),`${words}: ${text}`);
assert(JSON.stringify(labels(card()).filter(label=>label!=="메모 남기기"))===JSON.stringify(["단계가 아니에요","다시 분석할게요"]),`buttons: ${labels(card())}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_no_step_is_a_decision_the_server_takes_and_read_again_is_an_analysis(self) -> None:
        from tests.test_lane_an3_card_screen import posts_of

        result = self.run_screen(r"""
serve([["/review-decisions",answer(serverDraft.cards.map(each=>each.item_id==="missed:2:2-3"?{...each,decided:true}:each),3)],["/review-draft/source-pages",serverPage],["/review-draft",serverDraft]]);
let asked=null;analyzeRegisteredProtocol=async(id,options)=>{asked={id,...options};};loadProtocolCatalog=async()=>{};
globalThis.confirm=()=>true;
await loadReviewDraft(serverDraft.protocol_id);await tick();
await press(card(),"단계가 아니에요");
assert(posts().length===1,`posts: ${posts().length}`);
await press(card(),"다시 분석할게요");
assert(posts().length===1,"다시 분석할게요 sent a decision");
assert(asked&&asked.id===serverDraft.protocol_id&&asked.reanalysis===true,JSON.stringify(asked));
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        (post,) = posts_of(result)
        self.assertEqual({key: post["body"][key] for key in ("item_id", "action")},
                         {"item_id": "missed:2:2-3", "action": "confirm"})
        answer = self.catalog.record_review_decision(self.protocol_id, post["body"])
        self.assertTrue(next(card for card in answer["cards"]
                             if card["item_id"] == "missed:2:2-3")["decided"])


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
