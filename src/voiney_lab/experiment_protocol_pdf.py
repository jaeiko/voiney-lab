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
"""

from __future__ import annotations

import hashlib
import os
import stat
import threading
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass
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
    "MAX_PROTOCOL_PDF_BYTES",
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
    "TextVerification",
    "clear_protocol_pdf_cache",
    "extract_protocol_pdf",
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
#: Share of a page's non-space characters that are U+FFFD, private use or
#: unassigned, at or above which the page is treated as unreadable and needing
#: OCR. Measured on 2026-10-02 the four local sources hold none at all with
#: PyMuPDF; the one glyph the previous engine could not map (1 of 1303 on a
#: page) is the kind of stray this keeps from flagging a whole page.
UNREADABLE_PAGE_RATIO = 0.05
_UNREADABLE_CATEGORIES = frozenset({"Co", "Cn"})


def unreadable_character_count(text: str) -> int:
    """Characters that cannot be document content: U+FFFD, private use, unassigned."""

    return sum(
        1
        for character in text
        if character == "\ufffd"
        or unicodedata.category(character) in _UNREADABLE_CATEGORIES
    )


def page_ocr_reason(text: str) -> str | None:
    """Why a page needs OCR, or None when its text layer is usable."""

    visible = sum(1 for character in text if not character.isspace())
    if visible == 0:
        return OCR_REASON_NO_TEXT
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
        if band is not None and 0 < band <= len(text):
            # Snap to the start of the line the boundary lands in. Measured on
            # ANKOM page 3 the band cut two characters into "protocols.io"
            # because the first two glyphs sit a hair above it, and a boundary
            # inside a word is not a boundary between units of evidence. A line
            # is itself a geometric unit, so this reads nothing.
            band = text.rfind("\n", 0, band) + 1
        if band is not None and (band > len(text) or not text[band:].strip()):
            band = None
        reason = page_ocr_reason(text)
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
                blocks=engine_page.blocks,
            )
        )
    return tuple(pages)


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

    pages = _pages_from_engine(document)
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
