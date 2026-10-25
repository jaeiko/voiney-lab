"""Lane FIXV decision 2 (2026-10-24): a step's name is said once.

A step's Korean (reviewed or machine) begins with its own label, "2단계: "
(the translation style guide asks for it). Four replies put that Korean after
a lead that already names the step, so the voice said "2단계: 2단계: …":
the next-step preview ("다음 단계 뭐야"), a numbered step's preview
("3단계 미리보기"), a range summary ("1단계부터 3단계까지 알려줘") and the
current step read with a lab-equipment question ("피펫 어떻게 써?"). Found by
running every step of a localized protocol through the rules path with about
sixty sentences (``~/reports/fixv/tools/dup_probe.py``); no other spoken
reply named a step twice.

What is said now leaves out the Korean's own label; the screen text and the
quoted source lines stay as they were (person decision 10/24). A source line
that begins with its own number ("2 Prepare …") is a quotation and is said as
written.
"""

from __future__ import annotations

import asyncio
import dataclasses
import re
import unittest
from pathlib import Path
from unittest.mock import patch

import voiney_lab.server as server_module
from tests.protocol_vocabulary_support import build_fixture
from tests.test_lane_vox_sentence_speech import ONE_SECOND, FakeClock, Socket
from voiney_lab.curated_protocol import CuratedProtocolSession
from voiney_lab.language import Transcription
from voiney_lab.runtime_routing import route_curated_runtime_turn
from voiney_lab.server import ListenerSession, run_turn
from voiney_lab.tools import ToolContext
from voiney_lab.vad import TurnState

STEPS = (
    "1 Wash the column with 500 µL of Buffer 1 and spin it for 1 minute.",
    "2 Prepare a solution of 1.5mg/mL of DTT.",
    "Remove and discard the supernatant.",
    "4 Add 25uL of the above trypsin solution and wait for 10 minutes.",
    "Store the tube at 4 degrees.",
)
KOREAN = (
    "1단계: 컬럼을 Buffer 1 500 µL로 세척하고 1 minute 동안 원심분리합니다.",
    "2단계: DTT 1.5mg/mL 용액을 준비합니다.",
    "3단계: 상층액을 제거하고 버립니다.",
    "4단계: 위의 trypsin 용액 25uL를 넣고 10 minutes 동안 기다립니다.",
    "5단계: 튜브를 4 degrees에 보관합니다.",
)
#: The four replies that named a step twice, each with a sentence that reaches it.
SAID_TWICE_BEFORE = {
    "next_information": ("다음 단계 뭐야", "다음 단계 미리 알려줘"),
    "preview_step": ("3단계 미리보기", "4단계 미리 알려줘"),
    "step_range": ("1단계부터 3단계까지 알려줘", "2에서 4단계"),
    "lab_domain_qa": ("피펫 어떻게 써?", "센트리퓨지 뭐야"),
}
#: A step label, then within the same clause the same label with "단계:" again.
TWICE = re.compile(r"(?<![0-9])(?P<label>[0-9]+)\s*단계[^\n:：]{0,30}[:：]\s*(?P=label)\s*단계")


def _fixture(*, korean: bool = True):
    fixture = build_fixture(
        protocol_id="lane-fixv-labels", title="Fictional FIXV protocol", steps=STEPS,
    )
    machine = {f"step-{index}/current_step": text for index, text in enumerate(KOREAN, 1)}
    return dataclasses.replace(fixture, machine_localizations=machine if korean else None)


def _plan(fixture, words: str, *, at: int, language: str = "ko"):
    session = CuratedProtocolSession(fixture)
    session.activate_configured()
    session.plan("시작", turn_id=1, language="ko", configuration_id=1, generation=1)
    session.current_index = at
    return route_curated_runtime_turn(
        session, words, turn_id=2, language=language, configuration_id=1, generation=1,
    ).plan


def _body(text: str) -> str:
    return text.split(": ", 1)[1]


