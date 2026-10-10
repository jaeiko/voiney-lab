"""Lane SP1, decision 1 (2026-10-10): the calibration sentence on the screen, through the page script.

The server asks for the sentence as a turn of its own and tells the page
what it measured; the page shows the sentence while it is being read, the
result in the setting's line afterwards, asks the server to measure again
or to skip, and takes down the provisional card of a voice the server
ignored. Nothing about the voice is kept by the page.
"""

from __future__ import annotations

import unittest

from tests.test_screen_cleanup import run_page_script

SETUP = r"""
const configuration_id=71;
acceptedSessionConfiguration={configuration_id,mode:"cascade",language:"ko",server_generation:3,protocol_id:"protocol-x",revision_id:"pdf-1-analysis-1"};sessionActive=true;socket=new WS();
const SENTENCE="목소리를 맞출게요. 지금 이 문장을 평소처럼 읽어 주세요.";
async function say(message){await onMessage({data:JSON.stringify(message)},sessionGeneration,socket);}
const lastSent=()=>JSON.parse(socket.sent.at(-1));
"""


class CalibrationPageTests(unittest.TestCase):

    def test_the_sentence_is_shown_while_read_and_the_result_afterwards(self) -> None:
        result = run_page_script(SETUP + r"""
await say({type:"speaker.calibration",configuration_id,generation:3,state:"prompting",sentence:SENTENCE,sensitivity:"normal"});
assert(!node("voice-calibration").hidden,"the box is hidden while the prompt is said");
assert(node("voice-calibration-sentence").textContent===SENTENCE,`sentence: ${node("voice-calibration-sentence").textContent}`);
assert(node("voice-calibration-status").textContent.includes("안내 중"),`status: ${node("voice-calibration-status").textContent}`);
await say({type:"speaker.calibration",configuration_id,generation:3,state:"prompted",turn_id:2000000301,sentence:SENTENCE,sensitivity:"normal"});
assert(node("voice-calibration-status").textContent.includes("평소 목소리로 읽어 주세요"),`prompted status: ${node("voice-calibration-status").textContent}`);
await say({type:"speaker.calibration",configuration_id,turn_id:4,generation:3,state:"measured",sentence:SENTENCE,sensitivity:"normal",level_db:-24.3,margin_db:12,threshold_db:-36.3});
assert(node("voice-calibration").hidden,"the box stays after the measurement");
const line=node("voice-calibration-setting-status").textContent;
assert(line.includes("-24.3 dBFS")&&line.includes("여유 12 dB")&&line.includes("다른 사람 말로 보고 듣지 않아요"),`setting line: ${line}`);
// Another session's event is not this session's.
await say({type:"speaker.calibration",configuration_id:70,generation:3,state:"prompted",sentence:"다른 문장",sensitivity:"normal"});
assert(node("voice-calibration").hidden&&node("voice-calibration-sentence").textContent===SENTENCE,"another configuration's prompt was shown");
await say({type:"speaker.calibration",configuration_id,generation:3,state:"skipped",sentence:SENTENCE,sensitivity:"normal"});
assert(node("voice-calibration-setting-status").textContent.includes("건너뜀"),`skipped line: ${node("voice-calibration-setting-status").textContent}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_the_buttons_ask_the_server_and_only_in_a_session(self) -> None:
        result = run_page_script(SETUP + r"""
assert(requestVoiceCalibration("start"),"a start in a running session was refused");
let sent=lastSent();
assert(sent.type==="client.voice_calibration"&&sent.action==="start"&&sent.configuration_id===71&&sent.generation===3,`start control: ${JSON.stringify(sent)}`);
assert(node("voice-calibration-setting-status").textContent.includes("목소리를 맞출게요"),`start line: ${node("voice-calibration-setting-status").textContent}`);
assert(requestVoiceCalibration("skip"),"a skip in a running session was refused");
sent=lastSent();
assert(sent.type==="client.voice_calibration"&&sent.action==="skip"&&sent.configuration_id===71,`skip control: ${JSON.stringify(sent)}`);
const before=socket.sent.length;
sessionActive=false;
assert(!requestVoiceCalibration("start"),"a start without a session was sent");
assert(socket.sent.length===before,"a control went out without a session");
assert(node("voice-calibration-setting-status").textContent.includes("세션을 시작한 뒤"),`no-session line: ${node("voice-calibration-setting-status").textContent}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_the_servers_words_make_a_labelled_card_and_an_ignored_voice_none(self) -> None:
        result = run_page_script(SETUP + r"""
await say({type:"server.words",configuration_id,turn_id:2000000301,generation:3,words_kind:"voice_calibration",text:"목소리를 맞출게요. 화면의 문장을 평소 목소리로 읽어 주세요."});
const card=turnNode(2000000301,sessionGeneration);
assert(card.querySelector(".transcript").textContent==="목소리 맞추기",`card transcript: ${card.querySelector(".transcript").textContent}`);
assert(card.querySelector(".reply").textContent.includes("화면의 문장을"),`card reply: ${card.querySelector(".reply").textContent}`);
assert(card.dataset.greetingOwner==="words-2000000301","the server's words were not marked as its own turn");
await say({type:"speech.start",turn_id:5,generation:3,state:"USER_SPEAKING",voiced_frames:3,total_frames:12,prefix_frames_retained:12,duration_ms:240,reason:null,forced:false});
const key=turnBrowserKey(5,sessionGeneration);
assert(provisionalTurns.has(key),"the voice heard was not noted as a provisional turn");
turnNode(5,sessionGeneration);  // the "듣는 중…" card the page draws for a turn in progress
assert(turns.has(key),"no card for the voice heard");
await say({type:"speech.ignored",turn_id:5,generation:3,state:"COOLDOWN",voiced_frames:30,total_frames:45,prefix_frames_retained:12,duration_ms:900,reason:"other_speaker_level",forced:false,level_db:-40.2,reference_db:-24.3,margin_db:12,sensitivity:"normal",during_playback:false,ignored_count:1});
assert(!turns.has(key)&&!provisionalTurns.has(key),"the provisional card of an ignored voice stayed");
assert(!node("log").textContent.includes("-40.2"),"the ignored voice's level reached the conversation log");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
