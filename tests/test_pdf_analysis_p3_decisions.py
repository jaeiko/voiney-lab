"""PDF analysis decisions left open by lane P2 (lane P3, human decisions 2026-10-05).

Each group pins one decision:

a. Korean OCR line breaks: in evidence comparison only, on a page whose text
   came from OCR, a line break between two Hangul letters is joined ("날⏎짜"
   compares as "날짜"), like the line-end hyphen rule. Page text, its hash and
   evidence identities are untouched; a break between digits is not joined,
   and a text-layer page is compared as before.
b. Unnumbered sources: when the source prints no step numbers, source_label
   is left empty (prompt and validation). The screen numbers such steps in
   order. A label the source does not print is refused as before.
c. An analysis call that runs out of time has its own failure code,
   ``protocol_analysis_timeout``, with Korean text on the screen.
d. OCR "check the numbers": the page's running-footer band and letters drawn
   inside pictures are left out of the two engines' number comparison.
"""

from __future__ import annotations

import asyncio
import json
import os
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
import openai

from voiney_lab import experiment_protocol as domain
from voiney_lab import server
from voiney_lab.experiment_protocol_analysis import (
    ANALYSIS_SYSTEM_PROMPT,
    OpenAICompatibleProtocolAnalysisModel,
    ProtocolAnalysisEvidenceError,
    ProtocolAnalysisModelError,
    parse_protocol_analysis_response,
)
from voiney_lab.experiment_protocol_config import ProtocolPersistenceSettings
from voiney_lab.experiment_protocol_pdf import (
    ProtocolPdfExtraction,
    ProtocolPdfMetadata,
    ProtocolPdfPage,
)
from voiney_lab.experiment_protocol_store import initialize_protocol_store
from voiney_lab.model_providers import ModelProviderError
from voiney_lab.pdf_text_engine import BOTTOM_BAND_FRACTION, PdfTextBlock
from voiney_lab.protocol_catalog import ProtocolCatalog, SharedSecretApprovalPolicy
from voiney_lab.protocol_ocr import OcrPage, OcrResult
from voiney_lab import protocol_ocr_providers as ocrp
from tests.test_protocol_catalog import write_text_pdf
from tests.test_protocol_ocr import _blank_pdf

ROOT = Path(__file__).resolve().parents[1]

# An OCR page shaped like the reagent-kit guide (lane P2): CLOVA broke two
# words in the middle of a line.
OCR_PAGE = "\n".join(
    (
        "시약 키트 사용 설명서",
        "1. 개봉한 날",
        "짜를 용기에 기록합니다.",
        "2. 국",
        "제적인 기준에 따라 보관합니다.",
        "3. 시료 1",
        "5 mL 를 넣습니다.",
        "4. 시료를 A",
        "B 에 넣습니다.",
    )
)


def extraction(text: str = OCR_PAGE, *, ocr: bool = True) -> ProtocolPdfExtraction:
    return ProtocolPdfExtraction(
        original_filename="ocr.pdf",
        byte_size=1,
        sha256="e" * 64,
        media_type="application/pdf",
        page_count=1,
        encrypted=False,
        metadata=ProtocolPdfMetadata(None, None, None, None, None, None, None),
        pages=(ProtocolPdfPage(1, text, False, ocr_derived=ocr),),
    )


def evidence(excerpt: str, page: int = 1) -> dict:
    return {"source_page_number": page, "source_excerpt": excerpt}


def step(label: str, excerpt: str, instruction: str | None = None, *, step_id: str | None = None) -> dict:
    return {
        "step_id": step_id or f"step-{label or 'x'}",
        "source_label": label,
        "instruction_source_text": instruction or excerpt,
        "evidence": evidence(excerpt),
    }


def response(*steps: dict, title: str = "시약 키트 사용 설명서", sha: str = "e" * 64) -> dict:
    return {
        "analysis_schema_version": 1,
        "pdf_sha256": sha,
        "capability_policy_id": "p1-conservative",
        "protocol": {
            "protocol_id": "protocol-p3",
            "metadata": {"title": title, "original_language": "ko", "evidence": evidence(title)},
            "before_start": [],
            "materials": [],
            "equipment": [],
            "sections": [
                {
                    "section_id": "procedure",
                    "title_source_text": title,
                    "evidence": evidence(title),
                    "steps": list(steps),
                }
            ],
            "constructs": [],
            "description": None,
        },
    }


