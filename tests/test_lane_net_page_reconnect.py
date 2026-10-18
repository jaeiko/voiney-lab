"""Lane NET, decision 4 (2026-10-18): the page tries again and the server's state takes over.

After a loss the page tries again 1, 2, 4 and 8 seconds apart and then
every 15 seconds (at once when the browser is back online). Each try reads
the experiment as the server has it now (its version; an experiment no
longer running ends the trying), opens a new socket and sends one
session.start that continues the same experiment, with "reconnect" (when
the connection was lost, on the server's clock, and why) -- nothing said
or pressed while the connection was lost goes with it. When the server has
started the session its state takes the place of the screen's copy, even
though the new session's states number from 0 again; the band turns to
"다시 연결됐어요" and goes, and the same microphone listens again. A refusal
is a failed try, three end the trying; a try that does not open in time
gives way to the next.
"""

from __future__ import annotations

import unittest

from tests.lane_net_support import run_connection_script

SERVER = r"""
const fetches=[];let experiment={session_id:EXPERIMENT_ID,status:"in_progress",version:7,protocol_id:PROTOCOL_ID};
const respond=(payload,status=200)=>({ok:status<400,status,headers:{get:()=>"application/json"},json:async()=>payload});
globalThis.fetch=async url=>{
 fetches.push(String(url));
 if(url===`/api/workspace/experiments/${EXPERIMENT_ID}`)return typeof experiment==="number"?respond({detail:"gone"},experiment):respond(experiment);
 if(url==="/api/workspace/experiments")return respond({experiments:[]});
 return respond({});
};
// A page ready for audio again, as after a session that ran: the microphone
// worklet and the output are already open.
function audioOpen(){micWorkletNode={port:{}};micContext={state:"running",resume:async()=>{}};playContext={...fakeOutput(),state:"running",sampleRate:48000};}
const tries=()=>opened.length-1;
async function serverTakes(ws,state={}){
 const start=ws.texts("session.start").at(-1);
 await ws.deliver({type:"session.ready",configuration_id:start.configuration_id,generation:2,mode:"cascade",language:"ko",input_language:"ko",protocol_id:PROTOCOL_ID,revision_id:REVISION_ID,research_capabilities:{},experiment_session_id:EXPERIMENT_ID,experiment_session_version:8,experiment_recovered:true,connection_restored:true});
 await ws.deliver({type:"protocol.fixture.state",configuration_id:start.configuration_id,action:"attached",state:{...runState({current_step_label:"5",current_step_id:"step-5",...state}),revision:0}});
 await ws.deliver({type:"session.started",state:"IDLE",pipeline:"cascade"});
 await settle(6);
 return start;
}
"""


