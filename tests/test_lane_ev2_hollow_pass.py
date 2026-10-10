"""A hollow pass is not a pass, and a long document is not read at once (lane EV2, decision 4).

Human decision 2026-10-10. Lane AQ (10, 5 and 10) saw low reasoning and
Flash-Lite "pass" long documents with one or two steps, and a 197-page document
with 13. The server now counts the source's numbered steps from its text alone
(``count_source_numbered_steps``) and refuses an analysis that holds far fewer
of them -- "분석이 원문 단계를 다 담지 못했어요" with the advice to try again --
and refuses, before any model call, a document of more than 60 pages with the
advice to upload the pages that hold the procedure. The bounds are lane EV2's
measurement of the stored responses (report, decision 4).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from voiney_lab import experiment_protocol_analysis as analysis_module
from voiney_lab.experiment_protocol_analysis import (
    HOLLOW_MIN_SOURCE_STEPS,
    HOLLOW_STEP_SHARE,
    MAX_ANALYSIS_PAGES,
    ProtocolAnalysisIncompleteError,
    ProtocolAnalysisTooManyPagesError,
    analyze_protocol_extraction,
    count_source_numbered_steps,
    hollow_analysis_check,
)
from voiney_lab.experiment_protocol_config import ProtocolPersistenceSettings
from voiney_lab.experiment_protocol_pdf import (
    ProtocolPdfExtraction,
    ProtocolPdfMetadata,
    ProtocolPdfPage,
    clear_protocol_pdf_cache,
    extract_protocol_pdf,
)
from voiney_lab.experiment_protocol_store import initialize_protocol_store
from voiney_lab.protocol_catalog import ProtocolCatalog

TITLE = "Plasmid preparation"


def extraction(*pages: str) -> ProtocolPdfExtraction:
    return ProtocolPdfExtraction(
        original_filename="hollow.pdf", byte_size=1, sha256="a" * 64,
        media_type="application/pdf", page_count=len(pages), encrypted=False,
        metadata=ProtocolPdfMetadata(None, None, None, None, None, None, None),
        pages=tuple(ProtocolPdfPage(n, text, False) for n, text in enumerate(pages, start=1)),
    )


def numbered(count: int, start: int = 1, verb: str = "Add") -> str:
    return "\n".join(f"{n}. {verb} {n * 10} µL of buffer {n}." for n in range(start, start + count))


class TheSourceCountTests(unittest.TestCase):
    def test_numbered_lines_in_order_are_counted_across_sections_and_pages(self):
        source = extraction(
            f"{TITLE}\nA. Lysis\n{numbered(12)}",
            f"B. Wash\n{numbered(9)}",
        )
        self.assertEqual(count_source_numbered_steps(source), 21)

    def test_forms_of_a_step_number(self):
        lines = "\n".join(("1) Add buffer.", "(2) Mix gently.", "Step 3 Spin down.", "4 Discard the supernatant."))
        self.assertEqual(count_source_numbered_steps(extraction(f"{TITLE}\n{lines}")), 4)

    def test_values_and_section_numbers_are_not_steps(self):
        lines = "\n".join(("1.5 mL tube", "25 °C", "5.2 시약", "10% SDS"))
        self.assertEqual(count_source_numbered_steps(extraction(f"{TITLE}\n{lines}")), 0)

    def test_a_lone_number_or_a_broken_order_does_not_count(self):
        lines = "\n".join(("1. Add buffer.", "3. Mix.", "7. Spin."))
        self.assertEqual(count_source_numbered_steps(extraction(f"{TITLE}\n{lines}")), 0)

    def test_nothing_after_the_references_heading_counts(self):
        references = "\n".join(f"{n}. Doe J, Roe R. A method. J Biol Methods. 2019;{n}:1-9." for n in range(1, 41))
        source = extraction(f"{TITLE}\n{numbered(10)}\nReferences\n{references}")
        self.assertEqual(count_source_numbered_steps(source), 10)

    def test_a_numbered_citation_list_does_not_count_without_its_heading(self):
        citations = "\n".join(f"{n}. Doe J (2019) A method. Plant Methods {n}:1-9." for n in range(1, 41))
        source = extraction(f"{TITLE}\n{numbered(10)}\n{citations}")
        self.assertEqual(count_source_numbered_steps(source), 10)

    def test_running_page_numbers_do_not_count(self):
        source = extraction(
            f"1\n{TITLE}\n{numbered(3)}",
            f"2\n{numbered(3, start=4)}",
            f"3\n{numbered(3, start=7)}",
        )
        self.assertEqual(count_source_numbered_steps(source), 9)


def step(label: int) -> dict:
    text = f"Add {label * 10} µL of buffer {label}."
    return {
        "step_id": f"step-{label}", "source_label": str(label),
        "instruction_source_text": text,
        "evidence": {"source_page_number": 1, "source_excerpt": f"{label}. {text}"},
    }


def response(source: ProtocolPdfExtraction, labels) -> str:
    return json.dumps({
        "analysis_schema_version": 1, "pdf_sha256": source.sha256,
        "capability_policy_id": "p1-conservative",
        "protocol": {
            "protocol_id": "protocol-hollow",
            "metadata": {"title": TITLE, "original_language": "en",
                         "evidence": {"source_page_number": 1, "source_excerpt": TITLE}},
            "before_start": [], "materials": [], "equipment": [],
            "sections": [{
                "section_id": "lysis", "title_source_text": TITLE,
                "evidence": {"source_page_number": 1, "source_excerpt": TITLE},
                "steps": [step(label) for label in labels],
            }],
            "constructs": [], "description": None,
        },
    })


class _Model:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls = 0

    def analyze(self, *, system_prompt: str, input_json: str, response_schema: dict) -> str:
        self.calls += 1
        return self.reply


class TheHollowPassTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = extraction(f"{TITLE}\n{numbered(30)}")

    def test_the_bounds(self):
        self.assertEqual((HOLLOW_MIN_SOURCE_STEPS, HOLLOW_STEP_SHARE, MAX_ANALYSIS_PAGES), (20, 0.25, 60))

    def test_an_analysis_with_far_fewer_steps_is_refused(self):
        model = _Model(response(self.source, range(1, 3)))
        with self.assertRaises(ProtocolAnalysisIncompleteError) as raised:
            analyze_protocol_extraction(self.source, model)
        self.assertEqual(raised.exception.code, "protocol_analysis_incomplete")
        self.assertEqual((raised.exception.source_numbered_steps, raised.exception.analysis_steps), (30, 2))

    def test_an_analysis_with_a_quarter_of_the_steps_or_more_passes(self):
        draft = analyze_protocol_extraction(self.source, _Model(response(self.source, range(1, 9))))
        self.assertEqual(len(draft.protocol.sections[0].steps), 8)
        self.assertEqual(
            hollow_analysis_check(draft.protocol, self.source),
            {"source_numbered_steps": 30, "analysis_steps": 8, "hollow": False},
        )

    def test_a_source_with_few_numbered_steps_is_not_judged(self):
        source = extraction(f"{TITLE}\n{numbered(19)}")
        draft = analyze_protocol_extraction(source, _Model(response(source, range(1, 2))))
        self.assertEqual(len(draft.protocol.sections[0].steps), 1)

    def test_parsing_a_stored_response_is_not_judged(self):
        # The check is the model call's: a stored analysis is read as before.
        draft = analysis_module.parse_protocol_analysis_response(
            response(self.source, range(1, 3)), self.source)
        self.assertEqual(len(draft.protocol.sections[0].steps), 2)


class TheLongDocumentTests(unittest.TestCase):
    def test_more_than_sixty_pages_is_refused_before_the_model_is_called(self):
        source = extraction(*(f"{TITLE}\n{numbered(1, start=n)}" for n in range(1, 62)))
        model = _Model("{}")
        with self.assertRaises(ProtocolAnalysisTooManyPagesError) as raised:
            analyze_protocol_extraction(source, model)
        self.assertEqual(raised.exception.code, "protocol_analysis_too_many_pages")
        self.assertEqual(model.calls, 0)

    def test_sixty_pages_are_read(self):
        source = extraction(*(f"{TITLE} page {n}\nAdd buffer {n}." for n in range(1, 61)))
        model = _Model(response(source, ()))
        draft = analyze_protocol_extraction(source, model)
        self.assertEqual(model.calls, 1)
        self.assertEqual(draft.protocol.sections[0].steps, ())


def write_pages_pdf(path: Path, pages: list[str]) -> None:
    writer = PdfWriter()
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    font_ref = writer._add_object(font)
    for text in pages:
        page = writer.add_blank_page(width=612, height=792)
        content = DecodedStreamObject()
        lines = " ".join(
            f"({line.replace('(', '[').replace(')', ']')}) Tj 0 -14 Td" for line in text.split("\n"))
        content.set_data(f"BT /F1 11 Tf 72 720 Td {lines} ET".encode("ascii"))
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})})
        page[NameObject("/Contents")] = writer._add_object(content)
    with path.open("wb") as stream:
        writer.write(stream)


class CatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        clear_protocol_pdf_cache()
        self.store = initialize_protocol_store(ProtocolPersistenceSettings(True, self.root / "catalog"))
        self.catalog = ProtocolCatalog(self.store)

    def tearDown(self) -> None:
        self.store.close()
        clear_protocol_pdf_cache()
        self.temp.cleanup()

    def register(self, pages: list[str]) -> str:
        pdf = self.root / "doc.pdf"
        write_pages_pdf(pdf, pages)
        self.pdf = pdf
        return self.catalog.register(pdf, source_filename="doc.pdf", media_type="application/pdf").entry.protocol_id

    def analyze(self, protocol_id: str, model) -> Exception | None:
        analysis_id = "analysis-" + "b" * 32
        self.catalog.request_analysis(protocol_id, analysis_id)
        try:
            self.catalog.analyze(protocol_id, model, analysis_id=analysis_id)
        except Exception as exc:  # noqa: BLE001 - the test reads it
            return exc
        return None

    def failure_payload(self, protocol_id: str) -> dict:
        return next(
            event.payload for event in reversed(self.store.list_events(protocol_id))
            if event.event_type == "protocol_analysis_failed"
        )

    def test_a_hollow_analysis_is_recorded_and_said_with_the_advice_to_retry(self):
        protocol_id = self.register([f"{TITLE}\n" + "\n".join(
            f"{n}. Add {n * 10} uL of buffer {n}." for n in range(1, 31))])
        extraction_ = extract_protocol_pdf(self.pdf)
        reply = json.loads(response(extraction_, range(1, 3)))
        for item in reply["protocol"]["sections"][0]["steps"]:
            item["instruction_source_text"] = item["instruction_source_text"].replace("µL", "uL")
            item["evidence"]["source_excerpt"] = item["evidence"]["source_excerpt"].replace("µL", "uL")
        model = _Model(json.dumps(reply))
        error = self.analyze(protocol_id, model)
        self.assertIsInstance(error, ProtocolAnalysisIncompleteError)
        self.assertEqual(model.calls, 1)  # not retried by itself
        payload = self.failure_payload(protocol_id)
        self.assertEqual(payload["failure_code"], "protocol_analysis_incomplete")
        self.assertEqual(payload["step_counts"], {"source_numbered_steps": 30, "analysis_steps": 2})
        pipeline = self.catalog.pipeline_status(protocol_id)
        self.assertTrue(pipeline["blocked"])
        self.assertIn("분석이 원문 단계를 다 담지 못했어요", pipeline["message"])
        self.assertIn("분석 다시 시도", pipeline["action"])
        review = self.catalog.review(protocol_id)
        self.assertFalse(review["available_for_execution"])
        self.assertTrue(review["analysis_failure"]["retryable"])

    def test_a_document_over_sixty_pages_is_refused_with_the_scope_advice(self):
        protocol_id = self.register([f"{TITLE} page {n}\n{n}. Add buffer." for n in range(1, 62)])
        model = _Model("{}")
        error = self.analyze(protocol_id, model)
        self.assertIsInstance(error, ProtocolAnalysisTooManyPagesError)
        self.assertEqual(model.calls, 0)
        self.assertEqual(self.failure_payload(protocol_id)["failure_code"], "protocol_analysis_too_many_pages")
        pipeline = self.catalog.pipeline_status(protocol_id)
        self.assertTrue(pipeline["blocked"])
        self.assertIn("60쪽", pipeline["message"])
        self.assertIn("절차가 있는 쪽만", pipeline["action"])
        self.assertFalse(self.catalog.review(protocol_id)["analysis_failure"]["retryable"])


if __name__ == "__main__":
    unittest.main()
