"""The report's first pages, read at a glance (lane RP2, decision 1, 2026-10-19).

Human decision: the report opens with its header (protocol, review version,
reviewer, experimenter, date, start and end, time taken), then the counts
(steps n/N, deviations, anomalies, corrections, unconfirmed, lost
connections), one row per step, one row per timer, the deviations and
anomalies with the source's side, the check before the start, and a place
for the experimenter's name and date. Korean fonts and table widths fit A4.
Everything is built from the record; nothing is written by a model.
"""

from __future__ import annotations

import io
import unittest

from voiney_lab import experiment_reports as er

from tests.lane_rp2_support import (
    START_CHECK, deviation_run, environment, protocol_double, short_run, temporary,
    unconfirmed_run,
)


class _GlanceCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = temporary()
        self.env = environment()
        self.env.start()
        er._NARRATIVE_CACHE.clear()

    def tearDown(self) -> None:
        self.env.stop()
        er._NARRATIVE_CACHE.clear()
        self.temporary.cleanup()

    def markdown(self, record, fixture=None, start_check=START_CHECK) -> str:
        return record.store.export_markdown(
            record.report_id, fixture=fixture or protocol_double(), start_check=start_check).decode()

    @staticmethod
    def table(text: str, header_line: str) -> list[str]:
        lines = text.splitlines()
        start = lines.index(header_line) + 2
        rows = []
        for line in lines[start:]:
            if not line.startswith("|"):
                break
            rows.append(line)
        return rows


class ReportOrderTests(_GlanceCase):
    def test_the_first_pages_come_before_the_sections_in_the_decided_order(self) -> None:
        text = self.markdown(deviation_run(self.temporary.name))
        headings = [line for line in text.splitlines() if line.startswith("## ")]
        self.assertEqual(headings[:7], [
            "## 한눈 요약", "## 단계별 기록", "## 타이머", "## 편차·이상 모음",
            "## 시작 전 확인 결과", "## 실험자 확인", "## 1. 실험 목적"])
        # The header is the report's first table, right under the title.
        lines = text.splitlines()
        self.assertEqual(lines[0], "# Test digestion protocol 실험 보고서")
        self.assertEqual(lines[2], "| 항목 | 내용 |")

    def test_the_header_names_protocol_review_version_reviewer_and_experimenter_first(self) -> None:
        text = self.markdown(deviation_run(self.temporary.name))
        rows = self.table(text, "| 항목 | 내용 |")
        self.assertEqual(rows[:8], [
            "| 프로토콜 | Test digestion protocol |",
            "| 검토본 | 검토본 v2 · 김검토 · 2026-10-17 |",
            "| 검토자 | 김검토 |",
            "| 실험자 | 이실험 |",
            "| 날짜 | 2026년 10월 19일 (월) |",
            "| 시작 | 09:30 |",
            "| 끝 | 10:10 |",
            "| 걸린 시간 | 40분 |",
        ])
        # The other run rows follow, once each.
        for row in ("| 완료 단계 | 5 / 5 |", "| 결과 | 끝까지 완료 |",
                    "| 안전 주의 확인 | 시작 전 화면에서 원문 안전 주의 2건을 보고 시작함 |"):
            self.assertEqual(rows.count(row), 1, row)
        self.assertEqual(text.count("| 프로토콜 | "), 1)

    def test_a_run_without_a_review_version_says_so(self) -> None:
        text = self.markdown(short_run(self.temporary.name), protocol_double(labels=("1", "2"), review=False))
        self.assertIn("| 검토본 | 없음 (분석 결과 그대로 실행) |", text)
        # Lane DI: no reviewer words for a run without a review version.
        self.assertNotIn("검토자", text)


