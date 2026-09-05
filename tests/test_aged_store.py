"""A store with a history keeps working, and the two known defects are pinned.

See ``tests/aged_store`` for why a freshly created temporary store cannot
express the state in which both STEP 14's and STEP 22-B's defects appeared.
Each of the first two tests here fails against the code as it stood before the
corresponding fix, which is the only reason to believe the harness is doing
anything.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from voice_workflow_agent.experiment_protocol_config import (
    ProtocolPersistenceSettings,
)
from voice_workflow_agent.experiment_protocol_store import (
    ANALYSIS_SCHEMA_VERSION,
    ProtocolSerializationError,
    deserialize_analysis,
    initialize_protocol_store,
    serialize_analysis,
)
from voice_workflow_agent.protocol_catalog import ProtocolCatalog
import voice_workflow_agent.server as server_module

from tests.aged_store import (
    CORPUS_ANALYSIS_SCHEMA_VERSION,
    KNOWN_PAYLOAD_KEYS,
    TOLERATED_ABSENT_KEYS,
    analysis_payload_as_written_before,
    curated_fixture_for,
    draft_for,
    edited_fixture,
)
from tests.test_protocol_catalog import write_text_pdf


class AgedByAccumulationTests(unittest.TestCase):
    """The store has been running long enough to hold more than one analysis."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.settings = ProtocolPersistenceSettings(True, self.root / "catalog")
        self.store = initialize_protocol_store(self.settings)
        self.catalog = ProtocolCatalog(self.store)
        self.source_pdf = self.root / "source.pdf"
        write_text_pdf(
            self.source_pdf,
            "Protocol Test\nSection preparation\n1. Add solution.\nWear gloves.",
            title="Protocol Test",
        )
        self.protocol_id = "aged-development-fixture-v1"
        draft = draft_for(self.source_pdf, self.protocol_id)
        self.first = curated_fixture_for(
            draft, marker=b"fixture-before-the-edit", source_pdf=self.source_pdf
        )
        self.second = edited_fixture(self.first, marker=b"fixture-after-the-edit")

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def _age_the_store(self) -> None:
        """Bootstrap once, edit the fixture, bootstrap again."""

        self.catalog.bootstrap_development_fixture(self.first)
        self.catalog.bootstrap_development_fixture(self.second)

    def test_editing_the_fixture_leaves_the_store_readable(self) -> None:
        self._age_the_store()
        analyses = self.store.list_analysis_revisions(self.protocol_id, 1)
        self.assertEqual(len(analyses), 2)

        # The question is which fixture the store now serves, not how many
        # analyses it accumulated getting there.
        self.assertTrue(
            self.catalog.development_fixture_is_materialized(self.second)
        )
        self.assertFalse(
            self.catalog.development_fixture_is_materialized(self.first),
            "a superseded fixture must not claim to be what the catalog serves",
        )

    def test_a_fixture_the_store_never_saw_is_still_refused(self) -> None:
        self._age_the_store()
        stranger = edited_fixture(self.first, marker=b"fixture-never-bootstrapped")
        self.assertFalse(
            self.catalog.development_fixture_is_materialized(stranger)
        )

    def test_the_catalog_listing_survives_an_aged_store(self) -> None:
        """The exact 503 of 2026-09-04, reproduced through the real endpoint."""

        self._age_the_store()
        store = self.store
        catalog = self.catalog

        def open_catalog():
            return catalog, store

        with patch.object(
            server_module, "_protocol_store_settings", return_value=self.settings
        ), patch.object(
            server_module, "_open_protocol_catalog", side_effect=open_catalog
        ), patch.object(
            server_module, "server_config"
        ) as config, patch.object(
            server_module, "_configured_candidate_fixture", return_value=self.second
        ), patch.object(
            server_module, "_candidate_fixture_execution_state",
            return_value={
                "available_for_execution": False,
                "blocked_reason": "development_activation_not_recorded",
                "development_activation": {"activated": False},
                "approval": {},
            },
        ):
            config.return_value = None
            entries, _settings, candidate_id = (
                server_module._public_protocol_catalog_entries()
            )

        self.assertEqual(candidate_id, self.protocol_id)
        self.assertEqual(
            [item["protocol_id"] for item in entries], [self.protocol_id]
        )


