"""Lane SP1, decision 4 (2026-10-10): the voices not heard, one small line each, folded away.

A voice the server took for another person's (decision 1) was never
transcribed, so there are no words to show; the conversation keeps one
small line with the time -- "다른 사람 말로 보여 듣지 않았어요 · 14:32" --
in a list folded under it, and nothing else about it: no level, no record,
no report. A new session starts the list afresh.
"""

from __future__ import annotations

import unittest

from tests.test_screen_cleanup import run_page_script

SETUP = r"""
const configuration_id=71;
acceptedSessionConfiguration={configuration_id,mode:"cascade",language:"ko",server_generation:3,protocol_id:"protocol-x",revision_id:"pdf-1-analysis-1"};sessionActive=true;socket=new WS();
async function say(message){await onMessage({data:JSON.stringify(message)},sessionGeneration,socket);}
const ignored=(turn_id,extra={})=>({type:"speech.ignored",turn_id,generation:3,state:"COOLDOWN",voiced_frames:30,total_frames:45,prefix_frames_retained:12,duration_ms:900,reason:"other_speaker_level",forced:false,level_db:-40.2,reference_db:-24.3,margin_db:12,sensitivity:"normal",during_playback:false,ignored_count:1,...extra});
const lines=()=>node("ignored-speech-list").children.map(item=>item.textContent);
resetIgnoredSpeech();  // the fake DOM does not read the markup's hidden attribute
"""


class IgnoredListTests(unittest.TestCase):

    def test_each_ignored_voice_is_one_line_with_the_time_and_no_words(self) -> None:
        result = run_page_script(SETUP + r"""
assert(node("ignored-speech").hidden,"the list shows before anything was ignored");
await say(ignored(5));
assert(!node("ignored-speech").hidden,"the list stays hidden");
assert(node("ignored-speech-summary").textContent==="다른 사람 말로 보여 듣지 않은 말 1개",`summary: ${node("ignored-speech-summary").textContent}`);
assert(lines().length===1&&/^다른 사람 말로 보여 듣지 않았어요 · \d{2}:\d{2}$/.test(lines()[0]),`line: ${JSON.stringify(lines())}`);
await say(ignored(6,{during_playback:true}));
assert(node("ignored-speech-summary").textContent.endsWith("2개"),`summary: ${node("ignored-speech-summary").textContent}`);
assert(lines().length===2&&lines()[0].endsWith("· 답하는 중"),`lines: ${JSON.stringify(lines())}`);
const all=node("ignored-speech").textContent+node("log").textContent;
for(const secret of ["-40.2","-24.3","level","dB"])assert(!all.includes(secret),`${secret} is on the screen: ${all}`);
assert(!node("ignored-speech").open,"the list opened by itself");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_another_sessions_event_is_not_listed_and_a_new_session_starts_afresh(self) -> None:
        result = run_page_script(SETUP + r"""
await say(ignored(5,{configuration_id:70}));
assert(node("ignored-speech").hidden&&lines().length===0,"another configuration's voice was listed");
await say(ignored(5));
await say(ignored(6));
assert(lines().length===2,`lines: ${JSON.stringify(lines())}`);
resetIgnoredSpeech();
assert(node("ignored-speech").hidden&&lines().length===0&&node("ignored-speech-summary").textContent.endsWith("0개"),"a new session kept the old list");
for(let i=0;i<60;i++)await say(ignored(10+i));
assert(lines().length===50,`the list is bounded: ${lines().length}`);
assert(node("ignored-speech-summary").textContent.endsWith("60개"),`summary counts all: ${node("ignored-speech-summary").textContent}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
