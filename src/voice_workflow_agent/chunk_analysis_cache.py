"""Keep a validated chunk analysis across invocations, without keeping trust.

Merging an analysed Protocol needs every chunk at once, and a chunk costs one
provider call. In-gel is three chunks; the remaining authorized budget has been
two. With no memory between invocations that document could not be closed at
any budget below three, however many calls had already been paid for -- the
arithmetic, not the model, was the obstacle.

So a chunk that passed validation is written down and can be read back on a
later run. What is *not* carried across is trust. A cached entry is not an
analysis; it is a claim payload that once satisfied the rules, and it is put
back through the current rules -- the same
``parse_chunk_claim_response`` a live response goes through, against evidence
segments recomputed from the source now -- before anything uses it. An entry
that no longer validates is deleted and reported as a miss. The cache can
therefore make a call unnecessary; it can never make a claim admissible.

What is stored is the claim payload re-emitted canonically from the parsed
JSON: the schema's own fields, sorted, without the completion's whitespace,
key order, or anything else the provider wrapped around it. The raw completion
is not written to disk. Evidence segment ids are identities the server
computes from its own source bytes, so they are stored as they are.

The key names every input that could change what a valid answer looks like.
Miss any of them and a stale entry would be served as a fresh one:

* the source PDF's SHA-256 -- different bytes, different document
* the chunk's identity and exact page refs -- different question
* ``CLAIM_SCHEMA_VERSION`` -- different response contract
* the system prompt's hash -- different instructions
* ``EVIDENCE_SEGMENT_VERSION`` -- different segment boundaries, so different
  evidence handles, so every cited id in a stored payload becomes meaningless

Entries live under a development cache directory. Nothing here touches the
protocol store or any runtime database.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .experiment_protocol_pdf import ProtocolPdfExtraction
from .protocol_claim_analysis import (
    CLAIM_ANALYSIS_SYSTEM_PROMPT,
    CLAIM_SCHEMA_VERSION,
    EVIDENCE_SEGMENT_VERSION,
    ProviderClaimRequest,
    parse_chunk_claim_response,
)

CACHE_FORMAT_VERSION = 1
CACHE_DIRECTORY_ENV = "VOICE_WORKFLOW_AGENT_CHUNK_CACHE_DIR"
DEFAULT_CACHE_DIRECTORY = Path("data/development_cache/chunk_analysis")
#: A stored payload is bounded for the same reason a live response is: an
#: unbounded read is a denial of service against the process that trusts it.
MAX_CACHED_PAYLOAD_BYTES = 4 * 1024 * 1024


class ChunkCacheError(RuntimeError):
    """A cache entry could not be written or read as this module writes them."""


def prompt_sha256() -> str:
    """Identify the instructions the cached answer was produced under."""

    return hashlib.sha256(
        CLAIM_ANALYSIS_SYSTEM_PROMPT.encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class ChunkCacheKey:
    """Everything that decides whether a stored answer is still the answer."""

    source_sha256: str
    chunk_id: str
    ordinal: int
    core_page_refs: tuple[int, ...]
    context_page_refs: tuple[int, ...]
    source_revision: str
    claim_schema_version: int = CLAIM_SCHEMA_VERSION
    evidence_segment_version: int = EVIDENCE_SEGMENT_VERSION
    prompt_sha256: str = ""
    capability_policy_id: str = ""

    def identity(self) -> dict[str, Any]:
        return {
            "cache_format_version": CACHE_FORMAT_VERSION,
            "source_sha256": self.source_sha256,
            "chunk_id": self.chunk_id,
            "ordinal": self.ordinal,
            "core_page_refs": list(self.core_page_refs),
            "context_page_refs": list(self.context_page_refs),
            "source_revision": self.source_revision,
            "claim_schema_version": self.claim_schema_version,
            "evidence_segment_version": self.evidence_segment_version,
            "prompt_sha256": self.prompt_sha256 or prompt_sha256(),
            "capability_policy_id": self.capability_policy_id,
        }

    def digest(self) -> str:
        return hashlib.sha256(
            json.dumps(
                self.identity(), sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()


def key_for_chunk(
    extraction: ProtocolPdfExtraction,
    chunk: Any,
    *,
    capability_policy_id: str = "",
) -> ChunkCacheKey:
    """Name one chunk of one document under the current contract."""

    return ChunkCacheKey(
        source_sha256=extraction.sha256,
        chunk_id=chunk.chunk_id,
        ordinal=chunk.ordinal,
        core_page_refs=tuple(chunk.core_page_refs),
        context_page_refs=tuple(chunk.overlap_page_refs),
        source_revision=chunk.candidate_revision_id,
        prompt_sha256=prompt_sha256(),
        capability_policy_id=capability_policy_id,
    )


def canonical_claim_payload(raw_response: str) -> str:
    """The claim structure alone, in one deterministic form.

    Whatever the provider's formatting was, this is the object the schema
    describes, with its keys sorted and its whitespace gone. A completion that
    is not a JSON object is not a claim payload and is refused here rather than
    stored and refused later.
    """

    payload = json.loads(raw_response)
    if not isinstance(payload, dict):
        raise ChunkCacheError("A claim payload must be a JSON object.")
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class ChunkCacheHit:
    analysis: Any
    key_digest: str
    path: Path


class ChunkAnalysisCache:
    """Validated chunk payloads on disk, revalidated on every read."""

    def __init__(self, root: Path | None = None) -> None:
        if root is None:
            configured = os.environ.get(CACHE_DIRECTORY_ENV, "").strip()
            root = Path(configured) if configured else DEFAULT_CACHE_DIRECTORY
        self.root = Path(root)

    def path_for(self, key: ChunkCacheKey) -> Path:
        digest = key.digest()
        return self.root / digest[:2] / f"{digest}.json"

    def store(self, key: ChunkCacheKey, raw_response: str) -> Path:
        """Write one validated chunk's claim payload.

        The caller has already put this payload through
        ``parse_chunk_claim_response``; storing an unvalidated one would put a
        refusal in the cache and hand it back as a hit.
        """

        payload = canonical_claim_payload(raw_response)
        if len(payload.encode("utf-8")) > MAX_CACHED_PAYLOAD_BYTES:
            raise ChunkCacheError("Claim payload exceeds the cached size limit.")
        entry = {
            "identity": key.identity(),
            "claim_payload": payload,
        }
        path = self.path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.partial")
        temporary.write_text(
            json.dumps(entry, sort_keys=True, indent=2), encoding="utf-8"
        )
        temporary.replace(path)
        return path

    def load(
        self,
        key: ChunkCacheKey,
        *,
        extraction: ProtocolPdfExtraction,
        request: ProviderClaimRequest,
        capability_policy_id: str | None = None,
    ) -> ChunkCacheHit | None:
        """Return a re-validated analysis, or None for a miss.

        Every failure is a miss: a missing file, a file this module did not
        write, an identity that does not match the key it was filed under, or a
        payload the current rules refuse. The last case is the point of the
        exercise -- an entry stored under rules that have since changed must
        cost a call, not be waved through.
        """

        path = self.path_for(key)
        try:
            body = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None
        if len(body.encode("utf-8")) > MAX_CACHED_PAYLOAD_BYTES * 2:
            self._discard(path)
            return None
        try:
            entry = json.loads(body)
        except json.JSONDecodeError:
            self._discard(path)
            return None
        if (
            not isinstance(entry, dict)
            or set(entry) != {"identity", "claim_payload"}
            or entry.get("identity") != key.identity()
            or not isinstance(entry.get("claim_payload"), str)
        ):
            self._discard(path)
            return None
        arguments = {
            "extraction": extraction,
            "source_revision": key.source_revision,
            "chunk_id": key.chunk_id,
            "core_page_refs": tuple(key.core_page_refs),
            "request": request,
        }
        if capability_policy_id:
            arguments["capability_policy_id"] = capability_policy_id
        try:
            analysis = parse_chunk_claim_response(
                entry["claim_payload"], **arguments
            )
        except Exception:  # noqa: BLE001 - any refusal is a miss, never a pass
            self._discard(path)
            return None
        return ChunkCacheHit(
            analysis=analysis, key_digest=key.digest(), path=path
        )

    def _discard(self, path: Path) -> None:
        try:
            path.unlink()
        except OSError:
            pass

    def entries(self) -> tuple[Path, ...]:
        if not self.root.is_dir():
            return ()
        return tuple(sorted(self.root.rglob("*.json")))
