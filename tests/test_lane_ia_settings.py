"""Lane IA, decision ⑥ (2026-10-19): the settings tab.

Lane UI0's sheet (voice input, guidance; the protocol group as before), the
agent's voice (lane ON's list, preview and setting), the account (the name,
log out, a new password -- lane ON's routes; nothing to press in dev mode),
the home-screen guide (lane UI2), and the developer details only in dev mode
(the server's session says dev). Shares the fake page of
tests/test_lane_ia_tabs.py.
"""

from __future__ import annotations

import unittest

from tests.test_lane_ia_tabs import SETUP, panel, run
from tests.test_screen_cleanup import INDEX


class SettingsTests(unittest.TestCase):
    """Decision ⑥: the settings tab and what moved into it."""

    def test_the_agent_voice_is_listed_from_the_server_and_a_pick_is_saved(self) -> None:
        result = run(r"""
await boot(base());
go("#/settings");
const list=node("settings-agent-voice-list");
const picks=walk(list).filter(el=>hasClass(el,"wizard-option-pick"));
assert(picks.length===4,`voices: ${picks.length}`);
assert(picks[0].getAttribute("aria-pressed")==="true","the default voice is not marked");
assert(walk(list).filter(el=>hasClass(el,"wizard-option-aside")).length===4,"no 들어 보기 beside each voice");
calls.length=0;
picks[1].dispatch("click");await settle(8);
const put=posts().find(call=>call.url.includes("/api/experimenter/settings"));
assert(put&&put.body.agent_voice==="ko-KR-Chirp3-HD-Kore",`saved: ${JSON.stringify(put?.body)}`);
assert(node("settings-agent-voice-note").textContent.includes("저장"),node("settings-agent-voice-note").textContent);
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_account_section_logs_out_and_changes_the_password_in_accounts_mode(self) -> None:
        result = run(r"""
await boot([["/api/auth/logout",{signed_out:true}],["/api/auth/password",{changed:true}],["/api/auth/session",ACCOUNT_SESSION(true)],...base(ACCOUNT_SESSION(true))]);
go("#/settings");
assert(node("settings-account-name").textContent.includes("김재준"),node("settings-account-name").textContent);
assert(node("account-logout").hidden===false&&node("account-password-open").hidden===false,"the account buttons are hidden");
node("account-password-open").dispatch("click");
assert(node("account-password-form").hidden===false,"the password form did not open");
node("account-password-current").value="password1";node("account-password-new").value="password2";
node("account-password-send").dispatch("click");await settle(8);
const changed=posts().find(call=>call.url.includes("/api/auth/password"));
assert(changed&&changed.body.current==="password1"&&changed.body.new==="password2",`password: ${JSON.stringify(changed?.body)}`);
assert(node("account-password-status").textContent.includes("바꿨"),node("account-password-status").textContent);
node("account-logout").dispatch("click");await settle(8);
assert(posts().some(call=>call.url.includes("/api/auth/logout")),"no logout request");
assert(document.body.dataset.auth==="required","the login did not come back after the logout");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_dev_mode_shows_no_account_buttons_but_the_developer_details(self) -> None:
        result = run(r"""
await boot(base());
go("#/settings");
assert(node("settings-account-name").textContent.includes("실험자 (개발 로그인)"),node("settings-account-name").textContent);
assert(node("account-logout").hidden===true&&node("account-password-open").hidden===true,"account buttons in dev mode");
assert(document.body.dataset.dev==="on",`dev: ${document.body.dataset.dev}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_accounts_mode_hides_the_developer_details(self) -> None:
        html = INDEX.read_text(encoding="utf-8")
        self.assertIn('body:not([data-dev="on"]) #dev-diagnostics{display:none}', html)
        result = run(r"""
await boot([["/api/auth/session",ACCOUNT_SESSION(true)],...base(ACCOUNT_SESSION(true))]);
assert(document.body.dataset.dev==="off",`dev: ${document.body.dataset.dev}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_settings_panel_keeps_the_sheet_and_adds_the_sections_outside_it(self) -> None:
        html = INDEX.read_text(encoding="utf-8")
        block = panel(html, "settings")
        order = [block.index(marker) for marker in ('id="settings-sheet"', 'id="settings-agent-voice"', 'id="settings-account"', 'id="install-guide"', 'id="dev-diagnostics"')]
        self.assertEqual(order, sorted(order))
        sheet = block[block.index('<section id="settings-sheet"'):block.index("</section>", block.index('<section id="settings-sheet"'))]
        for outside in ("settings-agent-voice", "settings-account", "install-guide", "dev-diagnostics"):
            self.assertNotIn(f'id="{outside}"', sheet)
        self.assertIn('<summary>홈 화면에 추가</summary>', block)
        for ident in ("settings-agent-voice", "settings-account", "account-logout", "account-password-open", "account-password-form"):
            self.assertEqual(html.count(f'id="{ident}"'), 1, ident)
            self.assertIn(f'id="{ident}"', block, ident)


if __name__ == "__main__":
    unittest.main()
