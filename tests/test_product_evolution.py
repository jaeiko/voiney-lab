"""Tests for commercial product evolution capabilities:
1. Researcher Learning Mode (grounded step rationale, purpose, common mistakes).
2. Protocol Version Management & SHA256 audit queries.
3. Workflow Graph Conditional Branching (DAG transitions based on verified observations).
4. Multi-Session Experiment History & Continuation.
"""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from voice_workflow_agent.completion_intent import (
    is_history_or_continuation_intent,
    is_learning_question,
    is_version_question,
)
from voice_workflow_agent.document_store import SCHEMA as DOC_SCHEMA
from voice_workflow_agent.procedure_definitions import (
    ProcedureDefinition,
    ProcedureStep,
    SourceReference,
    load_procedure_definitions,
)
from voice_workflow_agent.procedure_store import ProcedureStore
from voice_workflow_agent.procedures import ProcedureController
from voice_workflow_agent.tools import ToolContext, execute_tool


class ProductEvolutionTests(unittest.TestCase):
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
                 "facility_sop", "Standard PCR Amplification SOP", "Lab Safety", "[]", "1.2", "ko", "LAB-MAIN",
                 "facility_authority", "approved", "operational", "sha-sop-01", "original", 1),
            )
            db.execute(
                """INSERT INTO sections
                (document_row_id,section_code,section_title,page_start,page_end,content,keywords)
                VALUES (?,?,?,?,?,?,?)""",
                (
                    cur.lastrowid, "SOP-1", "PCR Standard Procedure", 1, 3,
                    "1. 마스터믹스를 조제하고 튜브에 분주합니다.\n"
                    "2. 템플릿 DNA를 첨가하고 믹싱합니다.\n"
                    "3. PCR 기기에서 35사이클 증폭을 수행합니다.\n"
                    "4. 전기영동으로 증폭 산물을 확인합니다.",
                    "[]",
                ),
            )
        self.proc_path = self.root / "procedures.json"
        self.store_db = self.root / "procedures.sqlite"
        self.store = ProcedureStore(self.store_db)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _sample_definitions(self, with_branching: bool = False) -> dict[str, ProcedureDefinition]:
        steps = [
            {
                "step_id": "step-1",
                "order": 1,
                "title": "마스터믹스 조제",
                "approved_spoken_instruction": "1. 마스터믹스를 조제하고 튜브에 분주합니다.",
                "completion_mode": "explicit_confirmation",
                "source_reference": {"section_reference": "SOP-1", "page_start": 1, "page_end": 1},
                "purpose": "PCR 반응에 필요한 중합효소 및 dNTP 혼합물 균일 분주",
                "rationale": "효소 활성 저하를 방지하기 위해 얼음 위에서 조제해야 합니다.",
                "common_mistakes": ["실온에 장시간 방치", "피펫팅 시 거품 발생"],
                "observation_schema": {
                    "type": "text",
                    "required": True,
                    "label": "시약 로트번호",
                    "utterance_subjects": ["로트번호", "시약 로트"],
                },
                **({
                    "conditional_transitions": [
                        {"condition": "observation_equals:LOT-SKIP", "target_step_id": "step-3"},
                        {"condition": "observation_contains:RECOVERY", "target_step_id": "step-4"},
                    ]
                } if with_branching else {}),
            },
            {
                "step_id": "step-2",
                "order": 2,
                "title": "템플릿 DNA 첨가",
                "approved_spoken_instruction": "2. 템플릿 DNA를 첨가하고 믹싱합니다.",
                "completion_mode": "explicit_confirmation",
                "source_reference": {"section_reference": "SOP-1", "page_start": 2, "page_end": 2},
                "purpose": "표적 유전자 단편 제공",
                "rationale": "교차 오염을 방지하기 위해 필터 팁을 사용해야 합니다.",
                "common_mistakes": ["필터 팁 미사용", "템플릿 과다 첨가에 의한 반응 저해"],
            },
            {
                "step_id": "step-3",
                "order": 3,
                "title": "PCR 증폭 수행",
                "approved_spoken_instruction": "3. PCR 기기에서 35사이클 증폭을 수행합니다.",
                "completion_mode": "explicit_confirmation",
                "source_reference": {"section_reference": "SOP-1", "page_start": 2, "page_end": 3},
                "purpose": "열순환을 통한 DNA 대량 복제",
                "rationale": "초기 변성 95도 5분 유지가 완전한 단일 가닥 형성에 필수적입니다.",
                "common_mistakes": ["열순환기 뚜껑 히터 온도 미설정"],
            },
            {
                "step_id": "step-4",
                "order": 4,
                "title": "전기영동 분석",
                "approved_spoken_instruction": "4. 전기영동으로 증폭 산물을 확인합니다.",
                "completion_mode": "explicit_confirmation",
                "source_reference": {"section_reference": "SOP-1", "page_start": 3, "page_end": 3},
                "purpose": "증폭 산물의 분자량 및 단일 밴드 여부 검증",
                "rationale": "아가로스 겔 농도 1.5%에서 500bp 밴드가 최적 분리됩니다.",
                "common_mistakes": ["로딩 버퍼 미혼합", "전압 과다 인가"],
            },
        ]
        payload = {
            "procedures": [
                {
                    "schema_version": 1,
                    "procedure_id": "pcr-standard-01",
                    "title": "Standard PCR Protocol",
                    "version": "1.2",
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
                    "steps": steps,
                }
            ]
        }
        self.proc_path.write_text(json.dumps(payload), encoding="utf-8")
        return load_procedure_definitions(
            self.proc_path,
            self.catalog,
            facility_id="LAB-MAIN",
            language="ko",
            usage_scope="operational",
        )

    # -------------------------------------------------------------------------
    # 1. Researcher Learning Mode Tests
    # -------------------------------------------------------------------------
    def test_learning_metadata_loaded_and_retrieved(self):
        defs = self._sample_definitions()
        step1 = defs["pcr-standard-01"].steps[0]
        self.assertEqual(step1.purpose, "PCR 반응에 필요한 중합효소 및 dNTP 혼합물 균일 분주")
        self.assertIn("얼음 위에서 조제", step1.rationale)
        self.assertEqual(len(step1.common_mistakes), 2)
        self.assertIn("피펫팅 시 거품 발생", step1.common_mistakes)

        controller = ProcedureController(defs, self.store)
        controller.start("pcr-standard-01", facility_id="LAB-MAIN", language="ko", usage_scope="operational")

        learning = controller.get_learning_context()
        self.assertEqual(learning["status"], "success")
        self.assertEqual(learning["step_id"], "step-1")
        self.assertEqual(learning["step_number"], 1)
        self.assertEqual(learning["purpose"], "PCR 반응에 필요한 중합효소 및 dNTP 혼합물 균일 분주")
        self.assertIn("얼음 위에서 조제", learning["rationale"])
        self.assertEqual(len(learning["common_mistakes"]), 2)

        # Query explicit step-2 learning context
        step2_learning = controller.get_learning_context(step_id="step-2")
        self.assertEqual(step2_learning["step_id"], "step-2")
        self.assertEqual(step2_learning["purpose"], "표적 유전자 단편 제공")

    def test_execute_tool_learning_context(self):
        defs = self._sample_definitions()
        controller = ProcedureController(defs, self.store)
        controller.start("pcr-standard-01", facility_id="LAB-MAIN", language="ko", usage_scope="operational")

        context = ToolContext(
            catalog_path=self.catalog,
            facility_id="LAB-MAIN",
            language="ko",
            usage_scope="operational",
            procedure_controller=controller,
        )

        res = execute_tool("get_step_learning_context", {}, context=context)
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["step_id"], "step-1")
        self.assertIn("얼음 위에서 조제", res["rationale"])

    def test_learning_intent_classifier(self):
        self.assertTrue(is_learning_question("이 단계는 왜 필요한가요?"))
        self.assertTrue(is_learning_question("이 단계의 목적이 뭐야?"))
        self.assertTrue(is_learning_question("주의해야 할 흔한 실수가 뭐야?"))
        self.assertTrue(is_learning_question("why is this step necessary?"))
        self.assertTrue(is_learning_question("what are common mistakes?"))
        self.assertFalse(is_learning_question("현재 단계 완료했어"))
        self.assertFalse(is_learning_question("타이머 시작해줘"))

    # -------------------------------------------------------------------------
    # 2. Protocol Version Management Tests
    # -------------------------------------------------------------------------
    def test_protocol_sha256_and_version_info(self):
        defs = self._sample_definitions()
        proc = defs["pcr-standard-01"]
        self.assertTrue(isinstance(proc.protocol_sha256, str))
        self.assertEqual(len(proc.protocol_sha256), 64)

        controller = ProcedureController(defs, self.store)
        start_res = controller.start("pcr-standard-01", facility_id="LAB-MAIN", language="ko", usage_scope="operational")
        session_id = controller.attached_session_id

        version_info = controller.get_version_info()
        self.assertEqual(version_info["status"], "success")
        self.assertEqual(version_info["procedure_id"], "pcr-standard-01")
        self.assertEqual(version_info["version"], "1.2")
        self.assertEqual(version_info["approval_status"], "approved")
        self.assertEqual(version_info["protocol_sha256"], proc.protocol_sha256)
        self.assertEqual(version_info["active_session_id"], session_id)

    def test_execute_tool_version_info(self):
        defs = self._sample_definitions()
        controller = ProcedureController(defs, self.store)
        controller.start("pcr-standard-01", facility_id="LAB-MAIN", language="ko", usage_scope="operational")

        context = ToolContext(
            catalog_path=self.catalog,
            facility_id="LAB-MAIN",
            language="ko",
            usage_scope="operational",
            procedure_controller=controller,
        )

        res = execute_tool("get_protocol_version_info", {}, context=context)
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["version"], "1.2")
        self.assertEqual(len(res["protocol_sha256"]), 64)

    def test_version_intent_classifier(self):
        self.assertTrue(is_version_question("현재 프로토콜 버전이 뭐야?"))
        self.assertTrue(is_version_question("SOP 버전 알려줘"))
        self.assertTrue(is_version_question("프로토콜 해시 알려줘"))
        self.assertTrue(is_version_question("show protocol version"))
        self.assertTrue(is_version_question("what is the protocol sha256?"))
        self.assertFalse(is_version_question("시약 로트번호 1024 기록해줘"))

    # -------------------------------------------------------------------------
    # 3. Workflow Graph Conditional Branching (DAG) Tests
    # -------------------------------------------------------------------------
    def test_workflow_graph_conditional_branching_jump(self):
        defs = self._sample_definitions(with_branching=True)
        controller = ProcedureController(defs, self.store)
        controller.start("pcr-standard-01", facility_id="LAB-MAIN", language="ko", usage_scope="operational")

        # Step 1: Record observation matching conditional transition: "LOT-SKIP" -> target: "step-3"
        controller.record_observation("step-1", "LOT-SKIP")
        comp = controller.complete("step-1")
        self.assertEqual(comp["status"], "success")
        self.assertFalse(comp["completed"])
        # Should have branched directly to step-3 (skipping step-2)
        self.assertEqual(comp["state"]["current_step_id"], "step-3")
        self.assertEqual(comp["state"]["current_step_number"], 3)

    def test_workflow_graph_default_linear_transition_when_condition_unmatched(self):
        defs = self._sample_definitions(with_branching=True)
        controller = ProcedureController(defs, self.store)
        controller.start("pcr-standard-01", facility_id="LAB-MAIN", language="ko", usage_scope="operational")

        # Step 1: Record observation NOT matching conditional jump: "LOT-NORMAL-001"
        controller.record_observation("step-1", "LOT-NORMAL-001")
        comp = controller.complete("step-1")
        self.assertEqual(comp["status"], "success")
        self.assertFalse(comp["completed"])
        # Standard linear advance to step-2
        self.assertEqual(comp["state"]["current_step_id"], "step-2")
        self.assertEqual(comp["state"]["current_step_number"], 2)

    # -------------------------------------------------------------------------
    # 4. Long-Term Multi-Session Experiment History & Continuation Tests
    # -------------------------------------------------------------------------
    def test_experiment_history_and_continuation(self):
        defs = self._sample_definitions()
        controller = ProcedureController(defs, self.store)

        # Start session 1 and complete step 1
        s1 = controller.start("pcr-standard-01", facility_id="LAB-MAIN", language="ko", usage_scope="operational")
        session1_id = controller.attached_session_id
        controller.record_observation("step-1", "LOT-A")
        controller.complete("step-1")

        # History should list session 1
        history = controller.list_history(limit=5)
        self.assertEqual(history["status"], "success")
        self.assertGreaterEqual(len(history["sessions"]), 1)
        self.assertEqual(history["sessions"][0]["session_id"], session1_id)
        self.assertEqual(history["sessions"][0]["status"], "active")
        self.assertEqual(history["sessions"][0]["current_step_index"], 1)

        # Detach session (e.g. overnight)
        controller.detach()
        self.assertEqual(controller.current()["status"], "error")

        # Resume session 1
        resume_res = controller.resume(session1_id)
        self.assertEqual(resume_res["status"], "success")
        self.assertEqual(controller.attached_session_id, session1_id)
        self.assertEqual(resume_res["state"]["current_step_id"], "step-2")
        self.assertEqual(resume_res["state"]["current_step_number"], 2)

    def test_execute_tool_history_and_continuation(self):
        defs = self._sample_definitions()
        controller = ProcedureController(defs, self.store)
        s1 = controller.start("pcr-standard-01", facility_id="LAB-MAIN", language="ko", usage_scope="operational")
        session1_id = controller.attached_session_id

        context = ToolContext(
            catalog_path=self.catalog,
            facility_id="LAB-MAIN",
            language="ko",
            usage_scope="operational",
            procedure_controller=controller,
        )

        hist_res = execute_tool("get_experiment_history", {"limit": 5}, context=context)
        self.assertEqual(hist_res["status"], "success")
        self.assertEqual(hist_res["sessions"][0]["session_id"], session1_id)

        controller.detach()
        cont_res = execute_tool("continue_experiment", {"session_id": session1_id}, context=context)
        self.assertEqual(cont_res["status"], "success")
        self.assertEqual(controller.attached_session_id, session1_id)

    def test_history_and_continuation_intent_classifier(self):
        self.assertEqual(is_history_or_continuation_intent("이전 실험 목록 보여줘"), ("history", None))
        self.assertEqual(is_history_or_continuation_intent("최근 실험 이력 보여줘"), ("history", None))
        self.assertEqual(is_history_or_continuation_intent("show experiment history"), ("history", None))
        self.assertEqual(is_history_or_continuation_intent("어제 실험 이어서 진행해줘"), ("continue", None))
        self.assertEqual(is_history_or_continuation_intent("continue previous experiment"), ("continue", None))
        self.assertIsNone(is_history_or_continuation_intent("현재 단계 완료했습니다"))


if __name__ == "__main__":
    unittest.main()
