"""Lane WV, decision 1 (2026-10-09): the source's own figures first.

A step's pictures are cut from the uploaded PDF by geometry alone, shown in
the "원문 PDF 그림" panel with the caption printed under them, and "그림
보여줘" / "이 단계 그림 있어?" say so. Nothing here reads a word of the
source to decide what a figure is, nothing changes workflow state, and a
PDF that cannot be cut costs nothing but the picture.

The PDFs are built here with the engine's own library, as lane PX's tests
build theirs, so these run in both pytest baselines.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.protocol_vocabulary_support import build_fixture
from tests.test_screen_cleanup import run_page_script
from voiney_lab import pdf_text_engine as engine
from voiney_lab import server as server_module
from voiney_lab import source_figures
from voiney_lab.curated_protocol import (
    CuratedProtocolAction,
    CuratedProtocolSession,
    asked_about_values,
    picture_request_kind,
    web_lookup_subject,
)
from voiney_lab.experiment_protocol_pdf import clear_protocol_pdf_cache, extract_protocol_pdf
from voiney_lab.runtime_routing import route_curated_runtime_turn

STEPS = (
    "1 Cut the stained band out of the gel and place it in a tube.",
    "2 Wash the band with 500 µL of solution B.",
    "3 Remove and discard the supernatant.",
)


def _noise(pymupdf, width: int, height: int, seed: int = 7):
    """A picture with many colours, the way a photograph has."""

    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height), 0)
    value = seed
    samples = bytearray()
    for _ in range(width * height):
        value = (value * 1103515245 + 12345) & 0x7FFFFFFF
        samples += bytes(((value >> 16) & 255, (value >> 8) & 255, value & 255))
    pixmap.set_rect(pixmap.irect, (255, 255, 255))
    pixmap.samples_mv[:] = bytes(samples)  # type: ignore[index]
    return pixmap.tobytes("png")


def build_source_pdf(path: Path) -> None:
    """Page 1: the steps, a photograph with a legend under it, a note card drawn as
    an image with its text printed over it, and a QR-sized badge. Page 2: a
    second figure announced as "Figure 2", and the logo of every page."""

    import pymupdf

    document = pymupdf.open()
    photo = _noise(pymupdf, 240, 180)
    logo = _noise(pymupdf, 120, 120, seed=3)
    for index in range(3):
        page = document.new_page(width=612, height=792)
        page.insert_image(pymupdf.Rect(500, 30, 560, 90), stream=logo)  # on every page
        if index == 0:
            page.insert_text((60, 70), STEPS[0], fontsize=11)
            page.insert_text((60, 90), STEPS[1], fontsize=11)
            page.insert_text((60, 110), STEPS[2], fontsize=11)
            page.insert_image(pymupdf.Rect(90, 140, 330, 320), stream=photo)
            page.insert_text((92, 338), "Band in the tube before washing.", fontsize=10)
            page.insert_text((92, 360), "Keep the tube closed between washes.", fontsize=10)
            # A protocols.io-style note card: a picture of the box with its
            # text printed on top of it (a flat fill would already be out by
            # its pixel size; this one is out only by the text over it).
            page.insert_image(
                pymupdf.Rect(60, 400, 560, 520), stream=_noise(pymupdf, 300, 80, seed=9),
                keep_proportion=False,
            )
            page.insert_text((70, 430), "Note", fontsize=10)
            page.insert_text(
                (70, 460), "If the band is heavily stained, the wash can be repeated twice.",
                fontsize=10,
            )
            # A badge the size of a QR code.
            page.insert_image(pymupdf.Rect(480, 600, 578, 698), stream=_noise(pymupdf, 90, 90, seed=11))
        elif index == 1:
            page.insert_text((60, 70), "Expected result", fontsize=11)
            page.insert_image(pymupdf.Rect(120, 100, 400, 300), stream=_noise(pymupdf, 200, 150, seed=5))
            page.insert_text(
                (60, 320),
                "Figure 2. The band after the second wash, held against the light.",
                fontsize=10,
            )
    document.save(path)
    document.close()


class _SourcePdf(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.pdf = self.root / "source.pdf"
        build_source_pdf(self.pdf)
        self.sha256 = hashlib.sha256(self.pdf.read_bytes()).hexdigest()
        clear_protocol_pdf_cache()
        source_figures.clear_source_figure_cache()

    def tearDown(self) -> None:
        clear_protocol_pdf_cache()
        source_figures.clear_source_figure_cache()
        self.temp.cleanup()

    def fixture(self):
        """A text-built fixture whose steps sit on page 1, backed by the built PDF."""

        base = build_fixture(protocol_id="lane-wv-figures", title="Fictional lane WV protocol", steps=STEPS)
        return replace(base, source_pdf_path=self.pdf, source_pdf_sha256=self.sha256)


class EngineCropTests(_SourcePdf):
    """The one PDF engine reports where pictures sit and cuts them, in its child."""

    def test_the_engine_reports_each_picture_with_its_own_pixel_size(self) -> None:
        found = engine.page_images(self.pdf, (1,))
        self.assertEqual(len(found.pages), 1)
        page = found.pages[0]
        self.assertEqual((page.page_number, round(page.width), round(page.height)), (1, 612, 792))
        boxes = {(round(i.x0), round(i.y0), round(i.x1), round(i.y1)): (i.width_px, i.height_px) for i in page.images}
        self.assertEqual(boxes[(90, 140, 330, 320)], (240, 180))
        self.assertEqual(boxes[(60, 400, 560, 520)], (300, 80))
        self.assertEqual(boxes[(480, 600, 578, 698)], (90, 90))
        # The logo is placed on all three pages: a repeated xref.
        logo = next(i for i in page.images if round(i.x0) == 500)
        self.assertIn(logo.xref, found.repeated_xrefs)

    def test_a_clip_renders_to_png_at_a_bounded_size(self) -> None:
        (png,) = engine.render_clips_png(self.pdf, [(1, (90.0, 140.0, 330.0, 320.0))])
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        width, height = source_figures._png_size(png)
        self.assertEqual((width, height), (720, 540))  # 240 x 180 points at 3x
        self.assertEqual(engine.render_clips_png(self.pdf, []), ())

    def test_a_page_outside_the_document_is_a_document_error(self) -> None:
        with self.assertRaises(engine.PdfEngineDocumentError):
            engine.page_images(self.pdf, (9,))
        with self.assertRaises(engine.PdfEngineDocumentError):
            engine.render_clips_png(self.pdf, [(9, (0.0, 0.0, 10.0, 10.0))])


class FigureRuleTests(_SourcePdf):
    """Which placed pictures are figures, and which text is their caption."""

    def test_only_the_photograph_is_a_figure_on_page_one(self) -> None:
        figures = source_figures.figures_for_pages(self.pdf, self.sha256, [1])
        self.assertEqual([f.figure_id for f in figures], ["source-figure-1-1"])
        figure = figures[0]
        self.assertEqual(tuple(round(v) for v in figure.bounding_box), (90, 140, 330, 320))
        self.assertEqual(figure.caption, "Band in the tube before washing.")
        self.assertEqual(figure.mime_type, "image/png")
        self.assertEqual((figure.width_px, figure.height_px), (732, 552))  # with the 2-point margin
        self.assertEqual(figure.sha256, hashlib.sha256(
            source_figures.figure_content(self.pdf, self.sha256, figure.figure_id)[1]).hexdigest())

    def test_the_card_the_badge_and_the_logo_are_not_figures(self) -> None:
        found = engine.page_images(self.pdf, (1,))
        blocks = extract_protocol_pdf(self.pdf).pages[0].blocks
        boxes = source_figures.figure_boxes(found.pages[0], blocks, found.repeated_xrefs)
        self.assertEqual([tuple(round(v) for v in box) for box in boxes], [(90, 140, 330, 320)])
        # Without the text over it, the card is a picture like any other.
        boxes = source_figures.figure_boxes(found.pages[0], (), found.repeated_xrefs)
        self.assertIn((60, 400, 560, 520), [tuple(round(v) for v in box) for box in boxes])

    def test_a_caption_announcing_itself_may_start_at_the_margin(self) -> None:
        figures = source_figures.figures_for_pages(self.pdf, self.sha256, [2])
        self.assertEqual([f.figure_id for f in figures], ["source-figure-2-1"])
        self.assertEqual(
            figures[0].caption,
            "Figure 2. The band after the second wash, held against the light.",
        )

    def test_a_label_or_a_numbered_step_line_is_no_caption(self) -> None:
        block = lambda y0, text, x0=92.0: SimpleNamespace(x0=x0, y0=y0, x1=x0 + 200.0, y1=y0 + 12.0, text=text)  # noqa: E731
        box = (90.0, 140.0, 330.0, 320.0)
        self.assertEqual(source_figures.caption_under(box, [block(330.0, "Note")], 792.0), "")
        self.assertEqual(source_figures.caption_under(box, [block(330.0, "8Centrifuge the tube for a while.")], 792.0), "")
        self.assertEqual(source_figures.caption_under(box, [block(330.0, "2.1 Add the buffer to the tube now.")], 792.0), "")
        self.assertEqual(source_figures.caption_under(box, [block(400.0, "Too far under the picture.")], 792.0), "")
        self.assertEqual(source_figures.caption_under(box, [block(330.0, "Too far to the right.", x0=300.0)], 792.0), "")
        self.assertEqual(source_figures.caption_under(box, [block(330.0, "The band, seen from the side.")], 792.0), "The band, seen from the side.")
        long = "Figure 9. " + "x" * 700
        self.assertTrue(source_figures.caption_under(box, [block(330.0, long)], 792.0).endswith("…"))

    def test_tiles_of_one_figure_are_one_figure(self) -> None:
        page = engine.PdfPageImages(1, 612.0, 792.0, (
            engine.PdfImageInfo(100.0, 100.0, 300.0, 250.0, 11, 400, 300),
            engine.PdfImageInfo(302.0, 100.0, 500.0, 250.0, 12, 400, 300),
            engine.PdfImageInfo(100.0, 400.0, 300.0, 600.0, 13, 400, 400),
        ))
        boxes = source_figures.figure_boxes(page, (), frozenset())
        self.assertEqual([tuple(round(v) for v in box) for box in boxes], [(100, 100, 500, 250), (100, 400, 300, 600)])

    def test_the_frame_around_a_picture_is_dropped_for_the_picture(self) -> None:
        page = engine.PdfPageImages(1, 612.0, 792.0, (
            engine.PdfImageInfo(54.0, 100.0, 538.0, 500.0, 21, 2015, 1600),
            engine.PdfImageInfo(86.0, 150.0, 330.0, 360.0, 22, 1100, 980),
        ))
        boxes = source_figures.figure_boxes(page, (), frozenset())
        self.assertEqual([tuple(round(v) for v in box) for box in boxes], [(86, 150, 330, 360)])

    def test_a_picture_placed_off_the_page_is_clamped(self) -> None:
        page = engine.PdfPageImages(1, 612.0, 792.0, (
            engine.PdfImageInfo(71.0, 351.0, 825.0, 498.0, 31, 2000, 500),
        ))
        boxes = source_figures.figure_boxes(page, (), frozenset())
        self.assertEqual([tuple(round(v) for v in box) for box in boxes], [(71, 351, 612, 498)])


class CacheAndStepTests(_SourcePdf):
    def test_a_page_is_cut_once_and_a_step_reads_its_evidence_pages(self) -> None:
        fixture = self.fixture()
        self.assertIsNone(source_figures.cached_figures_for_step(fixture, 0))
        figures = source_figures.figures_for_step(fixture, 0)
        self.assertEqual([f.figure_id for f in figures], ["source-figure-1-1"])
        self.assertEqual(source_figures.cached_figures_for_step(fixture, 0), figures)
        with patch.object(engine, "page_images", side_effect=AssertionError("cut twice")):
            self.assertEqual(source_figures.figures_for_step(fixture, 1), figures)
        self.assertEqual(source_figures.step_pages(fixture.steps[0]), (1,))
        continued = SimpleNamespace(evidence=SimpleNamespace(source_page_number=1, continued_on_page_number=2))
        self.assertEqual(source_figures.step_pages(continued), (1, 2))
        self.assertEqual(
            [f.figure_id for f in source_figures.figures_for_pages(self.pdf, self.sha256, (1, 2))],
            ["source-figure-1-1", "source-figure-2-1"],
        )

    def test_without_a_pdf_or_with_another_file_nothing_is_cut(self) -> None:
        self.assertEqual(source_figures.figures_for_step(build_fixture(
            protocol_id="lane-wv-no-pdf", title="No PDF", steps=STEPS), 0), ())
        self.assertEqual(source_figures.cached_figures_for_step(build_fixture(
            protocol_id="lane-wv-no-pdf", title="No PDF", steps=STEPS), 0), ())
        self.assertEqual(source_figures.figures_for_pages(self.pdf, "0" * 64, [1]), ())
        self.assertIsNone(source_figures.figure_content(self.pdf, self.sha256, "source-figure-1-9"))
        self.assertIsNone(source_figures.figure_content(self.pdf, self.sha256, "../etc/passwd"))

    def test_an_engine_failure_costs_only_the_picture(self) -> None:
        with patch.object(engine, "page_images", side_effect=engine.PdfEngineError("down")):
            self.assertEqual(source_figures.figures_for_pages(self.pdf, self.sha256, [2]), ())
        # The failure is remembered for this run; the page is not retried on every turn.
        with patch.object(engine, "page_images", side_effect=AssertionError("retried")):
            self.assertEqual(source_figures.figures_for_pages(self.pdf, self.sha256, [2]), ())

    def test_the_public_shape_names_the_same_origin_route(self) -> None:
        figure = source_figures.figures_for_pages(self.pdf, self.sha256, [1])[0]
        shown = figure.public_dict(protocol_id="p/1", revision_id="r 1", source_sha256=self.sha256)
        self.assertEqual(shown["kind"], "source_figure")
        self.assertEqual(shown["url"], "/api/protocols/p%2F1/revisions/r%201/figures/source-figure-1-1")
        self.assertEqual(shown["label"], "원문 그림 · PDF p.1")
        self.assertEqual(shown["caption"], "Band in the tube before washing.")
        self.assertEqual(shown["source_document_id"], self.sha256)
        json.dumps(shown)


class PictureRequestReadingTests(unittest.TestCase):
    """The rules read a picture request, and a question for a value is not one."""

    def test_the_three_kinds_are_told_apart(self) -> None:
        for said, kind in (
            ("그림 보여줘", "source_figure"),
            ("사진 보여줘", "source_figure"),
            ("이 단계 그림 있어?", "source_figure"),
            ("이 단계 사진 있나요?", "source_figure"),
            ("그림 없어?", "source_figure"),
            ("이미지 좀 보여줘", "source_figure"),
            ("그림으로 그려 줘", "drawn_diagram"),
            ("도식으로 그려줘", "drawn_diagram"),
            ("이 단계 그려 줘", "drawn_diagram"),
            ("웹에서 찾아봐", "web_lookup"),
            ("써모믹서가 어떻게 생겼어?", "web_lookup"),
            ("젤 밴드 실제 사진 보여줘", "web_lookup"),
            ("인터넷에서 검색해 줘", "web_lookup"),
            ("what does a thermomixer look like", "web_lookup"),
            ("다음 단계 뭐야?", None),
            ("버퍼 얼마나 넣어?", None),
            ("", None),
        ):
            with self.subTest(said=said):
                self.assertEqual(picture_request_kind(said), kind)

    def test_the_thing_asked_about_is_read_from_the_words(self) -> None:
        self.assertEqual(web_lookup_subject("써모믹서가 어떻게 생겼어?"), "써모믹서")
        self.assertEqual(web_lookup_subject("젤 밴드 실제 사진 보여줘"), "젤 밴드")
        self.assertEqual(web_lookup_subject("이 단계 원심분리기는 어떤 모양이야"), "원심분리기")
        self.assertIsNone(web_lookup_subject("이게 어떻게 생겼어?"))
        self.assertIsNone(web_lookup_subject("웹에서 찾아봐"))

    def test_a_value_asked_with_a_picture_word_is_a_value_question(self) -> None:
        self.assertTrue(asked_about_values("그림에 나온 온도가 몇 도야?"))
        self.assertTrue(asked_about_values("사진 속 용액 얼마나 넣어?"))
        self.assertFalse(asked_about_values("그림 보여줘"))


class SessionWordsTests(_SourcePdf):
    """What the session says, and that nothing moves."""

    def _session(self, fixture=None, step_index: int = 0) -> CuratedProtocolSession:
        session = CuratedProtocolSession(fixture or self.fixture())
        session.activate_configured()
        session.plan("프로토콜 시작해줘", turn_id=1, language="ko", configuration_id=1, generation=1)
        session.current_index = step_index
        return session

    def _say(self, session, said: str, turn_id: int = 2):
        before = session.state()
        routed = route_curated_runtime_turn(
            session, said, turn_id=turn_id, language="ko", configuration_id=1, generation=1,
        )
        after = session.state()
        self.assertEqual(before["current_step_label"], after["current_step_label"], said)
        self.assertEqual(before["revision"], after["revision"], said)
        self.assertFalse(routed.plan.state_changed, said)
        return routed.plan

    def test_a_figure_on_the_page_is_said_to_be_on_the_screen(self) -> None:
        session = self._session()
        for turn, said in enumerate(("그림 보여줘", "이 단계 그림 있어?", "사진 보여줘"), start=2):
            plan = self._say(session, said, turn)
            self.assertIs(plan.action, CuratedProtocolAction.VISUAL_REQUEST, said)
            self.assertEqual(plan.visual_kind, "source_figure")
            self.assertEqual(
                plan.speech_text,
                "화면에 원문 그림을 띄웠어요. 원문 설명: Band in the tube before washing.",
            )
            self.assertIn("Cut the stained band out of the gel", plan.display_text)

    def test_a_page_without_a_figure_says_so_and_names_the_other_ways(self) -> None:
        session = self._session(build_fixture(protocol_id="lane-wv-no-pdf", title="No PDF", steps=STEPS))
        plan = self._say(session, "그림 보여줘")
        self.assertIs(plan.action, CuratedProtocolAction.VISUAL_REQUEST)
        self.assertEqual(
            plan.speech_text,
            "이 단계 원문에는 그림이 없어요. 웹에서 찾아보려면 '웹에서 찾아봐', "
            "도식이 필요하면 '그림으로 그려 줘'라고 말해 주세요.",
        )

    def test_web_and_drawing_requests_are_read_and_say_they_are_off(self) -> None:
        session = self._session()
        plan = self._say(session, "써모믹서가 어떻게 생겼어?")
        self.assertIs(plan.action, CuratedProtocolAction.VISUAL_REQUEST)
        self.assertEqual(plan.visual_kind, "web_lookup")
        self.assertEqual(plan.requested_entities, ("써모믹서",))
        self.assertEqual(
            plan.speech_text,
            "화면에 원문 그림을 띄웠어요. 써모믹서는 웹 찾아보기가 꺼져 있어 찾아보지 않았어요. 원문이 기준이에요.",
        )
        plan = self._say(session, "그림으로 그려 줘", 3)
        self.assertEqual(plan.visual_kind, "drawn_diagram")
        self.assertEqual(
            plan.speech_text,
            "화면에 원문 그림을 띄웠어요. 그림 그리기가 꺼져 있어 그리지 않았어요. 원문이 기준이에요.",
        )

    def test_a_value_question_with_a_picture_word_is_not_a_picture_request(self) -> None:
        session = self._session(step_index=1)
        plan = self._say(session, "그림에 나온 용액 얼마나 넣어?")
        self.assertIsNot(plan.action, CuratedProtocolAction.VISUAL_REQUEST)
        self.assertIn("500 µL", plan.display_text)

    def test_a_pronoun_looks_up_the_thing_just_asked_about_and_nothing_else(self) -> None:
        session = self._session(step_index=1)
        # Nothing recent: the pronoun names nothing, so nothing is looked up
        # (the older rules keep their reading of "그거 어떻게").
        plan = self._say(session, "그거 어떻게 생겼어?")
        self.assertIsNot(plan.action, CuratedProtocolAction.VISUAL_REQUEST)
        # After "용액 B가 뭐야?" the pronoun is solution B.
        asked = self._say(session, "용액 B가 뭐야?", 3)
        self.assertIsNot(asked.action, CuratedProtocolAction.VISUAL_REQUEST)
        plan = self._say(session, "그거 어떻게 생겼어?", 4)
        self.assertIs(plan.action, CuratedProtocolAction.VISUAL_REQUEST)
        self.assertEqual(plan.visual_kind, "web_lookup")
        self.assertEqual(plan.requested_entities, ("solution_b",))


class ServerScreenTests(_SourcePdf):
    """The screen fields carry the figures; the late event is sent for the step shown."""

    def _curated(self, fixture=None):
        session = CuratedProtocolSession(fixture or self.fixture())
        session.activate_configured()
        session.plan("프로토콜 시작해줘", turn_id=1, language="ko", configuration_id=1, generation=1)
        return session

    def test_screen_fields_say_not_yet_then_the_figures(self) -> None:
        curated = self._curated()
        self.assertIsNone(server_module.curated_screen_fields(curated)["source_figures"])
        source_figures.figures_for_step(curated.fixture, curated.current_index)
        shown = server_module.curated_screen_fields(curated)["source_figures"]
        self.assertEqual([item["figure_id"] for item in shown], ["source-figure-1-1"])
        self.assertEqual(shown[0]["protocol_id"], curated.fixture.protocol_id)
        self.assertEqual(shown[0]["revision_id"], curated.fixture.revision_id)
        self.assertEqual(shown[0]["source_document_id"], self.sha256)
        no_pdf = self._curated(build_fixture(protocol_id="lane-wv-no-pdf", title="No PDF", steps=STEPS))
        self.assertEqual(server_module.curated_screen_fields(no_pdf)["source_figures"], [])

    def test_the_late_event_names_the_step_the_pages_were_cut_for(self) -> None:
        curated = self._curated()
        sent: list[tuple[str, dict]] = []

        async def send(kind, **fields):
            sent.append((kind, fields))

        session = SimpleNamespace(active=True, accepted_configuration_id=7)
        asyncio.run(server_module._send_source_figures(session, curated, send, configuration_id=7))
        self.assertEqual(len(sent), 1)
        kind, fields = sent[0]
        self.assertEqual(kind, "protocol.figures.state")
        self.assertEqual(fields["configuration_id"], 7)
        self.assertEqual(fields["step_id"], curated.fixture.steps[0].step_id)
        self.assertEqual(fields["source_document_hash"], self.sha256)
        self.assertEqual([f["figure_id"] for f in fields["figures"]], ["source-figure-1-1"])
        # Moved on before the cut finished: nothing is sent.
        sent.clear()
        source_figures.clear_source_figure_cache()
        moved = SimpleNamespace(active=True, accepted_configuration_id=8)
        asyncio.run(server_module._send_source_figures(moved, curated, send, configuration_id=7))
        self.assertEqual(sent, [])

    def test_the_figure_route_serves_the_hash_labelled_bytes(self) -> None:
        from fastapi.testclient import TestClient

        fixture = self.fixture()
        figure, content = source_figures.figure_content(self.pdf, self.sha256, "source-figure-1-1")
        with patch.object(server_module, "_scope_catalog_resource", lambda protocol_id: None), \
             patch.object(server_module, "server_config", lambda: None), \
             patch.object(server_module, "_configured_candidate_fixture", lambda config: fixture):
            client = TestClient(server_module.app)
            response = client.get(
                f"/api/protocols/{fixture.protocol_id}/revisions/{fixture.revision_id}/figures/source-figure-1-1")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.content, content)
            self.assertEqual(response.headers["x-protocol-asset-sha256"], figure.sha256)
            self.assertEqual(response.headers["x-protocol-source-sha256"], self.sha256)
            self.assertEqual(response.headers["x-protocol-visual-kind"], "source_figure")
            self.assertEqual(response.headers["content-type"], "image/png")
            self.assertIn("sandbox", response.headers["content-security-policy"])
            self.assertEqual(client.get(
                f"/api/protocols/{fixture.protocol_id}/revisions/{fixture.revision_id}/figures/source-figure-1-9").status_code, 404)
            self.assertEqual(client.get(
                f"/api/protocols/{fixture.protocol_id}/revisions/other/figures/source-figure-1-1").status_code, 404)


PAGE_SETUP = r"""
acceptedSessionConfiguration={configuration_id:7,mode:"cascade",language:"ko",protocol_id:"protocol-x",revision_id:"rev-1"};
const sha="a".repeat(64);
const state={attached:true,protocol_id:"protocol-x",revision_id:"rev-1",display_name:"Fictional",development_only:false,readiness_status:"guidance_ready",active:true,current_step_label:"1",current_step_id:"step-1",total_steps:3,at_final_step:false,block_reason:null,revision:1,
 display_summary:"1 Cut the band.",primary_summary:"1단계: 밴드를 자릅니다.",source_language:"en",spoken_summary:"1단계입니다.",source_sha256:sha,source_filename:"source.pdf",
 warning_texts:[],warning_presentations:[],visual_assets:[],visual_status:"unavailable",source_page_refs:[1],workflow_status:"active"};
