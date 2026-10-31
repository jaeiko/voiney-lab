"""Lane STT1 (2026-10-31): the correction stage between Chirp 3 and understanding.

The person's test on 2026-10-31 and the LIVE3 probe showed Chirp 3 writing
a short utterance twice ("다음. 다음.", "1번 튜브에 1번 튜브에") and the
stored live rows showed term mishearings beside number and unit errors. The
stage reads a repeat once and hears the active protocol's own terms, and
nothing else: a number or unit never moves, a command word is never
rewritten, the recognizer's words stay in the experiment record, and every
change leaves a code for the metrics.

The fixtures are fictional protocols built from their own text
(``tests/protocol_vocabulary_support.build_fixture``) and the ten public
SIM protocols (biology, chemistry, materials, soil); nothing is in-gel's.
"""

from __future__ import annotations

import asyncio
import unittest
from dataclasses import dataclass

from scripts.sim.corpus import generate_cases, generate_development_cases, load_corpus
from scripts.sim.runner import run_case
from scripts.sim.split import partition_cases
from tests.lane_n_support import VoiceNotesHarness
from tests.protocol_vocabulary_support import build_fixture
from voiney_lab.curated_protocol import ProtocolVocabulary
from voiney_lab.language import Transcription
from voiney_lab.transcript_correction import (
    CODES,
    LEXICON_FORM,
    NUMBERS_GUARD,
    REPEAT_COLLAPSED,
    TERM_LETTERS,
    TERM_SOUND,
    TERM_SPELLING,
    TranscriptCorrection,
    correct_transcript,
    records_as_heard,
)


def digestion_vocabulary() -> ProtocolVocabulary:
    """A fictional digestion protocol whose text names trypsin, PBS and AMBIC."""

    fixture = build_fixture(
        protocol_id="stt1-digestion",
        title="Fictional digestion for correction tests",
        steps=(
            "1 Wash the pellet twice with 500 µL PBS and discard the supernatant.",
            "2 Add 100 µL of 50 mM ammonium bicarbonate (AMBIC) and vortex.",
            "3 Add 10 µL trypsin and incubate at 37 °C for 15 minutes.",
            "4 Destain the protein band with acetonitrile and dry it.",
        ),
        materials=(
            "Trypsin Promega Catalog #V5111",
            "PBS Gibco Catalog #10010",
            "Ammonium bicarbonate Sigma-Aldrich Catalog #09830",
            "Acetonitrile Fisher Catalog #A955",
        ),
        equipment=("Vortex mixer",),
    )
    return ProtocolVocabulary.from_fixture(fixture)


class RepeatTests(unittest.TestCase):
    def test_a_sentence_end_between_two_copies_reads_the_word_once(self) -> None:
        for said, expected in (("다음. 다음.", "다음."), ("네. 네.", "네."), ("다음 다음", "다음")):
            with self.subTest(said):
                correction = correct_transcript(said)
                self.assertEqual(correction.text, expected)
                self.assertEqual(correction.codes, (REPEAT_COLLAPSED,))
                self.assertEqual(correction.spoken, said)

    def test_a_repeated_phrase_is_read_once_and_the_record_keeps_both(self) -> None:
        correction = correct_transcript("침전 생겼어 침전 생겼어 기록해 줘")
        self.assertEqual(correction.text, "침전 생겼어 기록해 줘")
        self.assertEqual(correction.pairs, (("침전 생겼어 침전 생겼어", "침전 생겼어"),))
        self.assertEqual(correction.as_heard("침전 생겼어"), "침전 생겼어 침전 생겼어")

    def test_a_phrase_with_a_number_or_count_word_is_never_collapsed(self) -> None:
        for said in ("1번 튜브에 1번 튜브에", "15 ml 15 ml 넣었어", "하나 하나 확인했어", "두 두 번 씻었어", "37도 37도"):
            with self.subTest(said):
                correction = correct_transcript(said)
                self.assertEqual(correction.text, said)
                self.assertEqual(correction.changes, ())

    def test_a_command_word_repeated_inside_a_sentence_is_the_phrase(self) -> None:
        # "다음 다음 단계" names the step after next; only "다음. 다음." is a repeat.
        for said in ("다음 다음 단계 알려줘", "다시 다시 읽어 줘", "잠깐 잠깐 기다려"):
            with self.subTest(said):
                self.assertEqual(correct_transcript(said).changes, ())

    def test_three_copies_collapse_to_one(self) -> None:
        correction = correct_transcript("네. 네. 네.")
        self.assertEqual(correction.text, "네.")


