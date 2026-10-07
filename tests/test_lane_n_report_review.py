"""The report's values, confirmed before they go in (lane N, decision 3 of 2026-10-07).

When the experiment ends (stopped or completed) the server lists the record's
important values -- measurements, observations with a number, points done
differently from the source (a deviation note, a later start, a confirmed
return, a timer ended early) and anomalies. With any listed it says
"보고서에 넣을 중요 값 N개를 확인할게요." and reads them one by one, each
ending "맞으면 '네'라고 해 주세요". "네" confirms; "고쳐 줘, …" corrects (as
in decision 2) and confirms; "나중에 할게" stops and leaves the rest to the
screen's checklist (✓ 확인, 고치기). Each confirmation is an event. The
report's prose is written once the review is over (or left), a confirmed
value carries "실험자 확인", one not confirmed sits apart under "확인되지
않은 값", and finishing the checklist on the screen writes the prose again --
its one model call then. With nothing listed nothing is asked.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx

import voiney_lab.server as server
from tests.lane_n_support import VoiceNotesHarness, notes_fixture, shown
from tests.test_lab_report import _Client
from voiney_lab import experiment_reports as er

REPO = Path(__file__).resolve().parents[1]


def _event(kind: str, key: str, label: str = "2", *, wording: str | None = None,
           category: str | None = None, payload: dict | None = None) -> dict:
    return {
        "event_key": key, "event_type": kind, "step_label": label, "step_id": f"step-{label}",
        "user_wording": wording, "category": category, "payload": payload or {},
        "created_at": "2026-10-07T00:00:00+00:00",
    }


class ReviewItemTests(unittest.TestCase):
    """What the server lists, from the record alone."""

    def test_the_values_a_report_states_are_listed_and_nothing_else(self) -> None:
        events = [
            _event("observation", "m", wording="pH 7.2", category="measurement"),
            _event("observation", "d", wording="원문과 다르게 40도에서 했어", category="deviation"),
            _event("observation", "o1", wording="밴드가 2개 보여", category="observation"),
            _event("observation", "o2", wording="젤이 투명해졌어", category="observation"),
            _event("observation", "n", wording="튜브 라벨 A-170", category="note"),
            _event("anomaly", "a", wording="튜브를 쏟았어"),
            _event("steps_skipped", "s", "1", payload={"step_record": {
                "kind": "start_at_step", "start_step": "4", "skipped_step_labels": ["1", "2", "3"]}}),
            _event("repeat_returned", "b", "2", payload={"step_record": {
                "kind": "repeat_return", "from_step": "5", "to_step": "2", "round": 2}}),
            _event("step_completed", "t", "3", payload={"timer": {
                "completion_state": "step_exited_before_timer_elapsed",
                "elapsed_seconds": 360, "source_duration_seconds": 600}}),
        ]
        items = er.report_review_items(events)
        self.assertEqual(
            [(item["item_id"], item["kind"], item["text"]) for item in items],
            [
                ("r.m", "측정", "pH 7.2"),
                ("r.d", "편차", "원문과 다르게 40도에서 했어"),
                ("r.o1", "관찰", "밴드가 2개 보여"),
                ("r.a", "이상", "튜브를 쏟았어"),
                ("s.s", "건너뜀", "4단계부터 시작(1–3단계 건너뜀)"),
                ("b.b", "돌아감", "5단계에서 2단계로 돌아감 — 2회차(말로 확인한 돌아가기 기준)"),
                ("t.t", "타이머", "원문 시간 10분 중 6분에 단계를 끝냄"),
            ],
        )
        self.assertFalse(any(item["confirmed"] for item in items))

    def test_a_value_is_confirmed_as_it_reads_now(self) -> None:
        events = [
            _event("observation", "m", wording="pH 7.2", category="measurement"),
            _event(er.REVIEW_CONFIRMED, "c1", payload={"item_id": "r.m", "text": "pH 7.2"}),
        ]
        self.assertTrue(er.report_review_items(events)[0]["confirmed"])
        corrected = events + [_event("record_corrected", "f", payload={"record_fix": {
            "target_event_key": "m", "text_before": "pH 7.2", "text_after": "pH 7.4"}})]
        item = er.report_review_items(corrected)[0]
        self.assertEqual((item["text"], item["corrected"], item["original"]), ("pH 7.4", True, "pH 7.2"))
        self.assertFalse(item["confirmed"])
        withdrawn = events + [_event("record_retracted", "w", payload={"record_fix": {
            "target_event_key": "m", "text_before": "pH 7.2"}})]
        self.assertEqual(er.report_review_items(withdrawn), [])


class ServedReviewTests(VoiceNotesHarness, unittest.TestCase):
    """The production path: the end of the experiment, the review, the report."""

    NOTES = ("프로토콜 시작해줘", "1단계 완료했어", "실험노트에 적어 줘, pH 7.2",
             "기록해 줘, 원문과 다르게 Solution A를 600 µL 넣었어", "실험 종료해", "응")

    def prose_state(self, listener) -> dict:
        store = listener.experiment_report_store
        report_id = listener.experiment_report_id
        return {
            "held": er.REPORT_PROSE.held(store, report_id),
            "state": er.REPORT_PROSE.status(store, report_id)["state"],
        }

    def test_the_end_opens_the_review_and_holds_the_prose(self) -> None:
        socket = self.turns(*self.NOTES, snapshot=self.prose_state)
        self.assertEqual(
            shown(socket)[6],
            "2단계에서 실험을 종료했습니다. 지금까지의 기록을 보고서로 저장했어요. "
            "보고서에 넣을 중요 값 2개를 확인할게요. 첫째, 2단계 측정, pH 7.2. "
            "맞으면 '네'라고 해 주세요.",
        )
        self.assertIn("첫째, 2단계 측정, 피에이치 칠 점 이.", self.spoken[6][-1])
        self.assertEqual(self.snapshot, {"held": True, "state": "awaiting_review"})

    def test_each_yes_confirms_and_the_last_prepares_the_prose(self) -> None:
        socket = self.turns(*self.NOTES, "네", "응", snapshot=self.prose_state)
        replies = shown(socket)
        self.assertEqual(
            replies[7],
            "둘째, 2단계 편차, 원문과 다르게 Solution A를 600 µL 넣었어. 맞으면 '네'라고 해 주세요.")
        self.assertTrue(replies[8].startswith("모두 확인했어요. 보고서 문장을 준비할게요. 화면에서"))
        self.assertEqual(self.snapshot, {"held": False, "state": "ready"})
        confirmed = [e for e in self.report()["events"] if e["event_type"] == er.REVIEW_CONFIRMED]
        self.assertEqual([e["payload"]["text"] for e in confirmed],
                         ["pH 7.2", "원문과 다르게 Solution A를 600 µL 넣었어"])
        markdown = self.report_markdown()
        self.assertIn("| 2 | 측정 | pH 7.2 (실험자 확인) |", markdown)
        self.assertIn("- 2단계: 연구자 기록 — “원문과 다르게 Solution A를 600 µL 넣었어” (실험자 확인).", markdown)
        self.assertIn("보고서에 넣은 중요 값 2개를 실험자가 모두 확인했다", markdown)
        self.assertNotIn("확인되지 않은 값", markdown)

    def test_a_correction_is_asked_appended_and_confirmed(self) -> None:
        socket = self.turns(*self.NOTES, "고쳐 줘, 7.2가 아니라 7.4", "응", "나중에 할게")
        replies = shown(socket)
        self.assertEqual(replies[7], "'pH 7.2'를 'pH 7.4'로 고칠까요?")
        self.assertTrue(replies[8].startswith("'pH 7.4'로 고치고 확인했어요. 둘째,"))
        events = self.report()["events"]
        fix = next(e for e in events if e["event_type"] == "record_corrected")
        self.assertEqual(fix["payload"]["record_fix"]["source"], "voice_review")
        self.assertEqual(fix["payload"]["record_fix"]["text_after"], "pH 7.4")
        self.assertIn("| 2 | 측정 | pH 7.4 (정정됨 — 처음 기록 “pH 7.2”) (실험자 확인) |",
                      self.report_markdown())
        timeline = self.timeline()["timeline"]
        corrected = next(e for e in timeline if e["event_type"] == "observation_corrected")
        self.assertEqual(corrected["payload"]["content"], "pH 7.4")

    def test_later_leaves_the_rest_for_the_screen_and_prepares_the_prose(self) -> None:
        socket = self.turns(*self.NOTES, "네", "나중에 할게", snapshot=self.prose_state)
        self.assertTrue(shown(socket)[8].startswith(
            "알겠어요. 남은 1개는 화면의 확인 목록에서 확인할 수 있어요. "
            "확인하지 않은 값은 보고서에 따로 적어 둘게요."))
        self.assertEqual(self.snapshot, {"held": False, "state": "ready"})
        deferred = next(e for e in self.report()["events"] if e["event_type"] == er.REVIEW_DEFERRED)
        self.assertEqual(len(deferred["payload"]["item_ids"]), 1)
        markdown = self.report_markdown()
        self.assertIn("### 확인되지 않은 값", markdown)
        self.assertIn("- 2단계 편차: 원문과 다르게 Solution A를 600 µL 넣었어", markdown)
        self.assertIn("| 2 | 측정 | pH 7.2 (실험자 확인) |", markdown)

    def test_a_no_asks_how_to_correct_and_keeps_the_value(self) -> None:
        socket = self.turns(*self.NOTES, "아니", "네")
        replies = shown(socket)
        self.assertIn("어떻게 고칠까요?", replies[7])
        self.assertTrue(replies[8].startswith("둘째,"))

    def test_nothing_to_confirm_asks_nothing(self) -> None:
        socket = self.turns("프로토콜 시작해줘", "1단계 완료했어", "메모해 줘 튜브 라벨 A-170",
                            "실험 종료해", "응", snapshot=self.prose_state)
        reply = shown(socket)[5]
        self.assertNotIn("확인할게요", reply)
        self.assertIn("화면에서", reply)
        self.assertEqual(self.snapshot, {"held": False, "state": "ready"})

    def test_a_session_closed_mid_review_leaves_the_rest_for_the_screen(self) -> None:
        self.turns(*self.NOTES)
        events = self.report()["events"]
        deferred = next(e for e in events if e["event_type"] == er.REVIEW_DEFERRED)
        self.assertEqual(deferred["payload"]["source"], "session_closed")
        store = er.ExperimentReportStore(self.report_db)
        self.assertFalse(er.REPORT_PROSE.held(store, self.report()["report_id"]))
        self.assertIsNotNone(store.get_prose(self.report()["report_id"]))

    def test_the_report_state_carries_the_checklist(self) -> None:
        self.turns(*self.NOTES, "네")
        public = server._public_experiment_report_state(self.report())
        self.assertEqual([(item["kind"], item["confirmed"]) for item in public["review"]],
                         [("측정", True), ("편차", False)])


class ScreenChecklistTests(unittest.TestCase):
    """GET and POST /api/experiment-reports/{id}/review (✓ 확인, 고치기)."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = er.ExperimentReportStore(Path(tmp.name) / "r.sqlite")
        report = self.store.open_report(
            session_id="session-review", protocol_id="lane-n-wash", protocol_title="Fictional wash",
            protocol_revision="lane-n-wash-v1", protocol_sha256="9" * 64,
            readiness_status="guidance_ready", development_only=True)
        self.report_id = report["report_id"]
        for event in (
            _event("observation", "m", wording="pH 7.2", category="measurement"),
            _event("anomaly", "a", wording="튜브를 쏟았어"),
        ):
            self.store.append_event(
                self.report_id, event_key=event["event_key"], event_type=event["event_type"],
                step_label=event["step_label"], user_wording=event["user_wording"],
                category=event["category"])
        self.client = _Client({"results_summary": "기록된 값을 표에 적었다."})
        brain = er.ReportWriterBrain(client=self.client, model="fake-writer", timeout_seconds=5)
        for patcher in (
            mock.patch.dict(os.environ, {
                "VOINEY_LAB_EXPERIMENT_REPORTS_ENABLED": "true",
                "VOINEY_LAB_EXPERIMENT_REPORT_DB": str(self.store.path),
                "VOINEY_LAB_WORKSPACE_ENABLED": "false"}),
            mock.patch.object(server, "_scope_tenant_resource"),
            mock.patch.object(server, "_report_writer_brain", return_value=brain),
            mock.patch.object(er, "report_protocol_fixture",
                              return_value=(notes_fixture(), "")),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def call(self, method: str, path: str, body: dict | None = None) -> httpx.Response:
        async def run() -> httpx.Response:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),
                                         base_url="http://testserver") as http:
                return await http.request(
                    method, f"/api/experiment-reports/{self.report_id}{path}",
                    content=json.dumps(body) if body is not None else None,
                    headers={"Content-Type": "application/json"})
        return asyncio.run(run())

    def wait_for_prose(self) -> None:
        er.REPORT_PROSE.wait(self.store, self.report_id, 10)

    def test_values_are_confirmed_only_after_the_experiment(self) -> None:
        listing = self.call("GET", "/review").json()
        self.assertEqual([item["item_id"] for item in listing["items"]], ["r.m", "r.a"])
        self.assertEqual(self.call("POST", "/review/r.m", {"action": "confirm"}).status_code, 409)

    def test_the_last_confirmation_writes_the_prose_again_once(self) -> None:
        self.store.finalize(self.report_id, status="stopped", event_key="end")
        server._prepare_report_prose(self.store, self.report_id)
        self.wait_for_prose()
        self.assertEqual(len(self.client.calls), 1)
        first = self.call("POST", "/review/r.m", {"action": "confirm"}).json()
        self.assertEqual(first["unconfirmed"], 1)
        self.wait_for_prose()
        self.assertEqual(len(self.client.calls), 1)
        last = self.call("POST", "/review/r.a", {"action": "correct", "text": "튜브를 조금 쏟았어"}).json()
        self.assertEqual(last["unconfirmed"], 0)
        self.wait_for_prose()
        self.assertEqual(len(self.client.calls), 2)
        events = self.store.get_report(self.report_id)["events"]
        fix = next(e for e in events if e["event_type"] == "record_corrected")
        self.assertEqual(fix["payload"]["record_fix"]["source"], "screen")
        self.assertEqual(fix["payload"]["record_fix"]["text_after"], "튜브를 조금 쏟았어")
        self.assertEqual(
            [e["payload"]["source"] for e in events if e["event_type"] == er.REVIEW_CONFIRMED],
            ["screen", "screen"])
        markdown = self.store.export_markdown(self.report_id, fixture=notes_fixture()).decode()
        self.assertIn("튜브를 조금 쏟았어 (정정됨 — 처음 기록 “튜브를 쏟았어”) (실험자 확인)", markdown)

    def test_an_unknown_value_is_not_found(self) -> None:
        self.store.finalize(self.report_id, status="stopped", event_key="end")
        self.assertEqual(self.call("POST", "/review/r.zzz", {"action": "confirm"}).status_code, 404)


