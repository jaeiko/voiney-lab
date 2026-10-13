"""Local Protocol PDF validation, byte identity, metadata, and page text.

The SHA-256 value returned here identifies exact file bytes. It does not
indicate that a Protocol is trusted, approved, official, or current.

Every read of the PDF itself goes through ``pdf_text_engine`` (PyMuPDF). This
module owns what is recorded about the source: its type, its 64 MiB bound,
the SHA-256 of its bytes, the refusal of a file that changed while it was
being read, and which pages carry no usable text and need OCR.

Text from a born-digital PDF is taken as the engine reports it. The three-way
engine comparison that used to stand here -- pdfium checked against poppler's
pdftotext, unmapped glyphs read back from the document's ToUnicode map, and
the whole document refused when one page's character census differed -- was
removed on 2026-10-02. Measured that day over the four local sources (33
pages), PyMuPDF and the previous engine produced the same characters on every
page, differing only in whitespace. Text that cannot be read -- a scan, or a
page whose glyphs have no Unicode mapping -- is marked per page as needing OCR
instead of refusing the document.

A glyph the engine reports as U+FFFD is a character nobody can read, and one is
enough to mark its page: on 2026-10-03 the only two in the five local sources
stood directly before a duration value on ANKOM page 3, where the previous engine had
dropped them without a word. Nothing is put in their place -- they are left
out of the page text, the page warning says how many, and the page waits for
OCR. ``experiment_protocol.assess_readiness`` keeps a Protocol analysed from
such a page out of execution until that OCR text has been accepted.
"""

from __future__ import annotations

import hashlib
import re
import os
import stat
import threading
import time
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import BinaryIO

from voiney_lab import pdf_text_engine
from voiney_lab.pdf_text_engine import (
    PDF_WORKER_ADDRESS_SPACE_BYTES,
    PDF_WORKER_TIMEOUT_SECONDS,
    BOTTOM_BAND_FRACTION,
    PdfEngineDocumentError,
    PdfEngineError,
    PdfEngineTimeoutError,
    PdfTextBlock,
    _worker_timeout_seconds,
)


PDF_MEDIA_TYPE = "application/pdf"
MAX_PROTOCOL_PDF_BYTES = 64 * 1024 * 1024
_HASH_CHUNK_BYTES = 1024 * 1024

__all__ = [
    "BOTTOM_BAND_FRACTION",
    "DOCX_MEDIA_TYPE",
    "IMAGE_MEDIA_TYPES",
    "OFFICE_CONVERTER_ENV",
    "MAX_PROTOCOL_PDF_BYTES",
    "OCR_REASON_DROPPED_GLYPHS",
    "OCR_REASON_NO_TEXT",
    "OCR_REASON_UNREADABLE_GLYPHS",
    "PDF_MEDIA_TYPE",
    "PDF_WORKER_ADDRESS_SPACE_BYTES",
    "PDF_WORKER_TIMEOUT_SECONDS",
    "PdfTextBlock",
    "ProtocolPdfChangedError",
    "ProtocolPdfEncryptedError",
    "ProtocolPdfError",
    "ProtocolPdfExtraction",
    "ProtocolPdfMalformedError",
    "ProtocolPdfMetadata",
    "ProtocolPdfNotFoundError",
    "ProtocolPdfNotRegularFileError",
    "ProtocolPdfPage",
    "ProtocolPdfTooLargeError",
    "ProtocolPdfTypeError",
    "ProtocolPdfWorkerError",
    "ProtocolPdfWorkerTimeoutError",
    "ProtocolSourceConversionError",
    "ProtocolSourceConversionUnavailableError",
    "ProtocolSourceFormatError",
    "ProtocolSourceTooLargeError",
    "SOURCE_MEDIA_TYPES",
    "SourceConversion",
    "TextVerification",
    "clear_protocol_pdf_cache",
    "convert_source_to_pdf",
    "extract_protocol_pdf",
    "file_sha256",
    "find_office_converter",
    "pdf_filename",
    "page_ocr_reason",
    "unreadable_character_count",
    "_worker_timeout_seconds",
]


class ProtocolPdfError(ValueError):
    """Base class for safe, domain-specific Protocol PDF failures."""

    code = "protocol_pdf_error"


class ProtocolPdfNotFoundError(ProtocolPdfError):
    code = "protocol_pdf_not_found"


class ProtocolPdfNotRegularFileError(ProtocolPdfError):
    code = "protocol_pdf_not_regular_file"


class ProtocolPdfTooLargeError(ProtocolPdfError):
    code = "protocol_pdf_too_large"


class ProtocolPdfTypeError(ProtocolPdfError):
    code = "protocol_pdf_invalid_type"


class ProtocolPdfMalformedError(ProtocolPdfError):
    code = "protocol_pdf_malformed"


class ProtocolPdfEncryptedError(ProtocolPdfError):
    code = "protocol_pdf_encrypted"


