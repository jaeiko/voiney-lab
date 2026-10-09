"""Read a PDF -- structure, metadata, page text, blocks, page images.

This is the only module that touches a PDF library, so the library can be
replaced here without the rest of the server noticing.  The implementation is
PyMuPDF (MuPDF), licensed AGPL-3.0; that licence was accepted on 2026-10-02
together with the decision to keep every call behind this one module.

What the rest of the server receives is plain data: page count, encryption,
document metadata, and per page the text, where its running footer starts,
its text blocks with coordinates, font size and weight, and where its
pictures sit (lane PX, decision 2 of 2026-10-06: every read of a PDF --
text, text positions, picture positions, whether a text layer exists -- is
this one library, so a later change of library touches this module alone).
Nothing here decides whether a Protocol is trustworthy;
``experiment_protocol_pdf`` owns byte identity and every decision.

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
from collections.abc import Sequence
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


class PdfImageBox(NamedTuple):
    """Where one picture sits on the page: points, top-left origin.

    Reported by the engine for the next structure measurement (a figure
    beside a step, a scanned region); nothing stores or decides on it yet,
    so a stored analysis is unchanged by it.
    """

    x0: float
    y0: float
    x1: float
    y1: float


class PdfImageInfo(NamedTuple):
    """One placed picture with the size of the bytes behind it (lane WV).

    ``xref`` is 0 for an inline image. ``width_px``/``height_px`` are the
    picture's own pixel size: a 2-by-2 picture stretched over a card is a
    fill, not a figure. Geometry only; nothing here reads a word.
    """

    x0: float
    y0: float
    x1: float
    y1: float
    xref: int
    width_px: int
    height_px: int


class PdfPageImages(NamedTuple):
    """The pictures placed on one page, with the page's size in points."""

    page_number: int
    width: float
    height: float
    images: tuple[PdfImageInfo, ...]


class PdfDocumentImages(NamedTuple):
    """The requested pages' pictures, and the xrefs that repeat across the document.

    ``repeated_xrefs`` are the pictures placed on three or more pages -- a
    logo, a running header -- so a caller can leave them out of the figures.
    """

    pages: tuple[PdfPageImages, ...]
    repeated_xrefs: frozenset[int]


#: Longest side, in pixels, a cropped figure is rendered at.
FIGURE_RENDER_MAX_SIDE_PX = 1000
#: The most a crop is enlarged beyond 72 dpi; a thumbnail stays a thumbnail.
FIGURE_RENDER_MAX_SCALE = 3.0


@dataclass(frozen=True)
class EnginePage:
    #: None marks a page the engine could not read.
    text: str | None
    #: Offset where the trailing bottom-band run begins, or None.
    bottom_band_offset: int | None
    blocks: tuple[PdfTextBlock, ...]
    #: The page's pictures, in the engine's order.
    images: tuple[PdfImageBox, ...] = ()


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
    images: list[PdfImageBox] = []
    for block in raw.get("blocks", ()):
        if block.get("type") == 1:
            x0, y0, x1, y1 = block["bbox"]
            images.append(PdfImageBox(round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)))
            continue
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
    return EnginePage(
        text=text, bottom_band_offset=band, blocks=tuple(blocks), images=tuple(images),
    )


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
                pages.append(EnginePage(None, None, (), ()))
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


def page_images_in_process(
    path: str | Path, page_numbers: Sequence[int]
) -> PdfDocumentImages:
    """Where the pictures sit on the given pages, and which xrefs repeat (lane WV)."""

    import pymupdf

    document = pymupdf.open(Path(path), filetype="pdf")
    try:
        if document.needs_pass and not document.authenticate(""):
            raise PdfEngineDocumentError("encrypted")
        wanted = sorted({int(number) for number in page_numbers})
        if any(not 1 <= number <= document.page_count for number in wanted):
            raise PdfEngineDocumentError("page_out_of_range")
        counts: dict[int, int] = {}
        for index in range(document.page_count):
            for xref in {item[0] for item in document[index].get_images(full=True)}:
                if xref:
                    counts[xref] = counts.get(xref, 0) + 1
        pages: list[PdfPageImages] = []
        for number in wanted:
            page = document[number - 1]
            found: list[PdfImageInfo] = []
            for info in page.get_image_info(xrefs=True):
                x0, y0, x1, y1 = info["bbox"]
                found.append(PdfImageInfo(
                    round(float(x0), 2), round(float(y0), 2),
                    round(float(x1), 2), round(float(y1), 2),
                    int(info.get("xref") or 0),
                    int(info.get("width") or 0), int(info.get("height") or 0),
                ))
            pages.append(PdfPageImages(
                number, round(page.rect.width, 2), round(page.rect.height, 2),
                tuple(found),
            ))
        return PdfDocumentImages(
            tuple(pages), frozenset(xref for xref, n in counts.items() if n >= 3),
        )
    finally:
        document.close()


