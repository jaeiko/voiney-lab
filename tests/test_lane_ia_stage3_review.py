"""Lane IA stage ③, the lane's own review of its diff (2026-10-21, ~/reports/review_ia.md).

F1 a 401 that is not a lost session (a wrong current password: ``login_refused``) signed the
   person out; only the gate's ``authentication_required`` does now.
F2 the voice preview the sheet allows over the end screen was cut at the next redraw.
F3 a second [종료] while the first waited for the server's answer closed the socket and lost it.
F4 the start step pointed at the records tab's experiment select, which decision ⑤ moved into
   one record's screen; the step has its own [이어하지 않고 새로 시작].
"""

from __future__ import annotations

import unittest

from tests.test_lane_ia_bugs import AUDIO
from tests.test_lane_ia_stage3 import STAGE3
from tests.test_lane_ia_tabs import run


def run_review(body: str):
    return run(STAGE3 + AUDIO + body)


class ReviewTests(unittest.TestCase):
    def test_f1_a_wrong_current_password_keeps_the_person_signed_in(self) -> None:
        result = run_review(r"""
let replaced=null;Object.assign(globalThis.location,{pathname:"/",search:"",replace(url){replaced=url;},reload(){}});
await boot([["/api/auth/password",()=>unauthorized("login_refused")],...base(ACCOUNT_SESSION(true))]);
go("#/settings");
node("account-password-current").value="wrong-one";node("account-password-new").value="password2";
await changeAccountPassword();await settle(4);
assert(replaced===null&&document.body.dataset.auth==="ok",`a wrong password signed out: ${replaced} ${document.body.dataset.auth}`);
assert(node("account-password-status").textContent.length>0,"no word on the wrong password");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_f2_a_preview_over_the_end_screen_survives_a_redraw(self) -> None:
        result = run_review(r"""
await boot(previewAnswers());
go("#/experiment");pickProtocolFromList("p-1");await settle(8);
const ws=running();
await deliver(ws,{type:"experiment.ended",configuration_id:5,status:"completed",report:"saved"});await stopSession();await settle(4);
assert(document.body.dataset.view==="after",document.body.dataset.view);
openWizardSheet("agent");
asides(body())[0].dispatch("click");await settle(8);
assert(FakeAudio.made.length===1&&!FakeAudio.made[0].paused,"the preview did not play over the end screen");
renderScreenView();renderExperimentContext();
assert(!FakeAudio.made[0].paused&&wizard.previewing!==null,"a redraw cut the preview over the end screen");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_f3_a_second_stop_while_waiting_keeps_the_answer(self) -> None:
        result = run_review(r"""
await boot(withExperiments([{...RESUMABLE,status:"in_progress"}]));
go("#/experiment");pickProtocolFromList("p-1");await settle(8);
const ws=running();
const first=stopSession();await settle(2);
const second=stopSession();await settle(2);
assert(ws.readyState===1,"the second press closed the socket before the server answered");
await deliver(ws,{type:"experiment.session.state",state:{session_id:"s-9",version:5,status:"stopped"}});
await deliver(ws,{type:"session.stopped",state:"IDLE"});
await first;await second;await settle(8);
assert(document.body.dataset.view==="after",`view: ${document.body.dataset.view}`);
assert(sentTypes(ws).filter(type=>type==="session.stop").length===1,`stops sent: ${sentTypes(ws)}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_f4_the_start_step_offers_its_own_way_to_start_afresh(self) -> None:
        result = run_review(r"""
await boot(withExperiments([RESUMABLE]));
go("#/experiment");pickProtocolFromList("p-1");chooseExperimentSession("s-9",{force:true});await settle(8);
openWizardSheet("start");await settle(2);
const text=visibleText(body());
assert(!text.includes("기록 탭에서 실험 선택을 비우세요"),`the step points at a select on another screen: ${text}`);
const fresh=walk(body()).find(el=>el.tagName==="button"&&el.textContent==="이어하지 않고 새로 시작");
assert(fresh,`no way to start afresh: ${labels(body())}`);
fresh.dispatch("click");await settle(4);
assert(!experimentIsResumable()&&primary().textContent==="이 프로토콜로 시작",`after: ${experimentIsResumable()} ${primary().textContent}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr)

if __name__ == "__main__":
    unittest.main()
