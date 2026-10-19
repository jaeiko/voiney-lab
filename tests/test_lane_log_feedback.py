"""Lane LOG, decision 3: one question when an experiment ends.

"이번 실험에 도움이 됐나요?" -- five buttons (1-5), an optional one-line
memo, and a skip. The answer is kept on the server (``pilot_feedback`` in the
pilot metrics store) with the experiment record it is about and the account
that gave it; the experiment's summary row carries the rating. The memo is
kept for the team and never enters a metric.

The page's code is a new file, ``static/feedback.js``; ``index.html`` holds one
script line, the small box it fills in the 실험 후 view, and three calls.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import voiney_lab.server as server_module
from tests.lane_n_support import VoiceNotesHarness
from tests.test_screen_cleanup import PRELUDE, ROOT, run_page_script

STATIC = Path(server_module.__file__).with_name("static")


def _pilot():
    import voiney_lab.pilot_metrics as pilot

    return pilot


class FeedbackRouteTests(VoiceNotesHarness, unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.turns("프로토콜 시작해줘", "1단계 완료했어")
        self.report_id = self.report()["report_id"]

    def _post(self, body: dict, environment: dict | None = None):
        with patch.dict("os.environ", environment or self.environment, clear=False):
            return TestClient(server_module.app).post("/api/pilot/feedback", json=body)

    def _sources(self):
        pilot = _pilot()
        return pilot.PilotSources(
            metrics_db=self.workspace_dir / pilot.PILOT_METRICS_FILENAME,
            reports_db=self.report_db, workspace_db=None,
        )

    def test_a_rating_and_its_memo_are_kept_and_the_summary_carries_the_rating(self) -> None:
        response = self._post({"report_id": self.report_id, "rating": 4, "note": "  타이머가   좋았어요 "})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json(), {"saved": True})
        pilot = _pilot()
        (kept,) = pilot.PilotMetricsStore(self.workspace_dir / pilot.PILOT_METRICS_FILENAME).feedback()
        self.assertEqual(
            (kept["report_id"], kept["account_id"], kept["rating"], kept["skipped"], kept["note"]),
            (self.report_id, self.principal.principal_id, 4, 0, "타이머가 좋았어요"))
        (row,) = pilot.experiment_rows(self._sources())
        self.assertEqual((row["rating"], row["feedback_skipped"]), (4, 0))
        self.assertNotIn("타이머가", repr(row))

    def test_a_skip_is_kept_as_skipped_and_the_last_answer_counts(self) -> None:
        self.assertEqual(self._post({"report_id": self.report_id, "rating": 2}).status_code, 201)
        self.assertEqual(
            self._post({"report_id": self.report_id, "skipped": True}).status_code, 201)
        (row,) = _pilot().experiment_rows(self._sources())
        self.assertEqual((row["rating"], row["feedback_skipped"]), (None, 1))

    def test_what_is_not_an_answer_is_refused(self) -> None:
        for body in (
            {"report_id": self.report_id, "rating": 6},
            {"report_id": self.report_id, "rating": 0},
            {"report_id": self.report_id, "rating": "4"},
            {"report_id": self.report_id, "rating": True},
            {"report_id": self.report_id},
            {"report_id": self.report_id, "rating": 3, "skipped": True},
            {"report_id": self.report_id, "rating": 3, "note": "가" * 201},
            {"report_id": self.report_id, "rating": 3, "note": 7},
            {"report_id": "../etc", "rating": 3},
        ):
            self.assertEqual(self._post(body).status_code, 400, body)
        self.assertEqual(self._post({"report_id": "ER-NOT-THERE", "rating": 3}).status_code, 404)
        pilot = _pilot()
        self.assertEqual(
            pilot.PilotMetricsStore(self.workspace_dir / pilot.PILOT_METRICS_FILENAME).feedback(), [])

    def test_without_a_workspace_there_is_nowhere_to_keep_it(self) -> None:
        response = self._post(
            {"report_id": self.report_id, "rating": 5},
            {**self.environment, "VOINEY_LAB_WORKSPACE_ENABLED": "false"},
        )
        self.assertEqual(response.status_code, 503)


def run_feedback_script(body: str) -> subprocess.CompletedProcess:
    script = (STATIC / "feedback.js").read_text(encoding="utf-8")
    harness = (
        PRELUDE
        + "const posted=[];globalThis.fetch=async(url,options)=>{posted.push({url,options});"
        + "return {ok:true,status:201,json:async()=>({saved:true})}};\n"
        + "globalThis.localStorage={_v:{},getItem(k){return this._v[k]??null},setItem(k,v){this._v[k]=String(v)}};\n"
        + script
        + "\n(async()=>{\n" + body
        + "\n})().catch(error=>{console.error(error);process.exit(1)});\n"
    )
    return subprocess.run(["node", "-"], cwd=ROOT, text=True, input=harness, capture_output=True)


class FeedbackScriptTests(unittest.TestCase):
    def test_the_box_asks_once_the_experiment_ended_and_sends_the_rating(self) -> None:
        result = run_feedback_script(r"""
