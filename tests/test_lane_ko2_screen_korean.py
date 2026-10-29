"""Lane KO2, decisions 3-6 (2026-10-26): the screen Korean first, the caption as said, one step label.

The person's voice test 2 (10/26, the new screen, the uploaded protein PDF):
step 2 showed large in English ("2 Prepare two wash solutions: …"), the
next-step preview read the old 합니다체, the caption showed an English
sentence of the purpose ("You can do this manually or with the help of a
pipetting robot…"), "이 실험의 목적이 뭐야?" was answered with the old
"이것은 … 귀하의 … 환영합니다", and labels showed twice ("3단계: 3단계: …").

- Decision 3: a sentence the screen shows with no checked Korean is
  translated in place by the translation role -- lane SAY's path, used only
  when it passes the check -- and put on the session's fixture; until then
  the server's fields say so (``translation_missing``,
  ``next_step_translation_missing``) and carry the source. The current step,
  the next step, the answer box, the caption and the preview.
- Decision 4: the caption showed the reply's display text (with its "원문 ·
  English" quote) where the speech was Korean. ``reply.complete`` now
  carries what was said (``speech_text``) and the Korean primary text, and the
  new screen's caption shows what was said.
- Decision 5: a step label is shown and said once: a Korean reading's own
  "N단계: " is not put after another, and a source line that begins with its
  own number is not put after "N단계: " with the number again. The source
  quote ("원문 · English") is left as written.
- Decision 6: the purpose is answered from a reading of the current style
  version: a stored purpose of an older version, or none, is read anew in
  place (checked), "we" in the third person.

Provider calls are fake. Three fictional protocols of different fields
(a gel wash, a plasmid miniprep, a soil pH measurement) -- lane rules 10.
"""

from __future__ import annotations

import asyncio
import json
import re
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import tests.test_reader_speech as reader
from tests.lane_n_support import notes_fixture
from tests.protocol_vocabulary_support import build_fixture, miniprep_fixture
from voiney_lab import protocol_translation as pt
from voiney_lab import server as server_module
from voiney_lab.curated_protocol import (
    PURPOSE_FACT_KEY,
    CuratedProtocolSession,
)
from voiney_lab.runtime_routing import route_curated_runtime_turn

STEP_TWO = "2 Wash the pieces with 500 µL of Solution A for 10 min."
STEP_TWO_KO = "2단계: Solution A 500 µL로 조각을 10 min 동안 세척해요."
STEP_THREE_KO = "3단계: Solution A를 빼서 버려요."

SOIL_STEPS = (
    "1. Weigh 20 g of air-dried soil into a 50 mL beaker.",
    "2. Add 20 mL of reagent water and stir the suspension for 5 min.",
    "3. Let the suspension stand for 1 h.",
    "4. Measure the pH of the aqueous phase with a calibrated pH meter.",
)
_DOUBLED = re.compile(r"(?<![0-9A-Za-z])([0-9]+[A-Za-z]?)\s*단계\s*[:：]\s*\1\s*(?:단계|[.)]?\s+[A-Za-z])")


def soil_fixture():
    return build_fixture(
        protocol_id="fictional-soil-ph", title="Fictional soil pH", steps=SOIL_STEPS,
        materials=("reagent water",), equipment=("pH meter",))


def _session(fixture, index: int = 0) -> CuratedProtocolSession:
    session = CuratedProtocolSession(fixture)
    session.activate_configured()
    route_curated_runtime_turn(session, "프로토콜 시작해줘", turn_id=1, language="ko",
                               configuration_id=1, generation=1)
    session._notebook_question = None
    session.current_index = index
    return session


def _clear_spot_readings() -> None:
    server_module._SPOT_READINGS.clear()
    server_module._SPOT_ATTEMPTED.clear()
    getattr(server_module, "_SPOT_REFUSED", {}).clear()


def _shown_texts(plan) -> list[str]:
    """What a screen draws of a reply, the source quote left out."""

    texts = [re.split(r"\n\s*원문\s*·", plan.display_text or "", maxsplit=1)[0],
             plan.primary_text or "", plan.speech_text or ""]
    document = plan.display_document or {}
    for section in document.get("sections") or ():
        if section.get("kind") in {"source", "citation"}:
            continue
        texts.append(section.get("text") or "")
        texts.extend(item for item in section.get("items") or () if isinstance(item, str))
    return texts