def parse(payload: dict, source: ProtocolPdfExtraction | None = None):
    return parse_protocol_analysis_response(json.dumps(payload), source or extraction())


# --- a. Korean OCR line breaks ------------------------------------------------


class KoreanOcrLineBreakTests(unittest.TestCase):
    def test_a_claim_written_across_a_hangul_line_break_is_supported(self):
        draft = parse(response(step("1", "1. 개봉한 날\n짜를 용기에 기록합니다.",
                                    "개봉한 날짜를 용기에 기록합니다.")))
        self.assertEqual(
            draft.protocol.sections[0].steps[0].instruction_source_text,
            "개봉한 날짜를 용기에 기록합니다.",
        )

    def test_an_excerpt_written_joined_is_found_and_kept_as_the_page_prints_it(self):
        draft = parse(response(step("2", "2. 국제적인 기준에 따라 보관합니다.")))
        # Projected back onto the page's own characters, line break included.
        self.assertEqual(
            draft.protocol.sections[0].steps[0].evidence.source_excerpt,
            "2. 국\n제적인 기준에 따라 보관합니다.",
        )

    def test_the_page_text_and_its_hash_are_untouched(self):
        source = extraction()
        before = source.pages[0].text
        parse(response(step("1", "1. 개봉한 날짜를 용기에 기록합니다.")), source)
        self.assertEqual(source.pages[0].text, before)
        self.assertEqual(source.sha256, "e" * 64)

    def test_the_spaced_form_is_still_accepted(self):
        parse(response(step("1", "1. 개봉한 날 짜를 용기에 기록합니다.")))

    def test_a_line_break_between_digits_is_not_joined(self):
        with self.assertRaises(ProtocolAnalysisEvidenceError):
            parse(response(step("3", "3. 시료 15 mL 를 넣습니다.")))

    def test_a_line_break_between_latin_letters_is_not_joined(self):
        with self.assertRaises(ProtocolAnalysisEvidenceError):
            parse(response(step("4", "4. 시료를 AB 에 넣습니다.")))

    def test_a_text_layer_page_is_compared_as_before(self):
        with self.assertRaises(ProtocolAnalysisEvidenceError):
            parse(response(step("1", "1. 개봉한 날짜를 용기에 기록합니다.")),
                  extraction(ocr=False))

    def test_the_catalog_marks_only_ocr_pages_for_analysis(self):
        root = Path(tempfile.mkdtemp())
        source = root / "scanned.pdf"
        _blank_pdf(source)
        store = initialize_protocol_store(ProtocolPersistenceSettings(True, root / "catalog"))
        catalog = ProtocolCatalog(store)
        try:
            entry = catalog.register(source, source_filename="scanned.pdf",
                                     media_type="application/pdf").entry

            class Provider:
                def recognize(self, source_pdf, *, source_sha256, page_count):
                    return OcrResult(
                        source_sha256=source_sha256, provider="clova", provider_version="v2",
                        pages=(OcrPage(1, OCR_PAGE, 0.9, provider="clova", provider_version="v2"),
                               OcrPage(2, "Kept text layer", None, provider=ocrp.TEXT_LAYER,
                                       provider_version="pymupdf-1")),
                    )

            catalog.run_ocr(entry.protocol_id, Provider(), ocr_id="ocr-p3")
            catalog.review_ocr(entry.protocol_id, decision="accepted",
                               policy=SharedSecretApprovalPolicy("s"), presented_secret="s")
            revision = catalog._latest_protocol_revision(entry.protocol_id)
            from voiney_lab.experiment_protocol_pdf import extract_protocol_pdf

            analysed = catalog._extraction_for_analysis(revision, extract_protocol_pdf(source))
        finally:
            store.close()
        self.assertEqual([page.ocr_derived for page in analysed.pages], [True, False])
        self.assertEqual(analysed.pages[0].text, OCR_PAGE)


# --- b. sources without step numbers -------------------------------------------

UNNUMBERED_PAGE = "\n".join(
    (
        "시약 키트 사용 설명서",
        "검체의 준비",
        "검체를 멸균 용기에 담습니다.",
        "시약을 넣고 섞습니다.",
        "1. 시약을 보관합니다.",
    )
)