class ProtocolPdfWorkerError(ProtocolPdfError):
    """The parser process did not come back with a result.

    Deliberately not a ``ProtocolPdfMalformedError``.  A worker that died took
    the parser with it, and calling that a malformed document would put the
    blame on the source, hide a library fault behind a document fault, and
    make the reason a reader is given for a refusal untrue.
    """

    code = "protocol_pdf_worker_failed"


class ProtocolPdfWorkerTimeoutError(ProtocolPdfWorkerError):
    code = "protocol_pdf_worker_timeout"


class ProtocolPdfChangedError(ProtocolPdfError):
    code = "protocol_pdf_changed_during_extraction"


class TextVerification(str, Enum):
    """Retired. The verdict of the engine cross-check removed on 2026-10-02.

    Kept only so analyses stored before then still decode: they carry this
    enum inside ``ProtocolMetadata.pdf``. Nothing produces or reads it now.
    """

    VERIFIED = "verified"
    MISMATCH = "mismatch"
    COMPARATOR_UNAVAILABLE = "comparator_unavailable"


OCR_REASON_NO_TEXT = "no_text_layer"
OCR_REASON_UNREADABLE_GLYPHS = "unreadable_glyphs"
#: The text layer drops a Korean font's glyphs (lane AN, EV4 decision d).
OCR_REASON_DROPPED_GLYPHS = "dropped_glyphs"
#: Share of a page's non-space characters that are private use or unassigned,
#: at or above which the page is treated as unreadable and needing OCR. A
#: private-use character can be a font's own bullet, so one among many is a
#: warning rather than an OCR page. U+FFFD is not under this rule: see
#: ``page_ocr_reason``. Measured on 2026-10-03 the five local sources (73
#: pages) hold no private-use or unassigned character at all with PyMuPDF.
UNREADABLE_PAGE_RATIO = 0.05
_UNREADABLE_CATEGORIES = frozenset({"Co", "Cn"})
#: What the engine reports for a glyph with no Unicode mapping.
UNMAPPED_GLYPH = "\ufffd"


def unreadable_character_count(text: str) -> int:
    """Characters that cannot be document content: U+FFFD, private use, unassigned."""

    return sum(
        1
        for character in text
        if character == UNMAPPED_GLYPH
        or unicodedata.category(character) in _UNREADABLE_CATEGORIES
    )


def page_ocr_reason(text: str) -> str | None:
    """Why a page needs OCR, or None when its text layer is usable.

    One U+FFFD is enough. It stands for a glyph whose character the document
    does not declare, and it can sit inside a value or a unit as easily as
    anywhere else, so no share of the page makes it safe to read past.
    """

    visible = sum(1 for character in text if not character.isspace())
    if visible == 0:
        return OCR_REASON_NO_TEXT
    if UNMAPPED_GLYPH in text:
        return OCR_REASON_UNREADABLE_GLYPHS
    if unreadable_character_count(text) / visible >= UNREADABLE_PAGE_RATIO:
        return OCR_REASON_UNREADABLE_GLYPHS
    return None


@dataclass(frozen=True)
class ProtocolPdfMetadata:
    title: str | None
    author: str | None
    subject: str | None
    creator: str | None
    producer: str | None
    creation_date: str | None
    modification_date: str | None


@dataclass(frozen=True)
class ProtocolPdfPage:
    source_page_number: int
    text: str
    text_empty: bool
    warning: str | None = None
    #: Text offset where this page's trailing running-footer band begins, or
    #: None where the page has no separable one. Geometry, measured from
    #: character boxes; nothing here reads a word. See
    #: pdf_text_engine.BOTTOM_BAND_FRACTION for the measurement behind it.
    bottom_band_offset: int | None = None
    #: True when the page has no usable text layer and its text must come
    #: from OCR. Recorded per page; it never refuses the document.
    ocr_required: bool = False
    #: ``no_text_layer`` or ``unreadable_glyphs`` when ``ocr_required``.
    ocr_reason: str | None = None
    #: The engine's text blocks, with coordinates, font size and weight.
    #: Carried for the next structure measurement; nothing decides on them.
    blocks: tuple[PdfTextBlock, ...] = ()
    #: True when this page's text is accepted OCR output rather than the
    #: PDF's text layer (set by the catalog for analysis). Evidence
    #: comparison then joins a line break between two Hangul letters, as OCR
    #: breaks Korean words mid-line (human decision 2026-10-05, lane P3).
    ocr_derived: bool = False


