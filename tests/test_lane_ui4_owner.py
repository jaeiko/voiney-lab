"""Lane UI4 (2026-10-23): lane QA's QA-22 -- an administrator's home "이어서 하기" pointed at
another member's experiment.

The server gives an administrator the lab's experiments (lane ON decision ①), but only the
owner may continue one (workspace_store "Writes stay the owner's"): the card picked the latest
resumable experiment of anyone, the sheet's [실험 이어하기] sent its session and the server
refused it ("invalid session configuration"), and the administrator's own paused experiment was
hidden behind it. The list items carry ``owner_principal_id`` and /api/workspace/session the
``principal_id`` of who is signed in; the page now offers to continue only an experiment whose
owner is that person. An owner the server does not name (an older server) is not taken for
another person's.

Shares tests/test_lane_ia_tabs.py's harness (the ON wizard with the tab router).
"""

from __future__ import annotations

import unittest

from tests.test_lane_ia_tabs import run

OWNER = r"""
const ADMIN_SESSION={mode:"accounts",signed_in:true,account:{account_id:"a-admin",name:"관리자",admin:true,entitlement:"pilot",organization_id:"lab-pilot",disabled:false}};
const WORKSPACE={display_name:"관리자",principal_id:"pr-admin",organization_id:"lab-pilot",workspaces:["researcher"],authentication_method:"account"};
const MINE={...RESUMABLE,session_id:"s-mine",owner_principal_id:"pr-admin",current_step_label:"2",started_at:"2026-10-19T00:30:00+00:00"};
const THEIRS={...RESUMABLE,session_id:"s-theirs",owner_principal_id:"pr-member",status:"in_progress",current_step_label:"7",started_at:"2026-10-19T02:00:00+00:00"};
const timelineOf=item=>({...TIMELINE(item.status),session:{...TIMELINE(item.status).session,session_id:item.session_id,status:item.status,current_step_label:item.current_step_label}});
const asAdmin=items=>[["/api/workspace/session",WORKSPACE],["/api/workspace/experiments/s-mine/timeline",timelineOf(MINE)],["/api/workspace/experiments/s-theirs/timeline",timelineOf(THEIRS)],["/api/workspace/experiments",{experiments:items}],...base(ADMIN_SESSION)];
const resumeCard=()=>cards().find(card=>card.dataset.card==="resume")||null;
"""


def run_owner(body: str):
    return run(OWNER + body)


class OwnExperimentTests(unittest.TestCase):

    def test_an_administrators_home_continues_only_their_own_experiment(self) -> None:
        result = run_owner(r"""
await boot(asAdmin([THEIRS,MINE]));
go("#/home");
const card=resumeCard();
assert(card,`no resume card: ${cardWords()}`);
assert(card.textContent.includes("2단계")&&!card.textContent.includes("7단계"),`the card points at: ${card.textContent}`);
card.dispatch("click");await settle(8);
assert(currentExperimentSessionId==="s-mine",`continues: ${currentExperimentSessionId}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_another_members_experiment_alone_is_not_offered_to_continue(self) -> None:
        result = run_owner(r"""
await boot(asAdmin([THEIRS]));
go("#/home");
assert(resumeCard()===null,`another member's experiment is offered: ${cardWords()}`);
// Picked in the records (an administrator reads it), it is not continued from here either.
go("#/records/s-theirs");await settle(8);
assert(currentExperimentSessionId==="s-theirs","the record did not open");
assert(experimentIsResumable()===false,"another member's experiment counts as resumable");
go("#/experiment");
assert(node("experiment-start-title").textContent!=="실험 이어하기",`the tab offers: ${node("experiment-start-title").textContent}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_an_owner_the_server_does_not_name_is_not_taken_for_another(self) -> None:
        result = run_owner(r"""
const unnamed={...MINE,owner_principal_id:undefined};
await boot(asAdmin([unnamed]));
go("#/home");
assert(resumeCard(),`an experiment without a named owner is not offered: ${cardWords()}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
