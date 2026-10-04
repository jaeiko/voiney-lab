"""Read a PDF -- structure, metadata, page text, blocks, page images.

This is the only module that touches a PDF library, so the library can be
replaced here without the rest of the server noticing.  The implementation is
PyMuPDF (MuPDF), licensed AGPL-3.0; that licence was accepted on 2026-10-02
together with the decision to keep every call behind this one module.

What the rest of the server receives is plain data: page count, encryption,
document metadata, and per page the text, where its running footer starts,
and its text blocks with coordinates, font size and weight.  Nothing here
decides whether a Protocol is trustworthy; ``experiment_protocol_pdf`` owns
byte identity and every decision.

MuPDF is a C library, so it runs in a child process that is allowed to die.
The process boundary was introduced for pdfium after two crashes inside the
library killed the server (SIGABRT 2026-09-04, SIGSEGV 2026-09-05); it is kept
because a C parser fed untrusted files is the same class of risk whichever
library it is.  A child that dies is a request that fails with a specific
error, not a server that stops.

Page text is MuPDF's own characters, with one layout rule of ours: a line that
overlaps the previous line vertically by at least half the smaller line height
and starts to its right is the same printed line, and is joined to it with
one space.  MuPDF splits a printed line wherever the gap between glyphs is
wide, and a step number set apart from its instruction ("3" ... "Wash the
band") then lands on a line of its own.  Measured on 2026-10-02 over the four
local sources (33 pages), the numbered-line trigger found 25/61/17 step
labels with this rule -- the same as the previous engine on every page -- and
2/0/17 without it.  Characters are never added, dropped or reordered by it.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

ENGINE_NAME = "pymupdf"

#: The band, as a fraction of page height measured from the bottom, in which
#: a running footer sits. Measured across all four local sources in STEP 22:
#: the most-repeated line shape appears on every page of every document at a
#: bottom-normalised y of 0.023 to 0.026, and the bottom 8% of every page holds
#: nothing else. A geometric constant applied to every document alike; nothing
#: here reads a word.
BOTTOM_BAND_FRACTION = 0.08

#: Two MuPDF lines whose vertical extents overlap by at least this fraction of
#: the shorter one's height are one printed line. See the module docstring.
SAME_LINE_OVERLAP = 0.5

#: Resolution pages are rendered at for OCR.
OCR_RENDER_DPI = 300
#: Largest rendered image, in pixels. 300 dpi A4 is 8.7 MP; Google Cloud Vision
#: refuses images above 75 MP. A page large enough to exceed this is rendered
#: at the highest resolution that fits instead.
MAX_RENDER_PIXELS = 40_000_000

# Timeout: measured with pdfium, the largest local source (48.9 MB, 40 pages)
# took 1.25 s and the 16 s the one reproduced crash took to die is well below
# it, so a slow parse is not cut short and mistaken for a fault.
PDF_WORKER_TIMEOUT_SECONDS = 30.0
# Address space: peak RSS for that same document was 108 MB, so 1 GiB is
# roughly nine times the observed need while still killing a runaway long
# before a 4 GB machine is in trouble.  Rendering a 300 dpi page needs more
# headroom than reading text, so the render request carries its own cap.
PDF_WORKER_ADDRESS_SPACE_BYTES = 1024 * 1024 * 1024
PDF_RENDER_ADDRESS_SPACE_BYTES = 2 * 1024 * 1024 * 1024
_MAX_WORKER_OUTPUT_BYTES = 96 * 1024 * 1024
_WORKER_MODULE = "voiney_lab.pdf_text_engine"

_METADATA_KEYS = {
    "title": "title",
    "author": "author",
    "subject": "subject",
    "creator": "creator",
    "producer": "producer",
    "creation_date": "creationDate",
    "modification_date": "modDate",
}


class PdfEngineError(RuntimeError):
    """The engine process did not come back with a usable result."""


class PdfEngineTimeoutError(PdfEngineError):
    pass


class PdfEngineDocumentError(ValueError):
    """The engine read the file and the file itself is the problem."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


