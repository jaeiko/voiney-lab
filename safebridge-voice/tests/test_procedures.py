import tempfile
import unittest
from pathlib import Path

from safebridge_voice.procedure_definitions import (
    ProcedureDefinition, ProcedureStep, SourceReference,
)
from safebridge_voice.procedure_store import ProcedureStore
from safebridge_voice.procedures import (
    KOREAN_COMPLETION_PHRASES, KOREAN_TIMER_START_PHRASES,
    ProcedureController, authorized_completion_step_id,
    authorized_observation_arguments,
    authorized_timer_start_step_id,
)
from safebridge_voice.tools import (
    COMPLETE_CURRENT_STEP_TOOL_NAME, GET_CURRENT_STEP_TOOL_NAME,
    GET_WORKFLOW_SUMMARY_TOOL_NAME, RECORD_STEP_OBSERVATION_TOOL_NAME,
    START_PROCEDURE_TOOL_NAME, START_STEP_TIMER_TOOL_NAME, ToolContext, execute_tool,
)


def approved(procedure_id="demo"):
    source=SourceReference("DEMO",1,1)
    return ProcedureDefinition(
        1,procedure_id,"FICTIONAL NON-OPERATIONAL Demo","1","TEST","en","approved",
        "test_only",True,"doc","1","en",source,
        (ProcedureStep("one",1,"One","Read approved fictional instruction one.",
                       "explicit_confirmation",source),
         ProcedureStep("two",2,"Two","Read approved fictional instruction two.",
                       "explicit_confirmation",source)))


def workflow_approved():
    source=SourceReference("DEMO-WORKFLOW",1,1)
    return ProcedureDefinition(
        1,"workflow","FICTIONAL NON-OPERATIONAL Workflow","2","TEST","en",
        "approved","test_only",True,"workflow-doc","2","en",source,
        (
            ProcedureStep(
                "observe",1,"Observe","Report the fictional display value.",
                "explicit_confirmation",source,
                observation_schema={
                    "type":"text","required":True,"label":"Fictional display",
                    "utterance_subjects":[
                        "가상 라벨","가상 표시창","가상 표시창 색상",
                        "가상 표시창 색깔",
                    ],
                }),
            ProcedureStep(
                "wait",2,"Wait","Start the fixed fictional timer.",
                "explicit_confirmation",source,timer={"duration_seconds":10}),
        ))


class ProcedureToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=ProcedureStore(Path(self.tmp.name)/"procedure.sqlite")
        self.controller=ProcedureController({"demo":approved(),"other":approved("other")},self.store)
        self.context=ToolContext(Path("catalog.sqlite"),"TEST","en","test_only",
                                 procedure_controller=self.controller,
                                 procedure_completion_authorized_step_id=None)
    def tearDown(self): self.store.close(); self.tmp.cleanup()
    def call(self,name,args): return execute_tool(name,args,self.context)

    def test_start_is_compatible_single_attachment_and_idempotent(self):
        self.assertEqual(self.call(START_PROCEDURE_TOOL_NAME,{"procedure_id":"missing"})["code"],
                         "procedure_not_available")
        first=self.call(START_PROCEDURE_TOOL_NAME,{"procedure_id":"demo"})
        second=self.call(START_PROCEDURE_TOOL_NAME,{"procedure_id":"demo"})
        self.assertEqual(first["status"],"success"); self.assertTrue(second["idempotent"])
        self.assertEqual(len(self.store.list_events(self.controller.attached_session_id)),1)
        self.assertEqual(self.call(START_PROCEDURE_TOOL_NAME,{"procedure_id":"other"})["code"],
                         "procedure_conflict")

    def test_start_rechecks_current_trusted_language_facility_and_scope(self):
        for context in (
            ToolContext(Path("catalog.sqlite"),"OTHER","en","test_only",
                        procedure_controller=self.controller),
            ToolContext(Path("catalog.sqlite"),"TEST","ko","test_only",
                        procedure_controller=self.controller),
            ToolContext(Path("catalog.sqlite"),"TEST","en","operational",
                        procedure_controller=self.controller),
        ):
            self.assertEqual(execute_tool(
                START_PROCEDURE_TOOL_NAME,{"procedure_id":"demo"},context)["code"],
                "procedure_scope_mismatch")
            self.assertIsNone(self.controller.attached_session_id)

    def test_current_is_deterministic_read_only_and_approved(self):
        self.call(START_PROCEDURE_TOOL_NAME,{"procedure_id":"demo"})
        before=self.store.list_events(self.controller.attached_session_id)
        one=self.call(GET_CURRENT_STEP_TOOL_NAME,{})
        two=self.call(GET_CURRENT_STEP_TOOL_NAME,{})
        self.assertEqual(one,two)
        self.assertEqual(one["state"]["approved_current_instruction"],
                         approved().steps[0].instruction)
        self.assertEqual(self.store.list_events(self.controller.attached_session_id),before)

    def test_completion_skip_stale_replay_and_final_rules(self):
        self.call(START_PROCEDURE_TOOL_NAME,{"procedure_id":"demo"})
        session_id=self.controller.attached_session_id
        before=self.store.get_session(session_id)
        self.assertEqual(self.call(COMPLETE_CURRENT_STEP_TOOL_NAME,{"expected_step_id":"two"})["code"],
                         "explicit_confirmation_required")
        self.assertEqual(self.store.get_session(session_id),before)
        self.assertEqual(self.call(COMPLETE_CURRENT_STEP_TOOL_NAME,
                                   {"expected_step_id":"one"})["code"],
                         "explicit_confirmation_required")
        self.context=ToolContext(Path("catalog.sqlite"),"TEST","en","test_only",
                                 procedure_controller=self.controller,
                                 procedure_completion_authorized_step_id="one")
        first=self.call(COMPLETE_CURRENT_STEP_TOOL_NAME,{"expected_step_id":"one"})
        replay=self.call(COMPLETE_CURRENT_STEP_TOOL_NAME,{"expected_step_id":"one"})
        self.assertEqual(first["state"]["current_step_id"],"two")
        self.assertEqual(replay["status"],"already_completed")
        self.assertEqual(len([e for e in self.store.list_events(session_id)
                             if e["event_type"]=="step_completed"]),1)
        self.context=ToolContext(Path("catalog.sqlite"),"TEST","en","test_only",
                                 procedure_controller=self.controller,
                                 procedure_completion_authorized_step_id="two")
        final=self.call(COMPLETE_CURRENT_STEP_TOOL_NAME,{"expected_step_id":"two"})
        self.assertEqual(final["state"]["status"],"completed")
        self.assertEqual(self.call(COMPLETE_CURRENT_STEP_TOOL_NAME,
                                   {"expected_step_id":"two"})["status"],"already_completed")
        self.assertEqual(self.controller.complete("one")["code"],
                         "stale_step")

    def test_trusted_fields_cannot_be_injected(self):
        forbidden={"procedure_id":"demo","session_id":"x","facility_id":"OTHER",
                   "current_step_index":9,"runtime_database_path":"/tmp/x"}
        result=self.call(START_PROCEDURE_TOOL_NAME,forbidden)
        self.assertEqual(result["status"],"invalid_arguments")
        self.assertIsNone(self.controller.attached_session_id)
        self.assertEqual(self.call(GET_CURRENT_STEP_TOOL_NAME,{"session_id":"x"})["status"],
                         "invalid_arguments")
        self.assertEqual(self.call(COMPLETE_CURRENT_STEP_TOOL_NAME,
                                   {"expected_step_id":"one","status":"completed"})["status"],
                         "invalid_arguments")

    def test_detach_preserves_durable_audit(self):
        self.call(START_PROCEDURE_TOOL_NAME,{"procedure_id":"demo"})
        session_id=self.controller.attached_session_id
        self.call(COMPLETE_CURRENT_STEP_TOOL_NAME,{"expected_step_id":"one"})
        before=self.store.list_events(session_id)
        self.controller.detach()
        self.assertEqual(self.store.get_session(session_id)["status"],"active")
        self.assertEqual(self.store.list_events(session_id),before)

    def test_forced_completion_calls_require_exact_turn_authorization(self):
        korean=approved()
        korean=ProcedureDefinition(
            korean.schema_version,korean.procedure_id,korean.title,korean.version,
            korean.facility_id,"ko",korean.approval_status,korean.usage_scope,
            korean.active,korean.document_id,korean.document_version,"ko",
            korean.document_source,korean.steps)
        controller=ProcedureController({"demo":korean},self.store)
        start_context=ToolContext(Path("catalog.sqlite"),"TEST","ko","test_only",
                                  procedure_controller=controller)
        execute_tool(START_PROCEDURE_TOOL_NAME,{"procedure_id":"demo"},start_context)
        session_id=controller.attached_session_id
        negatives=(
            "아직 현재 단계를 완료하지 않았습니다",
            "현재 단계를 완료하면 어떻게 되나요",
            "현재 단계를 완료하지 마세요",
            "다음 단계가 무엇인가요",
            "1단계를 다시 설명해 주세요",
            "현재 단계를 완료했습니다 그리고 다음 단계도 완료해 주세요",
        )
        for transcript in negatives:
            before_row=self.store.get_session(session_id)
            before_events=self.store.list_events(session_id)
            authorized=authorized_completion_step_id(transcript,"ko",controller)
            forced=ToolContext(Path("catalog.sqlite"),"TEST","ko","test_only",
                               procedure_controller=controller,
                               procedure_completion_authorized_step_id=authorized)
            result=execute_tool(
                COMPLETE_CURRENT_STEP_TOOL_NAME,{"expected_step_id":"one"},forced)
            self.assertEqual(result["code"],"explicit_confirmation_required")
            self.assertEqual(self.store.get_session(session_id),before_row)
            self.assertEqual(self.store.list_events(session_id),before_events)
        for phrase in KOREAN_COMPLETION_PHRASES:
            self.assertEqual(
                authorized_completion_step_id(f"{phrase}.","ko",controller),
                "one",
            )
        authorized=authorized_completion_step_id(
            "현재 단계를 완료했습니다.","ko",controller)
        forced=ToolContext(Path("catalog.sqlite"),"TEST","ko","test_only",
                           procedure_controller=controller,
                           procedure_completion_authorized_step_id=authorized)
        result=execute_tool(
            COMPLETE_CURRENT_STEP_TOOL_NAME,{"expected_step_id":"one"},forced)
        self.assertEqual(result["state"]["current_step_id"],"two")
        self.assertEqual(len([event for event in self.store.list_events(session_id)
                              if event["event_type"]=="step_completed"]),1)

    def test_timer_start_phrases_bind_only_to_latest_current_step(self):
        korean=workflow_approved()
        korean=ProcedureDefinition(
            korean.schema_version,korean.procedure_id,korean.title,korean.version,
            korean.facility_id,"ko",korean.approval_status,korean.usage_scope,
            korean.active,korean.document_id,korean.document_version,"ko",
            korean.document_source,korean.steps)
        controller=ProcedureController({"workflow":korean},self.store)
        context=ToolContext(
            Path("catalog.sqlite"),"TEST","ko","test_only",
            procedure_controller=controller)
        execute_tool(
            START_PROCEDURE_TOOL_NAME,{"procedure_id":"workflow"},context)
        self.assertIsNone(authorized_timer_start_step_id(
            "타이머가 얼마나 남았나요?","ko",controller))
        self.assertIsNone(authorized_timer_start_step_id(
            "고정 타이머를 시작해줘 그리고 단계를 완료해줘","ko",controller))
        for phrase in KOREAN_TIMER_START_PHRASES:
            self.assertEqual(
                authorized_timer_start_step_id(f"{phrase}.","ko",controller),
                "observe",
            )

    def test_korean_observation_grammar_is_schema_bound_and_fail_closed(self):
        definition=workflow_approved()
        controller=ProcedureController({"workflow":definition},self.store)
        context=ToolContext(
            Path("catalog.sqlite"),"TEST","en","test_only",
            procedure_controller=controller)
        execute_tool(
            START_PROCEDURE_TOOL_NAME,{"procedure_id":"workflow"},context)

        accepted={
            "가상 라벨은 A-170이야.":"A-170",
            "가상 표시창은 빨간색이야.":"빨간색",
            "가상 표시창 색상은 빨간색이야.":"빨간색",
            "가상 표시창 색깔은 빨간색이야.":"빨간색",
        }
        for transcript,value in accepted.items():
            with self.subTest(transcript=transcript):
                self.assertEqual(
                    authorized_observation_arguments(
                        transcript,"ko",controller),
                    {"expected_step_id":"observe","value":value},
                )

        rejected=(
            "가상 표시창은 빨간색이야?",
            "가상 표시창은 빨간색인 것 같아.",
            "오늘 본 색은 빨간색이야.",
            "빨간색이야.",
            "가상 표시창은 빨간색이야. 보고서를 만들어 주세요.",
            "도와줘, 가상 표시창은 빨간색이야.",
            "가상 표시창은 빨간색이고 라벨은 A-170이야.",
            "가상 표시창은 빨간색 이야.",
        )
        for transcript in rejected:
            with self.subTest(transcript=transcript):
                self.assertIsNone(
                    authorized_observation_arguments(
                        transcript,"ko",controller))

        unattached=ProcedureController({"workflow":definition},self.store)
        self.assertIsNone(authorized_observation_arguments(
            "가상 표시창은 빨간색이야.","ko",unattached))

        no_schema=ProcedureController({"demo":approved()},self.store)
        no_schema_context=ToolContext(
            Path("catalog.sqlite"),"TEST","en","test_only",
            procedure_controller=no_schema)
        execute_tool(
            START_PROCEDURE_TOOL_NAME,{"procedure_id":"demo"},
            no_schema_context)
        self.assertIsNone(authorized_observation_arguments(
            "가상 표시창은 빨간색이야.","ko",no_schema))

        controller.block_for_handoff(
            "SR-20260722-A1B2C3","fictional observation")
        self.assertIsNone(authorized_observation_arguments(
            "가상 표시창은 빨간색이야.","ko",controller))

        one_step=ProcedureDefinition(
            definition.schema_version,definition.procedure_id+"-complete",
            definition.title,definition.version,definition.facility_id,
            definition.language,definition.approval_status,
            definition.usage_scope,definition.active,definition.document_id,
            definition.document_version,definition.document_language,
            definition.document_source,(definition.steps[0],),
        )
        completed=ProcedureController(
            {one_step.procedure_id:one_step},self.store)
        completed_context=ToolContext(
            Path("catalog.sqlite"),"TEST","en","test_only",
            procedure_controller=completed)
        execute_tool(
            START_PROCEDURE_TOOL_NAME,
            {"procedure_id":one_step.procedure_id},completed_context)
        completed.record_observation("observe","빨간색")
        completed.complete("observe")
        self.assertIsNone(authorized_observation_arguments(
            "가상 표시창은 빨간색이야.","ko",completed))

        class StaleController:
            def _attached(self):
                return definition,{"status":"active","current_step_index":0}
            def current(self):
                return {
                    "status":"success",
                    "state":{"status":"active","current_step_id":"wait"},
                }
        self.assertIsNone(authorized_observation_arguments(
            "가상 표시창은 빨간색이야.","ko",StaleController()))

    def test_required_observation_timer_and_audit_summary_gate_completion(self):
        now=[1000.0]
        controller=ProcedureController(
            {"workflow":workflow_approved()},self.store,clock=lambda:now[0])
        context=ToolContext(
            Path("catalog.sqlite"),"TEST","en","test_only",
            procedure_controller=controller)
        started=execute_tool(
            START_PROCEDURE_TOOL_NAME,{"procedure_id":"workflow"},context)
        self.assertEqual(started["state"]["observation"]["recorded_count"],0)

        authorized=ToolContext(
            Path("catalog.sqlite"),"TEST","en","test_only",
            procedure_controller=controller,
            procedure_completion_authorized_step_id="observe")
        self.assertEqual(
            execute_tool(
                COMPLETE_CURRENT_STEP_TOOL_NAME,
                {"expected_step_id":"observe"},authorized)["code"],
            "observation_required")
        recorded=execute_tool(
            RECORD_STEP_OBSERVATION_TOOL_NAME,
            {"expected_step_id":"observe","value":" red "},context)
        self.assertEqual(recorded["state"]["observation"]["latest_value"],"red")
        self.assertEqual(
            execute_tool(
                COMPLETE_CURRENT_STEP_TOOL_NAME,
                {"expected_step_id":"observe"},authorized)["state"]["current_step_id"],
            "wait")

        wait_authorized=ToolContext(
            Path("catalog.sqlite"),"TEST","en","test_only",
            procedure_controller=controller,
            procedure_completion_authorized_step_id="wait")
        self.assertEqual(
            execute_tool(
                COMPLETE_CURRENT_STEP_TOOL_NAME,
                {"expected_step_id":"wait"},wait_authorized)["code"],
            "timer_not_started")
        timer=execute_tool(
            START_STEP_TIMER_TOOL_NAME,{"expected_step_id":"wait"},context)
        self.assertEqual(timer["state"]["timer"]["remaining_seconds"],10)
        self.assertEqual(
            execute_tool(
                COMPLETE_CURRENT_STEP_TOOL_NAME,
                {"expected_step_id":"wait"},wait_authorized)["code"],
            "timer_not_elapsed")
        now[0]=1010.0
        completed=execute_tool(
            COMPLETE_CURRENT_STEP_TOOL_NAME,
            {"expected_step_id":"wait"},wait_authorized)
        self.assertEqual(completed["state"]["status"],"completed")
        summary=execute_tool(GET_WORKFLOW_SUMMARY_TOOL_NAME,{},context)
        self.assertEqual(len(summary["audit_summary"]["completed_steps"]),2)
        self.assertEqual(len(summary["audit_summary"]["observations"]),1)
        self.assertEqual(len(summary["audit_summary"]["timers"]),1)

    def test_observation_identifier_must_exactly_match_final_transcript(self):
        controller=ProcedureController({"workflow":workflow_approved()},self.store)
        base=dict(
            catalog_path=Path("catalog.sqlite"),
            facility_id="TEST",
            language="en",
            usage_scope="test_only",
            procedure_controller=controller,
        )
        execute_tool(
            START_PROCEDURE_TOOL_NAME,
            {"procedure_id":"workflow"},
            ToolContext(**base),
        )
        evidenced=ToolContext(
            **base,
            current_transcript="The fictional label is A-170.",
        )
        shortened=execute_tool(
            RECORD_STEP_OBSERVATION_TOOL_NAME,
            {"expected_step_id":"observe","value":"A-17"},
            evidenced,
        )
        self.assertEqual(shortened["code"],"observation_evidence_mismatch")
        self.assertEqual(
            self.store.list_observations(controller.attached_session_id),[])

        recorded=execute_tool(
            RECORD_STEP_OBSERVATION_TOOL_NAME,
            {"expected_step_id":"observe","value":"A-170"},
            evidenced,
        )
        self.assertEqual(recorded["status"],"success")
        self.assertEqual(
            recorded["state"]["observation"]["latest_value"],"A-170")

    def test_report_context_blocks_workflow_and_prevents_advancement(self):
        controller=ProcedureController({"workflow":workflow_approved()},self.store)
        context=ToolContext(
            Path("catalog.sqlite"),"TEST","en","test_only",
            procedure_controller=controller)
        execute_tool(
            START_PROCEDURE_TOOL_NAME,{"procedure_id":"workflow"},context)
        report_context=controller.report_context()
        self.assertEqual(report_context["step_id"],"observe")
        blocked=controller.block_for_handoff(
            "SR-20260722-A1B2C3","fictional red display")
        self.assertEqual(blocked["state"]["status"],"blocked_for_handoff")
        self.assertEqual(
            blocked["state"]["handoff"]["report_id"],"SR-20260722-A1B2C3")
        self.assertEqual(
            controller.record_observation("observe","green")["code"],
            "procedure_blocked_for_handoff")
        self.assertEqual(
            controller.complete("observe")["code"],
            "procedure_blocked_for_handoff")
        summary=controller.summary()
        self.assertEqual(
            summary["audit_summary"]["handoff"]["report_id"],
            "SR-20260722-A1B2C3")
