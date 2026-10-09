"""A diagram drawn from the step's own words, on request only (lane WV, decision 3).

"그림으로 그려 줘": when the source has no figure for the step, a simple
schematic is drawn from the step's text -- the vessel, what goes in, the
action -- and shown as "AI 가 그린 그림 — 실제와 다를 수 있음", never as
the source.

Decision of 2026-10-09 (measured, see the lane report): the drawing is an
SVG written by a text model (the OpenAI Responses API, the same key as the
web explanation), not a picture from an image model. An SVG is text the
server can check and change -- every number in it is compared with the
step's own values and taken out when it is not one of them, every element
and attribute is on an allow-list, there is no script, no link, no style,
no external reference -- and it costs about a thousandth of an image and
comes back in seconds. A raster picture can be neither checked for the
values it paints nor corrected, and an image model paints labels and
quantities of its own.

The SVG the model wrote is never served as it came: ``sanitize_svg``
rebuilds it from the allow-list, and the label is written into the picture
itself, so a saved copy still says what it is.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import threading
import time
import xml.etree.ElementTree as ElementTree
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from voiney_lab.answer_checks import permissive_hazard_topics, safety_instruction_topics

log = logging.getLogger("voiney_lab.drawn_diagrams")

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off", ""})

DRAWN_BACKEND = "openai_responses_svg"
#: The label written into every drawing and shown beside it.
DRAWN_LABEL = "AI 가 그린 그림 — 실제와 다를 수 있음"
SVG_MAX_BYTES = 64 * 1024
CANVAS_WIDTH = 640
CANVAS_HEIGHT = 400
REGISTRY_SIZE = 48

_SVG_NS = "http://www.w3.org/2000/svg"
_ALLOWED_ELEMENTS = frozenset({
    "svg", "g", "rect", "circle", "ellipse", "line", "polyline", "polygon", "path",
    "text", "tspan", "title", "desc", "defs", "marker",
})
_ALLOWED_ATTRIBUTES = frozenset({
    "x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "r", "rx", "ry", "width", "height",
    "d", "points", "fill", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin",
    "stroke-dasharray", "opacity", "fill-opacity", "stroke-opacity", "font-size",
    "font-family", "font-weight", "text-anchor", "dominant-baseline", "transform",
    "viewBox", "id", "marker-end", "marker-start", "refX", "refY", "markerWidth",
    "markerHeight", "orient", "dx", "dy", "preserveAspectRatio",
})
#: Wrappers whose children are kept while the wrapper itself goes (a link).
_UNWRAPPED_ELEMENTS = frozenset({"a", "switch"})
#: An attribute value may not reach outside the picture; "url(#id)" (a
#: marker in the picture) is the one url allowed.
_UNSAFE_VALUE = re.compile(r"url\s*\((?!\s*#)|javascript:|data:|&#|<|>", re.IGNORECASE)
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_SVG_BLOCK = re.compile(r"<svg\b.*?</svg>", re.IGNORECASE | re.DOTALL)
_ASSET_ID = re.compile(r"^[0-9a-f]{64}$")


def _enabled(name: str, default: str) -> bool:
    value = os.environ.get(name, default).strip().casefold()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ValueError(f"{name} must be a boolean")


@dataclass(frozen=True)
class DrawnDiagramSettings:
    enabled: bool
    model: str = "gpt-6-luna"
    timeout_seconds: float = 20.0

    @classmethod
    def from_environment(cls) -> "DrawnDiagramSettings":
        if not _enabled("VOINEY_LAB_DRAWN_DIAGRAMS_ENABLED", "false"):
            return cls(False)
        if not os.environ.get("OPENAI_API_KEY", "").strip():
            raise ValueError("VOINEY_LAB_DRAWN_DIAGRAMS_ENABLED needs OPENAI_API_KEY")
        model = os.environ.get("VOINEY_LAB_DRAWN_DIAGRAM_MODEL", "gpt-6-luna").strip()
        if not model:
            raise ValueError("VOINEY_LAB_DRAWN_DIAGRAM_MODEL is invalid")
        try:
            timeout = float(os.environ.get("VOINEY_LAB_DRAWN_DIAGRAM_TIMEOUT_SECONDS", "20").strip())
        except ValueError as exc:
            raise ValueError("VOINEY_LAB_DRAWN_DIAGRAM_TIMEOUT_SECONDS is invalid") from exc
        if not 3 <= timeout <= 60:
            raise ValueError("VOINEY_LAB_DRAWN_DIAGRAM_TIMEOUT_SECONDS is invalid")
        return cls(True, model, timeout)

    def public_capability(self) -> dict[str, Any]:
        return {
            "status": "enabled" if self.enabled else "disabled",
            "authority": "drawn_diagram",
            "model": self.model if self.enabled else None,
            "backend": DRAWN_BACKEND,
            "label": DRAWN_LABEL,
        }


@dataclass(frozen=True)
class DrawnDiagram:
    asset_id: str
    content: bytes
    step_id: str
    step_label: str
    source_sha256: str
    removed_numbers: tuple[str, ...]
    removed_texts: tuple[str, ...]
    model: str

    def public_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "url": f"/api/drawn-visuals/{self.asset_id}",
            "mime_type": "image/svg+xml",
            "step_id": self.step_id,
            "step_label": self.step_label,
            "label": DRAWN_LABEL,
            "removed_numbers": list(self.removed_numbers),
            "removed_texts": len(self.removed_texts),
            "model": self.model,
            "width_px": CANVAS_WIDTH,
            "height_px": CANVAS_HEIGHT,
        }


@dataclass(frozen=True)
class DrawnDiagramResult:
    status: str
    diagram: DrawnDiagram | None = None
    elapsed_ms: int = 0
    model: str = ""
    usage: Mapping[str, int] = field(default_factory=dict)
    reason: str = ""


class DiagramRegistry:
    """The drawings of this run, served same-origin by asset id and reused per step."""

    def __init__(self, size: int = REGISTRY_SIZE) -> None:
        self._size = size
        self._items: dict[str, DrawnDiagram] = {}
        self._by_step: dict[tuple[str, str, str], str] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()

    def keep(self, diagram: DrawnDiagram) -> None:
        with self._lock:
            if diagram.asset_id not in self._items:
                self._order.append(diagram.asset_id)
            self._items[diagram.asset_id] = diagram
            self._by_step[(diagram.source_sha256, diagram.step_id, diagram.model)] = diagram.asset_id
            while len(self._order) > self._size:
                gone = self._order.pop(0)
                self._items.pop(gone, None)
                self._by_step = {key: value for key, value in self._by_step.items() if value != gone}

    def get(self, asset_id: str) -> DrawnDiagram | None:
        if not _ASSET_ID.match(asset_id or ""):
            return None
        with self._lock:
            return self._items.get(asset_id)

    def for_step(self, source_sha256: str, step_id: str, model: str) -> DrawnDiagram | None:
        with self._lock:
            asset_id = self._by_step.get((source_sha256, step_id, model))
            return self._items.get(asset_id) if asset_id else None

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._by_step.clear()
            self._order.clear()


DRAWN_DIAGRAMS = DiagramRegistry()


# --- the check ------------------------------------------------------------------


def source_numbers(*texts: str) -> frozenset[str]:
    """The numbers the step's own words carry, as written ("500", "0.5", "15")."""

    found: set[str] = set()
    for text in texts:
        for match in _NUMBER.findall(text or ""):
            found.add(match)
            found.add(match.replace(",", ""))
    return frozenset(found)


