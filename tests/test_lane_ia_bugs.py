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

② On the experiment tab, with an analysed protocol picked, the console said
"준비됨 · 세션 시작을 한 번 눌러주세요." but no start button showed. Cause: the
실험 전 view hides the console's [새 실험 시작]/[종료] (the sheet is the start),
and the sentence still named it. Fix: the tab always shows one way to start --
[이 프로토콜로 시작] when a runnable protocol is picked (the sheet from the two
questions; straight to the start when the check before the start was already
read), [실험 이어하기] for a saved experiment, [새 실험 시작] otherwise -- and
the state line names the button that shows. Shares tests/test_lane_ia_tabs.py.
"""

from __future__ import annotations

import unittest

from tests.test_lane_ia_tabs import SETUP, run
from tests.test_screen_cleanup import INDEX

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


class ExperimentStartTests(unittest.TestCase):
    def test_an_open_sheet_keeps_its_step_when_the_start_data_arrives_late(self) -> None:
        # CI on 32b2585 (bugs-ia.spec.ts, first test of the run): the sheet was opened and moved on before the
        # page's own load finished, and the load put it back on its first step.
        result = run(r"""
let release=null;
serve([["/api/voice/voices",()=>new Promise(resolve=>{release=()=>resolve({ok:true,json:async()=>VOICES})})],...base()]);
await loadProtocolCatalog();await loadWorkspaceSession();
const loading=openStartWizard();await settle(2);
openWizardSheet("voice");wizardGo("protocol");
assert(wizard.open===true&&wizard.step==="protocol",`before the load: ${wizard.open} ${wizard.step}`);
release();await loading;await settle(4);
assert(wizard.step==="protocol",`the late load moved the open sheet back to: ${wizard.step}`);
closeWizardSheet();
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_tab_names_the_one_start_that_shows_and_the_state_line_matches(self) -> None:
        result = run(r"""
await boot(base());
go("#/experiment");
const startTitle=()=>node("experiment-start-title").textContent,startSub=()=>node("experiment-start-sub").textContent;
assert(startTitle()==="새 실험 시작",`no protocol: ${startTitle()}`);
assert(node("status").textContent==="준비됨 · '새 실험 시작' 단추를 눌러 주세요.",`state line: ${node("status").textContent}`);
assert(!node("status").textContent.includes("세션 시작을 한 번"),"the state line still names the hidden button");
pickProtocolFromList("p-1");await settle(8);
assert(startTitle()==="이 프로토콜로 시작"&&startSub().includes("Gamma"),`picked: ${startTitle()} / ${startSub()}`);
assert(node("experiment-start-open").dataset.start==="protocol",node("experiment-start-open").dataset.start);
assert(node("status").textContent==="준비됨 · '이 프로토콜로 시작' 단추를 눌러 주세요.",`state line: ${node("status").textContent}`);
node("experiment-start-open").dispatch("click");await settle(8);
assert(wizard.open===true&&["check","safety"].includes(wizard.step)&&node("protocol-id").value==="p-1",`the press did not open the sheet at the questions: ${wizard.open} ${wizard.step}`);
closeWizardSheet();
// A protocol that cannot run is not offered as a start.
pickProtocolFromList("p-2");await settle(8);
assert(startTitle()==="새 실험 시작",`unrunnable pick: ${startTitle()}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_check_already_read_starts_at_once_and_a_saved_experiment_continues(self) -> None:
        result = run(r"""
let started=0;startReviewedProtocol=async()=>{started+=1;};
await boot([["/start-check",{enabled:true,questions:[],skipped_reason:"no_numeric_values"}],...base()]);
go("#/experiment");
pickProtocolFromList("p-1");await settle(8);
openStartForProtocol("p-1");await settle(8);
assert(wizard.step==="safety",`the sheet is not at the safety check: ${wizard.step}`);
primary().dispatch("click");await settle(4);
// Lane EASY decision 2 (2026-10-24): "오늘 원문과 다르게 할 값" now follows the safety check.
assert(wizard.step==="changes"&&wizard.checkedProtocolId==="p-1",`the read safety check was not remembered: ${wizard.step} ${wizard.checkedProtocolId}`);
closeWizardSheet();
assert(node("experiment-start-sub").textContent.includes("바로 시작"),node("experiment-start-sub").textContent);
node("experiment-start-open").dispatch("click");await settle(8);
assert(started===1&&wizard.open===false,"a read check did not start at once");
// Another protocol picked: the check must be read again.
pickProtocolFromList("p-2");await settle(8);
assert(wizard.checkedProtocolId===null,"the read check survived a change of protocol");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_saved_experiment_offers_to_continue(self) -> None:
        result = run(r"""
await boot(withExperiments([RESUMABLE]));
go("#/experiment");
pickProtocolFromList("p-1");chooseExperimentSession("s-9",{force:true});await settle(8);
assert(experimentIsResumable(),"the saved experiment is not resumable in the fake");
assert(node("experiment-start-title").textContent==="실험 이어하기"&&node("experiment-start-sub").textContent.includes("3단계"),`${node("experiment-start-title").textContent} / ${node("experiment-start-sub").textContent}`);
assert(node("status").textContent==="준비됨 · '실험 이어하기' 단추를 눌러 주세요.",node("status").textContent);
node("experiment-start-open").dispatch("click");await settle(8);
assert(wizard.open===true&&wizard.step==="start"&&primary().textContent==="실험 이어하기",`${wizard.open} ${wizard.step} ${primary().textContent}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_message_another_handler_puts_on_the_state_line_is_kept(self) -> None:
        # CI on fcfe6ff (plain-words.spec.ts, lane U): the refused-pause sentence was overwritten by the 준비됨 line on the next redraw.
        result = run(r"""
await boot(base());
go("#/experiment");
assert(node("status").textContent.startsWith("준비됨 · '"),node("status").textContent);
showWorkflowControlRefused({action:"pause",reason:"experiment_ended",status:"stopped"});
assert(node("status").textContent.includes("실험이 이미 끝나"),`refused: ${node("status").textContent}`);
renderExperimentContext();renderRoute();
assert(node("status").textContent.includes("실험이 이미 끝나"),`after a redraw: ${node("status").textContent}`);
renderState("IDLE");
assert(node("status").textContent.startsWith("준비됨 · '")&&node("status").textContent.endsWith("' 단추를 눌러 주세요."),`after the state changed: ${node("status").textContent}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_no_other_words_point_at_the_hidden_console_button(self) -> None:
        html = INDEX.read_text(encoding="utf-8")
        self.assertNotIn("세션 시작을 한 번 눌러주세요", html.split("<script>", 1)[0])
        self.assertIn("홈이나 실험 탭의 ‘실험 이어하기’를 누르면", html)


if __name__ == "__main__":
    unittest.main()
