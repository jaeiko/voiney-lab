"""Read page text with pdfium, in a process that is allowed to die.

pdfium is a large C++ parser built ``-fno-exceptions``, and it killed this
server twice: SIGABRT on 2026-09-04 and SIGSEGV on 2026-09-05, both inside
``libpdfium.so``.  Two different signals on one code path is memory
corruption, which no Python-level guard can catch -- by the time the process
is unwinding, there is nothing left to fail closed with.  STEP 23 serialized
the calls, which removed the one cause it had evidence for; it could not
remove the class.

So the parse happens somewhere the server can afford to lose.  This module is
the whole of what runs in the child: it opens one document, reads the text of
each page, and hands back strings.  Everything that decides anything -- the
SHA-256 of the bytes, the page hashes, the segment boundaries, the independent
cross-check, glyph resolution -- stays in the parent, which never trusts the
child for identity.  A child that dies is a request that fails with a specific
error, not a server that stops.

The lock is kept although a spawned child is single-threaded and could not
contend for it.  The function is importable, and pdfium's rule -- no two calls
at once, "not even with different documents" -- has to hold in whatever
process ends up calling it.
"""

from __future__ import annotations

import threading
from pathlib import Path

_PDFIUM_LOCK = threading.RLock()


def read_page_texts(path: str | Path, page_count: int) -> list[str | None]:
    """Page text from the primary engine; None marks an unreadable page.

    One bad page does not lose the rest, and a document that cannot be opened
    at all comes back as every page unreadable -- the same contract the
    in-process version had, so the parent's handling of a degraded parse is
    unchanged.  What is *not* handled here is the process dying; that is the
    parent's to notice, and it must never look like a document with no text.
    """

    import pypdfium2

    texts: list[str | None] = [None] * page_count
    with _PDFIUM_LOCK:
        document = None
        try:
            document = pypdfium2.PdfDocument(Path(path))
            available = min(page_count, len(document))
            for page_index in range(available):
                try:
                    page = document[page_index]
                    text = page.get_textpage().get_text_range()
                    # Line-ending convention only.  PDF has no line
                    # terminators of its own; pypdfium2 renders CRLF while
                    # every other engine and every stored excerpt uses LF.  No
                    # character of content is added, removed, or substituted.
                    texts[page_index] = text.replace("\r\n", "\n").replace(
                        "\r", "\n"
                    )
                except Exception:  # noqa: BLE001 - one bad page, not all
                    texts[page_index] = None
        except Exception:  # noqa: BLE001 - fall through, every page unreadable
            pass
        finally:
            if document is not None:
                try:
                    document.close()
                except Exception:  # noqa: BLE001
                    pass
    return texts


def main() -> int:
    """Child entry point: cap the address space, read, print, exit.

    One JSON request arrives on stdin and one JSON reply leaves on stdout.  A
    subprocess is used rather than ``multiprocessing`` because ``spawn``
    re-imports the parent's ``__main__``, which is a different module in a
    server, a test runner and a script -- three ways for the parser to fail
    for reasons that have nothing to do with the document.

    The address-space cap is set before pypdfium2 is imported so the library's
    own allocations are inside it.  It bounds what a runaway parse can take
    from the machine; it does not make pdfium safe, because a build that
    cannot throw need not check that an allocation failed.  The process
    boundary is what makes this safe -- the cap only limits the blast radius.
    """

    import json
    import sys

    try:
        request = json.loads(sys.stdin.read())
        limit = int(request.get("address_space_bytes") or 0)
        if limit > 0:
            import resource

            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        texts = read_page_texts(
            request["path"], int(request["page_count"])
        )
    except BaseException as error:  # noqa: BLE001 - reported, never guessed at
        sys.stdout.write(
            json.dumps({"status": "error", "error": type(error).__name__})
        )
        return 1
    sys.stdout.write(json.dumps({"status": "ok", "page_texts": texts}))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised as a subprocess
    raise SystemExit(main())