class TermTests(unittest.TestCase):
    def setUp(self) -> None:
        self.vocabulary = digestion_vocabulary()

    def test_a_korean_sound_of_the_protocols_term_becomes_the_term(self) -> None:
        correction = correct_transcript("트립씬을 넣었어", self.vocabulary)
        self.assertEqual(correction.text, "trypsin을 넣었어")
        self.assertEqual(correction.codes, (TERM_SOUND,))
        self.assertEqual(correction.pairs, (("트립씬을", "trypsin"),))
        self.assertEqual(correction.as_heard("trypsin을 넣었어"), "트립씬을 넣었어")

    def test_letters_said_apart_and_a_one_letter_miss(self) -> None:
        letters = correct_transcript("에이 엠 빅 농도가 뭐야?", self.vocabulary)
        self.assertEqual(letters.text, "AMBIC 농도가 뭐야?")
        self.assertEqual(letters.codes, (TERM_LETTERS,))
        spelling = correct_transcript("trypsun 얼마나 넣어?", self.vocabulary)
        self.assertEqual(spelling.text, "trypsin 얼마나 넣어?")
        self.assertEqual(spelling.codes, (TERM_SPELLING,))

    def test_numbers_and_units_beside_a_term_never_move(self) -> None:
        correction = correct_transcript("트립씬 10 µL 넣고 37도에서 15분", self.vocabulary)
        self.assertEqual(correction.text, "trypsin 10 µL 넣고 37도에서 15분")
        self.assertEqual(
            [token for token in correction.text.split() if any(ch.isdigit() for ch in token)],
            [token for token in correction.spoken.split() if any(ch.isdigit() for ch in token)],
        )

    def test_a_term_the_protocol_does_not_name_is_left_as_heard(self) -> None:
        # Another protocol's reagent is nobody's term here.
        correction = correct_transcript("이오도아세타마이드 넣었어", self.vocabulary)
        self.assertEqual(correction.changes, ())

    def test_command_words_are_never_rewritten(self) -> None:
        for said in ("다음", "타이머 시작", "실험 일시정지", "현재 단계 알려줘", "완료했어", "3단계로 가 줘", "네", "아니"):
            with self.subTest(said):
                self.assertEqual(correct_transcript(said, self.vocabulary).changes, ())

    def test_no_protocol_hears_no_terms(self) -> None:
        self.assertEqual(correct_transcript("트립씬을 넣었어", None).changes, ())

    def test_every_code_is_known(self) -> None:
        self.assertEqual(set(CODES), {REPEAT_COLLAPSED, TERM_SOUND, TERM_SPELLING, TERM_LETTERS, LEXICON_FORM, NUMBERS_GUARD})


@dataclass(frozen=True)
class _Plan:
    anomaly_text: str | None = None
    observation_outcome: str | None = None
    note_record: dict | None = None
    notebook_record: dict | None = None
    record_fix: dict | None = None
    action: str = "record_observation"


class RecordsAsHeardTests(unittest.TestCase):
    def test_the_record_carries_the_words_as_heard_and_the_action_stays(self) -> None:
        correction = correct_transcript("트립씬 넣고 나니 침전 생겼어 침전 생겼어", digestion_vocabulary())
        self.assertEqual(correction.text, "trypsin 넣고 나니 침전 생겼어")
        plan = _Plan(
            observation_outcome="trypsin 넣고 나니 침전 생겼어",
            note_record={"content": "trypsin 넣고 나니 침전 생겼어", "values": []},
            notebook_record={"kind": "observation", "text": "trypsin 넣고 나니 침전 생겼어"},
        )
        restored = records_as_heard(plan, correction)
        self.assertEqual(restored.observation_outcome, "트립씬 넣고 나니 침전 생겼어 침전 생겼어")
        self.assertEqual(restored.note_record["content"], "트립씬 넣고 나니 침전 생겼어 침전 생겼어")
        self.assertEqual(restored.notebook_record["text"], "트립씬 넣고 나니 침전 생겼어 침전 생겼어")
        self.assertEqual(restored.action, "record_observation")
        self.assertEqual(records_as_heard(plan, None), plan)
        unchanged = TranscriptCorrection(spoken="다음", text="다음")
        self.assertIs(records_as_heard(plan, unchanged), plan)


