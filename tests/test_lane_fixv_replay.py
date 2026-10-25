"""Lane FIXV decision 1 (2026-10-24): "다시 들려줘" replays the whole last turn.

Since lane VOX a turn's answer arrives as one sound piece per sentence. The
page kept only the piece it played last, so "방금 답변 다시 들려줘" or
"안 들려" after a three-sentence answer gave back its last sentence. The page
now keeps every piece one turn played, in order; the first piece of a new
turn starts the kept sound over; the turn that asked for the replay never
replaces it; and the kept sound stops at a bound (120 s of 16 kHz PCM16)
with the pieces past it left out whole.

Read through the page's own script in node with a fake playback context
(contract-tested; a person hears it in the voice test).
"""

from __future__ import annotations

import unittest

from tests.test_screen_cleanup import run_page_script as _run_page_script


def run_page_script(body: str):
    """The page script with the body; a body that never reaches its end fails."""
    result = _run_page_script(body + '\nconsole.log("TEST-END");')
    if result.returncode == 0 and "TEST-END" not in result.stdout:
        result.returncode = 3
        result.stderr = "the test body did not reach its end\n" + result.stderr
    return result


# A cascade session at turn level; every piece is played to its end by hand.
SETUP = r"""
const configuration_id=7,generation=9;
acceptedSessionConfiguration={configuration_id,mode:"cascade",language:"ko",protocol_id:"p-fixv",revision_id:"pdf-1"};
sessionActive=true;pipelineMode="cascade";socket=new WS();
const settle=()=>new Promise(resolve=>setTimeout(resolve,0));
// What each started source held, as the PCM16 values it was given.
const played=[];let lastSource=null;
playContext={resume:async()=>{},currentTime:0,destination:{},
 createBuffer:(_channels,length)=>{const data=new Float32Array(length);return {length,getChannelData:()=>data};},
 createBufferSource:()=>({buffer:null,onended:null,connect(){},disconnect(){},stop(){},start(){played.push(Int16Array.from(this.buffer.getChannelData(0),x=>Math.round(x*32768)));lastSource=this;}})};
async function say(message){await onMessage({data:JSON.stringify({configuration_id,generation,...message})},sessionGeneration,socket);await settle();}
async function bytes(values){await onMessage({data:new Int16Array(values).buffer},sessionGeneration,socket);}
async function arrive(turn,index,values){await say({type:"audio.segment.start",turn_id:turn,segment_index:index});await bytes(values);await say({type:"audio.segment.end",turn_id:turn,segment_index:index});}
async function playedToEnd(){const source=lastSource;assert(source&&source.onended,"no piece is playing");source.onended();await settle();}
async function finish(turn,count){await say({type:"audio.complete",turn_id:turn,segment_count:count});await say({type:"turn.done",turn_id:turn,timings_ms:{},segment_count:count,route:"curated_protocol"});}
// One turn whose pieces arrive while the previous one plays, as lane VOX sends them.
async function turn(id,pieces,{replay=false}={}){
 await say({type:"speech.start",turn_id:id});
 if(replay)await say({type:"server.operation",turn_id:id,route:"curated_protocol",operation:"audio_replay_request"});
 for(const [index,values] of pieces.entries()){await arrive(id,index,values);await playedToEnd();}
 if(replay)await say({type:"audio.replay.request",turn_id:id,replay_count:1,state_mutation:false});
 await finish(id,pieces.length);
}
const values=buffer=>Array.from(new Int16Array(buffer||new ArrayBuffer(0)));
const same=(a,b)=>a.length===b.length&&a.every((x,i)=>x===b[i]);
"""


