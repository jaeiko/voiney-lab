"""The lab notebook a researcher reads (lane RP3, decision ①, 2026-10-20).

Human decision: the report is an experiment notebook for the researcher who
ran it, their PI and the lab meeting -- what was done, with which samples,
under which conditions, what was seen, what differed from the source and
from the run before, and what to change next -- in this order: header,
samples and conditions, the conditions actually used ("원문 값 → 실제"),
observations and results, what differed from the source and problems, what
differed from the run before, what to change next, the confirmation box
(experimenter, PI), and a small appendix (step times, the words as said,
one line of system events). Every value comes from the record and the
source; nothing is written by a model in this decision. The agent's own
doings (notices, lost connections, the check before the start) stay out of
the body.
"""

from __future__ import annotations

import io
import unittest

from voiney_lab import experiment_reports as er

from tests.lane_rp2_support import START_CHECK, deviation_run, protocol_double, short_run, unconfirmed_run
from tests.lane_rp3_support import (
    CELL_PURPOSE, cell_protocol, cell_run, coating_protocol, coating_run, earlier_cell_run,
    environment, headspace_protocol, headspace_run, temporary,
)


class _Case(unittest.TestCase):
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
    def section(text: str, heading: str) -> str:
        """The lines of one "## " section, up to the next one."""

        lines = text.splitlines()
        start = lines.index(heading) + 1
        end = next((i for i in range(start, len(lines)) if lines[i].startswith("## ")), len(lines))
        return "\n".join(lines[start:end])


class OrderAndHeaderTests(_Case):
    def test_the_sections_come_in_the_decided_order(self) -> None:
        text = self.markdown(deviation_run(self.temporary.name))
        headings = [line for line in text.splitlines() if line.startswith("## ")]
        self.assertEqual(headings, list(er.NOTEBOOK_HEADINGS))
        self.assertEqual(headings, [
            "## 실험 정보", "## 목적", "## 방법", "## 관찰·특이사항",
            "## 결과", "## 해석·다음 할 일", "## 분야별 기록", "## 부록"])

    def test_the_header_names_date_experimenter_protocol_version_source_and_progress(self) -> None:
        text = self.markdown(deviation_run(self.temporary.name))
        lines = text.splitlines()
        self.assertEqual(lines[0], "# Test digestion protocol")
        self.assertEqual(lines[2], "> 실험 노트")
        rows = self.table(text, "| 항목 | 내용 |")
        self.assertEqual(rows, [
            "| 날짜·시간 | 2026년 10월 19일 (월) 09:30 – 10:10 · 40분 |",
            "| 실험자 | 이실험 |",
            "| 프로토콜 | Test digestion protocol · 검토본 v2 (김검토, 2026-10-17) |",
            "| 출처 | Kim 2025. Test digestion protocol. protocols.io |",
            "| 프로토콜 상태 | 분석 통과 · 실험자가 시작함 |",
            "| 진행 | 끝까지 완료 · 5 / 5단계 |",
        ])

    def test_a_run_without_a_review_version_and_a_stopped_run(self) -> None:
        text = self.markdown(headspace_run(self.temporary.name), headspace_protocol(), start_check=None)
        self.assertIn("| 프로토콜 | Using dynamic headspace collections for bacterial volatile sampling"
                      " · 분석 결과 그대로 |", text)
        self.assertIn("| 출처 | Novakova 2024. Using dynamic headspace collections for bacterial volatile"
                      " sampling. protocols.io https://dx.doi.org/10.17504/protocols.io.kqdg3q4eev25/v1 |", text)
        self.assertIn("| 진행 | 중단 — 5단계에서 · 4 / 6단계 |", text)
        self.assertNotIn("검토자", text)

    def test_the_purpose_said_is_the_title_and_the_protocol_stays_under_it(self) -> None:
        text = self.markdown(cell_run(self.temporary.name), cell_protocol(), start_check=None)
        lines = text.splitlines()
        self.assertEqual(lines[0], "# A549 세포에 약물 X를 0.5랑 5 µM로 처리해서 생존율을 보는 실험")
        self.assertEqual(lines[2], "> 실험 노트 · Cell viability assay after drug treatment")
        self.assertIn("| 프로토콜 | Cell viability assay after drug treatment · 분석 결과 그대로 |", text)

    def test_what_the_body_leaves_out(self) -> None:
        text = self.markdown(cell_run(self.temporary.name), cell_protocol(), start_check=None)
        body = text.split("## 부록")[0]
        for gone in ("한눈 요약", "## 타이머", "편차·이상 모음", "시작 전 확인", "1. 실험 목적", "배경·원리",
                     "재료와 장비", "수행한 단계", "방법 요약", "고찰", "결론", "키워드", "원문에는 목적을",
                     "연구자가 직접 적는다", "확인할 점을 찾지 못했다", "NAME", "TYPE", "SKU", "Catalog #",
                     "연결 끊김", "1분 전 알림", "안전 주의 확인", "시간 기준", "걸린 시간"):
            self.assertNotIn(gone, body, gone)
        # NOTE1: the method keeps each exact source step beside actual values.
        for sentence in ("Seed A549 cells at 5000 cells per well", "Add the compound at 0.5 µM and 5 µM"):
            self.assertIn(sentence, body)


