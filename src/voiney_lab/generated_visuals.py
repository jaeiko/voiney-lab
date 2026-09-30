"""Feature-gated, evidence-bound instructional image generation."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import struct
import zlib
from dataclasses import dataclass
from typing import Any, Awaitable, Callable


_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off", ""})
_ASSET_ID = re.compile(r"^[0-9a-f]{64}$")


def _enabled(name: str) -> bool:
    raw = os.environ.get(name, "").strip().casefold()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    raise ValueError(f"{name} must be a boolean")


@dataclass(frozen=True)
class GeneratedVisualSettings:
    enabled: bool
    model: str = "grok-imagine-image-2.0"
    max_bytes: int = 5_000_000
    timeout_seconds: float = 60.0

    @classmethod
    def from_environment(cls) -> "GeneratedVisualSettings":
        enabled = _enabled("VOICE_WORKFLOW_AGENT_GENERATED_VISUALS_ENABLED")
        if not enabled:
            return cls(False)
        model = os.environ.get(
            "VOICE_WORKFLOW_AGENT_GENERATED_VISUAL_MODEL",
            "grok-imagine-image-2.0",
        ).strip()
        if not model or len(model) > 128:
            raise ValueError("generated visual model is invalid")
        timeout_raw = os.environ.get(
            "VOICE_WORKFLOW_AGENT_GENERATED_VISUAL_TIMEOUT_SECONDS", "60"
        ).strip()
        try:
            timeout_seconds = float(timeout_raw)
        except ValueError as exc:
            raise ValueError("generated visual timeout is invalid") from exc
        if not 5 <= timeout_seconds <= 120:
            raise ValueError("generated visual timeout is invalid")
        return cls(True, model, 5_000_000, timeout_seconds)


@dataclass(frozen=True)
class VisualSpecification:
    document_sha256: str
    protocol_id: str
    revision_id: str
    step_id: str
    step_label: str
    source_page: int
    source_evidence_ids: tuple[str, ...]
    action_summary: str
    verified_materials: tuple[str, ...]
    verified_tools: tuple[str, ...]
    verified_relations: tuple[str, ...]
    forbidden_inferences: tuple[str, ...]
    style: str = "restrained instructional line art"
    aspect_ratio: str = "4:3"
    spec_version: int = 1

    def canonical_dict(self) -> dict[str, Any]:
        return {
            name: getattr(self, name)
            for name in (
                "document_sha256", "protocol_id", "revision_id", "step_id",
                "step_label", "source_page", "source_evidence_ids",
                "action_summary", "verified_materials", "verified_tools",
                "verified_relations", "forbidden_inferences", "style",
                "aspect_ratio", "spec_version",
            )
        }

    def cache_key(self, model: str) -> str:
        raw = json.dumps(
            {"model": model, "spec": self.canonical_dict()},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def prompt(self) -> str:
        facts = {
            "action": self.action_summary,
            "materials": self.verified_materials,
            "tools": self.verified_tools,
            "relations": self.verified_relations,
        }
        return (
            "Create a rough instructional laboratory illustration from ONLY the "
            "verified facts in the JSON data below. Use clean restrained line art, "
            "a neutral background, and one clear action or a small sequence. Use no "
            "logos, decorative elements, embedded prose, invented labels, unsupported "
            "colors, equipment, PPE, quantities, temperatures, durations, or results. "
            "Do not make a photorealistic claim that this is the actual experiment. "
            "When a physical relation is not explicit, omit it. Verified facts:\n" +
            json.dumps(facts, ensure_ascii=False, sort_keys=True)
        )


@dataclass(frozen=True)
class GeneratedVisualAsset:
    asset_id: str
    cache_key: str
    protocol_id: str
    revision_id: str
    step_id: str
    step_label: str
    source_document_hash: str
    source_page: int
    source_evidence_ids: tuple[str, ...]
    mime_type: str
    content_sha256: str
    byte_size: int
    width: int
    height: int
    content: bytes

    def public_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "kind": "generated_instructional",
            "protocol_id": self.protocol_id,
            "revision_id": self.revision_id,
            "step_id": self.step_id,
            "step_label": self.step_label,
            "source_document_id": self.source_document_hash,
            "source_page": self.source_page,
            "source_evidence_ids": list(self.source_evidence_ids),
            "mime_type": self.mime_type,
            "sha256": self.content_sha256,
            "width": self.width,
            "height": self.height,
            "url": f"/api/generated-visuals/{self.asset_id}",
            "label": "AI-generated instructional illustration · not an original source image",
            "caption_primary": f"{self.step_label}단계 설명용 생성 이미지",
            "caption_source": "Exact amounts, warnings, and source facts remain controlled by text.",
        }


def _validated_image(raw: bytes, maximum: int) -> tuple[str, int, int]:
    if not isinstance(raw, bytes) or not 0 < len(raw) <= maximum:
        raise ValueError("generated image byte size is invalid")
    if raw.startswith(b"\x89PNG\r\n\x1a\n") and len(raw) >= 24:
        offset=8
        idat=[]
        width=height=0
        ended=False
        while offset+12<=len(raw):
            length=struct.unpack(">I",raw[offset:offset+4])[0]
            kind=raw[offset+4:offset+8]
            end=offset+12+length
            if end>len(raw):
                raise ValueError("generated PNG chunk is truncated")
            content=raw[offset+8:offset+8+length]
            expected=struct.unpack(">I",raw[offset+8+length:end])[0]
            if zlib.crc32(kind+content)&0xFFFFFFFF!=expected:
                raise ValueError("generated PNG checksum is invalid")
            if kind==b"IHDR":
                if offset!=8 or length!=13:
                    raise ValueError("generated PNG header is invalid")
                width,height=struct.unpack(">II",content[:8])
            elif kind==b"IDAT":
                idat.append(content)
            elif kind==b"IEND":
                if length!=0 or end!=len(raw):
                    raise ValueError("generated PNG ending is invalid")
                ended=True
                break
            offset=end
        if not ended or not idat:
            raise ValueError("generated PNG data is incomplete")
        try:
            if not zlib.decompress(b"".join(idat)):
                raise ValueError("generated PNG pixel data is empty")
        except zlib.error as exc:
            raise ValueError("generated PNG pixel data is invalid") from exc
        mime = "image/png"
    elif raw.startswith(b"\xff\xd8\xff") and raw.endswith(b"\xff\xd9"):
        mime, width, height = "image/jpeg", 0, 0
        offset = 2
        while offset + 9 < len(raw):
            if raw[offset] != 0xFF:
                offset += 1
                continue
            marker = raw[offset + 1]
            if marker in range(0xC0, 0xC4):
                height, width = struct.unpack(">HH", raw[offset + 5:offset + 9])
                break
            if offset + 4 > len(raw):
                break
            length = struct.unpack(">H", raw[offset + 2:offset + 4])[0]
            if length < 2:
                break
            offset += length + 2
    elif raw.startswith(b"RIFF") and len(raw) >= 30 and raw[8:12] == b"WEBP":
        mime = "image/webp"
        if raw[12:16] == b"VP8 " and len(raw) >= 30:
            width = (struct.unpack("<H", raw[26:28])[0] & 0x3FFF)
            height = (struct.unpack("<H", raw[28:30])[0] & 0x3FFF)
        elif raw[12:16] == b"VP8L" and len(raw) >= 25:
            b0, b1, b2, b3 = raw[21:25]
            width = 1 + (((b1 & 0x3F) << 8) | b0)
            height = 1 + (((b3 & 0xF) << 10) | (b2 << 2) | ((b1 & 0xC0) >> 6))
        elif raw[12:16] == b"VP8X" and len(raw) >= 30:
            width = 1 + struct.unpack("<I", raw[24:27] + b"\x00")[0]
            height = 1 + struct.unpack("<I", raw[27:30] + b"\x00")[0]
        else:
            width, height = 512, 512
    else:
        raise ValueError("generated image format is invalid")
    if not 64 <= width <= 8192 or not 64 <= height <= 8192:
        raise ValueError("generated image dimensions are invalid")
    return mime, width, height


class XaiImageGenerator:
    """Call the official OpenAI-compatible xAI image endpoint."""

    def __init__(self, client: Any, settings: GeneratedVisualSettings) -> None:
        if not settings.enabled:
            raise ValueError("generated visuals are disabled")
        self.client = client
        self.settings = settings

    async def generate(self, specification: VisualSpecification) -> bytes:
        extra_body: dict[str, Any] = {
            "aspect_ratio": specification.aspect_ratio or "4:3",
        }
        if "2.0" in self.settings.model:
            extra_body["resolution"] = "1k"
            extra_body["quality"] = "low"
        response = await asyncio.wait_for(
            self.client.images.generate(
                model=self.settings.model,
                prompt=specification.prompt(),
                response_format="b64_json",
                extra_body=extra_body,
            ),
            timeout=self.settings.timeout_seconds,
        )
        data = getattr(response, "data", None)
        encoded = getattr(data[0], "b64_json", None) if data else None
        if not isinstance(encoded, str) or not encoded:
            raise ValueError("image provider returned no base64 image")
        return base64.b64decode(encoded, validate=True)


class GeneratedVisualRegistry:
    """Process-local cache and one-in-flight owner per evidence-bound key."""

    def __init__(self) -> None:
        self._assets: dict[str, GeneratedVisualAsset] = {}
        self._cache: dict[str, str] = {}
        self._inflight: dict[str, asyncio.Task[tuple[GeneratedVisualAsset, bool]]] = {}
        self._lock = asyncio.Lock()

    def get(self, asset_id: str) -> GeneratedVisualAsset | None:
        return self._assets.get(asset_id) if _ASSET_ID.fullmatch(asset_id) else None

    async def obtain(
        self,
        specification: VisualSpecification,
        settings: GeneratedVisualSettings,
        generate: Callable[[VisualSpecification], Awaitable[bytes]],
    ) -> tuple[GeneratedVisualAsset, bool]:
        key = specification.cache_key(settings.model)
        async with self._lock:
            cached_id = self._cache.get(key)
            if cached_id is not None:
                return self._assets[cached_id], True
            existing = self._inflight.get(key)
            if existing is None:
                existing = asyncio.create_task(
                    self._create(specification, settings, generate, key)
                )
                self._inflight[key] = existing
        try:
            return await asyncio.shield(existing)
        finally:
            if existing.done():
                async with self._lock:
                    if self._inflight.get(key) is existing:
                        self._inflight.pop(key, None)

    async def _create(
        self,
        specification: VisualSpecification,
        settings: GeneratedVisualSettings,
        generate: Callable[[VisualSpecification], Awaitable[bytes]],
        key: str,
    ) -> tuple[GeneratedVisualAsset, bool]:
        raw = await generate(specification)
        mime, width, height = _validated_image(raw, settings.max_bytes)
        content_hash = hashlib.sha256(raw).hexdigest()
        asset = GeneratedVisualAsset(
            asset_id=content_hash,
            cache_key=key,
            protocol_id=specification.protocol_id,
            revision_id=specification.revision_id,
            step_id=specification.step_id,
            step_label=specification.step_label,
            source_document_hash=specification.document_sha256,
            source_page=specification.source_page,
            source_evidence_ids=specification.source_evidence_ids,
            mime_type=mime,
            content_sha256=content_hash,
            byte_size=len(raw),
            width=width,
            height=height,
            content=raw,
        )
        async with self._lock:
            self._assets[asset.asset_id] = asset
            self._cache[key] = asset.asset_id
        return asset, False


GENERATED_VISUALS = GeneratedVisualRegistry()
