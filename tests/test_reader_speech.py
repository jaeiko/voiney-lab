"""What a reader hears: short by default, the step in Korean only when asked.

Moving between steps, the current-step reply and the observation-recorded
reply speak one short confirmation; the screen carries the Korean guidance,
the English source and its page. Asking to hear the step ("이 단계 읽어줘",
"전체 내용을 읽어줘") reads it in Korean: the reviewed Korean translation
where the fixture has one, and otherwise -- when the read-only model roles
are enabled -- an automatic Korean reading that keeps every number, unit and
protocol term of the source, labelled on screen as unreviewed. A reading
that fails that check, or a provider failure, is said aloud and the source
is read as before. Provider calls here are fake.
"""

from __future__ import annotations

import asyncio
import json
import logging
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from voiney_lab.curated_protocol import (
    CuratedProtocolAction,
    CuratedProtocolSession,
    classify_curated_control_intent,
    reader_translation_issue,
)
from voiney_lab.language import Transcription
from voiney_lab.multi_brain import MultiBrainSettings
from voiney_lab.runtime_routing import route_curated_runtime_turn
from voiney_lab.server import ListenerSession, _acknowledge_report_persistence, run_turn
from voiney_lab.tools import ToolContext
from voiney_lab.vad import TurnState

from tests.protocol_vocabulary_support import (
    MINIPREP_STEPS,
    SOURCE_PDF,
    in_gel_fixture,
    miniprep_fixture,
)

#: Marks of the screen layout that are never spoken.
SCREEN_ONLY = ("원문 · English", "답변 · 한국어", "출처", "current_step", "원문 p.")

#: A faithful Korean reading of miniprep step 1 and one that changes a number.
GOOD_READING = "1단계: 세포 덩어리를 Tris-HCl buffer로 만든 Buffer 1 250 µL에 다시 풀어 줍니다."
BAD_READING = "1단계: 세포 덩어리를 Tris-HCl buffer로 만든 Buffer 1 25 µL에 다시 풀어 줍니다."


class _Socket:
    def __init__(self) -> None:
        self.text: list[dict] = []

    async def send_text(self, value: str) -> None:
        self.text.append(json.loads(value))

    async def send_bytes(self, value: bytes) -> None:
        pass


class _FakeModel:
    """An AsyncOpenAI stand-in that returns one Korean reading per call."""

    def __init__(self, reading: str | None = None, error: Exception | None = None):
        self.reading = reading
        self.error = error
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def __call__(self, *args, **kwargs):
        return self

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content=json.dumps({"korean": self.reading}, ensure_ascii=False)
        ))])


def _listener(fixture, index: int, *, model_roles: bool) -> ListenerSession:
    context = ToolContext(Path("/unused/offline-catalog"), None, "ko", "test_only")
    workflow = CuratedProtocolSession(fixture)
    workflow.active = True
    workflow.current_index = index
    session = ListenerSession(
        tool_context=context, curated_protocol_session=workflow,
        multi_brain_settings=MultiBrainSettings(model_roles),
    )
    session.active = True
    session.accept_configuration(41, "cascade", "ko", fixture.protocol_id)
    return session


def _turn(session: ListenerSession, transcript: str, turn_id: int, model=None):
    session.active_turn_id = turn_id
    session.next_turn_id = turn_id + 1
    session.turn_generations[turn_id] = session.generation
    session.detector.state = TurnState.PROCESSING
    spoken: list[str] = []
    socket = _Socket()

    async def immediate(function, *args, **kwargs):
        return function(*args, **kwargs)

    def synthesize(text, language=None):
        spoken.append(text)
        return b"\0\0"

    with patch(
        "voiney_lab.server.transcribe", return_value=Transcription(transcript, "ko"),
    ), patch(
        "voiney_lab.server.synthesize", side_effect=synthesize,
    ), patch(
        "voiney_lab.server.asyncio.to_thread", side_effect=immediate,
    ), patch(
        "voiney_lab.server.AsyncOpenAI",
        model if model is not None else _FakeModel(error=AssertionError("no model call")),
    ), patch(
        "voiney_lab.server.require_env", return_value="offline",
    ):
        asyncio.run(run_turn(socket, session, b"\0\0", turn_id, session.generation))
    reply = next(item for item in socket.text if item["type"] == "reply.delta")
    state = next(
        item["state"] for item in socket.text if item["type"] == "protocol.fixture.state"
    )
    return spoken, reply, state