class StateFieldTests(unittest.TestCase):
    """Decision 3 and 5: the step card's fields."""

    def test_a_step_with_korean_shows_its_body_without_a_second_label(self) -> None:
        fixture = replace(notes_fixture(), machine_localizations={"step-2/current_step": STEP_TWO_KO})
        state = _session(fixture, 1).state()
        self.assertEqual(state["current_step_label"], "2")
        self.assertEqual(state["primary_summary"], "Solution A 500 µL로 조각을 10 min 동안 세척해요.")
        self.assertIs(state["translation_missing"], False)
        self.assertEqual(state["display_summary"], STEP_TWO)  # the source, as written

    def test_a_step_without_korean_says_so_and_keeps_the_source(self) -> None:
        state = _session(notes_fixture(), 1).state()
        self.assertIsNone(state["primary_summary"])
        self.assertIs(state["translation_missing"], True)
        self.assertEqual(state["display_summary"], STEP_TWO)

    def test_a_reply_only_showing_a_source_sentence_gets_its_korean(self) -> None:
        # "이 단계 왜 해?" shows the step's whole source; the speech says only
        # its first sentence. The source quote below keeps the source.
        fixture = replace(notes_fixture(), machine_localizations={"step-2/current_step": STEP_TWO_KO})
        session = _session(fixture, 1)
        quoted = f"현재 2단계에서 프로토콜이 명시한 내용은 다음과 같습니다: {STEP_TWO[2:]}\n\n원문 · English\n{STEP_TWO}"
        plan = route_curated_runtime_turn(
            session, "지금 단계 뭐야", turn_id=2, language="ko", configuration_id=1, generation=1).plan
        plan = replace(plan, display_text=quoted, primary_text=quoted.split("\n")[0],
                       speech_text="2단계 내용입니다.")
        reading = session.korean_first(plan, language="ko").plan
        self.assertIn("다음과 같습니다: Solution A 500 µL로", reading.primary_text)
        self.assertNotIn("Wash the pieces", reading.primary_text)
        self.assertTrue(reading.display_text.endswith(f"원문 · English\n{STEP_TWO}"))
        self.assertIs(reading.translation_missing, False)
        # With no Korean: marked, and the source stays.
        bare = _session(notes_fixture(), 1)
        reading = bare.korean_first(replace(plan), language="ko").plan
        self.assertIs(reading.translation_missing, True)
        self.assertIn("Wash the pieces", reading.primary_text)

    def test_a_step_written_in_korean_needs_no_translation(self) -> None:
        fixture = build_fixture(
            protocol_id="fictional-korean", title="한국어 프로토콜",
            steps=("1 튜브에 완충액 200 µL를 넣는다.", "2 10분 동안 둔다."))
        state = _session(fixture).state()
        self.assertIs(state["translation_missing"], False)

    def test_the_next_step_line_has_one_label_or_says_it_is_not_translated(self) -> None:
        for fixture, index, korean in (
            (notes_fixture(), 1, STEP_THREE_KO),
            (miniprep_fixture(), 1, "3단계: 500 µL ethanol로 column을 세척하고 microcentrifuge에서 1 minute 동안 돌려요. phosphate-buffered saline (PBS)"),
            (soil_fixture(), 1, "3단계: 현탁액을 1 h 동안 그대로 둬요."),
        ):
            with self.subTest(protocol=fixture.protocol_id):
                step = fixture.steps[index + 1]
                session = _session(fixture, index)
                screen = server_module.curated_screen_fields(session)
                self.assertEqual(screen["next_step_sentence"], " ".join(step.instruction_source_text.split()))
                self.assertIs(screen["next_step_translation_missing"], True)
                self.assertEqual(screen["next_step_translation_status"], "missing")
                session.fixture = replace(fixture, machine_localizations={f"{step.step_id}/current_step": korean})
                screen = server_module.curated_screen_fields(session)
                self.assertEqual(screen["next_step_sentence"], " ".join(korean.split()))
                self.assertIs(screen["next_step_translation_missing"], False)
                self.assertEqual(screen["next_step_translation_status"], "available")
                # A reading stored without its label gets one, once.
                bare = re.sub(r"^\d+단계:\s*", "", korean)
                session.fixture = replace(fixture, machine_localizations={f"{step.step_id}/current_step": bare})
                self.assertEqual(server_module.curated_screen_fields(session)["next_step_sentence"],
                                 f"{step.source_label}단계: {' '.join(bare.split())}")