class UnnumberedSourceTests(unittest.TestCase):
    def source(self) -> ProtocolPdfExtraction:
        return extraction(UNNUMBERED_PAGE, ocr=False)

    def test_steps_of_an_unnumbered_source_carry_an_empty_label(self):
        draft = parse(response(step("", "검체를 멸균 용기에 담습니다.", step_id="s1"),
                               step("", "시약을 넣고 섞습니다.", step_id="s2")), self.source())
        self.assertEqual([s.source_label for s in draft.protocol.sections[0].steps], ["", ""])

    def test_a_heading_used_as_a_label_is_refused_as_before(self):
        with self.assertRaises(ProtocolAnalysisEvidenceError) as caught:
            parse(response(step("검체의 준비", "검체를 멸균 용기에 담습니다.")), self.source())
        self.assertEqual(caught.exception.diagnostic.reason_code, "source_label_not_found")

    def test_an_empty_label_for_a_printed_number_is_refused(self):
        with self.assertRaises(ProtocolAnalysisEvidenceError) as caught:
            parse(response(step("", "1. 시약을 보관합니다.")), self.source())
        self.assertEqual(caught.exception.diagnostic.reason_code, "source_label_not_found")
        self.assertEqual(caught.exception.diagnostic.field_path,
                         "protocol.sections[0].steps[0].source_label")
        with self.assertRaises(ProtocolAnalysisEvidenceError):
            parse(response(step("", "시약을 보관합니다.")), self.source())

    def test_a_protocol_mixing_labelled_and_empty_steps_is_refused(self):
        with self.assertRaises(Exception) as caught:
            parse(response(step("1", "1. 시약을 보관합니다.", step_id="s1"),
                           step("", "시약을 넣고 섞습니다.", step_id="s2")), self.source())
        cause = caught.exception.__cause__
        self.assertIsInstance(cause, domain.ProtocolValidationError)
        self.assertIs(cause.code, domain.ProtocolValidationCode.MISSING_SOURCE_LABEL)

    def test_the_prompt_asks_for_an_empty_label_when_nothing_is_numbered(self):
        prompt = " ".join(ANALYSIS_SYSTEM_PROMPT.split())
        self.assertIn("prints no step numbers", prompt)
        self.assertIn("leave source_label empty", prompt)
        self.assertIn("never use a heading", prompt.casefold())

    def test_the_screen_numbers_unlabelled_steps_in_order(self):
        root = Path(tempfile.mkdtemp())
        pdf = root / "beta.pdf"
        write_text_pdf(pdf, "Protocol Beta\nPrepare the sample tube.\nAdd the reagent and mix.\nWear gloves.",
                       title="Protocol Beta")
        store = initialize_protocol_store(ProtocolPersistenceSettings(True, root / "catalog"))
        catalog = ProtocolCatalog(store)

        class Model:
            def analyze(self, *, system_prompt, input_json, response_schema):
                sha = json.loads(input_json)["pdf"]["sha256"]
                payload = response(step("", "Prepare the sample tube.", step_id="s1"),
                                   step("", "Add the reagent and mix.", step_id="s2"),
                                   title="Protocol Beta", sha=sha)
                payload["protocol"]["metadata"]["original_language"] = "en"
                return json.dumps(payload)

        try:
            entry = catalog.register(pdf, source_filename="beta.pdf", media_type="application/pdf").entry
            catalog.request_analysis(entry.protocol_id, "analysis-beta")
            catalog.analyze(entry.protocol_id, Model(), analysis_id="analysis-beta")
            review = catalog.review(entry.protocol_id)
            stored = catalog._latest_analysis(catalog._latest_protocol_revision(entry.protocol_id))
        finally:
            store.close()
        steps = review["sections"][0]["steps"]
        self.assertEqual([s["source_label"] for s in steps], ["1", "2"])
        self.assertEqual([s["source_label_printed"] for s in steps], [False, False])
        # The stored analysis keeps what the source prints: nothing.
        self.assertEqual([s.source_label for s in stored.protocol.sections[0].steps], ["", ""])

    def test_execution_numbers_unlabelled_steps_in_order(self):
        from voiney_lab.protocol_catalog import protocol_with_display_step_labels

        draft = parse(response(step("", "검체를 멸균 용기에 담습니다.", step_id="s1"),
                               step("", "시약을 넣고 섞습니다.", step_id="s2")), self.source())
        shown = protocol_with_display_step_labels(draft.protocol)
        self.assertEqual([s.source_label for s in shown.sections[0].steps], ["1", "2"])
        numbered = parse(response(step("1", "1. 시약을 보관합니다.")), self.source()).protocol
        self.assertIs(protocol_with_display_step_labels(numbered), numbered)

    def test_domain_validation_accepts_all_empty_and_refuses_a_mix(self):
        protocol = parse(response(step("", "검체를 멸균 용기에 담습니다.", step_id="s1"),
                                  step("", "시약을 넣고 섞습니다.", step_id="s2")),
                         self.source()).protocol
        domain.validate_protocol(protocol)
        section = protocol.sections[0]
        mixed = section.steps[0].__class__(**{**section.steps[0].__dict__, "source_label": "1"})
        broken = protocol.__class__(**{**protocol.__dict__, "sections": (
            section.__class__(**{**section.__dict__, "steps": (mixed, section.steps[1])}),)})
        with self.assertRaises(domain.ProtocolValidationError):
            domain.validate_protocol(broken)


