"""Validated models for normalized safety-document manifests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from collections.abc import Iterable, Mapping
from typing import Any


DOCUMENT_TYPES = {"facility_sop", "supplier_sds", "equipment_manual", "regulatory_reference"}
SOURCE_AUTHORITIES = {"facility", "supplier", "manufacturer", "regulatory_agency", "test_fixture"}
# Local Voice Workflow Agent catalog review state; it does not imply facility approval by a supplier.
APPROVAL_STATUSES = {"draft", "approved", "superseded", "rejected"}
USAGE_SCOPES = {"operational", "demo", "reference_only", "test_only"}
TRANSLATION_STATUSES = {"original", "human_reviewed", "machine_unreviewed", "unavailable"}
REQUIRED_DOCUMENT_FIELDS = {
    "document_id", "document_family_id", "canonical_source_id", "canonical_version",
    "document_type", "title", "issuer", "version",
    "language", "source_authority", "approval_status", "usage_scope", "source_checksum",
    "translation_status", "active", "sections",
}


class ManifestValidationError(ValueError):
    """The normalized manifest is invalid and must not be ingested."""


def _text(value: Any, field: str, *, required: bool = False) -> str | None:
    if value is None:
        if required:
            raise ManifestValidationError(f"{field} is required")
        return None
    if not isinstance(value, str) or (required and not value.strip()):
        raise ManifestValidationError(f"{field} must be a non-empty string")
    return value.strip() or None


def _date(value: Any, field: str) -> str | None:
    value = _text(value, field)
    if value is None:
        return None
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ManifestValidationError(f"{field} must be ISO-8601") from exc
    return value


def _choice(value: Any, field: str, choices: set[str]) -> str:
    value = _text(value, field, required=True)
    if value not in choices:
        raise ManifestValidationError(f"{field} has unsupported value {value!r}")
    return value


@dataclass(frozen=True)
class ValidatedManifest:
    documents: tuple[dict[str, Any], ...]


def validate_manifest(
    payload: Any,
    existing_documents: Iterable[Mapping[str, Any]] = (),
) -> ValidatedManifest:
    """Validate and normalize an entire manifest before any database write."""
    if not isinstance(payload, dict) or not isinstance(payload.get("documents"), list):
        raise ManifestValidationError("manifest must contain a documents list")
    if not payload["documents"]:
        raise ManifestValidationError("manifest documents must not be empty")
    normalized: list[dict[str, Any]] = []
    identities: set[tuple[str, str, str]] = set()
    for index, raw in enumerate(payload["documents"]):
        prefix = f"documents[{index}]"
        if not isinstance(raw, dict):
            raise ManifestValidationError(f"{prefix} must be an object")
        missing = sorted(REQUIRED_DOCUMENT_FIELDS - raw.keys())
        if missing:
            raise ManifestValidationError(f"{prefix} missing required fields: {', '.join(missing)}")
        doc = {name: _text(raw.get(name), f"{prefix}.{name}") for name in (
            "manufacturer", "product_name", "product_code", "facility_id", "source_path", "source_uri"
        )}
        for name in ("document_id", "document_family_id", "canonical_source_id", "canonical_version",
                     "title", "issuer", "version", "language", "source_checksum"):
            doc[name] = _text(raw.get(name), f"{prefix}.{name}", required=True)
        doc["translation_of_document_id"] = _text(
            raw.get("translation_of_document_id"), f"{prefix}.translation_of_document_id"
        )
        doc["document_type"] = _choice(raw.get("document_type"), f"{prefix}.document_type", DOCUMENT_TYPES)
        doc["source_authority"] = _choice(raw.get("source_authority"), f"{prefix}.source_authority", SOURCE_AUTHORITIES)
        doc["approval_status"] = _choice(raw.get("approval_status"), f"{prefix}.approval_status", APPROVAL_STATUSES)
        doc["usage_scope"] = _choice(raw.get("usage_scope"), f"{prefix}.usage_scope", USAGE_SCOPES)
        doc["translation_status"] = _choice(raw.get("translation_status"), f"{prefix}.translation_status", TRANSLATION_STATUSES)
        if doc["translation_status"] != "original" and not doc["translation_of_document_id"]:
            raise ManifestValidationError(f"{prefix}.translation_of_document_id is required for translations")
        if doc["translation_status"] == "original" and doc["translation_of_document_id"]:
            raise ManifestValidationError(f"{prefix}.translation_of_document_id is only valid for translations")
        if (doc["source_authority"] == "test_fixture") != (doc["usage_scope"] == "test_only"):
            raise ManifestValidationError(f"{prefix} test_fixture authority and test_only scope must be used together")
        if not doc["source_path"] and not doc["source_uri"]:
            raise ManifestValidationError(f"{prefix} requires source_path or source_uri")
        if not isinstance(raw.get("active"), bool):
            raise ManifestValidationError(f"{prefix}.active must be a boolean")
        doc["active"] = raw["active"]
        doc["effective_at"] = _date(raw.get("effective_at"), f"{prefix}.effective_at")
        doc["review_due_at"] = _date(raw.get("review_due_at"), f"{prefix}.review_due_at")
        cas = raw.get("cas_numbers", [])
        if not isinstance(cas, list) or any(not isinstance(x, str) or not x.strip() for x in cas):
            raise ManifestValidationError(f"{prefix}.cas_numbers must be a list of non-empty strings")
        doc["cas_numbers"] = sorted(set(x.strip() for x in cas))
        identity = (doc["document_id"], doc["version"], doc["language"].casefold())
        if identity in identities:
            raise ManifestValidationError(
                f"duplicate document/version/language identity: {identity[0]} {identity[1]} {identity[2]}"
            )
        identities.add(identity)

        sections = raw["sections"]
        if not isinstance(sections, list) or not sections:
            raise ManifestValidationError(f"{prefix}.sections must be a non-empty list")
        doc["sections"] = []
        section_codes: set[str] = set()
        for s_index, section in enumerate(sections):
            sp = f"{prefix}.sections[{s_index}]"
            if not isinstance(section, dict):
                raise ManifestValidationError(f"{sp} must be an object")
            code = _text(section.get("section_code"), f"{sp}.section_code", required=True)
            content = _text(section.get("content"), f"{sp}.content", required=True)
            if code in section_codes:
                raise ManifestValidationError(f"{prefix} has duplicate section_code {code!r}")
            section_codes.add(code)
            start, end = section.get("page_start"), section.get("page_end")
            if not isinstance(start, int) or isinstance(start, bool) or start <= 0:
                raise ManifestValidationError(f"{sp}.page_start must be a positive integer")
            if not isinstance(end, int) or isinstance(end, bool) or end < start:
                raise ManifestValidationError(f"{sp}.page_end must be an integer >= page_start")
            keywords = section.get("keywords", [])
            if not isinstance(keywords, list) or any(not isinstance(x, str) or not x.strip() for x in keywords):
                raise ManifestValidationError(f"{sp}.keywords must be a list of non-empty strings")
            doc["sections"].append({
                "section_code": code, "section_title": _text(section.get("section_title"), f"{sp}.section_title", required=True),
                "page_start": start, "page_end": end, "content": content,
                "topic": _text(section.get("topic"), f"{sp}.topic"),
                "keywords": sorted(set(x.strip() for x in keywords)),
            })
        aliases = raw.get("aliases", [])
        if not isinstance(aliases, list):
            raise ManifestValidationError(f"{prefix}.aliases must be a list")
        doc["aliases"] = []
        for a_index, alias in enumerate(aliases):
            ap = f"{prefix}.aliases[{a_index}]"
            if not isinstance(alias, dict):
                raise ManifestValidationError(f"{ap} must be an object")
            doc["aliases"].append({
                "alias": _text(alias.get("alias"), f"{ap}.alias", required=True),
                "language": _text(alias.get("language"), f"{ap}.language", required=True),
                "approved": alias.get("approved"),
                "generic": alias.get("generic", False),
            })
            if not isinstance(doc["aliases"][-1]["approved"], bool) or not isinstance(doc["aliases"][-1]["generic"], bool):
                raise ManifestValidationError(f"{ap}.approved and generic must be booleans")
        normalized.append(doc)
    available = list(existing_documents) + normalized
    for index, doc in enumerate(normalized):
        if doc["translation_status"] == "original":
            continue
        prefix = f"documents[{index}]"
        referenced = [candidate for candidate in available
                      if candidate["document_id"] == doc["translation_of_document_id"]]
        if not referenced:
            raise ManifestValidationError(
                f"{prefix}.translation_of_document_id does not reference an existing document"
            )
        originals = [candidate for candidate in referenced
                     if candidate["translation_status"] == "original"]
        if not originals:
            raise ManifestValidationError(
                f"{prefix}.translation_of_document_id must reference an original document"
            )
        expected = (doc["document_family_id"], doc["canonical_source_id"], doc["canonical_version"])
        identities = {(candidate["document_family_id"], candidate["canonical_source_id"],
                       candidate["canonical_version"]) for candidate in originals}
        if identities != {expected}:
            raise ManifestValidationError(
                f"{prefix} translation canonical identity does not match its original document"
            )
    return ValidatedManifest(tuple(normalized))
