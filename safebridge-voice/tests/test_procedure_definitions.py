import json
import sqlite3
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from safebridge_voice.document_store import SCHEMA
from safebridge_voice.procedure_definitions import (
    ProcedureDefinitionError,
    load_procedure_definitions,
)


def definition():
    return {
        "schema_version": 1, "procedure_id": "fictional-demo-check",
        "title": "FICTIONAL NON-OPERATIONAL Demo Check", "version": "1",
        "facility_id": "TEST-FACILITY", "language": "en",
        "approval_status": "approved", "usage_scope": "test_only", "active": True,
        "approved_document": {
            "document_id": "fictional-doc", "version": "1", "language": "en",
            "section_reference": "DEMO", "page_start": 1, "page_end": 2,
        },
        "steps": [
            {"step_id": "demo-1", "order": 1, "title": "First fictional check",
             "approved_spoken_instruction": "Confirm the fictional blue marker is shown.",
             "completion_mode": "explicit_confirmation",
             "source_reference": {"section_reference": "DEMO", "page_start": 1, "page_end": 1}},
            {"step_id": "demo-2", "order": 2, "title": "Second fictional check",
             "approved_spoken_instruction": "Confirm the fictional green marker is shown.",
             "completion_mode": "explicit_confirmation",
             "source_reference": {"section_reference": "DEMO", "page_start": 2, "page_end": 2}},
        ],
    }


class ProcedureDefinitionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.catalog = self.root / "catalog.sqlite"
        with sqlite3.connect(self.catalog) as db:
            db.executescript(SCHEMA)
            db.execute("INSERT INTO catalog_metadata VALUES (2)")
            cur = db.execute(
                """INSERT INTO documents
                (document_id,document_family_id,canonical_source_id,canonical_version,
                 document_type,title,issuer,cas_numbers,version,language,facility_id,
                 source_authority,approval_status,usage_scope,source_checksum,
                 translation_status,active)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("fictional-doc","fictional-family","fictional-source","1",
                 "facility_sop","Fictional demo","Test","[]","1","en","TEST-FACILITY",
                 "test_fixture","approved","test_only","abc","original",1))
            db.execute(
                "INSERT INTO sections (document_row_id,section_code,section_title,page_start,page_end,content,keywords) VALUES (?,?,?,?,?,?,?)",
                (cur.lastrowid,"DEMO","Demo",1,2,
                 "Confirm the fictional blue marker is shown.\n"
                 "Confirm the fictional green marker is shown.","[]"))
        self.path = self.root / "procedures.json"

    def tearDown(self): self.tmp.cleanup()

    def load(self, values, **policy):
        self.path.write_text(json.dumps({"procedures": values}), encoding="utf-8")
        trusted={"facility_id":"TEST-FACILITY","language":"en","usage_scope":"test_only"}
        trusted.update(policy)
        return load_procedure_definitions(
            self.path, self.catalog, **trusted)

    def test_valid_definition_loads_and_instruction_is_immutable_source_text(self):
        value=definition()
        value["steps"][0]["observation_schema"]={
            "type":"text",
            "required":True,
            "label":"Fictional marker",
            "utterance_subjects":["fictional marker"],
        }
        loaded = self.load([value])
        self.assertEqual(loaded["fictional-demo-check"].steps[0].instruction,
                         definition()["steps"][0]["approved_spoken_instruction"])
        self.assertEqual(
            loaded["fictional-demo-check"].steps[0]
            .observation_schema["utterance_subjects"],
            ["fictional marker"],
        )

    def test_duplicate_procedure_and_step_ids_are_rejected(self):
        with self.assertRaises(ProcedureDefinitionError): self.load([definition(), definition()])
        bad = definition(); bad["steps"][1]["step_id"] = "demo-1"
        with self.assertRaises(ProcedureDefinitionError): self.load([bad])

    def test_missing_repeated_and_non_contiguous_orders_are_rejected(self):
        for orders in ([0, 1], [1, 1], [1, 3]):
            bad = definition()
            for step, order in zip(bad["steps"], orders): step["order"] = order
            with self.assertRaises(ProcedureDefinitionError): self.load([bad])
        bad = definition(); del bad["steps"][0]["order"]
        with self.assertRaises(ProcedureDefinitionError): self.load([bad])

    def test_unapproved_or_inactive_states_are_rejected(self):
        for field, value in (("approval_status","draft"),("approval_status","rejected"),
                             ("approval_status","superseded"),("active",False)):
            bad = definition(); bad[field] = value
            with self.assertRaises(ProcedureDefinitionError): self.load([bad])

    def test_document_and_policy_mismatches_are_rejected(self):
        mutations = [
            ("approved_document", "document_id", "other"),
            ("approved_document", "page_end", 3),
            (None, "facility_id", "OTHER"),
            (None, "language", "ko"),
            (None, "usage_scope", "operational"),
        ]
        for parent, key, value in mutations:
            bad = definition(); (bad[parent] if parent else bad)[key] = value
            with self.assertRaises(ProcedureDefinitionError): self.load([bad])
        with self.assertRaises(ProcedureDefinitionError):
            self.load([definition()], usage_scope="operational")

    def test_malformed_metadata_and_arbitrary_instruction_are_rejected(self):
        bad = definition(); bad["steps"][0]["timer"] = {"duration_seconds": 0}
        with self.assertRaises(ProcedureDefinitionError): self.load([bad])
        bad = definition(); bad["steps"][0]["observation_schema"] = {"type": "anything"}
        with self.assertRaises(ProcedureDefinitionError): self.load([bad])
        for subjects in (
            [],
            ["same","same"],
            [""],
            ["x\nsubject"],
            "not-a-list",
        ):
            bad=definition()
            bad["steps"][0]["observation_schema"]={
                "type":"text","required":True,
                "utterance_subjects":subjects,
            }
            with self.assertRaises(ProcedureDefinitionError): self.load([bad])
        bad = definition(); bad["steps"][0]["approved_spoken_instruction"] = ""
        with self.assertRaises(ProcedureDefinitionError): self.load([bad])
        for changed in (
            "Arbitrary but non-empty instruction.",
            "Confirm the fictional blue marker is not shown.",
            "Confirm the fictional blue marker is shown for 10 milliliters.",
        ):
            bad = definition(); bad["steps"][0]["approved_spoken_instruction"] = changed
            with self.assertRaises(ProcedureDefinitionError): self.load([bad])

    def test_malformed_and_timezone_incompatible_validity_dates_fail_closed(self):
        for column, value in (
            ("effective_at", "not-a-date"),
            ("review_due_at", "2027-01-01T00:00:00"),
        ):
            with sqlite3.connect(self.catalog) as db:
                db.execute(f"UPDATE documents SET {column}=?", (value,))
            with self.assertRaises(ProcedureDefinitionError):
                self.load([definition()])
            with sqlite3.connect(self.catalog) as db:
                db.execute(f"UPDATE documents SET {column}=NULL")
