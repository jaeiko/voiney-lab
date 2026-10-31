"""QA1: unanswered prospective bench questions use the grounded read-only path.

The public SIM corpus is the production-shaped ten-protocol boundary.  These
tests use only the development side's wording; the outcome-blind holdout is
scored by ``scripts.sim.run`` and is not inspected here.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scripts.sim.corpus import load_corpus
from scripts.sim.runner import grounded_answer_measurement, snapshot
from tests.lane_help_support import gel_wash_fixture, session_at
from voiney_lab import server
from voiney_lab.cascade_filler import (
    CascadeFiller,
    FillerSessionMemory,
    GROUNDED_ANSWER_STATUS_PHRASES,
)
from voiney_lab.curated_protocol import CuratedProtocolAction, CuratedProtocolSession
from voiney_lab.intent_arbitration import RequestIntent, arbitrate_request
from voiney_lab.knowledge import KnowledgeHit, compose_grounded_problem_answer
from voiney_lab.multi_brain import MultiBrainSettings
from voiney_lab.runtime_routing import route_curated_runtime_turn
from voiney_lab.safety_pack import SafetyDocumentRef, StepSafetyGuidance


DEVELOPMENT_QUESTIONS = (
    "문제가 생기면 다음으로 가도 돼?",
    "시료가 이상하면 어떻게 하지?",
)

GENERALIZED_WHAT_IF_QUESTIONS = (
    "만약 실험 결과가 실패하면 어떻게 해야 해?",
    "반응이 예상과 다를 경우에는 어떻게 하지?",
    "문제가 발생할 경우 무엇을 먼저 확인해야 해?",
    "이 단계에서 오류가 난다고 가정하면?",
    "결과가 이상할 수 있는 이유가 뭐야?",
)


class GroundedQuestionRoutingTests(unittest.TestCase):
    def test_prospective_problem_is_troubleshooting_not_general_qa(self) -> None:
        for words in (*DEVELOPMENT_QUESTIONS, *GENERALIZED_WHAT_IF_QUESTIONS):
            with self.subTest(words=words):
                decision = arbitrate_request(words)
                self.assertIs(decision.intent, RequestIntent.TROUBLESHOOTING)
                self.assertEqual(decision.reason_code, "problem_contingency_question")
                self.assertFalse(decision.state_mutation)

    def test_nearby_questions_keep_their_existing_routes(self) -> None:
        for words in (
            "AMBIC가 무엇인지 설명해 줄 수 있어?",
            "다음에 뭐 하면 돼?",
            "쏟으면 어떡해?",
            "튜브가 무엇인지 설명해 줄 수 있어?",
            "끝났다고 하면 어떻게 돼?",
            "거품이 왜 생기지?",
        ):
            with self.subTest(words=words):
                self.assertNotEqual(
                    arbitrate_request(words).reason_code,
                    "problem_contingency_question",
                )

    def test_all_ten_public_protocols_use_the_read_only_problem_help_path(self) -> None:
        protocols = load_corpus()
        self.assertEqual(len(protocols), 10)
        self.assertIn("chemistry", {item.field for item in protocols})
        self.assertIn("materials", {item.field for item in protocols})

        for protocol in protocols:
            for words in DEVELOPMENT_QUESTIONS:
                with self.subTest(protocol=protocol.code, words=words):
                    workflow = CuratedProtocolSession(protocol.fixture)
                    workflow.active = True
                    workflow._workflow_status = "running"
                    before = snapshot(workflow)

                    outcome = route_curated_runtime_turn(
                        workflow,
                        words,
                        turn_id=1,
                        language="ko",
                        configuration_id=1,
                        generation=1,
                    )
                    self.assertIs(outcome.plan.action, CuratedProtocolAction.PROBLEM_HELP)
                    self.assertEqual(outcome.plan.intent_kind, "grounded_problem_question")
                    self.assertFalse(outcome.plan.state_changed)
                    self.assertIsNotNone(outcome.plan.problem_help)
                    self.assertEqual(outcome.plan.problem_help["kind"], "grounded_question")
                    self.assertEqual(snapshot(workflow), before)
                    shown = outcome.plan.display_text
                    self.assertTrue(
                        ("원문 · p." in shown and "단계" in shown)
                        or ("원문에 없음" in shown and "p." in shown and "단계" in shown),
                        shown,
                    )


class _SelectorClient:
    model = "fake-answer-role"

    def __init__(self, *, safety: bool = False, failure: Exception | None = None) -> None:
        self.safety = safety
        self.failure = failure
        self.requests: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, **request):
        self.requests.append(request)
        if self.failure is not None:
            raise self.failure
        context = json.loads(request["messages"][2]["content"])
        facts = context["facts"]
        selected = next(
            (fact for fact in facts if fact["kind"] == "safety_document"), facts[0]
        ) if self.safety else facts[0]
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=json.dumps({"fact_id": selected["fact_id"]})),
        )])


def _listener(workflow: CuratedProtocolSession) -> server.ListenerSession:
    return server.ListenerSession(
        curated_protocol_session=workflow,
        multi_brain_settings=MultiBrainSettings(
            True, "fake-answer-role", answer_timeout_seconds=2,
            answer_brain_enabled=True, answer_brain_model="fake-answer-role",
            source_brain_enabled=False, visual_brain_enabled=False,
        ),
    )


class GroundedAnswerServerBoundaryTests(unittest.TestCase):
    def _compose(self, words: str, *, workflow=None, client=None):
        workflow = workflow or session_at("3", gel_wash_fixture())
        plan = route_curated_runtime_turn(
            workflow, words, turn_id=2, language="ko", configuration_id=1,
            generation=1,
        ).plan
        listener = _listener(workflow)
        client = client or _SelectorClient()
        with patch("voiney_lab.server._role_client", return_value=client):
            answer, causes = asyncio.run(server._with_problem_help(
                listener, workflow, plan, language="ko", timings={},
                turn_id=2, generation=1,
            ))
        return answer, causes, client

    def test_answer_role_selects_one_exact_source_fact_and_never_mutates(self) -> None:
        workflow = session_at("3", gel_wash_fixture())
        before = snapshot(workflow)
        answer, causes, client = self._compose(
            "온도가 이상하면 어떻게 하지?", workflow=workflow,
        )

        self.assertIsNone(causes)
        self.assertEqual(len(client.requests), 1)
        self.assertEqual(answer.problem_help["answer_selector_status"], "selected")
        self.assertEqual(len(answer.source_texts), 1)
        self.assertIn(answer.source_texts[0], answer.display_text)
        self.assertNotIn("계속 진행해도", answer.display_text)
        self.assertEqual(snapshot(workflow), before)

    def test_provider_failure_is_visible_and_keeps_exact_deterministic_source(self) -> None:
        answer, causes, _client = self._compose(
            "온도가 이상하면 어떻게 하지?",
            client=_SelectorClient(failure=RuntimeError("provider down")),
        )

        self.assertIsNone(causes)
        self.assertEqual(
            answer.problem_help["answer_selector_status"], "provider_RuntimeError",
        )
        self.assertIn("원문 선택을 완료하지 못해", answer.limitations[0])
        self.assertTrue(answer.source_texts)
        for text in answer.source_texts:
            self.assertIn(text, answer.display_text)

    def test_only_a_linked_non_demo_safety_excerpt_can_be_selected(self) -> None:
        workflow = session_at("2", gel_wash_fixture())
        document = SafetyDocumentRef(
            "sop-temperature", "facility_sop", "온도 이상 대응", "1", "ko",
            "MAIN", "general", "4.2", 7, None,
            "온도가 이상하면 장비를 멈춥니다.", False,
        )
        guidance = StepSafetyGuidance(
            "step-2", "2", (), (document,), (), (), "안전 SOP p.7",
        )
        workflow.set_safety_pack(SimpleNamespace(
            guidance_for_step=lambda _step, _index: guidance,
        ))
        answer, causes, _client = self._compose(
            "온도가 이상하면 어떻게 하지?", workflow=workflow,
            client=_SelectorClient(safety=True),
        )

        self.assertIsNone(causes)
        self.assertEqual(answer.source_texts, (document.summary_text,))
        self.assertIn("연구실 자료 · 안전 SOP p.7", answer.display_text)
        self.assertEqual(answer.citations[0]["kind"], "lab")

    def test_rejected_grounded_frame_stays_on_the_grounded_absence_contract(self) -> None:
        with patch(
            "voiney_lab.server.problem_help_violations",
            return_value=("permission",),
        ):
            answer, causes, _client = self._compose("온도가 이상하면 어떻게 하지?")

        self.assertIsNone(causes)
        self.assertIn("원문에 없음 · 현재", answer.display_text)
        self.assertIn("현재 프로토콜 검토본", answer.display_text)
        self.assertNotIn("지난 기록", answer.display_text)
        self.assertEqual(answer.source_texts, ())
        self.assertEqual(answer.problem_help["found"], {"protocol": 0, "lab": 0})


class GroundedAnswerCompositionTests(unittest.TestCase):
    def test_display_sources_and_counts_share_the_same_three_hit_limit(self) -> None:
        hits = tuple(
            KnowledgeHit(
                "protocol", f"{index}단계 노트", f"source line {index}",
                f"p.1 · {index}단계",
                details=(("kind", "note"), ("page", "1"),
                         ("step_label", str(index))),
            )
            for index in range(1, 5)
        )

        answer = compose_grounded_problem_answer(
            hits, language="ko", step_label="1", step_page=1,
        )

        self.assertEqual(answer.found, {"protocol": 3, "lab": 0})
        self.assertEqual(len(answer.sources), 3)
        self.assertIn("source line 3", answer.shown)
        self.assertNotIn("source line 4", answer.shown)


class GroundedAnswerLatencyCueTests(unittest.IsolatedAsyncioTestCase):
    async def test_pending_exact_source_answer_says_the_fixed_cue_at_three_seconds(self) -> None:
        now = 0.0
        audio: list[tuple[float, bytes]] = []
        events: list[tuple[float, str, dict]] = []

        async def sleep(seconds: float) -> None:
            nonlocal now
            now += seconds

        async def synthesize(text: str, _language: str) -> bytes:
            self.assertEqual(text, GROUNDED_ANSWER_STATUS_PHRASES["ko"])
            return b"status"

        filler = CascadeFiller(
            turn_id=1, generation=1, language="ko", delay_ms=100,
            synthesize=synthesize,
            send_audio=lambda _turn, _generation, pcm: _append_audio(audio, now, pcm),
            send_event=lambda kind, **fields: _append_event(events, now, kind, fields),
            send_clear=lambda *_args: _noop(),
            is_current=lambda *_args: True,
            clock=lambda: now,
            sleep=sleep,
            mode="tone",
            send_tone=lambda *_args: _noop(),
            status_delay_ms=3000,
            activity=lambda: "grounding_answer",
            memory=FillerSessionMemory(
                last_status_phrase=GROUNDED_ANSWER_STATUS_PHRASES["ko"],
            ),
        )
        filler.start()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        self.assertEqual(audio, [(3.0, b"status")])
        status = [item for item in events if item[2].get("cue") == "status"]
        self.assertEqual(status[0][0], 3.0)
        self.assertEqual(status[0][2]["activity"], "grounding_answer")


class GroundedAnswerMeasurementTests(unittest.TestCase):
    def test_exact_source_or_explicit_absence_is_grounded(self) -> None:
        source = "Add 5 mL buffer. Wear gloves."
        exact = grounded_answer_measurement(
            '원문 · p.1\n“Add 5 mL buffer.”', ("Add 5 mL buffer.",), source,
        )
        absent = grounded_answer_measurement(
            "원문에 없음 · 현재 1단계 · p.1", (), source,
        )
        self.assertEqual(exact, {
            "grounded": True, "invented_values": False,
            "generated_safety": False,
        })
        self.assertTrue(absent["grounded"])

    def test_new_value_and_ungrounded_safety_are_counted(self) -> None:
        measured = grounded_answer_measurement(
            "Add 10 mL buffer. 보안경을 착용하세요.",
            ("Add 5 mL buffer.",), "Add 5 mL buffer.",
        )
        self.assertFalse(measured["grounded"])
        self.assertTrue(measured["invented_values"])
        self.assertTrue(measured["generated_safety"])


async def _append_audio(target, now, pcm):
    target.append((now, pcm))


async def _append_event(target, now, kind, fields):
    target.append((now, kind, fields))


async def _noop():
    return None


if __name__ == "__main__":
    unittest.main()
