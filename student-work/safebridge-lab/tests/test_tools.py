import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import (
    CHECK_REPORT_TOOL,
    CREATE_REPORT_TOOL,
    SEARCH_TOOL,
    TOOLS,
    check_safety_report_status,
    create_safety_report,
    execute_tool,
    search_approved_safety_manual,
)


class ToolTests(unittest.TestCase):
    def test_all_schemas_are_strict_and_registered(self):
        self.assertEqual(len(TOOLS), 3)
        by_name = {tool["function"]["name"]: tool["function"] for tool in TOOLS}
        self.assertEqual(
            set(by_name),
            {
                "search_approved_safety_manual",
                "create_safety_report",
                "check_safety_report_status",
            },
        )
        for function in by_name.values():
            self.assertFalse(function["parameters"]["additionalProperties"])
        self.assertEqual(
            SEARCH_TOOL["function"]["parameters"]["properties"]["language"]["enum"],
            ["ko", "vi"],
        )
        self.assertEqual(
            set(CREATE_REPORT_TOOL["function"]["parameters"]["required"]),
            {"location", "summary", "urgency", "exposure_status", "language"},
        )
        self.assertEqual(
            CHECK_REPORT_TOOL["function"]["parameters"]["required"], ["report_id"]
        )

    def test_bilingual_search_success_and_safe_shape(self):
        for query, language in (("화학 누출", "ko"), ("máy thiết bị", "vi")):
            result = search_approved_safety_manual(query, language)
            self.assertEqual(result["status"], "success")
            self.assertLessEqual(len(result["matches"]), 3)
            self.assertEqual(
                set(result["matches"][0]),
                {
                    "document_id",
                    "title",
                    "section",
                    "guidance",
                    "source_label",
                    "demo_only",
                },
            )
            self.assertTrue(result["matches"][0]["demo_only"])

    def test_search_miss_invalid_unknown_and_error_are_structured(self):
        self.assertEqual(search_approved_safety_manual("zzzzzz", "ko")["status"], "not_found")
        self.assertEqual(search_approved_safety_manual("", "ko")["status"], "invalid_arguments")
        self.assertEqual(execute_tool("bad", {})["status"], "invalid_arguments")
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(
                search_approved_safety_manual("누출", "ko", Path(directory) / "missing")[
                    "status"
                ],
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
            with patch("tools._new_report_id", return_value="SR-20260722-A1B2C3"):
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
            with patch("tools._new_report_id", return_value="SR-20260722-A1B2C3"):
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