def render_clips_png_in_process(
    path: str | Path,
    clips: Sequence[tuple[int, tuple[float, float, float, float]]],
    *,
    max_side_px: int = FIGURE_RENDER_MAX_SIDE_PX,
) -> tuple[bytes, ...]:
    """Each clip (page number, points box) as PNG bytes, in memory (lane WV).

    A clip is rendered at the scale that fits ``max_side_px`` on its longer
    side, at most ``FIGURE_RENDER_MAX_SCALE`` times 72 dpi and never below it.
    """

    import pymupdf

    document = pymupdf.open(Path(path), filetype="pdf")
    try:
        if document.needs_pass and not document.authenticate(""):
            raise PdfEngineDocumentError("encrypted")
        out: list[bytes] = []
        for page_number, box in clips:
            if not 1 <= int(page_number) <= document.page_count:
                raise PdfEngineDocumentError("page_out_of_range")
            page = document[int(page_number) - 1]
            rect = pymupdf.Rect(*(float(value) for value in box)) & page.rect
            if rect.is_empty or rect.width < 1 or rect.height < 1:
                raise PdfEngineDocumentError("clip_out_of_range")
            longer = max(rect.width, rect.height)
            scale = max(1.0, min(FIGURE_RENDER_MAX_SCALE, max_side_px / longer))
            pixmap = page.get_pixmap(
                matrix=pymupdf.Matrix(scale, scale), clip=rect, alpha=False
            )
            out.append(pixmap.tobytes("png"))
        return tuple(out)
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
                "images": [list(image) for image in page.images],
            }
            for page in document.pages
        ],
    }


def _cap_address_space(limit: int) -> bool:
    """Cap this process's address space at ``limit`` bytes; True if it was.

    Linux (server, pilot labs, CI) always gets the cap, and a refusal there
    fails the request as before.  macOS is development only and its kernel
    does not enforce ``RLIMIT_AS``: setting it below the unlimited maximum
    raises ``ValueError`` ("current limit exceeds maximum limit"), measured on
    2026-10-05 (macOS 27.0.1, arm64, Python 3.14.8).  There alone, that
    refusal is accepted and the child reads without a memory cap; the time
    limit and the output size limit still apply (decision of 2026-10-04).
    Any other error, or a refusal on any other platform, still propagates.
    """

    import resource

    try:
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    except (ValueError, OSError):
        if sys.platform == "darwin":
            return False
        raise
    return True


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
            _cap_address_space(limit)
        operation = request.get("operation", "read")
        if operation == "read":
            reply = _document_payload(read_document_in_process(request["path"]))
        elif operation == "render":
            png = render_page_png_in_process(
                request["path"], int(request["page_number"]), int(request["dpi"])
            )
            reply = {"status": "ok", "png_base64": base64.b64encode(png).decode("ascii")}
        elif operation == "images":
            found = page_images_in_process(
                request["path"], [int(number) for number in request["page_numbers"]]
            )
            reply = {
                "status": "ok",
                "repeated_xrefs": sorted(found.repeated_xrefs),
                "pages": [
                    {
                        "page_number": page.page_number,
                        "width": page.width,
                        "height": page.height,
                        "images": [list(image) for image in page.images],
                    }
                    for page in found.pages
                ],
            }
        elif operation == "clips":
            pngs = render_clips_png_in_process(
                request["path"],
                [
                    (int(clip["page_number"]), tuple(float(v) for v in clip["box"]))
                    for clip in request["clips"]
                ],
                max_side_px=int(request.get("max_side_px") or FIGURE_RENDER_MAX_SIDE_PX),
            )
            reply = {
                "status": "ok",
                "pngs_base64": [base64.b64encode(png).decode("ascii") for png in pngs],
            }
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