def sanitize_svg(raw: str, allowed_numbers: frozenset[str]) -> tuple[bytes, tuple[str, ...], tuple[str, ...]]:
    """The SVG rebuilt from the allow-list, the numbers taken out, the texts taken out.

    Raises ``ValueError`` when what came is not a drawing at all.
    """

    if not raw or len(raw.encode("utf-8")) > SVG_MAX_BYTES:
        raise ValueError("svg size")
    if re.search(r"<!DOCTYPE|<!ENTITY|<\?xml-stylesheet", raw, re.IGNORECASE):
        raise ValueError("svg declarations")
    match = _SVG_BLOCK.search(raw)
    if match is None:
        raise ValueError("no svg")
    text = match.group(0)
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as exc:
        raise ValueError("svg parse") from exc
    removed_numbers: list[str] = []
    removed_texts: list[str] = []

    def local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    def checked_text(value: str | None) -> str | None:
        if value is None or not value.strip():
            return value
        words = " ".join(value.split())
        if safety_instruction_topics(words) or permissive_hazard_topics(words):
            removed_texts.append(words)
            return ""

        def keep_or_drop(number: re.Match[str]) -> str:
            token = number.group(0)
            if token in allowed_numbers or token.replace(",", "") in allowed_numbers:
                return token
            removed_numbers.append(token)
            return ""

        return _NUMBER.sub(keep_or_drop, value)

    def rebuilt_children(node: ElementTree.Element) -> list[ElementTree.Element]:
        built: list[ElementTree.Element] = []
        for child in node:
            if local(child.tag) in _UNWRAPPED_ELEMENTS:
                built.extend(rebuilt_children(child))
                continue
            clean_child = rebuild(child)
            if clean_child is not None:
                built.append(clean_child)
        return built

    def rebuild(node: ElementTree.Element) -> ElementTree.Element | None:
        name = local(node.tag)
        if name not in _ALLOWED_ELEMENTS:
            return None
        clean = ElementTree.Element(name)
        for key, value in node.attrib.items():
            attribute = local(key)
            if attribute not in _ALLOWED_ATTRIBUTES or _UNSAFE_VALUE.search(value):
                continue
            clean.set(attribute, checked_text(value) if attribute == "id" else value)
        clean.text = checked_text(node.text)
        clean.tail = checked_text(node.tail)
        for built in rebuilt_children(node):
            clean.append(built)
        return clean

    if local(root.tag) != "svg":
        raise ValueError("not svg")
    clean_root = rebuild(root)
    if clean_root is None or len(clean_root) == 0:
        raise ValueError("empty svg")
    clean_root.set("xmlns", _SVG_NS)
    clean_root.set("width", str(CANVAS_WIDTH))
    clean_root.set("height", str(CANVAS_HEIGHT))
    if "viewBox" not in clean_root.attrib:
        clean_root.set("viewBox", f"0 0 {CANVAS_WIDTH} {CANVAS_HEIGHT}")
    # The label, in the picture itself.
    band = ElementTree.SubElement(clean_root, "rect")
    band.set("x", "0"); band.set("y", str(CANVAS_HEIGHT - 26)); band.set("width", str(CANVAS_WIDTH))
    band.set("height", "26"); band.set("fill", "#fff3cd"); band.set("opacity", "0.95")
    label = ElementTree.SubElement(clean_root, "text")
    label.set("x", "12"); label.set("y", str(CANVAS_HEIGHT - 9)); label.set("font-size", "13")
    label.set("font-family", "sans-serif"); label.set("fill", "#5c4b00")
    label.text = DRAWN_LABEL
    out = ElementTree.tostring(clean_root, encoding="unicode")
    # ElementTree writes the default namespace as ns0 when parsed in; keep it bare.
    out = re.sub(r"\sxmlns:ns\d+=\"[^\"]*\"", "", out).replace("ns0:", "")
    return out.encode("utf-8"), tuple(dict.fromkeys(removed_numbers)), tuple(removed_texts)


