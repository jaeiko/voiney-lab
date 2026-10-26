"""Lane HEAR, decision 3 (2026-10-25): the current screen shows each dropped sound, never in silence.

Through the production page script: a ``speech.dropped`` event shows "잘 안
들렸어요" beside the microphone for a few seconds and adds one small line
with the reason in plain words to the folded list under the conversation.
A voice the filter took for someone else's keeps its own line, and a drop
while the run is paused shows nothing (the pause band already says why).
"""

from __future__ import annotations

import unittest

from tests.test_screen_cleanup import run_page_script

SETUP = r"""
const configuration_id=71;
acceptedSessionConfiguration={configuration_id,mode:"cascade",language:"ko",server_generation:3,protocol_id:"protocol-x",revision_id:"pdf-1-analysis-1"};sessionActive=true;socket=new WS();
async function say(message){await onMessage({data:JSON.stringify(message)},sessionGeneration,socket);}
const drop=(reason,extra={})=>({type:"speech.dropped",configuration_id,turn_id:4,generation:3,reason,duration_ms:900,level_db:-31.2,paused:false,during_playback:false,notice:"잘 안 들렸어요",...extra});
"""


class DroppedSpeechPageTests(unittest.TestCase):
    def test_a_drop_shows_the_notice_and_a_line_with_its_reason(self) -> None:
        result = run_page_script(SETUP + r"""
await say(drop("empty_transcript"));
assert(!node("run-heard").hidden,"the notice beside the microphone is hidden");
assert(node("run-heard").textContent==="잘 안 들렸어요",`notice: ${node("run-heard").textContent}`);
const list=node("ignored-speech-list");
assert(!node("ignored-speech").hidden,"the folded list stayed hidden");
assert(list.children[0].textContent.startsWith("잘 안 들렸어요 · 받아 적은 말이 없어요"),`line: ${list.children[0].textContent}`);
await say(drop("self_echo"));
assert(list.children[0].textContent.includes("안내 소리가 다시 들어왔어요"),`echo line: ${list.children[0].textContent}`);
assert(node("ignored-speech-summary").textContent.startsWith("잘 안 들린 말 2개"),`summary: ${node("ignored-speech-summary").textContent}`);
for(const code of ["empty_transcript","self_echo","-31.2"])assert(!visibleText(node("ignored-speech")).includes(code),`${code} on screen`);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_nothing_is_shown_for_a_paused_drop_or_another_session(self) -> None:
        result = run_page_script(SETUP + r"""
node("run-heard").hidden=true;
await say(drop("empty_transcript",{paused:true,notice:null}));
await say(drop("empty_transcript",{configuration_id:70}));
await say(drop("other_speaker_level"));
assert(node("run-heard").hidden,"a paused, foreign or filtered drop showed the notice");
assert(node("ignored-speech-list").children.length===0,"a line was added");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
