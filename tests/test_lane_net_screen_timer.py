"""Lane NET, decision 3 (2026-10-18): the step timer goes on as a copy on the screen.

While the connection is lost the screen counts the step timer on from the
timer's end the server last sent, marked "연결 끊김 중 화면 타이머". Its
last minute and its end are told on the device -- the timer sound, a buzz
and the band -- each once, and only when they come while the connection is
lost; a timer shorter than the server's last-minute length (from the
heartbeat) gets no last minute, as from the server. The page reads every
timer on the server's clock: the difference between the server's clock and
the device's is measured again on every connection, from the heartbeat that
answers a ping.
"""

from __future__ import annotations

import unittest

from tests.lane_net_support import run_connection_script

TIMER = r"""
const iso=ms=>new Date(ms).toISOString();
function timerState(leftMs,duration=600,extra={}){
 const deadline=voineyConnection.serverNow()+leftMs;
 return runState({timers:{step:{state:leftMs>0?"running":"expired",duration_seconds:duration,remaining_seconds:Math.max(0,Math.round(leftMs/1000)),elapsed_seconds:duration-Math.round(leftMs/1000),step_index:3,step_id:"step-4",step_label:"4",deadline_at:iso(deadline),started_at:iso(deadline-duration*1000),name:`${duration/60}분`,bound:"exact",...extra}}});
}
async function showTimer(ws,leftMs,duration,extra){await ws.deliver({type:"protocol.fixture.state",configuration_id:CONFIGURATION_ID,action:"start_timer",state:timerState(leftMs,duration,extra)});}
const label=()=>node("run-timer").children.find(child=>child.id==="run-timer-offline")||null;
const alertLine=()=>{const line=band()?.children.find(child=>child.dataset.line==="timer");return line&&!line.hidden?line.textContent:null;};
"""


class ScreenTimerTests(unittest.TestCase):

    def run_page(self, body: str) -> None:
        result = run_connection_script(TIMER + body)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_timers_are_read_on_the_server_clock_measured_from_a_ping(self) -> None:
        self.run_page(r"""
const ws=await openRun();
assert(voineyConnection.serverNow()===Date.now(),"a difference before any measure");
await advance(1000);
const ping=ws.texts("client.ping").at(-1);
await advance(80);
// The server's clock is 90 s ahead; the ping took 40 ms, the heartbeat 75.
await ws.deliver({type:"server.heartbeat",server_time_ms:ping.sent_at_ms+90045,ping_seq:ping.seq,ping_sent_at_ms:ping.sent_at_ms,ping_received_ms:ping.sent_at_ms+90040});
const offset=voineyConnection.serverNow()-Date.now();
assert(Math.abs(offset-90000)<50,`measured ${offset}`);
await showTimer(ws,65000);
assert(node("run-timer-digits").textContent==="1:05",`digits on the device clock: ${node("run-timer-digits").textContent}`);
// A later heartbeat over a longer round trip does not replace the better measure.
await advance(1000);const late=ws.texts("client.ping").at(-1);await advance(900);
await ws.deliver({type:"server.heartbeat",server_time_ms:late.sent_at_ms+91000,ping_seq:late.seq,ping_sent_at_ms:late.sent_at_ms,ping_received_ms:late.sent_at_ms+90990});
assert(Math.abs(voineyConnection.serverNow()-Date.now()-offset)<1,"a worse measure replaced a better one");
""")

    def test_every_connection_measures_again(self) -> None:
        self.run_page(r"""
const ws=await openRun();
await ws.deliver({type:"server.heartbeat",server_time_ms:Date.now()+90000});
assert(Math.abs(voineyConnection.serverNow()-Date.now()-90000)<1,"first measure");
const pending=init(sessionGeneration),next=opened.at(-1);next.open();await pending;
assert(Math.abs(voineyConnection.serverNow()-Date.now()-90000)<1,"the old measure was dropped before a new one");
await next.deliver({type:"server.heartbeat",server_time_ms:Date.now()+30000});
assert(Math.abs(voineyConnection.serverNow()-Date.now()-30000)<1,`not measured again: ${voineyConnection.serverNow()-Date.now()}`);
""")

    def test_the_screen_timer_tells_its_last_minute_and_its_end_once(self) -> None:
        self.run_page(r"""
const ws=await openRun();
await showTimer(ws,65000,600);
ws.drop();await settle();
assert(label()?.hidden===false&&label().textContent==="연결 끊김 중 화면 타이머","no screen timer label");
assert(alertLine()===null,"an alert at the loss");
await advance(4000);
assert(alertLine()===null&&sounds.length===2,`told early: ${alertLine()} ${sounds}`);
await advance(1500);
assert(alertLine()==="화면 타이머 · 1분 남았어요",`last minute: ${alertLine()}`);
assert(JSON.stringify(sounds)==="[740,494,880]"&&buzzes.length===2,`sound and buzz: ${JSON.stringify(sounds)} ${buzzes.length}`);
assert(["0:59","1:00"].includes(node("run-timer-digits").textContent),`digits: ${node("run-timer-digits").textContent}`);
await advance(60000);
assert(alertLine()==="화면 타이머 · 타이머가 끝났어요",`end: ${alertLine()}`);
assert(node("run-timer-digits").textContent==="끝남",`digits at the end: ${node("run-timer-digits").textContent}`);
await advance(30000);
assert(node("run-timer-foot").textContent==="0:30 지남",`foot: ${node("run-timer-foot").textContent}`);
assert(JSON.stringify(sounds)==="[740,494,880,880]"&&buzzes.length===3,`told again: ${JSON.stringify(sounds)} ${buzzes.length}`);
""")

    def test_what_the_server_already_told_or_never_tells_is_not_told(self) -> None:
        for name, setup, wait, expected in (
            ("short timer: no last minute", "await showTimer(ws,65000,120);", 70000, "[740,494,880]"),
            ("already in its last minute", "await showTimer(ws,30000,600);", 35000, "[740,494,880]"),
            ("already over", "await showTimer(ws,-10000,600);", 90000, "[740,494]"),
            ("the server's own rule", 'await ws.deliver({type:"server.heartbeat",server_time_ms:Date.now(),timer_last_minute_from_seconds:100,timer_last_minute_seconds:60});await showTimer(ws,65000,120);', 70000, "[740,494,880,880]"),
        ):
            with self.subTest(name):
                self.run_page(r"""
const ws=await openRun();
""" + setup + r"""
ws.drop();await settle();
await advance(""" + str(wait) + r""");
assert(JSON.stringify(sounds)===""" + repr(expected) + r""",`sounds: ${JSON.stringify(sounds)}`);
""")

    def test_a_minimum_is_named_as_the_server_names_it(self) -> None:
        self.run_page(r"""
const ws=await openRun();
await showTimer(ws,65000,7200,{bound:"minimum",name:"최소 2시간"});
ws.drop();await settle();
await advance(6000);
assert(alertLine()==="화면 타이머 · 최소 시간까지 1분 남았어요",`last minute: ${alertLine()}`);
await advance(60000);
assert(alertLine()==="화면 타이머 · 최소 시간이 지났어요",`end: ${alertLine()}`);
""")

    def test_no_timer_no_label(self) -> None:
        self.run_page(r"""
const ws=await openRun();
ws.drop();await settle();await advance(5000);
assert(label()===null||label().hidden===true,"a screen timer label without a timer");
""")


if __name__ == "__main__":
    unittest.main()
