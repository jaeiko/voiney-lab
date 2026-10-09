"""The source's own pictures for a step, cut from the uploaded PDF (lane WV, decision 1).

The uploaded protocol comes first: before anything from the web or anything
drawn, the step's own figures are shown -- the pictures printed on the page
the step's evidence sits on (and the page it continues on), with the caption
printed under each one when there is one.

Which placed picture is a figure is decided by geometry alone, never by a
model and never by a word of the text:

* a picture smaller than ``FIGURE_MIN_POINTS`` on the page, or whose own
  bytes are fewer than ``FIGURE_MIN_PIXELS`` a side, is a mark or a fill;
* a picture placed on three or more pages of the document is a logo or a
  running header (the engine reports those xrefs);
* a picture with a run of text printed over it is a card -- protocols.io
  draws each note, expected-result and equipment box as an image under
  its text -- not a figure;
* a picture that contains another kept picture is that picture's frame
  (the card around a photograph), so the inner one is kept;
* pictures that touch (a figure delivered in tiles) are one figure.

The caption is the nearest text block printed under the figure within
``CAPTION_MAX_GAP_POINTS``, starting within the figure's width, that is not
a numbered step line. It is the source's own words, shown as they are.

The crops are rendered by the one PDF engine (``pdf_text_engine``) in its
child process and kept in memory only, keyed by the source's SHA-256 and
the page, so a page is cut once per server run. Nothing here writes a file
or decides a workflow state.
"""

from __future__ import annotations

import collections
import hashlib
import logging
import re
import struct
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from voiney_lab import pdf_text_engine as engine
from voiney_lab.experiment_protocol_pdf import extract_protocol_pdf

log = logging.getLogger("voiney_lab.source_figures")

#: A picture narrower or shorter than this on the page (points) is a mark.
FIGURE_MIN_POINTS = 48.0
#: A picture whose own bytes are fewer pixels a side than this is a fill.
FIGURE_MIN_PIXELS = 64
#: A picture covering less of the page than this is a badge, a QR code, a
#: logo (protocols.io's title page carries both at just under 2%).
FIGURE_MIN_AREA_FRACTION = 0.022
#: A text block of at least this many characters printed over a picture
#: makes it a card (text drawn on a box), not a figure.
CARD_TEXT_MIN_CHARS = 24
#: Pictures closer than this (points) are tiles of one figure.
TILE_GAP_POINTS = 4.0
#: A caption starts at most this far (points) under the figure.
CAPTION_MAX_GAP_POINTS = 36.0
#: A block under the figure that does not announce itself as a caption
#: ("Figure 1", "Fig. 2") is taken as one only when it is short -- a
#: protocols.io legend -- and not a label ("Note") or a numbered step line.
CAPTION_MIN_CHARS = 8
CAPTION_MAX_CHARS = 200
#: A block that announces itself as a caption is shown up to this length.
CAPTION_WORD_MAX_CHARS = 600
#: The margin (points) kept around a crop.
CROP_MARGIN_POINTS = 2.0
#: How many (document, page) entries are kept in memory.
CACHE_PAGES = 96

_FIGURE_ID = re.compile(r"^source-figure-([1-9][0-9]*)-([1-9][0-9]*)$")
#: A numbered step line ("8Centrifuge…", "2.1 Add…"): never a caption.
_STEP_LINE = re.compile(r"^\s*\d{1,3}(?:\.\d{1,3})*\s*(?=[A-Za-z가-힣(])")
#: A block that announces itself as a figure caption ("Figure 1", "Fig. 2",
#: "그림 3"), at its start or after a title line printed over the legend.
_CAPTION_WORD = re.compile(
    r"(?:^|\s)(?:fig(?:ure)?s?\.?|scheme|그림|사진)\s*\d", re.IGNORECASE,
)


