"""Focused tests for separate immutable Protocol schema-v1 persistence."""

from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfWriter

from voice_workflow_agent.experiment_protocol import (
    ANALYSIS_REQUIRED_LABEL,
    BranchKind,
    ConditionalBranch,
    ConflictLevel,
    DependencyTarget,
    FixedRangeRepetition,
    MissingExecutionValue,
    ParallelWork,
    ProtocolConflict,
    ProtocolMetadata,
    ProtocolSection,
    ProtocolSourceStep,
    ProtocolSubAction,
    ReadinessStatus,
    ReadinessReasonCode,
    RecurringAction,
    RepeatUntil,
    ReusableSubprocedure,
    ScientificValue,
    SourceAmbiguity,
    SourceEvidence,
    assess_readiness,
)
from voice_workflow_agent.experiment_protocol_config import (
    ProtocolConfigurationError,
    ProtocolFeatureDisabledError,
    ProtocolPersistenceSettings,
)
from voice_workflow_agent.experiment_protocol_pdf import (
    ProtocolPdfPage,
    extract_protocol_pdf,
)
from voice_workflow_agent.experiment_protocol_store import (
    ANALYSIS_SCHEMA_VERSION,
    PROTOCOL_DATABASE_FILENAME,
    PROTOCOL_SCHEMA_VERSION,
    DuplicateProtocolIdentifierError,
    ExperimentProtocolRequiredError,
    ImmutableProtocolRecordError,
    InvalidExperimentIdentifierError,
    ProtocolSerializationError,
    ProtocolStorageInitializationError,
    ProtocolTransactionError,
    UnknownProtocolReferenceError,
    UnsupportedProtocolSchemaError,
    deserialize_analysis,
    initialize_protocol_store,
    serialize_analysis,
)


_FIXTURE_PAGE_WIDTH = 4000  # wide enough that one unwrapped fixture line
# is never clipped: a bounded extractor would otherwise drop the tail and
# disagree with an unbounded one on synthetic input only.


PAGE_TEXT = "\n".join(
    (
        "Section preparation",
        "1. Add 500 µL Solution A.",
        "Add 500 µL Solution A.",
        "No SpeedVac duration is stated.",
        "Repeat until the pH is neutral.",
        "Repeat steps 2–7.",
    )
)


def write_pdf(path: Path, title: str = "Storage fixture") -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = PdfWriter()
    writer.add_blank_page(width=_FIXTURE_PAGE_WIDTH, height=792)
    writer.add_metadata({"/Title": title})
    with path.open("wb") as stream:
        writer.write(stream)
    return path.read_bytes()


def evidence(excerpt: str) -> SourceEvidence:
    return SourceEvidence(1, excerpt)


def structured_protocol(source_pdf: Path):
    extracted = extract_protocol_pdf(source_pdf)
    extracted = replace(
        extracted,
        pages=(ProtocolPdfPage(1, PAGE_TEXT, False),),
    )
    action = ProtocolSubAction(
        "add-solution",
        "Add 500 µL Solution A.",
        evidence("Add 500 µL Solution A."),
        quantities=(ScientificValue("500 µL Solution A"),),
        missing_execution_values=(
            MissingExecutionValue(
                "speedvac-duration",
                "SpeedVac duration is absent.",
                evidence("No SpeedVac duration is stated."),
            ),
        ),
    )
    step = ProtocolSourceStep(
        "step-1",
        "1",
        "1. Add 500 µL Solution A.",
        evidence("1. Add 500 µL Solution A."),
        sub_actions=(action,),
    )
    protocol = __import__(
        "voice_workflow_agent.experiment_protocol",
        fromlist=["ExperimentProtocol"],
    ).ExperimentProtocol(
        "storage-fixture",
        ProtocolMetadata(
            extracted,
            "Storage fixture",
            "en",
            authors=("Fixture Author",),
            license="CC BY",
            source_status="In development",
        ),
        sections=(
            ProtocolSection(
                "preparation",
                "Section preparation",
                evidence("Section preparation"),
                (step,),
            ),
        ),
        constructs=(
            RepeatUntil(
                "neutral-ph",
                "Repeat until the pH is neutral.",
                ("step-1",),
                evidence("Repeat until the pH is neutral."),
                step_id="step-1",
            ),
            SourceAmbiguity(
                "self-reference",
                "Repeat steps 2–7.",
                evidence("Repeat steps 2–7."),
                step_id="step-1",
            ),
        ),
    )
    return protocol, assess_readiness(protocol)


