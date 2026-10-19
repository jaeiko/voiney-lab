"""Bounded OCR provider contract for source-preserving protocol onboarding.

OCR is text extraction evidence, not protocol approval. Providers receive one
verified immutable PDF path and must return page-numbered text for the same
SHA-256 source. This module validates the untrusted result before the catalog
may persist it for human review.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


MAX_OCR_PAGE_TEXT_BYTES = 1 * 1024 * 1024
MAX_OCR_DOCUMENT_TEXT_BYTES = 8 * 1024 * 1024
_SAFE_PROVIDER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ProtocolOcrError(RuntimeError):
    code = "protocol_ocr_error"


class ProtocolOcrUnavailableError(ProtocolOcrError):
    code = "protocol_ocr_not_configured"


class ProtocolOcrResultError(ProtocolOcrError):
    code = "protocol_ocr_result_invalid"


@dataclass(frozen=True)
class OcrValueBox:
    """Where a value OCR read sits on its rendered page (lane AN, FM).

    A number with the unit printed after it ("12,000 ×g", "0.5 mL"), and its
    box in the pixels of the page rendered at ``OcrPage.render_dpi``. Kept so
    that a value read from handwriting or a photo can be shown beside the
    piece of the image it was read from, for a person to check (the review
    tab is lane RV's).
    """

    text: str
    x0: float
    y0: float
    x1: float
    y1: float


#: The most value boxes kept for one page.
MAX_OCR_VALUE_BOXES = 2_000


@dataclass(frozen=True)
class OcrValueReading:
    """One number the chosen engine read on a page, and what the other engine
    read in the same place (lane AN3, decision A; human decision 2026-10-16).

    ``token`` is the number with the unit written right after it, in the form
    the two engines are compared by ("4000rpm", "37°c"); ``text`` is how the
    chosen engine wrote it; the box is where it stands on the page rendered
    at ``OcrPage.render_dpi``. ``other_token`` and ``other_text`` are the
    other engine's number in the same place, or None when it read no number
    there. Equal tokens are the deterministic ground on which a value runs
    without a person.
    """

    token: str
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    other_token: str | None = None
    other_text: str | None = None


@dataclass(frozen=True)
class OcrPage:
    source_page_number: int
    text: str
    confidence: float | None = None
    #: The engine that produced this page's text, and its version. Optional:
    #: a provider that reads every page with one engine names it only once,
    #: on the result.
    provider: str | None = None
    provider_version: str | None = None
    #: True when two engines read this page and their numbers or units
    #: differ. A mark for the reviewer; it blocks nothing.
    numeric_review_required: bool = False
    #: The values the chosen engine read, with their boxes, and the
    #: resolution the page was rendered at for it (lane AN, FM).
    value_boxes: tuple[OcrValueBox, ...] = ()
    render_dpi: int | None = None
    #: Lane AN3, decision A: the engine that also read the page, its whole
    #: text, and each number of the chosen engine with the other engine's
    #: number in the same place. ``value_readings`` is None when the numbers
    #: were not compared place by place (one engine read the page, or an
    #: engine gave no word boxes); an empty tuple is a page compared that
    #: holds no number.
    other_provider: str | None = None
    other_provider_version: str | None = None
    other_text: str | None = None
    value_readings: tuple[OcrValueReading, ...] | None = None
    #: The other engine's page confidence (lane AN3, round 2, decision ②):
    #: a page either engine read with low confidence settles no value.
    other_confidence: float | None = None


@dataclass(frozen=True)
class OcrResult:
    source_sha256: str
    provider: str
    provider_version: str
    pages: tuple[OcrPage, ...]
    languages: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


class ProtocolOcrProvider(Protocol):
    def recognize(
        self,
        source_pdf: Path,
        *,
        source_sha256: str,
        page_count: int,
    ) -> OcrResult: ...


def _finite_edges(*edges: object) -> bool:
    return all(
        isinstance(edge, (int, float)) and not isinstance(edge, bool)
        and math.isfinite(edge) and edge >= 0
        for edge in edges
    )


def _short_text(value: object) -> bool:
    return isinstance(value, str) and 0 < len(value) <= 64


def _valid_reading(reading: object) -> bool:
    if not isinstance(reading, OcrValueReading):
        return False
    if not (_short_text(reading.token) and _short_text(reading.text)):
        return False
    if (reading.other_token is None) != (reading.other_text is None):
        return False
    if reading.other_token is not None and not (
        _short_text(reading.other_token) and _short_text(reading.other_text)
    ):
        return False
    return (
        _finite_edges(reading.x0, reading.y0, reading.x1, reading.y1)
        and reading.x0 <= reading.x1 and reading.y0 <= reading.y1
    )


def validate_ocr_result(
    result: OcrResult,
    *,
    expected_sha256: str,
    expected_page_count: int,
) -> OcrResult:
    """Validate provider output and return its canonical immutable projection."""

    if not isinstance(result, OcrResult):
        raise ProtocolOcrResultError("OCR provider returned an invalid envelope.")
    if (
        _SHA256.fullmatch(expected_sha256) is None
        or result.source_sha256 != expected_sha256
    ):
        raise ProtocolOcrResultError("OCR source identity does not match the PDF.")
    if (
        not isinstance(expected_page_count, int)
        or isinstance(expected_page_count, bool)
        or expected_page_count < 1
        or expected_page_count > 10_000
    ):
        raise ProtocolOcrResultError("OCR page count is invalid.")
    if (
        _SAFE_PROVIDER.fullmatch(result.provider) is None
        or _SAFE_PROVIDER.fullmatch(result.provider_version) is None
    ):
        raise ProtocolOcrResultError("OCR provider identity is invalid.")
    expected_pages = tuple(range(1, expected_page_count + 1))
    actual_pages = tuple(page.source_page_number for page in result.pages)
    if actual_pages != expected_pages:
        raise ProtocolOcrResultError(
            "OCR must return every source page exactly once and in order."
        )
    total_bytes = 0
    other_bytes = 0
    non_empty = 0
    canonical_pages: list[OcrPage] = []
    for page in result.pages:
        if not isinstance(page.text, str):
            raise ProtocolOcrResultError("OCR page text is invalid.")
        encoded = page.text.encode("utf-8")
        if len(encoded) > MAX_OCR_PAGE_TEXT_BYTES:
            raise ProtocolOcrResultError("OCR page text exceeds the size limit.")
        total_bytes += len(encoded)
        if total_bytes > MAX_OCR_DOCUMENT_TEXT_BYTES:
            raise ProtocolOcrResultError("OCR document text exceeds the size limit.")
        if page.other_text is not None:
            if not isinstance(page.other_text, str):
                raise ProtocolOcrResultError("OCR page text is invalid.")
            other = len(page.other_text.encode("utf-8"))
            other_bytes += other
            if other > MAX_OCR_PAGE_TEXT_BYTES or other_bytes > MAX_OCR_DOCUMENT_TEXT_BYTES:
                raise ProtocolOcrResultError("OCR page text exceeds the size limit.")
        if page.text.strip():
            non_empty += 1
        confidence = page.confidence
        for value in (confidence, page.other_confidence):
            if value is not None and (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not 0 <= float(value) <= 1
            ):
                raise ProtocolOcrResultError("OCR page confidence is invalid.")
        for identity in (page.provider, page.provider_version,
                         page.other_provider, page.other_provider_version):
            if identity is not None and (
                not isinstance(identity, str)
                or _SAFE_PROVIDER.fullmatch(identity) is None
            ):
                raise ProtocolOcrResultError("OCR page provider identity is invalid.")
        readings = page.value_readings
        if readings is not None and (
            not isinstance(readings, tuple)
            or len(readings) > MAX_OCR_VALUE_BOXES
            or any(not _valid_reading(reading) for reading in readings)
        ):
            raise ProtocolOcrResultError("OCR page value readings are invalid.")
        if not isinstance(page.numeric_review_required, bool):
            raise ProtocolOcrResultError("OCR page review mark is invalid.")
        if page.render_dpi is not None and (
            not isinstance(page.render_dpi, int)
            or isinstance(page.render_dpi, bool)
            or not 36 <= page.render_dpi <= 1200
        ):
            raise ProtocolOcrResultError("OCR page resolution is invalid.")
        if len(page.value_boxes) > MAX_OCR_VALUE_BOXES or any(
            not isinstance(box, OcrValueBox)
            or not isinstance(box.text, str)
            or not 0 < len(box.text) <= 64
            or not all(
                isinstance(edge, (int, float)) and not isinstance(edge, bool)
                and math.isfinite(edge) and edge >= 0
                for edge in (box.x0, box.y0, box.x1, box.y1)
            )
            or box.x1 < box.x0
            or box.y1 < box.y0
            for box in page.value_boxes
        ):
            raise ProtocolOcrResultError("OCR page value boxes are invalid.")
        canonical_pages.append(
            OcrPage(
                source_page_number=page.source_page_number,
                text=page.text,
                confidence=float(confidence) if confidence is not None else None,
                provider=page.provider,
                provider_version=page.provider_version,
                numeric_review_required=page.numeric_review_required,
                value_boxes=tuple(page.value_boxes),
                render_dpi=page.render_dpi,
                other_provider=page.other_provider,
                other_provider_version=page.other_provider_version,
                other_text=page.other_text,
                value_readings=readings,
                other_confidence=(
                    float(page.other_confidence) if page.other_confidence is not None else None),
            )
        )
    if non_empty == 0:
        raise ProtocolOcrResultError("OCR returned no extractable text.")
    languages = tuple(dict.fromkeys(result.languages))
    if any(
        not isinstance(language, str)
        or not re.fullmatch(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{2,8})?", language)
        for language in languages
    ):
        raise ProtocolOcrResultError("OCR language metadata is invalid.")
    warnings = tuple(dict.fromkeys(result.warnings))
    if any(
        not isinstance(warning, str)
        or not warning.strip()
        or len(warning) > 500
        for warning in warnings
    ):
        raise ProtocolOcrResultError("OCR warning metadata is invalid.")
    return OcrResult(
        source_sha256=result.source_sha256,
        provider=result.provider,
        provider_version=result.provider_version,
        pages=tuple(canonical_pages),
        languages=languages,
        warnings=warnings,
    )


def ocr_result_payload(result: OcrResult) -> dict[str, object]:
    page_payloads = [
        {
            "source_page_number": page.source_page_number,
            "text": page.text,
            "confidence": page.confidence,
            "text_sha256": hashlib.sha256(page.text.encode("utf-8")).hexdigest(),
            "provider": page.provider,
            "provider_version": page.provider_version,
            "numeric_review_required": page.numeric_review_required,
            # Lane AN, FM: the values with their boxes on the page rendered
            # at render_dpi. Absent from OCR results stored before.
            "render_dpi": page.render_dpi,
            "value_boxes": [
                {"text": box.text, "box": [round(box.x0, 1), round(box.y0, 1),
                                           round(box.x1, 1), round(box.y1, 1)]}
                for box in page.value_boxes
            ],
            # Lane AN3, decision A: the other engine and, number by number,
            # what it read in the same place. Absent from results stored
            # before; None where the numbers were not compared by place.
            "other_provider": page.other_provider,
            "other_provider_version": page.other_provider_version,
            "other_text": page.other_text,
            "other_confidence": page.other_confidence,
            "value_readings": (
                None if page.value_readings is None else [
                    {"token": reading.token, "text": reading.text,
                     "box": [round(reading.x0, 1), round(reading.y0, 1),
                             round(reading.x1, 1), round(reading.y1, 1)],
                     "other_token": reading.other_token, "other_text": reading.other_text}
                    for reading in page.value_readings
                ]
            ),
        }
        for page in result.pages
    ]
    return {
        "source_sha256": result.source_sha256,
        "provider": result.provider,
        "provider_version": result.provider_version,
        "languages": list(result.languages),
        "warnings": list(result.warnings),
        "pages": page_payloads,
        "page_count": len(page_payloads),
        "review_state": "review_required",
        "executable": False,
    }
