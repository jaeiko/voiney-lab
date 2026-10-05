"""There is no report sending; the voice says so (lane M1, decision 5b).

Decision of 2026-10-04: the rules used to answer "교수님께 보고서 보내줘" by
asking to send the current report to a placeholder address ("등록된
지도교수님(advisor@university.edu)로 ... 전송할까요?"), and the router's
fallback said "보고서는 화면에서 보내 주세요." Neither can happen: nothing
sends a report. Both paths now say "보고서 전송은 지원하지 않아요. 보고서는
화면에서 Word나 마크다운 파일로 받을 수 있어요." -- the two download formats
the screen offers -- open no question and record nothing.
"""

from __future__ import annotations

import asyncio
import unittest
from pathlib import Path

from tests.protocol_vocabulary_support import miniprep_fixture
from tests.router_fakes import FakeRouterClient, answer_reply
from voiney_lab.curated_protocol import CuratedProtocolAction, CuratedProtocolSession
from voiney_lab.llm_router import LlmRouterSettings, route_turn_with_llm_router
from voiney_lab.runtime_routing import route_curated_runtime_turn

REPLY_KO = "보고서 전송은 지원하지 않아요. 보고서는 화면에서 Word나 마크다운 파일로 받을 수 있어요."
REPLY_EN = (
    "Sending reports is not supported. You can download the report from the "
    "screen as a Word or Markdown file."
)
HANDOFF_REQUESTS = (
    "교수님께 보고서 보내줘",
    "안전관리자에게 이상사항 전달해줘",
    "보고서 전송해줘",
)
ROOT = Path(__file__).resolve().parents[1]


def _session() -> CuratedProtocolSession:
    session = CuratedProtocolSession(miniprep_fixture())
    session.activate_configured()
    session.plan("시작", turn_id=1, language="ko", configuration_id=1, generation=1)
    return session


def _rules(session, said, *, turn_id=2, language="ko"):
    return route_curated_runtime_turn(
        session, said, turn_id=turn_id, language=language,
        configuration_id=1, generation=1,
    ).plan


class ReportSendUnsupportedTests(unittest.TestCase):
    def test_the_rules_say_sending_is_not_supported(self) -> None:
        for said in HANDOFF_REQUESTS:
            with self.subTest(said=said):
                session = _session()
                before = session.state()
                plan = _rules(session, said)
                self.assertIs(plan.action, CuratedProtocolAction.REPORT_HANDOFF)
                self.assertEqual(plan.speech_text, REPLY_KO)
                self.assertEqual(plan.display_text, REPLY_KO)
                self.assertNotIn("@", plan.display_text)
                self.assertFalse(plan.state_changed)
                self.assertFalse(session.awaiting_server_confirmation)
                self.assertEqual(session.state()["revision"], before["revision"])

    def test_english(self) -> None:
        plan = _rules(_session(), "send the report to the professor", language="en")
        self.assertIs(plan.action, CuratedProtocolAction.REPORT_HANDOFF)
        self.assertEqual(plan.speech_text, REPLY_EN)

    def test_a_yes_afterwards_sends_nothing(self) -> None:
        session = _session()
        _rules(session, "교수님께 보고서 보내줘")
        plan = _rules(session, "응, 보내줘", turn_id=3)
        self.assertIsNot(plan.action, CuratedProtocolAction.REPORT_HANDOFF)
        self.assertNotIn("전송", plan.speech_text or "")
        self.assertFalse(plan.state_changed)

    def test_the_router_fallback_says_the_same(self) -> None:
        session = _session()
        said = "교수님께 보고서 보내줘"
        outcome = asyncio.run(route_turn_with_llm_router(
            session, said, turn_id=2, language="ko",
            settings=LlmRouterSettings(enabled=True, model="fake", timeout_seconds=1.0),
            client_factory=lambda: FakeRouterClient(answer_reply(
                "교수님께 보고서를 보내드릴까요?", source_kind="none",
            )),
            rule_route=lambda: _async(route_curated_runtime_turn(
                session, said, turn_id=2, language="ko",
                configuration_id=1, generation=1,
            )),
            configuration_id=1, generation=1,
        ))
        self.assertEqual(outcome.handled_by, "fallback_rules")
        self.assertEqual(outcome.plan.speech_text, REPLY_KO)
        self.assertFalse(session.awaiting_server_confirmation)

    def test_no_placeholder_address_is_left_in_the_package(self) -> None:
        for path in (ROOT / "src/voiney_lab").rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                self.assertNotIn("advisor@university.edu", text)
                self.assertNotIn("safety@university.edu", text)
                self.assertNotIn("보고서는 화면에서 보내 주세요", text)


async def _async(value):
    return value


if __name__ == "__main__":
    unittest.main()