@dataclass(frozen=True)
class ProtocolPdfExtraction:
    """Evidence extracted from one exact local PDF file."""

    original_filename: str
    byte_size: int
    sha256: str
    media_type: str
    page_count: int
    encrypted: bool
    metadata: ProtocolPdfMetadata
    pages: tuple[ProtocolPdfPage, ...]
    warnings: tuple[str, ...] = ()
    # Retired fields of the removed engine cross-check. Analyses stored before
    # 2026-10-02 carry them, and the stored-analysis decoder refuses a field
    # the record type does not have, so they stay with empty defaults. Nothing
    # sets or reads them.
    text_verification: TextVerification | None = None
    divergent_page_numbers: tuple[int, ...] = ()
    glyph_resolutions: tuple[object, ...] = ()
    unresolved_glyph_reasons: tuple[str, ...] = ()
    #: Which engine read the text, and its version.
    text_engine: str | None = None
    text_engine_version: str | None = None

    @property
    def all_pages_inspected(self) -> bool:
        return len(self.pages) == self.page_count

    @property
    def non_empty_page_count(self) -> int:
        return sum(not page.text_empty for page in self.pages)

    @property
    def ocr_required_page_numbers(self) -> tuple[int, ...]:
        return tuple(
            page.source_page_number for page in self.pages if page.ocr_required
        )


def _safe_error(error_type: type[ProtocolPdfError], message: str) -> ProtocolPdfError:
    return error_type(message)


def _read_identity(stream: BinaryIO) -> tuple[int, str]:
    digest = hashlib.sha256()
    byte_size = 0
    while chunk := stream.read(_HASH_CHUNK_BYTES):
        byte_size += len(chunk)
        if byte_size > MAX_PROTOCOL_PDF_BYTES:
            raise _safe_error(
                ProtocolPdfTooLargeError,
                "Protocol PDF exceeds the 64 MiB size limit.",
            )
        digest.update(chunk)
    return byte_size, digest.hexdigest()


def _read_with_engine(source_path: Path) -> pdf_text_engine.EngineDocument:
    try:
        return pdf_text_engine.read_document(source_path)
    except PdfEngineDocumentError as error:
        if error.kind == "encrypted":
            raise _safe_error(
                ProtocolPdfEncryptedError,
                "Encrypted Protocol PDF requires a password.",
            ) from error
        raise _safe_error(
            ProtocolPdfMalformedError,
            "Protocol PDF structure is malformed, truncated, or unreadable.",
        ) from error
    except PdfEngineTimeoutError as error:
        raise _safe_error(
            ProtocolPdfWorkerTimeoutError,
            "Protocol PDF text extraction exceeded its time limit.",
        ) from error
    except PdfEngineError as error:
        raise _safe_error(
            ProtocolPdfWorkerError,
            "Protocol PDF text extraction did not complete.",
        ) from error


def _pages_from_engine(
    document: pdf_text_engine.EngineDocument,
) -> tuple[ProtocolPdfPage, ...]:
    pages: list[ProtocolPdfPage] = []
    for page_index, engine_page in enumerate(document.pages):
        warning = None
        text = engine_page.text
        if text is None:
            text = ""
            warning = "Page text could not be extracted; the page was retained as empty text."
        band = engine_page.bottom_band_offset
        reason = page_ocr_reason(text)
        unmapped = text.count(UNMAPPED_GLYPH)
        blocks = engine_page.blocks
        if unmapped:
            # Left out, never replaced: what stood there is unknown. The page
            # is already marked for OCR above, and the footer offset moves by
            # the characters removed before it.
            if band is not None:
                band -= text.count(UNMAPPED_GLYPH, 0, band)
            text = text.replace(UNMAPPED_GLYPH, "")
            blocks = tuple(
                block._replace(text=block.text.replace(UNMAPPED_GLYPH, ""))
                for block in blocks
            )
            warning = (
                f"{unmapped} character(s) on this page have no Unicode mapping "
                "in the PDF and were left out of its text; the page needs OCR."
            )
        if band is not None and 0 < band <= len(text):
            # Snap to the start of the line the boundary lands in. Measured on
            # ANKOM page 3 the band cut two characters into "protocols.io"
            # because the first two glyphs sit a hair above it, and a boundary
            # inside a word is not a boundary between units of evidence. A line
            # is itself a geometric unit, so this reads nothing.
            band = text.rfind("\n", 0, band) + 1
        if band is not None and (band > len(text) or not text[band:].strip()):
            band = None
        unreadable = unreadable_character_count(text)
        if warning is None and reason is None and unreadable:
            warning = (
                f"{unreadable} character(s) on this page could not be read "
                "from the PDF text layer."
            )
        pages.append(
            ProtocolPdfPage(
                source_page_number=page_index + 1,
                text=text,
                text_empty=not text.strip(),
                warning=warning,
                bottom_band_offset=band,
                ocr_required=reason is not None,
                ocr_reason=reason,
                blocks=blocks,
            )
        )
    return tuple(pages)


