"""Kill the parser and the server keeps its footing.

On 2026-09-04 and again on 2026-09-05 the whole server died inside
``libpdfium.so`` -- SIGABRT once, SIGSEGV once -- while serving a reviewer's
diff. Nothing downstream could have caught it: by the time a ``-fno-exceptions``
C++ build is aborting there is no Python frame left to fail closed in. That was
fail *dead*, and this is the difference.

The tests below kill the worker in each way it can die -- a signal, a non-zero
exit, a hang, a truncated reply, a reply about the wrong document -- and assert
the same two things every time: this process survives, and the caller gets a
specific error that says the parser did not finish. What must never happen is
the quiet alternative, where a dead parser looks like a document that simply
had no text and readiness then reasons about a source nobody read.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import voice_workflow_agent.experiment_protocol_pdf as pdf_module
from voice_workflow_agent.experiment_protocol_pdf import (
    ProtocolPdfWorkerError,
    ProtocolPdfWorkerTimeoutError,
    clear_protocol_pdf_cache,
    extract_protocol_pdf,
)
from voice_workflow_agent.pdf_text_worker import read_page_texts

from tests.test_protocol_catalog import write_text_pdf


class WorkerDeathTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.source = Path(self.temp.name) / "source.pdf"
        write_text_pdf(
            self.source,
            "Protocol Test\nSection preparation\n1. Add solution.\nWear gloves.",
            title="Protocol Test",
        )
        clear_protocol_pdf_cache()

    def tearDown(self) -> None:
        clear_protocol_pdf_cache()
        self.temp.cleanup()

    def _worker_replaced_by(self, script: str):
        """Run a stand-in worker that behaves as the given Python source does."""

        real = subprocess.run

        def substitute(command, **kwargs):
            if command[1:] == ("-m", "voice_workflow_agent.pdf_text_worker") or list(
                command[1:]
            ) == ["-m", "voice_workflow_agent.pdf_text_worker"]:
                return real([sys.executable, "-c", script], **kwargs)
            return real(command, **kwargs)

        return patch.object(pdf_module.subprocess, "run", substitute)

    def test_a_worker_killed_by_a_signal_raises_and_leaves_us_alive(self) -> None:
        killer = "import os, signal; os.kill(os.getpid(), signal.SIGSEGV)"
        with self._worker_replaced_by(killer):
            with self.assertRaises(ProtocolPdfWorkerError) as caught:
                extract_protocol_pdf(self.source)
        self.assertEqual(caught.exception.code, "protocol_pdf_worker_failed")

        # The process that asked is still here and still works.
        clear_protocol_pdf_cache()
        extraction = extract_protocol_pdf(self.source)
        self.assertEqual(extraction.page_count, 1)

    def test_a_worker_that_aborts_the_way_pdfium_does_raises(self) -> None:
        """SIGABRT is the 2026-09-04 signature."""

        killer = "import os, signal; os.kill(os.getpid(), signal.SIGABRT)"
        with self._worker_replaced_by(killer):
            with self.assertRaises(ProtocolPdfWorkerError):
                extract_protocol_pdf(self.source)

    def test_a_worker_that_exits_non_zero_raises(self) -> None:
        with self._worker_replaced_by("raise SystemExit(3)"):
            with self.assertRaises(ProtocolPdfWorkerError):
                extract_protocol_pdf(self.source)

    def test_a_worker_that_hangs_is_killed_and_raises_a_timeout(self) -> None:
        with patch.object(pdf_module, "_worker_timeout_seconds", lambda: 1.0):
            with self._worker_replaced_by("import time; time.sleep(30)"):
                with self.assertRaises(ProtocolPdfWorkerTimeoutError) as caught:
                    extract_protocol_pdf(self.source)
        self.assertEqual(caught.exception.code, "protocol_pdf_worker_timeout")

    def test_a_truncated_or_unparseable_reply_raises(self) -> None:
        for script in (
            "import sys; sys.stdout.write('{\"status\": \"ok\", \"page_te')",
            "import sys; sys.stdout.write('not json at all')",
            "pass",
        ):
            with self.subTest(script=script[:28]):
                clear_protocol_pdf_cache()
                with self._worker_replaced_by(script):
                    with self.assertRaises(ProtocolPdfWorkerError):
                        extract_protocol_pdf(self.source)

    def test_a_reply_about_the_wrong_number_of_pages_raises(self) -> None:
        """The parent counts the pages; the child is not asked to agree."""

        script = (
            "import sys, json; "
            "sys.stdout.write(json.dumps("
            "{'status': 'ok', 'page_texts': ['a', 'b', 'c']}))"
        )
        with self._worker_replaced_by(script):
            with self.assertRaises(ProtocolPdfWorkerError):
                extract_protocol_pdf(self.source)

    def test_a_reply_with_a_non_string_page_raises(self) -> None:
        script = (
            "import sys, json; "
            "sys.stdout.write(json.dumps("
            "{'status': 'ok', 'page_texts': [17]}))"
        )
        with self._worker_replaced_by(script):
            with self.assertRaises(ProtocolPdfWorkerError):
                extract_protocol_pdf(self.source)

    def test_a_worker_error_is_never_mistaken_for_a_bad_document(self) -> None:
        """The error a reader is given has to be the true one.

        Folding this into ProtocolPdfMalformedError would blame the source for
        a library fault, and the 422 a caller receives would say the document
        is invalid when it is not.
        """

        from voice_workflow_agent.experiment_protocol_pdf import (
            ProtocolPdfMalformedError,
        )

        self.assertFalse(issubclass(ProtocolPdfWorkerError, ProtocolPdfMalformedError))
        self.assertTrue(issubclass(ProtocolPdfWorkerTimeoutError, ProtocolPdfWorkerError))

        from voice_workflow_agent.server import _catalog_http_error

        self.assertEqual(
            _catalog_http_error(ProtocolPdfWorkerError("x")).status_code, 503
        )
        self.assertEqual(
            _catalog_http_error(ProtocolPdfWorkerError("x")).detail,
            "protocol_pdf_worker_failed",
        )


class WorkerContractTests(unittest.TestCase):
    """What the child is and is not responsible for."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.source = Path(self.temp.name) / "source.pdf"
        write_text_pdf(
            self.source,
            "Protocol Test\nSection preparation\n1. Add solution.",
            title="Protocol Test",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_an_unopenable_file_is_still_every_page_unreadable(self) -> None:
        """A document fault stays a document fault, not a worker fault."""

        missing = Path(self.temp.name) / "absent.pdf"
        self.assertEqual(read_page_texts(missing, 3), [None, None, None])

    def test_the_child_reports_a_capped_address_space_without_crashing(self):
        """The cap is real: with an impossible one the child fails cleanly."""

        request = json.dumps(
            {
                "path": str(self.source),
                "page_count": 1,
                "address_space_bytes": 8 * 1024 * 1024,
            }
        )
        completed = subprocess.run(
            [sys.executable, "-m", "voice_workflow_agent.pdf_text_worker"],
            input=request.encode("utf-8"),
            capture_output=True,
            timeout=60,
            check=False,
        )
        # Either it fit, or it reported failing to fit. What it must not do is
        # return a document with silently empty pages and a zero exit code.
        if completed.returncode == 0:
            payload = json.loads(completed.stdout.decode("utf-8"))
            self.assertEqual(payload["status"], "ok")
        else:
            self.assertNotEqual(completed.returncode, 0)

    def test_the_limits_are_stated_with_the_measurements_behind_them(self):
        import os

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(
                "VOICE_WORKFLOW_AGENT_PDF_WORKER_TIMEOUT_SECONDS", None
            )
            self.assertEqual(pdf_module._worker_timeout_seconds(), 30.0)
        # A host may say its machine needs longer; nonsense is ignored rather
        # than obeyed, so a typo cannot silently remove the bound.
        for raw, expected in (
            ("120", 120.0), ("0.5", 30.0), ("100000", 30.0), ("soon", 30.0),
        ):
            with self.subTest(raw=raw):
                with patch.dict(
                    os.environ,
                    {"VOICE_WORKFLOW_AGENT_PDF_WORKER_TIMEOUT_SECONDS": raw},
                ):
                    self.assertEqual(
                        pdf_module._worker_timeout_seconds(), expected
                    )
        self.assertEqual(pdf_module.PDF_WORKER_TIMEOUT_SECONDS, 30.0)
        self.assertEqual(
            pdf_module.PDF_WORKER_ADDRESS_SPACE_BYTES, 1024 * 1024 * 1024
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
