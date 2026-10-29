"""An upload records its public/commercial-source declaration.

The tests use synthetic text PDFs and the ASGI boundary. No provider or
network request leaves the process.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import quote

import httpx

from tests.test_protocol_catalog import _dedicated_to_thread, write_text_pdf
from voiney_lab import server as server_module
from voiney_lab.experiment_protocol_config import (
    ProtocolPersistenceSettings,
)
from voiney_lab.experiment_protocol_store import initialize_protocol_store
from voiney_lab.protocol_catalog import (
    ProtocolCatalog,
    ProtocolSourceDisclosure,
    ProtocolSourceStatus,
)


SETTING = "VOINEY_LAB_PUBLIC_SOURCE_ONLY"


class SourceDisclosureCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = initialize_protocol_store(
            ProtocolPersistenceSettings(True, self.root / "catalog")
        )
        self.catalog = ProtocolCatalog(self.store)

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def register(self, filename: str, title: str) -> str:
        path = self.root / filename
        write_text_pdf(
            path,
            f"{title}\n1. Follow the fictional source instruction.",
            title=title,
        )
        return self.catalog.register(
            path, source_filename=filename, media_type="application/pdf"
        ).entry.protocol_id

    def test_disclosures_are_typed_domain_neutral_and_append_only(self) -> None:
        examples = (
            ("cell-culture.pdf", "Fictional cell culture", ProtocolSourceStatus.YES, "공개 저장소 DOI 10.0000/example"),
            ("organic-synthesis.pdf", "Fictional synthesis", ProtocolSourceStatus.NO, "연구실 내부 합성법"),
            ("battery-slurry.pdf", "Fictional battery slurry", ProtocolSourceStatus.UNKNOWN, "공개 여부 확인 중"),
            ("composite-cure.pdf", "Fictional composite cure", ProtocolSourceStatus.YES, "시판 제품 설명서"),
        )
        protocol_ids: list[str] = []
        for filename, title, status, note in examples:
            with self.subTest(filename=filename):
                protocol_id = self.register(filename, title)
                protocol_ids.append(protocol_id)
                recorded = self.catalog.record_source_disclosure(
                    protocol_id, ProtocolSourceDisclosure(status=status, note=note)
                )
                self.assertEqual(recorded.status, status)
                public = self.catalog.get_entry(protocol_id).public_dict()["source_disclosure"]
                self.assertEqual(public["status"], status.value)
                self.assertEqual(public["note"], note)
                self.assertTrue(public["recorded"])
                self.assertTrue(public["recorded_at"])

        protocol_id = protocol_ids[0]
        same = ProtocolSourceDisclosure(
            status=ProtocolSourceStatus.YES,
            note="공개 저장소 DOI 10.0000/example",
        )
        self.catalog.record_source_disclosure(protocol_id, same)
        self.catalog.record_source_disclosure(
            protocol_id,
            ProtocolSourceDisclosure(
                status=ProtocolSourceStatus.UNKNOWN,
                note="공개 상태를 다시 확인 중",
            ),
        )
        self.catalog.record_source_disclosure(protocol_id, same)
        events = [
            event
            for event in self.store.list_events(protocol_id)
            if event.event_type == "protocol_source_disclosure_recorded"
        ]
        self.assertEqual(
            [event.payload["status"] for event in events],
            ["yes", "unknown", "yes"],
        )
        latest = self.catalog.get_entry(protocol_id).public_dict()["source_disclosure"]
        self.assertEqual(latest["status"], "yes")
        self.assertEqual(latest["note"], "공개 저장소 DOI 10.0000/example")

    def test_an_old_or_internal_registration_is_explicitly_unrecorded(self) -> None:
        protocol_id = self.register("legacy.pdf", "Fictional legacy protocol")
        self.assertEqual(
            self.catalog.get_entry(protocol_id).public_dict()["source_disclosure"],
            {
                "recorded": False,
                "status": None,
                "status_ko": "확인 기록 없음",
                "note": "",
                "recorded_at": None,
            },
        )


class PublicSourceUploadBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data_dir = self.root / "catalog"
        source = self.root / "fictional.pdf"
        self.pdf = write_text_pdf(
            source,
            "Fictional protocol\n1. Mix the fictional sample.",
            title="Fictional protocol",
        )
        self.provider = Mock(
            side_effect=AssertionError("upload must not call an analysis provider")
        )

    async def asyncTearDown(self) -> None:
        server_module._PROTOCOL_OCR_TASKS.clear()
        server_module._PROTOCOL_ANALYSIS_TASKS.clear()
        self.temp.cleanup()

    def open_catalog(self):
        store = initialize_protocol_store(
            ProtocolPersistenceSettings(True, self.data_dir)
        )
        return ProtocolCatalog(store), store

    async def request(
        self,
        path: str,
        *,
        setting: str | None,
        headers: dict[str, str] | None = None,
        json: object | None = None,
    ) -> httpx.Response:
        request_headers = {"Content-Type": "application/pdf", **(headers or {})}
        with (
            patch.dict(os.environ, {}, clear=False),
            patch.object(server_module, "_open_protocol_catalog", self.open_catalog),
            patch("fastapi.routing.run_in_threadpool", side_effect=_dedicated_to_thread),
            patch.object(server_module.asyncio, "to_thread", side_effect=_dedicated_to_thread),
            patch.object(server_module, "_protocol_analysis_model", self.provider),
        ):
            if setting is None:
                os.environ.pop(SETTING, None)
            else:
                os.environ[SETTING] = setting
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=server_module.app),
                base_url="http://testserver",
            ) as client:
                if json is not None:
                    return await client.post(path, json=json)
                return await client.post(path, content=self.pdf, headers=request_headers)

    def assert_store_empty(self) -> None:
        catalog, store = self.open_catalog()
        try:
            self.assertEqual(store.list_experiments(), ())
        finally:
            store.close()
        self.assertEqual(tuple((self.data_dir / "objects" / "sha256").glob("*/*.pdf")), ())

    async def test_the_default_is_off_and_legacy_uploads_remain_compatible(self) -> None:
        response = await self.request(
            "/api/protocols?filename=legacy.pdf", setting=None
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.assertFalse(response.json()["protocol"]["source_disclosure"]["recorded"])

    async def test_public_only_refuses_missing_no_and_unknown_before_storage(self) -> None:
        attempts = (
            ({}, "missing"),
            ({"X-Voiney-Source-Status": "no"}, "no"),
            ({"X-Voiney-Source-Status": "unknown"}, "unknown"),
        )
        for headers, label in attempts:
            with self.subTest(label=label):
                response = await self.request(
                    f"/api/protocols?filename={label}.pdf",
                    setting="true",
                    headers=headers,
                )
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(response.json()["detail"]["code"], "public_source_required")
                self.assert_store_empty()

    async def test_yes_and_its_korean_source_note_round_trip_through_all_reads(self) -> None:
        note = "시판 키트 설명서 · 제조사 공개 페이지"
        response = await self.request(
            "/api/protocols?filename=kit-insert.pdf",
            setting="true",
            headers={
                "X-Voiney-Source-Status": "yes",
                "X-Voiney-Source-Note": quote(note, safe=""),
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        protocol = response.json()["protocol"]
        self.assertEqual(
            protocol["source_disclosure"] | {"recorded_at": None},
            {
                "recorded": True,
                "status": "yes",
                "status_ko": "예 — 공개·시판 원문",
                "note": note,
                "recorded_at": None,
            },
        )
        self.assertTrue(protocol["source_disclosure"]["recorded_at"])
        protocol_id = protocol["protocol_id"]

        with (
            patch.object(server_module, "_open_protocol_catalog", self.open_catalog),
            patch.object(
                server_module,
                "server_config",
                return_value=Mock(curated_protocol_fixture_path=None),
            ),
            patch.object(
                server_module,
                "_protocol_store_settings",
                return_value=ProtocolPersistenceSettings(True, self.data_dir),
            ),
            patch("fastapi.routing.run_in_threadpool", side_effect=_dedicated_to_thread),
            patch.object(server_module.asyncio, "to_thread", side_effect=_dedicated_to_thread),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=server_module.app),
                base_url="http://testserver",
            ) as client:
                listed = await client.get("/api/protocols")
                detailed = await client.get(f"/api/protocols/{protocol_id}")
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(detailed.status_code, 200, detailed.text)
        self.assertEqual(listed.json()["protocols"][0]["source_disclosure"]["note"], note)
        self.assertEqual(detailed.json()["source_disclosure"]["status"], "yes")
        self.provider.assert_not_called()

    async def test_invalid_explicit_values_are_rejected_even_when_the_policy_is_off(self) -> None:
        for headers in (
            {"X-Voiney-Source-Status": "public"},
            {
                "X-Voiney-Source-Status": "yes",
                "X-Voiney-Source-Note": quote("x" * 501, safe=""),
            },
        ):
            with self.subTest(headers=headers):
                response = await self.request(
                    "/api/protocols?filename=invalid.pdf",
                    setting="false",
                    headers=headers,
                )
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(response.json()["detail"]["code"], "source_disclosure_invalid")
                self.assert_store_empty()

    async def test_an_invalid_public_only_setting_fails_closed_by_setting_name(self) -> None:
        response = await self.request(
            "/api/protocols?filename=invalid-setting.pdf",
            setting="sometimes",
            headers={"X-Voiney-Source-Status": "yes"},
        )
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(
            response.json()["detail"],
            {"code": "configuration_invalid", "setting": SETTING},
        )
        self.assert_store_empty()

    async def test_link_policy_refuses_before_any_google_fetch(self) -> None:
        fetch = Mock(side_effect=AssertionError("the refused link must not be fetched"))
        with (
            patch.object(server_module, "fetch_google_link", fetch),
            patch.object(server_module, "_request_account", return_value=object()),
        ):
            response = await self.request(
                "/api/protocols/import-link",
                setting="true",
                json={
                    "url": "https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUvWxYz/view",
                    "public_source_status": "unknown",
                    "source_note": "확인 중",
                },
            )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["detail"]["code"], "public_source_required")
        fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