#: A line that holds punctuation and nothing else: what a Korean sentence
#: leaves when its Hangul glyphs carry no characters (" . ", "    ,").
_PUNCTUATION_ONLY_LINE = re.compile(r"[\s.,:;()\[\]·\-–※~/%]+")
#: Lane AN, EV4 decision d: a text layer is read as dropping its Hangul when
#: at least this share of its lines is punctuation alone and at most this
#: share of its visible characters is Hangul. Measured on the 203 corpus
#: PDFs (lane AN report): mfds_tricaine_eugenol 0.19 and 0.05; every other
#: Korean document 0.07 at most and 0.52 at least.
DROPPED_HANGUL_PUNCTUATION_LINES = 0.15
DROPPED_HANGUL_SHARE = 0.15


def _hangul_count(text: str) -> int:
    return sum(1 for character in text if "\uac00" <= character <= "\ud7a3")


def _with_dropped_hangul_pages_marked(
    pages: tuple[ProtocolPdfPage, ...],
) -> tuple[ProtocolPdfPage, ...]:
    """Mark for OCR the pages of a Korean text layer that drops its Hangul.

    Lane AN, EV4 decision d (human decision 2026-10-13). A PDF whose Korean
    font maps its glyphs to no character keeps its numbers, units and Latin
    words and loses every Hangul syllable, leaving sentences that are only
    their spaces and periods (mfds_tricaine_eugenol: "2 g 50 mL 0.1 M
    Na2-EDTA 1 mL" / " . "). No U+FFFD marks them, so ``page_ocr_reason``
    sees a usable page. Read across the document: a text layer that holds
    some Hangul, where punctuation-only lines are common and Hangul rare, is
    dropping it, and each of its pages is read by OCR (a page that keeps
    some Hangul in another font may still drop the rest).
    """

    readable = [page for page in pages if not page.ocr_required and page.text.strip()]
    lines = [line for page in readable for line in page.text.splitlines() if line.strip()]
    visible = sum(1 for page in readable for character in page.text if not character.isspace())
    hangul = sum(_hangul_count(page.text) for page in readable)
    if not lines or not visible or not hangul:
        return pages
    bare = sum(
        1 for line in lines
        if _PUNCTUATION_ONLY_LINE.fullmatch(line) and any(mark in line for mark in ".,")
    )
    if bare / len(lines) < DROPPED_HANGUL_PUNCTUATION_LINES or hangul / visible > DROPPED_HANGUL_SHARE:
        return pages
    marked = []
    for page in pages:
        if page in readable:
            page = replace(
                page,
                ocr_required=True,
                ocr_reason=OCR_REASON_DROPPED_GLYPHS,
                warning=(
                    "The PDF text layer of this page drops its Hangul characters; "
                    "the page needs OCR."
                ),
            )
        marked.append(page)
    return tuple(marked)


# Extraction is a pure function of the file's bytes, and the catalog asks for
# the same bytes many times in one request: listing the catalog extracts every
# entry's source, and one reviewer diff extracts the same source twice (once
# via get_entry, once via review).  Each extraction is a child process, so
# repeating it makes every other reader wait.
#
# The cache is keyed by the identity the server already owns -- the SHA-256 of
# the bytes, their length, and the file name the extraction reports -- so a
# hit is only ever returned for bytes that hashed to the same value.  It
# decides nothing: readiness, approval and evidence all read the same
# extraction they would have read without it.  It lives for the life of the
# process and is never written to disk.
_EXTRACTION_CACHE_ENTRIES = 8
_extraction_cache: OrderedDict[
    tuple[str, int, str], ProtocolPdfExtraction
] = OrderedDict()
_extraction_cache_lock = threading.Lock()


def _extraction_cache_key(source_path: Path) -> tuple[str, int, str] | None:
    """Hash the file to name it, or None when it cannot be read as one.

    A file that cannot be identified is not a cache miss to be worked around;
    it simply takes the uncached path, where the real extractor raises the
    specific error the caller needs to see.
    """

    try:
        with source_path.open("rb") as stream:
            byte_size, checksum = _read_identity(stream)
    except (OSError, ProtocolPdfError):
        return None
    return checksum, byte_size, source_path.name


def clear_protocol_pdf_cache() -> None:
    """Drop every cached extraction.  Used by tests, never by request paths."""

    with _extraction_cache_lock:
        _extraction_cache.clear()


def extract_protocol_pdf(path: str | Path) -> ProtocolPdfExtraction:
    """Validate and inspect a local PDF without mutating or interpreting it."""

    source_path = Path(path)
    key = _extraction_cache_key(source_path)
    if key is not None:
        with _extraction_cache_lock:
            cached = _extraction_cache.get(key)
            if cached is not None:
                _extraction_cache.move_to_end(key)
                return cached
    extraction = _extract_protocol_pdf_uncached(source_path)
    # Keyed by what the extraction itself measured, never by what was read a
    # moment earlier: if the file changed in between, this stores the new
    # bytes under the new identity instead of mislabelling them as the old.
    with _extraction_cache_lock:
        _extraction_cache[
            (extraction.sha256, extraction.byte_size, source_path.name)
        ] = extraction
        _extraction_cache.move_to_end(
            (extraction.sha256, extraction.byte_size, source_path.name)
        )
        while len(_extraction_cache) > _EXTRACTION_CACHE_ENTRIES:
            _extraction_cache.popitem(last=False)
    return extraction


