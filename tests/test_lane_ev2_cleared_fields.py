"""A field execution never reads is emptied, not the analysis refused (lane EV2, decision 2).

Human decision 2026-10-10: when the only evidence failures of an analysis are in
fields execution never reads -- the title, the authors, the dates, the version,
the DOI, the source URI, the license, the source status, the description, a
section's title -- those fields are emptied and the analysis passes. The
emptying is recorded on the protocol (``cleared_fields``), in the analysis-ready
event, and shown before the start as one line ("원문에서 확인하지 못해 비운 칸:
저자·제목"). Step labels are not among them (they order the run), nor is anything
a step, a value, a timer, a safety statement, a completion criterion, a material,
a piece of equipment, a prerequisite or a construct holds: those still refuse.
"""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from tests.test_protocol_catalog import write_text_pdf
from voiney_lab import experiment_protocol as domain
from voiney_lab.experiment_protocol_analysis import (
    ANALYSIS_RESPONSE_SCHEMA,
    ProtocolAnalysisEvidenceError,
    ProtocolAnalysisResponseError,
    parse_protocol_analysis_response,
)
from voiney_lab.experiment_protocol_config import ProtocolPersistenceSettings
from voiney_lab.experiment_protocol_pdf import (
    ProtocolPdfExtraction,
    ProtocolPdfMetadata,
    ProtocolPdfPage,
    clear_protocol_pdf_cache,
    extract_protocol_pdf,
)
from voiney_lab.experiment_protocol_store import (
    deserialize_analysis,
    initialize_protocol_store,
    serialize_analysis,
)
from voiney_lab.protocol_catalog import ProtocolCatalog

ROOT = Path(__file__).resolve().parents[1]
TITLE = "Measuring Relative Telomere Length"
PAGE_1 = (
    f"{TITLE}\n"
    "Jane Doe, John Roe\n"
    "Published: 3 April 2020\n"
    "This protocol measures telomere length by qPCR.\n"
    "Procedure\n"
    "1. Add 5 mL buffer to the tube.\n"
    "2. Incubate for 10 min at 37 °C.\n"
    "Note: Wear gloves.\n"
    "Materials\n"
    "Tris buffer\n"
)


def source() -> ProtocolPdfExtraction:
    return ProtocolPdfExtraction(
        original_filename="cleared.pdf", byte_size=1, sha256="c" * 64,
        media_type="application/pdf", page_count=1, encrypted=False,
        metadata=ProtocolPdfMetadata(None, None, None, None, None, None, None),
        pages=(ProtocolPdfPage(1, PAGE_1, False),),
    )


def evidence(excerpt: str) -> dict:
    return {"source_page_number": 1, "source_excerpt": excerpt}


def response() -> dict:
    return {
        "analysis_schema_version": 1,
        "pdf_sha256": "c" * 64,
        "capability_policy_id": "p1-conservative",
        "protocol": {
            "protocol_id": "protocol-cleared",
            "metadata": {
                "title": TITLE, "original_language": "en", "evidence": evidence(TITLE),
                "authors": ["Jane Doe", "John Roe"],
                "authors_evidence": evidence("Jane Doe, John Roe"),
                "publication_date": "2020-04-03",
                "publication_date_evidence": evidence("Published: 3 April 2020"),
            },
            "before_start": [],
            "materials": [{
                "material_id": "tris", "name_source_text": "Tris buffer",
                "evidence": evidence("Tris buffer"),
            }],
            "equipment": [],
            "sections": [{
                "section_id": "procedure",
                "title_source_text": "Procedure",
                "evidence": evidence("Procedure"),
                "steps": [
                    {
                        "step_id": "step-1", "source_label": "1",
                        "instruction_source_text": "Add 5 mL buffer to the tube.",
                        "evidence": evidence("1. Add 5 mL buffer to the tube."),
                    },
                    {
                        "step_id": "step-2", "source_label": "2",
                        "instruction_source_text": "Incubate for 10 min at 37 °C.",
                        "evidence": evidence("2. Incubate for 10 min at 37 °C."),
                        "notes": [{
                            "statement_id": "gloves", "source_text": "Note: Wear gloves.",
                            "evidence": evidence("Note: Wear gloves."),
                        }],
                    },
                ],
            }],
            "constructs": [],
            "description": {
                "statement_id": "about",
                "source_text": "This protocol measures telomere length by qPCR.",
                "evidence": evidence("This protocol measures telomere length by qPCR."),
            },
        },
    }


