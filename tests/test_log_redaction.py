"""Logs keep the size of what people typed, never the words.

uvicorn's own access and WebSocket lines are checked through a real uvicorn
serving a stand-in app on 127.0.0.1, so the records are the ones uvicorn
actually emits; the stand-in keeps the run off the product's routes and data.
Importing voiney_lab.server installs the filter, exactly as uvicorn's import
of the app does. Nothing here needs the licensed Candidate A PDF.
"""

import hashlib
import io
import json
import logging
import tempfile
import threading
import time
import unittest
import warnings
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import httpx
import uvicorn
from uvicorn.logging import AccessFormatter, DefaultFormatter
from websockets.sync.client import connect

import voiney_lab.server  # noqa: F401  (installs the uvicorn log filter)
from voiney_lab.worker import process_once

SEARCH = "용매 누출"
FILENAME = "김교수_시료목록.pdf"
DEV_PROFILE = "lab-member-kim"


async def _stand_in_app(scope, receive, send):
    if scope["type"] == "http":
        message = await receive()
        while message.get("more_body"):
            message = await receive()
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
        await send({"type": "http.response.body", "body": b"ok"})
    elif scope["type"] == "websocket":
        await receive()
        await send({"type": "websocket.accept"})
        await send({"type": "websocket.close", "code": 1000})


class _CapturedUvicornLines:
    """Collect what uvicorn's two request-line loggers print, with uvicorn's own formatters."""

    def __init__(self, access_formatter=None, error_formatter=None):
        self.stream = io.StringIO()
        self.formatters = {
            "uvicorn.access": access_formatter
            or AccessFormatter('%(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s', use_colors=False),
            "uvicorn.error": error_formatter or DefaultFormatter("%(levelprefix)s %(message)s", use_colors=False),
        }

    def __enter__(self):
        self.saved = []
        for name, formatter in self.formatters.items():
            logger = logging.getLogger(name)
            handler = logging.StreamHandler(self.stream)
            handler.setFormatter(formatter)
            self.saved.append((logger, handler, logger.level))
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
        return self

    def __exit__(self, *exc):
        for logger, handler, level in self.saved:
            logger.removeHandler(handler)
            logger.setLevel(level)

    @property
    def text(self) -> str:
        return self.stream.getvalue()


