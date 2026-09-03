"""Every payload already on disk must still decode.

Adding one field to a domain dataclass made an analysis written months earlier
undecodable, and 1145 tests did not notice, because every one of them wrote its
payload with the same code that read it back. Only data that predates the
change can catch that, so this test reads what is actually on disk.

The file list is discovered, never maintained by hand: any store added under
the runtime directory is picked up automatically. Everything is opened
read-only in immutable mode, so running the suite cannot modify or lock an
operational store.
"""

from __future__ import annotations

import json
import sqlite3
import unittest
from pathlib import Path

from voice_workflow_agent import experiment_protocol as domain
from voice_workflow_agent.experiment_protocol_store import (
    ProtocolSerializationError,
    deserialize_analysis,
)

RUNTIME = Path("data/runtime")


def _stores() -> list[Path]:
    if not RUNTIME.is_dir():
        return []
    return sorted(
        path
        for path in RUNTIME.rglob("*.sqlite")
        if path.is_file() and not path.name.endswith(("-wal", "-shm"))
    )


def _payload_tables(connection: sqlite3.Connection) -> list[str]:
    return [
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        if row[0] == "analysis_payloads"
    ]


def _payloads() -> list[tuple[Path, str, str]]:
    found: list[tuple[Path, str, str]] = []
    for store in _stores():
        try:
            connection = sqlite3.connect(
                f"file:{store}?mode=ro&immutable=1", uri=True
            )
        except sqlite3.Error:
            continue
        try:
            for table in _payload_tables(connection):
                for row in connection.execute(
                    f"SELECT payload_sha256, payload_json FROM {table}"
                ):
                    found.append((store, row[0], row[1]))
        except sqlite3.Error:
            continue
        finally:
            connection.close()
    return found


class StoredPayloadsDecodeTests(unittest.TestCase):
    def test_a_store_is_present_to_read(self) -> None:
        """Without this, an empty sweep would look like a pass."""

        if not _stores():
            self.skipTest(
                f"No store found under {RUNTIME}; this check needs data that "
                "predates the current code to be meaningful."
            )
        self.assertTrue(_stores())

    def test_every_stored_analysis_payload_still_decodes(self) -> None:
        payloads = _payloads()
        if not payloads:
            self.skipTest(
                f"No analysis payloads found under {RUNTIME}. This check is "
                "only meaningful against data written by earlier code."
            )
        for store, digest, payload_json in payloads:
            with self.subTest(store=str(store), payload=digest[:16]):
                try:
                    protocol, readiness, policy_id, version = (
                        deserialize_analysis(payload_json)[:4]
                    )
                except ProtocolSerializationError as error:
                    self.fail(
                        f"{store.name} payload {digest[:16]} no longer "
                        f"decodes: {error}. A field added since it was "
                        f"written must come from its default."
                    )
                self.assertIsInstance(protocol, domain.ExperimentProtocol)
                self.assertIsInstance(readiness, domain.ReadinessAssessment)
                self.assertTrue(policy_id)

    def test_a_decoded_payload_is_usable_not_merely_parsed(self) -> None:
        """Decoding has to yield something a reviewer could actually read."""

        payloads = _payloads()
        if not payloads:
            self.skipTest(f"No analysis payloads found under {RUNTIME}.")
        for store, digest, payload_json in payloads:
            with self.subTest(store=str(store), payload=digest[:16]):
                protocol = deserialize_analysis(payload_json)[0]
                domain.validate_protocol(protocol)
                steps = [
                    step
                    for section in protocol.sections
                    for step in section.steps
                ]
                self.assertTrue(protocol.metadata.title)
                for step in steps:
                    self.assertTrue(step.evidence.source_excerpt)

    def test_the_payload_json_is_json(self) -> None:
        for store, digest, payload_json in _payloads():
            with self.subTest(store=str(store), payload=digest[:16]):
                self.assertIsInstance(json.loads(payload_json), dict)


class MissingRequiredFieldIsStillRefusedTests(unittest.TestCase):
    """Tolerating an absent field must not tolerate a truncated record.

    A field with a default may be missing from an old payload. A field without
    one may not: that record really is malformed.
    """

    def test_a_record_missing_a_required_field_is_refused(self) -> None:
        payload = {
            "analysis_schema_version": 1,
            "capability_policy_id": "p1-conservative",
            "protocol": {
                "$type": "SourceEvidence",
                "fields": {"source_excerpt": "text"},
            },
            "readiness": None,
        }
        with self.assertRaises(ProtocolSerializationError):
            deserialize_analysis(json.dumps(payload))

    def test_an_unknown_field_is_refused(self) -> None:
        payload = {
            "analysis_schema_version": 1,
            "capability_policy_id": "p1-conservative",
            "protocol": {
                "$type": "SourceEvidence",
                "fields": {
                    "source_page_number": 1,
                    "source_excerpt": "text",
                    "invented_field": 1,
                },
            },
            "readiness": None,
        }
        with self.assertRaises(ProtocolSerializationError):
            deserialize_analysis(json.dumps(payload))


if __name__ == "__main__":
    unittest.main()
