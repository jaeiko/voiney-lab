import asyncio
import json
import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from voice_workflow_agent.multi_brain import (
    AnswerBrainOutput,
    BrainFact,
    BrainClaim,
    BrainSnapshot,
    HybridMultiBrain,
    MultiBrainSettings,
    SourceBrainOutput,
    VisualBrainOutput,
    activation_for,
)
from voice_workflow_agent.curated_protocol import (
    ClaimAdmissionStatus,
    ClaimRequest,
    ClaimTargetType,
)
from voice_workflow_agent.server import _claim_admitted_answer


def snapshot(*, intent="related_question", visual=False):
    return BrainSnapshot(
        configuration_id=7,
        session_id="session-1",
        turn_id=3,
        generation_id=4,
        workflow_revision=5,
        protocol_id="candidate-a",
        document_sha256="a" * 64,
        step_id="step-2",
        step_index=1,
        language="ko",
        transcript=("HPLC water와 AMBIC 그림도 보여줘" if visual else "HPLC water와 AMBIC가 뭐야?"),
        intent_kind=intent,
        question_kind="definition",
        requested_entities=("hplc_water", "ambic"),
        question_dimensions=("definition", "relationship"),
        facts=(
            BrainFact("fact-water", "composition", "HPLC water is used to prepare 25 mM AMBIC.", 4),
            BrainFact("fact-ambic", "composition", "Solution A contains 25 mM AMBIC.", 4),
        ),
    )


def response(payload):
    return SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content=json.dumps(payload)),
    )])


class BarrierCompletions:
    def __init__(self):
        self.entered = 0
        self.maximum_active = 0
        self.active = 0
        self.release = asyncio.Event()
        self.names = []

    async def create(self, **kwargs):
        name = kwargs["response_format"]["json_schema"]["name"]
        self.names.append(name)
        self.entered += 1
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        if self.entered == 3:
            self.release.set()
        await asyncio.wait_for(self.release.wait(), timeout=1)
        self.active -= 1
        if "answer" in name:
            return response({
                "spoken_answer": "HPLC water와 AMBIC는 현재 용액 준비에 쓰입니다.",
                "display_answer": "HPLC water는 용매이고 AMBIC는 Solution A의 성분입니다.",
                "evidence_ids": ["fact-water", "fact-ambic"],
                "limitations": ["프로토콜 밖의 대체 조건은 승인하지 않습니다."],
            })
        if "source" in name:
            return response({
                "entities": ["hplc_water", "ambic"],
                "dimensions": ["definition", "relationship"],
                "scopes": ["ACTIVE_PROTOCOL", "AUTHORITATIVE_EXTERNAL_REFERENCE"],
                "query": "HPLC water AMBIC definition relationship",
                "needs_research": True,
            })
        return response({
            "helps": True,
            "entity": "hplc_water",
            "preferred_class": "authoritative_external_reference",
            "reason_code": "explicit_request",
        })


