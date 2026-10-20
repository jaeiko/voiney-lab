"""The report's first pages, read at a glance (lane RP2, decision 1, 2026-10-19),
as the lab notebook keeps them (lane RP3, decision ①, 2026-10-20).

Human decision of lane RP2: the report opens with its header, then one row
per step with its times, one row per timer, the deviations and anomalies
with the source's side, the check before the start, and a place for the
experimenter's name and date -- built from the record, nothing written by
a model. Lane RP3's decision ① moved what a researcher reads into the
notebook's body (the header, the conditions "원문 값 → 실제", the
differences) and the agent's doings into the appendix (the steps' times,
one line of system events: lost connections, timer notices, the check
before the start, the safety notices seen); the counts table, the notices
table and the check's table are gone from the body. These tests follow
each fact to where it now stands.
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

    @staticmethod
    def system_line(text: str) -> str:
        return next(line for line in text.splitlines() if line.startswith("> 시스템 사건: "))


class ReportOrderTests(_GlanceCase):
    def test_the_header_comes_first_and_the_sections_in_the_decided_order(self) -> None:
        text = self.markdown(deviation_run(self.temporary.name))
        headings = [line for line in text.splitlines() if line.startswith("## ")]
        self.assertEqual(headings, list(er.NOTEBOOK_HEADINGS))
        # The header is the report's first table, right under the title.
        lines = text.splitlines()
        self.assertEqual(lines[0], "# Test digestion protocol")
        self.assertEqual(lines[4], "| 항목 | 내용 |")

    def test_the_header_names_protocol_review_version_and_experimenter(self) -> None:
        text = self.markdown(deviation_run(self.temporary.name))
        rows = self.table(text, "| 항목 | 내용 |")
        self.assertEqual(rows[:3], [
            "| 날짜·시간 | 2026년 10월 19일 (월) 09:30 – 10:10 · 40분 |",
            "| 실험자 | 이실험 |",
            "| 프로토콜 | Test digestion protocol · 검토본 v2 (김검토, 2026-10-17) |",
        ])
        self.assertIn("| 진행 | 끝까지 완료 · 5 / 5단계 |", rows)
        self.assertEqual(text.count("| 프로토콜 | "), 1)

    def test_a_run_without_a_review_version_says_so(self) -> None:
        text = self.markdown(short_run(self.temporary.name), protocol_double(labels=("1", "2"), review=False))
        self.assertIn("| 프로토콜 | Short wash protocol · 분석 결과 그대로 |", text)
        # Lane DI: no reviewer words for a run without a review version.
        self.assertNotIn("검토자", text)
        self.assertNotIn("검토본", text)


class SystemLineTests(_GlanceCase):
    """The counts table is gone; the agent's doings are one appendix line."""

    def test_the_lost_connection_from_its_event(self) -> None:
        text = self.markdown(deviation_run(self.temporary.name))
        self.assertEqual(self.system_line(text),
                         "> 시스템 사건: 연결 끊김 1회 (3단계 09:50–09:52, 1분 30초) · 타이머 알림 0회 · "
                         "시작 전 확인 3답 (맞음 1 · 다르게 고름 1 · 원문대로 맞다고 함 1) · 안전 주의 2건 보고 시작 · 멈춤 0회")
        self.assertNotIn("한눈 요약", text)
        self.assertNotIn("| 단계 완료 | 편차 |", text)

    def test_a_short_run_and_a_stopped_one(self) -> None:
        short = self.markdown(short_run(self.temporary.name), protocol_double(labels=("1", "2")))
        self.assertIn("연결 끊김 0회 · 타이머 알림 0회 · 시작 전 확인 3답", self.system_line(short))
        stopped = self.markdown(unconfirmed_run(self.temporary.name))
        self.assertIn("타이머 알림 2회 (말함 2 · 화면 0)", self.system_line(stopped))
        self.assertIn("| 진행 | 중단 — 4단계에서 · 3 / 5단계 |", stopped)


