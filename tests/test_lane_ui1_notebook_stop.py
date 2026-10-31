"""How the notebook names the step a stopped run ended in (lane UI1, item 4).

Person's test of 2026-10-31: a run stopped while step 3 was going read
"3단계에서 끝냄 — 3–25단계는 하지 않음" under "원문과 다르게 한 점" -- wrong,
step 3 was begun. A step the run stopped in without completing it is "진행 중
종료"; the steps not done are the ones after it. A stop step that was completed
keeps "에서 끝냄". The same sentence in every projection (the screen's notebook,
Word, PDF, which all read one notebook) and in the report's facts. Checked on a
biology run (in-gel digestion double) and a materials run (sol-gel coating).
"""

from __future__ import annotations

import io
import unittest

from voiney_lab import experiment_reports as er

from tests.lane_rp2_support import protocol_double, unconfirmed_run
from tests.lane_rp3_support import coating_protocol, coating_run, environment, temporary


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

    @staticmethod
    def differences(text: str) -> list[str]:
        lines = text.splitlines()
        start = lines.index("### 원문과 다르게 한 점·문제") + 1
        end = next((i for i in range(start, len(lines)) if lines[i].startswith("#")), len(lines))
        return [line for line in lines[start:end] if line.startswith("- ")]


class StoppedInAStepTests(_Case):
    def test_a_step_begun_and_not_completed_is_ended_in_progress_and_not_counted_as_not_done(self) -> None:
        record = unconfirmed_run(self.temporary.name)
        text = record.store.export_markdown(record.report_id, fixture=protocol_double(), start_check=None).decode()
        self.assertIn("- 4단계 진행 중 종료 — 5단계는 하지 않음", self.differences(text))
        self.assertNotIn("4단계에서 끝냄", text)
        self.assertNotIn("4–5단계는 하지 않음", text)

    def test_a_materials_run_says_the_same(self) -> None:
        record = coating_run(self.temporary.name)
        text = record.store.export_markdown(record.report_id, fixture=coating_protocol(), start_check=None).decode()
        self.assertIn("- 5단계 진행 중 종료 — 6단계는 하지 않음", self.differences(text))
        self.assertNotIn("5–6단계는 하지 않음", text)

    def test_the_word_file_and_the_screen_notebook_carry_the_same_sentence(self) -> None:
        record = coating_run(self.temporary.name)
        # The screen's notebook is the route's projection: the narrative's facts (GET .../notebook).
        notebook = record.store._narrative(record.report_id, None, coating_protocol(), None).facts.notebook
        self.assertIn("5단계 진행 중 종료 — 6단계는 하지 않음", notebook.differences)
        from docx import Document

        document = Document(io.BytesIO(record.store.export_docx(record.report_id, fixture=coating_protocol(), start_check=None)))
        words = "\n".join(paragraph.text for paragraph in document.paragraphs)
        self.assertIn("5단계 진행 중 종료 — 6단계는 하지 않음", words)
        self.assertNotIn("5단계에서 끝냄", words)

    def test_the_report_facts_name_the_step_in_progress_too(self) -> None:
        record = coating_run(self.temporary.name)
        facts = record.store._narrative(record.report_id, None, coating_protocol(), None).facts
        self.assertIn("5단계 진행 중 실험을 끝내 6단계는 수행하지 않았다.", facts.deviations)

    def test_a_stop_in_the_last_step_names_no_steps_left(self) -> None:
        self.assertEqual(er._stopped_line("6", ["6"], completed=("1", "2", "3", "4", "5")), "6단계 진행 중 종료")

    def test_a_stop_step_already_completed_keeps_ended_at(self) -> None:
        self.assertEqual(er._stopped_line("3", ["4", "5"], completed=("1", "2", "3")), "3단계에서 끝냄 — 4–5단계는 하지 않음")
        self.assertEqual(er._stopped_line(None, ["4", "5"], completed=("1", "2", "3")), "실험을 끝냄 — 4–5단계는 하지 않음")


if __name__ == "__main__":
    unittest.main()