class PdfTextBlock(NamedTuple):
    """One MuPDF text block: page coordinates in points, top-left origin.

    A NamedTuple rather than a dataclass so a stored analysis can carry it
    through the existing tuple encoding without a new record type.  Recorded
    for the next measurement of structure detection; nothing decides on it yet.
    """

    x0: float
    y0: float
    x1: float
    y1: float
    font_size: float
    bold: bool
    text: str


@dataclass(frozen=True)
class EnginePage:
    #: None marks a page the engine could not read.
    text: str | None
    #: Offset where the trailing bottom-band run begins, or None.
    bottom_band_offset: int | None
    blocks: tuple[PdfTextBlock, ...]


@dataclass(frozen=True)
class EngineDocument:
    page_count: int
    encrypted: bool
    metadata: dict[str, str | None]
    pages: tuple[EnginePage, ...]
    warnings: tuple[str, ...]


def engine_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("pymupdf")
    except PackageNotFoundError:  # pragma: no cover - dependency is declared
        return "unknown"


def _worker_timeout_seconds() -> float:
    """30 s, unless the host says its machine needs longer.

    The bound exists to catch a hung parser.  It can be exceeded by a machine
    that cannot schedule a subprocess -- running the whole test suite on two
    cores with under 200 MB free tripped it once -- so it is settable rather
    than raised for everyone.  Nonsense is ignored rather than obeyed.
    """

    raw = os.environ.get("VOINEY_LAB_PDF_WORKER_TIMEOUT_SECONDS", "")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return PDF_WORKER_TIMEOUT_SECONDS
    return value if 1.0 <= value <= 600.0 else PDF_WORKER_TIMEOUT_SECONDS


# --- child side -------------------------------------------------------------


def _text_flags(pymupdf) -> int:
    # Without TEXT_CID_FOR_UNKNOWN_UNICODE a glyph with no Unicode mapping is
    # reported as U+FFFD, which experiment_protocol_pdf counts; with it the
    # glyph's internal number would pass for an ordinary character. On the
    # four local sources the two settings give identical text.
    #
    # Without TEXT_MEDIABOX_CLIP (and with an unbounded clip rectangle, see
    # _raw_page) a glyph placed beyond the page edge is still read, as the
    # previous engine read it. On the four local sources the text is identical
    # either way; only the synthetic fixtures, which write one very long line,
    # run past the edge.
    return (
        pymupdf.TEXTFLAGS_RAWDICT
        & ~pymupdf.TEXT_CID_FOR_UNKNOWN_UNICODE
        & ~pymupdf.TEXT_MEDIABOX_CLIP
    )


def _raw_lines(raw: dict) -> list[tuple[tuple[float, float, float, float], list[dict]]]:
    lines = []
    for block in raw.get("blocks", ()):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", ()):
            characters = [
                character
                for span in line.get("spans", ())
                for character in span.get("chars", ())
            ]
            if characters:
                lines.append((tuple(line["bbox"]), characters))
    return lines


def _raw_page(page, pymupdf) -> dict:
    """MuPDF's characters for one page, with layout control codes kept.

    A character code with no Unicode mapping comes back as U+FFFD. When that
    code is a whitespace control -- a newline or tab written inside a text
    string -- MuPDF's character-code fallback reports it as itself, and it is
    taken from there: it is layout, and the previous engine read it as a line
    break too. Every other unmapped code stays U+FFFD and is counted as
    unreadable. The second read happens only on a page that has a U+FFFD.
    """

    flags = _text_flags(pymupdf)
    clip = pymupdf.INFINITE_RECT()
    raw = page.get_text("rawdict", flags=flags, clip=clip)
    lines = _raw_lines(raw)
    if not any(c["c"] == "\ufffd" for _, chars in lines for c in chars):
        return raw
    coded = _raw_lines(
        page.get_text(
            "rawdict",
            flags=flags | pymupdf.TEXT_CID_FOR_UNKNOWN_UNICODE,
            clip=clip,
        )
    )
    if len(coded) != len(lines):
        return raw
    for (_, characters), (_, coded_characters) in zip(lines, coded):
        if len(characters) != len(coded_characters):
            continue
        for character, coded_character in zip(characters, coded_characters):
            if (
                character["c"] == "\ufffd"
                and len(coded_character["c"]) == 1
                and coded_character["c"].isspace()
            ):
                character["c"] = coded_character["c"]
    return raw