class SummaryTests(_GlanceCase):
    HEADER = "| 단계 완료 | 편차 | 이상 | 정정 | 미확인 | 연결 끊김 |"

    def test_the_counts_of_a_run_with_deviations(self) -> None:
        text = self.markdown(deviation_run(self.temporary.name))
        self.assertEqual(self.table(text, self.HEADER), ["| 5 / 5 | 2건 | 1건 | 1건 | 0개 | 1회 |"])
        # The lost connection, from its event: when, how long, at which step.
        self.assertIn("- 3단계에서 09:50에 연결이 끊겼다가 09:52에 다시 연결됨 — 끊긴 시간 1분 30초", text)

    def test_the_counts_of_a_short_run_and_a_stopped_one(self) -> None:
        short = self.markdown(short_run(self.temporary.name), protocol_double(labels=("1", "2")))
        self.assertEqual(self.table(short, self.HEADER), ["| 2 / 2 | 0건 | 0건 | 0건 | 0개 | 0회 |"])
        stopped = self.markdown(unconfirmed_run(self.temporary.name))
        self.assertEqual(self.table(stopped, self.HEADER), ["| 3 / 5 | 0건 | 0건 | 0건 | 2개 | 0회 |"])


class StepTableTests(_GlanceCase):
    HEADER = "| 단계 | 시작 | 끝 | 소요 | 기록한 값 | 편차 (원문 → 기록) | 이상 |"

    def test_one_row_per_step_with_times_values_deviations_and_anomalies(self) -> None:
        text = self.markdown(deviation_run(self.temporary.name))
        self.assertEqual(self.table(text, self.HEADER), [
            "| 1 | 09:30 | 09:36 | 6분 | 측정: 조각 크기 1 mm (정정됨) | — | — |",
            "| 2 | 09:36 | 09:47 | 11분 | — | 37°C → 40°C | — |",
            "| 3 | 09:47 | 09:58 | 11분 | — | 타이머 15분 → 10분에 끝냄 | — |",
            "| 4 | 09:58 | 10:05 | 7분 | — | — | 튜브를 쏟았어 |",
            "| 5 | 10:05 | 10:10 | 5분 | — | — | — |",
        ])

    def test_a_stopped_run_shows_the_step_it_stopped_at_and_the_steps_not_done(self) -> None:
        text = self.markdown(unconfirmed_run(self.temporary.name))
        rows = self.table(text, self.HEADER)
        # The value is not confirmed: decision 3 marks it.
        self.assertEqual(rows[3], "| 4 | 10:01 | 완료 기록 없음 | — | 측정: acetonitrile 180 µL 넣음 [미확인] | — | — |")
        self.assertEqual(rows[4], "| 5 | — | 수행 안 함 | — | — | — | — |")

    def test_a_pause_is_named_in_the_time_taken(self) -> None:
        record = short_run(self.temporary.name)
        record.event("00:38", "workflow_paused", "2")
        record.event("00:41", "workflow_resumed", "2")
        text = self.markdown(record, protocol_double(labels=("1", "2")))
        self.assertIn("| 2 | 09:36 | 09:47 | 11분 (멈춤 3분 포함) | — | — | — |", text)


class TimerTableTests(_GlanceCase):
    HEADER = "| 단계 | 원문 시간 | 켠 시각 | 1분 전 알림 | 끝 알림 | 단계 끝 | 결과 |"

    def test_a_timer_ended_early_and_one_waited_out_with_its_notices(self) -> None:
        early = self.markdown(deviation_run(self.temporary.name))
        self.assertEqual(self.table(early, self.HEADER),
                         ["| 3 | 15분 | 09:48 | — | — | 09:58 | 일찍 끝냄 (원문 15분 중 10분) |"])
        waited = self.markdown(unconfirmed_run(self.temporary.name))
        self.assertEqual(self.table(waited, self.HEADER),
                         ["| 3 | 15분 | 09:45 | 09:59 (말함) | 10:00 (말함) | 10:01 | 원문 시간 뒤 완료 (실제 16분) |"])

    def test_a_source_timer_never_started_is_a_row_and_no_timer_is_a_note(self) -> None:
        record = deviation_run(self.temporary.name)
        fixture = protocol_double()
        fixture.timer_manifest = {"p-step-03": 900, "p-step-05": 600}
        rows = self.table(self.markdown(record, fixture), self.HEADER)
        self.assertEqual(rows[1], "| 5 | 10분 | 켜지 않음 | — | — | 10:10 | 타이머 기록 없음 |")
        short = self.markdown(short_run(self.temporary.name), protocol_double(labels=("1", "2")))
        self.assertIn("## 타이머\n\n> 이 실험에는 타이머가 없다.", short)


