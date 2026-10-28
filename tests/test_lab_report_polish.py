"""The experiment report, polished (lane RP2, decisions of 2026-10-06).

A person read a headspace report in Word and found long gaps between lines,
materials and equipment as bare names in one list, and nothing on a later
start or a return within a repeat. Built on lane RP's protocol double and
ledger helpers (tests/test_lab_report.py); provider calls are a fake client.
"""

from __future__ import annotations

import asyncio
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from docx import Document
from docx.shared import Pt

from tests.test_lab_report import GOOD_REPLY, _Client, _ReportCase, _step, protocol_double
from tests.test_lane_r7_rule_gaps import _report_events, _Turns
from voiney_lab import experiment_reports as er


class WordSpacingTests(_ReportCase):
    """Decision 1: tighter body, list and table spacing; room around headings."""

    def document(self):
        self.run_steps_one_to_four_then_stop()
        return Document(io.BytesIO(self.store.export_docx(self.report_id, fixture=self.fixture)))

    def test_the_decided_values(self) -> None:
        # Line spacing about 1.15, 3-4 pt after a paragraph, 0-2 pt between
        # list items; headings keep more room before them than after.
        self.assertEqual(er.DOCX_SPACING["p"], (0, 4, 1.15))
        self.assertEqual(er.DOCX_SPACING["list"], (0, 1, 1.15))
        self.assertEqual(er.DOCX_SPACING["table"], (0, 0, 1.0))
        self.assertEqual(er.DOCX_SPACING["note"], (2, 4, 1.15))
        self.assertEqual(er.DOCX_SPACING["h1"], (12, 4, 1.0))
        self.assertEqual(er.DOCX_SPACING["h2"], (8, 3, 1.0))
        self.assertEqual(er.DOCX_SPACING["title"], (0, 8, 1.0))

    def test_word_paragraphs_carry_them(self) -> None:
        document = self.document()
        normal = document.styles["Normal"].paragraph_format
        self.assertEqual((normal.space_after, normal.line_spacing), (Pt(4), 1.15))
        paragraphs = document.paragraphs
        texts = [p.text for p in paragraphs]

        def spacing(paragraph):
            form = paragraph.paragraph_format
            return form.space_before, form.space_after, form.line_spacing

        self.assertEqual(spacing(paragraphs[texts.index("실험 정보")]), (Pt(12), Pt(4), 1.0))
        self.assertEqual(spacing(paragraphs[texts.index("단계 시각표")]), (Pt(8), Pt(3), 1.0))
        note = paragraphs[next(index for index, value in enumerate(texts)
                              if value.startswith("쓴 재료·장비:"))]
        self.assertEqual(spacing(note), (Pt(2), Pt(4), 1.15))
        # The differences subsection is one list; its items are close, its
        # last item gets normal body spacing.
        start = texts.index("원문과 다르게 한 점·문제") + 1
        items = []
        while texts[start + len(items)].startswith("• "):
            items.append(paragraphs[start + len(items)])
        self.assertGreaterEqual(len(items), 2)
        self.assertEqual({spacing(p) for p in items[:-1]}, {(Pt(0), Pt(1), 1.15)})
        self.assertEqual(spacing(items[-1]), (Pt(0), Pt(4), 1.15))
        cells = {spacing(p) for table in document.tables for row in table.rows
                 for cell in row.cells for p in cell.paragraphs}
        self.assertEqual(cells, {(Pt(0), Pt(0), 1.0)})

    def test_no_paragraph_falls_back_to_the_templates_ten_points(self) -> None:
        document = self.document()
        paragraphs = list(document.paragraphs) + [
            p for table in document.tables for row in table.rows for cell in row.cells
            for p in cell.paragraphs]
        for paragraph in paragraphs:
            self.assertIsNotNone(paragraph.paragraph_format.space_after, paragraph.text)
            self.assertLessEqual(paragraph.paragraph_format.space_after, Pt(8), paragraph.text)