class WholeTurnReplayTests(unittest.TestCase):

    def test_a_voice_replay_plays_every_sentence_of_the_last_answer_in_order(self) -> None:
        result = run_page_script(SETUP + r"""
await turn(1,[[11,12],[21,22,23],[31]]);
assert(same(values(lastReplayableAudio),[11,12,21,22,23,31]),`kept sound is not the whole turn: ${values(lastReplayableAudio)}`);
// "방금 답변 다시 들려줘": the server's short reply plays, then the kept turn once.
await turn(2,[[90,91]],{replay:true});
await playedToEnd();
assert(same(Array.from(played.at(-1)),[11,12,21,22,23,31]),`the replay played ${Array.from(played.at(-1))}`);
assert(same(values(lastReplayableAudio),[11,12,21,22,23,31]),"the turn that asked for the replay replaced the kept answer");
// "안 들려" right after: the same answer again, not the replay turn's own reply.
await turn(3,[[95]],{replay:true});
await playedToEnd();
assert(same(Array.from(played.at(-1)),[11,12,21,22,23,31]),`the second replay played ${Array.from(played.at(-1))}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_pieces_that_arrive_before_the_first_one_ends_are_kept_in_their_order(self) -> None:
        result = run_page_script(SETUP + r"""
await say({type:"speech.start",turn_id:1});
// The second sentence arrives first in time of reading; the queue plays by index.
await arrive(1,0,[1,1]);await arrive(1,2,[3,3]);await arrive(1,1,[2,2]);
await playedToEnd();await playedToEnd();await playedToEnd();
await finish(1,3);
assert(same(values(lastReplayableAudio),[1,1,2,2,3,3]),`kept order is wrong: ${values(lastReplayableAudio)}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_new_turn_starts_the_kept_sound_over(self) -> None:
        result = run_page_script(SETUP + r"""
await turn(1,[[1],[2],[3]]);
await turn(2,[[7,7],[8]]);
assert(same(values(lastReplayableAudio),[7,7,8]),`the new turn did not replace the old one: ${values(lastReplayableAudio)}`);
// The button path reads the same kept sound.
const ok=await playLastReplayableAudio(null);
assert(ok&&same(Array.from(played.at(-1)),[7,7,8]),`the replay button played ${Array.from(played.at(-1)||[])}`);
// A new session forgets it.
resetSessionUI();
assert(lastReplayableAudio===null,"a new session kept the last session's answer");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_new_connection_starts_over_even_when_its_turn_ids_repeat(self) -> None:
        # Each connection is a new server session: its turn ids and
        # generations start again, so "9:3" on the next socket is a new turn.
        result = run_page_script(SETUP + r"""
await turn(3,[[1,1],[2]]);
socket=new WS();
await turn(3,[[7,7]]);
assert(same(values(lastReplayableAudio),[7,7]),`the next connection's turn was added to the last one: ${values(lastReplayableAudio)}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_kept_sound_stops_at_its_bound_with_whole_pieces(self) -> None:
        result = run_page_script(SETUP + r"""
assert(REPLAY_AUDIO_MAX_BYTES===120*16000*2,`the bound is ${REPLAY_AUDIO_MAX_BYTES}`);
const seconds=(n,value)=>new Array(n*16000).fill(value);
// 50 s + 50 s fit; the third 50 s piece is left out whole, and so is a short
// piece after it (no gap in the middle of the replay).
await turn(1,[seconds(50,1),seconds(50,2),seconds(50,3),[4,4]]);
const kept=new Int16Array(lastReplayableAudio);
assert(kept.length===100*16000,`kept ${kept.length} samples`);
assert(kept[0]===1&&kept[50*16000]===2&&kept.at(-1)===2,"the kept pieces are not the first two");
// One piece longer than the bound is cut at the bound.
await turn(2,[seconds(130,5)]);
assert(lastReplayableAudio.byteLength===REPLAY_AUDIO_MAX_BYTES,`a long piece kept ${lastReplayableAudio.byteLength} bytes`);
// And the next turn starts over below the bound again.
await turn(3,[[6],[7]]);
assert(same(values(lastReplayableAudio),[6,7]),`after a bounded turn the next kept ${values(lastReplayableAudio).slice(0,4)}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
