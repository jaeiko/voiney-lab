"""Comprehensive regression test suite for server-owned intent arbitration priority.

Tests verify:
1. "왜 해야 돼?" / "이 단계를 왜 해야 돼?" routes to Learning Intent without repeating procedural instructions.
2. "현재 프로토콜의 버전을 알려줘" routes to Audit Intent.
3. "어제 실험 이어줘" with no stored sessions safely returns "저장된 진행 중인 실험 세션을 찾지 못했습니다."
4. "이 실험 성공할까?" returns safe uncertainty response without procedure explanation.
5. "왜 하는지 알려주고 다음 단계 알려줘" explains current step, previews next step, and asks confirmation without auto-advancing.
"""

import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any

from voice_workflow_agent.brain import ConversationHistory, stream_brain_turn
from voice_workflow_agent.completion_intent import (
    is_learning_question,
    is_version_question,
    is_history_or_continuation_intent,
    is_combined_learning_and_next_question,
    is_speculative_or_uncertainty_question,
    resolve_korean_completion_decision,
)
from voice_workflow_agent.document_store import SCHEMA as DOC_SCHEMA
from voice_workflow_agent.procedure_definitions import (
    ProcedureDefinition,
    load_procedure_definitions,
)
from voice_workflow_agent.procedure_store import ProcedureStore
from voice_workflow_agent.procedures import ProcedureController
from voice_workflow_agent.tools import (
    ToolContext,
    GET_STEP_LEARNING_CONTEXT_TOOL_NAME,
    GET_PROTOCOL_VERSION_INFO_TOOL_NAME,
    GET_EXPERIMENT_HISTORY_TOOL_NAME,
    CONTINUE_EXPERIMENT_TOOL_NAME,
)


class IntentArbitrationPriorityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.catalog = self.root / "catalog.sqlite"
        with sqlite3.connect(self.catalog) as db:
            db.executescript(DOC_SCHEMA)
            db.execute("INSERT INTO catalog_metadata VALUES (2)")
            cur = db.execute(
                """INSERT INTO documents
                (document_id,document_family_id,canonical_source_id,canonical_version,
                 document_type,title,issuer,cas_numbers,version,language,facility_id,
                 source_authority,approval_status,usage_scope,source_checksum,
                 translation_status,active)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("sop-bio-01", "fam-01", "src-01", "1",
                 "facility_sop", "In-Gel Digestion SOP", "Lab Safety", "[]", "1.2", "ko", "LAB-MAIN",
                 "facility_authority", "approved", "operational", "sha-sop-01", "original", 1),
            )
            db.execute(
                """INSERT INTO sections
                (document_row_id,section_code,section_title,page_start,page_end,content,keywords)
                VALUES (?,?,?,?,?,?,?)""",
                (
                    cur.lastrowid, "SOP-1", "In-Gel Digestion Procedure", 1, 3,
                    "1. 염색된 단백질 밴드를 준비해 작은 조각으로 나누고 AMBIC 용액이 담긴 튜브에 넣습니다.\n"
                    "2. DTT 50 마이크로리터를 첨가하고 56도에서 30분간 배양합니다.\n"
                    "3. 실온으로 냉각 후 반응을 확인합니다.",
                    "[]",
                ),
            )
        self.proc_path = self.root / "procedures.json"
        self.store_db = self.root / "procedures.sqlite"
        self.store = ProcedureStore(self.store_db)

        payload = {
            "procedures": [
                {
                    "schema_version": 1,
                    "procedure_id": "in-gel-digest-01",
                    "title": "단백질 겔 소화 표준 프로토콜",
                    "version": "2.1",
                    "facility_id": "LAB-MAIN",
                    "language": "ko",
                    "approval_status": "approved",
                    "usage_scope": "operational",
                    "active": True,
                    "approved_document": {
                        "document_id": "sop-bio-01",
                        "version": "1.2",
                        "language": "ko",
                        "section_reference": "SOP-1",
                        "page_start": 1,
                        "page_end": 3,
                    },
                    "steps": [
                        {
                            "step_id": "step-1",
                            "order": 1,
                            "title": "단백질 밴드 절단 및 AMBIC 처리",
                            "approved_spoken_instruction": "1. 염색된 단백질 밴드를 준비해 작은 조각으로 나누고 AMBIC 용액이 담긴 튜브에 넣습니다.",
                            "completion_mode": "explicit_confirmation",
                            "source_reference": {"section_reference": "SOP-1", "page_start": 1, "page_end": 1},
                            "purpose": "젤 안의 단백질을 분석 가능한 형태로 준비",
                            "rationale": "젤 조각에서 단백질을 분해하면 이후 질량분석 과정에서 단백질 식별이 가능해집니다",
                            "common_mistakes": ["오염 방지 및 시료 손상 주의"],
                        },
                        {
                            "step_id": "step-2",
                            "order": 2,
                            "title": "DTT 환원 반응 및 배양",
                            "approved_spoken_instruction": "2. DTT 50 마이크로리터를 첨가하고 56도에서 30분간 배양합니다.",
                            "completion_mode": "explicit_confirmation",
                            "source_reference": {"section_reference": "SOP-1", "page_start": 2, "page_end": 2},
                            "timer": {"duration_seconds": 1800},
                            "purpose": "단백질 이황화 결합 환원 및 변성",
                            "rationale": "열 변성과 환원 반응을 동시에 유도하여 구조를 완전히 풉니다",
                            "common_mistakes": ["온도 미달 및 시간 부족"],
                        },
                        {
                            "step_id": "step-3",
                            "order": 3,
                            "title": "냉각 및 최종 확인",
                            "approved_spoken_instruction": "3. 실온으로 냉각 후 반응을 확인합니다.",
                            "completion_mode": "explicit_confirmation",
                            "source_reference": {"section_reference": "SOP-1", "page_start": 3, "page_end": 3},
                            "purpose": "다음 알킬화 반응을 위한 열 안정화",
                            "rationale": "고온 지속 시 시약 불활성화 방지",
                            "common_mistakes": ["급냉으로 인한 침전"],
                        },
                    ],
                }
            ]
        }
        self.proc_path.write_text(json.dumps(payload), encoding="utf-8")
        self.definitions = load_procedure_definitions(
            self.proc_path,
            self.catalog,
            facility_id="LAB-MAIN",
            language="ko",
            usage_scope="operational",
        )
        self.proc_def = self.definitions["in-gel-digest-01"]
        self.clock_val = 1000.0
        self.controller = ProcedureController(self.definitions, self.store, clock=lambda: self.clock_val)

        self.tool_context = ToolContext(
            catalog_path=self.catalog,
            facility_id="LAB-MAIN",
            language="ko",
            usage_scope="operational",
            procedure_controller=self.controller,
        )

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    async def test_1_learning_intent_routing_and_no_procedural_repetition(self):
        """Verify '왜 해야 돼?' and '이 단계를 왜 해야 돼?' route to Learning Intent without repeating procedural instructions."""
        self.controller.start("in-gel-digest-01", facility_id="LAB-MAIN", language="ko", usage_scope="operational")

        test_utterances = [
            "왜 해야 돼?",
            "왜 해야 돼",
            "이 단계를 왜 해야 돼?",
            "이 단계를 왜 해야 돼",
            "왜 필요한가?",
            "목적이 뭐야?",
            "주의할 점 알려줘",
        ]

        for utterance in test_utterances:
            self.assertTrue(is_learning_question(utterance), f"'{utterance}' should match learning intent")

            history = ConversationHistory()
            spoken_sentences = []

            async def capture_sentence(seg):
                spoken_sentences.append(seg.text)

            result = await stream_brain_turn(
                client=None,
                history=history,
                transcript=utterance,
                on_sentence=capture_sentence,
                tool_context=self.tool_context,
            )

            self.assertEqual(result.tools_used, [GET_STEP_LEARNING_CONTEXT_TOOL_NAME])
            self.assertIn("목적은", result.text)
            self.assertIn("젤 안의 단백질을 분석 가능한 형태로 준비", result.text)
            self.assertIn("질량분석 과정에서 단백질 식별이 가능해집니다", result.text)
            self.assertIn("오염 방지 및 시료 손상 주의", result.text)

            # Ensure procedural instruction is NOT blindly recited
            self.assertNotIn("염색된 단백질 밴드를 준비해 작은 조각으로 나누고", result.text)

    async def test_2_audit_intent_routing_with_particles(self):
        """Verify '현재 프로토콜의 버전을 알려줘' routes to Audit Intent."""
        self.controller.start("in-gel-digest-01", facility_id="LAB-MAIN", language="ko", usage_scope="operational")

        test_utterances = [
            "현재 프로토콜의 버전을 알려줘",
            "현재 프로토콜 버전 알려줘",
            "프로토콜의 버전을 확인해줘",
            "이 SOP 몇 버전이야?",
            "프로토콜의 해시를 알려줘",
        ]

        for utterance in test_utterances:
            self.assertTrue(is_version_question(utterance), f"'{utterance}' should match version intent")

            history = ConversationHistory()
            spoken_sentences = []

            async def capture_sentence(seg):
                spoken_sentences.append(seg.text)

            result = await stream_brain_turn(
                client=None,
                history=history,
                transcript=utterance,
                on_sentence=capture_sentence,
                tool_context=self.tool_context,
            )

            self.assertEqual(result.tools_used, [GET_PROTOCOL_VERSION_INFO_TOOL_NAME])
            self.assertIn("단백질 겔 소화 표준 프로토콜", result.text)
            self.assertIn("버전 2.1", result.text)
            self.assertIn("문서 버전은 1.2", result.text)
            self.assertIn(self.proc_def.protocol_sha256[:8], result.text)

    async def test_3_resume_with_no_session_safe_response(self):
        """Verify '어제 실험 이어줘' without stored session returns exact safe message."""
        # Controller starts with zero stored previous sessions
        test_utterances = [
            "어제 실험 이어줘",
            "어제 실험을 이어줘",
            "어제 하던 실험 이어서 해줘",
        ]

        for utterance in test_utterances:
            intent_tuple = is_history_or_continuation_intent(utterance)
            self.assertIsNotNone(intent_tuple)
            self.assertEqual(intent_tuple[0], "continue")

            history = ConversationHistory()
            spoken_sentences = []

            async def capture_sentence(seg):
                spoken_sentences.append(seg.text)

            result = await stream_brain_turn(
                client=None,
                history=history,
                transcript=utterance,
                on_sentence=capture_sentence,
                tool_context=self.tool_context,
            )

            self.assertEqual(result.tools_used, [GET_EXPERIMENT_HISTORY_TOOL_NAME])
            self.assertEqual(result.text, "저장된 진행 중인 실험 세션을 찾지 못했습니다.")

    async def test_4_speculative_uncertainty_safe_response(self):
        """Verify '이 실험 성공할까?' returns safe uncertainty response without reciting procedure instructions."""
        self.controller.start("in-gel-digest-01", facility_id="LAB-MAIN", language="ko", usage_scope="operational")

        test_utterances = [
            "이 실험 결과가 성공할까?",
            "이 실험 성공할까?",
            "실험 성공할까?",
            "결과가 잘 나올까?",
        ]

        for utterance in test_utterances:
            self.assertTrue(
                is_speculative_or_uncertainty_question(utterance),
                f"'{utterance}' should be recognized as speculative uncertainty",
            )

            history = ConversationHistory()
            spoken_sentences = []

            async def capture_sentence(seg):
                spoken_sentences.append(seg.text)

            result = await stream_brain_turn(
                client=None,
                history=history,
                transcript=utterance,
                on_sentence=capture_sentence,
                tool_context=self.tool_context,
            )

            self.assertEqual(
                result.text,
                "현재 정보만으로 실험 성공 여부를 판단할 수 없습니다. 관찰 결과와 측정 데이터를 기록하면 함께 확인할 수 있습니다.",
            )
            # Ensure no procedural instruction recitation
            self.assertNotIn("염색된 단백질 밴드를 준비해", result.text)

    async def test_5_combined_question_explains_previews_and_asks_confirmation(self):
        """Verify '왜 하는지 알려주고 다음 단계 알려줘' explains, previews, and asks confirmation without auto-transition."""
        self.controller.start("in-gel-digest-01", facility_id="LAB-MAIN", language="ko", usage_scope="operational")
        self.assertEqual(self.controller.current()["state"]["current_step_number"], 1)
        self.assertEqual(self.controller.current()["state"]["current_step_id"], "step-1")
        self.assertEqual(self.controller.current()["state"]["completed_step_count"], 0)

        test_utterances = [
            "이 단계 왜 하는지 알려주고 다음 단계도 알려줘",
            "왜 하는지 알려주고 다음 단계 알려줘",
        ]

        for utterance in test_utterances:
            self.assertTrue(
                is_combined_learning_and_next_question(utterance),
                f"'{utterance}' should match combined question intent",
            )

            history = ConversationHistory()
            spoken_sentences = []

            async def capture_sentence(seg):
                spoken_sentences.append(seg.text)

            result = await stream_brain_turn(
                client=None,
                history=history,
                transcript=utterance,
                on_sentence=capture_sentence,
                tool_context=self.tool_context,
            )

            self.assertEqual(result.tools_used, [GET_STEP_LEARNING_CONTEXT_TOOL_NAME])
            self.assertIn("목적은 젤 안의 단백질을 분석 가능한 형태로 준비", result.text)
            self.assertIn("다음 단계는 2단계인 'DTT 환원 반응 및 배양'", result.text)
            self.assertIn("현재 단계를 완료하셨으면 다음 단계로 진행할까요?", result.text)

            # Strict Invariant: Step must NOT have auto-advanced!
            self.assertEqual(self.controller.current()["state"]["current_step_number"], 1)
            self.assertEqual(self.controller.current()["state"]["current_step_id"], "step-1")
            self.assertEqual(self.controller.current()["state"]["completed_step_count"], 0)


if __name__ == "__main__":
    unittest.main()