class SampleTests(_Case):
    def test_the_samples_said_at_the_start_are_a_table_with_the_words_under_it(self) -> None:
        text = self.markdown(cell_run(self.temporary.name), cell_protocol(), start_check=None)
        self.assertEqual(self.table(text, "| 시료 | 조건 | 대조군 |"), [
            "| 1번 샘플 | 처리군 |   |",
            "| 2번 샘플 | 처리군 |   |",
            "| 3번 샘플 | 대조군 | 예 |",
        ])
        self.assertEqual(self.section(text, "## 목적").strip(), CELL_PURPOSE)

    def test_nothing_said_is_said_so(self) -> None:
        text = self.markdown(deviation_run(self.temporary.name))
        self.assertEqual(self.section(text, "## 목적").strip(), "> 기록 없음")
        self.assertNotIn("| 시료 | 조건 | 대조군 |", self.section(text, "## 실험 정보"))

    def test_samples_are_read_from_the_words_when_the_event_holds_none(self) -> None:
        self.assertEqual(er.samples_in_text("1번 2번 샘플이 처리군이고 3번은 대조군"), [
            {"name": "1번 샘플", "condition": "처리군", "control": False},
            {"name": "2번 샘플", "condition": "처리군", "control": False},
            {"name": "3번 샘플", "condition": "대조군", "control": True},
        ])
        self.assertEqual(er.samples_in_text("시편 S1은 2 mm/s, S2는 4 mm/s"), [
            {"name": "S1", "condition": "2 mm/s", "control": False},
            {"name": "S2", "condition": "4 mm/s", "control": False},
        ])
        self.assertEqual(er.samples_in_text("시료 3개에 대조군 1개"), [
            {"name": "1번 시료", "condition": "", "control": False},
            {"name": "2번 시료", "condition": "", "control": False},
            {"name": "3번 시료", "condition": "", "control": False},
            {"name": "대조군", "condition": "", "control": True},
        ])
        self.assertEqual(er.samples_in_text("1~3번 샘플은 약물, 4번은 음성 대조군"), [
            {"name": "1번 샘플", "condition": "약물", "control": False},
            {"name": "2번 샘플", "condition": "약물", "control": False},
            {"name": "3번 샘플", "condition": "약물", "control": False},
            {"name": "4번 샘플", "condition": "음성 대조군", "control": True},
        ])
        self.assertEqual(er.samples_in_text("오늘은 생존율을 보는 실험이에요"), [])

    def test_a_record_names_the_sample_it_speaks_of(self) -> None:
        self.assertEqual(er.sample_in_text("3번 샘플에서 세포 밀도가 낮게 관찰됨"), "3번 샘플")
        self.assertEqual(er.sample_in_text("샘플 2번 생존율 41%"), "2번 샘플")
        self.assertEqual(er.sample_in_text("S2는 가장자리가 뿌옇게 보여"), "S2")
        self.assertEqual(er.sample_in_text("대조군은 정상"), "대조군")
        self.assertEqual(er.sample_in_text("조각 크기 1 mm"), "")


