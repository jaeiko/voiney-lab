"""Lane HEAR, decisions 1 and 2 (2026-10-25): the current screen measures the voice in the settings only.

Through the production page script: the audio-ready no longer asks for a
measurement (and carries the microphone's short id); "지금 목소리 맞추기"
during a run says it is done after the run, and outside one records the
sentence and posts it to the server's measuring route; the start wizard no
longer offers to measure at the start; "옆 사람 말 거르기" is the person's
switch, saved as a setting; and the server's word on the run's filter shows
in the switch's line.
"""

from __future__ import annotations

import unittest

from tests.test_screen_cleanup import run_page_script

SETUP = r"""
const configuration_id=71;
async function say(message){await onMessage({data:JSON.stringify(message)},sessionGeneration,socket);}
"""


class VoiceFilterPageTests(unittest.TestCase):
    def test_the_audio_ready_asks_for_no_measurement_and_names_the_microphone(self) -> None:
        result = run_page_script(SETUP + r"""
acceptedSessionConfiguration={configuration_id,mode:"cascade",language:"ko",server_generation:3,protocol_id:"p",revision_id:"r"};sessionActive=true;socket=new WS();
micWorkletNode={};playContext={state:"running",sampleRate:48000};
micStream={getAudioTracks:()=>[{label:"USB Mic",getSettings:()=>({deviceId:"abc123"})}]};
assert(sendAudioReady(),"audio ready was not sent");
const sent=socket.sent.map(item=>JSON.parse(item));
assert(sent.length===1&&sent[0].type==="client.audio_ready",`sent: ${JSON.stringify(sent)}`);
assert(/^mic-[0-9a-f]{8}$/.test(sent[0].mic_id),`mic id: ${sent[0].mic_id}`);
assert(micIdFor({label:"USB Mic",getSettings:()=>({deviceId:"abc123"})})===sent[0].mic_id,"the id is not stable");
assert(micIdFor({label:"Other",getSettings:()=>({deviceId:"abc123"})})!==sent[0].mic_id,"another microphone has the same id");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_in_a_run_the_button_says_after_the_run_and_sends_nothing(self) -> None:
        result = run_page_script(SETUP + r"""
acceptedSessionConfiguration={configuration_id,mode:"cascade",language:"ko",server_generation:3,protocol_id:"p",revision_id:"r"};sessionActive=true;socket=new WS();
let recorded=0;recordCalibrationPcm=async()=>{recorded+=1;return {pcm:new Int16Array(1),micId:null};};
assert(!requestVoiceCalibration("start"),"a measurement started in a run");
assert(socket.sent.length===0&&recorded===0,"something was sent or recorded in a run");
assert(node("voice-calibration-setting-status").textContent.includes("실험이 끝난 뒤"),`line: ${node("voice-calibration-setting-status").textContent}`);
await say({type:"speaker.calibration",configuration_id,generation:3,state:"refused",reason:"settings_only",words:"실험 중에는 목소리를 맞추지 않아요. 실험이 끝난 뒤 설정에서 맞춰 주세요."});
assert(node("voice-calibration-setting-status").textContent.includes("설정에서 맞춰 주세요"),"the server's answer was not shown");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_outside_a_run_the_sentence_is_recorded_and_posted(self) -> None:
        result = run_page_script(SETUP + r"""
sessionActive=false;
recordCalibrationPcm=async seconds=>({pcm:new Int16Array(16000*seconds).fill(1000),micId:"mic-0011aabb"});
globalThis.btoa=value=>Buffer.from(value,"binary").toString("base64");
const calls=[];
globalThis.fetch=async(url,options={})=>{calls.push({url:String(url),options});
 if(String(url).endsWith("/api/voice/calibration"))return json({state:"measured",level_db:-23.4,mic_id:"mic-0011aabb",speaker_filter:"off"});
 return json({settings:{speaker_filter:"off",voice_calibration:"measured"}});};
assert(await measureVoiceInSettings(),"the measurement did not finish");
const post=calls.find(call=>call.url.endsWith("/api/voice/calibration"));
assert(post&&post.options.method==="POST","nothing was posted");
const body=JSON.parse(post.options.body);
assert(body.mic_id==="mic-0011aabb"&&typeof body.pcm_base64==="string"&&body.pcm_base64.length>1000,`body: ${JSON.stringify(Object.keys(body))}`);
assert(node("voice-calibration-setting-status").textContent.includes("-23.4 dBFS"),`line: ${node("voice-calibration-setting-status").textContent}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_too_little_voice_asks_again(self) -> None:
        result = run_page_script(SETUP + r"""
sessionActive=false;
recordCalibrationPcm=async()=>({pcm:new Int16Array(160),micId:null});
globalThis.btoa=value=>Buffer.from(value,"binary").toString("base64");
globalThis.fetch=async()=>json({detail:{code:"too_little_voice"}},false);
assert(!(await measureVoiceInSettings()),"a refused measurement counted");
assert(node("voice-calibration-setting-status").textContent.includes("다시 맞춰 주세요"),`line: ${node("voice-calibration-setting-status").textContent}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_the_filter_switch_is_saved_and_the_runs_filter_is_shown(self) -> None:
        result = run_page_script(SETUP + r"""
const puts=[];globalThis.fetch=async(url,options={})=>{puts.push(JSON.parse(options.body||"{}"));return json({settings:{speaker_filter:"on"}});};
await saveExperimenterSetting("speaker_filter","on");
assert(puts.length===1&&puts[0].speaker_filter==="on",`put: ${JSON.stringify(puts)}`);
assert(node("speaker-filter").value==="on","the switch did not show the saved value");
acceptedSessionConfiguration={configuration_id,mode:"cascade",language:"ko",server_generation:3,protocol_id:"p",revision_id:"r"};sessionActive=true;
await say({type:"speaker.filter",configuration_id,state:"inactive",reason:"stale_device",margin_db:null,measured_at:null});
assert(node("speaker-filter-status").textContent.includes("마이크가 바뀌어 모든 말을 들어요"),`line: ${node("speaker-filter-status").textContent}`);
await say({type:"speaker.filter",configuration_id,state:"active",reason:"on",margin_db:12,measured_at:"2026-10-25T10:00:00+00:00"});
assert(node("speaker-filter-status").textContent.includes("켜짐"),`line: ${node("speaker-filter-status").textContent}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_the_wizard_no_longer_offers_to_measure_at_the_start(self) -> None:
        result = run_page_script(SETUP + r"""
const host=document.createElement("div"),primary=document.createElement("button");
renderWizardVoice(host,primary);
assert(!visibleText(host).includes("목소리를 맞출게요"),`wizard: ${visibleText(host)}`);
assert(node("wizard-calibrate").tagName==="div","the start-time checkbox is still drawn");
assert(node("wizard-lead").textContent.includes("설정에서 목소리를 맞춘 뒤"),`lead: ${node("wizard-lead").textContent}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
