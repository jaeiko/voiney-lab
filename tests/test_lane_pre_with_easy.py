"""Lane PRE decision 1 together with lane EASY's start step (merge of 2026-10-25).

Lane EASY put "오늘 원문과 다르게 할 값" between the safety check and the start,
and the start names the choice made there (session.start
``run_change_set_id``). Lane PRE refreshes the protocol list by itself and
asks the server once more before a start refuses. Merged, both must hold:

* from a stale list, the sheet's own ask, the steps and the start still send
  the choice -- also when the start asks the server again first;
* a refresh that brings a changed list while the experimenter types a value
  in the sheet does not rebuild the box under their fingers (a tablet's
  keyboard would close); the list itself is still taken.

Every test loads the whole page script into Node with the fake DOM
(tests/test_screen_cleanup.run_page_script).
"""

from __future__ import annotations

import unittest

from tests.test_lane_easy_run_changes_screen import CHANGES
from tests.test_lane_pre_catalog_refresh import SETUP as REFRESH_SETUP
from tests.test_screen_cleanup import run_page_script

SETUP = REFRESH_SETUP + CHANGES + r"""
const startStubs=()=>{const sent=[];sendControl=(type,body)=>{if(type==="session.start")sent.push(body);};init=async()=>{};globalThis.socket={readyState:1};return sent;};
const sheetSteps=[changesRoute,["/start-check/safety",{ready:true,reason:null,safety_acknowledged:true}],["/start-check",{enabled:true,questions:[],skipped_reason:"no_numeric_values",target:{kind:"analysis"}}]];
"""


def run(body: str):
    return run_page_script(SETUP + body)


class StartFromAStaleListTests(unittest.TestCase):
    def test_the_sheet_asks_then_the_steps_and_the_start_send_the_choice(self):
        result = run(r"""
const sent=startStubs();
// The page loaded while Gamma was still being analysed; the server has passed it since.
CATALOG.protocols[0]=ENTRY("p-1","Gamma",false,{lifecycle_state:"analyzing",analysis_run:RUN});
serve([...sheetSteps,...base()]);await loadProtocolCatalog();await openStartWizard();await settle();
assert(protocolCatalog.get("p-1").available_for_execution===false,"the page list is not stale");
CATALOG.protocols[0]=ENTRY("p-1","Gamma");
openWizardSheet("protocol");await settle(12);
const gamma=wizardPicks().find(el=>el.dataset.protocolId==="p-1");
assert(gamma&&gamma.disabled===false,"the sheet's own ask did not offer the passed protocol");
gamma.dispatch("click");await settle(8);
primary().dispatch("click");await settle(8);
assert(wizard.step==="safety",`step: ${wizard.step} · ${wizard.note}`);
primary().dispatch("click");await settle(8);
assert(wizard.step==="changes",`step: ${wizard.step}`);
const box=changeInput("s2#1");box.value="20분";box.dispatch("input");await settle();
primary().dispatch("click");await settle(8);
assert(wizard.step==="start"&&wizard.chosenChanges?.changeSetId==="run-changes-1",JSON.stringify(wizard.chosenChanges));
// The review on screen went stale meanwhile: the start asks again and still names the choice.
lastProtocolReview={...lastProtocolReview,available_for_execution:false};
const reviewsBefore=calls.filter(call=>call.url.includes("/api/protocols/p-1/review")).length;
primary().dispatch("click");await settle(12);
assert(wizard.note==="",`the start was refused: ${wizard.note}`);
assert(calls.filter(call=>call.url.includes("/api/protocols/p-1/review")).length===reviewsBefore+1,"the start did not read the review again");
assert(sent.length===1&&sent[0].protocol_id==="p-1"&&sent[0].run_change_set_id==="run-changes-1",JSON.stringify(sent));
""")
        self.assertEqual(result.returncode, 0, result.stderr)


class TypingInTheSheetTests(unittest.TestCase):
    def test_a_changed_list_does_not_rebuild_the_box_being_typed_in(self):
        result = run(r"""
await toChanges();
const box=changeInput("s2#1");box.value="2";box.dispatch("input");await settle();
// The box has the focus inside the sheet.
globalThis.document.activeElement=box;node("wizard-sheet").contains=el=>walk(body()).includes(el);
// A labmate's upload changes the lab's list; the next refresh brings it.
CATALOG.protocols.push(ENTRY("p-9","Iota",false,{lifecycle_state:"analyzing",analysis_run:RUN}));
await fireRefresh();
assert(protocolCatalog.has("p-9"),"the refresh did not take the changed list");
assert(changeInput("s2#1")===box,"the box being typed in was rebuilt");
assert(wizard.step==="changes"&&wizard.changeInputs["s2#1"]==="2","the typed value or the step was lost");
// Once the box is left, the sheet is drawn from the list again.
globalThis.document.activeElement=null;renderWizard();
assert(changeInput("s2#1")&&changeInput("s2#1").value==="2","the value did not come back after the box was left");
""")
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