@unittest.skipUnless(
    SOURCE_PDF.is_file(),
    f"requires the externally licensed Candidate A source PDF at {SOURCE_PDF}",
)
class InGelSpeechTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = in_gel_fixture()

    def reviewed(self, label: int) -> str:
        return self.fixture.localized_fact(
            self.fixture.steps[label - 1].step_id, "current_step")

    def test_moving_to_steps_2_8_10_speaks_only_the_confirmation(self) -> None:
        for label, timer in ((2, ""), (8, " 타이머를 시작하려면 말씀해주세요."), (10, "")):
            with self.subTest(step=label):
                session = _listener(self.fixture, label - 2, model_roles=False)
                curated = session.curated_protocol_session
                # Steps 8 and 10 follow observation gates, so arrive directly.
                curated.current_index = label - 1
                spoken, reply, state = _turn(session, "현재 단계 알려줘", 1)
                self.assertEqual(
                    spoken, [f"현재 {label}단계입니다. 안내를 화면에 표시했습니다.{timer}"]
                )
                for mark in SCREEN_ONLY:
                    self.assertNotIn(mark, spoken[0])
                self.assertNotIn(self.fixture.steps[label - 1].instruction_source_text, spoken[0])
                # The screen keeps the Korean guidance, the English and the page.
                self.assertIn(self.reviewed(label), reply["text"])
                self.assertIn(self.fixture.steps[label - 1].instruction_source_text, reply["text"])
                self.assertIn("원문 · English", reply["text"])
                self.assertEqual(state["spoken_summary"], spoken[0])

    def test_observation_recorded_reply_speaks_only_the_confirmation(self) -> None:
        for label, after in ((7, 8), (9, 10)):
            with self.subTest(step=label):
                curated = CuratedProtocolSession(self.fixture)
                curated.active = True
                curated.current_index = label - 1
                replies = (
                    ("다음", "네", "완전히 탈색됐어") if label == 7
                    else ("다음", "네", "하얗게 됐어", "네")
                )
                plans = [
                    route_curated_runtime_turn(curated, text, turn_id=turn, language="ko").plan
                    for turn, text in enumerate(replies, 1)
                ]
                advanced = plans[-1]
                self.assertEqual(advanced.action, CuratedProtocolAction.NEXT)
                self.assertTrue(advanced.reported_observation)
                acknowledged = _acknowledge_report_persistence(advanced, "ko")
                self.assertTrue(acknowledged.speech_text.startswith(
                    "말씀한 관찰 결과와 현재 단계 완료를 실험 기록에 반영했습니다. "
                    f"{after}단계로 이동했습니다. 안내를 화면에 표시했습니다."
                ))
                for mark in SCREEN_ONLY:
                    self.assertNotIn(mark, acknowledged.speech_text)
                self.assertIn("원문 · English", acknowledged.display_text)
                self.assertIn(self.reviewed(after), acknowledged.display_text)

    def test_asking_to_hear_steps_2_8_10_reads_the_reviewed_translation(self) -> None:
        for label in (2, 8, 10):
            for transcript in ("이 단계 읽어줘", "전체 내용을 읽어줘", "한국어로 읽어줘"):
                with self.subTest(step=label, transcript=transcript):
                    session = _listener(self.fixture, label - 1, model_roles=True)
                    before = session.curated_protocol_session.state()
                    spoken, reply, state = _turn(session, transcript, 1)
                    # A reviewed translation is read as it is, unannounced.
                    self.assertEqual(spoken, [self.reviewed(label)])
                    self.assertFalse(spoken[0].startswith("자동 번역"))
                    for mark in SCREEN_ONLY:
                        self.assertNotIn(mark, spoken[0])
                    self.assertIn("원문 · English", reply["text"])
                    self.assertIn(
                        self.fixture.steps[label - 1].instruction_source_text, reply["text"]
                    )
                    self.assertEqual(reply["translation_status"], "verified_sidecar")
                    self.assertEqual(state["spoken_summary"], spoken[0])
                    self.assertEqual(state["revision"], before["revision"])


class UntranslatedStepTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = miniprep_fixture()

    def test_without_model_roles_the_source_is_read_as_before(self) -> None:
        session = _listener(self.fixture, 0, model_roles=False)
        spoken, reply, state = _turn(session, "이 단계 읽어줘", 1)
        self.assertEqual(spoken, [MINIPREP_STEPS[0]])
        self.assertEqual(reply["translation_status"], "source_language")
        self.assertEqual(state["spoken_summary"], spoken[0])

    def test_a_checked_automatic_reading_is_spoken_and_labelled(self) -> None:
        model = _FakeModel(GOOD_READING)
        session = _listener(self.fixture, 0, model_roles=True)
        spoken, reply, state = _turn(session, "이 단계 읽어줘", 1, model)
        # Heard as automatic before it is heard at all.
        self.assertEqual(spoken, ["자동 번역입니다. " + GOOD_READING])
        self.assertEqual(reply["translation_status"], "model_assisted_unreviewed")
        first = reply["display_document"]["sections"][0]
        self.assertEqual(first["heading"], "한국어 안내 · 자동 번역(검토 전)")
        # The screen shows the reading itself, labelled by its heading.
        self.assertEqual(first["text"], GOOD_READING)
        # The source stays on screen under it, unchanged.
        self.assertIn(MINIPREP_STEPS[0], reply["text"])
        self.assertEqual(state["spoken_summary"], spoken[0])
        self.assertEqual(len(model.calls), 1)
        request = json.loads(model.calls[0]["messages"][1]["content"])
        self.assertEqual(request, {"step_label": "1", "source_text": MINIPREP_STEPS[0]})
        # Read again: the checked reading is reused, not requested twice.
        spoken_again, _, _ = _turn(session, "다시 읽어줘", 2, model)
        self.assertEqual(spoken_again, ["자동 번역입니다. " + GOOD_READING])
        self.assertEqual(len(model.calls), 1)

    def test_only_an_accepted_unreviewed_reading_is_announced_as_automatic(self) -> None:
        accepted = _listener(self.fixture, 0, model_roles=True)
        spoken, reply, _ = _turn(accepted, "이 단계 읽어줘", 1, _FakeModel(GOOD_READING))
        self.assertTrue(spoken[0].startswith("자동 번역입니다. "))
        self.assertEqual(spoken[0].count("자동 번역입니다."), 1)
        self.assertNotIn("자동 번역입니다.", reply["display_document"]["sections"][0]["text"])
        # A refused reading is not spoken, so nothing is announced as one.
        refused = _listener(self.fixture, 0, model_roles=True)
        spoken, _, _ = _turn(refused, "이 단계 읽어줘", 1, _FakeModel(BAD_READING))
        self.assertNotIn("자동 번역입니다.", spoken[0])
        # Without the model roles the source is read, unannounced.
        off = _listener(self.fixture, 0, model_roles=False)
        spoken, _, _ = _turn(off, "이 단계 읽어줘", 1)
        self.assertNotIn("자동 번역", spoken[0])

    def test_a_reading_that_changes_a_number_is_refused_aloud(self) -> None:
        session = _listener(self.fixture, 0, model_roles=True)
        spoken, reply, state = _turn(session, "이 단계 읽어줘", 1, _FakeModel(BAD_READING))
        self.assertEqual(
            spoken,
            ["한국어 자동 번역을 확인하지 못해 원문을 그대로 읽었습니다. " + MINIPREP_STEPS[0]],
        )
        self.assertNotIn("25 µL에", reply["text"])
        self.assertEqual(reply["translation_status"], "source_language")
        self.assertEqual(
            reply["display_document"]["sections"][0]["text"],
            "한국어 자동 번역을 확인하지 못해 원문을 그대로 읽었습니다.",
        )
        self.assertEqual(session.reader_translations, {})

    def test_a_provider_failure_is_bounded_and_said(self) -> None:
        session = _listener(self.fixture, 0, model_roles=True)
        before = session.curated_protocol_session.state()
        spoken, reply, _ = _turn(
            session, "이 단계 읽어줘", 1, _FakeModel(error=TimeoutError("slow")))
        self.assertTrue(spoken[0].startswith("한국어 자동 번역을 확인하지 못해"))
        self.assertTrue(spoken[0].endswith(MINIPREP_STEPS[0]))
        self.assertEqual(session.curated_protocol_session.state()["revision"], before["revision"])

    def test_logs_carry_no_statement_or_reading(self) -> None:
        session = _listener(self.fixture, 0, model_roles=True)
        with self.assertLogs("voiney_lab", level=logging.INFO) as captured:
            _turn(session, "이 단계 읽어줘", 1, _FakeModel(GOOD_READING))
        lines = [line for line in captured.output if "reader_translation" in line]
        self.assertEqual(len(lines), 1)
        self.assertIn("status=accepted", lines[0])
        self.assertNotIn("Resuspend", lines[0])
        self.assertNotIn("세포", lines[0])

    def test_moving_between_steps_never_calls_the_model(self) -> None:
        session = _listener(self.fixture, 0, model_roles=True)
        spoken, _, _ = _turn(session, "현재 단계 알려줘", 1)
        self.assertEqual(spoken, ["현재 1단계입니다. 안내를 화면에 표시했습니다."])


