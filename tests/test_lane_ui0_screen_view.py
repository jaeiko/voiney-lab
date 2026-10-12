"""Lane UI0, decision 1 (2026-10-12): one page and three views from the server's state.

The page draws "실험 전", "실험 중" or "실험 후" by a ``data-view`` on its body.
The view is read from the server's state alone: the experiment runs while
the session is live and the server's protocol state is active (a pause
included), it is over once the server said ``experiment.ended``, and it has
not started otherwise. A reply's words never move it.
"""

from __future__ import annotations

import unittest

from tests.test_screen_cleanup import INDEX, run_page_script

SETUP = r"""
const configuration_id=81;
acceptedSessionConfiguration={configuration_id,mode:"cascade",language:"ko",server_generation:2,protocol_id:"protocol-x",revision_id:"pdf-1-analysis-1"};socket=new WS();
document.body=new Element("body");document.body.dataset.view="before";
async function say(message){await onMessage({data:JSON.stringify(message)},sessionGeneration,socket);}
let revision=0;
const state=(extra={})=>({attached:true,protocol_id:"protocol-x",revision_id:"pdf-1-analysis-1",display_name:"Western blot",development_only:false,readiness_status:"guidance_ready",active:true,current_step_label:"3",current_step_id:"step-3",total_steps:12,at_final_step:false,block_reason:null,revision:++revision,display_summary:"Wash the membrane.",primary_summary:"막을 씻습니다.",source_language:"en",spoken_summary:null,warning_texts:[],warning_presentations:[],visual_assets:[],visual_status:"unavailable",source_page_refs:[3],workflow_status:"running",...extra});
const view=()=>document.body.dataset.view;
"""


class ScreenViewTests(unittest.TestCase):

    def test_the_markup_starts_before_the_experiment(self) -> None:
        html = INDEX.read_text(encoding="utf-8")
        self.assertIn('<body data-view="before">', html)
        result = run_page_script(SETUP + r"""
assert(screenView()==="before",`a fresh page is ${screenView()}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_an_active_server_state_in_a_live_session_is_the_experiment(self) -> None:
        result = run_page_script(SETUP + r"""
sessionActive=true;
await say({type:"protocol.fixture.state",configuration_id,action:"preview",state:state({active:false,workflow_status:"preview"})});
assert(view()==="before",`a previewed step is ${view()}`);
await say({type:"protocol.fixture.state",configuration_id,action:"start",state:state()});
assert(view()==="during",`a started protocol is ${view()}`);
await say({type:"protocol.fixture.state",configuration_id,action:"pause",state:state({workflow_status:"paused"})});
assert(view()==="during",`a pause is ${view()}`);
await say({type:"protocol.fixture.state",configuration_id,action:"block",state:state({block_reason:"endpoint_observation_not_reported"})});
assert(view()==="during",`a held step is ${view()}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_without_a_live_session_an_active_state_is_not_the_experiment(self) -> None:
        result = run_page_script(SETUP + r"""
sessionActive=false;
await say({type:"protocol.fixture.state",configuration_id,action:"start",state:state()});
assert(view()==="before",`a state with no live session is ${view()}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_the_server_end_is_after_and_a_new_session_is_before(self) -> None:
        result = run_page_script(SETUP + r"""
sessionActive=true;
await say({type:"protocol.fixture.state",configuration_id,action:"start",state:state()});
assert(view()==="during",`started: ${view()}`);
await say({type:"experiment.ended",configuration_id,status:"completed",report:"saved"});
assert(view()==="after",`ended: ${view()}`);
await say({type:"session.stopped",configuration_id});
assert(view()==="after",`stopped after the end: ${view()}`);
resetSessionUI();
assert(view()==="before",`a new session: ${view()}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_a_stopped_session_leaves_the_experiment_view(self) -> None:
        result = run_page_script(SETUP + r"""
sessionActive=true;
await say({type:"protocol.fixture.state",configuration_id,action:"start",state:state()});
assert(view()==="during",`started: ${view()}`);
await say({type:"session.stopped",configuration_id});
assert(view()==="before",`a stopped session with no server end: ${view()}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_reply_words_do_not_move_the_view(self) -> None:
        result = run_page_script(SETUP + r"""
sessionActive=true;
await say({type:"speech.start",turn_id:5,generation:2});
await say({type:"transcript",turn_id:5,generation:2,text:"실험 시작해 줘"});
await say({type:"reply.complete",turn_id:5,generation:2,text:"실험을 시작합니다. 1단계입니다."});
assert(view()==="before",`reply words moved the view: ${view()}`);
await say({type:"protocol.fixture.state",configuration_id,action:"start",state:state()});
await say({type:"speech.start",turn_id:6,generation:2});
await say({type:"reply.complete",turn_id:6,generation:2,text:"실험이 끝났습니다."});
assert(view()==="during",`reply words ended the view: ${view()}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_the_experiment_view_keeps_the_voice_console_and_the_step_card(self) -> None:
        html = INDEX.read_text(encoding="utf-8")
        style = html.split("<style>", 1)[1].split("</style>", 1)[0]
        self.assertIn('body[data-view="during"] .topbar', style)
        self.assertNotIn('body[data-view="during"] .voice-console{display:none}', style)
        self.assertNotIn('body[data-view="during"] .procedure-card{display:none}', style)


if __name__ == "__main__":
    unittest.main()