def _parse_image(raw: object) -> PdfImageBox:
    if (
        not isinstance(raw, list)
        or len(raw) != 4
        or not all(
            isinstance(item, (int, float)) and not isinstance(item, bool)
            for item in raw
        )
    ):
        raise PdfEngineError("PDF engine returned an unusable result.")
    return PdfImageBox(*(float(item) for item in raw))


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
        images = page.get("images", [])
        if (
            (text is not None and not isinstance(text, str))
            or (band is not None and not _is_offset(band))
            or not isinstance(blocks, list)
            or not isinstance(images, list)
        ):
            raise PdfEngineError("PDF engine returned an unusable result.")
        if text is None:
            band = None
        parsed.append(
            EnginePage(
                text=text,
                bottom_band_offset=band,
                blocks=tuple(_parse_block(block) for block in blocks),
                images=tuple(_parse_image(image) for image in images),
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


def _parse_image_info(raw: object) -> PdfImageInfo:
    if (
        not isinstance(raw, list)
        or len(raw) != 7
        or not all(
            isinstance(item, (int, float)) and not isinstance(item, bool)
            for item in raw[:4]
        )
        or not all(isinstance(item, int) and not isinstance(item, bool) for item in raw[4:])
    ):
        raise PdfEngineError("PDF engine returned an unusable result.")
    return PdfImageInfo(*(float(item) for item in raw[:4]), *(int(item) for item in raw[4:]))


def page_images(path: str | Path, page_numbers: Sequence[int]) -> PdfDocumentImages:
    """The pictures placed on the given pages, read in a child process (lane WV)."""

    payload = _run_worker(
        {
            "operation": "images",
            "path": str(path),
            "page_numbers": [int(number) for number in page_numbers],
            "address_space_bytes": PDF_WORKER_ADDRESS_SPACE_BYTES,
        }
    )
    pages = payload.get("pages")
    repeated = payload.get("repeated_xrefs")
    if (
        not isinstance(pages, list)
        or not isinstance(repeated, list)
        or any(not isinstance(item, int) or isinstance(item, bool) for item in repeated)
    ):
        raise PdfEngineError("PDF engine returned an unusable result.")
    parsed: list[PdfPageImages] = []
    for page in pages:
        if (
            not isinstance(page, dict)
            or not isinstance(page.get("page_number"), int)
            or not isinstance(page.get("width"), (int, float))
            or not isinstance(page.get("height"), (int, float))
            or not isinstance(page.get("images"), list)
        ):
            raise PdfEngineError("PDF engine returned an unusable result.")
        parsed.append(PdfPageImages(
            int(page["page_number"]), float(page["width"]), float(page["height"]),
            tuple(_parse_image_info(image) for image in page["images"]),
        ))
    return PdfDocumentImages(tuple(parsed), frozenset(repeated))


def render_clips_png(
    path: str | Path,
    clips: Sequence[tuple[int, tuple[float, float, float, float]]],
    *,
    max_side_px: int = FIGURE_RENDER_MAX_SIDE_PX,
) -> tuple[bytes, ...]:
    """Each clip as PNG bytes, rendered in a child process; kept in memory only."""

    if not clips:
        return ()
    payload = _run_worker(
        {
            "operation": "clips",
            "path": str(path),
            "clips": [
                {"page_number": int(page_number), "box": [float(v) for v in box]}
                for page_number, box in clips
            ],
            "max_side_px": int(max_side_px),
            "address_space_bytes": PDF_RENDER_ADDRESS_SPACE_BYTES,
        }
    )
    encoded = payload.get("pngs_base64")
    if not isinstance(encoded, list) or len(encoded) != len(clips):
        raise PdfEngineError("PDF engine returned an unusable result.")
    pngs: list[bytes] = []
    for item in encoded:
        if not isinstance(item, str):
            raise PdfEngineError("PDF engine returned an unusable result.")
        try:
            png = base64.b64decode(item, validate=True)
        except ValueError as error:
            raise PdfEngineError("PDF engine returned an unusable result.") from error
        if not png.startswith(b"\x89PNG\r\n\x1a\n"):
            raise PdfEngineError("PDF engine returned an unusable result.")
        pngs.append(png)
    return tuple(pngs)


if __name__ == "__main__":  # pragma: no cover - exercised as a subprocess
    raise SystemExit(main())
