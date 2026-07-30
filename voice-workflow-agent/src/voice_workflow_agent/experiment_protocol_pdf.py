"""Local Protocol PDF validation, byte identity, metadata, and page text.

The SHA-256 value returned here identifies exact file bytes. It does not
indicate that a Protocol is trusted, approved, official, or current.
"""

from __future__ import annotations

import hashlib
import logging
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from pypdf import PdfReader
from pypdf.errors import (
    FileNotDecryptedError,
    PdfReadError,
    PyPdfError,
    WrongPasswordError,
)


PDF_MEDIA_TYPE = "application/pdf"
MAX_PROTOCOL_PDF_BYTES = 64 * 1024 * 1024
_HASH_CHUNK_BYTES = 1024 * 1024
_METADATA_FIELDS = {
    "title": "/Title",
    "author": "/Author",
    "subject": "/Subject",
    "creator": "/Creator",
    "producer": "/Producer",
    "creation_date": "/CreationDate",
    "modification_date": "/ModDate",
}


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


class ProtocolPdfChangedError(ProtocolPdfError):
    code = "protocol_pdf_changed_during_extraction"


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

    @property
    def all_pages_inspected(self) -> bool:
        return len(self.pages) == self.page_count

    @property
    def non_empty_page_count(self) -> int:
        return sum(not page.text_empty for page in self.pages)


class _PdfWarningCollector(logging.Handler):
    """Collect recoverable pypdf parser warnings without emitting them."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage().strip()
        except (TypeError, ValueError):
            message = "pypdf reported a recoverable document warning."
        if message and message not in self.messages:
            self.messages.append(message)


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


def _metadata_value(raw_metadata: object, key: str) -> str | None:
    try:
        value = raw_metadata.get(key)  # type: ignore[attr-defined]
    except (AttributeError, KeyError, TypeError, ValueError, PyPdfError):
        return None
    if value is None:
        return None
    try:
        text = str(value).strip()
    except (TypeError, ValueError):
        return None
    return text or None


def _read_metadata(reader: PdfReader) -> tuple[ProtocolPdfMetadata, str | None]:
    try:
        raw_metadata = reader.metadata
    except (KeyError, TypeError, ValueError, PyPdfError):
        raw_metadata = None
        warning = "Embedded PDF metadata could not be read."
    else:
        warning = None
    values = {
        field: _metadata_value(raw_metadata, key)
        for field, key in _METADATA_FIELDS.items()
    }
    return ProtocolPdfMetadata(**values), warning


def _open_reader(stream: BinaryIO) -> PdfReader:
    try:
        reader = PdfReader(stream, strict=False)
        if reader.is_encrypted:
            try:
                decrypt_result = reader.decrypt("")
            except (FileNotDecryptedError, PdfReadError, WrongPasswordError):
                decrypt_result = 0
            if not decrypt_result:
                raise _safe_error(
                    ProtocolPdfEncryptedError,
                    "Encrypted Protocol PDF requires a password.",
                )
        return reader
    except ProtocolPdfError:
        raise
    except (FileNotDecryptedError, WrongPasswordError) as exc:
        raise _safe_error(
            ProtocolPdfEncryptedError,
            "Encrypted Protocol PDF requires a password.",
        ) from exc
    except (OSError, PdfReadError, PyPdfError, TypeError, ValueError) as exc:
        raise _safe_error(
            ProtocolPdfMalformedError,
            "Protocol PDF structure is malformed, truncated, or unreadable.",
        ) from exc


def _extract_pages(reader: PdfReader) -> tuple[ProtocolPdfPage, ...]:
    try:
        page_count = len(reader.pages)
    except (FileNotDecryptedError, OSError, PdfReadError, PyPdfError, TypeError, ValueError) as exc:
        raise _safe_error(
            ProtocolPdfMalformedError,
            "Protocol PDF page structure could not be opened.",
        ) from exc

    pages: list[ProtocolPdfPage] = []
    for page_index in range(page_count):
        warning = None
        try:
            text = reader.pages[page_index].extract_text() or ""
        except (
            FileNotDecryptedError,
            KeyError,
            OSError,
            PdfReadError,
            PyPdfError,
            TypeError,
            ValueError,
        ):
            text = ""
            warning = "Page text could not be extracted; the page was retained as empty text."
        pages.append(
            ProtocolPdfPage(
                source_page_number=page_index + 1,
                text=text,
                text_empty=not text.strip(),
                warning=warning,
            )
        )
    return tuple(pages)


def extract_protocol_pdf(path: str | Path) -> ProtocolPdfExtraction:
    """Validate and inspect a local PDF without mutating or interpreting it."""

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

    collector = _PdfWarningCollector()
    pypdf_logger = logging.getLogger("pypdf")
    pypdf_logger.addHandler(collector)
    try:
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
                stream.seek(0)
                reader = _open_reader(stream)
                metadata, metadata_warning = _read_metadata(reader)
                pages = _extract_pages(reader)
                final_stat = os.fstat(stream.fileno())
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
    finally:
        pypdf_logger.removeHandler(collector)

    if (
        initial_stat.st_dev != final_stat.st_dev
        or initial_stat.st_ino != final_stat.st_ino
        or initial_stat.st_size != final_stat.st_size
        or initial_stat.st_mtime_ns != final_stat.st_mtime_ns
        or byte_size != final_stat.st_size
    ):
        raise _safe_error(
            ProtocolPdfChangedError,
            "Protocol PDF changed during extraction.",
        )

    warnings = list(collector.messages)
    if metadata_warning and metadata_warning not in warnings:
        warnings.append(metadata_warning)
    warnings.extend(
        page.warning
        for page in pages
        if page.warning is not None and page.warning not in warnings
    )

    return ProtocolPdfExtraction(
        original_filename=source_path.name,
        byte_size=byte_size,
        sha256=checksum,
        media_type=PDF_MEDIA_TYPE,
        page_count=len(pages),
        encrypted=reader.is_encrypted,
        metadata=metadata,
        pages=pages,
        warnings=tuple(warnings),
    )