def _extract_protocol_pdf_uncached(path: str | Path) -> ProtocolPdfExtraction:
    """Do the full read and parse for one exact file."""

    source_path = Path(path)
    try:
        initial_stat = source_path.stat()
    except FileNotFoundError as exc:
        raise _safe_error(
            ProtocolPdfNotFoundError,
            "Protocol PDF file does not exist.",
        ) from exc
    except OSError as exc:
        raise _safe_error(
            ProtocolPdfNotRegularFileError,
            "Protocol PDF path is not an accessible regular file.",
        ) from exc

    if not stat.S_ISREG(initial_stat.st_mode):
        raise _safe_error(
            ProtocolPdfNotRegularFileError,
            "Protocol PDF path is not a regular file.",
        )
    if initial_stat.st_size > MAX_PROTOCOL_PDF_BYTES:
        raise _safe_error(
            ProtocolPdfTooLargeError,
            "Protocol PDF exceeds the 64 MiB size limit.",
        )

    try:
        with source_path.open("rb") as stream:
            opened_stat = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened_stat.st_mode):
                raise _safe_error(
                    ProtocolPdfNotRegularFileError,
                    "Protocol PDF path is not a regular file.",
                )
            byte_size, checksum = _read_identity(stream)
            stream.seek(0)
            if stream.read(5) != b"%PDF-":
                raise _safe_error(
                    ProtocolPdfTypeError,
                    "Protocol input is not a validated PDF file.",
                )
            # The engine reads the same path in its own process while this
            # descriptor stays open, and the final fstat below proves the file
            # it read is the one that was hashed.
            document = _read_with_engine(source_path)
            final_stat = os.fstat(stream.fileno())
            try:
                path_stat = source_path.stat()
            except OSError:
                path_stat = None
    except ProtocolPdfError:
        raise
    except FileNotFoundError as exc:
        raise _safe_error(
            ProtocolPdfNotFoundError,
            "Protocol PDF file does not exist.",
        ) from exc
    except OSError as exc:
        raise _safe_error(
            ProtocolPdfNotRegularFileError,
            "Protocol PDF file could not be read.",
        ) from exc

    if (
        initial_stat.st_dev != final_stat.st_dev
        or initial_stat.st_ino != final_stat.st_ino
        or initial_stat.st_size != final_stat.st_size
        or initial_stat.st_mtime_ns != final_stat.st_mtime_ns
        or byte_size != final_stat.st_size
        # The engine opened the path, not this descriptor, so the path must
        # still name the same file: a replaced file would be a different inode.
        or path_stat is None
        or path_stat.st_ino != initial_stat.st_ino
        or path_stat.st_dev != initial_stat.st_dev
        or path_stat.st_mtime_ns != initial_stat.st_mtime_ns
    ):
        raise _safe_error(
            ProtocolPdfChangedError,
            "Protocol PDF changed during extraction.",
        )

    pages = _with_dropped_hangul_pages_marked(_pages_from_engine(document))
    warnings = list(document.warnings)
    warnings.extend(
        page.warning
        for page in pages
        if page.warning is not None and page.warning not in warnings
    )
    ocr_pages = [page.source_page_number for page in pages if page.ocr_required]
    if ocr_pages and len(ocr_pages) < len(pages):
        note = (
            "Pages without a usable text layer need OCR: "
            + ", ".join(str(number) for number in ocr_pages)
            + "."
        )
        warnings.append(note)

    return ProtocolPdfExtraction(
        original_filename=source_path.name,
        byte_size=byte_size,
        sha256=checksum,
        media_type=PDF_MEDIA_TYPE,
        page_count=len(pages),
        encrypted=document.encrypted,
        metadata=ProtocolPdfMetadata(**document.metadata),
        pages=pages,
        warnings=tuple(dict.fromkeys(warnings)),
        text_engine=pdf_text_engine.ENGINE_NAME,
        text_engine_version=pdf_text_engine.engine_version(),
    )


# ---------------------------------------------------------------------------
# Sources that are not PDFs (lane AN, FM; human decision 2026-10-13)
# ---------------------------------------------------------------------------
#
# A Word document and a photo of a protocol are read through the same evidence
# path as a PDF: each is turned into the PDF that extraction, OCR and the
# analysis read, and that PDF is the source every quote is checked against
# and the page a person is shown. The upload's own bytes are named by their
# checksum in the registration (``SourceConversion``); nothing about the
# evidence check changes.