class ScreenTranslationTests(unittest.TestCase):
    """Decision 3: the screen's sentences translated in place, on lane SAY's path."""

    def setUp(self) -> None:
        _clear_spot_readings()
        self.addCleanup(_clear_spot_readings)

    def run_screen(self, session, model):
        sent: list[dict] = []

        async def send(kind, **fields):
            sent.append({"type": kind, **fields})
            return True

        async def go():
            with patch("voiney_lab.server.AsyncOpenAI", model), patch(
                "voiney_lab.server.require_env", return_value="offline"), patch.dict(
                    "os.environ", {"VOINEY_LAB_TRANSLATION_PROVIDER": "xai"}):
                server_module._queue_screen_korean(session, session.curated_protocol_session, send)
                await asyncio.gather(*list(session.visual_tasks), return_exceptions=True)
        asyncio.run(go())
        return sent

    def test_the_current_and_next_step_are_translated_and_the_card_redrawn(self) -> None:
        model = _Readings({"2 Wash": STEP_TWO_KO, "3 Remove": STEP_THREE_KO})
        session = reader._listener(notes_fixture(), 1, model_roles=True)
        sent = self.run_screen(session, model)
        self.assertEqual(len(model.calls), 2)
        states = [item for item in sent if item["type"] == "protocol.fixture.state"]
        self.assertEqual(len(states), 1)
        self.assertEqual(states[0]["action"], "translation_update")
        state, screen = states[0]["state"], states[0]["screen"]
        self.assertEqual(state["primary_summary"], "Solution A 500 µL로 조각을 10 min 동안 세척해요.")
        self.assertIs(state["translation_missing"], False)
        self.assertEqual(screen["next_step_sentence"], STEP_THREE_KO)
        self.assertIs(screen["next_step_translation_missing"], False)
        # Once in the process: another session on the revision makes no call.
        again = reader._listener(notes_fixture(), 1, model_roles=True)
        sent = self.run_screen(again, _Readings({}))
        self.assertEqual(
            [item["state"]["primary_summary"] for item in sent if item["type"] == "protocol.fixture.state"],
            ["Solution A 500 µL로 조각을 10 min 동안 세척해요."])

    def test_a_reading_that_fails_the_check_is_not_shown(self) -> None:
        model = _Readings({"2 Wash": "2단계: Solution A 50 µL로 조각을 10 min 동안 세척해요.",
                           "3 Remove": STEP_THREE_KO})
        session = reader._listener(notes_fixture(), 1, model_roles=True)
        sent = self.run_screen(session, model)
        state = [item for item in sent if item["type"] == "protocol.fixture.state"][-1]["state"]
        screen = [item for item in sent if item["type"] == "protocol.fixture.state"][-1]["screen"]
        self.assertIsNone(state["primary_summary"])
        self.assertIs(state["translation_missing"], True)
        self.assertEqual(screen["translation_status"], "rejected")

    def test_only_rejected_readings_still_redraw_with_the_rejection_status(self) -> None:
        model = _Readings({
            "2 Wash": "2단계: Solution A 50 µL로 조각을 10 min 동안 세척해요.",
            "3 Remove": "3단계: Solution A 50 µL를 빼서 버려요.",
        })
        session = reader._listener(notes_fixture(), 1, model_roles=True)

        sent = self.run_screen(session, model)

        states = [item for item in sent if item["type"] == "protocol.fixture.state"]
        self.assertEqual(len(states), 1)
        self.assertEqual(states[0]["action"], "translation_update")
        self.assertEqual(states[0]["screen"]["translation_status"], "rejected")
        self.assertEqual(states[0]["screen"]["next_step_translation_status"], "rejected")

    def test_without_the_translation_role_nothing_is_called_or_sent(self) -> None:
        model = _Readings({"2 Wash": STEP_TWO_KO})
        session = reader._listener(notes_fixture(), 1, model_roles=False)
        self.assertEqual(self.run_screen(session, model), [])
        self.assertEqual(model.calls, [])

    def test_a_step_with_korean_is_not_asked_for(self) -> None:
        fixture = replace(notes_fixture(), machine_localizations={
            "step-2/current_step": STEP_TWO_KO, "step-3/current_step": STEP_THREE_KO})
        model = _Readings({})
        session = reader._listener(fixture, 1, model_roles=True)
        self.assertEqual(self.run_screen(session, model), [])
        self.assertEqual(model.calls, [])


