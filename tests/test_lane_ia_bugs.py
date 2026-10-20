"""Lane IA, two bugs a person found on the dev server (10/20, dev 096f506 = stage ①).

① The agent-voice previews overlap: a second [들어 보기] while one plays does
not stop the first. Cause (confirmed by the reproduction below): the page
awaited only the start of audio.play(), cleared "듣는 중…" in finally so
another button could be pressed at once, made a new Audio without stopping
the one before, and never revoked the object URL. Fix: one preview player --
a new press stops the one before; "듣는 중…" until the sound ends (or fails,
or is stopped); the same button again stops; picking a voice, moving a step,
closing the sheet or starting the experiment stops; a spent object URL is
revoked; no preview while the experiment runs (its guidance speaks).

"""

from __future__ import annotations

import unittest

from tests.test_lane_ia_tabs import run

AUDIO = r"""
class FakeAudio{constructor(src){this.src=src;this.paused=true;this.played=0;this.pauses=0;this.onended=null;this.onerror=null;FakeAudio.made.push(this);}
 play(){this.paused=false;this.played+=1;return Promise.resolve();}
 pause(){this.paused=true;this.pauses+=1;}}
FakeAudio.made=[];globalThis.Audio=FakeAudio;
const revoked=[];let urls=0;globalThis.URL={createObjectURL:()=>`blob:preview-${++urls}`,revokeObjectURL:url=>{revoked.push(url);}};
const preview=()=>({ok:true,headers:{get:()=>"audio/wav"},blob:async()=>({size:3}),json:async()=>({})});
const previewAnswers=()=>[["/api/voice/preview",()=>preview()],...base()];
const asides=host=>walk(host).filter(el=>hasClass(el,"wizard-option-aside"));
const playing=()=>FakeAudio.made.filter(audio=>!audio.paused);
"""


def run_audio(body: str):
    return run(AUDIO + body)


class VoicePreviewTests(unittest.TestCase):
    def test_a_second_preview_stops_the_first_and_the_label_waits_for_the_end(self) -> None:
        result = run_audio(r"""
await boot(previewAnswers());
openWizardSheet("agent");
const buttons=asides(body());
assert(buttons.length===4,`asides: ${buttons.length}`);
buttons[0].dispatch("click");await settle(8);
assert(FakeAudio.made.length===1&&FakeAudio.made[0].played===1,"the first voice did not play");
assert(wizard.previewing==="ko-KR-Chirp3-HD-Charon",`previewing after play started: ${wizard.previewing} (cleared too early)`);
assert(asides(body())[0].textContent==="듣는 중…",`label while playing: ${asides(body())[0].textContent}`);
asides(body())[1].dispatch("click");await settle(8);
assert(FakeAudio.made.length===2,"the second voice did not play");
assert(FakeAudio.made[0].paused===true&&FakeAudio.made[0].pauses===1,"the first voice kept playing under the second");
assert(revoked.includes("blob:preview-1"),`the first object URL was not revoked: ${revoked}`);
assert(playing().length===1&&playing()[0]===FakeAudio.made[1],"not exactly one preview plays");
assert(wizard.previewing==="ko-KR-Chirp3-HD-Kore",`previewing: ${wizard.previewing}`);
const labels2=asides(body()).map(el=>el.textContent);
assert(labels2[0]==="들어 보기"&&labels2[1]==="듣는 중…",`labels: ${labels2}`);
FakeAudio.made[1].onended?.();await settle();
assert(wizard.previewing===null,"the end of the sound did not clear 듣는 중…");
assert(revoked.includes("blob:preview-2"),"the ended sound's URL was not revoked");
assert(asides(body()).every(el=>el.textContent==="들어 보기"),"a label stayed 듣는 중… after the end");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_same_button_again_stops_and_so_do_a_pick_a_step_change_the_close_and_the_run(self) -> None:
        result = run_audio(r"""
await boot(previewAnswers());
openWizardSheet("agent");
asides(body())[0].dispatch("click");await settle(8);
asides(body())[0].dispatch("click");await settle(4);
assert(FakeAudio.made[0].paused===true&&wizard.previewing===null,"the same button again did not stop the sound");
asides(body())[1].dispatch("click");await settle(8);
await wizardPickVoice("ko-KR-Chirp3-HD-Kore");await settle(4);
assert(FakeAudio.made[1].paused===true&&wizard.previewing===null,"picking a voice left the preview playing");
asides(body())[2].dispatch("click");await settle(8);
wizardGo("protocol");
assert(FakeAudio.made[2].paused===true,"moving a step left the preview playing");
wizardGo("agent");asides(body())[3].dispatch("click");await settle(8);
closeWizardSheet();
assert(FakeAudio.made[3].paused===true,"closing the sheet left the preview playing");
go("#/settings");
const settingsAsides=walk(node("settings-agent-voice-list")).filter(el=>hasClass(el,"wizard-option-aside"));
settingsAsides[0].dispatch("click");await settle(8);
assert(FakeAudio.made.length===5&&!FakeAudio.made[4].paused,"the settings tab's preview did not play");
assert(settingsAsides.length===4&&walk(node("settings-agent-voice-list")).filter(el=>hasClass(el,"wizard-option-aside"))[0].textContent==="듣는 중…","the settings label does not say 듣는 중…");
startRun();
assert(FakeAudio.made[4].paused===true&&wizard.previewing===null,"the run's start left the preview playing");
// While the experiment runs its guidance speaks: no preview.
walk(node("settings-agent-voice-list")).filter(el=>hasClass(el,"wizard-option-aside"))[1].dispatch("click");await settle(8);
assert(FakeAudio.made.length===5,"a preview played during the run");
assert(walk(node("settings-agent-voice-list")).filter(el=>hasClass(el,"wizard-option-aside")).every(el=>el.disabled===true),"preview buttons stay pressable during the run");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_press_before_the_first_sound_arrives_drops_it(self) -> None:
        result = run_audio(r"""
let release=null;
await boot([["/api/voice/preview",()=>new Promise(resolve=>{release=()=>resolve(preview());})],...base()]);
openWizardSheet("agent");
asides(body())[0].dispatch("click");await settle(2);
assert(wizard.previewing==="ko-KR-Chirp3-HD-Charon"&&FakeAudio.made.length===0,"the first press did not wait for its sound");
const firstRelease=release;
asides(body())[1].dispatch("click");await settle(2);
assert(wizard.previewing==="ko-KR-Chirp3-HD-Kore","the second press did not take over");
firstRelease();await settle(6);
assert(FakeAudio.made.length===0,"the first sound played after the second press took over");
release();await settle(6);
assert(FakeAudio.made.length===1&&!FakeAudio.made[0].paused,"the second sound did not play");
""")
        self.assertEqual(result.returncode, 0, result.stderr)



if __name__ == "__main__":
    unittest.main()
