"""Lane FX3, decision 1: a late experiment list or timeline does not undo a newer one.

The page loads the experiment list at startup (``loadExperimentSessions()``
with no experiment chosen) and again whenever the server names the running
experiment (``session.ready``, ``experiment.session.state``). When the
startup answer came back last, it chose "no experiment" and cleared the
timeline the newer answer had drawn -- the screen-side half of the
Playwright flake at ``tests/e2e/researcher.spec.ts`` (the test's half was
rendering before the startup answer). Each test drives the production page
script under Node with answers released in a chosen order.
"""

import unittest

from tests.test_screen_cleanup import run_page_script

# Answers held back until the test releases them, by path.
PENDING = r"""
const held=new Map();
async function settle(){for(let i=0;i<20;i++)await new Promise(r=>setTimeout(r,0));}
globalThis.fetch=(url)=>new Promise(resolve=>{const path=String(url).split("?")[0];(held.get(path)||held.set(path,[]).get(path)).push(resolve);});
// The oldest request on a path answers, or with newest=true the latest one.
async function release(path,payload,newest=false){const queue=held.get(path)||[];assert(queue.length,`no request for ${path}`);(newest?queue.pop():queue.shift())(json(payload));await settle();}
const LIST="/api/workspace/experiments";
const listed={experiments:[{session_id:"exp-2",protocol_id:"p-1",status:"in_progress",current_step_label:"3",started_at:"2026-10-16T01:00:00Z"}]};
const timeline={session:{session_id:"exp-2",version:4,status:"in_progress",protocol_id:"p-1",protocol_revision_id:"r-1",current_step_id:"step-3",current_step_label:"3"},
 timeline:[{event_type:"step_completed",step_label:"2",created_at:"2026-10-16T01:05:00Z"}],observation_count:0,evidence_count:0};
const rows=()=>node("experiment-event-timeline").children.map(child=>child.textContent).join(" | ");
"""


class LateStartupListTests(unittest.TestCase):
    def test_the_startup_list_answering_last_keeps_the_experiment_the_server_named(self):
        result = run_page_script(PENDING + r"""
const startup=loadExperimentSessions();            // the page's own startup: no experiment yet
currentExperimentSessionId="exp-2";               // experiment.session.state names the run
const named=loadExperimentSessions("exp-2");
await release(LIST,listed,true);                   // the newer list answers first
await release("/api/workspace/experiments/exp-2/timeline",timeline);
await named;
assert(currentExperimentSessionId==="exp-2","the named experiment was not selected");
const drawn=rows();
assert(!drawn.includes("진행 중인 실험 없음"),`the newer timeline was not drawn: ${drawn}`);
await release(LIST,{experiments:[]});             // the startup list answers last, empty
await startup;await settle();
assert(currentExperimentSessionId==="exp-2",`a late startup list dropped the experiment: ${currentExperimentSessionId}`);
assert(rows()===drawn,`a late startup list cleared the timeline: ${rows()}`);
assert(node("experiment-session-select").value==="exp-2","the select lost the running experiment");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_an_older_timeline_answering_last_does_not_replace_the_newer_one(self):
        result = run_page_script(PENDING + r"""
const first=loadExperimentTimeline("exp-1");
const second=loadExperimentTimeline("exp-2");
await release("/api/workspace/experiments/exp-2/timeline",timeline);
await second;
const drawn=rows();
assert(drawn.includes("2"),`the chosen experiment's timeline was not drawn: ${drawn}`);
await release("/api/workspace/experiments/exp-1/timeline",{...timeline,session:{...timeline.session,session_id:"exp-1",current_step_label:"9"},timeline:[{event_type:"observation_recorded",step_label:"9",created_at:"2026-10-16T00:00:00Z",observation:{category:"note",content:"예전 실험의 기록"}}]});
await first;await settle();
assert(!rows().includes("예전 실험의 기록"),`the older answer replaced the chosen timeline: ${rows()}`);
assert(rows()===drawn,"the chosen timeline changed");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_single_startup_load_still_resets_to_no_experiment(self):
        # Nothing newer: the startup answer is drawn as before.
        result = run_page_script(PENDING + r"""
const startup=loadExperimentSessions();
await release(LIST,{experiments:[]});
await startup;await settle();
assert(currentExperimentSessionId===null,"no experiment should be selected");
assert(rows().includes("진행 중인 실험 없음"),`the empty state was not drawn: ${rows()}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