def items_double() -> SimpleNamespace:
    """Names as protocols list them, steps that call them by name or short name."""

    steps = (
        _step("i-step-01", "1", "Place the gel pieces in 200 µL 25mM AMBIC."),
        _step("i-step-02", "2", "Make solution A from 25mM ammonium bicarbonate (AMBIC) and acetonitrile."),
        _step("i-step-03", "3", "Add trypsin made up in AMBIC."),
        _step("i-step-04", "4", "Add formic acid (FA, 10% v/v) to reach 1%."),
        _step("i-step-05", "5", "Make 200 mL of LB broth."),
        _step("i-step-06", "6", "Pour the LB agar into the plates."),
        _step("i-step-07", "7", "Fill two tubes with autoclaved LB."),
        _step("i-step-08", "8", "Use a glass Pasteur pipette and a small ferrule."),
        _step("i-step-09", "9", "Dry in the modified heating oven, then in the modified heating block."),
        _step("i-step-10", "10", "Mix with a pipette."),
    )
    double = protocol_double()
    double.draft.protocol.sections = (SimpleNamespace(title_source_text="All", steps=steps),)
    double.draft.protocol.materials = tuple(SimpleNamespace(name_source_text=name) for name in (
        "Acetonitrile LC-MS grade B&J Brand VWR International (Avantor) Catalog #BJLC015-2.5",
        "Ammonium bicarbonate Merck MilliporeSigma (Sigma-Aldrich) Catalog #A6141",
        "Promega trypsin Promega Catalog #V5113",
        "Formic acid, LC-MS grade Thermo Fisher Scientific Catalog #28905",
        "Lysogeny Broth (LB) (Sigma-Aldrich)",
        "Lysogeny Agar (Sigma-Aldrich)",
        "Glass pipettes (10 mL)",
        "Glass Pasteur pipette (150 mm)",
    ))
    double.draft.protocol.equipment = tuple(SimpleNamespace(name_source_text=name) for name in (
        "Thermomixer C Model 5382\nNAME\nThermomixer C",
        "modified heating oven",
        "modified heating block",
        "Ferrules",
    ))
    double.timer_manifest = {}
    double.localized_fact = lambda step_id, fact_id: None
    return double


class MaterialsAndEquipmentTablesTests(_ReportCase):
    """Decisions 2, 3 and 5: two tables, name as listed and the steps that name it."""

    def setUp(self) -> None:
        super().setUp()
        self.fixture = items_double()
        self.event("00:30", "session_started", "1")
        self.event("00:36", "step_completed", "1")

    def facts(self) -> er.ReportFacts:
        return er.ReportWriterBrain.facts_for(self.doc(), fixture=self.fixture)

    def test_each_item_lists_the_source_steps_that_name_it(self) -> None:
        steps = {item.name: er._label_runs(item.steps) or "—" for item in self.facts().items}
        self.assertEqual(steps, {
            # The name before its company and grade words.
            "Acetonitrile LC-MS grade B&J Brand VWR International (Avantor) Catalog #BJLC015-2.5": "2",
            # The short name the source gives it, wherever it is used.
            "Ammonium bicarbonate Merck MilliporeSigma (Sigma-Aldrich) Catalog #A6141": "1–3",
            "Promega trypsin Promega Catalog #V5113": "3",
            "Formic acid, LC-MS grade Thermo Fisher Scientific Catalog #28905": "4",
            # "LB" is the broth's short name; "LB agar" is the agar.
            "Lysogeny Broth (LB) (Sigma-Aldrich)": "5, 7",
            "Lysogeny Agar (Sigma-Aldrich)": "6",
            # Not "pipette" alone: other listed items are pipettes too.
            "Glass pipettes (10 mL)": "—",
            "Glass Pasteur pipette (150 mm)": "8",
            # Shown without the list's NAME label (lane FX, decision 4).
            "Thermomixer C Model 5382": "—",
            "modified heating oven": "9",
            "modified heating block": "9",
            "Ferrules": "8",
        })

    def test_the_items_used_are_one_line_without_the_lists_labels(self) -> None:
        # Lane RP3, decision ①: the materials and equipment tables are gone;
        # the notebook names the items the performed steps' text names.
        text = self.store.export_markdown(self.report_id, fixture=self.fixture).decode()
        self.assertIn("> 쓴 재료·장비: Ammonium bicarbonate", text)
        self.assertIn("| Ammonium bicarbonate | 재료 | Merck MilliporeSigma (Sigma-Aldrich) | A6141 |", text)
        self.assertNotIn("이름 (원문 그대로)", text)
        self.assertNotIn("Catalog #", text)
        self.assertNotIn("NAME", text)
        document = Document(io.BytesIO(self.store.export_docx(self.report_id, fixture=self.fixture)))
        self.assertIn("쓴 재료·장비: Ammonium bicarbonate", [p.text for p in document.paragraphs])

    def test_the_steps_column_is_narrow(self) -> None:
        name, steps = er.TABLE_WIDTHS[er.ITEM_HEADER]
        self.assertGreater(name, steps)
        self.assertAlmostEqual(name + steps, 1.0)

    def test_a_protocol_without_a_list_says_so(self) -> None:
        self.fixture.draft.protocol.materials = ()
        self.fixture.draft.protocol.equipment = ()
        text = self.store.export_markdown(self.report_id, fixture=self.fixture).decode()
        # Lane RP3, decision ①: without a list there is no line of items.
        self.assertNotIn("쓴 재료·장비", text)
        self.assertNotIn("이름 (원문 그대로)", text)


