"""NOTE1: a notebook draft grounded only in the protocol and run ledger."""

from __future__ import annotations

import unittest
import base64
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

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

    def test_stopped_run_does_not_fill_the_persons_next_task_from_the_source(self) -> None:
        record = coating_run(self.temporary.name)
        book = self.book(record, coating_protocol())
        self.assertEqual(book.next_change, "")
        self.assertEqual(book.next_lines, ())

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
                        source_inventory=tuple(
                            item.name_source_text
                            for item in (*fixture.draft.protocol.materials,
                                         *fixture.draft.protocol.equipment)
                        ),
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

    def test_numbered_korean_cell_identifier_is_grouped_in_the_sample_table(self) -> None:
        record = battery_run(self.temporary.name)
        record.event(
            "01:02", "observation", "4",
            user_wording="2번 셀 질량 12.3 mg", category="measurement",
        )
        book = self.book(record, battery_protocol())
        row = next(item for item in book.sample_rows if item.identifier == "2번 셀")
        self.assertEqual(row.records, ("2번 셀 질량 12.3 mg",))

    def test_value_bar_keeps_both_units_for_equivalent_scale(self) -> None:
        values = (
            er.NotebookValue(
                label="1", kind="양", family="volume", written="1000 µL", actual="1 mL",
            ),
        )
        _timeline, bars, _positions, _samples = er._notebook_visual_facts(
            SimpleNamespace(zone=ZoneInfo("UTC")), (), values,
        )
        self.assertEqual(
            (bars[0].source_number, bars[0].source_unit, bars[0].actual_number, bars[0].actual_unit),
            ("1000", "µL", "1", "mL"),
        )

    def test_zero_value_draws_no_positive_bar(self) -> None:
        row = er.NotebookValueBar(
            label="1단계 · 양", kind="양", source="0 mL", actual="1 mL",
            source_number="0", actual_number="1", unit="mL",
            source_unit="mL", actual_unit="mL", source_key="record-zero",
        )
        source, actual = er._notebook_bar_lines(row)
        self.assertNotIn("█", source)
        self.assertIn("█", actual)

    def test_number_audit_rejects_a_small_number_absent_from_source_and_record(self) -> None:
        record = cell_run(self.temporary.name, session="session-note1-number-audit")
        fixture = cell_protocol()
        book = self.book(record, fixture)
        source_steps = er._source_steps_from_fixture(fixture)
        events = record.doc()["events"]
        raw_numbers = set(er._NUMBER.findall(json.dumps(
            {"steps": source_steps, "events": events}, ensure_ascii=False, default=str,
        )))
        candidate = "31"
        self.assertNotIn(candidate, raw_numbers)
        self.assertLess(int(candidate), len(events) + len(source_steps) + 2)
        tampered = replace(book, interpretation_words=f"근거 없이 넣은 값 {candidate}")
        problems = er.audit_notebook_number_provenance(
            tampered,
            source_steps=source_steps,
            source_inventory=tuple(
                item.name_source_text
                for item in (*fixture.draft.protocol.materials, *fixture.draft.protocol.equipment)
            ),
            events=events,
        )
        self.assertIn(f"notebook.interpretation_words: {candidate}", problems)

    def test_number_audit_does_not_trust_an_unconfirmed_result_event(self) -> None:
        record = cell_run(self.temporary.name, session="session-note1-unconfirmed-number")
        fixture = cell_protocol()
        book = self.book(record, fixture)
        source_steps = er._source_steps_from_fixture(fixture)
        events = [
            *record.doc()["events"],
            {
                "event_key": "unsafe-result",
                "event_type": "result_recorded",
                "user_wording": "1.7 100",
                "confirmation_state": None,
                "payload": {"result": "1.7 100"},
            },
        ]
        tampered = replace(book, interpretation_words="기록된 결과는 1.7 과 100")
        problems = er.audit_notebook_number_provenance(
            tampered, source_steps=source_steps, events=events,
        )
        self.assertIn("notebook.interpretation_words: 1.7", problems)

        events[-1]["confirmation_state"] = "read_back"
        self.assertNotIn(
            "notebook.interpretation_words: 1.7",
            er.audit_notebook_number_provenance(
                tampered, source_steps=source_steps, events=events,
            ),
        )

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
        self.assertIn("| MTT reagent | 재료 | Sigma-Aldrich | M5655 | VOICE-L25 | 실험 후 |", markdown)
        coating = self.book(coating_run(self.temporary.name), coating_protocol())
        teos = next(item for item in coating.inventory if item.name.startswith("TEOS"))
        self.assertEqual((teos.vendor, teos.catalog_number), ("", ""))

    def test_yield_is_calculated_only_from_person_recorded_raw_values_with_formula(self) -> None:
        record = coating_run(self.temporary.name)
        key = record.event(
            "11:26", "observation", "6",
            user_wording="실제 수득량 2 mg, 이론 수득량 3 mg",
            category="measurement",
        )
        book = self.book(record, coating_protocol())
        field = next(item for item in book.domain.fields if item.label == "수득량·수율")
        self.assertEqual(field.origin, "calculated")
        self.assertEqual(field.value, "실제 2 mg · 이론 3 mg · 수율 66.67%")
        self.assertEqual(field.formula, "2 mg ÷ 3 mg × 100 = 66.67%")
        self.assertEqual(field.source_keys, (key,))
        markdown = record.store.export_markdown(
            record.report_id, fixture=coating_protocol(), start_check=None, previous=None,
        ).decode()
        self.assertIn("계산식: 2 mg ÷ 3 mg × 100 = 66.67%", markdown)
        self.assertEqual(
            er.audit_notebook_number_provenance(
                book,
                source_steps=er._source_steps_from_fixture(coating_protocol()),
                source_inventory=tuple(
                    item.name_source_text
                    for item in (*coating_protocol().draft.protocol.materials,
                                 *coating_protocol().draft.protocol.equipment)
                ),
                events=record.doc()["events"],
            ),
            (),
        )

    def test_record_photos_source_figures_and_bars_stay_bound_to_their_step(self) -> None:
        from docx import Document
        import io

        record = cell_run(self.temporary.name, session="session-note1-images")
        record.event(
            "03:58", "photo_attached", "6", user_wording="6단계 판독 사진",
            payload={
                "evidence_id": "evidence-photo-1", "media_type": "image/png",
                "interpretation_status": "not_interpreted",
            },
        )
        fixture = cell_protocol()
        fixture.protocol_id = "protocol-rp3-cell"
        fixture.revision_id = "rev-rp3-1"
        fixture.source_pdf_sha256 = "a" * 64
        fixture.steps = tuple(step for section in fixture.draft.protocol.sections for step in section.steps)
        figure = SimpleNamespace(
            asset_id="source-crop-2-1", source_document_id="a" * 64,
            source_page=2, normalized_bounding_box=(0.1, 0.2, 0.3, 0.4),
            caption_primary="원문 plate 배치 그림", caption_source="Figure 1. Plate layout.",
            mime_type="image/png", sha256="b" * 64,
        )
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )

        fixture.visual_for_step = lambda index: figure if index == 0 else None
        fixture.visual_content = lambda index: (figure, png)

        book = self.book(record, fixture)
        self.assertEqual(
            [(item.kind, item.step_label) for item in book.images if item.asset_id],
            [("photo", "6"), ("source_figure", "1")],
        )
        recorded_photo = next(item for item in book.images if item.asset_id == "evidence-photo-1")
        self.assertEqual(
            recorded_photo.url,
            "/api/workspace/experiments/session-note1-images/evidence/evidence-photo-1",
        )
        markdown = record.store.export_markdown(
            record.report_id, fixture=fixture, start_check=None, previous=None,
        ).decode()
        self.assertIn("![6단계 · 6단계 판독 사진]", markdown)
        self.assertIn("원문 plate 배치 그림", markdown)
        self.assertIn("█", markdown)
        docx = record.store.export_docx(
            record.report_id, fixture=fixture, start_check=None, previous=None,
            image_resolver=lambda image: (png, "image/png") if image.asset_id else None,
        )
        document = Document(io.BytesIO(docx))
        self.assertEqual(len(document.inline_shapes), 2)

    def test_source_figures_are_not_attached_before_any_step_is_completed(self) -> None:
        from voiney_lab.source_figures import SourceFigure

        record = RecordWriter(
            self.temporary.name, session="session-note1-no-completed-step",
            title="Cell viability assay after drug treatment",
            protocol_id="protocol-rp3-cell",
        )
        _start(record, "이실험")
        record.event("00:30", "session_started", "1")
        record.finalize("00:31", "stopped")
        fixture = cell_protocol()
        fixture.steps = tuple(
            step for section in fixture.draft.protocol.sections for step in section.steps
        )
        figure = SourceFigure(
            figure_id="source-figure-unperformed", source_page=1,
            bounding_box=(10.0, 20.0, 210.0, 160.0), caption="아직 하지 않은 단계 그림",
            mime_type="image/png", sha256="b" * 64, byte_size=68,
            width_px=200, height_px=140,
        )
        with mock.patch(
            "voiney_lab.source_figures.figures_for_step", return_value=(figure,),
        ):
            book = self.book(record, fixture)
        self.assertFalse(any(image.kind == "source_figure" for image in book.images))


if __name__ == "__main__":
    unittest.main()
