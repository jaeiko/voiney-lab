"""AI-written notes in the lab notebook, each checked, and the two new exports
(lane RP3, decision ③, 2026-10-20).

Human decision: the observation sentences and one result paragraph are
written by the report model (the analysis provider, Gemini 3.8 Flash, one
call per experiment as lane RP arranged), every number and unit in a
sentence must be in the record or the source, each sentence that passes is
marked "AI 정리", one that fails is dropped (the record's own words stand),
and VOINEY_LAB_REPORT_WRITER_ENABLED=false turns it off. Two exports are
added: a one-page lab-meeting summary (Word, PDF) and a results table
(.xlsx). Provider calls are a fake client only.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import unittest
from types import SimpleNamespace
from unittest import mock

from voiney_lab import experiment_reports as er

from tests.lane_rp2_support import START_CHECK, deviation_run, protocol_double
from tests.test_lane_rp2_report_pdf import _FakeOffice
from tests.lane_rp3_support import cell_protocol, cell_run, earlier_cell_run, environment, temporary


class _Client:
    """A fake chat client: one JSON reply, every call kept."""

    def __init__(self, reply) -> None:
        self.calls: list[dict] = []
        self.reply = reply

        async def create(**kwargs):
            self.calls.append(kwargs)
            content = self.reply if isinstance(self.reply, str) else json.dumps(self.reply, ensure_ascii=False)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
                                   usage={"prompt_tokens": 10, "completion_tokens": 5})

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


GOOD = {
    "observation_notes": [
        {"단계": "6", "시료": "1번 샘플", "문장": "1번 샘플 생존율 82%, 2번 샘플 41%로 처리군이 대조군보다 낮았다."},
        {"단계": "5", "시료": "3번 샘플", "문장": "3번 샘플(대조군)의 세포 밀도가 예상보다 낮았다."},
    ],
    "result_summary": "약물 X 0.5 µM과 5 µM 처리군의 생존율이 대조군보다 낮았고, 3단계는 40°C에서 넣었다.",
}


class _Case(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = temporary()
        self.addCleanup(self.temporary.cleanup)
        self.env = environment()
        self.env.start()
        self.addCleanup(self.env.stop)
        er._NARRATIVE_CACHE.clear()
        self.record = cell_run(self.temporary.name)
        self.fixture = cell_protocol()

    def facts(self) -> er.ReportFacts:
        return er.ReportWriterBrain.facts_for(self.record.doc(), fixture=self.fixture)

    def write(self, reply) -> tuple[er.ReportNarrative, _Client]:
        er._NARRATIVE_CACHE.clear()
        client = _Client(reply)
        brain = er.ReportWriterBrain(client=client, model="fake-model", timeout_seconds=5)
        doc = self.record.doc()
        return asyncio.run(brain.generate_narrative(doc, list(doc["events"]), fixture=self.fixture)), client


class WriterFactsTests(_Case):
    def test_the_model_reads_the_records_the_samples_and_the_source_values_only(self) -> None:
        _, client = self.write(GOOD)
        self.assertEqual(len(client.calls), 1)
        sent = json.loads(client.calls[0]["messages"][1]["content"])
        self.assertEqual(sent["목적"], "A549 세포에 약물 X를 0.5랑 5 µM로 처리해서 생존율을 보는 실험")
        self.assertEqual([s["이름"] for s in sent["시료"]], ["1번 샘플", "2번 샘플", "3번 샘플"])
        rows = {(r["단계"], r["기록"]) for r in sent["기록"]}
        self.assertIn(("6", "1번 샘플 생존율 82%"), rows)
        self.assertIn(("5", "3번 샘플에서 세포 밀도가 예상보다 낮게 관찰됨"), rows)
        self.assertEqual(sent["결과 (실험자가 말함)"], "처리군 생존율이 대조군보다 낮았어.")
        self.assertEqual(sent["다음에 바꿀 점 (실험자가 말함)"], "5 µM 농도를 2.5로 낮춰 볼게")
        self.assertIn("3단계 온도: 원문 37°C → 40°C", json.dumps(sent, ensure_ascii=False))
        for identifier in (self.record.report_id, "session-rp3-cell", "rev-rp3-1", "completed", "step_completed"):
            self.assertNotIn(identifier, client.calls[0]["messages"][1]["content"])
        instructions = client.calls[0]["messages"][0]["content"]
        self.assertIn("observation_notes", instructions)
        self.assertIn("result_summary", instructions)
        for gone in ("purpose", "background", "item_uses", "discussion_review", "next_steps"):
            self.assertNotIn(gone, instructions)


class CheckTests(_Case):
    def test_sentences_whose_numbers_are_in_the_record_or_source_pass_and_are_marked(self) -> None:
        narrative, _ = self.write(GOOD)
        self.assertEqual([(note.step_label, note.sample, note.text) for note in narrative.ai_notes], [
            ("6", "1번 샘플", "1번 샘플 생존율 82%, 2번 샘플 41%로 처리군이 대조군보다 낮았다."),
            ("5", "3번 샘플", "3번 샘플(대조군)의 세포 밀도가 예상보다 낮았다."),
        ])
        self.assertEqual(narrative.ai_result, GOOD["result_summary"])
        self.assertEqual(narrative.ai_rejected, ())
        text = er.render_markdown(narrative)
        section = text.split("## 관찰·특이사항")[1].split("## 결과")[0]
        self.assertNotIn("AI 정리", section)
        self.assertIn("처리군 생존율이 대조군보다 낮았어.", text.split("## 결과")[1].split("## 해석")[0])
        self.assertNotIn(GOOD["result_summary"], text)
        self.assertTrue(text.rstrip().splitlines()[-1].startswith(
            "> 이 실험 노트의 문장은 서버가 실험 기록과 프로토콜 원문에서 만들었다. "
            "모델이 쓴 문장은 포함하지 않았다. 작성 "),
            text[-300:])

    def test_a_sentence_with_a_value_not_in_the_record_or_source_is_dropped(self) -> None:
        narrative, _ = self.write({
            "observation_notes": [
                {"단계": "6", "시료": "1번 샘플", "문장": "1번 샘플 생존율 0.82로 낮았다."},
                {"단계": "6", "시료": "2번 샘플", "문장": "2번 샘플 생존율 41%였다."},
                {"단계": "1", "시료": "", "문장": "well 당 5000 cells를 심었고 배지는 150 µL 넣었다."},
                {"단계": "9", "시료": "", "문장": "9단계는 없다."},
                {"단계": "6", "시료": "", "문장": ""},
                {"단계": "6", "시료": "", "문장": "장갑을 반드시 끼고 주의해서 다룬다."},
            ],
            "result_summary": "생존율이 77%로 예상 범위였다.",
        })
        self.assertEqual([note.text for note in narrative.ai_notes], ["2번 샘플 생존율 41%였다."])
        self.assertEqual(narrative.ai_result, "")
        self.assertEqual(narrative.ai_rejected, (
            ("1번 샘플 생존율 0.82로 낮았다.", "기록·원문에 없는 숫자 0.82"),
            ("well 당 5000 cells를 심었고 배지는 150 µL 넣었다.", "기록·원문에 없는 값 150 µL"),
            ("9단계는 없다.", "기록에 없는 단계 9"),
            ("", "빈 문장"),
            ("장갑을 반드시 끼고 주의해서 다룬다.", "안전 지시 “주의”"),
            ("생존율이 77%로 예상 범위였다.", "기록·원문에 없는 값 77%"),
        ))
        text = er.render_markdown(narrative)
        self.assertNotIn("0.82", text)
        self.assertNotIn("77%", text)
        self.assertNotIn("2번 샘플 생존율 41%였다.", text)
        self.assertNotIn("결과 (AI 정리)", text)
        self.assertIn("모델이 쓴 문장은 포함하지 않았다.", text)

    def test_an_unconfirmed_value_may_not_be_stated(self) -> None:
        # The run's values were confirmed but one: a note built on it is dropped.
        self.record.event("04:00", "observation", "6", user_wording="3번 샘플 생존율 95%", category="measurement")
        self.record.event("04:01", er.REVIEW_DEFERRED, None, payload={})
        narrative, client = self.write({
            "observation_notes": [{"단계": "6", "시료": "3번 샘플", "문장": "3번 샘플 생존율은 95%였다."}],
            "result_summary": "",
        })
        self.assertEqual(narrative.ai_notes, ())
        self.assertEqual(narrative.ai_rejected[0][1], "확인되지 않은 값")
        # The model is told which value that is, in words, not by the server's mark.
        sent = client.calls[0]["messages"][1]["content"]
        self.assertIn("6단계 측정: 3번 샘플 생존율 95%", sent)
        self.assertNotIn("[미확인]", sent)

    def test_a_condition_the_experimenter_did_not_confirm_is_said_so_to_the_model(self) -> None:
        # A timer ended early that was never confirmed (lane RP2's "[미확인]"):
        # the rows the model reads say so in words, never with the mark.
        from tests.lane_rp2_support import RecordWriter

        with temporary() as directory, environment():
            record = RecordWriter(directory, session="session-rp3-unreviewed", title="Test digestion protocol")
            record.event("00:30", "session_started", "1")
            record.event("00:36", "step_completed", "1")
            record.event("00:40", "step_completed", "2")
            record.event("00:41", "timer_started", "3", payload={"timer": {"duration_seconds": 900}})
            record.event("01:06", "step_completed", "3", payload={"timer": {
                "completion_state": "step_exited_before_timer_elapsed",
                "step_exited_before_timer_elapsed": True, "elapsed_seconds": 600, "source_duration_seconds": 900}})
            record.event("01:15", "session_stopped", "4", payload={"stop_reason": "stopped_by_user"})
            record.finalize("01:15", "stopped")
            sent = er._writer_facts(er.ReportWriterBrain.facts_for(record.doc(), fixture=protocol_double()))
            rows = sent["실제로 한 조건"] + sent["원문과 다르게 한 점"]
            self.assertNotIn("[미확인]", "\n".join(rows))
            self.assertIn("3단계 타이머: 원문 15분 중 10분에 끝냄 (확인되지 않음)", rows)

    def test_no_model_or_a_failed_call_leaves_the_record_sentences_only(self) -> None:
        for reply in ("not json", None):
            with self.subTest(reply=reply):
                if reply is None:
                    narrative = er.ReportWriterBrain().build_deterministic_narrative(
                        self.record.doc(), list(self.record.doc()["events"]), fixture=self.fixture)
                else:
                    narrative, _ = self.write(reply)
                self.assertEqual(narrative.ai_notes, ())
                self.assertEqual(narrative.ai_result, "")
                text = er.render_markdown(narrative)
                self.assertNotIn("AI 정리", text)
                self.assertIn("모델이 쓴 문장은 포함하지 않았다", text)
                self.assertIn("| 6단계 · Read-out | 1번 샘플 | 1번 샘플 생존율 82%", text)

    def test_the_writer_off_by_setting_makes_no_call(self) -> None:
        with mock.patch.dict(os.environ, {"VOINEY_LAB_REPORT_WRITER_ENABLED": "false",
                                          "VOINEY_LAB_REPORT_PROVIDER": "google",
                                          "VOINEY_LAB_REPORT_MODEL": "gemini-3.8-flash"}):
            self.assertFalse(er.ReportWriterSettings.from_environment().enabled)


class MeetingSummaryTests(_Case):
    def test_one_page_with_purpose_samples_key_results_differences_and_next(self) -> None:
        from docx import Document

        # The run before is in the store and the reply was kept when the
        # experiment ended: the download compares the two runs.
        earlier_cell_run(self.temporary.name, self.record.store)
        self.record.store.save_prose(self.record.report_id, writer="fake-model", reply=GOOD, failure="",
                                     event_count=len(self.record.doc()["events"]))
        document = Document(io.BytesIO(
            self.record.store.export_meeting_docx(self.record.report_id, fixture=self.fixture)))
        texts = [p.text for p in document.paragraphs if p.text.strip()]
        self.assertEqual(texts[0], "A549 세포에 약물 X를 0.5랑 5 µM로 처리해서 생존율을 보는 실험")
        self.assertIn("랩미팅 요약 · 2026년 10월 20일 (화) · 이실험 · Cell viability assay after drug treatment", texts[1])
        for heading in ("목적", "시료", "핵심 결과", "지난번과 다른 점", "다음 할 일"):
            self.assertIn(heading, texts, heading)
        joined = "\n".join(texts)
        self.assertIn("결과 (AI 정리): 약물 X 0.5 µM과 5 µM 처리군의 생존율이 대조군보다 낮았고", joined)
        self.assertIn("실험자: “처리군 생존율이 대조군보다 낮았어.”", joined)
        self.assertIn("6단계 · 1번 샘플: 1번 샘플 생존율 82%, 2번 샘플 41%로 처리군이 대조군보다 낮았다. (AI 정리)", joined)
        self.assertIn("• 5 µM 농도를 2.5로 낮춰 볼게 (실험자가 말함)", joined)
        self.assertIn("• 3단계 온도: 지난번 원문대로 (37°C) → 이번 40°C", joined)
        cells = [cell.text for table in document.tables for row in table.rows for cell in row.cells]
        self.assertIn("3번 샘플", cells)
        for gone in ("부록", "단계 시각표", "시스템 사건", "말한 그대로의 기록"):
            self.assertNotIn(gone, joined)
        self.assertLess(len(joined), 2500)

    def test_the_store_exports_the_summary_as_word_and_pdf(self) -> None:
        from docx import Document

        narrative, _ = self.write(GOOD)
        word = self.record.store.export_meeting_docx(self.record.report_id, narrative)
        self.assertTrue(word.startswith(b"PK"))
        office = _FakeOffice()
        pdf = self.record.store.export_meeting_pdf(
            self.record.report_id, narrative, converter="/usr/bin/soffice", run=office)
        self.assertTrue(pdf.startswith(b"%PDF"))
        # The Word file handed to the converter is the summary (a .docx's
        # bytes carry its time, so the paragraphs are compared).
        texts = lambda data: [p.text for p in Document(io.BytesIO(data)).paragraphs]  # noqa: E731
        self.assertEqual(texts(office.document), texts(word))
        self.assertIn("핵심 결과", texts(word))


class ResultsTableTests(_Case):
    def test_the_xlsx_has_one_row_per_sample_and_record_with_the_condition_used(self) -> None:
        from openpyxl import load_workbook

        narrative, _ = self.write(GOOD)
        workbook = load_workbook(io.BytesIO(er.render_xlsx(narrative)))
        sheet = workbook["결과"]
        rows = [[cell.value for cell in row] for row in sheet.iter_rows()]
        self.assertEqual(rows[0], ["시료", "과정", "조건 (원문 → 실제)", "관찰", "측정값", "단위", "시각", "출처"])
        by_text = {row[3]: row for row in rows[1:]}
        self.assertEqual(by_text["1번 샘플 생존율 82%"][:7],
                         ["1번 샘플", "6단계 · Read-out", "원문대로 — 100 µL", "1번 샘플 생존율 82%", 82, "%", "12:50"])
        self.assertEqual(by_text["항온조가 안 내려가서 40도에서 넣었어"][:7],
                         ["—", "3단계 · Treatment", "온도 37°C → 40°C", "항온조가 안 내려가서 40도에서 넣었어", 40, "°C", "09:50"])
        self.assertEqual(by_text["well 당 5000 cells 심었어"][4:6], [None, None])
        self.assertEqual(by_text["1번 샘플 생존율 82%"][7], "기록")
        # The AI-written notes come after the records, marked as such.
        self.assertIn(["1번 샘플", "6단계 · Read-out", None, "1번 샘플 생존율 82%, 2번 샘플 41%로 처리군이 대조군보다 낮았다.",
                       None, None, None, "AI 정리"], rows)
        head = workbook["머리"]
        self.assertEqual([cell.value for cell in head["A"]][:2], ["실험", "날짜·시간"])

    def test_the_export_route_offers_the_three_formats(self) -> None:
        import httpx
        from voiney_lab.server import app

        async def get(url):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
                return await http.get(url)

        async def run_inline(function, *args, **kwargs):
            return function(*args, **kwargs)

        with mock.patch.dict(os.environ, {"VOINEY_LAB_EXPERIMENT_REPORTS_ENABLED": "true",
                                          "VOINEY_LAB_EXPERIMENT_REPORT_DB": str(self.record.store.path),
                                          "VOINEY_LAB_REPORT_WRITER_ENABLED": "false"}), \
                mock.patch.object(er, "report_protocol_fixture", return_value=(self.fixture, "")), \
                mock.patch("fastapi.routing.run_in_threadpool", side_effect=run_inline), \
                mock.patch.object(er.ExperimentReportStore, "export_meeting_pdf", return_value=b"%PDF-1.7 fake"):
            for suffix, media, prefix in (
                ("meeting-docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", b"PK"),
                ("meeting-pdf", "application/pdf", b"%PDF"),
                ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", b"PK"),
            ):
                with self.subTest(suffix=suffix):
                    response = asyncio.run(get(f"/api/experiment-reports/{self.record.report_id}.{suffix}"))
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertTrue(response.headers["content-type"].startswith(media))
                    self.assertTrue(response.content.startswith(prefix))
                    self.assertIn(f'filename="{self.record.report_id}.{suffix.replace("meeting-", "meeting.")}"',
                                  response.headers["content-disposition"])


class DeviationRunTests(unittest.TestCase):
    """The notes on lane RP2's record: a value only in the source passes; a
    value said and then corrected is no longer in the record."""

    def test_a_source_value_passes_and_a_corrected_value_does_not(self) -> None:
        with temporary() as directory, environment():
            er._NARRATIVE_CACHE.clear()
            record = deviation_run(directory)
            client = _Client({
                "observation_notes": [
                    {"단계": "2", "시료": "", "문장": "2단계는 원문의 37°C 대신 40°C에서 씻었다."},
                    {"단계": "1", "시료": "", "문장": "조각 크기 1 mm로 잘랐다(처음에는 2 mm라고 했다)."},
                ],
                "result_summary": "4단계에서 튜브를 쏟은 것 외에 원문대로 진행했다.",
            })
            brain = er.ReportWriterBrain(client=client, model="fake-model", timeout_seconds=5)
            doc = record.doc()
            narrative = asyncio.run(brain.generate_narrative(doc, list(doc["events"]), fixture=protocol_double()))
            self.assertEqual([note.text for note in narrative.ai_notes], ["2단계는 원문의 37°C 대신 40°C에서 씻었다."])
            # The model reads the latest words ("조각 크기 1 mm"); the value
            # said before the correction is not one it may state.
            self.assertEqual(narrative.ai_rejected,
                             (("조각 크기 1 mm로 잘랐다(처음에는 2 mm라고 했다).", "기록·원문에 없는 값 2 mm"),))
            self.assertEqual(narrative.ai_result, "4단계에서 튜브를 쏟은 것 외에 원문대로 진행했다.")
            text = record.store.export_markdown(record.report_id, narrative, fixture=protocol_double(),
                                                start_check=START_CHECK, previous=None).decode()
            self.assertNotIn("2단계는 원문의 37°C 대신 40°C에서 씻었다.", text)
            self.assertIn("40도에서 씻었어", text)


if __name__ == "__main__":
    unittest.main()