def _skip_record(start: str, skipped: list[str]) -> dict:
    return {"step_record": {"kind": "start_at_step", "start_step": start, "skipped_step_labels": skipped,
                            "skipped_step_ids": [f"p-step-0{label}" for label in skipped],
                            "experiment_started": True}}


def _return_record(from_step: str, to_step: str, round_: int) -> dict:
    return {"step_record": {"kind": "repeat_return", "repetition_id": "repeat-2-4",
                            "repeated_step_labels": ["2", "4"], "stated_at_step": from_step,
                            "from_step": from_step, "to_step": to_step, "returns_confirmed": round_ - 1,
                            "round": round_, "round_counted_from": "confirmed_returns"}}


def _round_completion(round_: int) -> dict:
    return {"step_record": {"kind": "repeat_round_completion", "repetition_id": "repeat-2-4",
                            "round": round_, "round_counted_from": "confirmed_returns",
                            "completed_before": True}}


class LaterStartAndReturnTests(_ReportCase):
    """Decision 6: lane R7's steps_skipped and repeat_returned, read as recorded."""

    def markdown(self) -> str:
        return self.store.export_markdown(self.report_id, fixture=self.fixture).decode()

    def deviations(self, text: str) -> list[str]:
        marker = "### 원문과 다르게 한 점·문제"
        if marker not in text:
            return []
        block = text.split(marker, 1)[1].split("## 관찰·특이사항", 1)[0]
        return [line[2:] for line in block.splitlines() if line.startswith("- ")]

    def test_a_later_start_is_written_from_its_event(self) -> None:
        self.event("00:30", "session_started", "3")
        self.event("00:30", "steps_skipped", "3", payload=_skip_record("3", ["1", "2"]))
        self.event("00:41", "step_completed", "3")
        deviations = self.deviations(self.markdown())
        # Never asked about, so not confirmed (lane RP2, decision 3, kept).
        self.assertEqual(deviations[0], "3단계부터 시작 — 1–2단계 건너뜀 [미확인]")
        self.assertFalse(any("이 기록에 없다" in line for line in deviations))

    def test_without_the_event_the_first_step_recorded_still_says_so(self) -> None:
        self.event("00:30", "session_started", "3")
        self.event("00:41", "step_completed", "3")
        self.assertEqual(self.deviations(self.markdown())[0], "1–2단계는 이 기록에 없다 (3단계부터 기록됨)")

    def run_with_a_return(self) -> None:
        self.event("00:30", "session_started", "1")
        self.event("00:36", "step_completed", "1")
        self.event("00:40", "step_completed", "2")
        self.event("00:41", "timer_started", "3", payload={"timer": {"duration_seconds": 900}})
        self.event("00:56", "step_completed", "3")
        self.event("00:58", "step_completed", "4")
        self.event("01:00", "repeat_returned", "2", payload=_return_record("4", "2", 2))
        self.event("01:05", "step_completed", "2", payload=_round_completion(2))
        self.event("01:06", "timer_started", "3", payload={"timer": {"duration_seconds": 900}})
        self.event("01:16", "step_completed", "3", payload=_round_completion(2))

    def test_a_return_is_in_the_step_table_and_the_deviations_with_its_round(self) -> None:
        self.run_with_a_return()
        text = self.markdown()
        # Lane RP3, decision ①: a return the source states is a repeat row of
        # the conditions, not a point done differently; the step times in
        # the appendix count the step twice.
        self.assertIn("| 2–4단계 반복 | 2회차까지 함 (말로 확인한 돌아가기 기준) |", text)
        self.assertNotIn("돌아갔다", "\n".join(self.deviations(text)))
        self.assertIn("| 2 (2회) | 09:36 | 10:05 | 9분 | — |", text)
        document = Document(io.BytesIO(self.store.export_docx(self.report_id, fixture=self.fixture)))
        cells = [[cell.text for cell in row.cells] for table in document.tables for row in table.rows]
        self.assertIn(["2–4단계 반복", "2회차까지 함 (말로 확인한 돌아가기 기준)"], cells)

    def test_each_round_times_its_own_timer(self) -> None:
        # The second round's timer started after the first completion: the
        # first round is not timed against it (it read "실제 0분").
        self.run_with_a_return()
        # Lane RP3, decision ①: the appendix's step times carry the timer words.
        steps = self.markdown().split("### 단계 시각표")[1].split("### 말한 그대로의 기록")[0]
        row = next(line for line in steps.splitlines() if line.startswith("| 3 (2회) | "))
        self.assertTrue(row.endswith("| 원문 15분 / 실제 1회차 15분, 2회차 10분 |"), row)

    def test_the_model_reads_the_start_and_the_return_as_recorded(self) -> None:
        self.run_with_a_return()
        client = _Client(GOOD_REPLY)
        brain = er.ReportWriterBrain(client=client, model="fake-model", timeout_seconds=5)
        doc = self.doc()
        asyncio.run(brain.generate_narrative(doc, list(doc["events"]), fixture=self.fixture))
        sent = json.loads(client.calls[0]["messages"][1]["content"])
        # Lane RP3, decision ③: the model reads the conditions used -- the
        # return with its round and its basis among them -- not a step table.
        self.assertIn("2–4단계 반복 — 2회차까지 함 (말로 확인한 돌아가기 기준)", sent["실제로 한 조건"])
        self.assertNotIn("수행한 단계", sent)