def parse(payload: dict):
    return parse_protocol_analysis_response(json.dumps(payload), source())


class AuxiliaryFieldsAreEmptiedTests(unittest.TestCase):
    def test_a_response_whose_fields_all_check_clears_nothing(self):
        draft = parse(response())
        self.assertEqual(draft.protocol.cleared_fields, ())
        self.assertEqual(draft.protocol.metadata.authors, ("Jane Doe", "John Roe"))

    def test_authors_the_page_does_not_print_are_emptied(self):
        payload = response()
        payload["protocol"]["metadata"]["authors"] = ["Jane Doe", "J. Roe"]
        draft = parse(payload)
        metadata = draft.protocol.metadata
        self.assertEqual(metadata.authors, ())
        self.assertIsNone(metadata.authors_evidence)
        self.assertEqual(draft.protocol.cleared_fields, ("metadata.authors",))
        # Everything else stands.
        self.assertEqual(metadata.title, TITLE)
        self.assertEqual(metadata.publication_date, "2020-04-03")
        self.assertEqual(len(draft.protocol.sections[0].steps), 2)

    def test_an_authors_quote_not_on_its_page_empties_the_authors(self):
        payload = response()
        payload["protocol"]["metadata"]["authors_evidence"] = evidence("Jane Doe 1,2, John Roe 1")
        draft = parse(payload)
        self.assertEqual(draft.protocol.metadata.authors, ())
        self.assertEqual(draft.protocol.cleared_fields, ("metadata.authors",))

    def test_a_title_the_page_does_not_print_is_emptied_and_its_quote_kept(self):
        payload = response()
        payload["protocol"]["metadata"]["title"] = "Measuring Relative Telomere Length by qPCR"
        draft = parse(payload)
        self.assertEqual(draft.protocol.metadata.title, "")
        self.assertEqual(draft.protocol.metadata.evidence.source_excerpt, TITLE)
        self.assertEqual(draft.protocol.cleared_fields, ("metadata.title",))

    def test_a_source_uri_that_is_the_title_reworded_is_emptied(self):
        payload = response()
        payload["protocol"]["metadata"]["source_uri"] = "An Optimised Protocol for Measuring Relative Telomere Length"
        draft = parse(payload)
        self.assertIsNone(draft.protocol.metadata.source_uri)
        self.assertEqual(draft.protocol.cleared_fields, ("metadata.source_uri",))

    def test_several_fields_are_emptied_in_one_analysis(self):
        payload = response()
        payload["protocol"]["metadata"]["authors"] = ["J. Doe"]
        payload["protocol"]["metadata"]["publication_date"] = "2020-04-30"
        payload["protocol"]["metadata"]["doi"] = "10.1000/xyz"
        draft = parse(payload)
        self.assertEqual(
            set(draft.protocol.cleared_fields),
            {"metadata.authors", "metadata.publication_date", "metadata.doi"},
        )
        self.assertIsNone(draft.protocol.metadata.publication_date)
        self.assertIsNone(draft.protocol.metadata.publication_date_evidence)
        self.assertIsNone(draft.protocol.metadata.doi)

    def test_a_description_the_page_does_not_print_is_emptied(self):
        payload = response()
        payload["protocol"]["description"]["source_text"] = "This protocol measures telomeres."
        draft = parse(payload)
        self.assertIsNone(draft.protocol.description)
        self.assertEqual(draft.protocol.cleared_fields, ("description",))

    def test_a_section_title_the_page_does_not_print_is_emptied(self):
        payload = response()
        payload["protocol"]["sections"][0]["title_source_text"] = "Procedures"
        draft = parse(payload)
        section = draft.protocol.sections[0]
        self.assertEqual(section.title_source_text, "")
        self.assertEqual(section.evidence.source_excerpt, "Procedure")
        self.assertEqual(draft.protocol.cleared_fields, ("sections.procedure.title_source_text",))
        self.assertEqual(len(section.steps), 2)


