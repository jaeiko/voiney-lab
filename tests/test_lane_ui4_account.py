"""Lane UI4 (2026-10-23): the account deletion button (lane FX5's route, lane IA's hand-over).

Lane FX5 (PR #108) built POST /api/auth/delete-account: the password again, the lab's last
administrator refused (409 last_admin with message_ko), the account switched off, its data
deleted, the session cookie cleared. The screen had no button for it. The settings tab's
account section now has [계정 지우기]: it asks once more -- what it deletes and that it cannot
be undone, and the password again, as the server asks -- sends the request through
serverFetch, and on success goes to the login at once (signOutOnScreen). A refusal says why
(the server's sentence when it sent one) and keeps the account signed in. Nothing in dev mode.

Shares tests/test_lane_ia_tabs.py's harness.
"""

from __future__ import annotations

import re
import unittest

from tests.test_lane_ia_tabs import panel, run
from tests.test_screen_cleanup import INDEX

ACCOUNT = r"""
let replaced=null;Object.assign(globalThis.location,{pathname:"/",search:"",replace(url){replaced=url;},reload(){}});
const reply=(status,payload)=>{const answer={ok:status<400,status,headers:{get:()=>"application/json"},json:async()=>payload,clone:()=>answer};return answer;};
const deletes=()=>posts().filter(call=>call.url.endsWith("/api/auth/delete-account"));
const deleteStatus=()=>node("account-delete-status").textContent;
"""


def run_account(body: str):
    return run(ACCOUNT + body)


class AccountDeleteTests(unittest.TestCase):

    def test_the_account_is_deleted_after_one_more_ask_and_the_login_comes_back(self) -> None:
        result = run_account(r"""
await boot([["/api/auth/delete-account",()=>reply(200,{deleted:true,message_ko:"계정과 그 계정이 남긴 기록을 지웠어요."})],["/api/auth/session",ACCOUNT_SESSION(true)],...base(ACCOUNT_SESSION(true))]);
go("#/settings");
assert(node("account-delete-open").hidden===false,"no delete button in accounts mode");
assert(node("account-delete-form").hidden===true,"the ask shows before the button is pressed");
node("account-delete-open").dispatch("click");
assert(node("account-delete-form").hidden===false,"the ask did not open");
assert(node("account-password-form").hidden===true,"the password form opened with it");
node("account-delete-send").dispatch("click");await settle(4);
assert(deletes().length===0,"deleted without the password");
assert(deleteStatus().includes("비밀번호"),deleteStatus());
node("account-delete-password").value="correct horse";
node("account-delete-send").dispatch("click");await settle(8);
assert(deletes().length===1&&JSON.stringify(deletes()[0].body)===JSON.stringify({password:"correct horse"}),`sent: ${JSON.stringify(deletes().map(call=>call.body))}`);
assert(document.body.dataset.auth==="required"&&replaced==="/",`the login did not come back: ${document.body.dataset.auth} ${replaced}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_refusal_says_why_and_keeps_the_account(self) -> None:
        result = run_account(r"""
let answer=reply(409,{detail:"last_admin",message_ko:"이 연구실의 마지막 관리자라서 계정을 지울 수 없어요. 다른 사람을 관리자로 초대한 뒤에 다시 해 주세요."});
await boot([["/api/auth/delete-account",()=>answer],["/api/auth/session",ACCOUNT_SESSION(true)],...base(ACCOUNT_SESSION(true))]);
go("#/settings");
node("account-delete-open").dispatch("click");
node("account-delete-password").value="correct horse";
node("account-delete-send").dispatch("click");await settle(8);
assert(deleteStatus().includes("마지막 관리자라서"),`last admin: ${deleteStatus()}`);
assert(document.body.dataset.auth==="ok"&&replaced===null,"a refusal signed the account out");
answer=reply(401,{detail:"login_refused"});
node("account-delete-password").value="wrong horse";
node("account-delete-send").dispatch("click");await settle(8);
assert(deleteStatus().includes("비밀번호가 맞지 않아요"),`wrong password: ${deleteStatus()}`);
assert(node("account-delete-password").value==="","the refused password stays in the field");
assert(document.body.dataset.auth==="ok"&&replaced===null,"a wrong password signed the account out");
answer=reply(500,{detail:"account_delete_incomplete"});
node("account-delete-password").value="correct horse";
node("account-delete-send").dispatch("click");await settle(8);
assert(deleteStatus().includes("계정은 꺼졌지만"),`halfway: ${deleteStatus()}`);
node("account-delete-cancel").dispatch("click");
assert(node("account-delete-form").hidden===true,"[그만두기] did not close the ask");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_dev_mode_has_no_delete_button(self) -> None:
        result = run_account(r"""
await boot(base());
go("#/settings");
assert(node("account-delete-open").hidden===true&&node("account-delete-form").hidden===true,"a delete button in dev mode");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_button_lives_in_the_account_section_and_asks_through_server_fetch(self) -> None:
        html = INDEX.read_text(encoding="utf-8")
        block = panel(html, "settings")
        account = block[block.index('<section id="settings-account"'):block.index("</section>", block.index('<section id="settings-account"'))]
        for ident in ("account-delete-open", "account-delete-form", "account-delete-warning", "account-delete-password",
                      "account-delete-send", "account-delete-cancel", "account-delete-status"):
            self.assertEqual(html.count(f'id="{ident}"'), 1, ident)
            self.assertIn(f'id="{ident}"', account, ident)
        warning = account[account.index('id="account-delete-warning"'):]
        warning = warning[warning.index(">") + 1:warning.index("</p>")]
        self.assertIn("되돌릴 수 없어요", warning)
        self.assertIn("비밀번호를 한 번 더", warning)
        script = html.split("<script>", 1)[1]
        body = script.split("async function deleteAccount(", 1)[1].split("\n}\n", 1)[0]
        self.assertIn('serverFetch("/api/auth/delete-account"', body)
        self.assertIsNone(re.search(r"(?<![\w.])fetch\(", body))


if __name__ == "__main__":
    unittest.main()
