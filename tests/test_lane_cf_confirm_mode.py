"""Lane CF, decision 1 (field interviews, 2026-10-07/08): how values are confirmed.

Each experimenter picks one way, remembered, changed by voice or on the
screen:

* 되읽기 (readback, the default): a value recorded is read back, decimal
  point included, and nothing is asked; a wrong one is fixed with "고쳐 줘";
* 바로 확인 (confirm): each value is asked about -- "맞으면 '네'라고 해
  주세요" -- before it is stored;
* 조용히 (quiet): nothing is read back; the values are confirmed together in
  the end-of-experiment review (lane N).

Simple commands never ask, whichever way is chosen; ending the experiment,
skipping steps, going back a step and taking back a completion are always
asked about once.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.identity_support import development_principal
from tests.lane_cf_support import Session, VoiceNotesHarness, shown, wash_session
from voiney_lab import server as server_module
from voiney_lab.curated_protocol import (
    CONFIRM_MODES,
    QUESTION_TIMINGS,
    experimenter_setting_request,
)
from voiney_lab.workspace_store import (
    WORKSPACE_SCHEMA_VERSION,
    WorkspaceSettings,
    initialize_workspace_store,
)

INDEX_HTML = Path(__file__).resolve().parents[1] / "src/voiney_lab/static/index.html"


class SettingWordsTests(unittest.TestCase):
    """The words that change a setting, read by rule."""

    def test_the_three_ways_and_the_two_timings(self) -> None:
        self.assertEqual(CONFIRM_MODES, ("readback", "confirm", "quiet"))
        self.assertEqual(QUESTION_TIMINGS, ("before_start", "during"))

    def test_each_way_is_named_by_its_words(self) -> None:
        cases = {
            "확인 질문 켜 줘": {"confirm_mode": "confirm"},
            "확인 질문 켜줘": {"confirm_mode": "confirm"},
            "바로 확인 모드로 바꿔 줘": {"confirm_mode": "confirm"},
            "확인 질문 꺼 줘": {"confirm_mode": "readback"},
            "되읽기 모드로 해 줘": {"confirm_mode": "readback"},
            "조용히 모드": {"confirm_mode": "quiet"},
            "조용히 모드로 바꿔줘": {"confirm_mode": "quiet"},
        }
        for said, expected in cases.items():
            with self.subTest(said=said):
                self.assertEqual(experimenter_setting_request(said), expected)

    def test_other_words_are_not_a_setting(self) -> None:
        for said in ("조용히 해", "확인해 줘", "질문 있어", "확인 질문 켜도 돼?", "모드가 뭐야", "1단계 완료했어"):
            with self.subTest(said=said):
                self.assertIsNone(experimenter_setting_request(said))


class VoiceSettingTests(Session, unittest.TestCase):
    """A setting said aloud is a front rule; it changes no workflow state."""

    def test_saying_a_way_changes_it_and_says_what_it_does(self) -> None:
        self.open_with(1)
        before = self.projection()
        plan = self.say("확인 질문 켜 줘")
        self.assertEqual(self.session.confirm_mode, "confirm")
        self.assertEqual(plan.setting_change, {"confirm_mode": "confirm"})
        self.assertEqual(
            plan.display_text,
            "확인 방식을 '바로 확인'으로 바꿨어요. 수치를 기록할 때마다 맞는지 여쭤볼게요.")
        self.assertFalse(plan.state_changed)
        self.assertEqual(self.projection()[:3], before[:3])
        self.assertEqual(self.session.last_front_rule, "experimenter_setting")
        plan = self.say("조용히 모드")
        self.assertEqual(self.session.confirm_mode, "quiet")
        self.assertEqual(
            plan.display_text,
            "확인 방식을 '조용히'로 바꿨어요. 기록은 되읽지 않고, 실험이 끝날 때 수치를 한 번에 확인해요.")
        plan = self.say("확인 질문 꺼 줘")
        self.assertEqual(self.session.confirm_mode, "readback")
        self.assertEqual(
            plan.display_text,
            "확인 방식을 '되읽기'로 바꿨어요. 수치를 기록하면 되읽어 드리고 묻지 않아요. "
            "틀리면 '고쳐 줘'라고 해 주세요.")
        plan = self.say("확인 질문 꺼 줘")
        self.assertEqual(plan.display_text, "이미 '되읽기' 방식이에요.")
        self.assertIsNone(plan.setting_change)

    def test_a_setting_is_heard_before_the_start_too(self) -> None:
        self.open_with(None)
        plan = self.say("조용히 모드")
        self.assertEqual(self.session.confirm_mode, "quiet")
        self.assertEqual(plan.setting_change, {"confirm_mode": "quiet"})
        self.assertFalse(self.session.active)

    def test_the_settings_are_what_the_router_snapshot_names(self) -> None:
        self.open_with(1, confirm_mode="quiet")
        # Lane WV (2026-10-09) added "web_lookup", on unless turned off.
        self.assertEqual(self.session.experimenter_settings(),
                         {"confirm_mode": "quiet", "question_timing": "during", "web_lookup": "on"})

    def test_an_unknown_value_falls_back_to_the_default(self) -> None:
        self.open_with(1, confirm_mode="sometimes")
        self.assertEqual(self.session.confirm_mode, "readback")


class NoteByModeTests(unittest.TestCase):
    """A value recorded, in each way."""

    def started(self, mode: str) -> Session:
        turns = wash_session(confirm_mode=mode)
        turns.say("프로토콜 시작해줘")
        turns.say("1단계 완료했어")
        return turns

    def test_readback_stores_at_once_and_asks_nothing(self) -> None:
        turns = self.started("readback")
        plan = turns.say("실험노트에 적어 줘, 0.5 mL")
        self.assertEqual(plan.note_record["content"], "0.5 mL")
        self.assertEqual(plan.note_record["values"][0]["text"], "0.5 mL")
        self.assertIsNone(turns.session.pending_note_confirmation)

    def test_confirm_asks_first_and_stores_only_on_a_yes(self) -> None:
        turns = self.started("confirm")
        plan = turns.say("실험노트에 적어 줘, 0.5 mL")
        self.assertIsNone(plan.note_record)
        self.assertFalse(plan.reported_observation)
        self.assertEqual(plan.display_text, "0.5 mL로 기록할까요? 맞으면 '네'라고 해 주세요.")
        self.assertEqual(plan.speech_text, "영 점 오 밀리리터로 기록할까요? 맞으면 '네'라고 해 주세요.")
        self.assertEqual(turns.session.last_front_rule, "note_record")
        plan = turns.say("네")
        self.assertEqual(turns.session.last_front_rule, "yes_no_open_question")
        self.assertEqual(plan.note_record["content"], "0.5 mL")
        self.assertTrue(plan.reported_observation)
        self.assertEqual(plan.observation_predicate, "measurement")

    def test_confirm_a_no_stores_nothing_and_asks_again_for_the_value(self) -> None:
        turns = self.started("confirm")
        turns.say("실험노트에 적어 줘, 0.5 mL")
        plan = turns.say("아니")
        self.assertIsNone(plan.note_record)
        self.assertFalse(plan.reported_observation)
        self.assertEqual(plan.display_text, "기록하지 않았어요. 값을 다시 말씀해 주세요.")

    def test_confirm_the_question_lasts_one_turn(self) -> None:
        turns = self.started("confirm")
        turns.say("실험노트에 적어 줘, 0.5 mL")
        turns.say("지금 몇 단계야")
        self.assertIsNone(turns.session.pending_note_confirmation)
        plan = turns.say("네")
        self.assertIsNone(plan.note_record)

    def test_confirm_a_note_without_a_value_is_not_asked_about(self) -> None:
        turns = self.started("confirm")
        plan = turns.say("메모해 줘 튜브 라벨 A-170")
        self.assertEqual(plan.note_record["content"], "튜브 라벨 A-170")

    def test_quiet_stores_at_once(self) -> None:
        turns = self.started("quiet")
        plan = turns.say("실험노트에 적어 줘, 0.5 mL")
        self.assertEqual(plan.note_record["content"], "0.5 mL")
        self.assertIsNone(turns.session.pending_note_confirmation)

    def test_the_router_is_told_a_value_question_is_open(self) -> None:
        turns = self.started("confirm")
        turns.say("실험노트에 적어 줘, 0.5 mL")
        questions = turns.session._open_questions(
            turn_id=turns.turn_id + 1, configuration_id=1, generation=1)
        self.assertEqual(questions.first_open, "note_confirm")


class SimpleCommandTests(unittest.TestCase):
    """Simple commands do the same in every way; the four hard-to-undo ones ask once."""

    SIMPLE = ("프로토콜 시작해줘", "1단계 완료했어", "이 단계 왜 해?", "타이머 시작해줘", "2단계 완료했어")

    def test_simple_commands_are_the_same_in_every_way(self) -> None:
        seen = {}
        for mode in ("readback", "confirm", "quiet"):
            turns = wash_session(confirm_mode=mode)
            seen[mode] = [(p.action, p.state_changed, p.display_text) for p in map(turns.say, self.SIMPLE)]
            self.assertIsNone(turns.session.pending_note_confirmation)
        self.assertEqual(seen["readback"], seen["confirm"])
        self.assertEqual(seen["readback"], seen["quiet"])
        actions = [action.value for action, _, _ in seen["readback"]]
        self.assertEqual(actions[:2], ["start", "next"])

    def test_the_hard_to_undo_ones_ask_once_in_every_way(self) -> None:
        for mode in ("readback", "confirm", "quiet"):
            with self.subTest(mode=mode):
                turns = wash_session(confirm_mode=mode)
                self.assertEqual(turns.text("3단계부터 시작해줘"), "1~2단계는 건너뛰고 3단계부터 시작할까요?")
                turns.say("아니")
                turns.say("프로토콜 시작해줘")
                turns.say("1단계 완료했어")
                turns.say("2단계 완료했어")
                self.assertEqual(
                    turns.text("이전 단계로 돌아가"), "2단계 완료를 취소하고 2단계로 돌아갈까요?")
                turns.say("아니")
                self.assertEqual(
                    turns.text("방금 완료 취소"), "2단계 완료를 취소하고 2단계로 돌아갈까요?")
                turns.say("아니")
                self.assertIn("종료할까요", turns.text("실험 종료해"))
                self.assertTrue(turns.session.active)


class ServedModeTests(VoiceNotesHarness, unittest.TestCase):
    """The production path: what is said, what is stored, what is remembered."""

    def notes(self) -> list[tuple[str, str]]:
        return [
            (e["category"], e["user_wording"]) for e in self.report()["events"]
            if e["event_type"] == "observation"
        ]

    def test_readback_reads_the_decimal_point_and_stores_once(self) -> None:
        socket = self.turns("프로토콜 시작해줘", "1단계 완료했어", "실험노트에 적어 줘, 0.5 mL")
        self.assertEqual(shown(socket)[3], "0.5 mL로 기록했어요.")
        self.assertEqual(self.spoken[3], ["영 점 오 밀리리터로 기록했어요."])
        self.assertEqual(self.notes(), [("measurement", "0.5 mL")])

    def test_confirm_said_by_voice_is_remembered_for_the_next_session(self) -> None:
        self.turns("확인 질문 켜 줘")
        socket = self.turns(
            "프로토콜 시작해줘", "1단계 완료했어", "실험노트에 적어 줘, 0.5 mL", "네",
            "실험노트에 적어 줘, 7 mL", "아니")
        replies = shown(socket)
        self.assertEqual(replies[3], "0.5 mL로 기록할까요? 맞으면 '네'라고 해 주세요.")
        self.assertEqual(replies[4], "0.5 mL로 기록했어요.")
        self.assertEqual(replies[6], "기록하지 않았어요. 값을 다시 말씀해 주세요.")
        self.assertEqual(self.notes(), [("measurement", "0.5 mL")])
        settings = [item for item in socket.sent if item.get("type") == "experimenter.settings"]
        self.assertEqual(settings[0]["settings"]["confirm_mode"], "confirm")

    def test_quiet_does_not_read_back(self) -> None:
        self.turns("조용히 모드")
        socket = self.turns("프로토콜 시작해줘", "1단계 완료했어", "실험노트에 적어 줘, 0.5 mL")
        self.assertEqual(shown(socket)[3], "기록했어요.")
        self.assertEqual(self.spoken[3], ["기록했어요."])
        self.assertEqual(self.notes(), [("measurement", "0.5 mL")])

    def test_the_setting_is_kept_append_only_in_the_workspace(self) -> None:
        self.turns("조용히 모드", "확인 질문 켜 줘")
        store = initialize_workspace_store(WorkspaceSettings(True, self.workspace_dir))
        try:
            self.assertEqual(store.experimenter_settings(self.principal), {"confirm_mode": "confirm"})
            rows = store._connection.execute(
                "SELECT name,value,source FROM experimenter_settings ORDER BY sequence_id").fetchall()
        finally:
            store.close()
        self.assertEqual([tuple(row) for row in rows],
                         [("confirm_mode", "quiet", "voice"), ("confirm_mode", "confirm", "voice")])


class SettingsApiTests(unittest.TestCase):
    """The screen reads and changes the same settings through the server."""

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

    def test_defaults_then_a_screen_change(self) -> None:
        # Lane WV (2026-10-09) added "web_lookup", on by default.
        self.assertEqual(
            self.call(server_module.get_experimenter_settings),
            {"settings": {"confirm_mode": "readback", "question_timing": "before_start",
                          "web_lookup": "on"}})
        changed = self.call(server_module.put_experimenter_settings, {"confirm_mode": "quiet"})
        self.assertEqual(changed["settings"]["confirm_mode"], "quiet")
        self.assertEqual(
            self.call(server_module.get_experimenter_settings)["settings"]["confirm_mode"], "quiet")

    def test_an_unknown_name_or_value_is_refused(self) -> None:
        for body in ({"confirm_mode": "sometimes"}, {"volume": "loud"}, {}):
            with self.subTest(body=body):
                with self.assertRaises(server_module.HTTPException) as raised:
                    self.call(server_module.put_experimenter_settings, body)
                self.assertEqual(raised.exception.status_code, 400)

    def test_without_a_workspace_the_server_remembers_them_while_it_runs(self) -> None:
        with patch.dict(os.environ, {"VOINEY_LAB_WORKSPACE_ENABLED": "false"}), \
                patch.dict(server_module._EXPERIMENTER_SETTINGS_MEMORY, clear=True):
            server_module.put_experimenter_settings({"question_timing": "during"})
            self.assertEqual(
                server_module.get_experimenter_settings()["settings"]["question_timing"], "during")


class WorkspaceSchemaTests(unittest.TestCase):

    def test_a_version_7_workspace_gains_the_settings_table(self) -> None:
        import sqlite3

        from voiney_lab import workspace_store as ws

        self.assertEqual(WORKSPACE_SCHEMA_VERSION, 8)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ws.WORKSPACE_DATABASE_FILENAME
            connection = sqlite3.connect(path)
            connection.executescript(ws.SCHEMA)
            for script in (ws.MIGRATION_1_TO_2, ws.MIGRATION_2_TO_3, ws.MIGRATION_3_TO_4,
                           ws.MIGRATION_4_TO_5, ws.MIGRATION_5_TO_6, ws.MIGRATION_6_TO_7):
                connection.executescript(script)
            connection.close()
            store = initialize_workspace_store(WorkspaceSettings(True, Path(tmp)))
            try:
                version = store._connection.execute(
                    "SELECT schema_version FROM schema_metadata").fetchone()[0]
                self.assertEqual(version, 8)
                principal = development_principal()
                store.bootstrap_principal(principal)
                store.record_experimenter_setting(
                    principal, name="confirm_mode", value="quiet", source="screen")
                for statement in ("DELETE FROM experimenter_settings",
                                  "UPDATE experimenter_settings SET value='confirm'"):
                    with self.assertRaises(sqlite3.DatabaseError):
                        store._connection.execute(statement)
            finally:
                store.close()


class SettingsScreenTests(unittest.TestCase):

    def test_the_screen_has_both_settings_and_uses_the_server(self) -> None:
        html = INDEX_HTML.read_text(encoding="utf-8")
        for marker in ('id="confirm-mode"', "/api/experimenter/settings",
                       '"experimenter.settings"', ">되읽기", ">바로 확인", ">조용히"):
            with self.subTest(marker=marker):
                self.assertIn(marker, html)


if __name__ == "__main__":
    unittest.main()
