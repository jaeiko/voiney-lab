"""pdfium runs in a child process, and nothing routes around that boundary.

pypdfium2 documents that pdfium is "inherently not thread-safe", that no two
pdfium calls may run at once -- "not even with different documents" -- and that
overlapping calls "crash or corrupt the process".  FastAPI runs a synchronous
endpoint in an AnyIO worker thread, so two reviewers opening the same protocol
overlapped two calls and the server died twice: SIGABRT on 2026-09-04 and
SIGSEGV on 2026-09-05, both inside ``libpdfium.so`` on an AnyIO worker thread.

STEP 23 serialized the calls, which removed the one cause there was evidence
for.  STEP 24 moved the parse out of the server process entirely, because two
crashes with two different signals is memory corruption and no amount of
serializing removes that class -- a build made ``-fno-exceptions`` cannot be
made to fail closed from inside.

Three things are pinned here.  pdfium is named in exactly one module, and that
module is the worker; the server process never imports it.  Every use inside
the worker sits under its lock, so whichever process ends up running that
function honours pdfium's "no two calls at once" rule.  And a worker that dies
produces a specific error rather than a document that appears to have no text.

The concurrency test measures the boundary and the lock, not a crash.  A
memory-safety fault reproduced once in more than a hundred attempts, so
asserting "no crash" would pass just as well without any fix.
"""

from __future__ import annotations

import ast
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import voice_workflow_agent.experiment_protocol_pdf as pdf_module
import voice_workflow_agent.pdf_text_worker as worker_module
from voice_workflow_agent.experiment_protocol_pdf import (
    ProtocolPdfWorkerError,
    ProtocolPdfWorkerTimeoutError,
    clear_protocol_pdf_cache,
    extract_protocol_pdf,
)

from tests.test_protocol_catalog import write_text_pdf

MODULE_PATH = Path(worker_module.__file__)
PACKAGE_ROOT = MODULE_PATH.parent
SERVER_SIDE_MODULE = Path(pdf_module.__file__)
LOCK_NAME = "_PDFIUM_LOCK"
LIBRARY_NAMES = {"pypdfium2", "pdfium"}


def _modules_naming_pdfium() -> set[str]:
    """Every module in the package that imports the pdfium binding."""

    naming = set()
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(alias.name.split(".")[0] in LIBRARY_NAMES
                       for alias in node.names):
                    naming.add(path.name)
            elif isinstance(node, ast.ImportFrom):
                if (node.module or "").split(".")[0] in LIBRARY_NAMES:
                    naming.add(path.name)
    return naming


class PdfiumIsNamedInOnePlaceTests(unittest.TestCase):
    def test_only_the_worker_module_imports_the_pdfium_binding(self) -> None:
        self.assertEqual(_modules_naming_pdfium(), {MODULE_PATH.name})

    def test_the_server_side_module_no_longer_names_pdfium(self) -> None:
        """The boundary is the point: the parser is not in this process."""

        tree = ast.parse(
            SERVER_SIDE_MODULE.read_text(), filename=str(SERVER_SIDE_MODULE)
        )
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                self.assertNotIn(node.id, LIBRARY_NAMES)

    def test_every_use_of_pdfium_sits_inside_the_lock(self) -> None:
        """Whichever process runs it, the calls are serialized."""

        tree = ast.parse(MODULE_PATH.read_text(), filename=str(MODULE_PATH))

        using: list[ast.FunctionDef] = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for inner in ast.walk(node):
                if isinstance(inner, ast.Name) and inner.id in LIBRARY_NAMES:
                    using.append(node)
                    break
        self.assertEqual(
            [node.name for node in using],
            ["read_page_texts"],
            "pdfium is used outside the one function the lock guards",
        )

        function = using[0]
        guarded = [
            statement for statement in function.body
            if isinstance(statement, ast.With)
            and any(
                isinstance(item.context_expr, ast.Name)
                and item.context_expr.id == LOCK_NAME
                for item in statement.items
            )
        ]
        self.assertEqual(len(guarded), 1, "the lock is not taken exactly once")
        held = guarded[0]
        for inner in ast.walk(function):
            if isinstance(inner, ast.Name) and inner.id in LIBRARY_NAMES:
                self.assertTrue(
                    held.lineno <= inner.lineno <= (held.end_lineno or 0),
                    f"a pdfium use on line {inner.lineno} is outside the lock",
                )

    def test_the_lock_is_a_process_wide_singleton(self) -> None:
        lock = getattr(worker_module, LOCK_NAME)
        self.assertIsInstance(lock, type(threading.RLock()))