class ExecutionFieldsStillRefuseTests(unittest.TestCase):
    def assertRefused(self, payload: dict) -> None:
        with self.assertRaises(ProtocolAnalysisEvidenceError):
            parse(payload)

    def test_a_step_instruction_not_on_its_page_refuses(self):
        payload = response()
        payload["protocol"]["sections"][0]["steps"][0]["instruction_source_text"] = "Add 50 mL buffer to the tube."
        self.assertRefused(payload)

    def test_a_step_quote_not_on_its_page_refuses(self):
        payload = response()
        payload["protocol"]["sections"][0]["steps"][1]["evidence"] = evidence("2. Incubate for 15 min at 37 °C.")
        self.assertRefused(payload)

    def test_a_step_label_not_at_its_quote_refuses(self):
        payload = response()
        payload["protocol"]["sections"][0]["steps"][1]["source_label"] = "3"
        self.assertRefused(payload)

    def test_a_note_not_on_its_page_refuses(self):
        payload = response()
        payload["protocol"]["sections"][0]["steps"][1]["notes"][0]["source_text"] = "Note: Wear goggles."
        self.assertRefused(payload)

    def test_a_material_not_on_its_page_refuses(self):
        payload = response()
        payload["protocol"]["materials"][0]["name_source_text"] = "Tris-HCl buffer"
        self.assertRefused(payload)

    def test_an_auxiliary_failure_beside_an_execution_failure_still_refuses(self):
        payload = response()
        payload["protocol"]["metadata"]["authors"] = ["J. Doe"]
        payload["protocol"]["sections"][0]["steps"][0]["instruction_source_text"] = "Add 50 mL buffer to the tube."
        self.assertRefused(payload)

    def test_the_shared_metadata_quote_is_not_emptied(self):
        # metadata.evidence is the protocol's own required quote.
        payload = response()
        payload["protocol"]["metadata"]["evidence"] = evidence("Measuring Telomere Length")
        self.assertRefused(payload)

    def test_a_section_quote_not_on_its_page_refuses(self):
        payload = response()
        payload["protocol"]["sections"][0]["evidence"] = evidence("Procedures")
        self.assertRefused(payload)


class TheServerAloneEmptiesTests(unittest.TestCase):
    def test_the_provider_schema_does_not_ask_for_cleared_fields(self):
        self.assertNotIn(
            "cleared_fields", ANALYSIS_RESPONSE_SCHEMA["$defs"]["ExperimentProtocol"]["properties"],
        )

    def test_a_provider_cannot_say_a_field_was_emptied(self):
        payload = response()
        payload["protocol"]["cleared_fields"] = ["metadata.title"]
        with self.assertRaises(ProtocolAnalysisResponseError):
            parse(payload)

    def test_an_empty_title_the_server_did_not_empty_is_refused(self):
        payload = response()
        payload["protocol"]["metadata"]["title"] = ""
        with self.assertRaises(ProtocolAnalysisResponseError):
            parse(payload)

    def test_the_domain_reads_the_record(self):
        protocol = parse(response()).protocol
        empty_title = replace(protocol, metadata=replace(protocol.metadata, title=""))
        with self.assertRaises(domain.ProtocolValidationError):
            domain.validate_protocol(empty_title)
        domain.validate_protocol(replace(empty_title, cleared_fields=("metadata.title",)))
        # A field recorded as emptied must be empty.
        with self.assertRaises(domain.ProtocolValidationError):
            domain.validate_protocol(replace(protocol, cleared_fields=("metadata.authors",)))
        # Only the fields execution never reads can be recorded.
        for field in ("sections.procedure.steps", "materials", "metadata.evidence", "sections.nowhere.title_source_text"):
            with self.subTest(field=field), self.assertRaises(domain.ProtocolValidationError):
                domain.validate_protocol(replace(protocol, cleared_fields=(field,)))

    def test_the_record_survives_storage_and_old_records_read_as_none_cleared(self):
        payload = response()
        payload["protocol"]["metadata"]["authors"] = ["J. Doe"]
        draft = parse(payload)
        stored, _ = serialize_analysis(draft.protocol, draft.readiness, draft.capability_policy_id)
        self.assertEqual(deserialize_analysis(stored)[0].cleared_fields, ("metadata.authors",))
        old = json.loads(stored)
        del old["protocol"]["fields"]["cleared_fields"]
        self.assertEqual(deserialize_analysis(json.dumps(old))[0].cleared_fields, ())

    def test_korean_names_for_the_line_before_the_start(self):
        self.assertEqual(
            domain.cleared_fields_line_ko(("metadata.authors", "metadata.title")),
            "원문에서 확인하지 못해 비운 칸: 저자·제목",
        )
        self.assertIsNone(domain.cleared_fields_line_ko(()))


