"""A router answer, as the server checks it and sends it (design §5, D4).

The model answers with one JSON object (spoken, display, source_kind,
evidence_ids, outside_pdf_term). The server uses it only when every check in
answer_checks passes:

* a number with a unit only if the cited source says it (a ``server_state``
  answer: the snapshot; an answer citing nothing may say none);
* a ``pdf`` answer cites at least one fact it was given, and only those;
* no claim that state changed ("넘어갔습니다", "기록했습니다", ...);
* no screen label written into the text (직접 답변, 원문 · English, 출처,
  PDF 밖, ...): the source, citation and marks are the server's to send;
* the protocol's own values (title, step count, current step, revision,
  hashes) exactly as the server has them;
* an outside-PDF explanation (D4) only of a term of the active protocol,
  answering what it means or what it is for, at most 120 characters, with no
  number and nothing on amounts, method, safety or completion;
* and the state did not move while the model was writing.

A failed check drops the answer: the rules answer, and where the rules have
no answer either, "PDF에서 확인할 수 없어요." An outside-PDF answer that
passes is marked by the server -- "PDF 밖 설명이니 유의" on the screen and
"PDF에는 따로 설명이 없어요." said before it. The answer goes to the screen as
separate values (body, source, citation), never as labelled text.
"""

from __future__ import annotations

import asyncio
import re
import unittest
from pathlib import Path

from tests.protocol_vocabulary_support import SOURCE_PDF, miniprep_fixture
from tests.router_fakes import FakeRouterClient, answer_reply
from tests.test_llm_router_wiring import _FakeAsyncOpenAI
from tests.test_voice_pause_resume_persistence import VoiceSessionHarness
from voiney_lab import server as server_module
from voiney_lab.answer_checks import display_label_violations
from voiney_lab.curated_protocol import CuratedProtocolAction, CuratedProtocolSession
from voiney_lab.llm_router import (
    LlmRouterSettings,
    RouterAnswer,
    answer_check_failures,
    route_turn_with_llm_router,
)
from voiney_lab.runtime_routing import route_curated_runtime_turn

ON = LlmRouterSettings(enabled=True, model="fake-router-model", timeout_seconds=1.0)
STATIC = Path(server_module.__file__).parent / "static" / "index.html"


def _session() -> CuratedProtocolSession:
    session = CuratedProtocolSession(miniprep_fixture())
    session.activate_configured()
    session.plan("시작", turn_id=1, language="ko", configuration_id=1, generation=1)
    session.current_index = 3
    return session


def _answer(spoken, *, display="", kind="pdf", ids=("S4.current_step",), term=None) -> RouterAnswer:
    return RouterAnswer(spoken, display, kind, tuple(ids), term)


def _route(session, said, client, *, turn_id=2):
    async def rules():
        return route_curated_runtime_turn(
            session, said, turn_id=turn_id, language="ko", configuration_id=1, generation=1,
        )

    return asyncio.run(route_turn_with_llm_router(
        session, said, turn_id=turn_id, language="ko", settings=ON,
        client_factory=lambda: client, rule_route=rules,
        configuration_id=1, generation=1,
    ))


class CheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = _session().router_context(turn_id=2, language="ko")

    def failures(self, answer, said="질문") -> tuple[str, ...]:
        return answer_check_failures(answer, self.context, utterance=said)

    def test_a_grounded_answer_passes(self) -> None:
        self.assertEqual(self.failures(_answer("50 µL water로 용출합니다.")), ())
        self.assertEqual(self.failures(_answer("지금은 4단계예요.", kind="server_state", ids=())), ())
        self.assertEqual(self.failures(_answer("PDF에서 확인할 수 없어요.", kind="none", ids=())), ())

    def test_evidence_must_be_given_and_cited(self) -> None:
        self.assertIn("evidence_id_unknown", self.failures(_answer("50 µL water로 용출합니다.",
                                                                   ids=("S4.current_step", "S9.x"))))
        self.assertIn("pdf_answer_without_evidence", self.failures(_answer("용출합니다.", ids=())))

    def test_numbers_only_from_the_cited_source(self) -> None:
        # 500 µL is in step 3, which was given but not cited.
        self.assertIn("number_not_in_source", self.failures(_answer("500 µL로 씻습니다.")))
        self.assertEqual(self.failures(_answer("500 µL로 씻습니다.", ids=("S3.current_step",))), ())
        self.assertIn("number_not_in_source",
                      self.failures(_answer("보통 37°C에서 해요.", kind="none", ids=())))
        # Citing nothing, a number the model was shown may be repeated.
        self.assertEqual(self.failures(_answer("Buffer 1은 PDF에 설명이 없어요.", kind="none", ids=())), ())

    def test_every_number_whatever_its_unit(self) -> None:
        # Found by the first real calls: "20분", "37도" carry no Latin unit,
        # so the moved NUMERIC check never read them.
        self.assertEqual(self.failures(_answer("50 µL 물로 용출해요. 4단계예요.")), ())
        for spoken in ("40분 동안 용출해요.", "70도에서 용출해요.", "60 µL로 용출해요."):
            with self.subTest(spoken):
                self.assertIn("number_not_in_source", self.failures(_answer(spoken)))
        self.assertIn("number_not_in_source",
                      self.failures(_answer("지금 12단계예요.", kind="server_state", ids=())))

    def test_no_claim_that_anything_changed(self) -> None:
        for spoken in ("5단계로 넘어갔습니다.", "말씀하신 내용을 기록했습니다.", "타이머를 시작했어요."):
            with self.subTest(spoken):
                self.assertIn("claims_state_change",
                              self.failures(_answer(spoken, kind="none", ids=())))

    def test_no_screen_label_in_the_text(self) -> None:
        for spoken in ("직접 답변 용출합니다.", "용출합니다.\n원문 · English\n4 Elute",
                       "PDF 밖 설명입니다.", "PDF에는 따로 설명이 없어요. 효소입니다."):
            with self.subTest(spoken):
                self.assertIn("display_label", self.failures(_answer(spoken, kind="none", ids=())))

    def test_server_values_as_the_server_has_them(self) -> None:
        self.assertIn("server_value",
                      self.failures(_answer("이 프로토콜은 총 7단계예요.", kind="server_state", ids=())))
        self.assertIn("server_value",
                      self.failures(_answer("현재 2단계입니다.", kind="server_state", ids=())))
        self.assertEqual(
            self.failures(_answer("이 프로토콜은 총 5단계예요.", kind="server_state", ids=())), (),
        )

    def test_an_outside_pdf_explanation_stays_inside_d4(self) -> None:
        fine = _answer("세포벽을 분해하는 효소예요.", kind="outside_pdf", ids=(), term="lysozyme")
        self.assertEqual(self.failures(fine, "lysozyme은 뭐야?"), ())
        self.assertEqual(self.failures(fine, "lysozyme은 왜 넣어?"), ())
        for answer, said, found in (
            (_answer("효소예요.", kind="outside_pdf", ids=(), term="trypsin"), "trypsin은 뭐야?",
             "term_not_in_active_protocol"),
            (fine, "lysozyme은 얼마나 넣어?", "question_asks_quantity"),
            (fine, "lysozyme은 어떻게 다뤄?", "question_asks_method"),
            (fine, "lysozyme은 안전해?", "question_asks_safety"),
            (_answer("세포벽을 분해하는 효소예요. " * 8, kind="outside_pdf", ids=(), term="lysozyme"),
             "lysozyme은 뭐야?", "answer_too_long"),
            (_answer("37도에서 잘 듣는 효소예요.", kind="outside_pdf", ids=(), term="lysozyme"),
             "lysozyme은 뭐야?", "answer_has_number"),
            (_answer("효소라서 조심히 넣으세요.", kind="outside_pdf", ids=(), term="lysozyme"),
             "lysozyme은 뭐야?", "answer_states_method"),
        ):
            with self.subTest(found):
                failures = self.failures(answer, said)
                self.assertTrue(any(item.startswith("outside_pdf:") and found in item
                                    for item in failures), failures)
        self.assertIn(
            "outside_pdf_term_without_outside_pdf",
            self.failures(_answer("50 µL water로 용출합니다.", term="lysozyme")),
        )


class BareNumberTests(unittest.TestCase):
    def test_numbers_are_compared_without_their_units(self) -> None:
        from voiney_lab.answer_checks import introduces_bare_numbers

        source = "Incubate for 15min at 37°C. 800 rpm, 37°C, 00:15:00. Add 1,000 µL."
        labels = [str(item) for item in range(1, 26)]
        for said, new in (
            ("37°C에서 15분 동안 배양합니다.", False),
            ("37도, 800 알피엠", False),
            ("1000 µL 넣어요.", False),
            ("3단계에서 15분", False),
            ("실온에서 20분", True),
            ("37도에서 30분", True),
            ("0.5 mL", True),
            ("40단계", True),
        ):
            with self.subTest(said):
                self.assertIs(introduces_bare_numbers(said, source, step_labels=labels), new)