class AgedByFieldSetTests(unittest.TestCase):
    """Payloads written before a field existed are still payloads."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source_pdf = self.root / "source.pdf"
        write_text_pdf(
            self.source_pdf,
            "Protocol Test\nSection preparation\n1. Add solution.\nWear gloves.",
            title="Protocol Test",
        )
        self.draft = draft_for(self.source_pdf, "aged-payload-protocol")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_every_optional_key_may_be_absent(self) -> None:
        for key in sorted(TOLERATED_ABSENT_KEYS):
            with self.subTest(missing=key):
                payload = analysis_payload_as_written_before(
                    self.draft, without=key
                )
                self.assertNotIn(key, json.loads(payload))
                protocol, readiness, policy_id, version, coverage = (
                    deserialize_analysis(payload)
                )
                self.assertEqual(protocol, self.draft.protocol)
                self.assertEqual(readiness, self.draft.readiness)
                self.assertEqual(policy_id, self.draft.capability_policy_id)
                self.assertEqual(version, ANALYSIS_SCHEMA_VERSION)
                self.assertEqual(coverage, ())

    def test_a_required_key_absent_is_still_refused(self) -> None:
        """Tolerating an old payload is not tolerating a broken one."""

        payload = analysis_payload_as_written_before(
            self.draft, without="capability_policy_id"
        )
        with self.assertRaises(ProtocolSerializationError):
            deserialize_analysis(payload)

    def test_an_unknown_key_is_still_refused(self) -> None:
        encoded, _ = serialize_analysis(
            self.draft.protocol,
            self.draft.readiness,
            self.draft.capability_policy_id,
        )
        payload = json.loads(encoded)
        payload["invented_field"] = 1
        with self.assertRaises(ProtocolSerializationError):
            deserialize_analysis(json.dumps(payload))


class SchemaChangeTripwireTests(unittest.TestCase):
    """Changing the stored shape must reach this file before it reaches a store."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source_pdf = self.root / "source.pdf"
        write_text_pdf(
            self.source_pdf,
            "Protocol Test\nSection preparation\n1. Add solution.\nWear gloves.",
            title="Protocol Test",
        )
        self.draft = draft_for(self.source_pdf, "tripwire-protocol")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_the_analysis_schema_version_matches_this_corpus(self) -> None:
        self.assertEqual(
            ANALYSIS_SCHEMA_VERSION,
            CORPUS_ANALYSIS_SCHEMA_VERSION,
            "ANALYSIS_SCHEMA_VERSION changed: every analysis already in a "
            "store was written at the old one. Decide what happens to those "
            "payloads, extend tests/aged_store to cover the old shape, then "
            "raise CORPUS_ANALYSIS_SCHEMA_VERSION.",
        )

    def test_the_serializer_emits_no_key_this_corpus_has_not_seen(self) -> None:
        encoded, _ = serialize_analysis(
            self.draft.protocol,
            self.draft.readiness,
            self.draft.capability_policy_id,
            page_coverage=(),
        )
        emitted = set(json.loads(encoded))
        self.assertLessEqual(
            emitted,
            set(KNOWN_PAYLOAD_KEYS),
            "serialize_analysis emits a key tests/aged_store does not know. "
            "A payload written before it existed will not carry it, so record "
            "whether its absence must be tolerated.",
        )

    def test_the_decoder_states_which_absences_it_tolerates(self) -> None:
        """The tolerated set is read from the decoder, not from a wish.

        ``deserialize_analysis`` names its optional keys inline.  If that set
        and this file's disagree, one of them is out of date and old payloads
        are the thing at risk.
        """

        import inspect

        source = inspect.getsource(deserialize_analysis)
        for key in TOLERATED_ABSENT_KEYS:
            self.assertIn(
                f'"{key}"',
                source,
                f"{key} is recorded as tolerated but the decoder never names it",
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
