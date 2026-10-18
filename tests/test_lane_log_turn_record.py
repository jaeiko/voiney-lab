"""Lane LOG, decision 1: one server record line per turn, codes only.

Lane FX3 found that the router's reason for handing a turn to the rules went
to the screen's socket only (``turn.route_decision.router.fallback_reason``),
so the server's own log could not say how often the model was late. Now every
voice turn that ends writes one ``pilot.turn`` line to the server log and one
row to the pilot metrics store (``pilot_metrics.sqlite`` in the workspace
folder): what the router did (off, a front rule, used, handed to the rules
with a reason code -- timeout, rate_limit, format, refused, error -- or
stopped), the turn's latency, whether the system asked back, and where a
state change came from (voice, a button, a tapped candidate).

Numbers, codes and times only: no words that were said, no protocol text, and
in the log line no account. A metrics write that fails never stops the turn.
"""

from __future__ import annotations

import asyncio
import sqlite3
import unittest
from unittest.mock import patch

from tests.lane_n_support import VoiceNotesHarness, shown
from tests.lane_ra_support import FakeAsyncOpenAI
from tests.router_fakes import FakeRouterClient, answer_reply
from tests.test_lane_fx3_clarify_choice import CHOICE, choice_fixture
from voiney_lab.model_providers import ModelProviderError

LOG_NAME = "voiney_lab"


def _metrics(harness):
    from voiney_lab.pilot_metrics import PILOT_METRICS_FILENAME, PilotMetricsStore

    return PilotMetricsStore(harness.workspace_dir / PILOT_METRICS_FILENAME)


def _lines(logs) -> list[str]:
    return [line.split(":", 2)[2] for line in logs.output if "pilot.turn " in line]


class ReasonCodeTests(unittest.TestCase):
    def test_each_router_reason_has_one_record_code(self) -> None:
        from voiney_lab.llm_router import router_reason_code

        for reason, code in (
            (None, "none"),
            ("timeout", "timeout"),
            ("model_unavailable:rate_limited", "rate_limit"),
            ("no_reply", "format"),
            ("answer_unreadable", "format"),
            ("refused:more_than_one_proposal", "refused"),
            ("refused:handoff_not_by_voice", "refused"),
            ("answer_rejected:numbers,display", "refused"),
            ("model_error", "error"),
            ("something_new", "error"),
        ):
            self.assertEqual(router_reason_code(reason), code, reason)


class VoiceTurnTests(VoiceNotesHarness, unittest.TestCase):
    def test_a_voice_turn_writes_one_line_of_codes_and_one_row(self) -> None:
        with self.assertLogs(LOG_NAME, level="INFO") as logs:
            socket = self.turns("프로토콜 시작해줘", "1단계 완료했어", "버퍼 얘기 좀 해줘")
        self.assertTrue(shown(socket)[2])
        lines = _lines(logs)
        self.assertEqual(len(lines), 3, lines)
        for line in lines:
            for word in ("1단계", "완료", "프로토콜", "버퍼", self.principal.principal_id):
                self.assertNotIn(word, line)
        self.assertRegex(lines[1], r"^pilot\.turn turn_id=2 router=off reason=none ")
        self.assertIn(" changed=1 step=1 ", lines[1])
        self.assertIn(" source=voice ", lines[1])
        self.assertRegex(lines[1], r" total_ms=\d+")
        rows = _metrics(self).turns()
        self.assertEqual([row["turn_id"] for row in rows], [1, 2, 3])
        second = rows[1]
        self.assertEqual(second["account_id"], self.principal.principal_id)
        self.assertEqual(second["session_id"], self.listener.session_id)
        self.assertEqual(second["report_id"], self.report()["report_id"])
        self.assertEqual(
            (second["router"], second["reason"], second["action"], second["source"]),
            ("off", "none", "next", "voice"))
        self.assertEqual((second["state_changed"], second["step_advanced"]), (1, 1))
        self.assertEqual(rows[2]["state_changed"], 0)
        self.assertIsInstance(second["total_ms"], int)

    def test_a_question_asked_back_is_counted(self) -> None:
        type(self).fixture = choice_fixture()
        try:
            with self.assertLogs(LOG_NAME, level="INFO") as logs:
                self.turns("프로토콜 시작해줘", "그거 얼마나 넣어?")
        finally:
            type(self).fixture = None
        rows = _metrics(self).turns()
        self.assertEqual(rows[-1]["clarified"], 1)
        self.assertIn(" clarify=1 ", _lines(logs)[-1])

    def test_a_metrics_write_that_fails_never_stops_the_turn(self) -> None:
        from voiney_lab.pilot_metrics import PilotMetricsStore

        with patch.object(
            PilotMetricsStore, "record_turn", side_effect=sqlite3.OperationalError("disk full"),
        ), self.assertLogs(LOG_NAME, level="WARNING") as logs:
            socket = self.turns(
                "프로토콜 시작해줘", "1단계 완료했어",
                snapshot=lambda listener: listener.curated_protocol_session.current_index)
        self.assertTrue(shown(socket)[2])
        self.assertEqual(self.snapshot, 1)
        self.assertTrue(any("pilot.metrics.failed" in line for line in logs.output))