class RulesPathTests(unittest.TestCase):

    def test_each_reply_that_named_a_step_twice_says_it_once_at_every_step(self) -> None:
        fixture = _fixture()
        for action, sentences in SAID_TWICE_BEFORE.items():
            for words in sentences:
                for at in range(len(STEPS)):
                    with self.subTest(words=words, at=at):
                        plan = _plan(fixture, words, at=at)
                        self.assertEqual(plan.action.value, action)
                        self.assertIsNone(TWICE.search(plan.speech_text), plan.speech_text)
                        if action == "next_information" and at == len(STEPS) - 1:
                            # The last step has no next step to preview.
                            continue
                        # The Korean itself is said, and each step it names is named.
                        named = [
                            index for index, korean in enumerate(KOREAN)
                            if _body(korean) in plan.speech_text
                        ]
                        self.assertTrue(named, plan.speech_text)
                        for index in named:
                            self.assertIn(f"{index + 1}단계", plan.speech_text)
                            # The screen shows the stored Korean as it is.
                            self.assertIn(KOREAN[index], plan.display_text)

    def test_the_next_step_preview_reads_once_and_keeps_the_rest_of_its_words(self) -> None:
        plan = _plan(_fixture(), "다음 단계 뭐야", at=0)
        self.assertEqual(
            plan.speech_text,
            "다음 단계 미리보기 · 2단계: DTT 1.5mg/mL 용액을 준비합니다. "
            "워크플로는 현재 1단계에 그대로 있습니다.",
        )
        self.assertEqual(
            plan.display_text,
            "다음 단계 미리보기 · 2단계: 2단계: DTT 1.5mg/mL 용액을 준비합니다. "
            "워크플로는 현재 1단계에 그대로 있습니다.",
        )

    def test_a_quoted_source_line_is_said_as_written(self) -> None:
        source_only = _fixture(korean=False)
        korean = _plan(source_only, "다음 단계 뭐야", at=0)
        self.assertIn("2단계: 2 Prepare a solution of 1.5mg/mL of DTT.", korean.speech_text)
        english = _plan(source_only, "what is the next step", at=0, language="en")
        self.assertTrue(
            english.speech_text.startswith(
                "Preview only — Step 2: 2 Prepare a solution of 1.5mg/mL of DTT."),
            english.speech_text,
        )
        summary = _plan(source_only, "1단계부터 3단계까지 알려줘", at=0)
        for line in STEPS[:3]:
            self.assertIn(line, summary.speech_text)


class SpokenTurnTests(unittest.TestCase):
    """Through run_turn: what the TTS is asked to say, and what the screen gets."""

    def test_the_voice_says_the_previewed_step_once(self) -> None:
        clock = FakeClock()
        workflow = CuratedProtocolSession(_fixture())
        workflow.active = True
        workflow.current_index = 0
        context = ToolContext(Path("/unused/offline-catalog"), None, "ko", "test_only")
        session = ListenerSession(tool_context=context, curated_protocol_session=workflow)
        session.active = True
        session.active_turn_id = 1
        session.next_turn_id = 2
        session.turn_generations[1] = session.generation
        session.accept_configuration(41, "cascade", "ko", "lane-fixv-labels")
        session.detector.state = TurnState.PROCESSING
        session.endpoint_at = clock.value
        socket = Socket(clock)
        asked: list[str] = []

        async def to_thread(function, *args, **kwargs):
            if function is server_module.synthesize:
                asked.append(args[0])
            return function(*args, **kwargs)

        async def turn() -> None:
            server_module._SPEAKING_SESSION.set(session)
            await run_turn(
                socket, session, b"\0\0", 1, 1,
                accepted_transcription=Transcription("다음 단계 뭐야", "ko"),
                accepted_stt_ms=0, clock=clock.now,
            )

        with patch("voiney_lab.server.synthesize", return_value=ONE_SECOND), \
                patch("voiney_lab.server.asyncio.to_thread", side_effect=to_thread):
            asyncio.run(turn())
        said = " ".join(asked)
        self.assertIn("2단계: DTT 1.5mg/mL 용액을 준비합니다.", said)
        self.assertIsNone(TWICE.search(said), said)
        self.assertEqual(said.count("2단계"), 1, said)
        (complete,) = socket.of("reply.complete")
        self.assertIn(KOREAN[1], complete["text"])
        self.assertEqual(workflow.current_index, 0)


if __name__ == "__main__":
    unittest.main()
