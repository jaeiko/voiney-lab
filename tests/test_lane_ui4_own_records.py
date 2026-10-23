"""Lane UI4 (2026-10-23): lane OWN's screen hand-over ① (PR #114, ~/reports/lane_own_report.md 5절).

An administrator reads a member's experiment record, but its values (✓ 확인 · 고치기), its rating
and its written report ([보고서 문장 다시 만들기]) stay the owner's: the server already refuses them
(404). The page drew those buttons for any record it showed. A record is another person's when
its experiment's owner (the list's ``owner_principal_id``) -- or an owner the record names itself
-- is not who is signed in (/api/workspace/session's ``principal_id``); for it the values are
listed without buttons, with a line that says whose they are, the rewrite button stays hidden
and the rating is not asked. The signed-in person's own record keeps them all.

Shares tests/test_lane_ia_stage3.py's harness and tests/test_lane_ui4_owner.py's accounts.
"""

from __future__ import annotations

import unittest

from tests.test_lane_ia_stage3 import STAGE3
from tests.test_lane_ia_tabs import run
from tests.test_lane_ui4_owner import OWNER

RECORDS = r"""
const RATED=[];globalThis.VoineyFeedback={ended(id){RATED.push(["ended",id]);},report(id){RATED.push(["report",id]);},reset(){RATED.push(["reset"]);}};
const ITEMS=[{item_id:"i-1",step_label:"2",kind:"측정",text:"37 C",confirmed:false,corrected:false,source_key:"e-1"},{item_id:"i-2",step_label:"3",kind:"메모",text:"탁함",confirmed:true,corrected:false,source_key:"e-2"}];
const report=(sessionId,extra={})=>({report_id:`ER-${sessionId}`,session_id:sessionId,protocol_id:"p-1",status:"completed",started_at:"2026-10-19T01:00:00+00:00",event_count:0,events:[],anomaly_count:0,blocker_count:0,review:ITEMS,...extra});
const prose=()=>({state:"ready",label:"보고서 문장 준비됨"});
const checklistButtons=()=>labels(node("report-review-checklist"));
async function showReport(ws,value){deliver(ws,{type:"experiment.report.state",configuration_id:5,generation:0,report:value});await settle(6);}
"""


def run_records(body: str):
    return run(STAGE3 + OWNER + RECORDS + body)


class AnotherPersonsRecordTests(unittest.TestCase):

    def test_a_members_record_shown_to_an_administrator_has_no_buttons_that_write(self) -> None:
        result = run_records(r"""
await boot([["/prose",prose()],...asAdmin([THEIRS,MINE])]);
const ws=running("s-mine");
await showReport(ws,report("s-theirs"));
assert(node("report-review-checklist").hidden===false,"the values are not listed");
assert(visibleText(node("report-review-checklist")).includes("37 C"),"the value is missing");
assert(checklistButtons().length===0,`buttons on another person's record: ${checklistButtons()}`);
assert(visibleText(node("report-review-checklist")).includes("기록한 사람만"),`no line says whose they are: ${visibleText(node("report-review-checklist"))}`);
assert(node("report-prose-again").hidden===true,"the rewrite button shows on another person's record");
assert(!RATED.some(call=>call[0]==="report"&&call[1]==="ER-s-theirs"),`the rating was offered: ${JSON.stringify(RATED)}`);
assert(RATED.some(call=>call[0]==="reset"),`the rating box was not put away: ${JSON.stringify(RATED)}`);
// The administrator's own record keeps them all.
await showReport(ws,report("s-mine"));
assert(checklistButtons().includes("✓ 확인")&&checklistButtons().includes("고치기"),`own record: ${checklistButtons()}`);
assert(node("report-prose-again").hidden===false,"the rewrite button is missing on one's own record");
assert(RATED.some(call=>call[0]==="report"&&call[1]==="ER-s-mine"),`own record not rated: ${JSON.stringify(RATED)}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_an_owner_named_by_the_record_itself_counts_too(self) -> None:
        result = run_records(r"""
await boot([["/prose",prose()],...asAdmin([MINE])]);
const ws=running("s-mine");
await showReport(ws,report("s-unknown",{owner_principal_id:"pr-member"}));
assert(checklistButtons().length===0,`buttons: ${checklistButtons()}`);
// A record whose owner nobody names is drawn as before.
await showReport(ws,report("s-unknown"));
assert(checklistButtons().includes("고치기"),`unnamed owner: ${checklistButtons()}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