class _Readings:
    """A translation model answering by the source sentence's first words."""

    def __init__(self, by_start: dict[str, str]) -> None:
        self.by_start = by_start
        self.calls: list[dict] = []
        from types import SimpleNamespace
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def __call__(self, *args, **kwargs):
        return self

    async def _create(self, **kwargs):
        from types import SimpleNamespace
        self.calls.append(kwargs)
        source = json.loads(kwargs["messages"][1]["content"])["source_text"]
        korean = next((text for start, text in self.by_start.items() if source.startswith(start)), "")
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content=json.dumps({"korean": korean}, ensure_ascii=False)))])


class CaptionTests(unittest.TestCase):
    """Decision 4: the caption is what was said."""

    def setUp(self) -> None:
        _clear_spot_readings()
        self.addCleanup(_clear_spot_readings)

    def turn(self, session, words):
        socket_events: list[dict] = []
        original = reader._Socket.send_text

        async def keep(socket, value):
            socket_events.append(json.loads(value))
            await original(socket, value)
        with patch.object(reader._Socket, "send_text", keep):
            spoken, _reply, _state = reader._turn(session, words, 1)
        return spoken, socket_events

    def test_reply_complete_carries_what_was_said_and_the_korean_primary(self) -> None:
        purpose = "We describe a fictional wash for gel pieces. You can do this by hand or with a robot."
        korean = "이 논문의 저자들은 젤 조각을 세척하는 가상의 방법을 설명해요. 손으로 하거나 로봇으로 할 수 있어요."
        fixture = _with_purpose(notes_fixture(), purpose, korean)
        session = reader._listener(fixture, 0, model_roles=False)
        spoken, events = self.turn(session, "이 실험의 목적이 뭐야?")
        complete = next(item for item in events if item["type"] == "reply.complete")
        delta = next(item for item in events if item["type"] == "reply.delta")
        self.assertEqual(complete["speech_text"], delta["speech_text"])
        self.assertEqual(" ".join(" ".join(spoken).split()), " ".join(complete["speech_text"].split()))
        self.assertEqual(complete["primary_text"], delta["primary_text"])
        # The display keeps the source quote; what is said has no English.
        self.assertIn("You can do this by hand", complete["text"])
        self.assertNotIn("You can do", complete["speech_text"])

    def test_the_new_screen_caption_reads_the_spoken_words(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "web/src/voice/runModel.ts").read_text()
        self.assertIn("speech_text", source)
        self.assertRegex(source, r"spokenText\(m\)")


def _with_purpose(fixture, purpose: str, korean: str | None, *, version: int | None = None):
    """The fictional wash with ``purpose`` as its abstract, and its Korean if any."""

    from tests.lane_n_support import STEPS
    fixture = build_fixture(
        protocol_id=fixture.protocol_id, title=fixture.title, steps=STEPS,
        materials=("Solution A", "Solution B"), equipment=("Speedvac",), abstract=purpose)
    if korean is None:
        return fixture
    versions = {PURPOSE_FACT_KEY: version} if version is not None else None
    return replace(fixture, machine_localizations={PURPOSE_FACT_KEY: korean},
                   translation_versions=versions)


class OneLabelTests(unittest.TestCase):
    """Decision 5: "N단계: N단계" and "N단계: N <source>" nowhere outside the source quote."""

    REQUESTS = ("다음 단계 뭐야", "이 단계 읽어줘", "자세히 알려줘", "다시 말해줘",
                "3단계 미리보기", "1단계부터 3단계까지 알려줘", "다음", "지금 단계 뭐야")

    def check(self, fixture) -> None:
        for index in range(len(fixture.steps) - 1):
            for words in self.REQUESTS:
                with self.subTest(protocol=fixture.protocol_id, step=index + 1, words=words):
                    session = _session(fixture, index)
                    plan = route_curated_runtime_turn(
                        session, words, turn_id=2, language="ko", configuration_id=1, generation=1).plan
                    plan = session.single_step_labels(session.korean_first(plan, language="ko").plan)
                    for text in _shown_texts(plan):
                        self.assertIsNone(_DOUBLED.search(text), text)

    def test_with_korean_readings_that_carry_their_label(self) -> None:
        for fixture in (notes_fixture(), miniprep_fixture(), soil_fixture()):
            korean = {
                f"{step.step_id}/current_step": f"{step.source_label}단계: 한국어 읽기 "
                + " ".join(re.findall(r"\d+(?:\.\d+)?\s*(?:µL|mL|min|g|h)\b", step.instruction_source_text))
                for step in fixture.steps
            }
            self.check(replace(fixture, machine_localizations=korean))

    def test_with_no_korean_the_source_line_is_not_put_after_its_own_label(self) -> None:
        for fixture in (notes_fixture(), miniprep_fixture(), soil_fixture()):
            self.check(fixture)

    def test_the_source_quote_is_left_as_written(self) -> None:
        session = _session(notes_fixture(), 1)
        plan = route_curated_runtime_turn(
            session, "이 단계 읽어줘", turn_id=2, language="ko", configuration_id=1, generation=1).plan
        plan = session.single_step_labels(plan)
        self.assertIn(f"원문 · English\n{STEP_TWO}", plan.display_text)

    def test_a_label_not_doubled_is_left_alone(self) -> None:
        session = _session(soil_fixture(), 0)
        plan = route_curated_runtime_turn(
            session, "지금 단계 뭐야", turn_id=2, language="ko", configuration_id=1, generation=1).plan
        text = "1단계: 20 g 흙을 달아요. 2단계: 2 mL를 더 넣지 않아요."
        fixed = session.single_step_labels(replace(plan, display_text=text, speech_text=text, primary_text=text))
        # "2단계: 2 mL" is a quantity after the label, not the step's own number.
        self.assertEqual(fixed.speech_text, text)


