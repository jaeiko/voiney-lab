"""Lane PL, stage 2b: a development fixture that does not fit takes only itself out.

Lane BT (section 6, item 1) left this open: when the configured development
fixture could not be loaded, or the catalog already held an entry under the
fixture's id that this fixture did not make, ``GET /api/protocols`` failed as a
whole with a 503 -- the protocols the experimenter had uploaded included. The
fixture is development material; the uploaded protocols are the product. Now
the fixture alone is shown as not executable (or left out when it cannot be
read at all) and the rest of the list is shown.

The fixture is fake here, so no licensed PDF is needed; the scope is
``source_protocol_only`` so the server needs no safety catalog.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.test_protocol_catalog import write_text_pdf
from voiney_lab import server as server_module
from voiney_lab.experiment_protocol_config import ProtocolPersistenceSettings
from voiney_lab.experiment_protocol_store import initialize_protocol_store
from voiney_lab.protocol_catalog import ProtocolCatalog
from voiney_lab.server import list_protocol_catalog


def _fake_fixture(protocol_id: str) -> SimpleNamespace:
    """What ``_candidate_catalog_dict`` reads of a development fixture."""

    return SimpleNamespace(
        protocol_id=protocol_id,
        title="Fictional development fixture",
        source_pdf_path=Path("/fictional/source.pdf"),
        source_pdf_sha256="0" * 64,
        revision_id="fixture-revision",
        draft=SimpleNamespace(readiness=SimpleNamespace(status=SimpleNamespace(value="guidance_ready"))),
        steps=(),
        development_only=False,
    )


class _ListCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = ProtocolPersistenceSettings(True, self.root / "catalog")
        store = initialize_protocol_store(self.settings)
        catalog = ProtocolCatalog(store)
        self.entries = []
        for number in (1, 2):
            pdf = self.root / f"protocol-{number}.pdf"
            write_text_pdf(pdf, f"Fictional protocol {number}. 1 Add buffer {number} to the tube.",
                           title=f"Fictional {number}")
            self.entries.append(catalog.register(
                pdf, source_filename=pdf.name, media_type="application/pdf").entry)
        store.close()

    def environment(self, **extra: str) -> dict[str, str]:
        return {
            "VOINEY_LAB_USAGE_SCOPE": "source_protocol_only",
            "VOINEY_LAB_PROTOCOL_ENABLED": "true",
            "VOINEY_LAB_PROTOCOL_DATA_DIR": str(self.settings.data_dir),
            "VOINEY_LAB_WORKSPACE_ENABLED": "false",
            **extra,
        }

    def listed(self, **extra: str) -> list[dict]:
        with patch.dict(os.environ, self.environment(**extra), clear=True):
            return list_protocol_catalog()["protocols"]


class FixtureThatDoesNotLoadTests(_ListCase):
    def test_the_uploaded_protocols_are_listed_when_the_fixture_cannot_be_read(self):
        missing = str(self.root / "no-such-fixture.json")
        shown = self.listed(
            VOINEY_LAB_CURATED_PROTOCOL_FIXTURE=missing,
            VOINEY_LAB_CURATED_PROTOCOL_PROVENANCE=missing,
            VOINEY_LAB_CURATED_PROTOCOL_SOURCE_PDF=missing,
        )
        self.assertEqual(
            sorted(item["protocol_id"] for item in shown),
            sorted(entry.protocol_id for entry in self.entries))
        # A fixture nobody could read is not shown as if it could run.
        self.assertFalse(any(item.get("development_only") for item in shown))

    def test_the_failure_is_logged_by_class_only(self):
        missing = str(self.root / "no-such-fixture.json")
        with self.assertLogs("voiney_lab", level="WARNING") as logged:
            self.listed(
                VOINEY_LAB_CURATED_PROTOCOL_FIXTURE=missing,
                VOINEY_LAB_CURATED_PROTOCOL_PROVENANCE=missing,
                VOINEY_LAB_CURATED_PROTOCOL_SOURCE_PDF=missing,
            )
        text = "\n".join(logged.output)
        self.assertIn("development_fixture_unavailable", text)
        self.assertNotIn(str(self.root), text)


class FixtureThatConflictsTests(_ListCase):
    def test_a_fixture_that_the_catalog_does_not_match_is_listed_as_blocked_and_the_rest_stay(self):
        clash, other = self.entries
        fixture = _fake_fixture(clash.protocol_id)
        with patch.object(server_module, "_configured_candidate_fixture", return_value=fixture):
            shown = self.listed(
                VOINEY_LAB_CURATED_PROTOCOL_FIXTURE="/fictional/fixture.json",
                VOINEY_LAB_CURATED_PROTOCOL_PROVENANCE="/fictional/provenance.json",
                VOINEY_LAB_CURATED_PROTOCOL_SOURCE_PDF="/fictional/source.pdf",
            )
        by_id = {item["protocol_id"]: item for item in shown}
        self.assertEqual(sorted(by_id), sorted([clash.protocol_id, other.protocol_id]))
        row = by_id[clash.protocol_id]
        self.assertIs(row["development_only"], True)
        self.assertIs(row["available_for_execution"], False)
        self.assertEqual(row["lifecycle_state"], "blocked")
        self.assertEqual(row["execution_blocked_reason"], "development_fixture_conflicts_with_catalog")
        # The uploaded protocol that does not clash is listed as an upload.
        self.assertIs(by_id[other.protocol_id]["development_only"], False)
        self.assertEqual(by_id[other.protocol_id]["lifecycle_state"], "uploaded")

    def test_a_fixture_that_matches_nothing_in_the_catalog_is_listed_beside_the_uploads(self):
        fixture = _fake_fixture("fixture-protocol-id")
        with patch.object(server_module, "_configured_candidate_fixture", return_value=fixture):
            shown = self.listed(
                VOINEY_LAB_CURATED_PROTOCOL_FIXTURE="/fictional/fixture.json",
                VOINEY_LAB_CURATED_PROTOCOL_PROVENANCE="/fictional/provenance.json",
                VOINEY_LAB_CURATED_PROTOCOL_SOURCE_PDF="/fictional/source.pdf",
            )
        self.assertEqual(len(shown), 3)
        self.assertEqual(shown[0]["protocol_id"], "fixture-protocol-id")
        self.assertEqual(shown[0]["execution_blocked_reason"], "usage_scope_not_development")


class NoFixtureTests(_ListCase):
    def test_without_a_fixture_the_list_is_the_uploads(self):
        shown = self.listed()
        self.assertEqual(
            sorted(item["protocol_id"] for item in shown),
            sorted(entry.protocol_id for entry in self.entries))


if __name__ == "__main__":
    unittest.main()