const box=node("pilot-feedback");box.hidden=true;
VoineyFeedback.report("ER-AAA");
assert(box.hidden,"asked before the experiment ended");
VoineyFeedback.ended();
assert(!box.hidden,"not shown at the end");
assert(visibleText(box).includes("이번 실험에 도움이 됐나요?"),visibleText(box));
const buttons=[];const walk=item=>{if(item.tagName==="button")buttons.push(item);item.children.forEach(walk)};walk(box);
const ratings=buttons.filter(item=>/^[1-5]$/.test(item.textContent));
assert(ratings.length===5,"five rating buttons: "+ratings.length);
const send=buttons.find(item=>item.textContent==="보내기"),skip=buttons.find(item=>item.textContent==="건너뛰기");
assert(send&&skip,"send and skip");
assert(send.disabled,"send before a rating");
ratings[3].dispatch("click");
assert(!send.disabled,"send after a rating");
let input=null;const find=item=>{if(item.tagName==="input")input=item;item.children.forEach(find)};find(box);
assert(input&&input.attributes.maxlength==="200","one-line memo of 200");
input.value="소리가 작았어요";
send.dispatch("click");
await new Promise(resolve=>setTimeout(resolve,0));
assert(posted.length===1,"one request");
assert(posted[0].url==="/api/pilot/feedback"&&posted[0].options.method==="POST","the route");
const sent=JSON.parse(posted[0].options.body);
assert(sent.report_id==="ER-AAA"&&sent.rating===4&&sent.skipped===false&&sent.note==="소리가 작았어요",JSON.stringify(sent));
assert(visibleText(box).includes("고맙습니다"),visibleText(box));
VoineyFeedback.reset();VoineyFeedback.report("ER-AAA");VoineyFeedback.ended();
assert(box.hidden,"asked again about the same experiment");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_skip_sends_skipped_without_a_rating(self) -> None:
        result = run_feedback_script(r"""
const box=node("pilot-feedback");
VoineyFeedback.ended();VoineyFeedback.report("ER-BBB");
const buttons=[];const walk=item=>{if(item.tagName==="button")buttons.push(item);item.children.forEach(walk)};walk(box);
buttons.find(item=>item.textContent==="건너뛰기").dispatch("click");
await new Promise(resolve=>setTimeout(resolve,0));
const sent=JSON.parse(posted[0].options.body);
assert(sent.skipped===true&&sent.rating===null&&sent.report_id==="ER-BBB",JSON.stringify(sent));
assert(box.hidden,"the box did not close after a skip");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_failed_save_says_so_and_can_be_sent_again(self) -> None:
        result = run_feedback_script(r"""
globalThis.fetch=async(url,options)=>{posted.push({url,options});return {ok:false,status:503,json:async()=>({})}};
const box=node("pilot-feedback");
VoineyFeedback.ended();VoineyFeedback.report("ER-CCC");
const buttons=[];const walk=item=>{if(item.tagName==="button")buttons.push(item);item.children.forEach(walk)};walk(box);
buttons.find(item=>item.textContent==="2").dispatch("click");
const send=buttons.find(item=>item.textContent==="보내기");send.dispatch("click");
await new Promise(resolve=>setTimeout(resolve,0));
assert(visibleText(box).includes("저장하지 못했습니다"),visibleText(box));
assert(!send.disabled,"cannot send again");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_script_writes_text_only(self) -> None:
        script = (STATIC / "feedback.js").read_text(encoding="utf-8")
        self.assertNotIn("innerHTML", script)
        self.assertNotIn("insertAdjacentHTML", script)


class PageHookTests(unittest.TestCase):
    def test_the_page_loads_the_script_once_and_holds_the_box(self) -> None:
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        self.assertEqual(html.count('<script src="/feedback.js"></script>'), 1)
        self.assertEqual(html.count('id="pilot-feedback"'), 1)
        self.assertIn("globalThis.VoineyFeedback?.ended(", html)
        self.assertIn("globalThis.VoineyFeedback?.report(", html)
        self.assertIn("globalThis.VoineyFeedback?.reset(", html)

    def test_the_page_runs_without_the_script(self) -> None:
        result = run_page_script(r"""
showExperimentEnded({status:"completed",report:"saved"});
""")
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