class PurposeTests(unittest.TestCase):
    """Decision 6: the purpose in the current style, "we" in the third person."""

    PURPOSE = "We describe a fictional wash for gel pieces. You can do this by hand or with a robot."
    NEW = "이 논문의 저자들은 젤 조각을 세척하는 가상의 방법을 설명해요. 손으로 하거나 로봇으로 할 수 있어요."
    OLD = "우리는 젤 조각을 세척하는 가상의 방법을 설명합니다. 손으로 하거나 로봇으로 할 수 있습니다."
    OLD_PASSING = "이것은 젤 조각을 세척하는 가상의 방법이며, 귀하는 손으로 하거나 로봇으로 할 수 있습니다."

    def setUp(self) -> None:
        _clear_spot_readings()
        self.addCleanup(_clear_spot_readings)

    def ask(self, fixture, model, *, roles=True):
        session = reader._listener(fixture, 0, model_roles=roles)
        spoken, reply, _state = reader._turn(session, "이 실험의 목적이 뭐야?", 1, model)
        return session, spoken[0], reply

    def test_an_older_version_purpose_is_read_anew_in_the_turn(self) -> None:
        fixture = _with_purpose(notes_fixture(), self.PURPOSE, self.OLD_PASSING, version=0)
        model = reader._FakeModel(self.NEW)
        session, spoken, reply = self.ask(fixture, model)
        self.assertEqual(len(model.calls), 1)
        self.assertIn("이 논문의 저자들은", spoken)
        self.assertNotIn("귀하", spoken + reply["text"])
        self.assertEqual(
            session.curated_protocol_session.fixture.machine_localizations[PURPOSE_FACT_KEY], self.NEW)
        # Asked again: no second call.
        reader._turn(session, "이 실험의 목적이 뭐야?", 2, model)
        self.assertEqual(len(model.calls), 1)

    def test_a_current_version_purpose_makes_no_call(self) -> None:
        fixture = _with_purpose(notes_fixture(), self.PURPOSE, self.NEW, version=pt.STYLE_VERSION)
        model = reader._FakeModel(error=AssertionError("no call"))
        _session_, spoken, _reply = self.ask(fixture, model)
        self.assertIn("이 논문의 저자들은", spoken)

    def test_a_purpose_with_no_korean_is_read_in_the_turn(self) -> None:
        # The stored purpose was refused (PMC8250384's "우리는 이전에 …"): none shown.
        fixture = _with_purpose(notes_fixture(), self.PURPOSE, None)
        model = reader._FakeModel(self.NEW)
        _session_, spoken, _reply = self.ask(fixture, model)
        self.assertEqual(len(model.calls), 1)
        self.assertIn("이 논문의 저자들은", spoken)

    def test_a_reading_that_speaks_as_we_is_never_said(self) -> None:
        fixture = _with_purpose(notes_fixture(), self.PURPOSE, None)
        _session_, spoken, reply = self.ask(fixture, reader._FakeModel(self.OLD), roles=True)
        self.assertNotIn("우리", spoken + reply["text"])
        self.assertIn("원문에 적힌 실험 목적", spoken)

    def test_without_the_role_an_older_passing_purpose_is_still_korean(self) -> None:
        fixture = _with_purpose(notes_fixture(), self.PURPOSE, self.OLD_PASSING, version=0)
        _session_, spoken, _reply = self.ask(fixture, None, roles=False)
        self.assertIn("가상의 방법", spoken)


if __name__ == "__main__":
    unittest.main()