class ConditionTests(_Case):
    def test_steps_done_as_the_source_says_are_one_row_and_a_difference_its_own(self) -> None:
        text = self.markdown(deviation_run(self.temporary.name))
        self.assertEqual(self.table(text, "| 과정 | 원문 값 → 실제 |"), [
            "| 2단계 · Gel band destaining | 온도 37°C → 40°C (“40도에서 씻었어”) · 그 밖 원문대로 (500 µL · 10 min) |",
            "| 3단계 · Gel band destaining | 시간 15 min → 10분에 끝냄 (타이머 일찍 끝냄) · 그 밖 원문대로 (37°C) |",
            "| 4단계 · Dehydration | 원문대로 — 200 µL |",
        ])
        # Only what the source names in a performed step: the thermomixer is
        # listed but no step text names it.
        self.assertIn("> 쓴 재료·장비: Acetonitrile", self.section(text, "## 부록"))
        text = self.markdown(cell_run(self.temporary.name), cell_protocol(), start_check=None)
        # Named as the source lists them, without the company and catalog words.
        self.assertIn("> 쓴 재료·장비: A549 cells · MTT reagent · DMSO · 96-well plate", self.section(text, "## 부록"))
        self.assertNotIn("Thermomixer", self.section(text, "## 부록"))

    def test_consecutive_steps_as_the_source_says_are_grouped(self) -> None:
        text = self.markdown(short_run(self.temporary.name), protocol_double(labels=("1", "2")), start_check=None)
        self.assertEqual(self.table(text, "| 과정 | 원문 값 → 실제 |"), [
            "| 2단계 · Gel band destaining | 원문대로 — 500 µL · 37°C · 10 min |",
        ])
        text = self.markdown(cell_run(self.temporary.name), cell_protocol(), start_check=None)
        rows = self.table(text, "| 과정 | 원문 값 → 실제 |")
        self.assertEqual(rows[0], "| 1–2단계 · Seeding | 원문대로 — 1단계 100 µL / 2단계 37°C · 24 h |")
        self.assertEqual(rows[1], "| 3단계 · Treatment | 온도 37°C → 40°C (“항온조가 안 내려가서 40도에서 넣었어”)"
                                  " · 그 밖 원문대로 (0.5 µM · 5 µM · 0.1%) |")

    def test_a_measurement_that_differs_from_the_source_is_the_actual_value(self) -> None:
        # Lane RA's comparison, applied to the record: a measured value of the
        # source's kind that differs is what was actually used.
        text = self.markdown(unconfirmed_run(self.temporary.name), start_check=None)
        rows = self.table(text, "| 과정 | 원문 값 → 실제 |")
        self.assertEqual(rows[0], "| 2단계 · Gel band destaining | 양 500 µL → 450 µL (“세척액 450 µL”) [미확인]"
                                  " · 그 밖 원문대로 (37°C · 10 min) |")
        self.assertEqual(rows[1], "| 3단계 · Gel band destaining | 원문대로 — 37°C · 15 min |")
        self.assertEqual(rows[2], "| 4단계 · Dehydration | 양 200 µL → 180 µL (“acetonitrile 180 µL 넣음”) [미확인]"
                                  " (끝내지 않음) |")

    def test_a_return_the_source_states_is_a_repeat_row_not_a_difference(self) -> None:
        record = short_run(self.temporary.name)
        record.event("00:48", "repeat_returned", "1", payload={"step_record": {
            "kind": "repeat_return", "repeated_step_labels": ["1", "2"], "stated_at_step": "2",
            "from_step": "2", "to_step": "1", "round": 2, "round_counted_from": "confirmed_returns",
            "source_text": "Repeat steps 1-2 until the gel is clear."}})
        record.event("00:50", "observation", "2", user_wording="세척을 두 번 반복했어", category="appearance")
        record.event("00:55", "step_completed", "2", payload={"step_record": {
            "kind": "repeat_round_completion", "round": 2}})
        text = self.markdown(record, protocol_double(labels=("1", "2")), start_check=None)
        rows = self.table(text, "| 과정 | 원문 값 → 실제 |")
        self.assertIn("| 1–2단계 반복 (2단계 원문: “Repeat steps 1-2 until the gel is clear.”) |"
                      " 2회차까지 함 (말로 확인한 돌아가기 기준) |", rows)
        self.assertNotIn("원문과 다르게 한 점·문제", text)

    def test_without_the_source_the_conditions_say_so(self) -> None:
        record = short_run(self.temporary.name)
        text = record.store.export_markdown(record.report_id, fixture=None, start_check=None, previous=None).decode()
        self.assertIn("> 프로토콜 원문을 불러오지 못해 원문 값을 적지 못했다.", self.section(text, "## 방법"))