def _page_text(page, pymupdf) -> EnginePage:
    height = page.rect.height
    raw = _raw_page(page, pymupdf)
    lines = _raw_lines(raw)
    blocks: list[PdfTextBlock] = []
    for block in raw.get("blocks", ()):
        if block.get("type") != 0:
            continue
        sizes: list[float] = []
        bold = False
        block_lines: list[str] = []
        for line in block.get("lines", ()):
            for span in line.get("spans", ()):
                if span.get("chars"):
                    sizes.append(float(span.get("size", 0.0)))
                    bold = bold or bool(
                        int(span.get("flags", 0)) & pymupdf.TEXT_FONT_BOLD
                    )
            text = "".join(
                character["c"]
                for span in line.get("spans", ())
                for character in span.get("chars", ())
            )
            if text:
                block_lines.append(text)
        if block_lines:
            x0, y0, x1, y1 = block["bbox"]
            blocks.append(
                PdfTextBlock(
                    round(x0, 2),
                    round(y0, 2),
                    round(x1, 2),
                    round(y1, 2),
                    round(max(sizes), 2) if sizes else 0.0,
                    bold,
                    "\n".join(block_lines),
                )
            )

    out: list[str] = []
    # (offset, top of the glyph measured from the page bottom)
    tops: list[tuple[int, float]] = []
    previous: tuple[float, float, float, float] | None = None
    for bbox, characters in lines:
        if previous is not None:
            overlap = min(bbox[3], previous[3]) - max(bbox[1], previous[1])
            shorter = min(bbox[3] - bbox[1], previous[3] - previous[1]) or 1.0
            if overlap / shorter >= SAME_LINE_OVERLAP and bbox[0] >= previous[2] - 0.5:
                if out and out[-1] != " " and characters[0]["c"] != " ":
                    out.append(" ")
                previous = (
                    min(previous[0], bbox[0]),
                    min(previous[1], bbox[1]),
                    bbox[2],
                    max(previous[3], bbox[3]),
                )
            else:
                out.append("\n")
                previous = bbox
        else:
            previous = bbox
        for character in characters:
            if not character["c"].isspace():
                tops.append((len(out), height - float(character["bbox"][1])))
            out.append(character["c"])
    text = "".join(out)

    band: int | None = None
    if height:
        for offset, top in reversed(tops):
            if top / height >= BOTTOM_BAND_FRACTION:
                break
            band = offset
    return EnginePage(text=text, bottom_band_offset=band, blocks=tuple(blocks))


def read_document_in_process(path: str | Path) -> EngineDocument:
    """Open one document and read everything the server needs from it."""

    import pymupdf

    pymupdf.TOOLS.mupdf_warnings(reset=True)
    try:
        document = pymupdf.open(Path(path), filetype="pdf")
    except Exception as error:  # noqa: BLE001 - MuPDF raises several types
        raise PdfEngineDocumentError("malformed") from error
    try:
        encrypted = bool(document.is_encrypted or document.needs_pass)
        if document.needs_pass and not document.authenticate(""):
            raise PdfEngineDocumentError("encrypted")
        # MuPDF rebuilds a broken cross-reference table and carries on. A
        # rebuilt document is MuPDF's guess at the structure, not the bytes
        # the source declares, so it is refused as malformed -- none of the
        # four local sources needs it.
        if document.is_repaired or document.page_count < 1:
            raise PdfEngineDocumentError("malformed")
        metadata_raw = document.metadata or {}
        metadata = {
            field: (str(metadata_raw.get(key) or "").strip() or None)
            for field, key in _METADATA_KEYS.items()
        }
        pages: list[EnginePage] = []
        for page_index in range(document.page_count):
            try:
                pages.append(_page_text(document[page_index], pymupdf))
            except Exception:  # noqa: BLE001 - one bad page, not all
                pages.append(EnginePage(None, None, ()))
        warnings = tuple(
            dict.fromkeys(
                line.strip()
                for line in (pymupdf.TOOLS.mupdf_warnings() or "").splitlines()
                if line.strip()
            )
        )
        return EngineDocument(
            page_count=document.page_count,
            encrypted=encrypted,
            metadata=metadata,
            pages=tuple(pages),
            warnings=warnings[:20],
        )
    finally:
        document.close()


