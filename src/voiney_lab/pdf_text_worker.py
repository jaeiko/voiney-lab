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


#: The band, as a fraction of page height measured from the bottom, in which
#: a running footer sits. Measured across all four local sources in STEP 22:
#: the most-repeated line shape appears on every page of every document at a
#: bottom-normalised y of 0.023 to 0.026, with a vertical spread of 0.0000,
#: and the bottom 8% of every page in all four holds nothing else. ANKOM's
#: page 30 "Safety information" block sits above it, which is the falsification
#: that check needed. It is a geometric constant applied to every document
#: alike; nothing here reads a word.
BOTTOM_BAND_FRACTION = 0.08


def _bottom_band_offset(
    page, boxes: list, normalized_char_boxes: list
) -> int | None:
    """Where the page's trailing bottom-band run begins, in text offsets.

    Returns the offset of the first character of the maximal suffix whose
    every non-blank character sits inside the bottom band -- or None when the
    page has no such suffix. Only a *trailing* run counts: a page whose body
    reaches into the band has no separable footer, and inventing one would cut
    an instruction in half.
    """

    height = page.get_size()[1]
    if not height:
        return None
    start: int | None = None
    for index, box in reversed(normalized_char_boxes):
        if box is None:
            return None
        if box[3] / height >= BOTTOM_BAND_FRACTION:
            break
        start = index
    return start


def read_page_texts(
    path: str | Path, page_count: int
) -> tuple[list[str | None], list[int | None]]:
    """Page text from the primary engine, and where each page's footer starts.

    None marks an unreadable page. The second list holds, per page, the text
    offset at which the trailing bottom-band run begins, or None where the page
    has no separable one. Both come from the same read, because the offsets are
    only meaningful against the exact text returned beside them.
    """

    import pypdfium2

    texts: list[str | None] = [None] * page_count
    bands: list[int | None] = [None] * page_count
    with _PDFIUM_LOCK:
        document = None
        try:
            document = pypdfium2.PdfDocument(Path(path))
            available = min(page_count, len(document))
            for page_index in range(available):
                try:
                    page = document[page_index]
                    text_page = page.get_textpage()
                    raw = text_page.get_text_range()
                    count = text_page.count_chars()
                    # Line-ending convention only.  PDF has no line
                    # terminators of its own; pypdfium2 renders CRLF while
                    # every other engine and every stored excerpt uses LF.  No
                    # character of content is added, removed, or substituted.
                    #
                    # The box of every surviving character is kept beside the
                    # offset it ends up at, so a dropped CR cannot silently
                    # shift the geometry away from the text it describes.
                    characters: list[str] = []
                    boxes: list[tuple[int, object]] = []
                    raw_index = 0
                    while raw_index < len(raw):
                        character = raw[raw_index]
                        if character == "\r":
                            if (
                                raw_index + 1 < len(raw)
                                and raw[raw_index + 1] == "\n"
                            ):
                                raw_index += 1
                                continue
                            character = "\n"
                        offset = len(characters)
                        characters.append(character)
                        if not character.isspace() and raw_index < count:
                            try:
                                boxes.append(
                                    (offset, text_page.get_charbox(raw_index))
                                )
                            except Exception:  # noqa: BLE001
                                boxes.append((offset, None))
                        raw_index += 1
                    texts[page_index] = "".join(characters)
                    bands[page_index] = _bottom_band_offset(page, None, boxes)
                except Exception:  # noqa: BLE001 - one bad page, not all
                    texts[page_index] = None
                    bands[page_index] = None
        except Exception:  # noqa: BLE001 - fall through, every page unreadable
            pass
        finally:
            if document is not None:
                try:
                    document.close()
                except Exception:  # noqa: BLE001
                    pass
    return texts, bands


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
        texts, bands = read_page_texts(
            request["path"], int(request["page_count"])
        )
    except BaseException as error:  # noqa: BLE001 - reported, never guessed at
        sys.stdout.write(
            json.dumps({"status": "error", "error": type(error).__name__})
        )
        return 1
    sys.stdout.write(
        json.dumps(
            {"status": "ok", "page_texts": texts, "bottom_band_offsets": bands}
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised as a subprocess
    raise SystemExit(main())
