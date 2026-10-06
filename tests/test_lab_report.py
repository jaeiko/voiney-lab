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
    def test_markdown_follows_the_report_order_with_identifiers_only_in_the_appendix(self) -> None:
        self.run_steps_one_to_four_then_stop()
        text = self.store.export_markdown(self.report_id, fixture=self.fixture).decode()
        headings = [line for line in text.splitlines() if line.startswith("#")]
        order = ["# Test digestion protocol 실험 보고서", "## 1. 실험 목적", "## 2. 배경·원리",
                 "## 3. 재료 및 방법", "## 4. 결과", "## 5. 고찰", "## 6. 결론", "## 참고문헌",
                 "## 부록 · 기록 정보"]
        self.assertEqual([h for h in headings if h in order], order)
        body, appendix = text.split("## 부록 · 기록 정보")
        for identifier in (self.report_id, *REPORT_ID_FIELDS, "stopped", "step_completed"):
            self.assertNotIn(identifier, body)
        for identifier in (self.report_id, "rev-test-1", "9" * 64, "step_completed"):
            self.assertIn(identifier, appendix)

    def test_run_information_is_in_the_researchers_time_zone_and_plain_words(self) -> None:
        self.run_steps_one_to_four_then_stop()
        text = self.store.export_markdown(self.report_id, fixture=self.fixture).decode()
        for row in ("| 날짜 | 2026년 10월 6일 (화) |", "| 시작 | 09:30 |", "| 끝 | 10:15 |",
                    "| 걸린 시간 | 45분 |", "| 완료 단계 | 4 / 5 |", "| 결과 | 중단 — 5단계에서 종료 |",
                    "| 수행자 | (직접 적어 주세요) |"):
            self.assertIn(row, text)
        self.assertIn("개발용 시험 프로토콜", text)
        with mock.patch.dict(os.environ, {"VOINEY_LAB_REPORT_TIMEZONE": "UTC"}):
            utc = self.store.export_markdown(self.report_id, fixture=self.fixture).decode()
        self.assertIn("| 시작 | 00:30 |", utc)

    def test_methods_results_and_deviations_come_from_the_record(self) -> None:
        self.run_steps_one_to_four_then_stop()
        text = self.store.export_markdown(self.report_id, fixture=self.fixture).decode()
        # Korean step text where it exists, the source's numbers unchanged.
        self.assertIn("| 3 | Solution A 500 µL로 37°C에서 15 min 동안 세척합니다. | 10:06 | 원문 15분 / 10분에 끝냄 |", text)
        self.assertIn("| 4 | 관찰 | 밴드가 투명해졌어 | 10:08 |", text)
        self.assertIn("| 4 | 이상 | 튜브를 쏟았어 | 10:09 |", text)
        self.assertIn("3단계: 원문 시간 15분 중 10분에 단계를 끝냈다(타이머를 일찍 끝냄).", text)
        self.assertIn("3단계에서 5분 동안 멈췄다가 다시 진행했다(09:50–09:55).", text)
        self.assertIn("5단계에서 실험을 끝내 5단계는 수행하지 않았다.", text)
        self.assertIn("4단계 이상(“튜브를 쏟았어”) 뒤에 어떻게 처리했는지 기록되지 않았다.", text)
        self.assertIn("(라) 연구자 해석", text)
        self.assertIn("[1] Kim 2025. Test digestion protocol. protocols.io https://dx.doi.org/10.1/test.v1", text)

    def test_a_record_without_observations_says_so(self) -> None:
        self.event("00:30", "session_started", "1")
        self.event("00:36", "step_completed", "1")
        text = self.store.export_markdown(self.report_id, fixture=self.fixture).decode()
        self.assertIn("## 4. 결과\n\n기록된 관찰이 없습니다.", text)

    def test_docx_has_the_same_sections_and_tables(self) -> None:
        from docx import Document

        self.run_steps_one_to_four_then_stop()
        document = Document(io.BytesIO(self.store.export_docx(self.report_id, fixture=self.fixture)))
        paragraphs = [p.text for p in document.paragraphs]
        for heading in ("Test digestion protocol 실험 보고서", "1. 실험 목적", "2. 배경·원리",
                        "3. 재료 및 방법", "4. 결과", "5. 고찰", "6. 결론", "참고문헌", "부록 · 기록 정보"):
            self.assertIn(heading, paragraphs)
        cells = [[c.text for c in row.cells] for table in document.tables for row in table.rows]
        self.assertIn(["시작", "09:30"], cells)
        self.assertIn(["4", "이상", "튜브를 쏟았어", "10:09"], cells)

    def test_an_unreadable_protocol_is_said_not_papered_over(self) -> None:
        self.event("00:30", "session_started", "1")
        self.event("00:36", "step_completed", "1")
        with mock.patch.object(er, "report_protocol_fixture", return_value=(None, "프로토콜 원문을 불러오지 못했다")):
            text = self.store.export_markdown(self.report_id).decode()
        self.assertNotIn("instruction.", text)
        self.assertIn("프로토콜 원문을 불러오지 못했다 — 단계 원문과 원문 조건을 이 보고서에 싣지 못했다.", text)

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


