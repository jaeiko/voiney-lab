"""Bounded OCR provider contract for source-preserving protocol onboarding.

OCR is text extraction evidence, not protocol approval. Providers receive one
verified immutable PDF path and must return page-numbered text for the same
SHA-256 source. This module validates the untrusted result before the catalog
may persist it for human review.
"""

from __future__ import annotations

import hashlib
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
class OcrPage:
    source_page_number: int
    text: str
    confidence: float | None = None


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
        if page.text.strip():
            non_empty += 1
        confidence = page.confidence
        if confidence is not None and (
            not isinstance(confidence, (int, float))
            or isinstance(confidence, bool)
            or not 0 <= float(confidence) <= 1
        ):
            raise ProtocolOcrResultError("OCR page confidence is invalid.")
        canonical_pages.append(
            OcrPage(
                source_page_number=page.source_page_number,
                text=page.text,
                confidence=float(confidence) if confidence is not None else None,
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