class ObservationTests(_Case):
    def test_records_are_grouped_by_step_with_their_sample(self) -> None:
        text = self.markdown(cell_run(self.temporary.name), cell_protocol(), start_check=None)
        self.assertEqual(self.table(text, "| 과정 | 시료 | 관찰·측정 (기록 그대로) | 시각 |"), [
            "| 1단계 · Seeding | — | well 당 5000 cells 심었어 | 09:40 |",
            "| 5단계 · Read-out | 3번 샘플 | 3번 샘플에서 세포 밀도가 예상보다 낮게 관찰됨 | 10:20 |",
            "| 6단계 · Read-out | 1번 샘플 | 1번 샘플 생존율 82% [정정됨]<br>처음 기록: 1번 샘플 생존율 72% | 12:50 |",
            "| 6단계 · Read-out | 2번 샘플 | 2번 샘플 생존율 41% | 12:51 |",
            "| 6단계 · Read-out | — | 사진: 570 nm 판독 화면 | 12:53 |",
        ])
        section = self.section(text, "## 관찰·특이사항")
        self.assertEqual(self.section(text, "## 결과").strip(), "처리군 생존율이 대조군보다 낮았어.")
        # A deviation and an anomaly are section 4's, not observations.
        self.assertNotIn("40도에서", section)

    def test_unconfirmed_values_keep_their_mark_and_the_line_under_the_title(self) -> None:
        text = self.markdown(unconfirmed_run(self.temporary.name), start_check=None)
        self.assertIn("> **확인 안 된 값 2개** — ", text)
        self.assertIn("| 2단계 · Gel band destaining | — | 세척액 450 µL [미확인] | 09:40 |", text)
        self.assertIn("| 4단계 · Dehydration | — | acetonitrile 180 µL 넣음 [미확인] | 10:04 |", text)

    def test_no_observation_and_no_result_are_said_so(self) -> None:
        text = self.markdown(short_run(self.temporary.name), protocol_double(labels=("1", "2")), start_check=None)
        section = self.section(text, "## 관찰·특이사항")
        self.assertIn("| 1단계 · Gel band destaining | — | 조각 크기 1 mm | 09:33 |", section)
        self.assertEqual(self.section(text, "## 결과").strip(), "> 기록 없음")
        record = headspace_run(self.temporary.name)
        text = self.markdown(record, headspace_protocol(), start_check=None)
        self.assertIn("| 1단계 · Materials to autoclave before experiment | — | LB 배지가 맑고 덩어리 없이 다 녹았어 | 09:42 |", text)

    def test_the_result_and_the_next_change_are_split_from_one_utterance(self) -> None:
        self.assertEqual(er.split_result_and_next("처리군 생존율이 대조군보다 낮았어. 다음에는 5 µM 농도를 2.5로 낮춰 볼게"),
                         ("처리군 생존율이 대조군보다 낮았어.", "5 µM 농도를 2.5로 낮춰 볼게"))
        self.assertEqual(er.split_result_and_next("다음엔 세척을 한 번 더 할게"), ("", "세척을 한 번 더 할게"))
        self.assertEqual(er.split_result_and_next("밴드가 깨끗하게 나왔어"), ("밴드가 깨끗하게 나왔어", ""))