class RouterTurnTests(VoiceNotesHarness, unittest.TestCase):
    def _run(self, *script, timeout: str = "2"):
        self.environment.update({
            "VOINEY_LAB_LLM_ROUTER_ENABLED": "true", "VOINEY_LAB_ROUTER_PROVIDER": "xai",
            "VOINEY_LAB_ROUTER_MODEL": "fake-router-model", "XAI_API_KEY": "test-only-not-a-key",
            "VOINEY_LAB_TRANSLATION_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "",
            "VOINEY_LAB_LLM_ROUTER_TIMEOUT_SECONDS": timeout,
        })
        FakeAsyncOpenAI.client = FakeRouterClient(*script)

        async def scenario(socket, listener, say):
            await say(1, "프로토콜 시작해줘")
            await say(2, "솔루션 에이는 어디에 보관해?")

        with self.assertLogs(LOG_NAME, level="INFO") as logs:
            self.voice(scenario, patch("voiney_lab.server.AsyncOpenAI", FakeAsyncOpenAI))
        return _metrics(self).turns(), _lines(logs)

    def test_a_late_model_is_recorded_as_handed_to_the_rules_for_timeout(self) -> None:
        rows, lines = self._run((answer_reply("늦은 답"), 0.8), timeout="0.3")
        self.assertEqual((rows[-1]["router"], rows[-1]["reason"]), ("fallback", "timeout"))
        self.assertIn("router=fallback reason=timeout", lines[-1])

    def test_the_request_limit_is_recorded_as_stopped(self) -> None:
        rows, lines = self._run(ModelProviderError("openai", "rate_limited", 429))
        self.assertEqual((rows[-1]["router"], rows[-1]["reason"]), ("stopped", "rate_limit"))
        self.assertEqual(rows[-1]["state_changed"], 0)
        self.assertIn("router=stopped reason=rate_limit", lines[-1])

    def test_a_model_answer_is_recorded_as_used(self) -> None:
        rows, lines = self._run(answer_reply("원문에 나와 있지 않아요.", source_kind="none"))
        self.assertEqual((rows[-1]["router"], rows[-1]["reason"]), ("used", "none"))
        self.assertEqual(rows[0]["router"], "front")

    def test_an_answer_a_check_rejects_is_recorded_as_refused(self) -> None:
        # "4 °C" is in no source text: the number check drops the answer.
        rows, lines = self._run(answer_reply("4 °C에 보관해요."))
        self.assertEqual((rows[-1]["router"], rows[-1]["reason"]), ("fallback", "refused"))
        self.assertIn("router=fallback reason=refused", lines[-1])


class SourceTests(VoiceNotesHarness, unittest.TestCase):
    fixture = choice_fixture()

    def test_a_tapped_candidate_is_the_source_of_its_turn(self) -> None:
        async def scenario(socket, listener, say):
            await say(1, "프로토콜 시작해줘")
            await say(2, "그거 얼마나 넣어?")
            listener.next_turn_id = 3
            listener.cooldown_until = 0
            socket.control({**CHOICE, "generation": listener.generation})
            await socket.wait_for(
                lambda item: item["type"] == "turn.done" and item.get("turn_id") == 3)
            listener.playback_ended(3)
            await asyncio.sleep(0.05)

        self.voice(scenario)
        rows = _metrics(self).turns()
        self.assertEqual([row["source"] for row in rows], ["voice", "voice", "candidate"])

    def test_a_pause_button_is_a_state_change_by_button(self) -> None:
        async def scenario(socket, listener, say):
            await say(1, "프로토콜 시작해줘")
            socket.control({
                "type": "workflow.pause", "configuration_id": 1,
                "generation": listener.generation,
            })
            await socket.wait_for(
                lambda item: item["type"] == "protocol.fixture.state"
                and item.get("action") == "pause")

        self.voice(scenario)
        marks = [
            (row["kind"], row["code"]) for row in _metrics(self).marks()
            if row["kind"] == "button_change"
        ]
        self.assertEqual(marks, [("button_change", "pause")])


if __name__ == "__main__":
    unittest.main()
