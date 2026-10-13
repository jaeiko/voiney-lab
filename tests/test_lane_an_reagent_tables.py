"""An Excel workbook attached to a protocol as its reagent table (lane AN, FM b).

Human decision 2026-10-13: an .xlsx is not a protocol source. It is attached
to a protocol as its "시약 표", every sheet and non-empty cell kept where the
workbook prints it, so that a question about an amount can be answered with
the table position and the cell value ("시약 표 Sheet1 B3: 10 mL"). It is never
analysed, never a step's evidence, and the protocol's readiness does not
depend on it. Connecting it to voice is a later lane; here is the data and
the lookup it will read.
"""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
import openpyxl

from tests.test_protocol_catalog import _dedicated_to_thread, write_text_pdf
from voiney_lab import protocol_catalog as catalog_module
from voiney_lab import server as server_module
from voiney_lab.experiment_protocol_config import ProtocolPersistenceSettings
from voiney_lab.experiment_protocol_pdf import clear_protocol_pdf_cache
from voiney_lab.experiment_protocol_store import initialize_protocol_store
from voiney_lab.protocol_catalog import (
    REAGENT_TABLE_MEDIA_TYPE,
    ProtocolCatalog,
    ReagentTableError,
    ReagentTableTooLargeError,
    find_reagent_cells,
    read_reagent_workbook,
)


def workbook_bytes(*, extra_sheet: bool = True) -> bytes:
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "시약"
    sheet.append(["Reagent", "Conc.", "Volume", "Share"])
    sheet.append(["Tris-HCl pH 8.0", "1 M", 10, 0.25])
    sheet.append(["EDTA", "0.5 M", 2.5, 0.125])
    sheet.append(["증류수", None, "=100-C2-C3", None])
    sheet["C2"].number_format = '0.0 "mL"'
    sheet["C3"].number_format = "0.00"
    sheet["D2"].number_format = "0%"
    sheet["D3"].number_format = "0.0%"
    sheet.merge_cells("F1:G1")
    sheet["F1"] = "Lot"
    if extra_sheet:
        hidden = book.create_sheet("old")
        hidden["A1"] = "Tris base"
        hidden.sheet_state = "hidden"
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


class ReadWorkbookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "reagents.xlsx"
        self.path.write_bytes(workbook_bytes())

    def tearDown(self) -> None:
        self.temp.cleanup()

    def cells(self, table, sheet="시약"):
        return {cell["ref"]: cell for cell in next(s for s in table["sheets"] if s["name"] == sheet)["cells"]}

    def test_every_non_empty_cell_is_kept_where_the_workbook_prints_it(self) -> None:
        table = read_reagent_workbook(self.path)
        self.assertEqual([sheet["name"] for sheet in table["sheets"]], ["시약", "old"])
        cells = self.cells(table)
        self.assertEqual(cells["A2"]["value"], "Tris-HCl pH 8.0")
        self.assertEqual((cells["C2"]["row"], cells["C2"]["column"], cells["C2"]["value"]), (2, 3, 10))
        self.assertNotIn("B4", cells)
        self.assertEqual(table["sheets"][0]["merged"], ["F1:G1"])
        self.assertEqual(table["sheets"][1]["state"], "hidden")
        self.assertEqual(table["cell_count"], 16)

    def test_percent_and_decimals_read_as_the_format_prints_them(self) -> None:
        cells = self.cells(read_reagent_workbook(self.path))
        self.assertEqual((cells["D2"]["display"], cells["D2"]["number_format"]), ("25%", "0%"))
        self.assertEqual(cells["D3"]["display"], "12.5%")
        self.assertEqual(cells["C3"]["display"], "2.50")
        # Any other format reads as the plain value, the format kept beside it.
        self.assertEqual((cells["C2"]["display"], cells["C2"]["number_format"]), ("10.0", '0.0 "mL"'))

    def test_a_formula_saved_without_its_value_is_kept_and_counted(self) -> None:
        table = read_reagent_workbook(self.path)
        cell = self.cells(table)["C4"]
        self.assertEqual((cell["formula"], cell["value"]), ("=100-C2-C3", None))
        self.assertEqual(len(table["warnings"]), 1)
        self.assertIn("수식 셀 1개", table["warnings"][0])

    def test_a_file_that_is_not_a_workbook_is_refused(self) -> None:
        self.path.write_bytes(b"Reagent,Volume\nEDTA,2 mL\n")
        with self.assertRaises(ReagentTableError):
            read_reagent_workbook(self.path)

    def test_a_workbook_too_large_is_refused_not_cut(self) -> None:
        with patch.object(catalog_module, "MAX_REAGENT_TABLE_CELLS", 5), \
                self.assertRaises(ReagentTableTooLargeError):
            read_reagent_workbook(self.path)


