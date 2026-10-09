"""Lane VT, decision 6 (2026-10-09): the "먼저 알려 주기" setting.

What the server says before it is asked is the experimenter's to choose:

* 모두 (``all``, the default): the timer's end and its last minute, the round
  of a fixed repeat, and where the run stands on coming back (decisions 1, 2,
  4 and 5);
* 필요한 것만 (``needed``): the timer's end and the round (decisions 1 and 4);
* 끄기 (``off``): nothing is said first -- the screen notice and the sound of
  a timer's end stay.

It is changed by voice ("먼저 알려 주기 꺼 줘") or on the screen and kept the
way ``confirm_mode`` is (decision 7's table). Saying it changes no workflow
state.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.identity_support import development_principal
from tests.lane_cf_support import Session
from tests.test_screen_cleanup import run_page_script
from voiney_lab import server as server_module
from voiney_lab.curated_protocol import (
    PROACTIVE_MODES,
    CuratedProtocolSession,
    experimenter_setting_request,
)
from voiney_lab.workspace_store import WorkspaceSettings, initialize_workspace_store

INDEX_HTML = Path(__file__).resolve().parents[1] / "src/voiney_lab/static/index.html"


class WordsTests(unittest.TestCase):

    def test_the_three_choices(self) -> None:
        self.assertEqual(PROACTIVE_MODES, ("all", "needed", "off"))

    def test_each_choice_is_named_by_its_words(self) -> None:
        cases = {
            "먼저 알려 주기 꺼 줘": "off",
            "먼저 알려주기 꺼줘": "off",
            "먼저 알려 주기는 꺼 주세요": "off",
            "먼저 알려 주기 끄기": "off",
            "먼저 알려 주기 켜 줘": "all",
            "먼저 알려 주기 다시 켜 줘": "all",
            "먼저 알려 주기 모두": "all",
            "먼저 알려 주기 모두로 해 줘": "all",
            "먼저 알려 주기 필요한 것만": "needed",
            "먼저 알려 주기 필요한 것만 해 줘": "needed",
            "먼저 알려 주기 필요한 거만으로 바꿔 줘": "needed",
        }
        for said, value in cases.items():
            with self.subTest(said=said):
                self.assertEqual(experimenter_setting_request(said), {"proactive_mode": value})

    def test_a_question_about_it_changes_nothing(self) -> None:
        for said in ("먼저 알려 주기 꺼도 돼?", "먼저 알려 주기가 뭐야", "알려 줘", "먼저 알려 줘"):
            with self.subTest(said=said):
                self.assertIsNone(experimenter_setting_request(said))


class VoiceTests(Session, unittest.TestCase):

    def test_saying_it_changes_the_setting_and_no_state(self) -> None:
        self.open_with(3)
        before = self.projection()
        plan = self.say("먼저 알려 주기 꺼 줘")
        self.assertEqual(self.session.proactive_mode, "off")
        self.assertEqual(plan.setting_change, {"proactive_mode": "off"})
        self.assertEqual(self.session.last_front_rule, "experimenter_setting")
        self.assertFalse(plan.state_changed)
        self.assertEqual(self.projection(), before)
        self.assertEqual(
            plan.speech_text,
            "먼저 알려 주기를 껐어요. 타이머가 끝나면 화면 알림과 소리만 내요.")

    def test_each_choice_says_what_it_does(self) -> None:
        self.open_with(3)
        plan = self.say("먼저 알려 주기 필요한 것만")
        self.assertEqual(self.session.proactive_mode, "needed")
        self.assertEqual(
            plan.speech_text,
            "먼저 알려 주기를 '필요한 것만'으로 바꿨어요. 타이머가 끝날 때와 반복 횟수만 말로 알려 드려요.")
        plan = self.say("먼저 알려 주기 모두")
        self.assertEqual(self.session.proactive_mode, "all")
        self.assertEqual(
            plan.speech_text,
            "먼저 알려 주기를 '모두'로 바꿨어요. 타이머가 끝날 때와 1분 전, 반복 횟수, "
            "오래 쉬었다 말씀하실 때 지금 단계를 말로 알려 드려요.")

    def test_the_same_choice_again_is_said_unchanged(self) -> None:
        self.open_with(3)
        plan = self.say("먼저 알려 주기 켜 줘")
        self.assertEqual(plan.intent_kind, "experimenter_setting_unchanged")
        self.assertEqual(plan.speech_text, "이미 먼저 알려 주기가 '모두'로 되어 있어요.")
        self.assertIsNone(plan.setting_change)

    def test_it_is_heard_before_the_start(self) -> None:
        self.open_with(None)
        plan = self.say("먼저 알려 주기 꺼 줘")
        self.assertEqual(plan.setting_change, {"proactive_mode": "off"})
        self.assertFalse(self.session.active)


class SessionTests(unittest.TestCase):

    def test_the_default_is_all_and_the_server_applies_a_kept_choice(self) -> None:
        from tests.lane_cb_support import headspace_fixture

        session = CuratedProtocolSession(headspace_fixture())
        self.assertEqual(session.proactive_mode, "all")
        session.apply_experimenter_settings({"proactive_mode": "needed"})
        self.assertEqual(session.proactive_mode, "needed")
        self.assertEqual(session.experimenter_settings()["proactive_mode"], "needed")
        session.apply_experimenter_settings({"proactive_mode": "loud"})
        self.assertEqual(session.proactive_mode, "needed")


class ServerSettingTests(unittest.TestCase):

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.workspace_dir = Path(tmp.name) / "workspace"
        self.principal = development_principal()
        store = initialize_workspace_store(WorkspaceSettings(True, self.workspace_dir))
        store.bootstrap_principal(self.principal)
        store.close()

    def call(self, function, *args):
        token = server_module._REQUEST_PRINCIPAL.set(self.principal)
        try:
            with patch.dict(os.environ, {
                "VOINEY_LAB_WORKSPACE_ENABLED": "true",
                "VOINEY_LAB_WORKSPACE_DATA_DIR": str(self.workspace_dir),
            }):
                return function(*args)
        finally:
            server_module._REQUEST_PRINCIPAL.reset(token)

    def test_the_default_then_a_screen_change_kept_like_confirm_mode(self) -> None:
        self.assertEqual(
            self.call(server_module.get_experimenter_settings)["settings"]["proactive_mode"], "all")
        changed = self.call(server_module.put_experimenter_settings, {"proactive_mode": "off"})
        self.assertEqual(changed["settings"]["proactive_mode"], "off")
        store = initialize_workspace_store(WorkspaceSettings(True, self.workspace_dir))
        try:
            rows = store._connection.execute(
                "SELECT name,value,source FROM experimenter_settings ORDER BY sequence_id").fetchall()
        finally:
            store.close()
        self.assertEqual([tuple(row) for row in rows], [("proactive_mode", "off", "screen")])
        with self.assertRaises(server_module.HTTPException):
            self.call(server_module.put_experimenter_settings, {"proactive_mode": "sometimes"})

    def test_without_a_workspace_it_is_held_while_the_server_runs(self) -> None:
        with patch.dict(os.environ, {"VOINEY_LAB_WORKSPACE_ENABLED": "false"}), \
                patch.dict(server_module._EXPERIMENTER_SETTINGS_MEMORY, clear=True):
            server_module.put_experimenter_settings({"proactive_mode": "needed"})
            self.assertEqual(
                server_module.get_experimenter_settings()["settings"]["proactive_mode"], "needed")


class ScreenTests(unittest.TestCase):

    def test_the_choice_is_on_the_screen_and_saved_through_the_server(self) -> None:
        html = INDEX_HTML.read_text(encoding="utf-8")
        for marker in ('id="proactive-mode"', 'saveExperimenterSetting("proactive_mode"',
                       ">모두", ">필요한 것만", ">끄기"):
            with self.subTest(marker=marker):
                self.assertIn(marker, html)
        result = run_page_script(r"""
renderExperimenterSettings({confirm_mode:"readback",question_timing:"before_start",web_lookup:"on",proactive_mode:"needed"});
assert(node("proactive-mode").value==="needed","the setting was not rendered");
renderExperimenterSettings({proactive_mode:"loud"});
assert(node("proactive-mode").value==="needed","an unknown value was taken");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