class DifferenceTests(_Case):
    def test_deviations_timers_ended_early_and_anomalies_with_their_words(self) -> None:
        text = self.markdown(deviation_run(self.temporary.name))
        section = self.section(text, "### 원문과 다르게 한 점·문제")
        self.assertEqual([line for line in section.splitlines() if line.startswith("- ")], [
            "- 2단계 온도: 원문 37°C → 40°C — “40도에서 씻었어”",
            "- 3단계 타이머: 원문 15분 중 10분에 끝냄",
            "- 이상 · 4단계 (10:00): “튜브를 쏟았어”",
        ])

    def test_a_later_start_a_stop_and_an_unconfirmed_measurement(self) -> None:
        record = unconfirmed_run(self.temporary.name)
        text = self.markdown(record, start_check=None)
        section = self.section(text, "### 원문과 다르게 한 점·문제")
        self.assertEqual([line for line in section.splitlines() if line.startswith("- ")], [
            "- 2단계 양: 원문 500 µL → 450 µL — “세척액 450 µL” [미확인]",
            "- 4단계 양: 원문 200 µL → 180 µL — “acetonitrile 180 µL 넣음” [미확인]",
            "- 4단계 진행 중 종료 — 5단계는 하지 않음",
        ])
        later = short_run(self.temporary.name)
        later.event("00:48", "steps_skipped", "2", payload={"step_record": {
            "kind": "start_at_step", "start_step": "2", "skipped_step_labels": ["1"]}})
        text = self.markdown(later, protocol_double(labels=("1", "2")), start_check=None)
        self.assertIn("- 2단계부터 시작 — 1단계 건너뜀", self.section(text, "### 원문과 다르게 한 점·문제"))

    def test_the_coating_run_lists_the_anomaly_and_the_stop(self) -> None:
        text = self.markdown(coating_run(self.temporary.name), coating_protocol(), start_check=None)
        section = self.section(text, "### 원문과 다르게 한 점·문제")
        self.assertEqual([line for line in section.splitlines() if line.startswith("- ")], [
            "- 3단계 속도: 원문 2 mm/s → 4 mm/s — “S2는 4 mm/s로 꺼냈어” [미확인]",
            "- 이상 · 5단계 (11:30): “로 온도가 380도에서 안 올라가” [미확인]",
            "- 5단계 진행 중 종료 — 6단계는 하지 않음",
        ])


class PreviousRunTests(_Case):
    def test_the_first_run_of_a_protocol_says_so(self) -> None:
        text = self.markdown(deviation_run(self.temporary.name))
        self.assertIn("> 처음 실험 — 같은 프로토콜의 앞선 기록이 없다.",
                      self.section(text, "### 같은 프로토콜의 앞선 내 기록"))

    def test_the_run_before_is_found_in_the_store_and_compared(self) -> None:
        record = cell_run(self.temporary.name)
        earlier = earlier_cell_run(self.temporary.name, record.store)
        previous = record.store.previous_finished_report(record.doc())
        self.assertEqual(previous["report_id"], earlier.report_id)
        self.assertIsNone(record.store.previous_finished_report(earlier.doc()))
        text = self.markdown(record, cell_protocol(), start_check=None)
        section = self.section(text, "### 같은 프로토콜의 앞선 내 기록")
        self.assertIn("> 지난번: 2026년 10월 19일 (월) 09:30 · 이실험 · 끝까지 완료 (6 / 6단계)", section)
        self.assertEqual([line for line in section.splitlines() if line.startswith("- ")], [
            "- 목적: 지난번 “A549 세포에 약물 X 5 µM 처리 뒤 생존율, 시료 2개에 대조군 1개” → 이번 “A549 세포에 약물 X를 0.5랑 5 µM로 처리해서 생존율을 보는 실험”",
            "- 시료: 지난번 1번 샘플 · 2번 샘플 · 대조군 → 이번 1번 샘플 · 2번 샘플 · 3번 샘플",
            "- 3단계 온도: 지난번 원문대로 (37°C) → 이번 40°C",
            "- 5단계 시간: 지난번 원문대로 (3 h) → 이번 2시간 36분에 끝냄",
            "- 5단계 관찰: 이번에만 기록 — “3번 샘플에서 세포 밀도가 예상보다 낮게 관찰됨”",
            "- 6단계 관찰: 지난번 “1번 샘플 생존율 65%” → 이번 “1번 샘플 생존율 82%” · “2번 샘플 생존율 41%” · “사진: 570 nm 판독 화면”",
            "- 결과: 지난번 “생존율이 65%로 예상 범위였어” → 이번 “처리군 생존율이 대조군보다 낮았어.”",
        ])

    def test_a_run_like_the_one_before_says_so(self) -> None:
        first = short_run(self.temporary.name)
        # The same flow a day later, in the same store.
        from tests.lane_rp3_support import RecordWriter
        later = RecordWriter(self.temporary.name, session="session-rp2-short-2", title="Short wash protocol",
                             protocol_id="protocol-rp2", day="2026-10-20", store=first.store,
                             revision="rev-test-1", prefix="p-step-0")
        later.event("00:29", "experimenter_recorded", None, payload={"display_name": "이실험"})
        later.event("00:30", "session_started", "1")
        later.event("00:33", "observation", "1", user_wording="조각 크기 1 mm", category="measurement",
                    payload={"experimenter_confirmed": True})
        later.event("00:36", "step_completed", "1")
        later.event("00:47", "step_completed", "2")
        later.event("00:47", "workflow_completed", "2")
        later.finalize("00:47", "completed")
        text = self.markdown(later, protocol_double(labels=("1", "2")), start_check=None)
        section = self.section(text, "### 같은 프로토콜의 앞선 내 기록")
        self.assertIn("> 지난번: 2026년 10월 19일 (월) 09:30 · 이실험 · 끝까지 완료 (2 / 2단계)", section)
        self.assertIn("> 지난번과 조건·관찰이 같다.", section)