@dataclass(frozen=True)
class SourceFigure:
    """One picture cut from the source, as the page and the browser see it."""

    figure_id: str
    source_page: int
    #: Points, top-left origin, on the source page: x0, y0, x1, y1.
    bounding_box: tuple[float, float, float, float]
    #: The source's caption under the picture, or "" when none is printed.
    caption: str
    mime_type: str
    sha256: str
    byte_size: int
    width_px: int
    height_px: int

    def public_dict(
        self, *, protocol_id: str, revision_id: str, source_sha256: str,
    ) -> dict[str, Any]:
        return {
            "figure_id": self.figure_id,
            "kind": "source_figure",
            "protocol_id": protocol_id,
            "revision_id": revision_id,
            "source_document_id": source_sha256,
            "source_page": self.source_page,
            "bounding_box": list(self.bounding_box),
            "caption": self.caption,
            "mime_type": self.mime_type,
            "sha256": self.sha256,
            "byte_size": self.byte_size,
            "width_px": self.width_px,
            "height_px": self.height_px,
            "label": f"원문 그림 · PDF p.{self.source_page}",
            "url": (
                f"/api/protocols/{quote(protocol_id, safe='')}/revisions/"
                f"{quote(revision_id, safe='')}/figures/{quote(self.figure_id, safe='')}"
            ),
        }


@dataclass(frozen=True)
class _Stored:
    figure: SourceFigure
    content: bytes


_cache: collections.OrderedDict[tuple[str, int], tuple[_Stored, ...]] = (
    collections.OrderedDict()
)
_cache_lock = threading.Lock()


def clear_source_figure_cache() -> None:
    with _cache_lock:
        _cache.clear()


# --- geometry ----------------------------------------------------------------

_Box = tuple[float, float, float, float]


def _contains(outer: _Box, inner: _Box, tolerance: float = 1.0) -> bool:
    return (
        outer[0] - tolerance <= inner[0]
        and outer[1] - tolerance <= inner[1]
        and outer[2] + tolerance >= inner[2]
        and outer[3] + tolerance >= inner[3]
        and (outer[2] - outer[0]) * (outer[3] - outer[1])
        > (inner[2] - inner[0]) * (inner[3] - inner[1]) + 1.0
    )


def _touches(a: _Box, b: _Box, gap: float) -> bool:
    return (
        a[0] - gap <= b[2] and b[0] - gap <= a[2]
        and a[1] - gap <= b[3] and b[1] - gap <= a[3]
    )


def _union(a: _Box, b: _Box) -> _Box:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _text_over(box: _Box, blocks: Sequence[Any]) -> bool:
    """Whether a run of text is printed over the picture (a card)."""

    for block in blocks:
        text = " ".join(str(block.text).split())
        if len(text) < CARD_TEXT_MIN_CHARS:
            continue
        cx = (block.x0 + block.x1) / 2
        cy = (block.y0 + block.y1) / 2
        if box[0] <= cx <= box[2] and box[1] <= cy <= box[3]:
            return True
    return False


def figure_boxes(
    page: engine.PdfPageImages, blocks: Sequence[Any], repeated_xrefs: frozenset[int],
) -> tuple[_Box, ...]:
    """The figures on one page as boxes (points), top to bottom; see the module."""

    kept: list[_Box] = []
    page_area = max(1.0, page.width * page.height)
    for image in page.images:
        # Clamped to the page: a picture can be placed partly off it.
        box = (
            max(0.0, image.x0), max(0.0, image.y0),
            min(page.width, image.x1), min(page.height, image.y1),
        )
        if box[2] - box[0] < FIGURE_MIN_POINTS or box[3] - box[1] < FIGURE_MIN_POINTS:
            continue
        if (box[2] - box[0]) * (box[3] - box[1]) < FIGURE_MIN_AREA_FRACTION * page_area:
            continue
        if 0 < image.width_px < FIGURE_MIN_PIXELS or 0 < image.height_px < FIGURE_MIN_PIXELS:
            continue
        if image.xref and image.xref in repeated_xrefs:
            continue
        if _text_over(box, blocks):
            continue
        kept.append(box)
    # A picture that frames another kept picture is the frame.
    inner = [
        box for box in kept
        if not any(other is not box and _contains(box, other) for other in kept)
    ]
    # Tiles of one figure.
    merged: list[_Box] = []
    for box in inner:
        joined = box
        rest: list[_Box] = []
        for other in merged:
            if _touches(joined, other, TILE_GAP_POINTS):
                joined = _union(joined, other)
            else:
                rest.append(other)
        merged = rest + [joined]
    # Merging can make a box that now touches an earlier one; settle it.
    changed = True
    while changed:
        changed = False
        for index, box in enumerate(merged):
            for other in merged[index + 1:]:
                if _touches(box, other, TILE_GAP_POINTS):
                    merged.remove(other)
                    merged[index] = _union(box, other)
                    changed = True
                    break
            if changed:
                break
    return tuple(sorted(merged, key=lambda box: (round(box[1]), round(box[0]))))