class StepTableTests(_GlanceCase):
    """One row per step performed, with its times, in the appendix."""

    HEADER = "| 단계 | 시작 | 끝 | 소요 | 타이머 (원문 / 실제) |"

    def test_one_row_per_step_with_times(self) -> None:
        text = self.markdown(deviation_run(self.temporary.name))
        self.assertEqual(self.table(text, self.HEADER), [
            "| 1 | 09:30 | 09:36 | 6분 | — |",
            "| 2 | 09:36 | 09:47 | 11분 | — |",
            "| 3 | 09:47 | 09:58 | 11분 | 원문 15분 / 10분에 끝냄 |",
            "| 4 | 09:58 | 10:05 | 7분 | — |",
            "| 5 | 10:05 | 10:10 | 5분 | — |",
        ])
        # The values, deviations and anomalies stand in the body's sections.
        self.assertIn("| 2단계 · Gel band destaining | 온도 37°C → 40°C (“40도에서 씻었어”) · 그 밖 원문대로 (500 µL · 10 min) |", text)
        self.assertIn("| 1단계 · Gel band destaining | — | 조각 크기 1 mm (정정됨 — 처음 “조각 크기 2 mm”) | 09:34 |", text)
        self.assertIn("- 이상 · 4단계 (10:00): “튜브를 쏟았어”", text)

    def test_a_stopped_run_shows_the_step_it_stopped_at_and_leaves_out_the_steps_not_done(self) -> None:
        text = self.markdown(unconfirmed_run(self.temporary.name))
        rows = self.table(text, self.HEADER)
        self.assertEqual(rows[3], "| 4 | 10:01 | 미완료 | — | — |")
        self.assertEqual(len(rows), 4)
        self.assertIn("- 4단계에서 끝냄 — 4–5단계는 하지 않음", text)

    def test_a_pause_is_named_in_the_time_taken(self) -> None:
        record = short_run(self.temporary.name)
        record.event("00:38", "workflow_paused", "2")
        record.event("00:41", "workflow_resumed", "2")
        text = self.markdown(record, protocol_double(labels=("1", "2")))
        self.assertIn("| 2 | 09:36 | 09:47 | 11분 (멈춤 3분 포함) | — |", text)
        self.assertIn("멈춤 1회", self.system_line(text))


class TimerTests(_GlanceCase):
    """The timers' words in the step times; the notices in the system line."""

    HEADER = "| 단계 | 시작 | 끝 | 소요 | 타이머 (원문 / 실제) |"

    def test_a_timer_ended_early_and_one_waited_out_with_its_notices(self) -> None:
        early = self.markdown(deviation_run(self.temporary.name))
        self.assertIn("| 3 | 09:47 | 09:58 | 11분 | 원문 15분 / 10분에 끝냄 |", early)
        self.assertIn("| 3단계 · Gel band destaining | 시간 15 min → 10분에 끝냄 (타이머 일찍 끝냄) · 그 밖 원문대로 (37°C) |", early)
        waited = self.markdown(unconfirmed_run(self.temporary.name))
        self.assertIn("| 3 | 09:44 | 10:01 | 17분 | 원문 15분 / 실제 16분 |", waited)
        self.assertIn("타이머 알림 2회 (말함 2 · 화면 0)", self.system_line(waited))

    def test_a_source_timer_never_started_says_so(self) -> None:
        record = deviation_run(self.temporary.name)
        fixture = protocol_double()
        fixture.timer_manifest = {"p-step-03": 900, "p-step-05": 600}
        rows = self.table(self.markdown(record, fixture), self.HEADER)
        self.assertEqual(rows[4], "| 5 | 10:05 | 10:10 | 5분 | 원문 10분 / 타이머 기록 없음 |")
        short = self.markdown(short_run(self.temporary.name), protocol_double(labels=("1", "2")))
        self.assertNotIn("## 타이머", short)