class RoutedAnswerTests(unittest.TestCase):
    def test_a_pdf_answer_goes_out_as_separate_values(self) -> None:
        session = _session()
        outcome = _route(session, "뭘로 용출해?", FakeRouterClient(answer_reply(
            "50 µL water로 용출합니다.", display="DNA를 50 µL water로 용출합니다.",
            evidence_ids=("S4.current_step",),
        )))
        plan = outcome.plan
        self.assertEqual(outcome.handled_by, "llm")
        self.assertEqual(plan.display_text, "DNA를 50 µL water로 용출합니다.")
        self.assertEqual(plan.speech_text, "50 µL water로 용출합니다.")
        self.assertEqual(display_label_violations(plan.display_text), ())
        self.assertEqual(
            [section["kind"] for section in plan.display_document["sections"]],
            ["section", "source", "citation"],
        )
        self.assertEqual(plan.display_document["sections"][1]["text"], "4 Elute the DNA with 50 µL water.")
        self.assertEqual(plan.display_document["sections"][2]["text"], "S4.current_step · p.1")
        self.assertEqual(plan.answer_origin, "current_protocol")

    def test_an_outside_pdf_answer_is_marked_by_the_server(self) -> None:
        session = _session()
        outcome = _route(session, "lysozyme은 뭐야?", FakeRouterClient(answer_reply(
            "세포벽을 분해하는 효소예요.", source_kind="outside_pdf", outside_pdf_term="lysozyme",
        )))
        plan = outcome.plan
        self.assertEqual(outcome.handled_by, "llm")
        self.assertEqual(plan.speech_text, "PDF에는 따로 설명이 없어요. 세포벽을 분해하는 효소예요.")
        self.assertEqual(plan.display_text, "세포벽을 분해하는 효소예요.")
        self.assertEqual(plan.display_document["sections"][0],
                         {"kind": "notice", "text": "PDF 밖 설명이니 유의"})
        self.assertEqual(plan.answer_origin, "supplemental_model_knowledge")
        self.assertIn("outside_pdf_explanation", plan.limitations)

    def test_a_dropped_answer_says_the_pdf_does_not_tell_when_the_rules_cannot(self) -> None:
        session = _session()
        outcome = _route(session, "오늘 점심 뭐 먹지", FakeRouterClient(answer_reply(
            "김치찌개를 추천해요. 37°C처럼 따뜻하게요.", source_kind="none",
        )))
        self.assertEqual(outcome.fallback_reason, "answer_rejected:number_not_in_source")
        self.assertIs(outcome.rule_route.plan.action, CuratedProtocolAction.OFF_TOPIC)
        self.assertEqual(outcome.plan.speech_text, "PDF에서 확인할 수 없어요.")
        self.assertEqual(outcome.plan.display_text, "PDF에서 확인할 수 없어요.")
        self.assertFalse(outcome.plan.state_changed)

    def test_a_dropped_answer_keeps_a_rules_answer_where_there_is_one(self) -> None:
        session = _session()
        outcome = _route(session, "버퍼 1은 얼마나 넣어?", FakeRouterClient(answer_reply(
            "300 µL 넣어요.", source_kind="none",
        )))
        self.assertEqual(outcome.handled_by, "fallback_rules")
        self.assertIs(outcome.plan, outcome.rule_route.plan)

    def test_an_answer_written_while_the_state_moved_is_dropped(self) -> None:
        session = _session()
        client = FakeRouterClient(answer_reply("50 µL water로 용출합니다.",
                                               evidence_ids=("S4.current_step",)))
        create = client.chat.completions.create

        async def pressed_pause_meanwhile(**kwargs):
            stream = await create(**kwargs)
            session.pause_workflow()
            return stream

        client.chat.completions.create = pressed_pause_meanwhile
        outcome = _route(session, "뭘로 용출해?", client)
        self.assertEqual(outcome.handled_by, "fallback_rules")
        self.assertIn("state_changed_during_answer", outcome.fallback_reason)


class ScreenTests(unittest.TestCase):
    def test_the_screen_draws_the_notice_and_the_new_transition(self) -> None:
        page = STATIC.read_text(encoding="utf-8")
        self.assertIn('sec.kind==="notice"', page)
        self.assertIn("answer-notice", page)
        screen = re.search(r"composing:new Set\(\[([^\]]*)\]\)", page).group(1)
        self.assertIn('"checking_protocol"', screen)
        self.assertIn("checking_protocol", server_module.TURN_PROGRESS_TRANSITIONS["composing"])


@unittest.skipUnless(
    SOURCE_PDF.is_file(),
    f"requires the externally licensed Candidate A source PDF at {SOURCE_PDF}",
)
class RouterAnswerVoiceTests(VoiceSessionHarness, unittest.TestCase):
    def test_the_reply_carries_the_structure_and_the_marks(self) -> None:
        self.environment["VOINEY_LAB_LLM_ROUTER_ENABLED"] = "true"
        self.environment["XAI_API_KEY"] = "test-only-not-a-key"
        _FakeAsyncOpenAI.client = FakeRouterClient(answer_reply(
            "젤 조각을 담는 작은 시험관이에요.", source_kind="outside_pdf",
            outside_pdf_term="gel plug",
        ))

        async def scenario(socket, listener, say):
            await say(1, "프로토콜 시작해줘")
            await say(2, "gel plug이 뭐야?")

        from unittest.mock import patch
        socket, _ = self._session(scenario, patch("voiney_lab.server.AsyncOpenAI", _FakeAsyncOpenAI))
        self.assertEqual(self._errors(socket), [])
        delta = socket.for_turn(2, "reply.delta")[-1]
        self.assertEqual(delta["text"], "젤 조각을 담는 작은 시험관이에요.")
        self.assertEqual(delta["speech_text"], "PDF에는 따로 설명이 없어요. 젤 조각을 담는 작은 시험관이에요.")
        self.assertEqual(delta["display_document"]["sections"][0]["text"], "PDF 밖 설명이니 유의")
        self.assertEqual(delta["answer_origin"], "supplemental_model_knowledge")
        self.assertEqual(display_label_violations(delta["text"]), ())


if __name__ == "__main__":
    unittest.main()
