"""NOTE1: a notebook draft grounded only in the protocol and run ledger."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from tests.lane_rp3_support import (
    CELL_PURPOSE,
    RecordWriter,
    _double,
    _start,
    cell_protocol,
    cell_run,
    coating_protocol,
    coating_run,
    environment,
    temporary,
)
from voiney_lab import experiment_reports as er
from voiney_lab.report_projection import project_protocol_for_report


def battery_protocol() -> SimpleNamespace:
    return _double(
        "Coin-cell cathode cycling",
        (("Assembly", (
            ("1", "Use cathode sample B-17 with electrode loading 8.2 mg/cm2.", 2, ()),
            ("2", "Add 80 µL electrolyte and assemble CR2032 cell B-17 in a glovebox at H2O 0.2 ppm and O2 0.3 ppm.", 2, ()),
            ("3", "Crimp cell B-17 at 1000 psi.", 3, ()),
            ("4", "Cycle cell B-17 at 0.1 C between 2.5 V and 4.2 V.", 3, ()),
        )),),
        {},
        ("Cathode sample B-17", "Electrolyte"),
        ("Glovebox", "CR2032 crimper", "Cycler"),
        {},
        "Kim 2026. Coin-cell cathode cycling.",
        "https://dx.doi.org/10.1/battery.v1",
    )


def battery_run(directory: str) -> RecordWriter:
    record = RecordWriter(
        directory, session="session-note1-battery", title="Coin-cell cathode cycling",
        protocol_id="protocol-note1-battery",
    )
    _start(record, "최전지", notices=1)
    record.event("00:30", "session_started", "1")
    record.event("00:30", "purpose_recorded", "1", user_wording="B-17 셀의 첫 사이클 기록")
    record.event("00:40", "observation", "1", user_wording="B-17 전극 로딩 8.1 mg/cm2", category="measurement")
    record.event("00:42", "step_completed", "1")
    record.event("00:48", "observation", "2", user_wording="B-17 전해액 80 µL", category="measurement")
    record.event("00:50", "step_completed", "2")
    record.event("00:55", "step_completed", "3")
    record.event("01:00", "step_completed", "4")
    record.event("01:00", "workflow_completed", "4")
    record.event("01:01", "result_recorded", "4", user_wording="결과는 아직 입력하지 않음", payload={"result": "", "next_change": ""})
    record.finalize("01:01", "completed")
    return record


class NotebookDraftTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = temporary()
        self.env = environment()
        self.env.start()
        er._NARRATIVE_CACHE.clear()

    def tearDown(self) -> None:
        self.env.stop()
        self.temporary.cleanup()
        er._NARRATIVE_CACHE.clear()

    @staticmethod
    def book(record: RecordWriter, fixture: SimpleNamespace) -> er.LabNotebook:
        narrative = record.store._narrative(
            record.report_id, None, fixture, start_check=None, previous=None,
        )
        assert narrative.facts is not None and narrative.facts.notebook is not None
        return narrative.facts.notebook

    def test_common_sections_are_the_handoff_draft_and_blank_when_not_said(self) -> None:
        record = cell_run(self.temporary.name)
        book = self.book(record, cell_protocol())
        self.assertEqual(er.NOTEBOOK_SECTIONS, (
            "실험 정보", "목적", "방법", "관찰·특이사항", "결과", "해석·다음 할 일", "분야별 기록", "부록",
        ))
        self.assertEqual(book.purpose_words, CELL_PURPOSE)
        self.assertTrue(book.method_rows)
        self.assertEqual(book.method_rows[0].source_text, "Seed A549 cells at 5000 cells per well in a 96-well plate in 100 µL medium.")
        self.assertEqual(book.interpretation_words, "")
        markdown = record.store.export_markdown(
            record.report_id, fixture=cell_protocol(), start_check=None, previous=None,
        ).decode()
        self.assertIn("## 목적", markdown)
        self.assertIn("## 방법", markdown)
        self.assertIn("## 해석·다음 할 일", markdown)
        self.assertNotIn("AI 정리", markdown)

    def test_three_source_selected_domains_have_only_source_or_record_evidence(self) -> None:
        cases = (
            (cell_run(self.temporary.name, session="session-note1-bio"), cell_protocol(), "biology", "세포주"),
            (coating_run(self.temporary.name), coating_protocol(), "chemistry", "시약(몰·질량·당량)"),
            (battery_run(self.temporary.name), battery_protocol(), "battery_materials", "전극 로딩"),
        )
        for record, fixture, code, field_name in cases:
            with self.subTest(code=code):
                book = self.book(record, fixture)
                self.assertEqual(book.domain.code, code)
                self.assertEqual(book.domain.selected_by, "source_analysis")
                self.assertIn(field_name, [field.label for field in book.domain.fields])
                self.assertEqual(
                    er.audit_notebook_number_provenance(
                        book,
                        source_steps=er._source_steps_from_fixture(fixture),
                        events=record.doc()["events"],
                    ),
                    (),
                )

    def test_domain_visual_data_is_deterministic_and_uses_recorded_locations(self) -> None:
        cell_record = cell_run(self.temporary.name, session="session-note1-layout")
        cell_record.event("03:58", "observation", "6", user_wording="3번 well 탁함", category="appearance")
        cell = self.book(cell_record, cell_protocol())
        self.assertTrue(cell.timeline)
        self.assertTrue(any(item.kind == "observation" for item in cell.timeline))
        self.assertTrue(any(position.label == "3번 well" for position in cell.plate_positions))
        battery = self.book(battery_run(self.temporary.name), battery_protocol())
        self.assertTrue(any(row.identifier == "B-17" for row in battery.sample_rows))
        self.assertTrue(any(bar.source and bar.actual for bar in battery.value_bars))

    def test_protocol_projection_never_invents_an_objective(self) -> None:
        protocol = SimpleNamespace(
            protocol_id="p-no-purpose", metadata=SimpleNamespace(title="Protocol 12"),
            description=None, materials=(), equipment=(), before_start=(), sections=(),
        )
        self.assertEqual(project_protocol_for_report(protocol).objective, "")

    def test_methods_inventory_and_person_domain_override_are_append_only_inputs(self) -> None:
        record = cell_run(self.temporary.name, session="session-note1-methods")
        before = len(record.doc()["events"])
        record.event("03:58", "notebook_inventory_recorded", None, payload={
            "item_kind": "재료", "item_name": "MTT reagent Sigma-Aldrich Catalog #M5655",
            "vendor": "Sigma-Aldrich", "catalog_number": "M5655", "lot_number": "L24-09",
            "stage": "after",
        })
        record.event("03:59", "notebook_domain_selected", None, payload={"domain": "chemistry"})
        record.event(
            "04:00", "observation", "6",
            user_wording="MTT reagent 구매처 Sigma-Aldrich, 카탈로그 번호 M5655, Lot 번호 VOICE-L25",
            category="note",
        )
        book = self.book(record, cell_protocol())
        self.assertEqual(len(record.doc()["events"]), before + 3)
        self.assertEqual(book.domain.code, "chemistry")
        self.assertEqual(book.domain.selected_by, "person")
        mtt = next(item for item in book.inventory if item.name.startswith("MTT reagent"))
        self.assertEqual(
            (mtt.vendor, mtt.catalog_number, mtt.lot_number, mtt.stage),
            ("Sigma-Aldrich", "M5655", "VOICE-L25", "after"),
        )
        markdown = record.store.export_markdown(
            record.report_id, fixture=cell_protocol(), start_check=None, previous=None,
        ).decode()
        self.assertIn("| MTT reagent Sigma-Aldrich Catalog #M5655 | 재료 | Sigma-Aldrich | M5655 | VOICE-L25 | 실험 후 |", markdown)


if __name__ == "__main__":
    unittest.main()
