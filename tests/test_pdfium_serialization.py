"""Every pdfium call in this process is serialized, and nothing routes around it.

pypdfium2 documents that pdfium is "inherently not thread-safe", that no two
pdfium calls may run at once -- "not even with different documents" -- and that
overlapping calls "crash or corrupt the process".  FastAPI runs a synchronous
endpoint in an AnyIO worker thread, so two reviewers opening the same protocol
overlapped two calls and the server died twice: SIGABRT on 2026-09-04 and
SIGSEGV on 2026-09-05, both inside ``libpdfium.so`` on an AnyIO worker thread.

Two things are pinned here.  The first is structural, derived from the module's
own syntax tree rather than from a grep: pdfium is named in exactly one module,
inside exactly one function, and that function's body lies entirely inside the
lock.  The second is behavioural: under real concurrency the lock is actually
taken, contended, and never held by two threads at once.

The behavioural test measures the *lock*, not a crash.  A memory-safety fault
is intermittent -- one reproduction in more than a hundred attempts -- so
asserting "no crash" would pass just as well without the fix and prove nothing.
What is falsifiable in CI is whether the calls were serialized.
"""

from __future__ import annotations

import ast
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import voice_workflow_agent.experiment_protocol_pdf as pdf_module
from voice_workflow_agent.experiment_protocol_pdf import (
    clear_protocol_pdf_cache,
    extract_protocol_pdf,
)

from tests.test_protocol_catalog import write_text_pdf

MODULE_PATH = Path(pdf_module.__file__)
PACKAGE_ROOT = MODULE_PATH.parent
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
    def test_only_one_module_imports_the_pdfium_binding(self) -> None:
        self.assertEqual(_modules_naming_pdfium(), {MODULE_PATH.name})

    def test_every_use_of_pdfium_sits_inside_the_lock(self) -> None:
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
            ["_pypdfium_page_texts"],
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
        lock = getattr(pdf_module, LOCK_NAME)
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

    def test_overlapping_extractions_never_run_two_pdfium_calls_at_once(self):
        observer = _ObservingLock()
        errors: list[BaseException] = []
        results: list[tuple[str, int]] = []
        barrier = threading.Barrier(self.THREADS)

        def extract(index: int) -> None:
            source = self.sources[index % len(self.sources)]
            try:
                barrier.wait(timeout=30)
                for _ in range(self.CALLS // self.THREADS):
                    # The cache would answer every call after the first, which
                    # would leave pdfium untouched and prove nothing, so each
                    # call goes to the library.
                    clear_protocol_pdf_cache()
                    extraction = extract_protocol_pdf(source)
                    results.append((extraction.sha256, extraction.page_count))
            except BaseException as error:  # noqa: BLE001 - reported, not swallowed
                errors.append(error)

        with unittest.mock.patch.object(pdf_module, LOCK_NAME, observer):
            with ThreadPoolExecutor(max_workers=self.THREADS) as pool:
                list(pool.map(extract, range(self.THREADS)))

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
        # Same bytes in, same identity out, whichever thread did the work.
        by_source = {sha for sha, _ in results}
        self.assertEqual(len(by_source), len(self.sources))
        self.assertTrue(all(pages >= 1 for _, pages in results))

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
