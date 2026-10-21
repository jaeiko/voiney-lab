"""Lane IA stage ③ (2026-10-21): the experiment tab, the records tab, one address function,
and the screen items lane QA marked "파일럿 전에 꼭" (~/reports/lane_qa_report.md section 0).

④ the experiment tab: every start goes through the sheet (QA-01: the 실험 후 view's console
  [새 실험 시작] sent session.start with no questions and no safety notice, and the after
  view's experiment panel showed on every tab); [종료] ends on the server's word and shows
  the end screen (QA-10: the page closed its socket right after session.stop, so the
  server's answer never arrived), and an ended experiment is never offered as "진행 중"
  (QA-95); the questions the server asks before the start show while they are heard
  (QA-12); a turn's error the server survives does not end the session (QA-13).
⑤ the records tab: the list of past experiments, then one experiment's record.
⑨ every server address is read through one function (values unchanged).
Accounts: a logout lands on the login, not on a spent invite (QA-16); a 401 sends the page
to the login and the reconnecting stops (QA-17).
Shares tests/test_lane_ia_tabs.py's SETUP (the ON wizard harness with the tab router).
"""

from __future__ import annotations

import re
import unittest

from tests.test_lane_ia_tabs import run
from tests.test_screen_cleanup import INDEX

STAGE3 = r"""
const ENDED={...RESUMABLE,session_id:"s-8",status:"stopped",current_step_label:"5",started_at:"2026-10-18T01:00:00+00:00"};
let made=0;const RealWS=globalThis.WebSocket;globalThis.WebSocket=class extends RealWS{constructor(...args){super(...args);made+=1;this.url=args[0];}};
function running(id="s-9"){socket=new WebSocket("ws://test/ws");acceptedSessionConfiguration={configuration_id:5,mode:"cascade",language:"ko",protocol_id:"p-1",revision_id:"pdf-1",experiment_session_id:id};sessionActive=true;curatedProtocolState=runState();renderScreenView();return socket;}
const sentTypes=ws=>ws.sent.map(value=>JSON.parse(value).type);
const deliver=(ws,message)=>onMessage({data:JSON.stringify(message)},sessionGeneration,ws);
const unauthorized=()=>({ok:false,status:401,headers:{get:()=>"application/json"},json:async()=>({detail:"authentication_required"})});
"""


def run3(body: str):
    return run(STAGE3 + body)


class AddressTests(unittest.TestCase):
    """⑨ one function reads every server address; the values stay the same."""

    def test_every_request_and_the_socket_go_through_one_function(self) -> None:
        html = INDEX.read_text(encoding="utf-8")
        script = html.split("<script>", 1)[1]
        self.assertEqual(re.findall(r"""(?<![\w.])fetch\(\s*["`]/""", script), [], "a request names its address itself")
        self.assertNotIn("location.host", script)
        self.assertEqual(script.count("new WebSocket("), 1)
        result = run3(r"""
await boot(base());
calls.length=0;
serverOrigin=()=>"https://lab.example";
await loadProtocolCatalog();
assert(calls.length>0&&calls.every(call=>call.url.startsWith("https://lab.example/api/")),`requests: ${calls.map(call=>call.url)}`);
assert(serverSocketUrl()==="wss://lab.example/ws",serverSocketUrl());
serverOrigin=()=>"";
assert(serverUrl("/api/protocols")==="/api/protocols","the same-origin address changed");
assert(serverSocketUrl()==="ws://test/ws",serverSocketUrl());
""")
        self.assertEqual(result.returncode, 0, result.stderr)


class StartThroughTheSheetTests(unittest.TestCase):
    """QA-01: every start goes through the sheet; the after view keeps to its tab."""

    def test_the_after_views_start_opens_the_sheet_and_sends_nothing(self) -> None:
        result = run3(r"""
await boot(base());
go("#/experiment");pickProtocolFromList("p-1");await settle(8);
const ws=running();
await deliver(ws,{type:"experiment.ended",configuration_id:5,status:"completed",report:"saved"});await settle(4);
assert(document.body.dataset.view==="after",`view: ${document.body.dataset.view}`);
// As lane QA did it: [종료] on the end screen, then the console's [새 실험 시작].
await stopSession();await settle(4);
assert(document.body.dataset.view==="after"&&node("start").disabled!==true,`the end screen or its start is gone: ${document.body.dataset.view}`);
const before=made,sent=ws.sent.length;
node("start").dispatch("click");await settle(8);
assert(made===before&&ws.sent.length===sent,`the console start opened a session: sockets ${made-before}, sent ${ws.sent.slice(sent)}`);
assert(wizard.open===true&&document.body.dataset.wizard==="on",`the sheet did not open: ${wizard.open} ${document.body.dataset.wizard}`);
closeWizardSheet();
assert(document.body.dataset.view==="after","closing the sheet left the end screen");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_after_views_panel_keeps_to_the_experiment_tab(self) -> None:
        html = INDEX.read_text(encoding="utf-8")
        self.assertNotIn('body[data-view="after"] #tab-experiment{display', html)
        self.assertIn('body[data-view="after"] #tab-experiment:not([hidden]){display:grid;', html)

if __name__ == "__main__":
    unittest.main()