def caption_under(box: _Box, blocks: Sequence[Any], page_height: float) -> str:
    """The source's caption printed under the figure, or ""."""

    width = box[2] - box[0]
    best: Any = None
    for block in blocks:
        gap = block.y0 - box[3]
        if gap < -2.0 or gap > CAPTION_MAX_GAP_POINTS:
            continue
        if block.x1 <= box[0] or block.x0 >= box[2]:
            continue
        if page_height and block.y0 > page_height * (1.0 - engine.BOTTOM_BAND_FRACTION):
            continue
        announced = _CAPTION_WORD.search(str(block.text)[:160]) is not None
        # A legend that does not announce itself must start within the
        # figure's width; "Figure 1 …" may start at the column's margin.
        if not announced and (block.x0 < box[0] - 12.0 or block.x0 > box[0] + 0.5 * width):
            continue
        if best is None or block.y0 < best.y0:
            best = block
    if best is None:
        return ""
    text = " ".join(str(best.text).split())
    if _CAPTION_WORD.search(text[:160]):
        if len(text) > CAPTION_WORD_MAX_CHARS:
            text = text[:CAPTION_WORD_MAX_CHARS].rstrip() + "…"
        return text
    if (
        len(text) < CAPTION_MIN_CHARS or len(text) > CAPTION_MAX_CHARS
        or _STEP_LINE.match(text)
    ):
        return ""
    return text


# --- the cut -------------------------------------------------------------------


def _png_size(png: bytes) -> tuple[int, int]:
    if len(png) < 24 or not png.startswith(b"\x89PNG\r\n\x1a\n"):
        return 0, 0
    width, height = struct.unpack(">II", png[16:24])
    return int(width), int(height)


def _cut_page(source_pdf: Path, source_sha256: str, page_number: int) -> tuple[_Stored, ...]:
    extraction = extract_protocol_pdf(source_pdf)
    if extraction.sha256 != source_sha256:
        log.warning("source figures: the source identity changed; nothing cut")
        return ()
    if not 1 <= page_number <= len(extraction.pages):
        return ()
    blocks = tuple(extraction.pages[page_number - 1].blocks)
    found = engine.page_images(source_pdf, (page_number,))
    if not found.pages:
        return ()
    page = found.pages[0]
    boxes = figure_boxes(page, blocks, found.repeated_xrefs)
    if not boxes:
        return ()
    clips = []
    for box in boxes:
        clips.append((page_number, (
            max(0.0, box[0] - CROP_MARGIN_POINTS), max(0.0, box[1] - CROP_MARGIN_POINTS),
            min(page.width, box[2] + CROP_MARGIN_POINTS),
            min(page.height, box[3] + CROP_MARGIN_POINTS),
        )))
    pngs = engine.render_clips_png(source_pdf, clips)
    stored: list[_Stored] = []
    for index, (box, png) in enumerate(zip(boxes, pngs, strict=True), start=1):
        width_px, height_px = _png_size(png)
        if not width_px or not height_px:
            continue
        figure = SourceFigure(
            figure_id=f"source-figure-{page_number}-{index}",
            source_page=page_number,
            bounding_box=tuple(round(value, 2) for value in box),
            caption=caption_under(box, blocks, page.height),
            mime_type="image/png",
            sha256=hashlib.sha256(png).hexdigest(),
            byte_size=len(png),
            width_px=width_px,
            height_px=height_px,
        )
        stored.append(_Stored(figure, png))
    return tuple(stored)