DOCX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
#: The photo formats read, by upload media type: the Pillow format name the
#: file must open as.
IMAGE_MEDIA_TYPES = {
    "image/jpeg": "JPEG",
    "image/png": "PNG",
    "image/heic": "HEIF",
    "image/heif": "HEIF",
}
SOURCE_MEDIA_TYPES = frozenset({PDF_MEDIA_TYPE, DOCX_MEDIA_TYPE, *IMAGE_MEDIA_TYPES})
#: A photo is placed on its page at the resolution OCR renders pages at, so
#: OCR reads the photo's own pixels, neither enlarged nor shrunk.
IMAGE_PDF_DPI = pdf_text_engine.OCR_RENDER_DPI
#: The largest photo read, in pixels: a 48 MP phone photo fits; Pillow's own
#: decompression-bomb guard refuses at 179 MP.
MAX_SOURCE_IMAGE_PIXELS = 100_000_000
#: JPEG quality of the photo inside the PDF. The photo is the evidence a
#: person checks a handwritten value against, so it is kept close to as taken.
IMAGE_PDF_JPEG_QUALITY = 95
#: How long LibreOffice may take to turn one Word document into a PDF.
#: Measured on the 15 corpus .docx with LibreOffice 26.2: 3.4-27.8 s.
OFFICE_CONVERSION_TIMEOUT_SECONDS = 120
#: LibreOffice's ``soffice`` (human decision 2026-10-13: it must be installed
#: on the server). Empty: looked up on PATH and in its default install places.
OFFICE_CONVERTER_ENV = "VOINEY_LAB_OFFICE_CONVERTER"
#: Where LibreOffice is looked for besides PATH: its default install places.
OFFICE_CONVERTER_PLACES = (
    "/usr/bin/soffice",
    "/usr/lib/libreoffice/program/soffice",
    "/opt/libreoffice/program/soffice",
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
)
#: The date written into a photo's PDF (with a fixed title), so the same photo
#: gives the same PDF bytes.
_FIXED_PDF_DATE = time.gmtime(946684800)  # 2000-01-01T00:00:00Z


class ProtocolSourceFormatError(ProtocolPdfError):
    """The upload is not the format its media type names."""

    code = "invalid_source_file"


class ProtocolSourceTooLargeError(ProtocolPdfError):
    code = "protocol_image_too_large"


class ProtocolSourceConversionUnavailableError(ProtocolPdfError):
    """This server lacks what turns the upload into a PDF (a system install)."""

    code = "source_conversion_unavailable"

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class ProtocolSourceConversionError(ProtocolPdfError):
    """The converter ran and did not give a PDF."""

    code = "source_conversion_failed"


@dataclass(frozen=True)
class SourceConversion:
    """What a registered PDF was made from (lane AN, FM)."""

    media_type: str
    sha256: str
    byte_size: int
    converter: str
    #: Format details a person may want: the photo's pixel size, whether its
    #: EXIF orientation was applied.
    detail: tuple[tuple[str, object], ...] = ()
    #: The name the file was uploaded under; the PDF is registered as
    #: ``pdf_filename``.
    filename: str | None = None

    def payload(self) -> dict[str, object]:
        return {
            "media_type": self.media_type,
            "sha256": self.sha256,
            "byte_size": self.byte_size,
            "converter": self.converter,
            **({"filename": self.filename} if self.filename is not None else {}),
            **dict(self.detail),
        }


def pdf_filename(filename: str) -> str:
    """The name a converted upload's PDF is registered under: "notes.jpg" ->
    "notes.pdf"."""

    stem, dot, _ = filename.rpartition(".")
    return f"{stem if dot and stem else filename}.pdf"


def source_media_type(content_type: str) -> str:
    """The media type of an upload's Content-Type header, parameters dropped."""

    return content_type.casefold().split(";", 1)[0].strip()


def convert_source_to_pdf(
    source: str | Path,
    media_type: str,
    destination: str | Path,
    *,
    office_converter: str | None = None,
) -> SourceConversion:
    """Write the PDF an uploaded Word document or photo is read as.

    ``office_converter`` names LibreOffice's ``soffice`` for a Word document;
    by default it is looked up (``find_office_converter``).
    """

    media_type = source_media_type(media_type)
    if media_type in IMAGE_MEDIA_TYPES:
        return image_to_pdf(source, media_type, destination)
    if media_type == DOCX_MEDIA_TYPE:
        return docx_to_pdf(source, destination, converter=office_converter)
    raise ProtocolPdfTypeError("The upload is not a PDF, a Word document or a photo.")


def file_sha256(path: str | Path) -> str:
    """The SHA-256 of a file, read in chunks."""

    return _file_identity(Path(path))[0]


