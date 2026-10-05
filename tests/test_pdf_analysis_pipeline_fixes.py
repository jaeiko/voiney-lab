"""PDF analysis pipeline fixes (lane P2, human decisions 2026-10-05).

Each group reproduces one failure measured on real PDFs in lane P1 and pins
the decided behaviour:

1. The analysis provider call runs in the background with a configurable
   time limit (default 600 s); the measured calls took 236-394 s against a
   120 s limit. Running and failed states use the existing status values.
2. The response schema requires ExperimentProtocol's list fields and
   ProtocolSection.steps, so a provider cannot drop every step by omission.
3. A step label is supported when the source text right before the cited
   excerpt is that step's number at the start of a line, even when the
   excerpt itself leaves the number out. A missing or different number is
   still refused, and the prompt asks for step quotes that start with it.
4. Metadata fields and values (quantities, durations) may carry their own
   evidence on the page where they are printed. Numbers are compared as
   strictly as before.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
import openai

from voiney_lab import experiment_protocol as domain
from voiney_lab import server
from voiney_lab.experiment_protocol_analysis import (
    ANALYSIS_RESPONSE_SCHEMA,
    ANALYSIS_SYSTEM_PROMPT,
    OpenAICompatibleProtocolAnalysisModel,
    ProtocolAnalysisEvidenceError,
    parse_protocol_analysis_response,
)
from voiney_lab.experiment_protocol_config import ProtocolPersistenceSettings
from voiney_lab.experiment_protocol_pdf import (
    ProtocolPdfExtraction,
    ProtocolPdfMetadata,
    ProtocolPdfPage,
)
from voiney_lab.experiment_protocol_store import (
    deserialize_analysis,
    initialize_protocol_store,
    serialize_analysis,
)
from voiney_lab.protocol_catalog import ProtocolCatalog
from voiney_lab.setting_names import BY_NAME
from tests.test_protocol_catalog import write_text_pdf

# Page 1 is shaped like a protocols.io page (bare line-head numbers), page 2
# like an IBRIC page ("1." numbers) followed by notes, as measured in lane P1.
PAGE_ONE = "\n".join(
    (
        "Dynamic Headspace Collections",
        "Dec 05, 2024",
        "3 Wash the band with 500 µL of solution A.",
        "4 Remove and discard solution A from the tube.",
        "Wash twice with 3 mL buffer and keep it cold.",
        "13 Incubate the plate at 37 °C.",
        "6.1 Dry the gel pieces.",
        "5 Centrifuge for 5 min.",
        "7 Centrifuge for 5 min.",
        "8.Glued to its number.",
    )
)
PAGE_TWO = "\n".join(
    (
        "Created: Dec 05, 2024",
        "실험 당일 ",
        "1. Seahorse XF Base 배지를 보충하여 assay 배지를 준비합니다 [노트 2]. ",
        "2. Assay 배지를 37°C 에 보관합니다. ",
        "노트 ",
        "2. 2mM glutamine 을 보충합니다. Incubate for 45 min.",
    )
)


def extraction() -> ProtocolPdfExtraction:
    return ProtocolPdfExtraction(
        original_filename="synthetic.pdf",
        byte_size=1,
        sha256="c" * 64,
        media_type="application/pdf",
        page_count=2,
        encrypted=False,
        metadata=ProtocolPdfMetadata(None, None, None, None, None, None, None),
        pages=(
            ProtocolPdfPage(1, PAGE_ONE, False),
            ProtocolPdfPage(2, PAGE_TWO, False),
        ),
    )


def evidence(page: int, excerpt: str) -> dict:
    return {"source_page_number": page, "source_excerpt": excerpt}


def step(label: str, page: int, excerpt: str, **extra) -> dict:
    return {
        "step_id": f"step-{label.replace('.', '-')}",
        "source_label": label,
        "instruction_source_text": excerpt,
        "evidence": evidence(page, excerpt),
        **extra,
    }


def response(*steps: dict, metadata: dict | None = None, **protocol) -> dict:
    return {
        "analysis_schema_version": 1,
        "pdf_sha256": "c" * 64,
        "capability_policy_id": "p1-conservative",
        "protocol": {
            "protocol_id": "protocol-p2",
            "metadata": metadata
            or {
                "title": "Dynamic Headspace Collections",
                "original_language": "en",
                "evidence": evidence(1, "Dynamic Headspace Collections"),
            },
            "before_start": [],
            "materials": [],
            "equipment": [],
            "sections": [
                {
                    "section_id": "procedure",
                    "title_source_text": "Dynamic Headspace Collections",
                    "evidence": evidence(1, "Dynamic Headspace Collections"),
                    "steps": list(steps),
                }
            ],
            "constructs": [],
            "description": None,
            **protocol,
        },
    }


def parse(payload: dict):
    return parse_protocol_analysis_response(json.dumps(payload), extraction())


# --- 1. background analysis and its time limit ------------------------------


class AnalysisTimeLimitTests(unittest.TestCase):
    ENV = {
        "XAI_API_KEY": "fake-key",
        "VOINEY_LAB_ANALYSIS_MODEL": "grok-4.6",
    }

    def client_timeout(self, extra: dict[str, str]) -> float:
        with patch.dict(os.environ, {**self.ENV, **extra}, clear=True), patch.object(
            server, "OpenAI"
        ) as client:
            server._protocol_analysis_model()
        client.assert_called_once()
        return client.call_args.kwargs["timeout"]

    def test_the_default_limit_outlasts_the_measured_calls(self):
        # Lane P1 measured 236-394 s per call; the limit was 120 s.
        self.assertEqual(self.client_timeout({}), 600.0)

    def test_the_limit_is_a_setting(self):
        self.assertEqual(
            self.client_timeout(
                {"VOINEY_LAB_PROTOCOL_ANALYSIS_TIMEOUT_SECONDS": "900"}
            ),
            900.0,
        )

    def test_an_invalid_limit_refuses_to_build_the_client(self):
        for value in ("0", "-5", "abc", "inf", "nan", "100000"):
            with self.subTest(value=value), patch.dict(
                os.environ,
                {**self.ENV, "VOINEY_LAB_PROTOCOL_ANALYSIS_TIMEOUT_SECONDS": value},
                clear=True,
            ), patch.object(server, "OpenAI") as client:
                with self.assertRaises(server.ServerConfigurationError):
                    server._protocol_analysis_model()
                client.assert_not_called()

    def test_the_setting_is_in_the_setting_table_with_its_default(self):
        row = BY_NAME["VOINEY_LAB_PROTOCOL_ANALYSIS_TIMEOUT_SECONDS"]
        self.assertEqual(row.default, "600")

    def test_the_browser_request_runs_in_the_background_by_default(self):
        parameter = inspect.signature(server.trigger_protocol_analysis).parameters[
            "background"
        ]
        self.assertIs(parameter.default, True)

    def test_a_provider_time_out_in_the_background_reads_as_analysis_failed(self):
        root = Path(tempfile.mkdtemp())
        pdf = root / "alpha.pdf"
        write_text_pdf(
            pdf,
            "Protocol Alpha\nSection preparation\n1. Add solution.\nWear gloves.",
            title="Protocol Alpha",
        )
        settings = ProtocolPersistenceSettings(True, root / "catalog")
        store = initialize_protocol_store(settings)
        try:
            entry = ProtocolCatalog(store).register(
                pdf, source_filename="alpha.pdf", media_type="application/pdf"
            ).entry
        finally:
            store.close()

        def open_catalog():
            opened = initialize_protocol_store(settings)
            return ProtocolCatalog(opened), opened

        client = Mock()
        client.chat.completions.create.side_effect = openai.APITimeoutError(
            request=httpx.Request("POST", "https://api.x.ai/v1/chat/completions")
        )
        model = OpenAICompatibleProtocolAnalysisModel(client, "grok-4.6", "high")

        async def scenario():
            accepted = await server.trigger_protocol_analysis(entry.protocol_id)
            # While the provider works the request is visible as pending.
            self.assertEqual(accepted["analysis_run"]["state"], "analysis_pending")
            await server._PROTOCOL_ANALYSIS_TASKS[entry.protocol_id]
            await asyncio.sleep(0)

        async def to_thread(function, /, *args, **kwargs):
            return function(*args, **kwargs)

        with patch.object(
            server, "_open_protocol_catalog", side_effect=open_catalog
        ), patch.object(server.asyncio, "to_thread", side_effect=to_thread), patch.object(
            server, "_protocol_analysis_model", return_value=model
        ):
            asyncio.run(scenario())
            status = server.get_protocol_analysis_status(entry.protocol_id)

        # Existing status values; no new one was needed for the screen.
        self.assertEqual(status["state"], "analysis_failed")
        self.assertEqual(status["lifecycle_state"], "blocked")
        self.assertEqual(status["failure_code"], "protocol_analysis_model_failed")


# --- 2. required list fields -------------------------------------------------


class RequiredListFieldsTests(unittest.TestCase):
    def test_experiment_protocol_list_fields_are_required(self):
        required = set(ANALYSIS_RESPONSE_SCHEMA["$defs"]["ExperimentProtocol"]["required"])
        self.assertLessEqual(
            {
                "before_start",
                "materials",
                "equipment",
                "sections",
                "constructs",
                "description",
            },
            required,
        )

    def test_section_steps_are_required(self):
        self.assertIn(
            "steps", ANALYSIS_RESPONSE_SCHEMA["$defs"]["ProtocolSection"]["required"]
        )

    def test_shared_metadata_evidence_is_required_beside_field_evidence(self):
        # A real response cited only title_evidence and left this out.
        self.assertIn(
            "evidence", ANALYSIS_RESPONSE_SCHEMA["$defs"]["ProtocolMetadata"]["required"]
        )
        prompt = " ".join(ANALYSIS_SYSTEM_PROMPT.split())
        self.assertIn("metadata.evidence is always required", prompt)

    def test_description_stays_nullable(self):
        description = ANALYSIS_RESPONSE_SCHEMA["$defs"]["ExperimentProtocol"][
            "properties"
        ]["description"]
        self.assertIn({"type": "null"}, description["anyOf"])

    def test_other_optional_fields_are_unchanged(self):
        # Only the decided fields moved; e.g. a step's notes stay optional.
        self.assertNotIn(
            "notes", ANALYSIS_RESPONSE_SCHEMA["$defs"]["ProtocolSourceStep"]["required"]
        )
        self.assertNotIn(
            "authors", ANALYSIS_RESPONSE_SCHEMA["$defs"]["ProtocolMetadata"]["required"]
        )


# --- 3. step label right before the excerpt -----------------------------------


class StepLabelBeforeExcerptTests(unittest.TestCase):
    def assert_label_refused(self, payload: dict) -> None:
        with self.assertRaises(ProtocolAnalysisEvidenceError) as context:
            parse(payload)
        self.assertEqual(context.exception.diagnostic.reason_code, "source_label_not_found")

    def test_a_bare_line_head_number_before_the_excerpt_supports_the_label(self):
        draft = parse(response(step("3", 1, "Wash the band with 500 µL of solution A.")))
        self.assertEqual(draft.protocol.sections[0].steps[0].source_label, "3")

    def test_a_dotted_line_head_number_before_the_excerpt_supports_the_label(self):
        draft = parse(
            response(step("2", 2, "Assay 배지를 37°C 에 보관합니다."))
        )
        self.assertEqual(draft.protocol.sections[0].steps[0].source_label, "2")

    def test_an_excerpt_that_starts_with_the_number_is_still_accepted(self):
        parse(response(step("4", 1, "4 Remove and discard solution A from the tube.")))

    def test_a_different_number_before_the_excerpt_is_refused(self):
        self.assert_label_refused(
            response(step("4", 1, "Wash the band with 500 µL of solution A."))
        )

    def test_no_number_before_the_excerpt_is_refused(self):
        self.assert_label_refused(
            response(step("1", 1, "Wash twice with 3 mL buffer and keep it cold."))
        )

    def test_a_number_inside_a_sentence_is_not_a_step_number(self):
        self.assert_label_refused(
            response(step("3", 1, "mL buffer and keep it cold."))
        )

    def test_the_number_must_be_whole(self):
        # "13 Incubate…": label 3 is the tail of 13, not the step number.
        self.assert_label_refused(response(step("3", 1, "Incubate the plate at 37 °C.")))
        # "6.1 Dry…": label 1 is the tail of 6.1.
        self.assert_label_refused(response(step("1", 1, "Dry the gel pieces.")))

    def test_the_number_must_be_separated_from_the_excerpt(self):
        # "8.Glued…": no space between the number and the text, the way "1.5 mL"
        # has none -- "5 mL" after "1." is not step 1.
        self.assert_label_refused(response(step("8", 1, "Glued to its number.")))

    def test_an_excerpt_found_after_two_different_numbers_is_refused(self):
        # "Centrifuge for 5 min." is printed after 5 and after 7 on page 1.
        self.assert_label_refused(response(step("5", 1, "Centrifuge for 5 min.")))

    def test_the_prompt_asks_step_quotes_to_start_with_the_source_number(self):
        prompt = " ".join(ANALYSIS_SYSTEM_PROMPT.split())
        self.assertIn("start with the step's own source number", prompt)


# --- 4. evidence per metadata field and per value ----------------------------


class EvidencePerFieldTests(unittest.TestCase):
    def metadata(self, **extra) -> dict:
        return {
            "title": "Dynamic Headspace Collections",
            "original_language": "en",
            "created_date": "Dec 05, 2024",
            "evidence": evidence(1, "Dynamic Headspace Collections"),
            **extra,
        }

    def test_a_metadata_field_printed_on_another_page_can_cite_that_page(self):
        # Title on page 1, "Created:" on page 2: one evidence could not hold both.
        payload = response(
            step("3", 1, "Wash the band with 500 µL of solution A."),
            metadata=self.metadata(
                created_date="Created: Dec 05, 2024",
                created_date_evidence=evidence(2, "Created: Dec 05, 2024"),
            ),
        )
        draft = parse(payload)
        self.assertEqual(
            draft.protocol.metadata.created_date_evidence.source_page_number, 2
        )

    def test_without_its_own_evidence_the_field_is_checked_on_the_shared_page(self):
        payload = response(
            step("3", 1, "Wash the band with 500 µL of solution A."),
            metadata=self.metadata(created_date="Created: Dec 05, 2024"),
        )
        with self.assertRaises(ProtocolAnalysisEvidenceError) as context:
            parse(payload)
        self.assertEqual(context.exception.diagnostic.reason_code, "claim_not_found")
        self.assertEqual(
            context.exception.diagnostic.field_path, "protocol.metadata.created_date"
        )

    def test_a_field_evidence_quote_must_be_on_its_page(self):
        payload = response(
            step("3", 1, "Wash the band with 500 µL of solution A."),
            metadata=self.metadata(
                created_date="Created: Dec 05, 2024",
                created_date_evidence=evidence(1, "Created: Dec 05, 2024"),
            ),
        )
        with self.assertRaises(ProtocolAnalysisEvidenceError) as context:
            parse(payload)
        self.assertEqual(context.exception.diagnostic.reason_code, "quote_not_found")

    def note_value_step(self, value: dict) -> dict:
        return step(
            "1",
            2,
            "Seahorse XF Base 배지를 보충하여 assay 배지를 준비합니다 [노트 2].",
            sub_actions=[
                {
                    "action_id": "prepare-medium",
                    "instruction_source_text": (
                        "Seahorse XF Base 배지를 보충하여 assay 배지를 준비합니다"
                    ),
                    "evidence": evidence(
                        2, "Seahorse XF Base 배지를 보충하여 assay 배지를 준비합니다"
                    ),
                    "quantities": [value],
                }
            ],
        )

    def test_a_value_printed_in_a_note_on_another_page_can_cite_that_page(self):
        # The step is on page 1 here; its value is in the note on page 2.
        payload = response(
            step(
                "3",
                1,
                "Wash the band with 500 µL of solution A.",
                sub_actions=[
                    {
                        "action_id": "wash",
                        "instruction_source_text": "Wash the band",
                        "evidence": evidence(1, "Wash the band"),
                        "quantities": [
                            {"source_text": "500 µL"},
                            {
                                "source_text": "2mM glutamine",
                                "evidence": evidence(2, "2mM glutamine"),
                            },
                        ],
                    }
                ],
            )
        )
        draft = parse(payload)
        value = draft.protocol.sections[0].steps[0].sub_actions[0].quantities[1]
        self.assertEqual(value.evidence.source_page_number, 2)

    def test_without_its_own_evidence_a_value_is_checked_on_its_owners_page(self):
        payload = response(
            step(
                "3",
                1,
                "3 Wash the band with 500 µL of solution A.",
                sub_actions=[
                    {
                        "action_id": "wash",
                        "instruction_source_text": "Wash the band",
                        "evidence": evidence(1, "Wash the band"),
                        "quantities": [{"source_text": "2mM glutamine"}],
                    }
                ],
            )
        )
        with self.assertRaises(ProtocolAnalysisEvidenceError) as context:
            parse(payload)
        self.assertEqual(context.exception.diagnostic.reason_code, "claim_not_found")
        self.assertEqual(context.exception.diagnostic.matching_source_pages, (2,))

    def test_a_value_with_its_own_evidence_still_needs_the_exact_number(self):
        payload = response(
            step(
                "3",
                1,
                "3 Wash the band with 500 µL of solution A.",
                sub_actions=[
                    {
                        "action_id": "wash",
                        "instruction_source_text": "Wash the band",
                        "evidence": evidence(1, "Wash the band"),
                        "quantities": [
                            {
                                "source_text": "3mM glutamine",
                                "evidence": evidence(2, "2mM glutamine"),
                            }
                        ],
                    }
                ],
            )
        )
        with self.assertRaises(ProtocolAnalysisEvidenceError) as context:
            parse(payload)
        self.assertEqual(context.exception.diagnostic.reason_code, "claim_not_found")

    def test_a_duration_can_cite_its_own_page(self):
        payload = response(
            step(
                "3",
                1,
                "Wash the band with 500 µL of solution A.",
                sub_actions=[
                    {
                        "action_id": "wash",
                        "instruction_source_text": "Wash the band",
                        "evidence": evidence(1, "Wash the band"),
                        "estimated_duration": {
                            "source_text": "45 min",
                            "evidence": evidence(2, "Incubate for 45 min."),
                        },
                    }
                ],
            )
        )
        draft = parse(payload)
        duration = draft.protocol.sections[0].steps[0].sub_actions[0].estimated_duration
        self.assertEqual(duration.evidence.source_page_number, 2)

    def test_the_schema_offers_the_new_evidence_fields_as_optional(self):
        definitions = ANALYSIS_RESPONSE_SCHEMA["$defs"]
        for record, field in (
            ("ScientificValue", "evidence"),
            ("EstimatedDuration", "evidence"),
            ("ProtocolMetadata", "title_evidence"),
            ("ProtocolMetadata", "created_date_evidence"),
            ("ProtocolMetadata", "source_status_evidence"),
        ):
            with self.subTest(record=record, field=field):
                self.assertIn(field, definitions[record]["properties"])
                self.assertNotIn(field, definitions[record]["required"])

    def test_the_new_evidence_survives_the_store_round_trip(self):
        payload = response(
            step(
                "3",
                1,
                "Wash the band with 500 µL of solution A.",
                sub_actions=[
                    {
                        "action_id": "wash",
                        "instruction_source_text": "Wash the band",
                        "evidence": evidence(1, "Wash the band"),
                        "quantities": [
                            {
                                "source_text": "2mM glutamine",
                                "evidence": evidence(2, "2mM glutamine"),
                            }
                        ],
                    }
                ],
            ),
            metadata=self.metadata(
                created_date="Created: Dec 05, 2024",
                created_date_evidence=evidence(2, "Created: Dec 05, 2024"),
            ),
        )
        draft = parse(payload)
        restored = deserialize_analysis(
            serialize_analysis(
                draft.protocol, draft.readiness, draft.capability_policy_id
            )[0]
        )[0]
        self.assertEqual(restored, draft.protocol)

    def test_the_prompt_asks_for_evidence_where_the_value_is_printed(self):
        prompt = " ".join(ANALYSIS_SYSTEM_PROMPT.split())
        self.assertIn("_evidence", prompt)
        self.assertIn("printed on a different page", prompt)


if __name__ == "__main__":
    unittest.main()