# --- c. analysis time-out ------------------------------------------------------


class AnalysisTimeoutCodeTests(unittest.TestCase):
    def model(self, error: Exception) -> OpenAICompatibleProtocolAnalysisModel:
        client = Mock()
        client.chat.completions.create.side_effect = error
        return OpenAICompatibleProtocolAnalysisModel(client, "m", "high")

    def analyze(self, model):
        return model.analyze(system_prompt="s", input_json="{}", response_schema={})

    def test_an_sdk_timeout_has_its_own_code(self):
        error = openai.APITimeoutError(request=httpx.Request("POST", "https://api.x.ai/v1/chat"))
        with self.assertRaises(ProtocolAnalysisModelError) as caught:
            self.analyze(self.model(error))
        self.assertEqual(caught.exception.code, "protocol_analysis_timeout")

    def test_an_adapter_timeout_has_its_own_code(self):
        for provider in ("anthropic", "openai", "google"):
            with self.subTest(provider=provider), self.assertRaises(ProtocolAnalysisModelError) as caught:
                self.analyze(self.model(ModelProviderError(provider, "timeout")))
            self.assertEqual(caught.exception.code, "protocol_analysis_timeout")

    def test_other_provider_failures_keep_the_model_failed_code(self):
        for error in (ModelProviderError("openai", "invalid_request", 400), RuntimeError("x")):
            with self.subTest(error=error), self.assertRaises(ProtocolAnalysisModelError) as caught:
                self.analyze(self.model(error))
            self.assertEqual(caught.exception.code, "protocol_analysis_model_failed")

    def test_the_screen_has_korean_text_for_the_code(self):
        html = (ROOT / "src/voiney_lab/static/index.html").read_text(encoding="utf-8")
        self.assertIn('protocol_analysis_timeout:"분석 시간 초과', html)

    def test_a_timed_out_background_analysis_reads_as_its_code_and_can_be_retried(self):
        root = Path(tempfile.mkdtemp())
        pdf = root / "alpha.pdf"
        write_text_pdf(pdf, "Protocol Alpha\nSection preparation\n1. Add solution.\nWear gloves.",
                       title="Protocol Alpha")
        settings = ProtocolPersistenceSettings(True, root / "catalog")
        store = initialize_protocol_store(settings)
        try:
            entry = ProtocolCatalog(store).register(
                pdf, source_filename="alpha.pdf", media_type="application/pdf").entry
        finally:
            store.close()

        def open_catalog():
            opened = initialize_protocol_store(settings)
            return ProtocolCatalog(opened), opened

        model = self.model(ModelProviderError("google", "timeout"))

        async def scenario():
            await server.trigger_protocol_analysis(entry.protocol_id)
            await server._PROTOCOL_ANALYSIS_TASKS[entry.protocol_id]
            await asyncio.sleep(0)

        async def to_thread(function, /, *args, **kwargs):
            return function(*args, **kwargs)

        with patch.object(server, "_open_protocol_catalog", side_effect=open_catalog), \
                patch.object(server.asyncio, "to_thread", side_effect=to_thread), \
                patch.object(server, "_protocol_analysis_model", return_value=model):
            asyncio.run(scenario())
            status = server.get_protocol_analysis_status(entry.protocol_id)
        self.assertEqual(status["state"], "analysis_failed")
        self.assertEqual(status["failure_code"], "protocol_analysis_timeout")
        catalog, opened = open_catalog()
        try:
            failure = catalog.review(entry.protocol_id)["analysis_failure"]
        finally:
            opened.close()
        self.assertEqual(failure["code"], "protocol_analysis_timeout")
        self.assertIs(failure["retryable"], True)
        self.assertIn("VOINEY_LAB_PROTOCOL_ANALYSIS_TIMEOUT_SECONDS", failure["action"])


