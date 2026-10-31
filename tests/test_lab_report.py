"""The experiment report a researcher reads (lane RP, decisions 2, 3 and 5).

The report is built from a small protocol double (no licensed PDF needed) and
a ledger written through the store, with times a person would take. Provider
calls are a fake client only.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from voiney_lab import experiment_reports as er


def _statement(text: str) -> SimpleNamespace:
    return SimpleNamespace(source_text=text)


def _step(step_id: str, label: str, text: str, expected: tuple[str, ...] = ()) -> SimpleNamespace:
    return SimpleNamespace(
        step_id=step_id, source_label=label, instruction_source_text=text,
        expected_results=tuple(_statement(item) for item in expected), sub_actions=(),
    )


def protocol_double() -> SimpleNamespace:
    """Five steps in two sections, Korean for steps 1-4, a 15-minute timer on 3."""

    sections = (
        SimpleNamespace(title_source_text="Gel band destaining 30m", steps=(
            _step("p-step-01", "1", "Cut the band into 1 mm pieces."),
            _step("p-step-02", "2", "Prepare solution A."),
            _step("p-step-03", "3", "Wash with 500 µL solution A at 37°C for 15 min."),
        )),
        SimpleNamespace(title_source_text="Dehydration 15m", steps=(
            _step("p-step-04", "4", "Remove the solution.",
                  expected=("The gel should look white.",)),
            _step("p-step-05", "5", "Dry the gel."),
        )),
    )
    korean = {
        "p-step-01": "1단계: 밴드를 1 mm 조각으로 자릅니다.",
        "p-step-02": "2단계: Solution A를 준비합니다.",
        "p-step-03": "3단계: Solution A 500 µL로 37°C에서 15 min 동안 세척합니다.",
        "p-step-04": "4단계: 용액을 제거합니다.",
    }
    page = ("Test digestion protocol\nDOI: https://dx.doi.org/10.1/test.v1\n"
            "Protocol Citation: Kim 2025. Test digestion protocol. protocols.io\n"
            "Keywords: gel digestion, protein identification\n")
    protocol = SimpleNamespace(
        sections=sections,
        materials=(SimpleNamespace(name_source_text="Acetonitrile"),),
        equipment=(SimpleNamespace(name_source_text="Thermomixer"),),
        metadata=SimpleNamespace(pdf=SimpleNamespace(pages=(SimpleNamespace(text=page),))),
    )
    return SimpleNamespace(
        draft=SimpleNamespace(protocol=protocol),
        revision_id="rev-test-1",
        timer_manifest={"p-step-03": 900},
        localized_fact=lambda step_id, fact_id: korean.get(step_id) if fact_id == "current_step" else None,
    )


REPORT_ID_FIELDS = ("session-lab-1", "protocol-lab", "rev-test-1", "9" * 64)


class _ReportCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = er.ExperimentReportStore(Path(self.temporary.name) / "r.sqlite")
        self.fixture = protocol_double()
        self.clock = "2026-10-06T00:30:00+00:00"
        self.patch = mock.patch.object(er, "_now", lambda: self.clock)
        self.patch.start()
        self.env = mock.patch.dict(os.environ, {"VOINEY_LAB_REPORT_TIMEZONE": "Asia/Seoul"})
        self.env.start()
        er._NARRATIVE_CACHE.clear()
        report = self.store.open_report(
            session_id="session-lab-1", protocol_id="protocol-lab",
            protocol_title="Test digestion protocol", protocol_revision="rev-test-1",
            protocol_sha256="9" * 64, readiness_status="analysis_required",
            development_only=True,
        )
        self.report_id = report["report_id"]
        self.turn = 0

    def tearDown(self) -> None:
        self.patch.stop()
        self.env.stop()
        er._NARRATIVE_CACHE.clear()
        self.temporary.cleanup()

    def at(self, clock: str) -> None:
        self.clock = f"2026-10-06T{clock}:00+00:00"

    def event(self, clock: str, event_type: str, label: str | None, **fields) -> None:
        self.at(clock)
        self.turn += 1
        self.store.append_event(
            self.report_id, event_key=f"turn-{self.turn}", event_type=event_type,
            step_id=f"p-step-0{label}" if label else None, step_label=label, **fields,
        )

    def run_steps_one_to_four_then_stop(self) -> None:
        self.event("00:30", "session_started", "1")
        self.event("00:36", "step_completed", "1")
        self.event("00:40", "step_completed", "2")
        self.event("00:41", "timer_started", "3", payload={"timer": {"duration_seconds": 900}})
        self.event("00:50", "workflow_paused", "3")
        self.event("00:55", "workflow_resumed", "3")
        self.event("01:06", "step_completed", "3", payload={"timer": {
            "completion_state": "step_exited_before_timer_elapsed",
            "step_exited_before_timer_elapsed": True, "elapsed_seconds": 600,
            "source_duration_seconds": 900}})
        self.event("01:08", "observation", "4", user_wording="밴드가 투명해졌어",
                   category="color", confirmation_state="user_reported")
        self.event("01:09", "anomaly", "4", user_wording="튜브를 쏟았어",
                   category="spill_exposure_safety_event", severity="unknown",
                   confirmation_state="user_reported")
        self.event("01:12", "step_completed", "4")
        self.event("01:15", "session_stopped", "5", payload={"stop_reason": "stopped_by_user"})
        self.at("01:15")
        self.store.finalize(self.report_id, status="stopped", event_key="turn-final")

    def doc(self) -> dict:
        return self.store.get_report(self.report_id)


class ReportStructureTests(_ReportCase):
    def test_markdown_follows_the_report_order_without_identifiers(self) -> None:
        # Decision 6 (2026-10-06): no appendix and no event list in the
        # researcher's report; the ledger and the JSON export keep them.
        self.run_steps_one_to_four_then_stop()
        text = self.store.export_markdown(self.report_id, fixture=self.fixture).decode()
        headings = [line for line in text.splitlines() if line.startswith("#")]
        self.assertEqual(headings[:2], ["# Test digestion protocol", "## 실험 정보"])
        self.assertEqual([h for h in headings if h.startswith("## ")], list(er.NOTEBOOK_HEADINGS))
        for identifier in (self.report_id, *REPORT_ID_FIELDS, "stopped", "step_completed"):
            self.assertNotIn(identifier, text)
        exported = self.store.export_json(self.report_id).decode()
        for identifier in (self.report_id, "rev-test-1", "9" * 64, "step_completed"):
            self.assertIn(identifier, exported)

    def test_run_information_is_in_the_researchers_time_zone_and_plain_words(self) -> None:
        self.run_steps_one_to_four_then_stop()
        text = self.store.export_markdown(self.report_id, fixture=self.fixture).decode()
        for row in ("| 날짜·시간 | 2026년 10월 6일 (화) 09:30 – 10:15 · 45분 |",
                    "| 진행 | 중단 — 5단계에서 · 4 / 5단계 |",
                    "| 실험자 | (직접 적어 주세요) |"):
            self.assertIn(row, text)
        # Lane DI (2026-10-08): the run table names the protocol's state as
        # "분석 통과 · 실험자가 시작함"; a curated development fixture adds
        # what it is, without approval or test-protocol wording.
        self.assertIn("| 프로토콜 상태 | 분석 통과 · 실험자가 시작함", text)
        self.assertIn("개발용 큐레이션 분석(원문 PDF 기준)", text)
        self.assertNotIn("승인", text)
        with mock.patch.dict(os.environ, {"VOINEY_LAB_REPORT_TIMEZONE": "UTC"}):
            utc = self.store.export_markdown(self.report_id, fixture=self.fixture).decode()
        self.assertIn("| 날짜·시간 | 2026년 10월 6일 (화) 00:30 – 01:15 · 45분 |", utc)

    def test_methods_results_and_deviations_come_from_the_record(self) -> None:
        self.run_steps_one_to_four_then_stop()
        text = self.store.export_markdown(self.report_id, fixture=self.fixture).decode()
        # Lane NOTE2: keep the value comparison and also show the checked
        # Korean method first, followed by the exact source wording.
        self.assertIn("| 3단계 · Gel band destaining | 시간 15 min → 10분에 끝냄 (타이머 일찍 끝냄) [미확인] · 그 밖 원문대로 (500 µL · 37°C) |", text)
        self.assertIn("Solution A 500 µL로 37°C에서 15 min 동안 세척합니다.", text)
        self.assertIn("<small>원문: Wash with 500 µL solution A at 37°C for 15 min.</small>", text)
        self.assertIn("| 4단계 · Dehydration | — | 밴드가 투명해졌어 | 10:08 |", text)
        self.assertIn("- 3단계 타이머: 원문 15분 중 10분에 끝냄 [미확인]", text)
        # Never asked about, so not confirmed (lane RP2, decision 3, kept).
        self.assertIn("- 이상 · 4단계 (10:09): “튜브를 쏟았어” [미확인]", text)
        self.assertIn("- 5단계에서 끝냄 — 5단계는 하지 않음", text)
        # The pause is in the appendix's step times, not in the body.
        self.assertIn("| 3 | 09:40 | 10:06 | 26분 (멈춤 5분 포함) | 원문 15분 / 10분에 끝냄 |", text)
        self.assertIn("> 출처: [1] Kim 2025. Test digestion protocol. protocols.io https://dx.doi.org/10.1/test.v1 — 실험에 쓴 프로토콜 원문", text)

    def test_a_record_without_observations_marks_the_section_as_unrecorded(self) -> None:
        self.event("00:30", "session_started", "1")
        self.event("00:36", "step_completed", "1")
        text = self.store.export_markdown(self.report_id, fixture=self.fixture).decode()
        section = text.split("## 관찰·특이사항", 1)[1].split("## 결과", 1)[0]
        self.assertEqual(section.strip(), "> 기록 없음")

    def test_docx_has_the_same_sections_and_tables(self) -> None:
        from docx import Document

        self.run_steps_one_to_four_then_stop()
        document = Document(io.BytesIO(self.store.export_docx(self.report_id, fixture=self.fixture)))
        paragraphs = [p.text for p in document.paragraphs]
        for heading in ("Test digestion protocol", *er.NOTEBOOK_SECTIONS):
            self.assertIn(heading, paragraphs)
        cells = [[c.text for c in row.cells] for table in document.tables for row in table.rows]
        self.assertIn(["날짜·시간", "2026년 10월 6일 (화) 09:30 – 10:15 · 45분"], cells)
        self.assertIn(["4단계 · Dehydration", "—", "밴드가 투명해졌어", "10:08"], cells)
        self.assertIn("• 이상 · 4단계 (10:09): “튜브를 쏟았어” [미확인]", paragraphs)

    def test_an_unreadable_protocol_is_said_not_papered_over(self) -> None:
        self.event("00:30", "session_started", "1")
        self.event("00:36", "step_completed", "1")
        with mock.patch.object(er, "report_protocol_fixture", return_value=(None, "프로토콜 원문을 불러오지 못했다")):
            text = self.store.export_markdown(self.report_id).decode()
        self.assertNotIn("instruction.", text)
        self.assertIn("> 프로토콜 원문을 불러오지 못해 원문 값을 적지 못했다.", text)

    def test_a_fixture_of_another_revision_is_not_used(self) -> None:
        other = protocol_double()
        other.revision_id = "rev-other"
        with mock.patch("voiney_lab.server.server_config", return_value=None), \
                mock.patch("voiney_lab.server._configured_candidate_fixture", return_value=other):
            other.protocol_id = "protocol-lab"
            fixture, why = er.report_protocol_fixture(self.doc())
        self.assertIsNone(fixture)
        self.assertIn("버전", why)


class _Client:
    def __init__(self, reply: dict | str) -> None:
        self.calls: list[dict] = []
        self.reply = reply

        async def create(**kwargs):
            self.calls.append(kwargs)
            content = self.reply if isinstance(self.reply, str) else json.dumps(self.reply, ensure_ascii=False)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


#: Lane RP3, decision ③: the model's reply is the observation notes and one
#: result paragraph; every number in them is in the record or the source.
GOOD_REPLY = {
    "observation_notes": [{"단계": "4", "시료": "", "문장": "4단계에서 밴드가 투명해졌다."}],
    "result_summary": "3단계는 원문 15분 중 10분에 끝냈고, 4단계에서 튜브를 쏟았다.",
}


class ReportWriterTests(_ReportCase):
    """Lane RP3, decision ③: the model reads the notebook's facts and writes
    the observation notes and one result paragraph, each checked
    (tests/test_lane_rp3_ai_notes.py has the checks themselves)."""

    def write(self, reply: dict | str) -> tuple[er.ReportNarrative, _Client]:
        client = _Client(reply)
        brain = er.ReportWriterBrain(client=client, model="fake", timeout_seconds=5)
        doc = self.doc()
        narrative = asyncio.run(brain.generate_narrative(doc, list(doc["events"]), fixture=self.fixture))
        return narrative, client

    def test_the_model_reads_experiment_content_without_identifiers(self) -> None:
        self.run_steps_one_to_four_then_stop()
        narrative, client = self.write(GOOD_REPLY)
        sent = client.calls[0]["messages"][1]["content"]
        for identifier in (self.report_id, *REPORT_ID_FIELDS, "stopped", "step_completed",
                           "user_command", "p-step-03", "analysis_required"):
            self.assertNotIn(identifier, sent)
        for content in ("밴드가 투명해졌어", "튜브를 쏟았어", "원문 15분 중 10분에 끝냄", "500 µL · 37°C"):
            self.assertIn(content, sent)
        # Decision ③: no step sentence is copied to the model -- it reads the
        # conditions used and the records -- and no mark of the server's.
        self.assertNotIn("Solution A 500 µL로 37°C에서 15 min 동안 세척합니다.", sent)
        self.assertNotIn("[미확인]", sent)
        self.assertEqual([note.text for note in narrative.ai_notes], ["4단계에서 밴드가 투명해졌다."])
        self.assertEqual(narrative.ai_result, GOOD_REPLY["result_summary"])
        self.assertEqual(narrative.writer, "fake")

    def test_the_report_uses_no_outside_sources(self) -> None:
        # Decision of 2026-10-06, option (a): no Google Search grounding, so
        # nothing but the protocol is cited and the writer is never asked to
        # search.
        self.run_steps_one_to_four_then_stop()
        narrative, client = self.write(GOOD_REPLY)
        self.assertNotIn("web_search", client.calls[0])
        self.assertNotIn("외부 자료", client.calls[0]["messages"][1]["content"])
        self.assertEqual([source.kind for source in narrative.sources], ["protocol"])
        self.assertNotIn("구글", er.render_markdown(narrative))

    def test_a_failed_model_keeps_the_same_structure_with_the_servers_sentences(self) -> None:
        self.run_steps_one_to_four_then_stop()
        narrative, client = self.write("not json")
        self.assertEqual(len(client.calls), 1)
        self.assertEqual((narrative.ai_notes, narrative.ai_result), ((), ""))
        self.assertEqual((narrative.writer, narrative.failure), ("fake (실패)", "모델 답 실패(JSONDecodeError)"))
        deterministic = er.ReportWriterBrain().build_deterministic_narrative(
            self.doc(), list(self.doc()["events"]), fixture=self.fixture)
        self.assertEqual(er.render_markdown(narrative), er.render_markdown(deterministic))


if __name__ == "__main__":
    unittest.main()


class ReportExportRouteTests(_ReportCase):
    """GET /api/experiment-reports/{id}.md|.docx through the server (production boundary)."""

    def fetch(self, suffix: str, client: object | None = None, extra: dict | None = None):
        import httpx
        from voiney_lab.server import app

        async def run_inline(function, *args, **kwargs):
            return function(*args, **kwargs)

        async def get(url: str):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://testserver") as http:
                return await http.get(url)

        environment = {
            "VOINEY_LAB_EXPERIMENT_REPORTS_ENABLED": "true",
            "VOINEY_LAB_EXPERIMENT_REPORT_DB": str(self.store.path),
            "VOINEY_LAB_REPORT_WRITER_ENABLED": "true" if client else "false",
            **(extra or {}),
        }
        patches = [mock.patch.dict(os.environ, environment),
                   mock.patch.object(er, "report_protocol_fixture", return_value=(self.fixture, ""))]
        if client is None:
            patches.append(mock.patch("fastapi.routing.run_in_threadpool", side_effect=run_inline))
        # With a writer the route runs in FastAPI's thread pool, as served:
        # the writer starts its own event loop there (asyncio.run).
        if client is not None:
            patches.append(mock.patch("voiney_lab.server._role_client", return_value=client))
        for item in patches:
            item.start()
        try:
            return asyncio.run(get(f"/api/experiment-reports/{self.report_id}.{suffix}"))
        finally:
            for item in reversed(patches):
                item.stop()

    def test_markdown_export_is_the_researchers_report(self) -> None:
        self.run_steps_one_to_four_then_stop()
        response = self.fetch("md")
        self.assertEqual(response.status_code, 200)
        text = response.text
        self.assertTrue(text.startswith("# Test digestion protocol\n"))
        self.assertIn("- 이상 · 4단계 (10:09): “튜브를 쏟았어” [미확인]", text)
        self.assertNotIn(self.report_id, text)

    def test_both_exports_use_one_model_call_for_the_finished_report(self) -> None:
        # Decision 9 (2026-10-06): one model call per experiment. A finished
        # report with nothing prepared is prepared by the first download;
        # the second download uses what was kept.
        from docx import Document

        self.run_steps_one_to_four_then_stop()
        client = _Client(GOOD_REPLY)
        for suffix in ("md", "docx"):
            response = self.fetch(suffix, client, {
                "VOINEY_LAB_REPORT_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "test-key",
                "VOINEY_LAB_REPORT_MODEL": "fake-report-model"})
            self.assertEqual(response.status_code, 200, suffix)
            self.assertEqual(len(client.calls), 1, suffix)
            if suffix == "md":
                text = response.text
            else:
                text = "\n".join(p.text for p in Document(io.BytesIO(response.content)).paragraphs)
            # A prepared model suggestion never fills observation, result or
            # interpretation in the grounded notebook body.
            self.assertNotIn("AI 정리 —", text, suffix)
            self.assertNotIn("4단계에서 밴드가 투명해졌다.", text, suffix)
            self.assertIn("Test digestion protocol", text, suffix)


class NumberAndUnitCheckTests(_ReportCase):
    """Decision 3 (2026-10-06): a number with a unit is compared with its unit."""

    def problems(self, sentence: str) -> list[str]:
        self.run_steps_one_to_four_then_stop()
        facts = er.ReportWriterBrain.facts_for(self.doc(), fixture=self.fixture)
        # Lane RP3, decision ③: the same check, on an AI-written sentence.
        return [why for _, why in er.check_ai_notes({"result_summary": sentence}, facts)[2]]

    def test_the_same_value_written_another_way_passes(self) -> None:
        # The source says "500 µL", "37°C" and "15 min".
        self.assertEqual(self.problems("3단계는 Solution A 500 uL로 37 ℃에서 15분 동안 세척했다."), [])
        self.assertEqual(self.problems("3단계는 500μL, 37°C, 15min 조건이다."), [])

    def test_a_known_number_with_another_unit_is_refused(self) -> None:
        # 15 is in the record (15 min) but 15 hours is not.
        self.assertEqual(self.problems("3단계를 15시간 동안 세척했다."), ["기록·원문에 없는 값 15시간"])
        self.assertEqual(self.problems("3단계에 37 mL 를 썼다."), ["기록·원문에 없는 값 37 mL"])

    def test_a_converted_value_is_refused(self) -> None:
        self.assertEqual(self.problems("3단계를 0.25 h 동안 세척했다."), ["기록·원문에 없는 값 0.25 h"])

    def test_numbers_without_a_unit_are_checked_as_before(self) -> None:
        self.assertEqual(self.problems("1–4단계를 수행하고 10:06에 3단계를 마쳤다."), [])
        self.assertEqual(self.problems("77개 조각을 만들었다."), ["기록·원문에 없는 숫자 77"])


class ReportLayoutTests(_ReportCase):
    """Decisions 4–6 (2026-10-06): column widths, the experimenter, authorship."""

    # Lane RP3, decision ①: the notebook's tables.
    NARROW = {"단계", "종류", "시각", "항목", "과정", "시료", "대조군", "시작", "끝", "소요", "확인",
              "이름", "날짜·시각"}
    WIDE = {"내용", "원문 값 → 실제", "관찰·측정 (기록 그대로)", "타이머 (원문 / 실제)", "말한 그대로",
            "조건", "서명·한 줄 의견"}

    def narrative(self, reply: dict | None = None) -> er.ReportNarrative:
        doc = self.doc()
        if reply is None:
            return er.ReportWriterBrain().build_deterministic_narrative(doc, list(doc["events"]), fixture=self.fixture)
        brain = er.ReportWriterBrain(client=_Client(reply), model="fake-model", timeout_seconds=5)
        return asyncio.run(brain.generate_narrative(doc, list(doc["events"]), fixture=self.fixture))

    def test_every_table_has_widths_with_short_values_narrow(self) -> None:
        self.run_steps_one_to_four_then_stop()
        tables = [content for kind, content in er.report_blocks(self.narrative()) if kind == "table"]
        self.assertGreaterEqual(len(tables), 5)
        for header, _rows, widths in tables:
            self.assertEqual(len(widths), len(header), header)
            self.assertAlmostEqual(sum(widths), 1.0, places=6)
        by_header = {header: widths for header, _rows, widths in tables}
        self.assertLess(by_header[er.METHOD_HEADER][0], by_header[er.METHOD_HEADER][1])
        self.assertLess(by_header[er.OBSERVATION_HEADER][1], by_header[er.OBSERVATION_HEADER][2])
        self.assertLess(by_header[er.INVENTORY_HEADER][1], by_header[er.INVENTORY_HEADER][0])

    def test_word_applies_the_widths_and_a_fixed_layout(self) -> None:
        from docx import Document
        from docx.oxml.ns import qn

        self.run_steps_one_to_four_then_stop()
        document = Document(io.BytesIO(self.store.export_docx(self.report_id, fixture=self.fixture)))
        expected = [content for kind, content in er.report_blocks(self.narrative())
                    if kind in {"table", "sign"}]
        self.assertEqual(len(document.tables), len(expected))
        for table in document.tables:
            header = tuple(cell.text for cell in table.rows[0].cells)
            shares = er.TABLE_WIDTHS[header]
            grid = [column.width for column in table.columns]
            for width, share in zip(grid, shares):
                self.assertAlmostEqual(width / sum(grid), share, places=2, msg=header)
            for row in table.rows:
                self.assertEqual([cell.width for cell in row.cells], grid)
            layout = table._tbl.tblPr.find(qn("w:tblLayout"))
            self.assertEqual(layout.get(qn("w:type")), "fixed")

    def test_markdown_dashes_carry_the_widths(self) -> None:
        self.run_steps_one_to_four_then_stop()
        text = self.store.export_markdown(self.report_id, fixture=self.fixture).decode()
        lines = text.splitlines()
        rule = lines[lines.index("| 과정 | 시료 | 관찰·측정 (기록 그대로) | 시각 |") + 1]
        self.assertEqual([len(part) for part in rule.strip("|").split("|")], [8, 5, 23, 4])

    def test_the_experimenter_is_the_name_the_server_recorded(self) -> None:
        self.event("00:30", "experimenter_recorded", None, payload={"display_name": "김연구"})
        self.event("00:31", "experimenter_recorded", None, payload={"display_name": "다른 사람"})
        self.run_steps_one_to_four_then_stop()
        text = self.store.export_markdown(self.report_id, fixture=self.fixture).decode()
        self.assertIn("| 실험자 | 김연구 |", text)
        self.assertNotIn("다른 사람", text)
        self.assertNotIn("수행자", text)

    def test_the_run_table_says_the_analysis_passed_and_the_experimenter_started(self) -> None:
        # Lane DI (2026-10-08, decision 7): the approval row is gone; the row
        # says the rule, and the safety statements seen before the start.
        self.event("00:29", "safety_notices_acknowledged", None,
                   payload={"notice_count": 2, "confirmed_by": "experimenter_start"})
        self.run_steps_one_to_four_then_stop()
        text = self.store.export_markdown(self.report_id, fixture=self.fixture).decode()
        self.assertIn("| 프로토콜 상태 | 분석 통과 · 실험자가 시작함", text)
        # Lane RP3, decision ①: the safety notices seen are in the appendix's system line.
        self.assertIn("안전 주의 2건 보고 시작", text)
        for gone in ("승인", "검토자", "test_mode", "준비 검사"):
            self.assertNotIn(gone, text)

    def test_the_last_line_says_who_wrote_the_sentences_and_when(self) -> None:
        self.run_steps_one_to_four_then_stop()
        self.at("02:00")
        text = er.render_markdown(self.narrative())
        self.assertTrue(text.rstrip().endswith(
            "> 이 실험 노트의 문장은 서버가 실험 기록과 프로토콜 원문에서 만들었다. 모델이 쓴 문장은 포함하지 않았다. "
            "작성 2026년 10월 6일 11:00."), text[-300:])
        # Model output may be prepared for other derived exports, but the
        # handoff notebook itself remains source-and-record only.
        text = er.render_markdown(self.narrative(GOOD_REPLY))
        self.assertTrue(text.rstrip().endswith(
            "> 이 실험 노트의 문장은 서버가 실험 기록과 프로토콜 원문에서 만들었다. "
            "모델이 쓴 문장은 포함하지 않았다. 작성 2026년 10월 6일 11:00."), text[-300:])


class _SlowClient(_Client):
    """A fake model that answers only when the test lets it."""

    def __init__(self, reply: dict | str) -> None:
        super().__init__(reply)
        import threading

        self.release = threading.Event()
        answer = self.chat.completions.create

        async def create(**kwargs):
            await asyncio.to_thread(self.release.wait, 5)
            return await answer(**kwargs)

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


class ReportProsePreparationTests(_ReportCase):
    """Decision 9 (2026-10-06): the prose is written once when the experiment ends."""

    def setUp(self) -> None:
        super().setUp()
        self.preparer = er.ReportProsePreparer()
        fixture = mock.patch.object(er, "report_protocol_fixture", return_value=(self.fixture, ""))
        fixture.start()
        self.addCleanup(fixture.stop)

    def brain(self, client: object) -> er.ReportWriterBrain:
        return er.ReportWriterBrain(client=client, model="fake-model", timeout_seconds=5)

    def prepare(self, client: object, *, again: bool = False) -> str:
        state = self.preparer.start(self.store, self.report_id, lambda: self.brain(client), again=again)
        self.assertTrue(self.preparer.wait(self.store, self.report_id, 10))
        return state

    def test_an_experiment_in_progress_is_not_prepared(self) -> None:
        self.event("00:30", "session_started", "1")
        client = _Client(GOOD_REPLY)
        self.assertEqual(self.preparer.start(self.store, self.report_id, lambda: self.brain(client)),
                         "not_finished")
        self.assertEqual(client.calls, [])
        self.assertEqual(self.preparer.status(self.store, self.report_id)["state"], "not_finished")

    def test_a_finished_report_is_written_once_and_downloads_use_it(self) -> None:
        self.run_steps_one_to_four_then_stop()
        client = _SlowClient(GOOD_REPLY)
        self.assertEqual(self.preparer.start(self.store, self.report_id, lambda: self.brain(client)),
                         "preparing")
        status = self.preparer.status(self.store, self.report_id)
        self.assertEqual(status["state"], "preparing")
        self.assertIn("보고서 준비 중", status["label"])
        self.assertEqual(self.preparer.start(self.store, self.report_id, lambda: self.brain(client)),
                         "preparing")
        client.release.set()
        self.assertTrue(self.preparer.wait(self.store, self.report_id, 10))
        status = self.preparer.status(self.store, self.report_id)
        self.assertEqual((status["state"], status["ai_written"]), ("ready", True))
        self.assertIn("AI(fake-model)", status["label"])
        self.assertEqual(self.preparer.start(self.store, self.report_id, lambda: self.brain(client)), "ready")
        text = self.store.export_markdown(self.report_id, fixture=self.fixture).decode()
        self.store.export_docx(self.report_id, fixture=self.fixture)
        self.assertEqual(len(client.calls), 1)
        self.assertNotIn("> AI 정리 —", text)
        self.assertIn("모델이 쓴 문장은 포함하지 않았다", text)

    def test_asking_again_is_a_second_call(self) -> None:
        self.run_steps_one_to_four_then_stop()
        client = _Client(GOOD_REPLY)
        self.prepare(client)
        self.prepare(client, again=True)
        self.assertEqual(len(client.calls), 2)

    def test_without_a_model_the_servers_sentences_are_ready_at_once(self) -> None:
        self.run_steps_one_to_four_then_stop()
        self.assertEqual(self.preparer.start(self.store, self.report_id, lambda: None), "ready")
        status = self.preparer.status(self.store, self.report_id)
        self.assertEqual((status["state"], status["ai_written"]), ("ready", False))
        self.assertIn("서버 문장", status["label"])
        text = self.store.export_markdown(self.report_id, fixture=self.fixture).decode()
        self.assertIn("모델이 쓴 문장은 포함하지 않았다", text)

    def test_a_failed_model_leaves_the_servers_sentences(self) -> None:
        self.run_steps_one_to_four_then_stop()
        client = _Client("not json")
        self.prepare(client)
        status = self.preparer.status(self.store, self.report_id)
        self.assertEqual((status["state"], status["ai_written"]), ("ready", False))
        self.assertIn("AI 문장을 쓰지 못함: 모델 답 실패(JSONDecodeError)", status["label"])
        text = self.store.export_markdown(self.report_id, fixture=self.fixture).decode()
        self.assertNotIn("AI 정리 —", text)
        self.assertIn("모델이 쓴 문장은 포함하지 않았다", text)

    def test_records_added_after_preparing_appear_and_are_counted(self) -> None:
        self.run_steps_one_to_four_then_stop()
        client = _Client(GOOD_REPLY)
        self.prepare(client)
        self.event("01:30", "photo_attached", "4", user_wording="gel.png",
                   confirmation_state="user_reported")
        status = self.preparer.status(self.store, self.report_id)
        self.assertEqual(status["new_records"], 1)
        self.assertIn("기록이 1건 늘었습니다", status["label"])
        text = self.store.export_markdown(self.report_id, fixture=self.fixture).decode()
        self.assertIn("| 4단계 · Dehydration | — | 사진: gel.png | 10:30 |", text)
        self.assertEqual(len(client.calls), 1)


class QuotedObservationTests(_ReportCase):
    """The results check pairs each quotation mark with its own closing mark.

    Found in the round-2 live run (scenario C): a recorded endpoint answer
    quotes the source in curly quotes; the model quoted that record in
    straight quotes, and the check paired the marks wrongly and refused a
    sentence that only quoted the record.
    """

    def check(self, results: str) -> list[str]:
        self.event("00:30", "session_started", "1")
        self.event("00:31", "observation", "4",
                   user_wording="원문 종점 “The gel should look white.” — 답: 네",
                   confirmation_state="user_reported")
        self.event("00:32", "observation", "4", user_wording="젤 조각이 다 흡수했어",
                   confirmation_state="user_reported")
        facts = er.ReportWriterBrain.facts_for(self.doc(), fixture=self.fixture)
        # Lane RP3, decision ③: the same check, on an AI-written sentence.
        return [why for _, why in er.check_ai_notes({"result_summary": results}, facts)[2]]

    def test_a_record_with_curly_quotes_quoted_in_straight_ones_passes(self) -> None:
        self.assertEqual(self.check(
            '4단계에서 "원문의 끝 조건(“The gel should look white.”)을 충족했다고 답했다"로 확인되었으며, '
            '"젤 조각이 다 흡수했어"가 기록되었다.'), [])

    def test_an_unrecorded_quotation_is_still_refused(self) -> None:
        self.assertEqual(self.check('4단계에서 “젤이 노랗게 변했다”가 기록되었다.'),
                         ["기록에 없는 관찰 “젤이 노랗게 변했다”"])