class LookupTests(unittest.TestCase):
    def setUp(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "reagents.xlsx"
            path.write_bytes(workbook_bytes())
            self.tables = [{"table_id": "t1", "filename": "reagents.xlsx", **read_reagent_workbook(path)}]

    def test_a_question_finds_the_cell_with_its_row_and_headers(self) -> None:
        (found,) = find_reagent_cells(self.tables, "tris hcl")
        self.assertEqual((found["sheet"], found["ref"], found["value"]), ("시약", "A2", "Tris-HCl pH 8.0"))
        row = {cell["ref"]: (cell["header"], cell["display"]) for cell in found["row"]}
        self.assertEqual(row["C2"], ("Volume", "10.0"))
        self.assertEqual(row["B2"], ("Conc.", "1 M"))

    def test_hangul_and_hidden_sheets_are_searched(self) -> None:
        self.assertEqual([hit["ref"] for hit in find_reagent_cells(self.tables, "증류수")], ["A4"])
        self.assertEqual([(hit["sheet"], hit["ref"]) for hit in find_reagent_cells(self.tables, "tris")],
                         [("시약", "A2"), ("old", "A1")])

    def test_a_title_line_above_the_table_is_not_its_header(self) -> None:
        book = openpyxl.Workbook()
        sheet = book.active
        sheet.append(["S1 Table. Key resources"])
        sheet.append(["REAGENT", "SOURCE", "IDENTIFIER"])
        sheet.append(["Proteinase K", "Qiagen", "19131"])
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "s1.xlsx"
            book.save(path)
            table = {"table_id": "t2", **read_reagent_workbook(path)}
        (hit,) = find_reagent_cells([table], "proteinase")
        self.assertEqual([(cell["header"], cell["display"]) for cell in hit["row"]],
                         [("REAGENT", "Proteinase K"), ("SOURCE", "Qiagen"), ("IDENTIFIER", "19131")])
        (title,) = find_reagent_cells([table], "key resources")
        self.assertEqual(title["row"][0]["header"], None)

    def test_nothing_or_punctuation_finds_nothing(self) -> None:
        self.assertEqual(find_reagent_cells(self.tables, " - "), [])
        self.assertEqual(find_reagent_cells(self.tables, "glycerol"), [])


class AttachTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data_dir = self.root / "catalog"
        clear_protocol_pdf_cache()
        self.env = patch.dict(os.environ, {
            "VOINEY_LAB_WORKSPACE_ENABLED": "false", "VOINEY_LAB_USAGE_SCOPE": "demo"})
        self.env.start()
        self.patches = (
            patch.object(server_module, "_open_protocol_catalog", self._open_catalog),
            patch("fastapi.routing.run_in_threadpool", side_effect=_dedicated_to_thread),
            patch.object(server_module.asyncio, "to_thread", side_effect=_dedicated_to_thread),
        )
        for item in self.patches:
            item.start()
        pdf = self.root / "p.pdf"
        write_text_pdf(pdf, "Fictional protocol. 1. Add 10 mL Tris-HCl.", title="P")
        catalog, store = self._open_catalog()
        try:
            entry = catalog.register(pdf, source_filename="p.pdf", media_type="application/pdf").entry
        finally:
            store.close()
        self.protocol_id, self.status = entry.protocol_id, entry.analysis_status

    def tearDown(self) -> None:
        for item in self.patches:
            item.stop()
        self.env.stop()
        clear_protocol_pdf_cache()
        self.temp.cleanup()

    def _open_catalog(self):
        store = initialize_protocol_store(ProtocolPersistenceSettings(True, self.data_dir))
        return ProtocolCatalog(store), store

    async def attach(self, client, data: bytes, name="reagents.xlsx", media_type=REAGENT_TABLE_MEDIA_TYPE,
                     protocol_id=None):
        return await client.post(
            f"/api/protocols/{protocol_id or self.protocol_id}/reagent-tables?filename={name}",
            content=data, headers={"Content-Type": media_type})

    def client(self):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=server_module.app),
                                 base_url="http://testserver")

    async def test_a_workbook_is_attached_once_and_read_back_cell_by_cell(self) -> None:
        async with self.client() as client:
            first = await self.attach(client, workbook_bytes())
            self.assertEqual(first.status_code, 201, first.text)
            summary = first.json()["reagent_table"]
            self.assertFalse(first.json()["deduplicated"])
            self.assertEqual(summary["sheets"], [{"name": "시약", "cells": 15}, {"name": "old", "cells": 1}])
            again = await self.attach(client, workbook_bytes(), name="copy.xlsx")
            self.assertTrue(again.json()["deduplicated"])
            listed = (await client.get(f"/api/protocols/{self.protocol_id}/reagent-tables")).json()
        (table,) = listed["reagent_tables"]
        self.assertEqual(table["table_id"], summary["table_id"])
        self.assertEqual(table["authority"], "person_upload")
        self.assertEqual(find_reagent_cells([table], "EDTA")[0]["ref"], "A3")

    async def test_a_reagent_table_changes_nothing_about_the_protocol(self) -> None:
        async with self.client() as client:
            await self.attach(client, workbook_bytes())
        catalog, store = self._open_catalog()
        try:
            entry = catalog.get_entry(self.protocol_id)
            kinds = [event.event_type for event in store.list_events(self.protocol_id)]
        finally:
            store.close()
        self.assertEqual(entry.analysis_status, self.status)
        self.assertEqual(kinds, ["protocol_registered", "protocol_reagent_table_attached"])

    async def test_refusals_name_their_reason(self) -> None:
        async with self.client() as client:
            cases = (
                (workbook_bytes(), "r.xlsx", "text/csv", None, 415, "unsupported_reagent_table_media_type"),
                (b"Reagent,Volume\n", "r.xlsx", REAGENT_TABLE_MEDIA_TYPE, None, 422, "invalid_reagent_table"),
                (b"", "r.xlsx", REAGENT_TABLE_MEDIA_TYPE, None, 422, "invalid_reagent_table"),
                (workbook_bytes(), "r.csv", REAGENT_TABLE_MEDIA_TYPE, None, 422, "invalid_reagent_table"),
                (workbook_bytes(), "r.xlsx", REAGENT_TABLE_MEDIA_TYPE, "protocol-" + "0" * 32, 404,
                 "protocol_catalog_not_found"),
            )
            for data, name, media_type, protocol_id, status, detail in cases:
                with self.subTest(name=name, media_type=media_type, status=status):
                    response = await self.attach(client, data, name, media_type, protocol_id)
                    self.assertEqual((response.status_code, response.json()["detail"]), (status, detail))
            with patch.object(server_module, "MAX_REAGENT_TABLE_BYTES", 100):
                large = await self.attach(client, workbook_bytes())
            self.assertEqual((large.status_code, large.json()["detail"]), (413, "reagent_table_too_large"))
            listed = (await client.get(f"/api/protocols/{self.protocol_id}/reagent-tables")).json()
        self.assertEqual(listed["reagent_tables"], [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