def _file_identity(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(_HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def image_to_pdf(
    source: str | Path, media_type: str, destination: str | Path
) -> SourceConversion:
    """One photo as a one-page PDF with no text layer, so OCR reads it.

    The page is the photo at ``IMAGE_PDF_DPI``, turned upright by its EXIF
    orientation; transparency is laid on white. A HEIC photo needs the
    optional ``pillow-heif`` package (``pip install -e '.[heic]'``); without
    it the upload is refused by name, never read in part.
    """

    source = Path(source)
    expected = IMAGE_MEDIA_TYPES[source_media_type(media_type)]
    try:
        from PIL import Image, ImageOps, UnidentifiedImageError
    except ImportError as exc:  # pragma: no cover - Pillow is a dependency
        raise ProtocolSourceConversionUnavailableError(
            "Pillow is not installed.", code="image_support_unavailable"
        ) from exc
    if expected == "HEIF":
        try:
            import pillow_heif
        except ImportError as exc:
            raise ProtocolSourceConversionUnavailableError(
                "HEIC photos need the optional pillow-heif package.",
                code="heic_support_unavailable",
            ) from exc
        pillow_heif.register_heif_opener()
    sha256, byte_size = _file_identity(source)
    try:
        with Image.open(source) as opened:
            if opened.format != expected:
                raise ProtocolSourceFormatError(
                    "The photo is not the format its media type names."
                )
            width, height = opened.size
            if width * height > MAX_SOURCE_IMAGE_PIXELS:
                raise ProtocolSourceTooLargeError("The photo has too many pixels.")
            orientation = opened.getexif().get(0x0112, 1)
            image = ImageOps.exif_transpose(opened)
            if image.mode in {"RGBA", "LA", "PA"} or (
                image.mode == "P" and "transparency" in image.info
            ):
                image = image.convert("RGBA")
                flat = Image.new("RGB", image.size, (255, 255, 255))
                flat.paste(image, mask=image.getchannel("A"))
                image = flat
            elif image.mode != "RGB":
                image = image.convert("RGB")
            image.save(
                Path(destination),
                "PDF",
                resolution=float(IMAGE_PDF_DPI),
                quality=IMAGE_PDF_JPEG_QUALITY,
                title="Protocol photo",
                creationDate=_FIXED_PDF_DATE,
                modDate=_FIXED_PDF_DATE,
            )
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError) as exc:
        if isinstance(exc, Image.DecompressionBombError):
            raise ProtocolSourceTooLargeError("The photo has too many pixels.") from exc
        raise ProtocolSourceFormatError("The photo could not be read.") from exc
    return SourceConversion(
        media_type=source_media_type(media_type),
        sha256=sha256,
        byte_size=byte_size,
        converter=f"pillow-{_pillow_version()}",
        detail=(
            ("pixels", [width, height]),
            ("exif_orientation_applied", orientation not in (None, 1)),
            ("pdf_dpi", IMAGE_PDF_DPI),
        ),
    )


def _pillow_version() -> str:
    from PIL import __version__

    return __version__


def find_office_converter(environ: dict[str, str] | None = None) -> str | None:
    """LibreOffice's ``soffice`` on this machine, or None.

    ``VOINEY_LAB_OFFICE_CONVERTER`` names it; a name that is not a file is
    None (the upload is refused by name) rather than some other LibreOffice.
    """

    import shutil

    env = os.environ if environ is None else environ
    configured = (env.get(OFFICE_CONVERTER_ENV) or "").strip()
    if configured:
        return configured if Path(configured).is_file() else None
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    return next((place for place in OFFICE_CONVERTER_PLACES if Path(place).is_file()), None)


#: The fonts LibreOffice prints Korean in (lane AN, FM a). A Korean Word
#: document names fonts a Linux server lacks ("맑은 고딕", "바탕", or a theme
#: font LibreOffice does not resolve); LibreOffice then prints its Latin
#: letters in DejaVu and its Hangul in another fallback (WenQuanYi), and the
#: PDF text that mixes the two loses its word spaces and breaks its lines at
#: every change of script ("시료2 g" / "을50 mL") -- quotes would no longer
#: match. With the Korean names and the generic families pointed at Nanum,
#: one font prints the whole line and the text reads as written (measured
#: 2026-10-13 with LibreOffice 26.2 and fonts-nanum). Only a document that
#: holds Hangul is printed so: LibreOffice's own fallback lists name Korean
#: fonts, and an English document printed under these rules took Nanum and
#: broke its lines and pages differently (7 of the 15 corpus .docx), so an
#: English document is printed as LibreOffice prints it. Only the LibreOffice
#: run sees this file; the system's own font set is included unchanged, and
#: without the Nanum fonts installed the rules match nothing.
_KOREAN_SANS = ("맑은 고딕", "Malgun Gothic", "굴림", "Gulim", "굴림체", "GulimChe",
                "돋움", "Dotum", "돋움체", "DotumChe", "함초롬돋움", "HCR Dotum",
                "Apple SD Gothic Neo", "Noto Sans KR", "Noto Sans CJK KR")
_KOREAN_SERIF = ("바탕", "Batang", "바탕체", "BatangChe", "궁서", "Gungsuh",
                 "함초롬바탕", "HCR Batang", "신명조", "Noto Serif KR", "Noto Serif CJK KR")


def _office_fontconfig(base: str) -> str:
    def alias(families: tuple[str, ...], target: str) -> str:
        return "".join(
            f'  <alias binding="strong"><family>{family}</family>'
            f"<prefer><family>{target}</family></prefer></alias>\n"
            for family in families
        )

    return (
        '<?xml version="1.0"?>\n<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">\n'
        f'<fontconfig>\n  <include ignore_missing="yes">{base}</include>\n'
        + alias(_KOREAN_SANS + ("sans-serif",), "NanumGothic")
        + alias(_KOREAN_SERIF + ("serif",), "NanumMyeongjo")
        + "</fontconfig>\n"
    )


def _docx_holds_hangul(archive) -> bool:
    """Whether the document's body, headers, footers or notes hold Hangul."""

    parts = [
        name for name in archive.namelist()
        if name.startswith("word/") and name.endswith(".xml")
        and name.split("/")[-1].startswith(("document", "header", "footer", "footnotes", "endnotes"))
    ]
    return any(
        re.search("[\uac00-\ud7a3]", archive.read(name).decode("utf-8", "replace"))
        for name in parts
    )


def docx_to_pdf(
    source: str | Path,
    destination: str | Path,
    *,
    converter: str | None = None,
    run=None,
) -> SourceConversion:
    """A Word document as the PDF LibreOffice prints it.

    LibreOffice must be installed on the server (human decision 2026-10-13;
    with ``fonts-nanum`` for Korean documents, see ``_office_fontconfig``).
    Without it the upload is refused with ``docx_conversion_unavailable`` and
    the reader is asked to save the document as a PDF; the document is never
    read some other way that the evidence check was not built for.
    """

    import subprocess
    import tempfile
    import zipfile

    source = Path(source)
    if not zipfile.is_zipfile(source):
        raise ProtocolSourceFormatError("The Word document is not a .docx file.")
    with zipfile.ZipFile(source) as archive:
        if "word/document.xml" not in archive.namelist():
            raise ProtocolSourceFormatError("The Word document is not a .docx file.")
        korean = _docx_holds_hangul(archive)
    converter = converter or find_office_converter()
    if converter is None:
        raise ProtocolSourceConversionUnavailableError(
            "LibreOffice is not installed on this server.",
            code="docx_conversion_unavailable",
        )
    run = run or subprocess.run
    sha256, byte_size = _file_identity(source)
    with tempfile.TemporaryDirectory(prefix="protocol-docx-") as root:
        work = Path(root)
        copy = work / "source.docx"
        copy.write_bytes(source.read_bytes())
        environment = dict(os.environ)
        if korean:
            fonts = work / "fonts.conf"
            fonts.write_text(
                _office_fontconfig(environment.get("FONTCONFIG_FILE") or "/etc/fonts/fonts.conf"),
                encoding="utf-8",
            )
            environment["FONTCONFIG_FILE"] = str(fonts)
        try:
            completed = run(
                [
                    converter,
                    "--headless",
                    "--norestore",
                    f"-env:UserInstallation={(work / 'profile').as_uri()}",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    str(work / "out"),
                    str(copy),
                ],
                capture_output=True,
                timeout=OFFICE_CONVERSION_TIMEOUT_SECONDS,
                check=False,
                env=environment,
            )
        except subprocess.TimeoutExpired as exc:
            raise ProtocolSourceConversionError(
                "LibreOffice did not finish converting the Word document."
            ) from exc
        except OSError as exc:
            raise ProtocolSourceConversionUnavailableError(
                "LibreOffice could not be started.", code="docx_conversion_unavailable"
            ) from exc
        produced = work / "out" / "source.pdf"
        if completed.returncode != 0 or not produced.is_file() or produced.stat().st_size == 0:
            raise ProtocolSourceConversionError(
                "LibreOffice did not produce a PDF from the Word document."
            )
        Path(destination).write_bytes(produced.read_bytes())
    return SourceConversion(
        media_type=DOCX_MEDIA_TYPE,
        sha256=sha256,
        byte_size=byte_size,
        converter=_office_converter_name(converter, run),
    )


def _office_converter_name(converter: str, run) -> str:
    """"libreoffice-24.2.7.2" from ``soffice --version``, or "libreoffice"."""

    try:
        completed = run([converter, "--version"], capture_output=True, timeout=30, check=False)
        match = re.search(rb"LibreOffice\s+([0-9][0-9.]*)", completed.stdout or b"")
    except Exception:  # noqa: BLE001 - the name is a label, not a check
        match = None
    return f"libreoffice-{match[1].decode()}" if match else "libreoffice"
