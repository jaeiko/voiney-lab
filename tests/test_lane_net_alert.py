"""Lane NET, decision 2 (2026-10-18): a lost connection is said loudly; the screen turns read only.

A band across the whole screen says "연결이 끊겼어요 — 다시 연결 중 (n초)",
counting the seconds since the loss, with one short sound and the phone's
buzz where it has one. The current step, the next step and the safety notes
the server last sent stay on the screen, in the run view. The microphone is
paused ("마이크 멈춤 · 연결되면 다시 들어요"), and nothing is sent or kept to
send later: a tap on any control outside the band goes nowhere. A run that
cannot be continued (no durable experiment) keeps the page's own close
handling.
"""

from __future__ import annotations

import unittest

from tests.lane_net_support import run_connection_script

LOST = "연결이 끊겼어요 — 다시 연결 중"
MIC = "마이크 멈춤 · 연결되면 다시 들어요"


class LostConnectionAlertTests(unittest.TestCase):

    def run_page(self, body: str) -> None:
        result = run_connection_script(body)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_the_band_counts_the_seconds_and_the_run_stays_on_the_screen(self) -> None:
        self.run_page(r"""
const ws=await openRun({warning_texts:["Wear gloves."]});
const step=node("procedure-primary").textContent,next=node("run-next-step").textContent,safety=node("procedure-warning").textContent;
assert(step.includes("37 °C에서 둡니다."),`no step before the loss: ${step}`);
assert(next.includes("상층액을 버립니다")&&safety.includes("Wear gloves."),`next step or safety missing before the loss: ${next} / ${safety}`);
ws.drop();await settle();
assert(band()&&band().hidden===false,"no band");
assert(bandLine("title")==="연결이 끊겼어요 — 다시 연결 중 (0초)",`title: ${bandLine("title")}`);
await advance(5000);
assert(bandLine("title")==="연결이 끊겼어요 — 다시 연결 중 (5초)",`after 5 s: ${bandLine("title")}`);
assert(bandLine("mic")==="마이크 멈춤 · 연결되면 다시 들어요",`mic line: ${bandLine("mic")}`);
assert(bandLine("screen").startsWith("화면을 켜 두세요"),`screen line: ${bandLine("screen")}`);
assert(document.body.dataset.connection==="lost"&&document.body.dataset.view==="during",`view: ${JSON.stringify(document.body.dataset)}`);
assert(node("procedure-primary").textContent===step,"the step left the screen");
assert(node("run-next-step").textContent===next&&node("run-next-step").hidden===false,"the next step left the screen");
assert(node("procedure-warning").textContent===safety&&node("procedure-warning").hidden===false,"the safety note left the screen");
assert(curatedProtocolState.attached===true&&curatedProtocolState.current_step_label==="4","the server's last state was dropped");
assert(node("run-mic").textContent==="마이크 멈춤 · 연결되면 다시 들어요",`run-mic: ${node("run-mic").textContent}`);
assert(micTrack.enabled===false,"the microphone still listens");
assert(node("status").textContent==="연결이 끊겼어요 — 다시 연결 중",`status: ${node("status").textContent}`);
""")

    def test_one_short_sound_and_one_buzz(self) -> None:
        self.run_page(r"""
const ws=await openRun();
ws.emit("error");ws.drop();await settle();
await advance(10000);
assert(JSON.stringify(sounds)==="[740,494]",`sounds: ${JSON.stringify(sounds)}`);
assert(buzzes.length===1,`buzzes: ${JSON.stringify(buzzes)}`);
assert(!node("status").textContent.startsWith("오류"),`an error on the run's socket failed the page: ${node("status").textContent}`);
""")

    def test_nothing_is_sent_or_kept_while_lost(self) -> None:
        self.run_page(r"""
const ws=await openRun();
const before=ws.sent.length;
ws.drop();await settle();
sendControl("workflow.pause",{configuration_id:CONFIGURATION_ID});togglePauseSession();
await advance(20000);
assert(socket===null,"the page still holds a socket");
assert(ws.sent.length===before,`sent on the lost socket: ${ws.sent.slice(before)}`);
""")

    def test_a_tap_on_a_control_goes_nowhere_but_the_band_stays_live(self) -> None:
        self.run_page(r"""
const ws=await openRun();
let tap=tapEvent();
assert(!tap.prevented&&!tap.stopped,"a tap was stopped before the loss");
ws.drop();await settle();
tap=tapEvent();
assert(tap.prevented&&tap.stopped,"a tap on a control went through while lost");
tap=tapEvent(true);
assert(!tap.prevented&&!tap.stopped,"a tap in the band was stopped");
""")

    def test_offline_and_silence_say_it_the_same_way(self) -> None:
        for trigger in ('fire("offline");', "await advance(3000);"):
            with self.subTest(trigger=trigger):
                self.run_page(r"""
const ws=await openRun();
""" + trigger + r"""
await settle();
assert(band()?.hidden===false&&bandLine("title").startsWith("연결이 끊겼어요 — 다시 연결 중"),"no band");
assert(sounds.length===2&&buzzes.length===1,"not said once");
""")

    def test_a_run_that_cannot_be_continued_keeps_the_page_close(self) -> None:
        self.run_page(r"""
const ws=await openRun();
acceptedSessionConfiguration={...acceptedSessionConfiguration,experiment_session_id:null,experiment_session_version:null};currentExperimentSessionId=null;
ws.drop();await settle();
assert(band()===null,"a band for a run that cannot be continued");
assert(sessionActive===false&&node("status").textContent.includes("연결이 예기치 않게 종료됐습니다"),`status: ${node("status").textContent}`);
""")


if __name__ == "__main__":
    unittest.main()
