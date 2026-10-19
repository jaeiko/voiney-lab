"""Values not confirmed, said at the top and marked where they stand
(lane RP2, decision 3, 2026-10-19).

Human decision: when the report holds values the experimenter has not
confirmed, its first line after the title says "확인 안 된 값 n개" and each
such value carries a mark in the tables. The export is not held back.
"""

from __future__ import annotations

import io
import unittest

from voiney_lab import experiment_reports as er

from tests.lane_rp2_support import (
    START_CHECK, deviation_run, environment, protocol_double, short_run, temporary, unconfirmed_run,
)

STEP_HEADER = "| 단계 | 시작 | 끝 | 소요 | 기록한 값 | 편차 (원문 → 기록) | 이상 |"


class _Case(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = temporary()
        self.env = environment()
        self.env.start()
        er._NARRATIVE_CACHE.clear()

    def tearDown(self) -> None:
        self.env.stop()
        self.temporary.cleanup()

    def markdown(self, record, fixture=None) -> str:
        return record.store.export_markdown(
            record.report_id, fixture=fixture or protocol_double(), start_check=START_CHECK).decode()


class TopLineTests(_Case):
    def test_the_line_under_the_title_counts_and_lists_them(self) -> None:
        lines = self.markdown(unconfirmed_run(self.temporary.name)).splitlines()
        self.assertEqual(lines[0], "# Test digestion protocol 실험 보고서")
        self.assertEqual(lines[2], f"> **확인 안 된 값 2개** — 실험자가 아직 확인하지 않은 값이다. "
                                   f"표에 {er.UNCONFIRMED_MARK}로 표시했다. 화면의 확인 목록에서 확인하면 빠진다.")
        self.assertEqual(lines[4:6], ["- 2단계 측정: 세척액 450 µL", "- 4단계 측정: acetonitrile 180 µL 넣음"])
        self.assertEqual(lines[7], "| 항목 | 내용 |")

    def test_no_line_when_every_value_was_confirmed(self) -> None:
        text = self.markdown(deviation_run(self.temporary.name))
        self.assertNotIn("확인 안 된 값", text)
        self.assertNotIn(er.UNCONFIRMED_MARK, text)

    def test_a_value_never_reviewed_is_unconfirmed_too(self) -> None:
        record = short_run(self.temporary.name)
        record.event("00:50", "observation", "2", user_wording="세척액 500 µL", category="measurement")
        text = self.markdown(record, protocol_double(labels=("1", "2")))
        self.assertIn("> **확인 안 된 값 1개**", text)
        self.assertIn("| 2 | 09:36 | 09:47 | 11분 | 측정: 세척액 500 µL [미확인] | — | — |", text)

    def test_a_correction_after_a_confirmation_takes_it_back(self) -> None:
        record = deviation_run(self.temporary.name)
        record.event("01:20", "record_corrected", "2", payload={"record_fix": {
            "target_event_key": "turn-6", "text_before": "40도에서 씻었어", "text_after": "39도에서 씻었어",
            "source": "voice"}})
        text = self.markdown(record)
        self.assertIn("> **확인 안 된 값 1개**", text)
        self.assertIn("- 2단계 편차: 39도에서 씻었어", text)
        self.assertIn("원문 37°C → “39도에서 씻었어” (정정됨) [미확인]", text)


class MarkTests(_Case):
    def test_each_unconfirmed_value_is_marked_in_the_step_table(self) -> None:
        text = self.markdown(unconfirmed_run(self.temporary.name))
        lines = text.splitlines()
        rows = lines[lines.index(STEP_HEADER) + 2:lines.index(STEP_HEADER) + 7]
        self.assertEqual(rows[1], "| 2 | 09:36 | 09:44 | 8분 | 측정: 세척액 450 µL [미확인] | — | — |")
        self.assertEqual(rows[3], "| 4 | 10:01 | 완료 기록 없음 | — | 측정: acetonitrile 180 µL 넣음 [미확인] | — | — |")

    def test_unconfirmed_deviations_and_anomalies_are_marked_in_both_tables(self) -> None:
        record = short_run(self.temporary.name)
        record.event("00:52", "observation", "2", user_wording="40도에서 씻었어", category="deviation",
                     payload={"deviations": [{"kind": "temperature", "said": "40°C", "number": "40",
                                              "unit": "°C", "source": "37°C"}]})
        record.event("00:53", "anomaly", "2", user_wording="튜브를 쏟았어", category="spill_exposure_safety_event")
        text = self.markdown(record, protocol_double(labels=("1", "2")))
        self.assertIn("| 2 | 09:36 | 09:47 | 11분 | — | 37°C → 40°C [미확인] | 튜브를 쏟았어 [미확인] |", text)
        self.assertIn("| 1 | 2 | 편차 | 37°C · 2쪽 | 40도에서 씻었어 | 09:52 | 미확인 |", text)
        self.assertIn("| 2 | 2 | 이상 | 2쪽 | 튜브를 쏟았어 | 09:53 | 미확인 |", text)
        self.assertIn("> **확인 안 된 값 2개**", text)


class WordTests(_Case):
    def test_word_puts_the_line_first_in_bold_and_still_exports(self) -> None:
        from docx import Document

        record = unconfirmed_run(self.temporary.name)
        document = Document(io.BytesIO(record.store.export_docx(
            record.report_id, fixture=protocol_double(), start_check=START_CHECK)))
        paragraphs = document.paragraphs
        self.assertEqual(paragraphs[0].text, "Test digestion protocol 실험 보고서")
        self.assertTrue(paragraphs[1].text.startswith("확인 안 된 값 2개 — "))
        self.assertTrue(paragraphs[1].runs[0].bold)
        self.assertEqual(paragraphs[2].text, "• 2단계 측정: 세척액 450 µL")


if __name__ == "__main__":
    unittest.main()