class RulesToReportTests(_Turns, unittest.TestCase):
    """The production path: the rules move the run, the server records it, the report reads it."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.listener = SimpleNamespace(
            session_id="lane-rp2-session",
            experiment_report_store=er.ExperimentReportStore(Path(tmp.name) / "r.sqlite"),
            experiment_report_id=None,
        )

    def record(self, *said: str) -> None:
        for text in said:
            before = self.session.current_index
            plan = self.say(text)
            if plan.state_changed:
                _report_events(self.session, plan, before, turn_id=self.turn_id, listener=self.listener)

    def markdown(self) -> str:
        return self.listener.experiment_report_store.export_markdown(
            self.listener.experiment_report_id, fixture=self.session.fixture).decode()

    def test_a_confirmed_return_reaches_the_report_with_its_round(self) -> None:
        self.open(None)
        self.record("프로토콜 시작해줘", "1단계 완료했어", "2단계 완료했어", "3단계 완료했어",
                    "4단계 완료했어", "2단계로 돌아가", "응", "2단계 완료했어")
        text = self.markdown()
        self.assertRegex(text, r"\| \d+–\d+단계 반복 \([^|]*\) \| 2회차까지 함 \(말로 확인한 돌아가기 기준\) \|")
        self.assertNotIn("돌아갔다", text)
        self.assertRegex(text, r"\| 2 \(2회\) \| \d\d:\d\d \| \d\d:\d\d \| [^|]+ \| ")

    def test_a_confirmed_later_start_reaches_the_report_as_recorded(self) -> None:
        self.open(None)
        self.record("4단계부터 시작해줘", "응", "4단계 완료했어")
        text = self.markdown()
        self.assertIn("- 4단계부터 시작 — 1–3단계 건너뜀", text)
        self.assertNotIn("이 기록에 없다", text)