class PageReconnectTests(unittest.TestCase):

    def run_page(self, body: str) -> None:
        result = run_connection_script(SERVER + body)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_tries_come_1_2_4_8_and_then_every_15_seconds_apart(self) -> None:
        self.run_page(r"""
const ws=await openRun();
ws.drop();await settle();
const lostAt=Date.now(),at=[];
for(let step=0;step<800&&at.length<7;step++){
 await advance(100);
 if(opened.length-1>at.length){at.push(Math.round((Date.now()-lostAt)/100)/10);const attempt=opened.at(-1);attempt.emit("error");attempt.drop();await settle();}
}
const gaps=at.map((value,index)=>Math.round((value-(index?at[index-1]:0))*10)/10);
assert(JSON.stringify(gaps)==="[1,2,4,8,15,15,15]",`gaps between tries: ${JSON.stringify(gaps)} at ${JSON.stringify(at)}`);
assert(band().hidden===false&&bandLine("tries").startsWith("7번 시도했어요 · 다음 시도"),`tries line: ${bandLine("tries")}`);
assert(!node("status").textContent.startsWith("오류"),`a failed try failed the page: ${node("status").textContent}`);
""")

    def test_a_try_continues_the_same_experiment_and_takes_nothing_from_the_loss(self) -> None:
        self.run_page(r"""
const ws=await openRun();
ws.drop();await settle();
const lostAt=Date.now();
togglePauseSession();sendControl("workflow.pause",{configuration_id:CONFIGURATION_ID});
await advance(1000);
assert(fetches.includes(`/api/workspace/experiments/${EXPERIMENT_ID}`),`the experiment was not read first: ${fetches}`);
const next=opened.at(-1);assert(next!==ws,"no new socket");
assert(bandLine("tries")==="다시 연결하는 중… · 1번째 시도",`tries line: ${bandLine("tries")}`);
next.open();await settle(6);
const sent=next.texts().filter(item=>item.type!=="client.ping");
assert(sent.length===1&&sent[0].type==="session.start",`sent on the new socket: ${JSON.stringify(sent)}`);
const start=sent[0];
assert(start.experiment_session_id===EXPERIMENT_ID&&start.experiment_session_version===7,`not the experiment as the server has it: ${JSON.stringify(start)}`);
assert(start.protocol_id===PROTOCOL_ID&&start.mode==="cascade"&&start.language==="ko"&&!("input_language" in start),`not the run's configuration: ${JSON.stringify(start)}`);
assert(start.reconnect?.reason==="socket_closed"&&start.reconnect.lost_at_ms===lostAt,`reconnect: ${JSON.stringify(start.reconnect)}`);
assert(start.configuration_id>CONFIGURATION_ID,"the start reused the lost session's configuration id");
""")

    def test_the_servers_state_takes_the_place_of_the_screen_copy(self) -> None:
        self.run_page(r"""
const ws=await openRun({timers:{step:{state:"running",duration_seconds:600,remaining_seconds:120,elapsed_seconds:480,step_index:3,step_id:"step-4",step_label:"4",deadline_at:new Date(Date.now()+120000).toISOString(),started_at:new Date(Date.now()-480000).toISOString(),name:"10분",bound:"exact"}}});
audioOpen();
ws.drop();await settle();
assert(micTrack.enabled===false,"the microphone listened while lost");
await advance(1000);const next=opened.at(-1);next.open();await settle(6);
await serverTakes(next);
assert(node("procedure-progress").textContent.startsWith("5단계"),`the server's step is not on the screen: ${node("procedure-progress").textContent}`);
assert(socket===next&&sessionActive===true,"the page is not on the new session");
assert(micTrack.enabled===true,"the microphone did not listen again");
assert(band().hidden===false&&bandLine("title")==="다시 연결됐어요 — 서버 상태로 바꿨어요"&&document.body.dataset.connection==="restored",`restored band: ${bandLine("title")}`);
const label=node("run-timer").children.find(child=>child.id==="run-timer-offline");
assert(!label||label.hidden===true,"the screen timer label stayed");
assert(next.texts("client.audio_ready").length===1,"audio not reported ready to the new session");
assert(next.texts("client.voice_calibration").length===0,"the wearer was asked to read again");
let tap=tapEvent();assert(!tap.prevented,"controls stayed read only after the server took over");
await advance(4000);
assert(band().hidden===true&&document.body.dataset.connection==="","the band stayed");
await advance(60000);
assert(opened.length===2,`tried again after the server took over: ${opened.length}`);
""")

    def test_a_paused_run_comes_back_paused(self) -> None:
        self.run_page(r"""
const ws=await openRun({workflow_status:"paused"});
audioOpen();
ws.drop();await settle();
await advance(1000);const next=opened.at(-1);next.open();await settle(6);
await serverTakes(next,{workflow_status:"paused"});
assert(workflowPaused===true,"the page shows a paused run as running");
assert(node("rail-pause-session").textContent==="▶ 다시 시작",`pause control: ${node("rail-pause-session").textContent}`);
""")

    def test_a_refusal_is_a_failed_try_and_three_end_the_trying(self) -> None:
        self.run_page(r"""
const ws=await openRun();
ws.drop();await settle();
for(let refusal=1;refusal<=3;refusal++){
 for(let step=0;step<300&&opened.length-1<refusal;step++)await advance(100);
 const next=opened.at(-1);next.open();await settle(6);
 await next.deliver({type:"error",message:"invalid session configuration"});
 if(refusal<3)assert(band().hidden===false&&!node("status").textContent.startsWith("오류"),`refusal ${refusal} failed the page: ${node("status").textContent}`);
}
assert(band().hidden===true,"the band stayed after the trying ended");
assert(sessionActive===false&&curatedProtocolState.attached!==true,"the run stayed after the trying ended");
assert(node("status").textContent.startsWith("다시 연결됐지만 서버가 이 실험을 이어 받지 않았어요"),`status: ${node("status").textContent}`);
await advance(60000);assert(opened.length===4,`tried after giving up: ${opened.length}`);
""")

    def test_an_experiment_ended_elsewhere_ends_the_trying(self) -> None:
        for name, answer, words in (
            ("stopped", '{session_id:EXPERIMENT_ID,status:"stopped",version:9}', "다시 연결됐지만 이 실험은 이미 끝났어요"),
            ("gone", "404", "다시 연결됐지만 이 실험을 찾지 못했어요"),
        ):
            with self.subTest(name):
                self.run_page(r"""
const ws=await openRun();
experiment=""" + answer + r""";
ws.drop();await settle();
await advance(1000);
assert(opened.length===1,"a socket opened for an experiment that cannot continue");
assert(band().hidden===true&&sessionActive===false,"the run stayed");
assert(node("status").textContent.startsWith(""" + repr(words) + r"""),`status: ${node("status").textContent}`);
""")

    def test_back_online_tries_at_once(self) -> None:
        self.run_page(r"""
const ws=await openRun();
fire("offline");
await advance(300);
assert(fetches.length===0,"tried before its time while offline");
fire("online");await settle(6);
assert(fetches.includes(`/api/workspace/experiments/${EXPERIMENT_ID}`)&&opened.length===2,`no try on coming back online: ${fetches} ${opened.length}`);
""")

    def test_a_try_that_never_opens_gives_way_to_the_next(self) -> None:
        self.run_page(r"""
const ws=await openRun();
ws.drop();await settle();
await advance(1000);
const hung=opened.at(-1);
await advance(11900);
assert(hung.closedWith===null,"given up before its time");
await advance(200);
assert(hung.closedWith?.code===4001,`a try that never opened was kept: ${JSON.stringify(hung.closedWith)}`);
await advance(2000);
assert(opened.length===3,`the next try did not follow at its time: ${opened.length}`);
""")


if __name__ == "__main__":
    unittest.main()