def render_page_png_in_process(
    path: str | Path, page_number: int, dpi: int = OCR_RENDER_DPI
) -> bytes:
    """One page as PNG bytes, in memory. Never written to disk."""

    import pymupdf

    document = pymupdf.open(Path(path), filetype="pdf")
    try:
        if document.needs_pass and not document.authenticate(""):
            raise PdfEngineDocumentError("encrypted")
        if not 1 <= page_number <= document.page_count:
            raise PdfEngineDocumentError("page_out_of_range")
        page = document[page_number - 1]
        scale = dpi / 72.0
        pixels = page.rect.width * scale * page.rect.height * scale
        if pixels > MAX_RENDER_PIXELS:
            scale *= (MAX_RENDER_PIXELS / pixels) ** 0.5
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
        return pixmap.tobytes("png")
    finally:
        document.close()


def _document_payload(document: EngineDocument) -> dict[str, object]:
    return {
        "status": "ok",
        "page_count": document.page_count,
        "encrypted": document.encrypted,
        "metadata": document.metadata,
        "warnings": list(document.warnings),
        "pages": [
            {
                "text": page.text,
                "bottom_band_offset": page.bottom_band_offset,
                "blocks": [list(block) for block in page.blocks],
            }
            for page in document.pages
        ],
    }


def main() -> int:
    """Child entry point: cap the address space, do one request, exit.

    One JSON request arrives on stdin and one JSON reply leaves on stdout.  A
    subprocess is used rather than ``multiprocessing`` because ``spawn``
    re-imports the parent's ``__main__``, which differs between a server, a
    test runner and a script.  The cap is set before PyMuPDF is imported so the
    library's own allocations are inside it.
    """

    try:
        request = json.loads(sys.stdin.read())
        limit = int(request.get("address_space_bytes") or 0)
        if limit > 0:
            import resource

            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        operation = request.get("operation", "read")
        if operation == "read":
            reply = _document_payload(read_document_in_process(request["path"]))
        elif operation == "render":
            png = render_page_png_in_process(
                request["path"], int(request["page_number"]), int(request["dpi"])
            )
            reply = {"status": "ok", "png_base64": base64.b64encode(png).decode("ascii")}
        else:
            raise ValueError("unknown operation")
    except PdfEngineDocumentError as error:
        sys.stdout.write(json.dumps({"status": "document_error", "kind": error.kind}))
        return 0
    except BaseException as error:  # noqa: BLE001 - reported, never guessed at
        sys.stdout.write(json.dumps({"status": "error", "error": type(error).__name__}))
        return 1
    sys.stdout.write(json.dumps(reply))
    return 0


# --- parent side ------------------------------------------------------------