# --- the drawing ------------------------------------------------------------------

_DEVELOPER_PROMPT = (
    "You draw a simple schematic of one laboratory protocol step for a bench researcher, "
    "as an SVG {width} by {height} (viewBox 0 0 {width} {height}, white background). Show the "
    "vessel (a tube, a plate, a gel piece, an instrument), what goes in or comes out, and the "
    "action, with arrows and short labels in {language}. Use only basic shapes (rect, circle, "
    "ellipse, line, polyline, polygon, path, text, g), plain attributes (fill, stroke, "
    "stroke-width, font-size, text-anchor, transform), no style attribute, no script, no "
    "image, no link, no external reference. Write no number at all unless it is one of these, "
    "copied exactly: {numbers}. Write no safety instruction. Leave the bottom 30 pixels empty. "
    "Output only the SVG markup, nothing before or after it."
)


def _output_text(response: Any) -> str:
    text = str(getattr(response, "output_text", "") or "")
    if text:
        return text
    for item in getattr(response, "output", None) or ():
        if getattr(item, "type", None) == "message":
            for part in getattr(item, "content", None) or ():
                if getattr(part, "type", None) == "output_text":
                    text += str(getattr(part, "text", "") or "")
    return text


async def draw_step_diagram(
    client: Any,
    settings: DrawnDiagramSettings,
    *,
    step_id: str,
    step_label: str,
    step_text: str,
    source_sha256: str,
    extra_texts: tuple[str, ...] = (),
    language: str = "ko",
    registry: DiagramRegistry = DRAWN_DIAGRAMS,
) -> DrawnDiagramResult:
    """One drawing of the step, checked; the same step is drawn once per run."""

    kept = registry.for_step(source_sha256, step_id, settings.model)
    if kept is not None:
        return DrawnDiagramResult("success", kept, model=settings.model, reason="cached")
    started = time.perf_counter()
    numbers = source_numbers(step_text, *extra_texts)
    language_name = {"ko": "Korean", "en": "English", "vi": "Vietnamese"}.get(language, "Korean")
    try:
        response = await asyncio.wait_for(
            client.responses.create(
                model=settings.model,
                input=[
                    {"role": "developer", "content": _DEVELOPER_PROMPT.format(
                        width=CANVAS_WIDTH, height=CANVAS_HEIGHT, language=language_name,
                        numbers=", ".join(sorted(numbers)) or "none",
                    )},
                    {"role": "user", "content": f"Step {step_label}: {step_text.strip()}"},
                ],
                store=False,
                max_output_tokens=3000,
            ),
            timeout=settings.timeout_seconds,
        )
    except asyncio.TimeoutError:
        return DrawnDiagramResult("timeout", model=settings.model,
                                  elapsed_ms=round((time.perf_counter() - started) * 1000))
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - reported, never raised into the turn
        log.info("drawing failed category=%s", type(exc).__name__)
        return DrawnDiagramResult("provider_error", model=settings.model,
                                  elapsed_ms=round((time.perf_counter() - started) * 1000))
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    usage = getattr(response, "usage", None)
    counts = {
        "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
        "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
    }
    try:
        content, removed_numbers, removed_texts = sanitize_svg(_output_text(response), numbers)
    except ValueError as exc:
        log.info("drawing unusable reason=%s", exc)
        return DrawnDiagramResult("unusable", model=settings.model, elapsed_ms=elapsed_ms,
                                  usage=counts, reason=str(exc))
    diagram = DrawnDiagram(
        asset_id=hashlib.sha256(content).hexdigest(), content=content,
        step_id=step_id, step_label=step_label, source_sha256=source_sha256,
        removed_numbers=removed_numbers, removed_texts=removed_texts,
        model=str(getattr(response, "model", None) or settings.model),
    )
    registry.keep(diagram)
    return DrawnDiagramResult("success", diagram, elapsed_ms=elapsed_ms, model=diagram.model, usage=counts)
