"""Lane SP1, decision 2 (2026-10-10): automatic gain control is off while the wearer's voice is measured.

The page asked the browser for echoCancellation, noiseSuppression and
autoGainControl all on. Automatic gain lifts a quiet (farther) voice toward
the wearer's level and so undoes decision 1's measurement. Now, in the
default "목소리를 맞추면 끔", the open microphone track has its automatic
gain turned off the moment the calibration sentence is asked for, kept off
after the measurement, and turned back on only when the measurement was
skipped; "항상 켬" and "항상 끔" do what they say. The track is not
reopened, and the server is told again what was asked and what the
browser does (client.audio_constraints). Echo cancellation and noise
suppression stay as they were.
"""

from __future__ import annotations

import unittest

from tests.test_screen_cleanup import run_page_script

SETUP = r"""
const configuration_id=71;
acceptedSessionConfiguration={configuration_id,mode:"cascade",language:"ko",server_generation:3,protocol_id:"protocol-x",revision_id:"pdf-1-analysis-1"};sessionActive=true;socket=new WS();
const applied=[];
const settings={echoCancellation:true,noiseSuppression:true,autoGainControl:true};
const track={label:"USB mic",readyState:"live",getSettings:()=>({...settings}),applyConstraints:async c=>{applied.push({...c});Object.assign(settings,c);},stop(){},addEventListener(){}};
micStream={getAudioTracks:()=>[track],getTracks:()=>[track]};
micAudioConstraints={channelCount:1,echoCancellation:true,noiseSuppression:true,autoGainControl:true};
const SENTENCE="목소리를 맞출게요. 지금 이 문장을 평소처럼 읽어 주세요.";
async function say(message){await onMessage({data:JSON.stringify(message)},sessionGeneration,socket);await new Promise(r=>setTimeout(r,0));}
const constraintControls=()=>socket.sent.map(v=>JSON.parse(v)).filter(m=>m.type==="client.audio_constraints");
"""


class AgcTests(unittest.TestCase):

    def test_the_default_turns_gain_off_when_the_sentence_is_asked_and_keeps_it_off(self) -> None:
        result = run_page_script(SETUP + r"""
assert(agcMode()==="calibrated",`default mode: ${agcMode()}`);
await say({type:"speaker.calibration",configuration_id,generation:3,state:"prompting",sentence:SENTENCE,sensitivity:"normal"});
assert(applied.length===1&&applied[0].autoGainControl===false,`constraints applied at the prompt: ${JSON.stringify(applied)}`);
let told=constraintControls();
assert(told.length===1&&told[0].requested.autoGainControl===false&&told[0].actual.autoGainControl===false&&told[0].requested.echoCancellation===true&&told[0].requested.noiseSuppression===true,`server told: ${JSON.stringify(told)}`);
assert(node("agc-status").textContent.includes("꺼짐"),`status: ${node("agc-status").textContent}`);
await say({type:"speaker.calibration",configuration_id,generation:3,state:"prompted",turn_id:2000000301,sentence:SENTENCE,sensitivity:"normal"});
await say({type:"speaker.calibration",configuration_id,turn_id:4,generation:3,state:"measured",sentence:SENTENCE,sensitivity:"normal",level_db:-24.3,margin_db:12,threshold_db:-36.3});
assert(applied.length===1,`gain was touched again after the prompt: ${JSON.stringify(applied)}`);
assert(settings.autoGainControl===false,"gain came back on after the measurement");
// Measured: a later skip (of a second measurement) does not turn it back on.
await say({type:"speaker.calibration",configuration_id,generation:3,state:"skipped",sentence:SENTENCE,sensitivity:"normal"});
assert(settings.autoGainControl===false,"gain came back on after a skip although a reference stands");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_a_skip_without_a_measurement_turns_gain_back_on(self) -> None:
        result = run_page_script(SETUP + r"""
await say({type:"speaker.calibration",configuration_id,generation:3,state:"prompted",turn_id:2000000301,sentence:SENTENCE,sensitivity:"normal"});
assert(settings.autoGainControl===false,"gain not off at the prompt");
await say({type:"speaker.calibration",configuration_id,generation:3,state:"skipped",sentence:SENTENCE,sensitivity:"normal"});
assert(settings.autoGainControl===true,"gain not back on after the skip");
assert(applied.length===2&&applied[1].autoGainControl===true,`applied: ${JSON.stringify(applied)}`);
const told=constraintControls();
assert(told.length===2&&told[1].requested.autoGainControl===true,`server told: ${JSON.stringify(told)}`);
assert(node("agc-status").textContent.includes("켜짐"),`status: ${node("agc-status").textContent}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_always_on_leaves_the_track_alone_and_always_off_asks_for_it_off_at_open(self) -> None:
        result = run_page_script(SETUP + r"""
node("agc-mode").value="on";
await say({type:"speaker.calibration",configuration_id,generation:3,state:"prompted",turn_id:2000000301,sentence:SENTENCE,sensitivity:"normal"});
await say({type:"speaker.calibration",configuration_id,turn_id:4,generation:3,state:"measured",sentence:SENTENCE,sensitivity:"normal",level_db:-24.3,margin_db:12,threshold_db:-36.3});
assert(applied.length===0,`"항상 켬" touched the track: ${JSON.stringify(applied)}`);
node("agc-mode").value="off";
let requested=null;
navigator.mediaDevices.getSupportedConstraints=()=>({echoCancellation:true,noiseSuppression:true,autoGainControl:true});
navigator.mediaDevices.getUserMedia=async constraints=>{requested=constraints;return {getTracks:()=>[track],getAudioTracks:()=>[track]};};
await openMicStream();
assert(requested.audio.autoGainControl===false&&requested.audio.echoCancellation===true&&requested.audio.noiseSuppression===true,`"항상 끔" open request: ${JSON.stringify(requested)}`);
node("agc-mode").value="calibrated";
await openMicStream();
assert(requested.audio.autoGainControl===true,`default open request: ${JSON.stringify(requested)}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