class PublicProtocolTests(unittest.TestCase):
    """The ten public SIM protocols: no development sentence changes its meaning."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.protocols = load_corpus()
        pairs = tuple((p, c) for p in cls.protocols for c in generate_cases(p))
        partition = partition_cases(pairs, seed=1027, holdout_fraction=.3)
        cls.development = list(partition.development) + [
            (p, c) for p in cls.protocols for c in generate_development_cases(p)]

    def test_commands_and_questions_of_every_field_are_left_as_said(self) -> None:
        fields = set()
        for protocol, case in self.development:
            if not case.text.strip():
                continue
            fields.add(protocol.field)
            vocabulary = ProtocolVocabulary.from_fixture(protocol.fixture)
            correction = correct_transcript(case.text, vocabulary)
            if correction.changes:
                # The only development sentence with a repeat: a one-syllable
                # answer said twice at the purpose question ("편. 편.").
                self.assertEqual(correction.codes, (REPEAT_COLLAPSED,), (protocol.code, case.text))
                self.assertEqual(case.family, "safe1_purpose")
        self.assertEqual(fields, {"biology", "chemistry", "materials", "soil"})

    def test_a_repeat_at_the_server_boundary_changes_no_state_and_names_its_code(self) -> None:
        for protocol in self.protocols:
            if protocol.field not in {"chemistry", "materials", "biology"}:
                continue
            case = next(c for c in generate_cases(protocol) if c.family == "timer_remaining")
            heard = f"{case.text} {case.text}"
            result = asyncio.run(run_case(
                protocol, case, transcriber=lambda data, *a, **k: Transcription(heard, "ko")))
            with self.subTest(protocol.code):
                self.assertEqual(result["changed_fields"], [])
                self.assertEqual(result["action"], "timer_status")
                self.assertEqual(result["correction_codes"], [REPEAT_COLLAPSED])
                self.assertEqual(result["corrected_transcript"], case.text)
                self.assertEqual(result["transcript"], heard)


if __name__ == "__main__":
    unittest.main()


class LedgerTests(VoiceNotesHarness, unittest.TestCase):
    """Through run_turn with the report store on: the record keeps the heard words."""

    def test_a_misheard_term_is_understood_and_recorded_as_heard(self) -> None:
        # "Speedvak" is one letter off the fictional wash's equipment; the
        # protocol's prose spells it "speedvac", so that is the term.
        # "넘어가" skips the purpose question the start opens (lane RP3).
        socket = self.turns("프로토콜 시작해줘", "넘어가", "Speedvak 돌렸어 기록해 줘")
        interpreted = socket.for_turn(3, "turn.interpretation")[-1]
        self.assertEqual(interpreted["raw_transcript"], "Speedvak 돌렸어 기록해 줘")
        self.assertEqual(interpreted["corrected_transcript"], "speedvac 돌렸어 기록해 줘")
        self.assertEqual(interpreted["correction_codes"], [TERM_SPELLING])
        self.assertIn(["Speedvak", "speedvac"], interpreted["transcript_corrections"])
        self.assertEqual(interpreted["action"], "record_observation")
        self.assertEqual(socket.for_turn(3, "transcript")[-1]["text"], "Speedvak 돌렸어 기록해 줘")
        observations = [
            event for event in self.report()["events"] if event["event_type"] == "observation"]
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0]["user_wording"], "Speedvak 돌렸어")

    def test_words_that_are_the_record_are_left_as_said(self) -> None:
        # SAFE1: a one-syllable purpose said twice is read back as said and confirmed first.
        socket = self.turns("프로토콜 시작해줘", "편. 편.")
        interpreted = socket.for_turn(2, "turn.interpretation")[-1]
        self.assertEqual(interpreted["correction_codes"], [])
        self.assertEqual(interpreted["corrected_transcript"], "편. 편.")
        self.assertIn("목적을 '편. 편.'으로 기록할까요?", socket.reply(2))
        self.assertNotIn("purpose_recorded", [event["event_type"] for event in self.report()["events"]])

    def test_a_repeated_command_advances_once_as_the_single_word_did(self) -> None:
        socket = self.turns("프로토콜 시작해줘", "넘어가", "다음. 다음.", snapshot=lambda listener: listener.curated_protocol_session.current_index)
        interpreted = socket.for_turn(3, "turn.interpretation")[-1]
        self.assertEqual(interpreted["correction_codes"], [REPEAT_COLLAPSED])
        self.assertEqual(interpreted["corrected_transcript"], "다음.")
        self.assertEqual(self.snapshot, 1)
