"""Local Protocol PDF validation, byte identity, metadata, and page text.

The SHA-256 value returned here identifies exact file bytes. It does not
indicate that a Protocol is trusted, approved, official, or current.

Two PDF libraries are used, deliberately, for different jobs:

* ``pypdf`` owns file structure, encryption detection, document metadata and
  the recoverable-warning taxonomy.  Its *text* extractor is not used.
* ``pypdfium2`` owns page text.  pypdf's extractor silently substituted
  private-use glyphs for real characters -- ``(50:49:1)`` came back as
  ``\ue08150\ue09249\ue0921)`` and ``00:30:00`` as ``00\ue09230\ue09200`` --
  which exact-evidence validation cannot catch, because the corrupted text is
  self-consistent and hashes cleanly.

Because a silent substitution is invisible to every downstream check, the
extracted text is cross-checked against an independent engine before it is
allowed to become canonical evidence.  See ``verify_page_text``.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import re
import stat
import subprocess
import unicodedata
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import BinaryIO

import pypdfium2
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


COMPARATOR_COMMAND = "pdftotext"
COMPARATOR_TIMEOUT_SECONDS = 60.0
_COMPARATOR_MAX_OUTPUT_BYTES = 32 * 1024 * 1024
_HYPHENS = "\u2010\u2011\u2012\u2013\u2014\u2212-"
_UNMAPPED_CATEGORIES = frozenset({"Co", "Cn"})


class TextVerification(str, Enum):
    """Whether an independent engine confirmed the extracted page text."""

    VERIFIED = "verified"
    MISMATCH = "mismatch"
    COMPARATOR_UNAVAILABLE = "comparator_unavailable"


def canonical_text_census(text: str) -> Counter[str]:
    """Engine-agnostic character census used to compare two extractions.

    Two correct engines legitimately disagree about line breaking, reading
    order of superscripts, control-character padding and end-of-line hyphens,
    so all four are normalized away.  What survives is an order-independent
    multiset of the characters that carry meaning.

    Unmapped code points -- private use (``Co``) and noncharacter or unassigned
    (``Cn``) -- are deliberately *kept*: they are exactly the corruption this
    census exists to detect.  Comparing only alphanumerics would not work: a
    corrupted ``(50:49:1)`` and a correct one both reduce to ``50491``.

    Unmapped code points are *not* compared here, and the reason is structural
    rather than a judgement that they do not matter.  Each engine fails at an
    unmapped glyph in its own way -- pdfium emits U+FFFE, pypdf emits a
    private-use code point, poppler deletes the character and joins the words
    -- so comparing placeholders compares the engines' defects instead of the
    document.  Those positions are decided one at a time against the PDF's own
    ToUnicode declaration, and admitted page text contains none of them: see
    ``resolve_unmapped_page_text``.  What this census is for is the other
    corruption, a real character silently replaced by another, which no
    per-position resolution would catch.
    """

    census: Counter[str] = Counter()
    for character in unicodedata.normalize("NFKC", text):
        if character.isspace():
            continue
        if unicodedata.category(character) in {"Cc", "Cf"} | _UNMAPPED_CATEGORIES:
            continue
        if character in _HYPHENS:
            continue
        census[character] += 1
    return census


def unmapped_code_points(text: str) -> Counter[str]:
    """Code points that can never be document content.

    A private-use code point has no meaning outside the font that defines it,
    and a noncharacter such as U+FFFE is permanently reserved and can never be
    assigned.  Either one appearing in extracted text means the extractor could
    not map a glyph to a character, so the character at that position is
    unknown.

    What it *was* is not recoverable here.  In the sources measured, U+FFFE
    stands where a hyphen or dash belongs, but which one is not determinable
    from the extraction: a comparison engine reports a plain hyphen at some of
    those positions, and elsewhere the surrounding text uses an en dash.
    Substituting either would be repairing the source into something it may not
    say, so this is reported and refused rather than corrected.
    """

    return Counter(
        character
        for character in unicodedata.normalize("NFKC", text)
        if unicodedata.category(character) in _UNMAPPED_CATEGORIES
    )


_MIN_RESOLUTION_ANCHOR = 3
_RESOLUTION_ANCHOR = 8


@dataclass(frozen=True)
class GlyphResolution:
    """One unmapped position, and the document declaration that resolved it."""

    source_page_number: int
    text_offset: int
    unmapped_code_point: int
    resolved_character: str
    resolved_by: str
    declared_by_document: bool


def _declared_unicode_values(page: object) -> frozenset[str]:
    """Every character the fonts on this page declare through ToUnicode.

    This is the document speaking about its own glyphs.  A resolution is only
    accepted when the character an engine reports is one of these, so the
    interpretation comes from the PDF's declaration and never from a vote
    between placeholders or from what a character looks like.
    """

    resources = page.get("/Resources") if hasattr(page, "get") else None
    fonts = resources.get("/Font") if resources else None
    if not fonts:
        return frozenset()
    values: set[str] = set()
    for reference in fonts.values():
        try:
            font = reference.get_object()
            stream = font.get("/ToUnicode")
            if stream is None:
                continue
            data = stream.get_object().get_data().decode("latin-1")
        except Exception:  # noqa: BLE001 - a font we cannot read declares nothing
            continue
        for block in re.finditer(r"beginbfchar(.*?)endbfchar", data, re.S):
            for _, target in re.findall(
                r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block.group(1)
            ):
                if len(target) <= 4:
                    values.add(chr(int(target, 16)))
        for block in re.finditer(r"beginbfrange(.*?)endbfrange", data, re.S):
            for low, high, target in re.findall(
                r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>",
                block.group(1),
            ):
                base = int(target, 16)
                for step in range(int(high, 16) - int(low, 16) + 1):
                    if base + step <= 0xFFFF:
                        values.add(chr(base + step))
    return frozenset(values)


def _dense(text: str) -> tuple[str, tuple[int, ...]]:
    """Text without whitespace, plus each kept character's original offset.

    Engines disagree about line breaking, so positions are matched with the
    whitespace removed.
    """

    kept = [(index, char) for index, char in enumerate(text) if not char.isspace()]
    return "".join(char for _, char in kept), tuple(index for index, _ in kept)


def _anchors(dense: str, position: int) -> tuple[str, str]:
    """Mapped characters either side of a position, used to locate it again."""

    left: list[str] = []
    index = position - 1
    while index >= 0 and len(left) < _RESOLUTION_ANCHOR:
        if unicodedata.category(dense[index]) in _UNMAPPED_CATEGORIES:
            break
        left.append(dense[index])
        index -= 1
    right: list[str] = []
    index = position + 1
    while index < len(dense) and len(right) < _RESOLUTION_ANCHOR:
        if unicodedata.category(dense[index]) in _UNMAPPED_CATEGORIES:
            break
        right.append(dense[index])
        index += 1
    return "".join(reversed(left)), "".join(right)


def _character_between(candidate: str, left: str, right: str) -> tuple[str, str]:
    """What one engine has between these anchors.

    Returns a status and, for ``character``, the single character found.
    ``deleted`` means the engine joined the anchors with nothing between them,
    which is a known lossy behaviour and not a statement about the character.
    """

    if len(left) < _MIN_RESOLUTION_ANCHOR or len(right) < _MIN_RESOLUTION_ANCHOR:
        return "alignment_failed", ""
    found = re.findall(
        re.escape(left) + r"(.?)" + re.escape(right), candidate, re.S
    )
    if len(found) != 1:
        return "alignment_failed", ""
    character = found[0]
    if not character:
        return "deleted", ""
    if unicodedata.category(character) in _UNMAPPED_CATEGORIES:
        return "unresolved", character
    return "character", character


def resolve_unmapped_page_text(
    text: str,
    *,
    source_page_number: int,
    candidates: Mapping[str, str],
    declared: frozenset[str],
) -> tuple[str, tuple[GlyphResolution, ...], tuple[str, ...]]:
    """Read each unmapped position from the document's own declaration.

    Reading a character the PDF declares in its ToUnicode map is not repair.
    It is reading the source, and the server owning the authority over its
    evidence is exactly this case.  What is forbidden is everything that is
    *not* the document speaking: no majority vote between placeholders, no
    "it looks like a hyphen", no inference from the surrounding words.

    Two engines reporting different real characters is a genuine conflict and
    is refused.  An engine that deleted the position says nothing, so it
    neither resolves nor conflicts.
    """

    dense, offsets = _dense(text)
    # Anchors come from whitespace-free text, so the candidates must be matched
    # the same way: the engines disagree about line breaking, and that is not a
    # disagreement about characters.
    dense_candidates = {
        engine: _dense(candidate)[0] for engine, candidate in candidates.items()
    }
    resolutions: list[GlyphResolution] = []
    failures: list[str] = []
    replacements: dict[int, str] = {}
    for position, character in enumerate(dense):
        if unicodedata.category(character) not in _UNMAPPED_CATEGORIES:
            continue
        left, right = _anchors(dense, position)
        reported: dict[str, str] = {}
        statuses: set[str] = set()
        for engine, candidate in dense_candidates.items():
            status, found = _character_between(candidate, left, right)
            statuses.add(status)
            if status == "character":
                reported[engine] = found
        distinct = set(reported.values())
        if len(distinct) > 1:
            failures.append(
                f"page {source_page_number}: engines report different "
                f"characters for one unmapped position"
            )
            continue
        if not distinct:
            failures.append(
                f"page {source_page_number}: no engine resolved an unmapped "
                f"position ({'unresolved' if 'unresolved' in statuses else 'alignment_failed' if 'alignment_failed' in statuses else 'deleted'})"
            )
            continue
        found = next(iter(distinct))
        if found not in declared:
            failures.append(
                f"page {source_page_number}: a character was reported for an "
                f"unmapped position but the document does not declare it"
            )
            continue
        engine = sorted(
            name for name, value in reported.items() if value == found
        )[0]
        resolutions.append(
            GlyphResolution(
                source_page_number=source_page_number,
                text_offset=offsets[position],
                unmapped_code_point=ord(character),
                resolved_character=found,
                resolved_by=engine,
                declared_by_document=True,
            )
        )
        replacements[offsets[position]] = found
    if failures:
        # The page is refused, so nothing was read into admitted text. Report
        # no resolutions rather than resolutions that were never applied.
        return text, (), tuple(failures)
    if not replacements:
        return text, (), ()
    rebuilt = "".join(
        replacements.get(index, char) for index, char in enumerate(text)
    )
    return rebuilt, tuple(resolutions), ()


def _comparator_pages(path: Path) -> tuple[str, ...] | None:
    """Extract page text with an independent engine, or None if unavailable.

    The comparator is a separate process, so it is invoked with an argument
    vector (never a shell string), with the source path passed after ``--`` so
    a filename can never be read as an option, under a wall-clock timeout, and
    with its output size bounded.
    """

    executable = shutil.which(COMPARATOR_COMMAND)
    if executable is None:
        return None
    try:
        completed = subprocess.run(
            [executable, "-q", "--", str(path), "-"],
            capture_output=True,
            timeout=COMPARATOR_TIMEOUT_SECONDS,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    payload = completed.stdout[:_COMPARATOR_MAX_OUTPUT_BYTES]
    if len(completed.stdout) > _COMPARATOR_MAX_OUTPUT_BYTES:
        return None
    text = payload.decode("utf-8", errors="replace")
    pages = text.split("\f")
    if pages and not pages[-1].strip():
        pages = pages[:-1]
    return tuple(pages)


def verify_page_text(
    path: Path,
    pages: tuple[ProtocolPdfPage, ...],
) -> tuple[TextVerification, tuple[int, ...]]:
    """Cross-check primary page text against an independent engine.

    Any unmapped code point still present is decided here, before the
    comparator is consulted at all.  By this point resolution has already run,
    so a surviving unmapped position is one the document does not declare, or
    one two engines read differently.  It is a property of our own extraction
    rather than of any disagreement, and deciding it only by comparison would
    leave it undetected wherever no comparison engine is installed -- and that
    verdict is acknowledgeable by a person, so genuinely unreadable text could
    be waved through.  Refusing it is not acknowledgeable and does not depend
    on the environment.
    """

    unmapped = tuple(
        page.source_page_number
        for page in pages
        if unmapped_code_points(page.text)
    )
    if unmapped:
        return TextVerification.MISMATCH, unmapped
    comparator = _comparator_pages(path)
    if comparator is None:
        return TextVerification.COMPARATOR_UNAVAILABLE, ()
    if len(comparator) != len(pages):
        return TextVerification.MISMATCH, ()
    divergent = tuple(
        page.source_page_number
        for page, other in zip(pages, comparator)
        if canonical_text_census(page.text) != canonical_text_census(other)
    )
    if divergent:
        return TextVerification.MISMATCH, divergent
    return TextVerification.VERIFIED, ()


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
    text_verification: TextVerification = TextVerification.COMPARATOR_UNAVAILABLE
    divergent_page_numbers: tuple[int, ...] = ()
    # One entry per unmapped position read from the document's own ToUnicode
    # declaration, naming the position, the character and the engine that
    # reported it.  A position that could not be recorded this way was not
    # resolved, and the extraction fails its cross-check instead.
    glyph_resolutions: tuple[GlyphResolution, ...] = ()
    unresolved_glyph_reasons: tuple[str, ...] = ()

    @property
    def text_cross_checked(self) -> bool:
        return self.text_verification is TextVerification.VERIFIED

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


def _pypdfium_page_texts(path: Path, page_count: int) -> list[str | None]:
    """Read page text with the primary engine; None marks an unreadable page."""

    texts: list[str | None] = [None] * page_count
    document = None
    try:
        document = pypdfium2.PdfDocument(path)
        available = min(page_count, len(document))
        for page_index in range(available):
            try:
                page = document[page_index]
                text = page.get_textpage().get_text_range()
                # Line-ending convention only.  PDF has no line terminators of
                # its own; pypdfium2 renders CRLF while every other engine and
                # every stored excerpt uses LF.  No character of content is
                # added, removed, or substituted here.
                texts[page_index] = text.replace("\r\n", "\n").replace("\r", "\n")
            except Exception:  # noqa: BLE001 - one bad page must not lose the rest
                texts[page_index] = None
    except Exception:  # noqa: BLE001 - fall through with every page unreadable
        pass
    finally:
        if document is not None:
            try:
                document.close()
            except Exception:  # noqa: BLE001
                pass
    return texts


def _extract_pages(
    reader: PdfReader,
    source_path: Path,
) -> tuple[
    tuple[ProtocolPdfPage, ...],
    tuple[GlyphResolution, ...],
    tuple[str, ...],
]:
    try:
        page_count = len(reader.pages)
    except (FileNotDecryptedError, OSError, PdfReadError, PyPdfError, TypeError, ValueError) as exc:
        raise _safe_error(
            ProtocolPdfMalformedError,
            "Protocol PDF page structure could not be opened.",
        ) from exc

    primary = _pypdfium_page_texts(source_path, page_count)
    pages: list[ProtocolPdfPage] = []
    resolutions: list[GlyphResolution] = []
    failures: list[str] = []
    # Both secondary engines are expensive -- one is a subprocess, the other
    # re-reads every content stream -- and the overwhelming majority of pages
    # have nothing to resolve.  They are consulted only for a page that does.
    comparator: tuple[str, ...] | None = None
    comparator_loaded = False
    for page_index in range(page_count):
        warning = None
        text = primary[page_index]
        if text is None:
            text = ""
            warning = "Page text could not be extracted; the page was retained as empty text."
        if unmapped_code_points(text):
            if not comparator_loaded:
                comparator_loaded = True
                comparator = _comparator_pages(source_path)
                if comparator is not None and len(comparator) != page_count:
                    comparator = None
            candidates: dict[str, str] = {}
            declared_page_text = _declared_page_text(reader, page_index)
            if declared_page_text is not None:
                candidates["pypdf"] = declared_page_text
            if comparator is not None:
                candidates[COMPARATOR_COMMAND] = comparator[page_index]
            try:
                declared = _declared_unicode_values(reader.pages[page_index])
            except Exception:  # noqa: BLE001 - an unreadable font declares nothing
                declared = frozenset()
            text, resolved, page_failures = resolve_unmapped_page_text(
                text,
                source_page_number=page_index + 1,
                candidates=candidates,
                declared=declared,
            )
            resolutions.extend(resolved)
            failures.extend(page_failures)
        pages.append(
            ProtocolPdfPage(
                source_page_number=page_index + 1,
                text=text,
                text_empty=not text.strip(),
                warning=warning,
            )
        )
    return tuple(pages), tuple(resolutions), tuple(failures)


def _declared_page_text(reader: PdfReader, page_index: int) -> str | None:
    """One page's text from the engine that applies the document's ToUnicode.

    This engine is not fit to be a census comparator -- measured against the
    primary it silently drops parentheses, so it reports divergence on 68 of
    the 99 pages across the four local sources, including two documents with
    no unmapped glyph at all.  It is used only to report what character stands
    at one already-identified position, and that report is accepted only when
    the document's own ToUnicode declares it.
    """

    try:
        return reader.pages[page_index].extract_text()
    except Exception:  # noqa: BLE001 - absence is recorded, never assumed clean
        return None


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
                (
                    pages,
                    glyph_resolutions,
                    glyph_failures,
                ) = _extract_pages(reader, source_path)
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

    verification, divergent = verify_page_text(source_path, pages)
    if glyph_failures:
        # An unmapped position the document does not settle. Refused for the
        # same reason a census divergence is refused: the text we would quote
        # as canonical evidence contains a character we cannot source.
        verification = TextVerification.MISMATCH
        divergent = tuple(
            sorted(
                {*divergent}
                | {
                    page.source_page_number
                    for page in pages
                    if unmapped_code_points(page.text)
                }
            )
        )
    # Recorded, never raised.  Extraction is also a read-time operation used to
    # browse and review an already registered Protocol, and a failed
    # cross-check is exactly when a person most needs to open the document.
    # Admission and readiness act on this verdict; see plan_protocol_chunks
    # and assess_readiness.
    verification_warning = {
        TextVerification.MISMATCH: (
            "Extracted page text did not match an independent extraction engine."
        ),
        TextVerification.COMPARATOR_UNAVAILABLE: (
            "Extracted page text was not cross-checked: no comparison engine "
            "is available in this environment."
        ),
    }.get(verification)
    if verification_warning and verification_warning not in warnings:
        warnings.append(verification_warning)
    for reason in glyph_failures:
        if reason not in warnings:
            warnings.append(reason)

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
        text_verification=verification,
        divergent_page_numbers=divergent,
        glyph_resolutions=glyph_resolutions,
        unresolved_glyph_reasons=glyph_failures,
    )