def advanced_protocol(source_pdf: Path):
    protocol, _ = structured_protocol(source_pdf)
    second_step = ProtocolSourceStep(
        "step-2",
        "2",
        "Repeat steps 2–7.",
        evidence("Repeat steps 2–7."),
    )
    section = replace(
        protocol.sections[0],
        steps=(protocol.sections[0].steps[0], second_step),
    )
    constructs = (
        ConditionalBranch(
            "conditional-branch",
            BranchKind.ALTERNATIVE,
            "Repeat steps 2–7.",
            ("step-2",),
            evidence("Repeat steps 2–7."),
            step_id="step-1",
        ),
        FixedRangeRepetition(
            "fixed-range",
            "step-1",
            "step-2",
            "Repeat steps 2–7.",
            evidence("Repeat steps 2–7."),
            repeat_count=2,
        ),
        RepeatUntil(
            "repeat-until",
            "Repeat until the pH is neutral.",
            ("step-1",),
            evidence("Repeat until the pH is neutral."),
            step_id="step-1",
        ),
        ParallelWork(
            "parallel-work",
            ("step-1", "step-2"),
            "Repeat steps 2–7.",
            evidence("Repeat steps 2–7."),
        ),
        RecurringAction(
            "recurring-action",
            DependencyTarget("step-1", "add-solution"),
            ScientificValue("500 µL Solution A"),
            "Add 500 µL Solution A.",
            evidence("Add 500 µL Solution A."),
            step_id="step-1",
            action_id="add-solution",
        ),
        ReusableSubprocedure(
            "reusable-subprocedure",
            ("step-1", "step-2"),
            "Repeat steps 2–7.",
            evidence("Repeat steps 2–7."),
        ),
        SourceAmbiguity(
            "source-ambiguity",
            "Repeat steps 2–7.",
            evidence("Repeat steps 2–7."),
            step_id="step-1",
        ),
        ProtocolConflict(
            "protocol-conflict",
            ConflictLevel.EXECUTION_VALUE,
            "No SpeedVac duration is stated.",
            evidence("No SpeedVac duration is stated."),
            step_id="step-1",
        ),
    )
    protocol = replace(
        protocol,
        sections=(section,),
        constructs=constructs,
    )
    return protocol, assess_readiness(protocol)


class ProtocolStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data_dir = self.root / "protocol-data"
        self.settings = ProtocolPersistenceSettings(True, self.data_dir)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def initialize(self):
        return initialize_protocol_store(self.settings)

    def test_default_disabled_configuration_has_no_side_effects(self):
        target = self.root / "must-not-exist"
        settings = ProtocolPersistenceSettings.from_environment({})

        self.assertFalse(settings.enabled)
        self.assertIsNone(settings.data_dir)
        with self.assertRaises(ProtocolFeatureDisabledError):
            initialize_protocol_store(settings)
        self.assertFalse(target.exists())
        self.assertFalse(self.data_dir.exists())

    def test_configuration_requires_boolean_and_absolute_enabled_path(self):
        with self.assertRaises(ProtocolConfigurationError):
            ProtocolPersistenceSettings.from_environment(
                {"VOICE_WORKFLOW_AGENT_PROTOCOL_ENABLED": "maybe"}
            )
        with self.assertRaises(ProtocolConfigurationError):
            ProtocolPersistenceSettings.from_environment(
                {
                    "VOICE_WORKFLOW_AGENT_PROTOCOL_ENABLED": "true",
                    "VOICE_WORKFLOW_AGENT_PROTOCOL_DATA_DIR": "relative/path",
                }
            )

    def test_enabled_initialization_creates_only_separate_schema_v1_store(self):
        store = self.initialize()
        try:
            self.assertEqual(
                store.database_path,
                self.data_dir / PROTOCOL_DATABASE_FILENAME,
            )
            self.assertTrue(store.database_path.is_file())
            self.assertEqual(store.schema_version(), PROTOCOL_SCHEMA_VERSION)
            self.assertTrue(store.foreign_keys_enabled())
            tables = {
                row[0]
                for row in store._connection.execute(
                    """SELECT name FROM sqlite_master
                    WHERE type='table' AND name NOT LIKE 'sqlite_%'"""
                )
            }
            self.assertEqual(
                tables,
                {
                    "schema_metadata",
                    "pdf_objects",
                    "experiments",
                    "protocol_revisions",
                    "analysis_payloads",
                    "analysis_revisions",
                    "clarifications",
                    "protocol_events",
                },
            )
        finally:
            store.close()
        self.assertEqual(
            tuple(self.root.rglob("*.sqlite")),
            (self.data_dir / PROTOCOL_DATABASE_FILENAME,),
        )

    def test_initialization_failure_is_sanitized(self):
        not_directory = self.root / "not-a-directory"
        not_directory.write_text("occupied", encoding="utf-8")
        with self.assertRaises(ProtocolStorageInitializationError) as context:
            initialize_protocol_store(
                ProtocolPersistenceSettings(True, not_directory)
            )
        self.assertNotIn(str(self.root), str(context.exception))
        self.assertNotIn("Traceback", str(context.exception))

    def test_existing_application_databases_remain_byte_identical(self):
        first = self.root / "application.sqlite"
        second = self.root / "procedure.sqlite"
        first.write_bytes(b"existing-application-database")
        second.write_bytes(b"existing-procedure-database")
        before = {
            path: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (first, second)
        }

        store = self.initialize()
        store.close()

        self.assertEqual(
            {
                path: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in (first, second)
            },
            before,
        )

    def test_experiment_requires_protocol_and_valid_identifier(self):
        store = self.initialize()
        try:
            with self.assertRaises(ExperimentProtocolRequiredError):
                store.create_experiment("experiment-1", None)
            with self.assertRaises(InvalidExperimentIdentifierError):
                store.create_experiment("not stable", self.root / "missing.pdf")
            self.assertIsNone(store.get_experiment("experiment-1"))
        finally:
            store.close()

    def test_initial_and_replacement_revisions_are_immutable_and_monotonic(self):
        first = self.root / "first.pdf"
        second = self.root / "second.pdf"
        write_pdf(first, "First")
        write_pdf(second, "Second")
        store = self.initialize()
        try:
            creation = store.create_experiment("experiment-1", first)
            replacement = store.append_protocol_revision(
                "experiment-1",
                second,
            )
            revisions = store.list_protocol_revisions("experiment-1")
        finally:
            store.close()

        self.assertEqual(creation.protocol_revision.revision_number, 1)
        self.assertEqual(replacement.revision_number, 2)
        self.assertEqual(
            tuple(revision.revision_number for revision in revisions),
            (1, 2),
        )
        self.assertNotEqual(
            revisions[0].pdf_checksum,
            revisions[1].pdf_checksum,
        )

        third = self.root / "third.pdf"
        write_pdf(third, "Third")
        reopened = self.initialize()
        try:
            third_revision = reopened.append_protocol_revision(
                "experiment-1",
                third,
            )
            self.assertEqual(third_revision.revision_number, 3)
            self.assertEqual(
                len(reopened.list_protocol_revisions("experiment-1")),
                3,
            )
        finally:
            reopened.close()

    def test_duplicate_experiment_id_is_idempotent_or_conflicting(self):
        first = self.root / "first.pdf"
        second = self.root / "second.pdf"
        write_pdf(first, "First")
        write_pdf(second, "Second")
        store = self.initialize()
        try:
            initial = store.create_experiment("experiment-1", first)
            replay = store.create_experiment("experiment-1", first)
            self.assertEqual(initial, replay)
            with self.assertRaises(DuplicateProtocolIdentifierError):
                store.create_experiment("experiment-1", second)
        finally:
            store.close()

    def test_analysis_round_trip_is_lossless_and_identity_checked(self):
        source = self.root / "protocol.pdf"
        write_pdf(source)
        protocol, readiness = structured_protocol(source)
        store = self.initialize()
        try:
            store.create_experiment("experiment-1", source)
            saved = store.append_analysis_revision(
                "experiment-1",
                1,
                "analysis-1",
                protocol,
                readiness,
                "p1-conservative",
            )
            loaded = store.get_analysis_revision("experiment-1", 1, 1)
        finally:
            store.close()

        self.assertEqual(saved, loaded)
        self.assertEqual(loaded.protocol, protocol)
        self.assertEqual(loaded.readiness, readiness)
        self.assertEqual(loaded.analysis_schema_version, ANALYSIS_SCHEMA_VERSION)
        self.assertEqual(loaded.capability_policy_id, "p1-conservative")
        action = loaded.protocol.sections[0].steps[0].sub_actions[0]
        self.assertEqual(action.quantities[0].source_text, "500 µL Solution A")
        self.assertEqual(
            action.evidence,
            evidence("Add 500 µL Solution A."),
        )
        self.assertEqual(
            action.missing_execution_values[0].value_id,
            "speedvac-duration",
        )
        self.assertIsInstance(
            loaded.protocol.constructs[0],
            RepeatUntil,
        )
        self.assertIsInstance(
            loaded.protocol.constructs[1],
            SourceAmbiguity,
        )
        self.assertEqual(loaded.readiness_label, ANALYSIS_REQUIRED_LABEL)
        self.assertEqual(
            loaded.readiness_reason_codes,
            readiness.reason_codes,
        )
        self.assertIn(
            ReadinessReasonCode.UNRESOLVED_AMBIGUITY.value,
            loaded.readiness_reason_codes,
        )

    def test_analysis_codec_is_deterministic_and_rejects_lossy_payloads(self):
        source = self.root / "protocol.pdf"
        write_pdf(source)
        protocol, readiness = structured_protocol(source)

        first_json, first_checksum = serialize_analysis(
            protocol,
            readiness,
            "p1-conservative",
        )
        second_json, second_checksum = serialize_analysis(
            protocol,
            readiness,
            "p1-conservative",
        )

        self.assertEqual((first_json, first_checksum), (second_json, second_checksum))
        decoded_protocol, decoded_readiness, policy_id, schema_version = (
            deserialize_analysis(first_json)
        )
        self.assertEqual(decoded_protocol, protocol)
        self.assertEqual(decoded_readiness, readiness)
        self.assertEqual(policy_id, "p1-conservative")
        self.assertEqual(schema_version, ANALYSIS_SCHEMA_VERSION)
        with self.assertRaises(ProtocolSerializationError) as context:
            deserialize_analysis('{"analysis_schema_version":1}')
        self.assertNotIn("Traceback", str(context.exception))

    def test_every_advanced_construct_round_trips_losslessly(self):
        source = self.root / "protocol.pdf"
        write_pdf(source)
        protocol, readiness = advanced_protocol(source)

        payload_json, _ = serialize_analysis(
            protocol,
            readiness,
            "p1-conservative",
        )
        decoded, decoded_readiness, policy_id, schema_version = (
            deserialize_analysis(payload_json)
        )

        self.assertEqual(decoded, protocol)
        self.assertEqual(
            tuple(type(item) for item in decoded.constructs),
            (
                ConditionalBranch,
                FixedRangeRepetition,
                RepeatUntil,
                ParallelWork,
                RecurringAction,
                ReusableSubprocedure,
                SourceAmbiguity,
                ProtocolConflict,
            ),
        )
        self.assertIs(decoded.constructs[0].kind, BranchKind.ALTERNATIVE)
        self.assertIs(
            decoded.constructs[-1].level,
            ConflictLevel.EXECUTION_VALUE,
        )
        self.assertEqual(
            decoded.constructs[0].branch_step_ids,
            ("step-2",),
        )
        self.assertEqual(
            decoded.constructs[3].concurrent_step_ids,
            ("step-1", "step-2"),
        )
        self.assertEqual(
            decoded.constructs[4].source_text,
            "Add 500 µL Solution A.",
        )
        self.assertEqual(
            decoded.constructs[4].evidence,
            evidence("Add 500 µL Solution A."),
        )
        self.assertEqual(
            decoded.sections[0]
            .steps[0]
            .sub_actions[0]
            .missing_execution_values[0]
            .value_id,
            "speedvac-duration",
        )
        self.assertEqual(decoded_readiness, readiness)
        self.assertIs(
            decoded_readiness.status,
            ReadinessStatus.ANALYSIS_REQUIRED,
        )
        self.assertEqual(
            decoded_readiness.reason_codes,
            readiness.reason_codes,
        )
        self.assertEqual(policy_id, "p1-conservative")
        self.assertEqual(schema_version, ANALYSIS_SCHEMA_VERSION)

    def test_changed_analysis_appends_and_identical_payload_bytes_deduplicate(self):
        source = self.root / "protocol.pdf"
        write_pdf(source)
        protocol, readiness = structured_protocol(source)
        changed = replace(
            protocol,
            metadata=replace(protocol.metadata, title="Changed analysis title"),
        )
        store = self.initialize()
        try:
            store.create_experiment("experiment-1", source)
            first = store.append_analysis_revision(
                "experiment-1",
                1,
                "analysis-1",
                protocol,
                readiness,
                "p1-conservative",
            )
            replay_payload = store.append_analysis_revision(
                "experiment-1",
                1,
                "analysis-2",
                protocol,
                readiness,
                "p1-conservative",
            )
            second = store.append_analysis_revision(
                "experiment-1",
                1,
                "analysis-3",
                changed,
                readiness,
                "p1-conservative",
            )
            payload_count = store._connection.execute(
                "SELECT count(*) FROM analysis_payloads"
            ).fetchone()[0]
        finally:
            store.close()

        self.assertEqual(first.analysis_revision_number, 1)
        self.assertEqual(replay_payload.analysis_revision_number, 2)
        self.assertEqual(first.payload_sha256, replay_payload.payload_sha256)
        self.assertEqual(second.analysis_revision_number, 3)
        self.assertNotEqual(first.payload_sha256, second.payload_sha256)
        self.assertEqual(payload_count, 2)

    def test_analysis_revision_number_and_payload_dedup_survive_reopen(self):
        source = self.root / "protocol.pdf"
        write_pdf(source)
        protocol, readiness = structured_protocol(source)
        changed = replace(
            protocol,
            metadata=replace(protocol.metadata, title="Changed after reopen"),
        )
        store = self.initialize()
        try:
            store.create_experiment("experiment-1", source)
            first = store.append_analysis_revision(
                "experiment-1",
                1,
                "analysis-1",
                protocol,
                readiness,
                "p1-conservative",
            )
        finally:
            store.close()

        reopened = self.initialize()
        try:
            preserved_first = reopened.get_analysis_revision(
                "experiment-1",
                1,
                1,
            )
            second = reopened.append_analysis_revision(
                "experiment-1",
                1,
                "analysis-2",
                changed,
                readiness,
                "p1-conservative",
            )
            third = reopened.append_analysis_revision(
                "experiment-1",
                1,
                "analysis-3",
                changed,
                readiness,
                "p1-conservative",
            )
            revisions = reopened.list_analysis_revisions("experiment-1", 1)
            payload_count = reopened._connection.execute(
                "SELECT count(*) FROM analysis_payloads"
            ).fetchone()[0]
        finally:
            reopened.close()

        self.assertEqual(preserved_first, first)
        self.assertEqual(second.analysis_revision_number, 2)
        self.assertEqual(third.analysis_revision_number, 3)
        self.assertEqual(second.payload_sha256, third.payload_sha256)
        self.assertEqual(
            tuple(item.analysis_revision_number for item in revisions),
            (1, 2, 3),
        )
        self.assertEqual(revisions[0], first)
        self.assertEqual(payload_count, 2)

    def test_analysis_identifier_conflict_and_revision_mismatch_are_rejected(self):
        source = self.root / "protocol.pdf"
        replacement = self.root / "replacement.pdf"
        write_pdf(source, "First")
        write_pdf(replacement, "Second")
        protocol, readiness = structured_protocol(source)
        changed = replace(
            protocol,
            metadata=replace(protocol.metadata, title="Changed"),
        )
        store = self.initialize()
        try:
            store.create_experiment("experiment-1", source)
            store.append_analysis_revision(
                "experiment-1",
                1,
                "analysis-1",
                protocol,
                readiness,
                "p1-conservative",
            )
            with self.assertRaises(DuplicateProtocolIdentifierError):
                store.append_analysis_revision(
                    "experiment-1",
                    1,
                    "analysis-1",
                    changed,
                    readiness,
                    "p1-conservative",
                )
            store.append_protocol_revision("experiment-1", replacement)
            with self.assertRaises(UnknownProtocolReferenceError):
                store.append_analysis_revision(
                    "experiment-1",
                    2,
                    "analysis-2",
                    protocol,
                    readiness,
                    "p1-conservative",
                )
        finally:
            store.close()

    def test_clarifications_and_events_are_append_only_ordered_and_reopen(self):
        source = self.root / "protocol.pdf"
        write_pdf(source)
        protocol, readiness = structured_protocol(source)
        store = self.initialize()
        store.create_experiment("experiment-1", source)
        store.append_analysis_revision(
            "experiment-1",
            1,
            "analysis-1",
            protocol,
            readiness,
            "p1-conservative",
        )
        first_clarification = store.append_clarification(
            "clarification-1",
            "experiment-1",
            1,
            1,
            ambiguity_source_text="Repeat steps 2–7.",
            interpretation="Researcher retained the ambiguity.",
            researcher="researcher-1",
            reason="Source range is self-referential.",
            related_step_id="step-1",
        )
        second_clarification = store.append_clarification(
            "clarification-2",
            "experiment-1",
            1,
            1,
            ambiguity_source_text="Repeat steps 2–7.",
            interpretation="Second append-only note.",
            researcher="researcher-1",
            reason="Additional context.",
            related_step_id="step-1",
        )
        first_event = store.append_event(
            "event-1",
            "experiment-1",
            1,
            "analysis_recorded",
            {"analysis_id": "analysis-1"},
            analysis_revision_number=1,
        )
        second_event = store.append_event(
            "event-2",
            "experiment-1",
            1,
            "clarification_recorded",
            {"clarification_id": "clarification-1"},
            analysis_revision_number=1,
        )
        store.close()

        reopened = self.initialize()
        try:
            clarifications = reopened.list_clarifications("experiment-1")
            events = reopened.list_events("experiment-1")
        finally:
            reopened.close()

        self.assertLess(
            first_clarification.sequence_id,
            second_clarification.sequence_id,
        )
        self.assertEqual(
            tuple(item.clarification_id for item in clarifications),
            ("clarification-1", "clarification-2"),
        )
        self.assertLess(first_event.sequence_id, second_event.sequence_id)
        self.assertEqual(
            tuple(item.event_id for item in events),
            ("event-1", "event-2"),
        )

    def test_dangling_clarification_and_event_references_are_rejected(self):
        source = self.root / "protocol.pdf"
        write_pdf(source)
        store = self.initialize()
        try:
            store.create_experiment("experiment-1", source)
            with self.assertRaises(UnknownProtocolReferenceError):
                store.append_clarification(
                    "clarification-1",
                    "experiment-1",
                    1,
                    999,
                    ambiguity_source_text="Missing analysis.",
                    interpretation="None.",
                    researcher="researcher-1",
                    reason="Test.",
                )
            with self.assertRaises(UnknownProtocolReferenceError):
                store.append_event(
                    "event-1",
                    "missing-experiment",
                    1,
                    "test_event",
                    {},
                )
        finally:
            store.close()

    def test_database_triggers_reject_update_and_delete(self):
        source = self.root / "protocol.pdf"
        write_pdf(source)
        protocol, readiness = structured_protocol(source)
        store = self.initialize()
        try:
            store.create_experiment("experiment-1", source)
            store.append_analysis_revision(
                "experiment-1",
                1,
                "analysis-1",
                protocol,
                readiness,
                "p1-conservative",
            )
            store.append_clarification(
                "clarification-1",
                "experiment-1",
                1,
                1,
                ambiguity_source_text="Repeat steps 2–7.",
                interpretation="No rewrite.",
                researcher="researcher-1",
                reason="Test.",
            )
            store.append_event(
                "event-1",
                "experiment-1",
                1,
                "analysis_recorded",
                {"analysis_id": "analysis-1"},
                analysis_revision_number=1,
            )
            tables = (
                "schema_metadata",
                "pdf_objects",
                "experiments",
                "protocol_revisions",
                "analysis_payloads",
                "analysis_revisions",
                "clarifications",
                "protocol_events",
            )
            for table in tables:
                for operation, statement in (
                    ("UPDATE", f"UPDATE {table} SET rowid=rowid"),
                    ("DELETE", f"DELETE FROM {table}"),
                ):
                    with self.subTest(
                        table=table,
                        operation=operation,
                    ), self.assertRaises(
                        ImmutableProtocolRecordError
                    ) as context:
                        store._execute_write(statement)
                    store._connection.rollback()
                    public_message = str(context.exception)
                    self.assertEqual(
                        public_message,
                        "Stored Protocol records cannot be changed or deleted.",
                    )
                    self.assertNotIn("immutable", public_message)
                    self.assertNotIn("append-only", public_message)
                    self.assertNotIn(operation, public_message)
                    self.assertNotIn(str(self.root), public_message)
                    self.assertNotIn("Traceback", public_message)
                    self.assertIsInstance(
                        context.exception.__cause__,
                        sqlite3.DatabaseError,
                    )
            self.assertFalse(hasattr(store, "update"))
            self.assertFalse(hasattr(store, "delete"))
        finally:
            store.close()

    def test_transaction_failure_rolls_back_database_records(self):
        source = self.root / "protocol.pdf"
        source_bytes = write_pdf(source)
        checksum = hashlib.sha256(source_bytes).hexdigest()
        store = self.initialize()
        try:
            store._connection.executescript(
                """
                CREATE TRIGGER injected_revision_failure
                BEFORE INSERT ON protocol_revisions
                BEGIN SELECT RAISE(ABORT,'injected private SQL detail'); END;
                """
            )
            with self.assertRaises(ProtocolTransactionError) as context:
                store.create_experiment("experiment-1", source)
            self.assertIsNone(store.get_experiment("experiment-1"))
            for table in (
                "pdf_objects",
                "experiments",
                "protocol_revisions",
                "analysis_payloads",
                "analysis_revisions",
                "clarifications",
                "protocol_events",
            ):
                with self.subTest(table=table):
                    self.assertEqual(
                        store._connection.execute(
                            f"SELECT count(*) FROM {table}"
                        ).fetchone()[0],
                        0,
                    )
            retained = store.file_store.verify_object(
                checksum,
                expected_size=len(source_bytes),
            )
            self.assertEqual(retained.checksum, checksum)
            self.assertEqual(
                (
                    self.data_dir
                    / "objects"
                    / "sha256"
                    / checksum[:2]
                    / f"{checksum}.pdf"
                ).read_bytes(),
                source_bytes,
            )
            self.assertNotIn("injected", str(context.exception))
            self.assertNotIn("SQL", str(context.exception))
        finally:
            store.close()

        reopened = self.initialize()
        try:
            self.assertIsNone(reopened.get_experiment("experiment-1"))
            self.assertEqual(
                reopened.list_protocol_revisions("experiment-1"),
                (),
            )
            reopened._connection.execute(
                "DROP TRIGGER injected_revision_failure"
            )
            reopened._connection.commit()
            deduplication_results = []
            original_store = reopened.file_store.store

            def record_deduplication(source_path):
                result = original_store(source_path)
                deduplication_results.append(result.deduplicated)
                return result

            with patch.object(
                reopened.file_store,
                "store",
                side_effect=record_deduplication,
            ):
                creation = reopened.create_experiment(
                    "experiment-1",
                    source,
                )
            self.assertEqual(deduplication_results, [True])
            self.assertEqual(
                creation.protocol_revision.pdf_checksum,
                checksum,
            )
            self.assertEqual(
                reopened.file_store.verify_object(
                    checksum,
                    expected_size=len(source_bytes),
                ),
                retained,
            )
            self.assertEqual(
                len(
                    tuple(
                        (self.data_dir / "objects" / "sha256").rglob(
                            "*.pdf"
                        )
                    )
                ),
                1,
            )
        finally:
            reopened.close()

    def test_unknown_schema_version_fails_without_rewriting_database(self):
        self.data_dir.mkdir(parents=True)
        database = self.data_dir / PROTOCOL_DATABASE_FILENAME
        with sqlite3.connect(database) as connection:
            connection.execute(
                "CREATE TABLE schema_metadata(schema_version INTEGER)"
            )
            connection.execute("INSERT INTO schema_metadata VALUES (999)")
        before = hashlib.sha256(database.read_bytes()).hexdigest()

        with self.assertRaises(UnsupportedProtocolSchemaError):
            self.initialize()

        self.assertEqual(
            hashlib.sha256(database.read_bytes()).hexdigest(),
            before,
        )

    def test_reopen_preserves_experiment_analysis_and_object_integrity(self):
        source = self.root / "protocol.pdf"
        source_bytes = write_pdf(source)
        protocol, readiness = structured_protocol(source)
        store = self.initialize()
        creation = store.create_experiment("experiment-1", source)
        store.append_analysis_revision(
            "experiment-1",
            1,
            "analysis-1",
            protocol,
            readiness,
            "p1-conservative",
        )
        store.close()

        reopened = self.initialize()
        try:
            self.assertIsNotNone(reopened.get_experiment("experiment-1"))
            self.assertEqual(
                reopened.get_analysis_revision("experiment-1", 1, 1).protocol,
                protocol,
            )
            verified = reopened.file_store.verify_object(
                creation.protocol_revision.pdf_checksum,
                expected_size=len(source_bytes),
            )
            self.assertEqual(
                verified.checksum,
                hashlib.sha256(source_bytes).hexdigest(),
            )
        finally:
            reopened.close()


if __name__ == "__main__":
    unittest.main()