def _run_worker(request: dict[str, object]) -> dict[str, object]:
    try:
        completed = subprocess.run(
            [sys.executable, "-m", _WORKER_MODULE],
            input=json.dumps(request).encode("utf-8"),
            capture_output=True,
            timeout=_worker_timeout_seconds(),
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise PdfEngineTimeoutError("PDF engine exceeded its time limit.") from error
    except OSError as error:
        raise PdfEngineError("PDF engine could not be started.") from error
    if completed.returncode != 0:
        # A negative return code is a signal: the crash the boundary exists
        # for, reported as itself.
        raise PdfEngineError("PDF engine did not complete.")
    if len(completed.stdout) > _MAX_WORKER_OUTPUT_BYTES:
        raise PdfEngineError("PDF engine returned an unusable result.")
    try:
        payload = json.loads(completed.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PdfEngineError("PDF engine returned an unusable result.") from error
    if not isinstance(payload, dict):
        raise PdfEngineError("PDF engine returned an unusable result.")
    if payload.get("status") == "document_error":
        kind = payload.get("kind")
        raise PdfEngineDocumentError(kind if isinstance(kind, str) else "malformed")
    if payload.get("status") != "ok":
        raise PdfEngineError("PDF engine returned an unusable result.")
    return payload


def _is_offset(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _parse_block(raw: object) -> PdfTextBlock:
    if (
        not isinstance(raw, list)
        or len(raw) != 7
        or not all(
            isinstance(item, (int, float)) and not isinstance(item, bool)
            for item in raw[:5]
        )
        or not isinstance(raw[5], bool)
        or not isinstance(raw[6], str)
    ):
        raise PdfEngineError("PDF engine returned an unusable result.")
    return PdfTextBlock(*(float(item) for item in raw[:5]), raw[5], raw[6])


def read_document(path: str | Path) -> EngineDocument:
    """Read one document in a fresh child process and validate the reply.

    Raises ``PdfEngineDocumentError`` when the file is the problem and
    ``PdfEngineError`` when the engine is.  The child is never trusted for
    identity: the caller hashes the bytes itself.
    """

    payload = _run_worker(
        {
            "operation": "read",
            "path": str(path),
            "address_space_bytes": PDF_WORKER_ADDRESS_SPACE_BYTES,
        }
    )
    page_count = payload.get("page_count")
    pages = payload.get("pages")
    metadata = payload.get("metadata")
    warnings = payload.get("warnings")
    if (
        not isinstance(page_count, int)
        or isinstance(page_count, bool)
        or page_count < 1
        or not isinstance(pages, list)
        or len(pages) != page_count
        or not isinstance(payload.get("encrypted"), bool)
        or not isinstance(metadata, dict)
        or set(metadata) != set(_METADATA_KEYS)
        or any(value is not None and not isinstance(value, str) for value in metadata.values())
        or not isinstance(warnings, list)
        or any(not isinstance(item, str) for item in warnings)
    ):
        raise PdfEngineError("PDF engine returned an unusable result.")
    parsed: list[EnginePage] = []
    for page in pages:
        if not isinstance(page, dict):
            raise PdfEngineError("PDF engine returned an unusable result.")
        text = page.get("text")
        band = page.get("bottom_band_offset")
        blocks = page.get("blocks")
        if (
            (text is not None and not isinstance(text, str))
            or (band is not None and not _is_offset(band))
            or not isinstance(blocks, list)
        ):
            raise PdfEngineError("PDF engine returned an unusable result.")
        if text is None:
            band = None
        parsed.append(
            EnginePage(
                text=text,
                bottom_band_offset=band,
                blocks=tuple(_parse_block(block) for block in blocks),
            )
        )
    return EngineDocument(
        page_count=page_count,
        encrypted=payload["encrypted"],
        metadata=dict(metadata),
        pages=tuple(parsed),
        warnings=tuple(warnings),
    )


def render_page_png(
    path: str | Path, page_number: int, *, dpi: int = OCR_RENDER_DPI
) -> bytes:
    """Render one page to PNG bytes in a child process. Kept in memory only."""

    payload = _run_worker(
        {
            "operation": "render",
            "path": str(path),
            "page_number": page_number,
            "dpi": dpi,
            "address_space_bytes": PDF_RENDER_ADDRESS_SPACE_BYTES,
        }
    )
    encoded = payload.get("png_base64")
    if not isinstance(encoded, str):
        raise PdfEngineError("PDF engine returned an unusable result.")
    try:
        png = base64.b64decode(encoded, validate=True)
    except ValueError as error:
        raise PdfEngineError("PDF engine returned an unusable result.") from error
    if not png.startswith(b"\x89PNG\r\n\x1a\n"):
        raise PdfEngineError("PDF engine returned an unusable result.")
    return png


if __name__ == "__main__":  # pragma: no cover - exercised as a subprocess
    raise SystemExit(main())