def _page_entries(source_pdf: Path, source_sha256: str, page_number: int) -> tuple[_Stored, ...]:
    key = (source_sha256, int(page_number))
    with _cache_lock:
        cached = _cache.get(key)
        if cached is not None:
            _cache.move_to_end(key)
            return cached
    try:
        entries = _cut_page(Path(source_pdf), source_sha256, int(page_number))
    except engine.PdfEngineError as exc:
        log.warning("source figures: engine failed page=%s error=%s", page_number, type(exc).__name__)
        entries = ()
    except Exception as exc:  # noqa: BLE001 - a picture is never worth a failed turn
        log.warning("source figures: page=%s error=%s", page_number, type(exc).__name__)
        entries = ()
    with _cache_lock:
        _cache[key] = entries
        _cache.move_to_end(key)
        while len(_cache) > CACHE_PAGES:
            _cache.popitem(last=False)
    return entries


def figures_for_pages(
    source_pdf: Path | None, source_sha256: str | None, pages: Sequence[int],
) -> tuple[SourceFigure, ...]:
    """The figures on the given pages, in page order; () without a source."""

    if source_pdf is None or not isinstance(source_sha256, str) or len(source_sha256) != 64:
        return ()
    figures: list[SourceFigure] = []
    for page_number in sorted({int(page) for page in pages if isinstance(page, int) and page > 0}):
        figures.extend(entry.figure for entry in _page_entries(source_pdf, source_sha256, page_number))
    return tuple(figures)


def figure_content(
    source_pdf: Path | None, source_sha256: str | None, figure_id: str,
) -> tuple[SourceFigure, bytes] | None:
    """The figure and its PNG bytes, or None when the id names no figure."""

    match = _FIGURE_ID.match(figure_id or "")
    if match is None or source_pdf is None or not isinstance(source_sha256, str):
        return None
    for entry in _page_entries(source_pdf, source_sha256, int(match.group(1))):
        if entry.figure.figure_id == figure_id:
            return entry.figure, entry.content
    return None


def step_pages(step: Any) -> tuple[int, ...]:
    """The page a step's evidence is printed on, and the page it continues on."""

    evidence = getattr(step, "evidence", None)
    pages = []
    for name in ("source_page_number", "continued_on_page_number"):
        value = getattr(evidence, name, None)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            pages.append(value)
    return tuple(dict.fromkeys(pages))


def cached_figures_for_step(fixture: Any, index: int) -> tuple[SourceFigure, ...] | None:
    """The step's figures when every one of its pages is already cut; None otherwise.

    For the screen fields, which are built on the event loop: nothing here
    opens the PDF. The server cuts the pages in a thread and sends the
    figures after.
    """

    steps = getattr(fixture, "steps", ())
    source_sha256 = getattr(fixture, "source_pdf_sha256", None)
    if (
        not isinstance(index, int) or not 0 <= index < len(steps)
        or getattr(fixture, "source_pdf_path", None) is None
        or not isinstance(source_sha256, str) or len(source_sha256) != 64
    ):
        return ()
    figures: list[SourceFigure] = []
    with _cache_lock:
        for page_number in sorted(step_pages(steps[index])):
            entries = _cache.get((source_sha256, page_number))
            if entries is None:
                return None
            figures.extend(entry.figure for entry in entries)
    return tuple(figures)


def figures_for_step(fixture: Any, index: int) -> tuple[SourceFigure, ...]:
    """The current step's source figures, or () when the fixture has no PDF."""

    steps = getattr(fixture, "steps", ())
    if not isinstance(index, int) or not 0 <= index < len(steps):
        return ()
    return figures_for_pages(
        getattr(fixture, "source_pdf_path", None),
        getattr(fixture, "source_pdf_sha256", None),
        step_pages(steps[index]),
    )