class IssueTableTests(_GlanceCase):
    HEADER = "| 번호 | 단계 | 종류 | 원문 (쪽) | 기록 | 시각 | 확인 |"

    def test_deviations_and_anomalies_with_the_source_side(self) -> None:
        text = self.markdown(deviation_run(self.temporary.name))
        self.assertEqual(self.table(text, self.HEADER), [
            "| 1 | 2 | 편차 | 37°C · 2쪽 | 40도에서 씻었어 | 09:40 | 확인 |",
            "| 2 | 3 | 타이머 일찍 끝냄 | 15분 · 3쪽 | 10분에 단계를 끝냄 | 09:58 | 확인 |",
            "| 3 | 4 | 이상 | 3쪽 | 튜브를 쏟았어 | 10:00 | 확인 |",
        ])
        self.assertIn("> 원문 (쪽)은 원문의 값과 그 단계가 원문에 있는 쪽이다.", text)

    def test_a_corrected_record_shows_what_was_first_recorded(self) -> None:
        record = deviation_run(self.temporary.name)
        record.event("01:20", "record_corrected", "4", payload={"record_fix": {
            "target_event_key": "turn-11", "text_before": "튜브를 쏟았어", "text_after": "튜브 하나를 쏟았어",
            "source": "voice"}})
        rows = self.table(self.markdown(record), self.HEADER)
        self.assertEqual(rows[2], "| 3 | 4 | 이상 | 3쪽 | 튜브 하나를 쏟았어<br>처음 기록: “튜브를 쏟았어” | 10:00 | 미확인 |")

    def test_a_run_without_any_says_so(self) -> None:
        text = self.markdown(short_run(self.temporary.name), protocol_double(labels=("1", "2")))
        self.assertIn("## 편차·이상 모음\n\n> 기록에서 편차·이상을 찾지 못했다.", text)


class StartCheckTests(_GlanceCase):
    HEADER = "| 확인한 값 | 단계 | 원문 값 | 고른 값 | 결과 | 시각 |"

    def test_each_answer_before_the_start_and_the_safety_notices(self) -> None:
        text = self.markdown(deviation_run(self.temporary.name))
        self.assertEqual(self.table(text, self.HEADER), [
            "| 시간 | 3 | 15 min | 150 min | 다르게 고름 | 09:27 |",
            "| 시간 | 3 | 15 min | — | 원문대로 맞다고 함 | 09:27 |",
            "| 온도 | 2 | 37°C | 37°C | 맞음 | 09:28 |",
        ])
        self.assertIn("- 안전 주의: 시작 전 화면에서 원문 안전 주의 2건을 보고 시작함", text)

    def test_no_answers_and_answers_not_read_are_told_apart(self) -> None:
        record = deviation_run(self.temporary.name)
        none = self.markdown(record, start_check=())
        self.assertIn("> 시작 전 핵심 값 확인 기록이 없다 (확인이 꺼져 있었거나 확인할 숫자 값이 없었다).", none)
        unread = self.markdown(record, start_check=None)
        self.assertIn("> 시작 전 핵심 값 확인 기록을 읽지 못했다.", unread)

    def test_answers_are_read_from_the_protocol_ledger_between_runs(self) -> None:
        events = [
            {"event_type": "protocol_start_check_answered", "payload": dict(START_CHECK[2])},
            {"event_type": "protocol_start_check_answered",
             "payload": {**START_CHECK[0], "recorded_at": "2026-10-18T05:00:00+00:00"}},
            {"event_type": "protocol_start_check_answered",
             "payload": {**START_CHECK[0], "recorded_at": "2026-10-19T00:31:00+00:00"}},
            {"event_type": "protocol_reviewed", "payload": {"recorded_at": "2026-10-19T00:10:00+00:00"}},
        ]
        chosen = er.start_check_answers_between(
            events, since="2026-10-18T23:00:00+00:00", until="2026-10-19T00:30:00+00:00")
        self.assertEqual(chosen, [START_CHECK[2]])