class UvicornRequestLineTests(unittest.TestCase):
    def _serve(self, http: str, ws: str, client) -> None:
        config = uvicorn.Config(
            _stand_in_app, host="127.0.0.1", port=0, http=http, ws=ws,
            lifespan="off", log_config=None, access_log=True,
        )
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started:
                if time.monotonic() > deadline or not thread.is_alive():
                    self.fail("uvicorn did not start")
                time.sleep(0.01)
            client(server.servers[0].sockets[0].getsockname()[1])
        finally:
            server.should_exit = True
            thread.join(timeout=10)
        self.assertFalse(thread.is_alive())

    def test_a_real_uvicorn_logs_each_query_value_by_its_length(self):
        def client(port: int) -> None:
            base = f"http://127.0.0.1:{port}"
            httpx.get(f"{base}/api/workspace/protocols", params={"search": SEARCH, "page": "2"}, timeout=5)
            httpx.post(f"{base}/api/protocol-pdfs", params={"filename": FILENAME}, content=b"%PDF-", timeout=5)
            httpx.get(f"{base}/static/app.css", timeout=5)
            with connect(f"ws://127.0.0.1:{port}/ws?dev_profile={DEV_PROFILE}&lang=ko", open_timeout=5):
                pass

        for http in ("h11", "httptools"):
            for ws in ("websockets", "websockets-sansio"):
                with self.subTest(http=http, ws=ws), warnings.catch_warnings():
                    # uvicorn and websockets deprecate the legacy "websockets"
                    # implementation, which "--ws websockets" still selects.
                    warnings.simplefilter("ignore", DeprecationWarning)
                    warnings.filterwarnings("ignore", message="The `websockets` implementation is deprecated")
                    with _CapturedUvicornLines() as captured:
                        self._serve(http, ws, client)
                    text = captured.text
                    for typed in (SEARCH, "용매", FILENAME, "김교수", DEV_PROFILE):
                        self.assertNotIn(typed, text)
                        self.assertNotIn(quote(typed), text)
                    self.assertIn(
                        f'"GET /api/workspace/protocols?search=<{len(SEARCH)} chars>&page=<1 chars> HTTP/1.1" 200',
                        text,
                    )
                    self.assertIn(
                        f'"POST /api/protocol-pdfs?filename=<{len(FILENAME)} chars> HTTP/1.1" 200', text
                    )
                    # A path with no query is logged as it was.
                    self.assertIn('"GET /static/app.css HTTP/1.1" 200', text)
                    self.assertIn(
                        f'"WebSocket /ws?dev_profile=<{len(DEV_PROFILE)} chars>&lang=<2 chars>" [accepted]', text
                    )

    def test_a_record_in_another_shape_loses_its_whole_query(self):
        plain = logging.Formatter("%(message)s")
        with _CapturedUvicornLines(access_formatter=plain, error_formatter=plain) as captured:
            access = logging.getLogger("uvicorn.access")
            access.info("%s %s", "GET", f"/api/workspace/protocols?search={quote(SEARCH)}")
            # The usual line, but a key that is not a plain name.
            access.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:5000", "GET", f"/p?{quote(SEARCH)}=1", "1.1", 200)
            # The query split between the format and its arguments.
            access.info("%s?%s", "/api/workspace/protocols", f"search={quote(SEARCH)}")
            error = logging.getLogger("uvicorn.error")
            error.info("opening %s", f"/ws?dev_profile={DEV_PROFILE}")
            error.info(f"GET /x?filename={quote(FILENAME)} failed")
        lines = captured.text.splitlines()
        self.assertEqual(
            lines,
            [
                "GET /api/workspace/protocols?<query removed>",
                '127.0.0.1:5000 - "GET /p?<query removed> HTTP/1.1" 200',
                "/api/workspace/protocols?<query removed>",
                "opening /ws?<query removed>",
                "GET /x?<query removed> failed",
            ],
        )


class _FakeCompletions:
    def create(self, **kwargs):
        report = json.loads(kwargs["messages"][1]["content"])
        text = f"보고 {report['id']}를 관리자에게 인계합니다. Voice Workflow Agent 자동 인계"
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


class WorkerLogTests(unittest.TestCase):
    def test_the_report_location_reaches_the_log_as_size_and_digest(self):
        location = "3층 김교수 연구실 흄후드 앞"
        reports = [
            {"id": "SR-20261001-AAAAAA", "location": location, "summary": "reported issue",
             "urgency": "urgent", "exposure_status": "unknown", "language": "ko", "filed_at_epoch": 1},
            # An urgency outside the known set is not copied into the log either.
            {"id": "SR-20261001-BBBBBB", "location": location, "summary": "reported issue",
             "urgency": "누가 다쳤어요", "exposure_status": "unknown", "language": "ko", "filed_at_epoch": 2},
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inbox = root / "reports" / "inbox.jsonl"
            inbox.parent.mkdir(parents=True)
            inbox.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in reports), encoding="utf-8")
            with self.assertLogs("voiney_lab.worker", "INFO") as captured:
                handled = process_once(
                    inbox_path=inbox,
                    processed_path=root / "reports" / "processed.txt",
                    status_dir=root / "reports" / "status",
                    outbox_dir=root / "outbox",
                    client=SimpleNamespace(chat=SimpleNamespace(completions=_FakeCompletions())),
                )
        self.assertEqual(handled, 2)
        logged = "\n".join(captured.output)
        for typed in (location, "김교수", "누가 다쳤어요"):
            self.assertNotIn(typed, logged)
        digest = hashlib.sha256(location.encode("utf-8")).hexdigest()[:16]
        self.assertIn(
            f"processing SR-20261001-AAAAAA (location_chars={len(location)} location_sha256={digest}, urgent)",
            logged,
        )
        self.assertIn(
            f"processing SR-20261001-BBBBBB (location_chars={len(location)} location_sha256={digest}, unknown_urgency)",
            logged,
        )


if __name__ == "__main__":
    unittest.main()