class IssueTests(_GlanceCase):
    """The deviations and anomalies, with the source's side, in section 4."""

    def issues(self, text: str) -> list[str]:
        block = text.split("## 4. 원문과 다르게 한 점·문제")[1].split("## 5.")[0]
        return [line for line in block.splitlines() if line.startswith("- ")]

    def test_deviations_and_anomalies_with_the_source_side(self) -> None:
        text = self.markdown(deviation_run(self.temporary.name))
        self.assertEqual(self.issues(text), [
            "- 2단계 온도: 원문 37°C → 40°C — “40도에서 씻었어”",
            "- 3단계 타이머: 원문 15분 중 10분에 끝냄",
            "- 이상 · 4단계 (10:00): “튜브를 쏟았어”",
        ])

    def test_a_corrected_record_shows_what_was_first_recorded(self) -> None:
        record = deviation_run(self.temporary.name)
        record.event("01:20", "record_corrected", "4", payload={"record_fix": {
            "target_event_key": "turn-11", "text_before": "튜브를 쏟았어", "text_after": "튜브 하나를 쏟았어",
            "source": "voice"}})
        text = self.markdown(record)
        self.assertEqual(self.issues(text)[2], "- 이상 · 4단계 (10:00): “튜브 하나를 쏟았어” [미확인]")
        self.assertIn("| 10:00 | 4 | 이상 | 튜브를 쏟았어 → 10:20 정정: 튜브 하나를 쏟았어 |", text)

    def test_a_run_without_any_says_so(self) -> None:
        text = self.markdown(short_run(self.temporary.name), protocol_double(labels=("1", "2")))
        self.assertIn("## 4. 원문과 다르게 한 점·문제\n\n> 원문대로 했고, 기록된 문제가 없다.", text)


class StartCheckTests(_GlanceCase):
    """The check before the start: counted in the system line, not a table."""

    def test_the_answers_before_the_start_and_the_safety_notices(self) -> None:
        text = self.markdown(deviation_run(self.temporary.name))
        self.assertIn("시작 전 확인 3답 (맞음 1 · 다르게 고름 1 · 원문대로 맞다고 함 1)", self.system_line(text))
        self.assertIn("안전 주의 2건 보고 시작", self.system_line(text))
        self.assertNotIn("| 확인한 값 |", text)
        self.assertNotIn("150 min", text)

    def test_no_answers_and_answers_not_read_are_told_apart(self) -> None:
        record = deviation_run(self.temporary.name)
        none = self.markdown(record, start_check=())
        self.assertIn("시작 전 확인 기록 없음", self.system_line(none))
        unread = self.markdown(record, start_check=None)
        self.assertIn("시작 전 확인 기록을 읽지 못함", self.system_line(unread))

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
    def test_a_place_for_the_experimenter_and_the_pi(self) -> None:
        text = self.markdown(deviation_run(self.temporary.name))
        block = text.split("## 7. 확인")[1].split("## 8. 부록")[0]
        self.assertIn("| 확인 | 이름 | 날짜·시각 | 서명·한 줄 의견 |", block)
        self.assertIn("| 실험자 |", block)
        self.assertIn("| PI |", block)


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

    def test_the_tables_fit_the_text_width_and_repeat_their_header(self) -> None:
        from docx.oxml.ns import qn
        from docx.shared import Cm

        document = self.document()
        headers = {tuple(cell.text for cell in table.rows[0].cells): table for table in document.tables}
        section = document.sections[0]
        text_width = section.page_width - section.left_margin - section.right_margin
        for header in (er.CONDITION_HEADER, er.OBSERVATION_HEADER, er.CONFIRM_HEADER,
                       er.APPENDIX_STEP_HEADER, er.APPENDIX_RECORD_HEADER):
            table = headers[header]
            self.assertLessEqual(sum(column.width for column in table.columns), text_width + Cm(0.01), header)
            self.assertAlmostEqual(sum(er.TABLE_WIDTHS[header]), 1.0, places=6)
            first = table.rows[0]._tr.trPr
            self.assertIsNotNone(first.find(qn("w:tblHeader")), header)


if __name__ == "__main__":
    unittest.main()
