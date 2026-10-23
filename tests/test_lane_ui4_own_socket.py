"""Lane UI4 (2026-10-23): lane OWN's screen hand-over ② (PR #114, ~/reports/lane_own_report.md 5절).

The server closes every voice socket of an account it signed out, switched off or deleted --
and, on a password change, every socket but leaves this device's HTTP login -- with the code
1008 ("authentication required"). The page took that close as any lost connection: it said the
connection ended (or tried again, for a run) with an account that may be gone. On a 1008 close
it now asks who is signed in (GET /api/auth/session): signed out, it goes to the login at once,
as a 401 does (signOutOnScreen); still signed in, the close is handled as before.

Shares tests/test_lane_ia_stage3.py's harness.
"""

from __future__ import annotations

import unittest

from tests.test_lane_ia_stage3 import STAGE3
from tests.test_lane_ia_tabs import run

SOCKET = r"""
let replaced=null;Object.assign(globalThis.location,{pathname:"/",search:"",replace(url){replaced=url;},reload(){}});
let signedIn=true;
const sessionAsks=()=>calls.filter(call=>call.url.endsWith("/api/auth/session")).length;
async function openSocket(){const opening=init(sessionGeneration);const ws=socket;ws.onopen?.();await opening;return ws;}
"""


def run_socket(body: str):
    return run(STAGE3 + SOCKET + body)


class PolicyCloseTests(unittest.TestCase):

    def test_a_1008_close_with_the_account_signed_out_goes_to_the_login(self) -> None:
        result = run_socket(r"""
await boot([["/api/auth/session",()=>ACCOUNT_SESSION(signedIn)],...base(ACCOUNT_SESSION(true)).slice(1)]);
go("#/experiment");
const ws=await openSocket(),generation=sessionGeneration,before=sessionAsks();
signedIn=false;
ws.onclose({code:1008,reason:"authentication required"});await settle(6);
assert(sessionAsks()===before+1,`the account was not asked again: ${sessionAsks()-before}`);
assert(document.body.dataset.auth==="required",`auth: ${document.body.dataset.auth}`);
assert(replaced==="/","the page did not leave for the login");
assert(sessionGeneration!==generation,"the reconnecting would go on (same generation)");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_1008_close_with_the_account_still_signed_in_is_a_lost_connection(self) -> None:
        result = run_socket(r"""
await boot([["/api/auth/session",()=>ACCOUNT_SESSION(signedIn)],...base(ACCOUNT_SESSION(true)).slice(1)]);
go("#/experiment");
const ws=await openSocket(),before=sessionAsks();
ws.onclose({code:1008,reason:"authentication required"});await settle(6);
assert(sessionAsks()===before+1,"the account was not asked again");
assert(replaced===null&&document.body.dataset.auth==="ok",`signed out although still signed in: ${replaced} ${document.body.dataset.auth}`);
assert(visibleState==="ERROR",`the close was not handled as before: ${visibleState}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_another_close_does_not_ask(self) -> None:
        result = run_socket(r"""
await boot([["/api/auth/session",()=>ACCOUNT_SESSION(signedIn)],...base(ACCOUNT_SESSION(true)).slice(1)]);
go("#/experiment");
const ws=await openSocket(),before=sessionAsks();
ws.onclose({code:1006,reason:""});await settle(6);
assert(sessionAsks()===before,"an ordinary close asked for the account");
""")
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