class NextAndConfirmationTests(_Case):
    def test_what_to_change_next_and_the_sources_next_step(self) -> None:
        text = self.markdown(cell_run(self.temporary.name), cell_protocol(), start_check=None)
        self.assertIn("| 다음 할 일 | 5 µM 농도를 2.5로 낮춰 볼게 |",
                      self.section(text, "## 해석·다음 할 일"))
        text = self.markdown(coating_run(self.temporary.name), coating_protocol(), start_check=None)
        self.assertEqual(self.section(text, "## 해석·다음 할 일").strip(), "> 기록 없음")
        self.assertNotIn("5단계부터 이어서 한다 (원문의 다음 단계)", text)
        text = self.markdown(deviation_run(self.temporary.name))
        self.assertEqual(self.section(text, "## 해석·다음 할 일").strip(), "> 기록 없음")

    def test_the_confirmation_box_has_a_row_for_the_experimenter_and_the_pi(self) -> None:
        record = deviation_run(self.temporary.name)
        text = self.markdown(record)
        self.assertIn("### 확인 기록\n\n> 기록 없음\n\n### 단계 시각표", text)
        # Decision ④'s event, read when it is there: name, time, one line.
        record.event("02:00", "pi_confirmed", None, payload={"name": "김교수", "comment": "온도 편차 확인함, 다음엔 항온조 먼저"})
        text = self.markdown(record)
        self.assertEqual(self.table(text, "| 확인 | 이름 | 날짜·시각 | 서명·한 줄 의견 |"), [
            "| PI | 김교수 | 2026년 10월 19일 11:00 | 온도 편차 확인함, 다음엔 항온조 먼저 |",
        ])
        self.assertNotIn("아직 없음", text)


class AppendixTests(_Case):
    def test_the_step_times_the_words_as_said_and_one_line_of_system_events(self) -> None:
        text = self.markdown(deviation_run(self.temporary.name))
        appendix = self.section(text, "## 부록")
        self.assertEqual(self.table(appendix, "| 단계 | 시작 | 끝 | 소요 | 타이머 (원문 / 실제) |"), [
            "| 1 | 09:30 | 09:36 | 6분 | — |",
            "| 2 | 09:36 | 09:47 | 11분 | — |",
            "| 3 | 09:47 | 09:58 | 11분 | 원문 15분 / 10분에 끝냄 |",
            "| 4 | 09:58 | 10:05 | 7분 | — |",
            "| 5 | 10:05 | 10:10 | 5분 | — |",
        ])
        self.assertEqual(self.table(appendix, "| 시각 | 단계 | 종류 | 말한 그대로 |"), [
            "| 09:34 | 1 | 측정 | 조각 크기 2 mm → 10:02 정정: 조각 크기 1 mm |",
            "| 09:40 | 2 | 편차 | 40도에서 씻었어 |",
            "| 10:00 | 4 | 이상 | 튜브를 쏟았어 |",
        ])
        self.assertIn("### 검토본 v2 · 김검토 · 2026-10-17", appendix)
        self.assertIn("> 검토자가 원문에서 바꾼 값이 없다.", appendix)
        self.assertIn("> 시스템 사건: 연결 끊김 1회 (3단계 09:50–09:52, 1분 30초) · 타이머 알림 0회 · "
                      "시작 전 확인 3답 (맞음 1 · 다르게 고름 1 · 원문대로 맞다고 함 1) · 안전 주의 2건 보고 시작 · "
                      "멈춤 0회", appendix)
        self.assertIn("> 출처: [1] Kim 2025. Test digestion protocol. protocols.io — 실험에 쓴 프로토콜 원문", appendix)
        self.assertRegex(text.rstrip().splitlines()[-1],
                         r"^> 이 실험 노트의 문장은 서버가 실험 기록과 프로토콜 원문에서 만들었다\. 모델이 쓴 문장은 포함하지 않았다\. "
                         r"작성 \d{4}년 \d{1,2}월 \d{1,2}일 \d{2}:\d{2}\.$")

    def test_the_sayings_of_the_run_and_the_notices_are_in_the_appendix(self) -> None:
        text = self.markdown(coating_run(self.temporary.name), coating_protocol(), start_check=())
        appendix = self.section(text, "## 부록")
        rows = self.table(appendix, "| 시각 | 단계 | 종류 | 말한 그대로 |")
        self.assertEqual(rows[0], "| 09:30 | 1 | 목적·시료 | 졸겔 코팅 두께가 인출 속도에 따라 어떻게 달라지는지 보려고요."
                                  " 시편 S1은 2 mm/s, S2는 4 mm/s |")
        self.assertIn("> 시스템 사건: 연결 끊김 0회 · 타이머 알림 2회 (말함 1 · 화면 1) · 시작 전 확인 기록 없음 · "
                      "안전 주의 1건 보고 시작 · 멈춤 0회", appendix)
        self.assertIn("| 4 | 11:12 | 11:25 | 13분 | 원문 10분 / 실제 12분 |", appendix)


