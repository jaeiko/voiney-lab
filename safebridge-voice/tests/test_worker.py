import json
import tempfile
import unittest
from email import policy
from email.parser import BytesParser
from pathlib import Path
from types import SimpleNamespace

from safebridge_voice.worker import MAX_ATTEMPTS, load_status, pending_reports, process_once


class FakeCompletions:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        if self.fail:
            raise RuntimeError("temporary model failure")
        report = json.loads(kwargs["messages"][1]["content"])
        text = f"보고 {report['id']}를 관리자에게 인계합니다. SafeBridge Voice 자동 인계"
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=text))]
        )


class FakeClient:
    def __init__(self, fail: bool = False):
        self.completions = FakeCompletions(fail=fail)
        self.chat = SimpleNamespace(completions=self.completions)


def report(report_id: str, urgency: str, filed_at: float) -> dict:
    return {
        "id": report_id,
        "location": "Lab A",
        "summary": "reported issue",
        "urgency": urgency,
        "exposure_status": "unknown",
        "language": "ko",
        "filed_at_epoch": filed_at,
    }


class WorkerTests(unittest.TestCase):
    def paths(self, root: Path):
        return (
            root / "reports" / "inbox.jsonl",
            root / "reports" / "processed.txt",
            root / "reports" / "status",
            root / "outbox",
        )

    def write_reports(self, path: Path, values: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "".join(json.dumps(value) + "\n" for value in values), encoding="utf-8"
        )

    def test_worker_prioritizes_emergency_and_completes_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inbox, processed, status_dir, outbox = self.paths(root)
            routine = report("SR-20260722-AAAAAA", "routine", 1)
            emergency = report("SR-20260722-BBBBBB", "emergency", 2)
            self.write_reports(inbox, [routine, emergency])
            client = FakeClient()

            handled = process_once(
                inbox_path=inbox,
                processed_path=processed,
                status_dir=status_dir,
                outbox_dir=outbox,
                client=client,
            )

            self.assertEqual(handled, 2)
            requested_ids = [
                json.loads(call["messages"][1]["content"])["id"]
                for call in client.completions.requests
            ]
            self.assertEqual(requested_ids, [emergency["id"], routine["id"]])
            self.assertEqual(
                set(processed.read_text(encoding="utf-8").split()),
                {routine["id"], emergency["id"]},
            )
            for value in (routine, emergency):
                self.assertTrue((outbox / f"{value['id']}.eml").exists())
                self.assertEqual(load_status(value["id"], status_dir)["state"], "handoff_ready")
            parsed = BytesParser(policy=policy.default).parsebytes(
                (outbox / f"{emergency['id']}.eml").read_bytes()
            )
            self.assertIn("[긴급]", parsed["Subject"])

    def test_worker_receives_linked_workflow_context(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inbox, processed, status_dir, outbox = self.paths(root)
            value = report("SR-20260722-DDDDDD", "urgent", 1)
            value["workflow"] = {
                "workflow_session_id": "workflow-1",
                "procedure_id": "fictional-demo",
                "procedure_title": "FICTIONAL NON-OPERATIONAL Demo",
                "procedure_version": "2.0",
                "step_id": "observe",
                "step_number": 3,
                "step_title": "가상 표시창 관찰",
                "latest_observation": {"label": "색상", "value": "빨간색"},
            }
            self.write_reports(inbox, [value])
            client = FakeClient()

            self.assertEqual(
                process_once(
                    inbox_path=inbox,
                    processed_path=processed,
                    status_dir=status_dir,
                    outbox_dir=outbox,
                    client=client,
                ),
                1,
            )

            payload = json.loads(
                client.completions.requests[0]["messages"][1]["content"]
            )
            self.assertEqual(payload["workflow"]["step_id"], "observe")
            self.assertEqual(
                payload["workflow"]["latest_observation"]["value"], "빨간색"
            )

    def test_failure_retries_three_times_then_stops(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inbox, processed, status_dir, outbox = self.paths(root)
            value = report("SR-20260722-CCCCCC", "urgent", 1)
            self.write_reports(inbox, [value])
            client = FakeClient(fail=True)

            for attempt in range(1, MAX_ATTEMPTS + 1):
                self.assertEqual(
                    process_once(
                        inbox_path=inbox,
                        processed_path=processed,
                        status_dir=status_dir,
                        outbox_dir=outbox,
                        client=client,
                    ),
                    0,
                )
                status = load_status(value["id"], status_dir)
                self.assertEqual(status["attempts"], attempt)
                expected = "failed" if attempt == MAX_ATTEMPTS else "retry_pending"
                self.assertEqual(status["state"], expected)

            self.assertEqual(pending_reports(inbox, processed, status_dir), [])
            self.assertFalse(processed.exists())
            self.assertFalse((outbox / f"{value['id']}.eml").exists())


if __name__ == "__main__":
    unittest.main()
