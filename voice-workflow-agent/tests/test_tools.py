import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from voice_workflow_agent.tools import (
    CHECK_REPORT_TOOL,
    CREATE_REPORT_TOOL,
    SEARCH_TOOL,
    TOOLS,
    ToolContext,
    check_safety_report_status,
    create_safety_report,
    execute_tool,
    search_approved_safety_manual,
)
from voice_workflow_agent.document_store import ingest_manifest
from tests.test_retrieval import operational_document


class ToolTests(unittest.TestCase):
    def test_all_schemas_are_strict_and_registered(self):
        self.assertEqual(len(TOOLS), 9)
        by_name = {tool["function"]["name"]: tool["function"] for tool in TOOLS}
        self.assertEqual(
            set(by_name),
            {
                "search_approved_safety_manual",
                "create_safety_report",
                "check_safety_report_status",
                "start_procedure",
                "get_current_step",
                "complete_current_step",
                "record_step_observation",
                "start_step_timer",
                "get_workflow_summary",
            },
        )
        for function in by_name.values():
            self.assertFalse(function["parameters"]["additionalProperties"])
        self.assertEqual(set(SEARCH_TOOL["function"]["parameters"]["properties"]), {"query", "topic"})
        self.assertEqual(set(SEARCH_TOOL["function"]["parameters"]["required"]), {"query", "topic"})
        self.assertEqual(
            set(CREATE_REPORT_TOOL["function"]["parameters"]["required"]),
            {"location", "summary", "urgency", "exposure_status", "language"},
        )
        self.assertEqual(
            CHECK_REPORT_TOOL["function"]["parameters"]["required"], ["report_id"]
        )

    def test_model_facing_descriptions_define_selection_and_safety_contracts(self):
        by_name = {tool["function"]["name"]: tool["function"] for tool in TOOLS}
        required_guidance = {
            "search_approved_safety_manual": (
                "approved local safety material",
                "only a successful, answerable result",
                "trusted server context",
            ),
            "create_safety_report": (
                "explicit user confirmation",
                "draft awaiting confirmation",
                "does not contact emergency services",
            ),
            "check_safety_report_status": (
                "previously submitted Voice Workflow Agent report",
                "read-only status check",
                "draft that has not been confirmed",
            ),
            "start_procedure": (
                "explicitly asks to begin",
                "trusted facility",
                "Do not claim that the workflow started",
            ),
            "get_current_step": (
                "read-only",
                "approved instruction",
                "does not start, complete, skip",
            ),
            "complete_current_step": (
                "explicitly confirms completion",
                "required observations",
                "blocked_for_handoff",
            ),
            "record_step_observation": (
                "current finalized user transcript",
                "Preserve every letter, digit",
                "does not complete the step",
            ),
            "start_step_timer": (
                "accepts no duration argument",
                "does not reset the deadline",
                "does not complete the step",
            ),
            "get_workflow_summary": (
                "server-owned audit summary",
                "not for retrieving new SOP or SDS facts",
                "accepts no model-supplied session",
            ),
        }
        for name, fragments in required_guidance.items():
            description = by_name[name]["description"]
            self.assertGreater(len(description), 300, name)
            for fragment in fragments:
                self.assertIn(fragment, description, name)
            for schema in by_name[name]["parameters"]["properties"].values():
                self.assertGreater(len(schema.get("description", "")), 40, name)

    def test_explicit_topic_is_required_and_validated(self):
        context = ToolContext(Path("unused.sqlite"), None, "en", "operational")
        for arguments in ({"query": "FICTIONAL"}, {"query": "FICTIONAL", "topic": "unsupported"}):
            result = execute_tool("search_approved_safety_manual", arguments, context)
            self.assertEqual(result, {"status": "invalid_arguments", "answerable": False, "matches": []})

    def test_sqlite_search_uses_trusted_context_and_safe_shape(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "catalog.sqlite"
            doc = operational_document(language="ko", aliases=[
                {"alias": "가상 용제", "language": "ko", "approved": True}
            ])
            ingest_manifest({"documents": [doc]}, db)
            context = ToolContext(db, "TEST-FACILITY", "ko", "operational")
            result = execute_tool("search_approved_safety_manual", {
                "query": "가상 용제", "topic": "first_aid"
            }, context)
            self.assertEqual(result["status"], "success")
            match = result["matches"][0]
            self.assertEqual(match["language"], "ko")
            self.assertEqual(match["section_code"], "SDS-04")
            self.assertNotIn("source_path", match)
            self.assertNotIn(str(db), json.dumps(result, ensure_ascii=False))

    def test_model_cannot_override_trusted_retrieval_context(self):
        context = ToolContext(Path("trusted.sqlite"), "TRUSTED", "ko", "operational")
        for field, value in (("language", "vi"), ("facility_id", "OTHER"),
                             ("usage_scope", "reference_only"), ("db_path", "other.sqlite")):
            result = execute_tool("search_approved_safety_manual", {"query": "x", field: value}, context)
            self.assertEqual(result["status"], "invalid_arguments")
            self.assertEqual(result.get("matches", []), [])

    def test_trusted_language_facility_and_scope_reach_retrieval(self):
        context = ToolContext(Path("trusted.sqlite"), "TRUSTED-FACILITY", "vi", "reference_only")
        with patch("voice_workflow_agent.retrieval.search_safety_documents",
                   return_value={"status":"not_found","answerable":False,"matches":[]}) as search:
            result = execute_tool("search_approved_safety_manual",
                                  {"query":"FICTIONAL","topic":"first_aid"}, context)
        self.assertEqual(result["status"], "not_found")
        search.assert_called_once_with("FICTIONAL", "vi", Path("trusted.sqlite"),
                                       usage_scope="reference_only", facility_id="TRUSTED-FACILITY",
                                       topic="first_aid")

    def test_search_miss_invalid_unknown_and_error_are_structured(self):
        self.assertEqual(search_approved_safety_manual("zzzzzz")["status"], "invalid_arguments")
        self.assertEqual(search_approved_safety_manual("")["status"], "invalid_arguments")
        self.assertEqual(execute_tool("bad", {})["status"], "invalid_arguments")
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(
                search_approved_safety_manual("누출", context=ToolContext(
                    Path(directory) / "missing", None, "ko", "operational"), topic="spill")["status"],
                "error",
            )

    def test_create_report_is_fast_queue_work_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            inbox = Path(directory) / "reports" / "inbox.jsonl"
            arguments = {
                "location": "  3층   유기화학실 후드 앞 ",
                "summary": "용액이 바닥으로 새고 있음",
                "urgency": "urgent",
                "exposure_status": "unknown",
                "language": "ko",
                "material_or_equipment": "아세톤",
            }
            with patch("voice_workflow_agent.tools._new_report_id", return_value="SR-20260722-A1B2C3"):
                first = create_safety_report(**arguments, inbox_path=inbox, now_epoch=1000)
                second = create_safety_report(**arguments, inbox_path=inbox, now_epoch=1001)
            self.assertEqual(first["status"], "success")
            self.assertFalse(first["deduplicated"])
            self.assertEqual(second["report_id"], first["report_id"])
            self.assertTrue(second["deduplicated"])
            lines = inbox.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            queued = json.loads(lines[0])
            self.assertEqual(queued["location"], "3층 유기화학실 후드 앞")
            self.assertEqual(queued["material_or_equipment"], "아세톤")

    def test_report_can_carry_trusted_workflow_context(self):
        with tempfile.TemporaryDirectory() as directory:
            inbox=Path(directory)/"inbox.jsonl"
            workflow={
                "workflow_session_id":"workflow-1",
                "procedure_id":"fictional-demo",
                "step_id":"observe",
                "step_number":3,
            }
            with patch(
                "voice_workflow_agent.tools._new_report_id",
                return_value="SR-20260722-A1B2C3",
            ):
                result=create_safety_report(
                    "Lab A","reported anomaly","urgent","unknown","ko",
                    inbox_path=inbox,now_epoch=1000,workflow_context=workflow)
            self.assertEqual(result["status"],"success")
            queued=json.loads(inbox.read_text(encoding="utf-8"))
            self.assertEqual(queued["workflow"],workflow)

    def test_report_dispatch_links_and_blocks_attached_workflow(self):
        class Controller:
            def __init__(self): self.blocked=None
            def report_context(self):
                return {
                    "workflow_session_id":"workflow-1",
                    "procedure_id":"fictional-demo",
                    "step_id":"observe",
                }
            def block_for_handoff(self,report_id,reason):
                self.blocked=(report_id,reason)
                return {
                    "status":"success","operation":"block_for_handoff",
                    "idempotent":False,
                    "state":{
                        "attached":True,"status":"blocked_for_handoff",
                        "handoff":{"report_id":report_id},
                    },
                }
        controller=Controller()
        context=ToolContext(
            Path("catalog.sqlite"),"TEST","ko","test_only",
            procedure_controller=controller)
        arguments={
            "location":"Lab A","summary":"reported anomaly",
            "urgency":"urgent","exposure_status":"unknown","language":"ko",
        }
        with patch(
            "voice_workflow_agent.tools.create_safety_report",
            return_value={
                "status":"success","report_id":"SR-20260722-A1B2C3",
                "report_status":"queued_for_handoff",
            },
        ) as create:
            result=execute_tool("create_safety_report",arguments,context)
        self.assertEqual(
            create.call_args.kwargs["workflow_context"]["step_id"],"observe")
        self.assertEqual(
            controller.blocked,
            ("SR-20260722-A1B2C3","reported anomaly"))
        self.assertTrue(result["procedure_blocked"])
        self.assertEqual(
            result["procedure_state"]["status"],"blocked_for_handoff")

    def test_report_validation_and_exact_dispatch_arguments(self):
        base = {
            "location": "A lab",
            "summary": "spill",
            "urgency": "urgent",
            "exposure_status": "unknown",
            "language": "ko",
        }
        self.assertEqual(create_safety_report(**{**base, "urgency": "maybe"})["status"], "invalid_arguments")
        self.assertEqual(create_safety_report(**{**base, "location": ""})["status"], "invalid_arguments")
        self.assertEqual(execute_tool("create_safety_report", {**base, "extra": 1})["status"], "invalid_arguments")
        self.assertEqual(execute_tool("create_safety_report", {"summary": "x"})["status"], "invalid_arguments")

    def test_status_moves_from_queue_to_handoff_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inbox = root / "reports" / "inbox.jsonl"
            processed = root / "reports" / "processed.txt"
            status_dir = root / "reports" / "status"
            outbox = root / "outbox"
            with patch("voice_workflow_agent.tools._new_report_id", return_value="SR-20260722-A1B2C3"):
                created = create_safety_report(
                    "Lab A",
                    "small spill",
                    "routine",
                    "no",
                    "ko",
                    inbox_path=inbox,
                    now_epoch=1000,
                )
            queued = check_safety_report_status(
                created["report_id"],
                inbox_path=inbox,
                processed_path=processed,
                status_dir=status_dir,
                outbox_dir=outbox,
            )
            self.assertEqual(queued["report_status"], "queued_for_handoff")
            processed.write_text(created["report_id"] + "\n", encoding="utf-8")
            status_dir.mkdir()
            (status_dir / f"{created['report_id']}.json").write_text(
                json.dumps({"state": "handoff_ready", "attempts": 1}), encoding="utf-8"
            )
            outbox.mkdir()
            (outbox / f"{created['report_id']}.eml").write_text("demo", encoding="utf-8")
            done = check_safety_report_status(
                created["report_id"],
                inbox_path=inbox,
                processed_path=processed,
                status_dir=status_dir,
                outbox_dir=outbox,
            )
            self.assertEqual(done["report_status"], "handoff_ready")
            self.assertEqual(done["attempts"], 1)


if __name__ == "__main__":
    unittest.main()