class SignatureTests(_GlanceCase):
    def test_a_place_for_the_name_and_the_date(self) -> None:
        text = self.markdown(deviation_run(self.temporary.name))
        block = text.split("## 실험자 확인")[1].split("## 1. 실험 목적")[0]
        self.assertIn("| 실험자 확인 | 적는 칸 |", block)
        for row in ("| 이름 |", "| 날짜 |", "| 서명 |"):
            self.assertIn(row, block)
        self.assertIn("> 위 기록이 실제로 한 실험과 같은지 확인한 뒤 적는다.", block)


class WordLayoutTests(_GlanceCase):
    def document(self):
        from docx import Document

        record = deviation_run(self.temporary.name)
        return Document(io.BytesIO(record.store.export_docx(
            record.report_id, fixture=protocol_double(), start_check=START_CHECK)))

    def test_a4_with_a_korean_font_for_every_script(self) -> None:
        from docx.oxml.ns import qn
        from docx.shared import Cm

        document = self.document()
        section = document.sections[0]
        # Word keeps lengths in twips, a few EMU from the centimetres.
        self.assertAlmostEqual(section.page_width, Cm(21.0), delta=Cm(0.01))
        self.assertAlmostEqual(section.page_height, Cm(29.7), delta=Cm(0.01))
        fonts = document.styles["Normal"].element.rPr.rFonts
        for script in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
            self.assertEqual(fonts.get(qn(script)), er.REPORT_FONT, script)
        for theme in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme"):
            self.assertIsNone(fonts.get(qn(theme)), theme)

    def test_the_new_tables_fit_the_text_width_and_repeat_their_header(self) -> None:
        from docx.oxml.ns import qn
        from docx.shared import Cm

        document = self.document()
        headers = {tuple(cell.text for cell in table.rows[0].cells): table for table in document.tables}
        section = document.sections[0]
        text_width = section.page_width - section.left_margin - section.right_margin
        for header in (er.GLANCE_SUMMARY_HEADER, er.GLANCE_STEP_HEADER, er.GLANCE_TIMER_HEADER,
                       er.GLANCE_ISSUE_HEADER, er.GLANCE_PRESTART_HEADER, er.GLANCE_SIGN_HEADER):
            table = headers[header]
            self.assertLessEqual(sum(column.width for column in table.columns), text_width + Cm(0.01), header)
            self.assertAlmostEqual(sum(er.TABLE_WIDTHS[header]), 1.0, places=6)
            first = table.rows[0]._tr.trPr
            self.assertIsNotNone(first.find(qn("w:tblHeader")), header)

    def test_a_cell_with_several_values_keeps_them_on_their_own_lines(self) -> None:
        record = deviation_run(self.temporary.name)
        record.event("00:35", "observation", "1", user_wording="색이 하얗게 변함", category="appearance")
        from docx import Document

        document = Document(io.BytesIO(record.store.export_docx(
            record.report_id, fixture=protocol_double(), start_check=START_CHECK)))
        steps = next(table for table in document.tables
                     if tuple(cell.text for cell in table.rows[0].cells) == er.GLANCE_STEP_HEADER)
        cell = steps.rows[1].cells[4]
        self.assertEqual([paragraph.text for paragraph in cell.paragraphs],
                         ["측정: 조각 크기 1 mm (정정됨)", "관찰: 색이 하얗게 변함"])

    def test_the_sections_start_on_a_new_page(self) -> None:
        from docx.oxml.ns import qn

        document = self.document()
        texts = [paragraph.text for paragraph in document.paragraphs]
        before = document.paragraphs[texts.index("1. 실험 목적") - 1]
        breaks = before._p.findall(".//" + qn("w:br"))
        self.assertTrue(any(item.get(qn("w:type")) == "page" for item in breaks))


if __name__ == "__main__":
    unittest.main()