class MultiBrainTests(unittest.IsolatedAsyncioTestCase):
    def test_activation_is_conditional_and_commands_never_fan_out(self):
        self.assertEqual(activation_for(
            intent_kind="completion_and_next", visual_requested=False,
            unresolved_dimensions=(),
        ).roles, ())
        self.assertEqual(activation_for(
            intent_kind="protocol_entity_question", visual_requested=False,
            unresolved_dimensions=(),
        ).roles, ("answer",))
        self.assertEqual(activation_for(
            intent_kind="related_question", visual_requested=False,
            unresolved_dimensions=("definition",),
        ).roles, ("answer", "source"))
        self.assertEqual(activation_for(
            intent_kind="visual_request", visual_requested=True,
            unresolved_dimensions=(),
        ).roles, ("answer", "visual"))
        self.assertEqual(activation_for(
            intent_kind="related_question", visual_requested=True,
            unresolved_dimensions=("relationship",),
        ).roles, ("answer", "source", "visual"))

    async def test_three_roles_overlap_and_return_only_typed_read_only_outputs(self):
        completions = BarrierCompletions()
        client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        settings = MultiBrainSettings(True, "fake-model", 2, 2, .2)
        activation = activation_for(
            intent_kind="related_question", visual_requested=True,
            unresolved_dimensions=("definition",),
        )
        run = HybridMultiBrain(client, settings).start(snapshot(visual=True), activation)
        answer, source, visual = await asyncio.gather(
            run.terminal("answer"), run.terminal("source"), run.terminal("visual"),
        )
        self.assertGreaterEqual(completions.maximum_active, 3)
        self.assertEqual(len(set(completions.names)), 3)
        self.assertIsInstance(answer.output, AnswerBrainOutput)
        self.assertIsInstance(source.output, SourceBrainOutput)
        self.assertIsInstance(visual.output, VisualBrainOutput)
        self.assertNotIn("mutate", answer.output.__dict__)
        self.assertNotIn("admit", source.output.__dict__)
        self.assertNotIn("speak", visual.output.__dict__)

    async def test_primary_budget_does_not_cancel_the_bounded_provider_task(self):
        started = asyncio.Event()
        release = asyncio.Event()

        class SlowCompletions:
            async def create(self, **_kwargs):
                started.set()
                await release.wait()
                return response({
                    "spoken_answer": "활성 프로토콜 근거를 설명합니다.",
                    "display_answer": "활성 프로토콜 근거의 읽기 전용 설명입니다.",
                    "evidence_ids": ["fact-water"],
                    "limitations": [],
                })

        client = SimpleNamespace(chat=SimpleNamespace(completions=SlowCompletions()))
        run = HybridMultiBrain(
            client, MultiBrainSettings(True, "fake", 2, 2, .01),
        ).start(snapshot(), activation_for(
            intent_kind="protocol_entity_question", visual_requested=False,
            unresolved_dimensions=(),
        ))
        await started.wait()
        self.assertIsNone(await run.terminal("answer", timeout=.01))
        self.assertFalse(run.tasks["answer"].cancelled())
        release.set()
        terminal = await run.terminal("answer")
        self.assertEqual(terminal.status, "success")

    async def test_public_deadline_does_not_wait_for_transport_cancellation_cleanup(self):
        request_options = {}

        class CancellationSlowCompletions:
            async def create(self, **kwargs):
                request_options.update(kwargs)
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    # Model a provider transport that is slow to unwind after
                    # cancellation. The public Brain terminal must not wait.
                    await asyncio.sleep(1)

        client = SimpleNamespace(
            chat=SimpleNamespace(completions=CancellationSlowCompletions())
        )
        run = HybridMultiBrain(
            client, MultiBrainSettings(True, "fake", .05, .05, .01),
        ).start(snapshot(), activation_for(
            intent_kind="protocol_entity_question", visual_requested=False,
            unresolved_dimensions=(),
        ))
        started = time.perf_counter()
        terminal = await run.terminal("answer")
        self.assertEqual(terminal.status, "timeout")
        self.assertLess(time.perf_counter() - started, .25)
        self.assertEqual(request_options["timeout"], .05)

    async def test_answer_gate_rejects_new_operational_numbers_and_persistence_claims(self):
        payloads = [
            {
                "spoken_answer": "99 µL를 사용하세요.",
                "display_answer": "99 µL를 사용하세요.",
                "evidence_ids": ["fact-water"], "limitations": [],
            },
            {
                "spoken_answer": "제가 완료를 기록했습니다.",
                "display_answer": "제가 완료를 기록했습니다.",
                "evidence_ids": ["fact-water"], "limitations": [],
            },
        ]
        for payload in payloads:
            with self.subTest(payload=payload["spoken_answer"]):
                completions = SimpleNamespace(create=lambda **_kwargs: None)

                async def create(**_kwargs):
                    return response(payload)

                completions.create = create
                client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
                run = HybridMultiBrain(
                    client, MultiBrainSettings(True, "fake", 1, 1, .1),
                ).start(snapshot(), activation_for(
                    intent_kind="protocol_entity_question", visual_requested=False,
                    unresolved_dimensions=(),
                ))
                terminal = await run.terminal("answer")
                self.assertEqual(terminal.status, "rejected")
                self.assertIsNone(terminal.output)

    async def test_cancel_stops_every_inflight_role(self):
        entered = asyncio.Event()

        class NeverCompletions:
            async def create(self, **_kwargs):
                entered.set()
                await asyncio.Event().wait()

        client = SimpleNamespace(chat=SimpleNamespace(completions=NeverCompletions()))
        run = HybridMultiBrain(
            client, MultiBrainSettings(True, "fake", 2, 2, .1),
        ).start(snapshot(visual=True), activation_for(
            intent_kind="related_question", visual_requested=True,
            unresolved_dimensions=("definition",),
        ))
        await entered.wait()
        run.cancel()
        await asyncio.gather(*run.tasks.values(), return_exceptions=True)
        self.assertTrue(all(task.cancelled() for task in run.tasks.values()))

    async def test_claim_sections_admit_supported_part_without_open_rationale(self):
        base=snapshot()
        supported=BrainClaim(
            "claim-supported","entity","hplc_water","definition",
            "ACTIVE_PROTOCOL",("fact-water",),"local_supported",
        )
        unresolved=BrainClaim(
            "claim-open","comparison","hplc_water-vs-ordinary","difference",
            "AUTHORITATIVE_EXTERNAL_REFERENCE",("fact-water",),
            "research_required","comparison_absent_from_active_protocol",
        )
        with_claims=BrainSnapshot(**{
            **base.__dict__,"claims":(supported,unresolved),
        })

        async def create(**_kwargs):
            return response({
                "spoken_answer":"HPLC water는 이 단계의 AMBIC 용액에 쓰입니다.",
                "display_answer":"HPLC water는 이 단계의 AMBIC 용액에 쓰입니다.",
                "evidence_ids":["fact-water"],"limitations":[],
                "claim_sections":[{
                    "claim_id":"claim-supported",
                    "text":"HPLC water는 이 단계의 AMBIC 용액에 쓰입니다.",
                    "evidence_ids":["fact-water"],
                }],
            })

        client=SimpleNamespace(chat=SimpleNamespace(
            completions=SimpleNamespace(create=create)))
        terminal=await HybridMultiBrain(
            client,MultiBrainSettings(True,"fake",1,1,.1),
        ).start(with_claims,activation_for(
            intent_kind="related_question",visual_requested=False,
            unresolved_dimensions=("difference",),
        )).terminal("answer")
        self.assertEqual(terminal.status,"success")
        plan=SimpleNamespace(
            unresolved_claim_ids=("claim-open",),
            claim_requests=(
                ClaimRequest(
                    "claim-supported",ClaimTargetType.ENTITY,"hplc_water",
                    "definition","ACTIVE_PROTOCOL",("fact-water",),
                    ClaimAdmissionStatus.LOCAL_SUPPORTED,
                    "HPLC water는 AMBIC 용액에 쓰입니다.",
                ),
                ClaimRequest(
                    "claim-open",ClaimTargetType.COMPARISON,
                    "hplc_water-vs-ordinary","difference",
                    "AUTHORITATIVE_EXTERNAL_REFERENCE",("fact-water",),
                    ClaimAdmissionStatus.RESEARCH_REQUIRED,
                    "일반 물과의 차이는 별도 권위 근거가 필요합니다.",
                ),
            ),
        )
        admitted=_claim_admitted_answer(terminal.output,plan)
        self.assertIsNotNone(admitted)
        self.assertIn("AMBIC",admitted.display_answer)
        self.assertIn("별도 권위 근거",admitted.display_answer)

    async def test_unresolved_claim_cannot_be_smuggled_into_answer_section(self):
        base=snapshot()
        unresolved=BrainClaim(
            "claim-open","comparison","water-comparison","difference",
            "AUTHORITATIVE_EXTERNAL_REFERENCE",("fact-water",),
            "research_required","not_in_protocol",
        )
        with_claims=BrainSnapshot(**{**base.__dict__,"claims":(unresolved,)})

        async def create(**_kwargs):
            return response({
                "spoken_answer":"Unsupported comparison.",
                "display_answer":"Unsupported comparison.",
                "evidence_ids":["fact-water"],"limitations":[],
                "claim_sections":[{
                    "claim_id":"claim-open","text":"Unsupported comparison.",
                    "evidence_ids":["fact-water"],
                }],
            })

        client=SimpleNamespace(chat=SimpleNamespace(
            completions=SimpleNamespace(create=create)))
        terminal=await HybridMultiBrain(
            client,MultiBrainSettings(True,"fake",1,1,.1),
        ).start(with_claims,activation_for(
            intent_kind="related_question",visual_requested=False,
            unresolved_dimensions=("difference",),
        )).terminal("answer")
        self.assertEqual(terminal.status,"rejected")

    def test_settings_are_explicit_and_bounded(self):
        with patch.dict(os.environ, {
            "VOICE_WORKFLOW_AGENT_MULTI_BRAIN_ENABLED": "true",
            "VOICE_WORKFLOW_AGENT_MULTI_BRAIN_MODEL": "grok-test",
            "VOICE_WORKFLOW_AGENT_ANSWER_BRAIN_PRIMARY_BUDGET_SECONDS": "0.5",
            "VOICE_WORKFLOW_AGENT_ANSWER_BRAIN_TIMEOUT_SECONDS": "7",
            "VOICE_WORKFLOW_AGENT_PLANNER_BRAIN_TIMEOUT_SECONDS": "5",
        }, clear=False):
            settings = MultiBrainSettings.from_environment()
        self.assertTrue(settings.enabled)
        self.assertEqual(settings.model, "grok-test")
        self.assertEqual(settings.primary_answer_budget_seconds, .5)
        self.assertLess(settings.primary_answer_budget_seconds, settings.answer_timeout_seconds)