const figure={figure_id:"source-figure-1-1",kind:"source_figure",protocol_id:"protocol-x",revision_id:"rev-1",source_document_id:sha,source_page:1,bounding_box:[90,140,330,320],caption:"Band in the tube before washing.",mime_type:"image/png",sha256:"b".repeat(64),byte_size:1000,width_px:732,height_px:552,label:"원문 그림 · PDF p.1",url:"/api/protocols/protocol-x/revisions/rev-1/figures/source-figure-1-1"};
const send=(state,screen,extra={})=>onMessage({data:JSON.stringify({type:"protocol.fixture.state",configuration_id:7,state,screen,...extra})},sessionGeneration,socket);
const panel=()=>node("procedure-visual");
const images=()=>panel().children.flatMap(child=>child.tagName==="figure"?child.children.filter(item=>item.tagName==="img"):[]);
"""


class PageTests(unittest.TestCase):
    """The production page draws the figures with their captions, and only for the step shown."""

    def test_figures_are_drawn_with_their_source_caption(self) -> None:
        result = run_page_script(PAGE_SETUP + r"""
await send(state,{source_figures:[figure]});
assert(images().length===1&&images()[0].src===figure.url,`figure not drawn: ${JSON.stringify(panel().children.map(c=>c.tagName))}`);
assert(panel().textContent.includes("원문 캡션 · Band in the tube before washing."),`caption missing: ${panel().textContent}`);
assert(node("source-visual-state").textContent==="원문 그림",`state line: ${node("source-visual-state").textContent}`);
assert(panel().textContent.includes("원문 페이지 크게 보기"),"page link missing");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_figure_for_another_protocol_or_route_is_not_drawn(self) -> None:
        result = run_page_script(PAGE_SETUP + r"""
await send({...state,revision:2},{source_figures:[{...figure,protocol_id:"protocol-y"}]});
assert(images().length===0,"a figure of another protocol was trusted");
await send({...state,revision:3},{source_figures:[{...figure,url:"https://example.com/x.png"}]});
assert(images().length===0,"a remote url was trusted");
await send({...state,revision:4},{source_figures:[{...figure,source_document_id:"c".repeat(64)}]});
assert(images().length===0,"a figure of another source document was trusted");
assert(node("source-visual-state").textContent.includes("사용 가능한 시각 자료 없음"),`fallback line: ${node("source-visual-state").textContent}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_late_event_draws_for_the_step_shown_only(self) -> None:
        result = run_page_script(PAGE_SETUP + r"""
await send(state,{source_figures:null});
assert(node("source-visual-state").textContent==="원문 그림 확인 중",`not-yet line: ${node("source-visual-state").textContent}`);
const late=(fields)=>onMessage({data:JSON.stringify({type:"protocol.figures.state",configuration_id:7,protocol_id:"protocol-x",revision_id:"rev-1",source_document_hash:sha,step_id:"step-1",step_label:"1",figures:[figure],...fields})},sessionGeneration,socket);
await late({step_id:"step-2"});
assert(images().length===0,"figures of another step were drawn");
await late({});
assert(images().length===1,"late figures were not drawn for the step shown");
await send({...state,revision:2,current_step_label:"2",current_step_id:"step-2",source_page_refs:[1]},{source_figures:[]});
assert(images().length===0&&node("source-visual-state").textContent.includes("사용 가능한 시각 자료 없음"),"the next step kept the old figures");
""")
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