TEXT = "Protocol Alpha Jane Doe Section preparation 1. Add solution. Wear gloves."


class _Model:
    def __init__(self, reply: str) -> None:
        self.reply = reply

    def analyze(self, *, system_prompt: str, input_json: str, response_schema: dict) -> str:
        return self.reply


class CatalogRecordsAndShowsTheEmptiedFieldsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        clear_protocol_pdf_cache()
        self.store = initialize_protocol_store(ProtocolPersistenceSettings(True, self.root / "catalog"))
        self.catalog = ProtocolCatalog(self.store)
        self.pdf = self.root / "alpha.pdf"
        write_text_pdf(self.pdf, TEXT, title="Alpha file title")
        self.protocol_id = self.catalog.register(
            self.pdf, source_filename="alpha.pdf", media_type="application/pdf",
        ).entry.protocol_id

    def tearDown(self) -> None:
        self.store.close()
        clear_protocol_pdf_cache()
        self.temp.cleanup()

    def analyze(self, *, title: str, authors: list[str]) -> None:
        extraction = extract_protocol_pdf(self.pdf)
        reply = {
            "analysis_schema_version": 1, "pdf_sha256": extraction.sha256,
            "capability_policy_id": "p1-conservative",
            "protocol": {
                "protocol_id": self.protocol_id,
                "metadata": {
                    "title": title, "original_language": "en",
                    "evidence": evidence("Protocol Alpha"), "authors": authors,
                },
                "before_start": [], "materials": [], "equipment": [],
                "sections": [{
                    "section_id": "preparation", "title_source_text": "Section preparation",
                    "evidence": evidence("Section preparation"),
                    "steps": [{
                        "step_id": "step-1", "source_label": "1",
                        "instruction_source_text": "Add solution.",
                        "evidence": evidence("1. Add solution."),
                        "warnings": [{"statement_id": "gloves", "source_text": "Wear gloves.",
                                      "evidence": evidence("Wear gloves.")}],
                    }],
                }],
                "constructs": [], "description": None,
            },
        }
        analysis_id = "analysis-" + "e" * 32
        self.catalog.request_analysis(self.protocol_id, analysis_id)
        self.catalog.analyze(self.protocol_id, _Model(json.dumps(reply)), analysis_id=analysis_id)

    def test_the_review_says_which_fields_were_emptied_in_one_line(self):
        self.analyze(title="Protocol Alpha for Cells", authors=["J. Doe"])
        review = self.catalog.review(self.protocol_id)
        self.assertTrue(review["available_for_execution"])
        self.assertEqual(
            [item["field"] for item in review["cleared_fields"]],
            ["metadata.title", "metadata.authors"],
        )
        self.assertEqual(review["cleared_fields_ko"], "원문에서 확인하지 못해 비운 칸: 제목·저자")
        # An emptied title is not shown as an empty name: the file's own title stands in.
        self.assertEqual(review["title"], "Alpha file title")

    def test_the_analysis_ready_event_records_them(self):
        self.analyze(title="Protocol Alpha", authors=["J. Doe"])
        ready = next(
            event for event in self.store.list_events(self.protocol_id)
            if event.event_type == "protocol_analysis_ready"
        )
        self.assertEqual(ready.payload["cleared_fields"], ["metadata.authors"])

    def test_nothing_emptied_shows_no_line(self):
        self.analyze(title="Protocol Alpha", authors=["Jane Doe"])
        review = self.catalog.review(self.protocol_id)
        self.assertEqual(review["cleared_fields"], [])
        self.assertIsNone(review["cleared_fields_ko"])
        self.assertEqual(review["title"], "Protocol Alpha")


class StartScreenShowsTheLineTests(unittest.TestCase):
    def test_the_start_summary_renders_the_server_line_as_text(self):
        html = (ROOT / "src" / "voiney_lab" / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="protocol-start-cleared"', html)
        start = html.index("function renderStartSummary(")
        body = html[start: html.index("\n}\n", start)]
        self.assertIn("cleared_fields_ko", body)
        self.assertIn('$("protocol-start-cleared")', body)
        self.assertNotIn("innerHTML", body)


if __name__ == "__main__":
    unittest.main()
