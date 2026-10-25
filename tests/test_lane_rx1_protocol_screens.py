"""Lane RX1 (2026-10-25): the read-only fields the new screen's protocol pages
need, and a person's own name for a protocol.

Human decisions 2026-10-25 (lane RX1 brief):
- The confirmation screen puts a highlight band on the source page image
  where the card's lines stand (docs/UI_SPEC.md 4.6). The server gives each
  line of a source page its box on the page, in PDF points, read from the
  text engine's blocks; nothing decides on it.
- The upload asks which pages to analyse with small pictures of the pages:
  the page count before any analysis, and a thumbnail rendering of a page.
- "오늘 원문과 다르게 할 값" shows a step's sentence (Korean first, the
  source folded) with each changeable value marked: every value carries its
  place in the step's sentence, and the options list the steps with their
  text and stored Korean.
- A title the analysis read from a cover ("표지 최종 하리") can be replaced by
  a person: one more append-only ledger event, never a rewrite.

Every test runs against the catalog and the real routes with a fake PDF; no
provider is called.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from tests.test_lane_rv_review_pending import _CatalogCase
from voiney_lab import server
from voiney_lab import source_figures
from voiney_lab.experiment_protocol_store import initialize_protocol_store
from voiney_lab.protocol_catalog import (
    PROTOCOL_TITLE_MAX_CHARS,
    PROTOCOL_TITLE_RENAMED_EVENT,
    ProtocolCatalog,
    ProtocolTitleInvalidError,
)
from voiney_lab.start_check import RunChanges, step_changeable_values

PNG_HEAD = b"\x89PNG\r\n\x1a\n"


class _RouteCase(_CatalogCase):
    """The routes with the catalog of the test case (the store is thread-affine)."""

    def open_catalog(self):
        opened = initialize_protocol_store(self.settings)
        return ProtocolCatalog(opened), opened

    def routes(self):
        return patch.multiple(
            server,
            _open_protocol_catalog=self.open_catalog,
            _scope_catalog_resource=lambda *args, **kwargs: None,
            _start_check_principal_id=lambda: "person-a",
        )


class LineBoxesTests(_RouteCase):
    """Decision 2: the band on the page image needs each line's box."""

    def test_every_line_of_a_source_page_has_its_box_in_page_points(self) -> None:
        page = self.catalog.review_source_page(self.protocol_id, 1)
        boxes = [line["box"] for line in page["lines"]]
        self.assertEqual(len(boxes), 5)
        for box in boxes:
            self.assertIsInstance(box, list)
            self.assertEqual(len(box), 4)
            x0, y0, x1, y1 = box
            self.assertLess(x0, x1)
            self.assertLess(y0, y1)
            # The fixture prints at x = 72 on a 612 x 792 pt page.
            self.assertAlmostEqual(x0, 72.0, delta=2.0)
            self.assertLess(x1, 612.0)
        # Lines follow one another down the page, each below the one before.
        tops = [box[1] for box in boxes]
        self.assertEqual(tops, sorted(tops))
        self.assertGreater(tops[1] - tops[0], 8.0)

    def test_a_page_without_blocks_has_no_boxes_and_still_its_lines(self) -> None:
        from voiney_lab.experiment_protocol_pdf import ProtocolPdfPage
        from voiney_lab.protocol_catalog import _line_boxes

        page = ProtocolPdfPage(source_page_number=1, text="one\ntwo", text_empty=False)
        self.assertEqual(_line_boxes(page), [None, None])

    def test_a_block_s_lines_are_shared_down_its_box(self) -> None:
        from voiney_lab.experiment_protocol_pdf import ProtocolPdfPage
        from voiney_lab.pdf_text_engine import PdfTextBlock
        from voiney_lab.protocol_catalog import _line_boxes

        block = PdfTextBlock(x0=40, y0=100, x1=300, y1=130, font_size=10.0, bold=False,
                             text="first line\nsecond line")
        other = PdfTextBlock(x0=40, y0=200, x1=200, y1=212, font_size=10.0, bold=False,
                             text="third line")
        page = ProtocolPdfPage(source_page_number=1, text="first line\nsecond line\n\nthird line",
                               text_empty=False, blocks=(block, other))
        self.assertEqual(_line_boxes(page), [
            [40.0, 100.0, 300.0, 115.0], [40.0, 115.0, 300.0, 130.0], None,
            [40.0, 200.0, 200.0, 212.0]])

    def test_the_route_says_the_image_s_resolution_beside_the_lines(self) -> None:
        with self.routes():
            payload = server.get_protocol_review_source_page(self.protocol_id, 1)
        # The image route renders at this resolution: a box in points is placed
        # on the image by dpi / 72, with no page size to guess.
        self.assertEqual(payload["image_dpi"], source_figures.PAGE_IMAGE_DPI)
        self.assertIsInstance(payload["lines"][0]["box"], list)


