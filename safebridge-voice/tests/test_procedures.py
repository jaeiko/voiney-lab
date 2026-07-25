import tempfile
import unittest
from pathlib import Path

from safebridge_voice.procedure_definitions import (
    ProcedureDefinition, ProcedureStep, SourceReference,
)
from safebridge_voice.procedure_store import ProcedureStore
from safebridge_voice.procedures import (
    KOREAN_COMPLETION_PHRASES, ProcedureController, authorized_completion_step_id,
)
from safebridge_voice.tools import (
    COMPLETE_CURRENT_STEP_TOOL_NAME, GET_CURRENT_STEP_TOOL_NAME,
    START_PROCEDURE_TOOL_NAME, ToolContext, execute_tool,
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
        self.assertEqual(KOREAN_COMPLETION_PHRASES,{
            "현재 단계를 완료했습니다","이 단계를 완료했습니다","현재 단계 완료했습니다"})
        authorized=authorized_completion_step_id("현재 단계를 완료했습니다.","ko",controller)
        forced=ToolContext(Path("catalog.sqlite"),"TEST","ko","test_only",
                           procedure_controller=controller,
                           procedure_completion_authorized_step_id=authorized)
        result=execute_tool(
            COMPLETE_CURRENT_STEP_TOOL_NAME,{"expected_step_id":"one"},forced)
        self.assertEqual(result["state"]["current_step_id"],"two")
        self.assertEqual(len([event for event in self.store.list_events(session_id)
                              if event["event_type"]=="step_completed"]),1)
