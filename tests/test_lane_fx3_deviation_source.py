"""Lane FX3, decision 5: the after-run record shows a deviation's source value.

With the router first, a note whose value differs from the step's source
value is recorded as a deviation (lane RA, decision c), and the run card
showed "기준치와 다름 · 원문 …" -- but the report's event and the review item
lacked the values, so the 실험 후 record could only say "기준치와 다름".
Now the observation event carries ``deviations`` (each: what was said, its
number and unit, the source's value), ``report_review_items`` passes them on
for a 편차 item, and the record draws "기준치와 다름 · 원문 10 min".
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from tests.lane_n_support import VoiceNotesHarness, shown
from tests.lane_ra_support import FakeAsyncOpenAI
from tests.router_fakes import FakeRouterClient, tool_reply
from tests.test_lane_ui2_after_review import SETUP
from tests.test_screen_cleanup import run_page_script
from voiney_lab.experiment_reports import report_review_items

ROUTER_FIRST = {
    "VOINEY_LAB_LLM_ROUTER_ENABLED": "true", "VOINEY_LAB_ROUTER_FIRST": "true",
    "VOINEY_LAB_ROUTER_PROVIDER": "xai", "VOINEY_LAB_ROUTER_MODEL": "fake-router-model",
    "XAI_API_KEY": "test-only-not-a-key",
    "VOINEY_LAB_TRANSLATION_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "",
}
DEVIATION = {"kind": "time", "said": "15분", "number": "15", "unit": "min", "source": "10 min"}


def _event(key, category, wording, payload=None):
    return {"event_key": key, "event_type": "observation", "step_label": "2",
            "category": category, "user_wording": wording, "payload": payload or {}}


class ReviewItemTests(unittest.TestCase):
    def test_a_deviation_item_carries_the_values_of_its_event(self) -> None:
        items = report_review_items([
            _event("e-1", "deviation", "15분 동안 씻었어",
                   {"deviations": [DEVIATION, "not a value", {"kind": "speed"}]}),
            _event("e-2", "measurement", "500 µL 넣었어", {"deviations": []}),
            _event("e-3", "deviation", "편차로 기록해 줘 거품이 났어"),
        ])
        by_key = {item["source_key"]: item for item in items}
        self.assertEqual(by_key["e-1"]["kind"], "편차")
        self.assertEqual(by_key["e-1"]["deviations"], [DEVIATION, {"kind": "speed"}])
        # A deviation said as such, with no source value to compare, has none.
        self.assertEqual(by_key["e-3"]["deviations"], [])
        # Other kinds are as they were.
        self.assertNotIn("deviations", by_key["e-2"])


class VoiceTests(VoiceNotesHarness, unittest.TestCase):
    def test_the_recorded_event_and_the_review_item_hold_the_source_value(self) -> None:
        self.environment.update(ROUTER_FIRST)
        said = "15분 동안 씻었어, 특이사항으로 메모해 줘"
        FakeAsyncOpenAI.client = FakeRouterClient(tool_reply(("record_log", {
            "type": "deviation", "value": "15분 동안 씻었어", "evidence": said,
        })))

        async def scenario(socket, listener, say):
            await say(1, "프로토콜 시작해줘")
            await say(2, "1단계 완료했어")
            await say(3, said)

        socket = self.voice(scenario, patch("voiney_lab.server.AsyncOpenAI", FakeAsyncOpenAI))
        self.assertIn("원문은 10 min", shown(socket)[3])
        events = self.report()["events"]
        note = next(e for e in events if e["event_type"] == "observation")
        self.assertEqual(note["category"], "deviation")
        recorded = note["payload"]["deviations"]
        self.assertEqual([(d["kind"], d["said"], d["unit"], d["source"]) for d in recorded],
                         [("time", "15분", "min", "10 min")])
        item = next(i for i in report_review_items(events) if i["kind"] == "편차")
        self.assertEqual(item["deviations"], recorded)

    def test_a_note_without_a_deviation_records_no_values(self) -> None:
        self.environment.update(ROUTER_FIRST)
        said = "10분 동안 씻었어, 메모해 줘"
        FakeAsyncOpenAI.client = FakeRouterClient(tool_reply(("record_log", {
            "type": "measurement", "value": "10분 동안 씻었어", "evidence": said,
        })))

        async def scenario(socket, listener, say):
            await say(1, "프로토콜 시작해줘")
            await say(2, "1단계 완료했어")
            await say(3, said)

        self.voice(scenario, patch("voiney_lab.server.AsyncOpenAI", FakeAsyncOpenAI))
        note = next(e for e in self.report()["events"] if e["event_type"] == "observation")
        self.assertNotEqual(note["category"], "deviation")
        self.assertNotIn("deviations", note["payload"])


PAGE = SETUP + r"""
const deviation=(extra={})=>({item_id:"r.e-9",kind:"편차",step_label:"4",text:"40도 1000 rpm 으로 돌렸어",source_key:"e-9",corrected:false,original:"",confirmed:false,workspace_event_key:null,confirmed_when_recorded:false,...extra});
const textOf=item=>deep(reportReviewRow("ER-0000000000000000AAAA",item),"report-review-text")[0];
"""


class PageTests(unittest.TestCase):
    def test_the_record_draws_the_source_values_beside_the_tag(self) -> None:
        result = run_page_script(PAGE + r"""
const text=textOf(deviation({deviations:[
 {kind:"temperature",said:"40도",number:"40",unit:"°C",source:"37 °C"},
 {kind:"speed",said:"1000 rpm",number:"1000",unit:"rpm",source:"800 rpm"},
 {kind:"speed",said:"1000 rpm",number:"1000",unit:"rpm",source:"800 rpm"}]}));
assert(text.textContent==="편차: 40도 1000 rpm 으로 돌렸어 기준치와 다름 · 원문 37 °C · 800 rpm",text.textContent);
assert(deep(text,"ui-badge")[0].textContent==="기준치와 다름","the tag changed");
assert(deep(text,"report-review-source")[0].textContent===" · 원문 37 °C · 800 rpm",deep(text,"report-review-source")[0]?.textContent);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_without_source_values_the_row_is_as_before(self) -> None:
        result = run_page_script(PAGE + r"""
for(const extra of [{},{deviations:[]},{deviations:[{kind:"time"}]},{deviations:"37 °C"}]){
 const text=textOf(deviation(extra));
 assert(text.textContent==="편차: 40도 1000 rpm 으로 돌렸어 기준치와 다름",`${JSON.stringify(extra)}: ${text.textContent}`);
 assert(deep(text,"report-review-source").length===0,"a source line with no source value");
}
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