class WordTests(_Case):
    def test_word_has_the_same_tables_with_widths_and_a_box_to_sign(self) -> None:
        from docx import Document
        from docx.oxml.ns import qn

        record = cell_run(self.temporary.name)
        document = Document(io.BytesIO(record.store.export_docx(
            record.report_id, fixture=cell_protocol(), start_check=None)))
        headers = [tuple(cell.text for cell in table.rows[0].cells) for table in document.tables]
        self.assertEqual(headers, [
            ("항목", "내용"), er.SAMPLE_HEADER, er.METHOD_HEADER, er.CONDITION_HEADER,
            er.VALUE_BAR_HEADER, er.TIMELINE_HEADER, er.OBSERVATION_HEADER,
            ("항목", "내용"), er.DOMAIN_HEADER, er.INVENTORY_HEADER,
            er.APPENDIX_STEP_HEADER, er.APPENDIX_RECORD_HEADER,
        ])
        for table in document.tables:
            header = tuple(cell.text for cell in table.rows[0].cells)
            shares = er.TABLE_WIDTHS[header]
            grid = [column.width for column in table.columns]
            for width, share in zip(grid, shares):
                self.assertAlmostEqual(width / sum(grid), share, places=2, msg=header)
            self.assertEqual(table._tbl.tblPr.find(qn("w:tblLayout")).get(qn("w:type")), "fixed")
        # The appendix is in smaller type than the body.
        sizes = {run.font.size.pt for table in document.tables[10:] for row in table.rows[1:]
                 for cell in row.cells for paragraph in cell.paragraphs for run in paragraph.runs if run.font.size}
        self.assertEqual(sizes, {8.0})
        text = "\n".join(paragraph.text for paragraph in document.paragraphs)
        self.assertIn("A549 세포에 약물 X를 0.5랑 5 µM로 처리해서 생존율을 보는 실험", text)
        self.assertIn("확인 기록", text)

    def test_every_notebook_table_has_widths_that_sum_to_one(self) -> None:
        for header in (er.SAMPLE_HEADER, er.CONDITION_HEADER, er.OBSERVATION_HEADER, er.CONFIRM_HEADER,
                       er.APPENDIX_STEP_HEADER, er.APPENDIX_RECORD_HEADER, er.REVIEW_CHANGE_HEADER,
                       er.METHOD_HEADER, er.TIMELINE_HEADER, er.VALUE_BAR_HEADER,
                       er.DOMAIN_HEADER, er.SAMPLE_RECORD_HEADER, er.INVENTORY_HEADER):
            self.assertEqual(len(er.TABLE_WIDTHS[header]), len(header), header)
            self.assertAlmostEqual(sum(er.TABLE_WIDTHS[header]), 1.0, places=6, msg=header)


if __name__ == "__main__":
    unittest.main()