GOOD_REPLY = {
    "purpose": "이 실험은 젤 속 단백질을 분석할 수 있게 준비하는 것을 목적으로 한다[1].",
    "background": "원문은 탈색과 탈수 순서로 진행한다[1].",
    "methods_summary": "1–4단계를 수행했고 3단계는 원문 15분 중 10분에 끝냈다.",
    "results_summary": "4단계에서 “밴드가 투명해졌어”라는 관찰과 “튜브를 쏟았어”라는 이상이 기록되었다.",
    "discussion_confirmed": ["1–4단계가 완료로 기록되었다."],
    "discussion_to_check": ["4단계 이상 뒤 처리 내용을 확인해야 한다."],
    "discussion_review": [{"항목 번호": 2, "제안": "쏟은 양이 결과에 영향을 주었는지 검토한다."}],
    "conclusion": "5단계 중 4단계를 마치고 5단계에서 중단했다.",
    "next_steps": ["5단계부터 이어서 진행한다."],
}


class ReportWriterTests(_ReportCase):
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
        for content in ("Solution A 500 µL로 37°C에서 15 min 동안 세척합니다.", "밴드가 투명해졌어",
                        "튜브를 쏟았어", "원문 15분 / 10분에 끝냄", "09:30"):
            self.assertIn(content, sent)
        self.assertEqual(set(narrative.section_origin.values()), {"모델"})
        self.assertEqual(narrative.purpose, GOOD_REPLY["purpose"])
        self.assertEqual(narrative.discussion_review,
                         ("검토 제안 (항목 2): 쏟은 양이 결과에 영향을 주었는지 검토한다.",))

    def test_each_check_replaces_only_the_section_that_fails(self) -> None:
        self.run_steps_one_to_four_then_stop()
        bad = dict(GOOD_REPLY)
        bad["purpose"] = "37°C에서 소화하는 실험이다[1]."
        bad["methods_summary"] = "1–4단계를 수행했고 3단계는 20분 걸렸다."
        bad["results_summary"] = "4단계에서 “젤이 노랗게 변했다”는 관찰이 기록되었다."
        bad["conclusion"] = f"기록 {self.report_id} 는 stopped 상태다."
        bad["discussion_review"] = [{"항목 번호": 99, "제안": "온도가 원인으로 보인다."}]
        bad["discussion_confirmed"] = ["밴드가 투명해진 것은 탈색이 끝난 것으로 보인다."]
        narrative, _ = self.write(bad)
        fallback = er.deterministic_sections(narrative.facts)
        for key, reason in (
            ("purpose", "실험 조건 숫자"),
            ("methods_summary", "기록·원문에 없는 숫자 20"),
            ("results_summary", "기록에 없는 관찰"),
            ("conclusion", "본문에 ER-/fixture-/candidate- 식별자"),
            ("discussion_review", "기록된 이상·편차에 붙지 않은 원인 추정"),
            ("discussion_confirmed", "추측 표현"),
        ):
            self.assertEqual(narrative.section_origin[key], "대체", key)
            self.assertTrue(any(reason in item for item in narrative.rejected[key]), (key, narrative.rejected[key]))
            self.assertEqual(getattr(narrative, key), fallback[key] if not isinstance(fallback[key], tuple) else tuple(fallback[key]))
        self.assertEqual(narrative.section_origin["discussion_to_check"], "모델")
        text = er.render_markdown(narrative)
        self.assertIn("| 1. 실험 목적 | 대체 — 실험 조건 숫자 |", text)

    def test_purpose_sentences_need_a_source_number(self) -> None:
        self.run_steps_one_to_four_then_stop()
        reply = dict(GOOD_REPLY, purpose="이 실험은 단백질을 분석하기 위한 것이다.")
        narrative, _ = self.write(reply)
        self.assertEqual(narrative.section_origin["purpose"], "대체")
        self.assertIn("출처 번호가 없는 문장", narrative.rejected["purpose"])

    def test_the_report_uses_no_outside_sources(self) -> None:
        # Decision of 2026-10-06, option (a): no Google Search grounding, so
        # nothing but the protocol is cited and the writer is never asked to
        # search.
        self.run_steps_one_to_four_then_stop()
        narrative, client = self.write(dict(GOOD_REPLY, background="트립신은 단백질을 자른다[2]."))
        self.assertNotIn("web_search", client.calls[0])
        self.assertNotIn("외부 자료", client.calls[0]["messages"][1]["content"])
        self.assertEqual(narrative.section_origin["background"], "대체")
        self.assertIn("없는 출처 번호", narrative.rejected["background"])
        self.assertEqual([source.kind for source in narrative.sources], ["protocol"])
        text = er.render_markdown(narrative)
        self.assertIn("외부 자료는 쓰지 않았다. 이 칸은 프로토콜 원문만으로 썼다.", text)
        self.assertNotIn("구글", text)

    def test_a_failed_model_keeps_the_same_structure_with_the_servers_sentences(self) -> None:
        self.run_steps_one_to_four_then_stop()
        narrative, client = self.write("not json")
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(set(narrative.section_origin.values()), {"대체"})
        deterministic = er.ReportWriterBrain().build_deterministic_narrative(
            self.doc(), list(self.doc()["events"]), fixture=self.fixture)
        def body(item: er.ReportNarrative) -> str:
            return er.render_markdown(item).split("## 3. 재료 및 방법")[1].split("## 부록")[0]

        self.assertEqual(body(narrative), body(deterministic))


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
        self.assertTrue(text.startswith("# Test digestion protocol 실험 보고서"))
        self.assertIn("| 4 | 이상 | 튜브를 쏟았어 | 10:09 |", text)
        self.assertNotIn(self.report_id, text.split("## 부록 · 기록 정보")[0])

    def test_both_exports_use_the_report_model_when_it_has_a_key(self) -> None:
        from docx import Document

        self.run_steps_one_to_four_then_stop()
        for suffix in ("md", "docx"):
            er._NARRATIVE_CACHE.clear()
            client = _Client(GOOD_REPLY)
            response = self.fetch(suffix, client, {
                "VOINEY_LAB_REPORT_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "test-key",
                "VOINEY_LAB_REPORT_MODEL": "fake-report-model"})
            self.assertEqual(response.status_code, 200, suffix)
            self.assertEqual(len(client.calls), 1, suffix)
            if suffix == "md":
                text = response.text
            else:
                text = "\n".join(p.text for p in Document(io.BytesIO(response.content)).paragraphs)
            self.assertIn(GOOD_REPLY["purpose"], text, suffix)