class SourcePagesTests(_RouteCase):
    """Decision 1: the page picker's count and small pictures, before any analysis."""

    def test_the_page_count_is_read_before_an_analysis(self) -> None:
        self.assertEqual(self.catalog.source_page_count(self.protocol_id), 3)
        with self.routes():
            payload = server.get_protocol_source_pages(self.protocol_id)
        self.assertEqual(payload["page_count"], 3)
        self.assertEqual(payload["protocol_id"], self.protocol_id)
        self.assertEqual(payload["image_dpi"], source_figures.PAGE_IMAGE_DPI)
        self.assertEqual(payload["thumbnail_dpi"], server.SOURCE_THUMBNAIL_DPI)

    def test_a_thumbnail_is_a_small_png_of_the_page(self) -> None:
        server._SOURCE_THUMBNAILS.clear()
        with self.routes():
            thumb = server.get_protocol_review_source_page_image(self.protocol_id, 2, size="thumb")
            full = server.get_protocol_review_source_page_image(self.protocol_id, 2)
        self.assertTrue(thumb.body.startswith(PNG_HEAD))
        self.assertEqual(thumb.media_type, "image/png")
        self.assertLess(len(thumb.body), len(full.body))
        # A 612 x 792 pt page at the thumbnail's dpi: the PNG header says so.
        width = int.from_bytes(thumb.body[16:20], "big")
        height = int.from_bytes(thumb.body[20:24], "big")
        self.assertEqual((width, height), (612 * server.SOURCE_THUMBNAIL_DPI // 72, 792 * server.SOURCE_THUMBNAIL_DPI // 72))
        self.assertEqual(thumb.headers["x-protocol-source-page"], "2")
        # Kept in memory for the next look; a page the document lacks is unknown.
        self.assertIn((self.sha256, 2), server._SOURCE_THUMBNAILS)
        with self.routes(), self.assertRaises(server.HTTPException) as caught:
            server.get_protocol_review_source_page_image(self.protocol_id, 9, size="thumb")
        self.assertEqual(caught.exception.status_code, 404)


class RenameTests(_RouteCase):
    """Decision 4: a person's name for a protocol, as one more ledger event."""

    def test_a_new_name_shows_everywhere_the_title_is_read(self) -> None:
        self.assertEqual(self.catalog.get_entry(self.protocol_id).title, "Protocol Gamma")
        entry = self.catalog.rename(self.protocol_id, "  감마 단백질 소화  ", actor_principal_id="person-a")
        self.assertEqual(entry.title, "감마 단백질 소화")
        self.assertEqual(self.catalog.get_entry(self.protocol_id).title, "감마 단백질 소화")
        self.assertEqual(self.catalog.review_draft(self.protocol_id)["title"], "감마 단백질 소화")
        (event,) = self.events(PROTOCOL_TITLE_RENAMED_EVENT)
        self.assertEqual(event["title"], "감마 단백질 소화")
        self.assertEqual(event["actor_principal_id"], "person-a")
        self.assertEqual(event["title_before"], "Protocol Gamma")
        # The source and its metadata are untouched; the latest name wins.
        self.assertEqual(self.catalog.rename(self.protocol_id, "두 번째 이름").title, "두 번째 이름")
        self.assertEqual(len(self.events(PROTOCOL_TITLE_RENAMED_EVENT)), 2)

    def test_the_name_survives_an_analysis_and_a_review_version(self) -> None:
        self.catalog.rename(self.protocol_id, "내 이름")
        entry, _ = self.analyze(self.answer())
        self.assertTrue(entry.available_for_execution)
        self.assertEqual(entry.title, "내 이름")
        self.assertEqual(self.catalog.review(self.protocol_id)["title"], "내 이름")

    def test_an_empty_too_long_or_control_character_name_is_refused(self) -> None:
        for bad in ("", "   ", "x" * (PROTOCOL_TITLE_MAX_CHARS + 1), "줄\n바꿈", "탭\t안", None, 12):
            with self.subTest(bad=bad), self.assertRaises(ProtocolTitleInvalidError):
                self.catalog.rename(self.protocol_id, bad)  # type: ignore[arg-type]
        self.assertEqual(self.events(PROTOCOL_TITLE_RENAMED_EVENT), [])
        self.assertEqual(self.catalog.get_entry(self.protocol_id).title, "Protocol Gamma")

    def test_the_route_renames_for_the_signed_in_person_and_refuses_bad_names(self) -> None:
        # The route names the person the start check names (the signed-in
        # account, or the development identity); routes() answers "person-a".
        with self.routes():
            answer = server.post_protocol_title(self.protocol_id, {"title": "경로로 바꾼 이름"})
            self.assertEqual(answer["protocol"]["title"], "경로로 바꾼 이름")
            self.assertEqual(answer["title"], "경로로 바꾼 이름")
            with self.assertRaises(server.HTTPException) as caught:
                server.post_protocol_title(self.protocol_id, {"title": ""})
        self.assertEqual(caught.exception.status_code, 400)
        self.assertEqual(caught.exception.detail, "protocol_title_invalid")
        (event,) = self.events(PROTOCOL_TITLE_RENAMED_EVENT)
        self.assertEqual(event["actor_principal_id"], "person-a")
        self.assertEqual(self.catalog.get_entry(self.protocol_id).title, "경로로 바꾼 이름")


class RunChangeSentencesTests(_RouteCase):
    """Decision 3: each value's place in its step's sentence, and the steps' text."""

    def setUp(self) -> None:
        super().setUp()
        self.analyze(self.answer(pages=(1, 2, 3)))

    def test_a_value_knows_where_it_stands_in_the_sentence(self) -> None:
        text = "2. Incubate for 10 min at 37 C."
        time, warm = step_changeable_values("s2", "2", text)
        self.assertEqual(text[time.start:time.end], "10 min")
        self.assertEqual(text[warm.start:warm.end], "37 C")
        self.assertEqual(time.public_dict()["start"], text.index("10 min"))
        self.assertEqual(time.public_dict()["end"], text.index("10 min") + len("10 min"))

    def test_the_options_list_the_steps_with_their_text_and_their_values(self) -> None:
        options = RunChanges(self.catalog).options(self.protocol_id)
        steps = options["steps"]
        self.assertEqual([step["step_label"] for step in steps], ["1", "2", "3", "4", "5", "6"])
        second = steps[1]
        self.assertEqual(second["text"], "2. Incubate for 10 min at 37 C.")
        self.assertIsNone(second["text_ko"])
        values = {value["item_id"]: value for value in options["values"]}
        self.assertEqual([values[item]["written"] for item in second["value_ids"]], ["10 min", "37 C"])
        for item in second["value_ids"]:
            value = values[item]
            self.assertEqual(second["text"][value["start"]:value["end"]], value["written"])
        # A step without a changeable value is listed too, empty-handed.
        self.assertEqual(steps[2]["value_ids"], [])

    def test_stored_korean_travels_with_the_step(self) -> None:
        def localize(fixture):
            from dataclasses import replace

            return replace(fixture, machine_localizations={"s2/current_step": "2. 37 °C에서 10분 동안 둡니다."})

        options = RunChanges(self.catalog, localize=localize).options(self.protocol_id)
        self.assertEqual(options["steps"][1]["text_ko"], "2. 37 °C에서 10분 동안 둡니다.")
        self.assertIsNone(options["steps"][0]["text_ko"])

    def test_the_route_attaches_the_revision_s_translations(self) -> None:
        seen = []

        def with_translations(fixture, *, start_missing=True):
            seen.append(start_missing)
            return fixture

        with self.routes(), patch.object(server, "_with_revision_translations", side_effect=with_translations), \
                patch.object(server, "_last_run_values", return_value=None):
            options = server.get_protocol_run_changes(self.protocol_id)
        self.assertEqual(seen, [False])
        self.assertEqual(len(options["steps"]), 6)
        self.assertIn("start", options["values"][0])


if __name__ == "__main__":
    unittest.main()
