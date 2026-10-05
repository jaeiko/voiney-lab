"""The PDF child's address-space cap: always on Linux, waived only on macOS.

Decision of 2026-10-04. Linux (server, pilot labs, CI) caps the child's
address space exactly as before, and a refusal there still fails the request.
macOS is development only and refuses ``RLIMIT_AS`` below its unlimited
maximum with ``ValueError``; there alone the child reads without a memory cap.
The time limit and the output size limit are not touched by any of this.

The platform is swapped in with ``patch.object(sys, "platform", ...)`` so both
sides are checked on either host; the two ``RealHost`` tests then run the
helper in a fresh interpreter on the host they are actually on.
"""

from __future__ import annotations

import io
import json
import resource
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import voiney_lab.pdf_text_engine as engine_module
from voiney_lab.pdf_text_engine import PDF_WORKER_ADDRESS_SPACE_BYTES

from tests.test_protocol_catalog import write_text_pdf

LIMIT = PDF_WORKER_ADDRESS_SPACE_BYTES
REFUSALS = (ValueError("current limit exceeds maximum limit"), OSError(22, "Invalid argument"))


class CapByPlatformTests(unittest.TestCase):
    def test_linux_sets_the_cap_exactly_as_before(self) -> None:
        with patch.object(sys, "platform", "linux"), patch("resource.setrlimit") as setrlimit:
            self.assertTrue(engine_module._cap_address_space(LIMIT))
        setrlimit.assert_called_once_with(resource.RLIMIT_AS, (LIMIT, LIMIT))

    def test_linux_refusal_still_fails(self) -> None:
        for refusal in REFUSALS:
            with self.subTest(refusal=type(refusal).__name__):
                with patch.object(sys, "platform", "linux"), patch(
                    "resource.setrlimit", side_effect=refusal
                ):
                    with self.assertRaises(type(refusal)):
                        engine_module._cap_address_space(LIMIT)

    def test_darwin_refusal_goes_on_without_a_cap(self) -> None:
        for refusal in REFUSALS:
            with self.subTest(refusal=type(refusal).__name__):
                with patch.object(sys, "platform", "darwin"), patch(
                    "resource.setrlimit", side_effect=refusal
                ) as setrlimit:
                    self.assertFalse(engine_module._cap_address_space(LIMIT))
                # macOS is still asked for the cap first; it is not skipped.
                setrlimit.assert_called_once_with(resource.RLIMIT_AS, (LIMIT, LIMIT))

    def test_darwin_that_accepts_the_cap_keeps_it(self) -> None:
        with patch.object(sys, "platform", "darwin"), patch("resource.setrlimit"):
            self.assertTrue(engine_module._cap_address_space(LIMIT))

    def test_darwin_other_errors_still_fail(self) -> None:
        for error in (RuntimeError("boom"), MemoryError(), TypeError("bad")):
            with self.subTest(error=type(error).__name__):
                with patch.object(sys, "platform", "darwin"), patch(
                    "resource.setrlimit", side_effect=error
                ):
                    with self.assertRaises(type(error)):
                        engine_module._cap_address_space(LIMIT)

    def test_other_platforms_refusal_still_fails(self) -> None:
        for platform in ("freebsd14", "openbsd7", "cygwin", "aix"):
            with self.subTest(platform=platform):
                with patch.object(sys, "platform", platform), patch(
                    "resource.setrlimit", side_effect=ValueError("refused")
                ):
                    with self.assertRaises(ValueError):
                        engine_module._cap_address_space(LIMIT)


class ChildEntryPointTests(unittest.TestCase):
    """``main()`` in this process, with stdin/stdout swapped for the request."""

    def setUp(self) -> None:
        # Imported before sys.platform is swapped, so the library loads as
        # it would on this host.
        import pymupdf  # noqa: F401

        self.temp = tempfile.TemporaryDirectory()
        self.source = Path(self.temp.name) / "source.pdf"
        write_text_pdf(
            self.source,
            "Protocol Test\nSection preparation\n1. Add solution.\nWear gloves.",
            title="Protocol Test",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _run_main(self, platform: str, refusal: BaseException) -> tuple[int, dict]:
        request = json.dumps(
            {"operation": "read", "path": str(self.source), "address_space_bytes": LIMIT}
        )
        stdout = io.StringIO()
        with patch.object(sys, "platform", platform), patch(
            "resource.setrlimit", side_effect=refusal
        ), patch.object(sys, "stdin", io.StringIO(request)), patch.object(sys, "stdout", stdout):
            code = engine_module.main()
        return code, json.loads(stdout.getvalue())

    def test_darwin_reads_the_document_when_the_cap_is_refused(self) -> None:
        code, payload = self._run_main("darwin", ValueError("current limit exceeds maximum limit"))
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "ok")
        self.assertIn("Add solution.", "".join(page["text"] for page in payload["pages"]))

    def test_linux_fails_the_request_when_the_cap_is_refused(self) -> None:
        code, payload = self._run_main("linux", ValueError("current limit exceeds maximum limit"))
        self.assertEqual(code, 1)
        self.assertEqual(payload, {"status": "error", "error": "ValueError"})

    def test_darwin_other_error_fails_the_request(self) -> None:
        code, payload = self._run_main("darwin", RuntimeError("boom"))
        self.assertEqual(code, 1)
        self.assertEqual(payload, {"status": "error", "error": "RuntimeError"})


_PROBE = (
    "import resource, json\n"
    "from voiney_lab.pdf_text_engine import _cap_address_space\n"
    "applied = _cap_address_space({limit})\n"
    "print(json.dumps([applied, list(resource.getrlimit(resource.RLIMIT_AS))]))\n"
)


def _probe() -> tuple[bool, list[int]]:
    completed = subprocess.run(
        [sys.executable, "-c", _PROBE.format(limit=LIMIT)],
        capture_output=True,
        timeout=60,
        check=True,
    )
    applied, limits = json.loads(completed.stdout.decode("utf-8"))
    return applied, limits


class RealHostTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux host only")
    def test_linux_child_really_has_the_cap(self) -> None:
        applied, limits = _probe()
        self.assertTrue(applied)
        self.assertEqual(limits, [LIMIT, LIMIT])

    @unittest.skipUnless(sys.platform == "darwin", "macOS host only")
    def test_macos_child_goes_on_uncapped(self) -> None:
        applied, limits = _probe()
        self.assertFalse(applied)
        self.assertEqual(limits[0], resource.RLIM_INFINITY)


if __name__ == "__main__":
    unittest.main()