# --- d. OCR number comparison ---------------------------------------------------


def png(width: int, height: int) -> bytes:
    """A real (blank) PNG of the given size: the provider reads its header."""

    raw = b"".join(b"\x00" + b"\x00" * width for _ in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


# A 600 x 792 pt page rendered at 300 dpi: 2500 x 3300 px, 300/72 px per pt.
WIDTH, HEIGHT, SCALE = 2500, 3300, 300 / 72
FOOTER_Y = HEIGHT * (1 - BOTTOM_BAND_FRACTION / 2)
BODY_BLOCK = PdfTextBlock(72, 100, 520, 400, 11.0, False, "body")


def word(text: str, x_pt: float, y_px: float, line_break: bool = False) -> ocrp.OcrWord:
    x = x_pt * SCALE
    return ocrp.OcrWord(text, x, y_px, x + 40 * SCALE, y_px + 40, "\n" if line_break else " ")


def body(*, value: str = "500") -> tuple[ocrp.OcrWord, ...]:
    y = 200 * SCALE
    return (word("Add", 80, y), word(value, 120, y), word("µL", 160, y, True))


def outcome(name: str, words: tuple[ocrp.OcrWord, ...]):
    text = "".join(w.text + w.after for w in words)
    engine = Mock()
    engine.name, engine.version = name, f"{name}-1"
    return ocrp._EngineOutcome(engine, ocrp.RecognizedPage(text=text, confidence=0.9, words=words), None)


class OcrNumberComparisonTests(unittest.TestCase):
    def region(self, blocks=(BODY_BLOCK,)) -> ocrp.NumericRegion:
        return ocrp.NumericRegion.from_page(png(WIDTH, HEIGHT), dpi=300, blocks=blocks)

    def review(self, clova_words, google_words, region=None) -> bool:
        selection = ocrp.select_page_text(
            1, (outcome(ocrp.CLOVA, clova_words), outcome(ocrp.GOOGLE, google_words)),
            region=region if region is not None else self.region())
        return selection.page.numeric_review_required

    def test_a_footer_band_difference_is_left_out(self):
        clova = body() + (word("December", 80, FOOTER_Y), word("14,2019", 140, FOOTER_Y))
        google = body() + (word("December", 80, FOOTER_Y), word("14,", 140, FOOTER_Y),
                           word("2019", 160, FOOTER_Y))
        self.assertFalse(self.review(clova, google))

    def test_a_body_difference_is_still_marked(self):
        self.assertTrue(self.review(body(), body(value="600")))

    def test_letters_drawn_outside_the_text_layer_are_left_out(self):
        picture_y = 600 * SCALE  # below the only text block: a photo
        clova = body() + (word("46/13", 100, picture_y),)
        google = body() + (word("ANKOM", 100, picture_y), word("28816", 140, picture_y))
        self.assertFalse(self.review(clova, google))

    def test_on_a_scan_without_a_text_layer_nothing_is_taken_for_a_picture(self):
        picture_y = 600 * SCALE
        clova = body() + (word("46/13", 100, picture_y),)
        google = body() + (word("28816", 140, picture_y),)
        self.assertTrue(self.review(clova, google, region=self.region(blocks=())))

    def test_without_word_boxes_the_whole_texts_are_compared_as_before(self):
        clova = ocrp._EngineOutcome(outcome(ocrp.CLOVA, ()).engine,
                                    ocrp.RecognizedPage("Add 500 µL\nDecember 14,2019"), None)
        google = ocrp._EngineOutcome(outcome(ocrp.GOOGLE, ()).engine,
                                     ocrp.RecognizedPage("Add 500 µL\nDecember 14, 2019"), None)
        selection = ocrp.select_page_text(1, (clova, google), region=self.region())
        self.assertTrue(selection.page.numeric_review_required)

    def test_the_chosen_text_is_unchanged(self):
        # English, so Google's text is chosen; its footer stays in that text.
        google = body() + (word("December", 80, FOOTER_Y), word("14,2019", 140, FOOTER_Y))
        selection = ocrp.select_page_text(
            1, (outcome(ocrp.CLOVA, body()), outcome(ocrp.GOOGLE, google)), region=self.region())
        self.assertEqual(selection.page.provider, ocrp.GOOGLE)
        self.assertIn("14,2019", selection.page.text)
        self.assertFalse(selection.page.numeric_review_required)

    def test_clova_field_boxes_are_read(self):
        session = Mock()
        session.post.return_value = Mock(status_code=200, content=b"x", json=lambda: {
            "images": [{"inferResult": "SUCCESS", "fields": [
                {"inferText": "Add", "lineBreak": False, "inferConfidence": 0.9,
                 "boundingPoly": {"vertices": [{"x": 10, "y": 20}, {"x": 50, "y": 20},
                                               {"x": 50, "y": 40}, {"x": 10, "y": 40}]}},
                {"inferText": "500", "lineBreak": True, "inferConfidence": 0.9}]}]})
        page = ocrp.ClovaOcrRecognizer("https://clova.invalid/x", "s", session=session).recognize_page(
            b"png", page_number=1)
        self.assertEqual(page.text, "Add 500")
        self.assertEqual(page.words[0], ocrp.OcrWord("Add", 10, 20, 50, 40, " "))
        self.assertEqual(len(page.words), 1)  # a field without a box is not placed

    def test_google_word_boxes_are_read(self):
        def symbol(text, brk=None):
            out = {"text": text}
            if brk:
                out["property"] = {"detectedBreak": {"type": brk}}
            return out

        session = Mock()
        session.post.return_value = Mock(status_code=200, content=b"x", json=lambda: {"responses": [{
            "fullTextAnnotation": {"text": "Add 500\n", "pages": [{"confidence": 0.9, "blocks": [{
                "paragraphs": [{"words": [
                    {"boundingBox": {"vertices": [{"x": 1, "y": 2}, {"x": 9, "y": 2}, {"x": 9, "y": 8}, {"y": 8}]},
                     "symbols": [symbol("A"), symbol("d"), symbol("d", "SPACE")]},
                    {"boundingBox": {"vertices": [{"x": 11, "y": 2}, {"x": 19, "y": 2}, {"x": 19, "y": 8}, {"x": 11, "y": 8}]},
                     "symbols": [symbol("5"), symbol("0"), symbol("0")]},
                    {"boundingBox": {"vertices": [{"x": 20, "y": 2}, {"x": 29, "y": 2}, {"x": 29, "y": 8}, {"x": 20, "y": 8}]},
                     "symbols": [symbol("°"), symbol("C", "LINE_BREAK")]},
                ]}]}]}]}}]})
        page = ocrp.GoogleVisionRecognizer("k", session=session).recognize_page(b"png", page_number=1)
        # No break after "500": Google runs it into "°C", as its own text does.
        self.assertEqual(page.words, (ocrp.OcrWord("Add", 0, 2, 9, 8, " "),
                                      ocrp.OcrWord("500", 11, 2, 19, 8, ""),
                                      ocrp.OcrWord("°C", 20, 2, 29, 8, "\n")))
        self.assertEqual(ocrp.numeric_tokens(ocrp._comparison_text(page, ocrp.NumericRegion(100, 100, None, ()))),
                         ocrp.numeric_tokens("Add 500°C"))

    def test_the_provider_measures_the_rendered_page_and_its_text_blocks(self):
        region = ocrp.NumericRegion.from_page(png(WIDTH, HEIGHT), dpi=300, blocks=(BODY_BLOCK,))
        self.assertEqual((region.width, region.height), (WIDTH, HEIGHT))
        self.assertAlmostEqual(region.pixels_per_point, SCALE)
        self.assertEqual(region.excluded(word("x", 80, FOOTER_Y)), "footer_band")
        self.assertEqual(region.excluded(word("x", 100, 600 * SCALE)), "outside_text_layer")
        self.assertIsNone(region.excluded(body()[0]))
        self.assertIsNone(ocrp.NumericRegion.from_page(b"not a png", dpi=300, blocks=()))


if __name__ == "__main__":
    unittest.main()
