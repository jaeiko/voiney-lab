"""Scenario-based end-to-end evaluation for Voice Workflow Agent conversational routing.

Tests verify:
1. Learning question routing and Professor persona responses.
2. Protocol version and SHA256 audit inquiries.
3. Multi-session experiment history and continuation.
4. Normal procedure progression with zero regressions.
5. Curated protocol fixture conversational routing.
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


class VoiceProductScenarioTests(unittest.IsolatedAsyncioTestCase):
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
                    "1. 용액 A를 조제하여 튜브에 분주합니다.\n"
                    "2. DTT 용액을 첨가하고 56도에서 30분 배양합니다.\n"
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
                            "title": "용액 A 조제 및 분주",
                            "approved_spoken_instruction": "1. 용액 A를 조제하여 튜브에 분주합니다.",
                            "completion_mode": "explicit_confirmation",
                            "source_reference": {"section_reference": "SOP-1", "page_start": 1, "page_end": 1},
                            "purpose": "단백질 환원을 위한 최적 pH 완충 환경 조성",
                            "rationale": "DTT 반응 효율을 극대화하여 이황화 결합을 완벽하게 환원시키기 위함",
                            "common_mistakes": ["용액 농도 오차 및 팁 오염"],
                        },
                        {
                            "step_id": "step-2",
                            "order": 2,
                            "title": "DTT 반응 및 배양",
                            "approved_spoken_instruction": "2. DTT 용액을 첨가하고 56도에서 30분 배양합니다.",
                            "completion_mode": "explicit_confirmation",
                            "source_reference": {"section_reference": "SOP-1", "page_start": 2, "page_end": 2},
                            "timer": {"duration_seconds": 1800},
                            "purpose": "단백질 변성 및 이황화 결합 절단",
                            "rationale": "열 변성과 환원 반응을 동시에 유도하기 위함",
                            "common_mistakes": ["온도 미달 및 시간 부족"],
                        },
                        {
                            "step_id": "step-3",
                            "order": 3,
                            "title": "냉각 및 최종 확인",
                            "approved_spoken_instruction": "3. 실온으로 냉각 후 반응을 확인합니다.",
                            "completion_mode": "explicit_confirmation",
                            "source_reference": {"section_reference": "SOP-1", "page_start": 3, "page_end": 3},
                            "purpose": "다음 효소 반응을 위한 열 안정화",
                            "rationale": "고온 상태 지속 시 시약 불활성화 방지",
                            "common_mistakes": ["급냉으로 인한 침전 발생"],
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

    async def test_scenario_1_learning_question_routing(self):
        """Verify real spoken learning queries activate Learning Mode without repeating raw instructions."""
        self.controller.start("in-gel-digest-01", facility_id="LAB-MAIN", language="ko", usage_scope="operational")

        learning_queries = [
            "근데 이 단계를 왜 해야 되는데?",
            "왜 이 단계 해야 해?",
            "이 단계 목적이 뭐야?",
            "주의할 점 알려줘",
            "이걸 왜 하는 거야?",
            "주의해야 할 흔한 실수가 뭐야?",
            "이 단계는 왜 필요한가요?",
        ]

        for query in learning_queries:
            self.assertTrue(
                is_learning_question(query),
                f"Query '{query}' failed to classify as learning question",
            )

            history = ConversationHistory()
            spoken_sentences = []

            async def capture_sentence(seg):
                spoken_sentences.append(seg.text)

            result = await stream_brain_turn(
                client=None,
                history=history,
                transcript=query,
                on_sentence=capture_sentence,
                tool_context=self.tool_context,
            )

            self.assertEqual(result.tools_used, [GET_STEP_LEARNING_CONTEXT_TOOL_NAME])
            self.assertIn("목적은", result.text)
            self.assertIn("최적 pH 완충 환경 조성", result.text)
            self.assertIn("주의할 점", result.text)
            self.assertIn("용액 농도 오차 및 팁 오염", result.text)

            # Ensure the raw instruction is not blindly repeated
            self.assertNotIn("용액 A를 조제하여 튜브에 분주합니다", result.text)

    async def test_scenario_2_protocol_version_inquiry_routing(self):
        """Verify version and hash queries return audit metadata concisely."""
        self.controller.start("in-gel-digest-01", facility_id="LAB-MAIN", language="ko", usage_scope="operational")

        version_queries = [
            "현재 프로토콜 버전 알려줘",
            "이 SOP 몇 버전이야?",
            "이 문서 해시 알려줘",
            "현재 프로토콜 버전이 뭐야?",
            "이 실험의 프로토콜 해시 알려줘",
        ]

        for query in version_queries:
            self.assertTrue(
                is_version_question(query),
                f"Query '{query}' failed to classify as version question",
            )

            history = ConversationHistory()
            spoken_sentences = []

            async def capture_sentence(seg):
                spoken_sentences.append(seg.text)

            result = await stream_brain_turn(
                client=None,
                history=history,
                transcript=query,
                on_sentence=capture_sentence,
                tool_context=self.tool_context,
            )

            self.assertEqual(result.tools_used, [GET_PROTOCOL_VERSION_INFO_TOOL_NAME])
            self.assertIn("단백질 겔 소화 표준 프로토콜", result.text)
            self.assertIn("버전 2.1", result.text)
            self.assertIn("문서 버전은 1.2", result.text)
            self.assertIn("해시는", result.text)
            self.assertIn(self.proc_def.protocol_sha256[:8], result.text)

    async def test_scenario_3_experiment_continuation_routing(self):
        """Verify experiment history and multi-day resumption queries."""
        # 1. Start and advance session
        start_res = self.controller.start("in-gel-digest-01", facility_id="LAB-MAIN", language="ko", usage_scope="operational")
        session_id = self.controller.attached_session_id
        self.controller.complete("step-1")

        # 2. Detach session (e.g. overnight 16h)
        self.controller.detach()

        # 3. Test history intent
        history_queries = [
            "이전 실험 목록 보여줘",
            "최근 실험 기록 알려줘",
            "지난 실험 내역 확인해줘",
        ]
        for query in history_queries:
            intent_tuple = is_history_or_continuation_intent(query)
            self.assertIsNotNone(intent_tuple)
            self.assertEqual(intent_tuple[0], "history")

            history = ConversationHistory()
            spoken_sentences = []

            async def capture_sentence(seg):
                spoken_sentences.append(seg.text)

            result = await stream_brain_turn(
                client=None,
                history=history,
                transcript=query,
                on_sentence=capture_sentence,
                tool_context=self.tool_context,
            )

            self.assertEqual(result.tools_used, [GET_EXPERIMENT_HISTORY_TOOL_NAME])
            self.assertIn("최근 실험 기록", result.text)
            self.assertIn("in-gel-digest-01", result.text)

        # 4. Test continuation intent
        continue_queries = [
            "어제 하던 실험 이어서 해줘",
            "전에 하던 것 계속하자",
            "지난 실험 불러와서 계속해줘",
        ]
        for query in continue_queries:
            intent_tuple = is_history_or_continuation_intent(query)
            self.assertIsNotNone(intent_tuple)
            self.assertEqual(intent_tuple[0], "continue")

            history = ConversationHistory()
            spoken_sentences = []

            async def capture_sentence(seg):
                spoken_sentences.append(seg.text)

            result = await stream_brain_turn(
                client=None,
                history=history,
                transcript=query,
                on_sentence=capture_sentence,
                tool_context=self.tool_context,
            )

            self.assertEqual(result.tools_used, [CONTINUE_EXPERIMENT_TOOL_NAME])
            self.assertIn("이전 실험 상태를 확인했습니다", result.text)
            self.assertIn("1단계까지 완료된 세션을 불러왔습니다", result.text)
            self.assertIn("2단계", result.text)
            self.assertIn("계속 진행할까요?", result.text)
            self.assertEqual(self.controller.attached_session_id, session_id)

    async def test_scenario_4_normal_procedure_progression(self):
        """Verify normal progression commands still work cleanly without collision."""
        start_res = self.controller.start("in-gel-digest-01", facility_id="LAB-MAIN", language="ko", usage_scope="operational")
        self.assertEqual(start_res["status"], "success")
        self.assertEqual(start_res["state"]["current_step_id"], "step-1")

        # Intent classification for completion
        self.assertTrue(resolve_korean_completion_decision("1단계 완료했어", language="ko").is_completion)
        self.assertTrue(resolve_korean_completion_decision("현재 단계 완료했으니 다음으로 넘어가자", language="ko").is_completion)
        self.assertFalse(is_learning_question("1단계 완료했어"))
        self.assertFalse(is_version_question("1단계 완료했어"))

        # Step 1 complete
        c1 = self.controller.complete("step-1")
        self.assertEqual(c1["status"], "success")
        self.assertEqual(c1["state"]["current_step_id"], "step-2")

        # Step 2 timer
        self.clock_val = 1000.0
        t2 = self.controller.start_timer("step-2")
        self.assertEqual(t2["status"], "success")

        # Step 2 complete with timer elapsed
        self.clock_val = 3000.0
        c2 = self.controller.complete("step-2")
        self.assertEqual(c2["status"], "success")
        self.assertEqual(c2["state"]["current_step_id"], "step-3")

        # Step 3 complete (final)
        c3 = self.controller.complete("step-3")
        self.assertEqual(c3["status"], "success")
        self.assertTrue(c3["completed"])
        self.assertEqual(c3["state"]["status"], "completed")


if __name__ == "__main__":
    unittest.main()
