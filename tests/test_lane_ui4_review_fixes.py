"""Lane UI4 self-review (2026-10-23, ~/reports/review_ui4.md): what the first pass left.

R1. Another member's experiment picked in the records tab (an administrator reads the lab's)
still offered the ledger's writes -- [현재 단계에 기록], [현재 단계에 첨부], [체크포인트에서 새 실험]
-- which the server refuses (writes stay the owner's, lane OWN), and the resume line told the
administrator to press '실험 이어하기', which QA-22 no longer offers; the list mixed the lab's
experiments with nothing to say whose (lane QA a/F4). For another person's experiment the
ledger's writing part is put away, its list row and record line say so, and the resume line says
that continuing and recording are the owner's.

Shares tests/test_lane_ia_tabs.py's harness and tests/test_lane_ui4_owner.py's accounts.
"""

from __future__ import annotations

import unittest

from tests.test_lane_ia_tabs import run
from tests.test_lane_ui4_owner import OWNER

FIXES = r"""
const stoppedTheirs={...THEIRS,status:"stopped"};
const withCheckpoints=item=>({...timelineOf(item),recovery:{eligible:false,restored:{protocol_id:"p-1",protocol_revision_id:"pdf-1",current_step_id:"s3",current_step_label:"3",completed_step_count:2},not_restored:[],next_action:"checkpoint_restart",checkpoints:[{step_id:"s3",step_label:"3",carried_step_count:2,stopped_here:true}]}});
"""


def run_fixes(body: str):
    return run(OWNER + FIXES + body)


class AnotherMembersLedgerTests(unittest.TestCase):

    def test_another_members_experiment_offers_no_ledger_writes(self) -> None:
        result = run_fixes(r"""
await boot(asAdmin([THEIRS,MINE]));
go("#/records");
const rows=walk(node("records-list")).filter(el=>hasClass(el,"records-row")).map(el=>el.textContent);
assert(rows.length===2&&rows[0].includes("다른 사람의 실험")&&!rows[1].includes("다른 사람의 실험"),`list: ${JSON.stringify(rows)}`);
go("#/records/s-theirs");await settle(8);
assert(currentExperimentSessionId==="s-theirs","the record did not open");
assert(node("experiment-ledger-capture").hidden===true,"the ledger's writes show on another member's experiment");
assert(node("records-detail-line").textContent.includes("다른 사람의 실험"),`line: ${node("records-detail-line").textContent}`);
const said=node("experiment-resume-disclosure").textContent;
assert(!said.includes("실험 이어하기")&&said.includes("실험한 사람만"),`resume line: ${said}`);
// One's own keeps them.
go("#/records/s-mine");await settle(8);
assert(node("experiment-ledger-capture").hidden===false,"one's own ledger lost its writes");
assert(!node("records-detail-line").textContent.includes("다른 사람의 실험"),node("records-detail-line").textContent);
assert(node("experiment-resume-disclosure").textContent.includes("실험 이어하기"),node("experiment-resume-disclosure").textContent);
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_another_members_stopped_experiment_offers_no_checkpoint_restart(self) -> None:
        result = run_fixes(r"""
await boot([["/api/workspace/experiments/s-theirs/timeline",withCheckpoints(stoppedTheirs)],["/api/workspace/experiments/s-mine/timeline",withCheckpoints({...MINE,status:"stopped"})],...asAdmin([stoppedTheirs,{...MINE,status:"stopped"}])]);
go("#/records/s-theirs");await settle(8);
assert(node("experiment-checkpoint-restart").hidden===true,"a checkpoint restart is offered on another member's experiment");
go("#/records/s-mine");await settle(8);
assert(node("experiment-checkpoint-restart").hidden===false,"one's own stopped experiment lost its checkpoint restart");
""")
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
