"""Lane UI3, decision 5 (2026-10-14): the 실험 후 record shows a deviation.

The server's list of the report's values names a deviation note's kind
"편차" (``report_review_items``: a note the experimenter called a deviation
or, with the router first, one whose value differs from the source's -- lane
RA, decision c). The record marks such a value "기준치와 다름" where it is
listed and counts it beside the other counts; a record without one is drawn
as before. The source's value is not in the list, so it is not drawn.
"""

from __future__ import annotations

import unittest

from tests.test_lane_ui2_after_review import SETUP
from tests.test_screen_cleanup import run_page_script

DEVIATION = SETUP + r"""
const withDeviation=()=>[...items(),
 {item_id:"r.e-9",kind:"편차",step_label:"4",text:"40도 1000 rpm 으로 돌렸어",source_key:"e-9",corrected:false,original:"",confirmed:false,workspace_event_key:"voice-0-9-observation-record_observation",confirmed_when_recorded:false}];
const recordWith=list=>report({review:list,events:[...report().events,{event_key:"e-9",event_type:"observation",step_label:"4",category:"deviation",created_at:"2026-10-13T00:35:00Z",user_wording:"40도 1000 rpm 으로 돌렸어"}]});
"""


class DeviationTagTests(unittest.TestCase):

    def test_a_deviation_is_tagged_where_it_is_listed_and_counted(self) -> None:
        result = run_page_script(DEVIATION + r"""
await say({type:"experiment.report.state",generation:0,report:recordWith(withDeviation())});
const summary=kids(box(),"report-review-summary")[0];
assert(summary.textContent==="실험자 확인 2미확인 2정정 1이상 1편차 1",summary.textContent);
const step4=kids(box(),"report-review-step").find(step=>step.children[0].textContent==="4단계");
const row=step4.children.find(child=>child.className==="report-review-row"&&child.textContent.includes("40도"));
assert(row&&row.dataset.deviation==="true","the deviation row is not marked");
const text=deep(row,"report-review-text")[0];
assert(text.textContent==="편차: 40도 1000 rpm 으로 돌렸어 기준치와 다름",text.textContent);
const tag=deep(text,"ui-badge")[0];
assert(tag&&tag.textContent==="기준치와 다름"&&tag.dataset.kind==="warning",`tag: ${tag?.textContent} ${tag?.dataset?.kind}`);
assert(deep(row,"ui-badge").map(badge=>badge.textContent).join("|")==="기준치와 다름|미확인","the confirmation tag changed");
const other=step4.children.find(child=>child.className==="report-review-row"&&child.textContent.includes("젤 조각"));
assert(other.dataset.deviation!=="true"&&!other.textContent.includes("기준치"),"an anomaly was tagged as a deviation");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_the_m6_card_tags_a_pending_deviation_and_a_record_without_one_is_unchanged(self) -> None:
        result = run_page_script(DEVIATION + r"""
const first=[withDeviation()[3],...items(true)];
await say({type:"experiment.report.state",generation:0,report:recordWith(first)});
const card=kids(box(),"report-pending")[0];
assert(card.children[0].textContent==="미확인 기록 1건",card.children[0].textContent);
assert(deep(card,"report-review-text")[0].textContent==="4단계 편차: 40도 1000 rpm 으로 돌렸어 기준치와 다름",deep(card,"report-review-text")[0].textContent);
await say({type:"experiment.report.state",generation:0,report:report()});
const summary=kids(box(),"report-review-summary")[0];
assert(summary.textContent==="실험자 확인 2미확인 1정정 1이상 1",`a record without a deviation counts one: ${summary.textContent}`);
assert(!visibleText(box()).includes("기준치"),"기준치 on a record without a deviation");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
