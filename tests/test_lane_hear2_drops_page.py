"""Lane HEAR2, decision 4 (2026-10-26): the current screen shows the server's reason for a drop.

Through the production page script: a ``speech.dropped`` event's ``notice``
and ``reason_ko`` (the server's plain words) show beside the microphone for
a few seconds, so a run of drops in a noisy room says why without a word
spoken; the folded list keeps the server's words too; a provider that
failed shows its own notice and reason.
"""

from __future__ import annotations

import unittest

from tests.test_screen_cleanup import run_page_script

SETUP = r"""
const configuration_id=71;
acceptedSessionConfiguration={configuration_id,mode:"cascade",language:"ko",server_generation:3,protocol_id:"protocol-x",revision_id:"pdf-1-analysis-1"};sessionActive=true;socket=new WS();
async function say(message){await onMessage({data:JSON.stringify(message)},sessionGeneration,socket);}
const drop=(reason,extra={})=>({type:"speech.dropped",configuration_id,turn_id:4,generation:3,reason,duration_ms:900,level_db:-31.2,paused:false,during_playback:false,notice:"잘 안 들렸어요",reason_ko:null,...extra});
"""


class DropReasonPageTests(unittest.TestCase):
    def test_the_servers_reason_shows_beside_the_microphone_and_in_the_line(self) -> None:
        result = run_page_script(SETUP + r"""
await say(drop("minimum_voiced_frames",{reason_ko:"너무 짧은 소리였어요"}));
assert(!node("run-heard").hidden,"the notice beside the microphone is hidden");
assert(node("run-heard").textContent==="잘 안 들렸어요 · 너무 짧은 소리였어요",`notice: ${node("run-heard").textContent}`);
const list=node("ignored-speech-list");
assert(list.children[0].textContent.startsWith("잘 안 들렸어요 · 너무 짧은 소리였어요"),`line: ${list.children[0].textContent}`);
await say(drop("stt_failed",{code:"quota_exceeded",notice:"받아쓰기 서비스가 답하지 않아요",reason_ko:"받아쓰기 서비스의 한도가 다 됐어요"}));
assert(node("run-heard").textContent==="받아쓰기 서비스가 답하지 않아요 · 받아쓰기 서비스의 한도가 다 됐어요",`provider notice: ${node("run-heard").textContent}`);
assert(list.children[0].textContent.startsWith("받아쓰기 서비스가 답하지 않아요 · 받아쓰기 서비스의 한도가 다 됐어요"),`provider line: ${list.children[0].textContent}`);
for(const code of ["stt_failed","quota_exceeded","minimum_voiced_frames"])assert(!visibleText(node("ignored-speech")).includes(code),`${code} on screen`);
// An older server without the words: the screen's own table.
await say(drop("self_echo"));
assert(node("run-heard").textContent==="잘 안 들렸어요 · 안내 소리가 다시 들어왔어요",`fallback: ${node("run-heard").textContent}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