class UnconfirmedValuesInProseTests(unittest.TestCase):
    """Model prose may not state a value the researcher did not confirm."""

    def facts(self, *, confirmed: bool) -> er.ReportFacts:
        events = [
            _event("observation", "m", wording="pH 7.2", category="measurement"),
        ]
        if confirmed:
            events.append(_event(er.REVIEW_CONFIRMED, "c", payload={"item_id": "r.m", "text": "pH 7.2"}))
        report = {"protocol_title": "Fictional wash", "status": "stopped", "events": events,
                  "started_at": "2026-10-07T00:00:00+00:00", "ended_at": "2026-10-07T01:00:00+00:00"}
        return er.build_report_facts(report, fixture=notes_fixture())

    def test_an_unconfirmed_value_is_refused_and_a_confirmed_one_kept(self) -> None:
        reply = {"results_summary": "2단계에서 pH 7.2를 측정했다."}
        refused = er.check_report_sections(reply, self.facts(confirmed=False))
        self.assertIn("results_summary", refused)
        self.assertEqual(er.check_report_sections(reply, self.facts(confirmed=True)), {})

    def test_the_model_reads_which_values_are_unconfirmed(self) -> None:
        sent = er._writer_facts(self.facts(confirmed=False))
        self.assertEqual(sent["확인되지 않은 값"], ["2단계 측정: pH 7.2"])
        self.assertEqual(sent["기록"], [])
        sent = er._writer_facts(self.facts(confirmed=True))
        self.assertEqual(sent["확인되지 않은 값"], [])
        self.assertTrue(sent["기록"][0]["실험자 확인"])


class ScreenTests(unittest.TestCase):
    def test_the_checklist_is_text_only_with_confirm_and_correct(self) -> None:
        html = (REPO / "src/voiney_lab/static/index.html").read_text(encoding="utf-8")
        block = html.split("function renderReportReview", 1)[1].split("\nasync function refreshReportProse", 1)[0]
        for words in ("✓ 확인", "고치기", "고쳐서 확인", "실험자 확인", "/review/"):
            self.assertIn(words, block)
        self.assertNotIn("innerHTML", block)
        self.assertIn('id="report-review-checklist"', html)
        self.assertIn("renderReportReview(report.report_id,report.review,report.status)", html)


if __name__ == "__main__":
    unittest.main()