class ReadingCheckTests(unittest.TestCase):
    SOURCE = "8 Wash the gel piece with 500 µL acetonitrile 800 rpm, 22°C, 00:15:00"
    READING = "8단계: 젤 조각을 acetonitrile 500 µL로 세척하며 조건은 800 rpm, 22°C, 00:15:00입니다."

    def check(self, reading: str) -> str | None:
        return reader_translation_issue(
            self.SOURCE, reading, required_terms=("acetonitrile",), step_label="8")

    def test_a_faithful_reading_passes(self) -> None:
        self.assertIsNone(self.check(self.READING))
        for source, reading in (
            ("Incubate for 15min at 37°C", "15분 동안 37도에서 배양합니다."),
            ("Incubate for 15 min at 37°C", "37°C에서 15분 동안 배양합니다."),
            ("Mix by inverting the tube five times.", "튜브를 다섯 번 뒤집어 섞습니다."),
            ("Do not vortex the sample.", "시료를 볼텍스하지 마세요."),
            ("Wash the cells with 1,000 µL PBS", "PBS 1000 µL로 세포를 씻습니다."),
        ):
            with self.subTest(source=source):
                self.assertIsNone(reader_translation_issue(source, reading))
        # The step label may lead the reading even if the statement lacks it.
        self.assertIsNone(reader_translation_issue(
            "Add 25 mM AMBIC", "3단계: 25 mM AMBIC를 넣습니다.", step_label="3"))

    def test_each_kind_of_drift_is_refused(self) -> None:
        for reading, issue in (
            ("", "empty"),
            ("8 Wash the gel piece with 500 µL acetonitrile", "not_korean"),
            (self.READING.replace("500", "50"), "quantities_changed"),
            (self.READING.replace("00:15:00", "00:15:00, 2회"), "quantities_changed"),
            (self.READING.replace(" rpm", ""), "quantities_changed"),
            (self.READING.replace("µL", "mL"), "quantities_changed"),
            (self.READING.replace("acetonitrile", "아세토니트릴"), "term_missing"),
            (self.READING + " 추가 설명" * 120, "too_long"),
        ):
            with self.subTest(issue=issue, reading=reading[:40]):
                self.assertEqual(self.check(reading), issue)

    def test_a_changed_quantity_cannot_hide_behind_its_units(self) -> None:
        # Each number keeps its own unit, prefixes stay distinct, a Korean
        # unit word counts only right after its number, and counts written
        # as words count too.
        for source, reading in (
            ("Add 25 mM AMBIC", "25 µM AMBIC를 넣습니다."),
            ("Add 25 mM AMBIC", "25 AMBIC를 넣습니다."),
            ("Incubate for 15 min at 37°C", "15°C에서 37분 동안 배양합니다."),
            ("Use 10 µL and 1 mL", "10 mL와 1 µL를 씁니다."),
            ("Spin 1 min", "충분히 1 돌립니다."),
            ("Heat to 60°C", "온도를 60 으로 올립니다."),
            ("Mix by inverting the tube five times.", "튜브를 열 번 뒤집어 섞습니다."),
        ):
            with self.subTest(source=source, reading=reading):
                self.assertEqual(
                    reader_translation_issue(source, reading), "quantities_changed")

    def test_a_negation_may_be_neither_added_nor_dropped(self) -> None:
        for source, reading in (
            ("Add 10 µL lysozyme.", "10 µL lysozyme을 넣지 마세요."),
            ("Do not vortex the sample.", "시료를 볼텍스합니다."),
        ):
            with self.subTest(source=source):
                self.assertEqual(
                    reader_translation_issue(source, reading), "negation_changed")


class ReadRequestVocabularyTests(unittest.TestCase):
    def test_asking_to_hear_the_step_reads_it(self) -> None:
        for transcript in (
            "이 단계 읽어줘", "현재 단계 읽어줘", "단계 내용 읽어 주세요", "내용을 읽어줘",
            "한국어로 읽어줘", "이 단계 한국어로 읽어줘", "다시 읽어줘", "읽어줘",
            "소리 내서 읽어줘", "전체 내용을 읽어줘",
        ):
            with self.subTest(transcript=transcript):
                intent = classify_curated_control_intent(transcript, language="ko")
                self.assertEqual(intent.action, CuratedProtocolAction.FULL_DETAIL)
                self.assertEqual(intent.intent_kind, "full_detail")
                self.assertFalse(intent.allows_state_mutation)

    def test_other_requests_keep_their_meaning(self) -> None:
        for transcript, action in (
            ("현재 단계 알려줘", CuratedProtocolAction.CURRENT),
            ("다시 말해줘", CuratedProtocolAction.REPEAT),
        ):
            with self.subTest(transcript=transcript):
                self.assertIs(
                    classify_curated_control_intent(transcript, language="ko").action, action)
        for transcript in ("원문 그대로 읽어줘", "다음 단계 읽어줘", "논문 읽어줘"):
            with self.subTest(transcript=transcript):
                intent = classify_curated_control_intent(transcript, language="ko")
                self.assertNotEqual(intent.intent_kind, "full_detail")


if __name__ == "__main__":
    unittest.main()