class _ObservingLock:
    """A lock that records whether two holders ever overlapped."""

    def __init__(self) -> None:
        self._inner = threading.RLock()
        self._state = threading.Lock()
        self.holders = 0
        self.max_holders = 0
        self.acquisitions = 0
        self.contended = 0

    def __enter__(self):
        if not self._inner.acquire(blocking=False):
            with self._state:
                self.contended += 1
            self._inner.acquire()
        with self._state:
            self.acquisitions += 1
            self.holders += 1
            self.max_holders = max(self.max_holders, self.holders)
        return self

    def __exit__(self, *exc_info):
        with self._state:
            self.holders -= 1
        self._inner.release()
        return False


class ConcurrentExtractionIsSerializedTests(unittest.TestCase):
    """The reproduction shape from 2026-09-04, measured rather than hoped."""

    THREADS = 8
    CALLS = 24

    def setUp(self) -> None:
        import tempfile

        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.sources = []
        for index in range(4):
            path = root / f"source-{index}.pdf"
            write_text_pdf(
                path,
                f"Protocol {index}\nSection one\n1. Add {index} mL of buffer.",
                title=f"Protocol {index}",
            )
            self.sources.append(path)
        clear_protocol_pdf_cache()

    def tearDown(self) -> None:
        clear_protocol_pdf_cache()
        self.temp.cleanup()

    def test_in_process_calls_to_the_worker_function_are_serialized(self):
        """The lock still matters wherever that function is called directly.

        In the server the boundary makes overlap impossible -- each parse is
        its own process -- but the function is importable, and pdfium's rule
        has to hold for whoever calls it. This calls it the way an in-process
        caller would and measures the lock.
        """

        observer = _ObservingLock()
        errors: list[BaseException] = []
        pages: list[int] = []
        barrier = threading.Barrier(self.THREADS)

        def read(index: int) -> None:
            source = self.sources[index % len(self.sources)]
            try:
                barrier.wait(timeout=30)
                for _ in range(self.CALLS // self.THREADS):
                    texts = worker_module.read_page_texts(source, 1)
                    pages.append(len(texts))
            except BaseException as error:  # noqa: BLE001 - reported
                errors.append(error)

        with unittest.mock.patch.object(worker_module, LOCK_NAME, observer):
            with ThreadPoolExecutor(max_workers=self.THREADS) as pool:
                list(pool.map(read, range(self.THREADS)))

        self.assertEqual(errors, [])
        self.assertGreaterEqual(observer.acquisitions, self.CALLS)
        self.assertEqual(
            observer.max_holders,
            1,
            "two threads held the pdfium lock at the same time",
        )
        self.assertGreater(
            observer.contended,
            0,
            "no thread ever waited, so this run did not exercise concurrency",
        )
        self.assertEqual(set(pages), {1})

    def test_each_extraction_gets_its_own_worker_process(self):
        """Isolation is per document: a damaged heap cannot reach the next one."""

        import subprocess

        real = subprocess.run
        started: list[tuple] = []

        def counting_run(command, **kwargs):
            started.append(tuple(command))
            return real(command, **kwargs)

        with unittest.mock.patch.object(
            pdf_module.subprocess, "run", counting_run
        ):
            for source in self.sources:
                clear_protocol_pdf_cache()
                extract_protocol_pdf(source)

        # pdftotext, the independent cross-check engine, is also a
        # subprocess; only the pdfium worker is counted here.
        workers = [
            command for command in started
            if command[1:] == ("-m", "voice_workflow_agent.pdf_text_worker")
        ]
        self.assertEqual(len(workers), len(self.sources))
        self.assertEqual(
            {command[0] for command in workers}, {pdf_module.sys.executable}
        )

    def test_concurrent_extraction_returns_what_serial_extraction_returns(self):
        """Corruption need not crash; it can also hand back wrong text.

        Nothing can prove text is uncorrupted, but a difference between the
        serial and concurrent readings of the same bytes would be visible, and
        that is the failure mode this asserts against.
        """

        clear_protocol_pdf_cache()
        serial = {
            source.name: tuple(
                page.text for page in extract_protocol_pdf(source).pages
            )
            for source in self.sources
        }

        concurrent: dict[str, tuple[str, ...]] = {}
        guard = threading.Lock()
        barrier = threading.Barrier(self.THREADS)

        def extract(index: int) -> None:
            source = self.sources[index % len(self.sources)]
            barrier.wait(timeout=30)
            clear_protocol_pdf_cache()
            pages = tuple(
                page.text for page in extract_protocol_pdf(source).pages
            )
            with guard:
                previous = concurrent.setdefault(source.name, pages)
                assert previous == pages, source.name

        with ThreadPoolExecutor(max_workers=self.THREADS) as pool:
            list(pool.map(extract, range(self.THREADS)))

        self.assertEqual(concurrent, serial)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
