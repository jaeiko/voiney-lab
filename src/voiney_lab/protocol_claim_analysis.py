"""Page evidence segments: one page's text cut into exact numbered-action blocks.

What is left of the evidence-first claim-chunk path (deleted on 2026-10-13,
lane AN, decision 1; it lives in the git history). The claim DTOs, the claim
prompt and schema, chunk planning, the chunk cache, the merge and the audits
went with it. Only the segment generator stays, because two readers outside
the analysis still cite segments by their ids: the in-gel development
fixture's timer manifest (``curated_protocol._load_timer_manifest``) and the unread-page disclosure in
``CuratedProtocolSession``. The ids are unchanged: the same version, identity
fields and boundaries as before, so the manifest's handles still resolve.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from voiney_lab.experiment_protocol_pdf import ProtocolPdfExtraction


# The segment scheme's version; an id changes whenever it does.
# 3: step labels are at most three digits (a four-digit run is a citation
# year, not a step), so block boundaries derived under version 2 are not
# comparable with these.
# 4: a line ending in sentence punctuation also ends a block, so unnumbered
# content is no longer absorbed into the preceding numbered step.
# 6 since STEP 30: the running footer became a segment of its own, so the last
# segment of 35 of the 99 local pages lost its footer and changed identity. The
# leading segments of every page were untouched, which is why the ten cited
# timer handles and the reviewer's one cited handle still resolve -- but "most
# ids survived" is not the same as "the scheme is unchanged", and this number
# names the scheme.
EVIDENCE_SEGMENT_VERSION = 6
_MAX_PROVIDER_SEGMENT_CHARS = 4096
# A source step label is at most three digits.  A four-digit run followed by a
# period is a citation year ("Laliberté 2019. Measuring leaf carbon..."), not a
# step, and admitting it made the completeness invariant demand an action claim
# for the publication year.
# A hierarchical label ("3.2", "3.10", "3.1.1") is a step label in documents
# that number their procedure that way.  Measured on the four local sources,
# admitting only the bare integer left intracellular's entire procedure
# invisible: the twelve labels the system saw were headings and contents
# entries, and every real instruction -- "3.2 Pipette 1.6 mL of cold
# metabolite extraction buffer" -- began with a number this pattern could not
# read.
_NUMBERED_SOURCE_LINE = re.compile(
    r"(?m)^[ \t]*(?P<label>[1-9][0-9]{0,2}(?:\.[0-9]{1,3}){0,2})"
    r"(?:[.)])?[ \t]+(?P<next>\S+)"
)
# A line that ends a sentence ends a content unit.  Without this, every
# unnumbered block -- a hazard warning, a note, a preparation value -- is
# absorbed into the preceding numbered step and has no evidence unit of its
# own, so nothing can cite it and nothing can record that it was considered.
# Keyed on the line break rather than on the sentence, so a wrapped sentence
# stays whole.
_SENTENCE_LINE_END = re.compile(r"[.!?]\s*\n")
_VALUE_UNITS = {
    "c",
    "g",
    "h",
    "l",
    "mg",
    "min",
    "ml",
    "mm",
    "mm3",
    "rpm",
    "s",
    "ul",
    "µl",
    "μl",
}


@dataclass(frozen=True)
class ProtocolEvidenceSegment:
    """One deterministic, page-bounded unit of immutable extracted text."""

    segment_id: str
    source_revision: str
    source_sha256: str
    source_page_number: int
    page_text_sha256: str
    segment_index: int
    text: str


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _page_text_sha256(extraction: ProtocolPdfExtraction, page_number: int) -> str:
    return hashlib.sha256(
        extraction.pages[page_number - 1].text.encode("utf-8")
    ).hexdigest()


def _subordinate_to_a_label_on_the_page(
    matches: list[re.Match[str]],
) -> set[int]:
    """Offsets of hierarchical labels that hang off a label already here.

    "3.2" is a step where the procedure is numbered that way, and a note where
    step 3 is on the same page and 3.2 is written under it.  The two are told
    apart structurally, by whether the parent number is itself a label on this
    page, and never by reading the line.

    The measurement that settled the direction: on headspace, "6.1 While we use
    LB as the primary medium..." sits directly under "6 Transfer 10 mL of
    autoclaved LB...", and "18.1 Equation for working out dilution volume"
    directly under step 18 -- a remark and an equation, neither of them an
    instruction.  On intracellular no page carries both "3" and "3.4", because
    "3.4 Using stainless steel forceps, place the appropriate filter..." *is*
    the step.  Admitting the first pair would make the completeness invariant
    demand an action claim for a note, which is the failure STEP 12 removed.
    """

    labels = {match.group("label") for match in matches}
    return {
        match.start("label")
        for match in matches
        if "." in match.group("label")
        and match.group("label").rsplit(".", 1)[0] in labels
    }


def _numbered_action_matches(page_text: str) -> tuple[re.Match[str], ...]:
    matches = sorted(
        _NUMBERED_SOURCE_LINE.finditer(page_text),
        key=lambda match: match.start("label"),
    )
    subordinate = _subordinate_to_a_label_on_the_page(matches)
    actions: list[re.Match[str]] = []
    seen_offsets: set[int] = set()
    for match in matches:
        following = match.group("next").strip(".,:;()[]{}").casefold()
        offset = match.start("label")
        if (
            following.isdecimal()
            or following in _VALUE_UNITS
            or offset in seen_offsets
            or offset in subordinate
        ):
            continue
        actions.append(match)
        seen_offsets.add(offset)
    return tuple(actions)


def _bounded_action_block_boundaries(
    page_text: str, bottom_band_offset: int | None = None
) -> tuple[int, ...]:
    """Return exact action-block boundaries with a deterministic size ceiling.

    The running footer is a boundary of its own. Measured across the four local
    sources, the footer was merged into whatever preceded it on 31 segments, so
    a timer badge and a page number became one unit of evidence: headspace page
    6's "1h 30m" arrived glued to "protocols.io | ... 6/16", and a claim citing
    the duration cited the page number with it. The split is geometric -- see
    ProtocolPdfPage.bottom_band_offset -- and reads no words.
    """

    coarse = sorted(
        {
            0,
            len(page_text),
            *((bottom_band_offset,) if bottom_band_offset else ()),
            *(match.start("label") for match in _numbered_action_matches(page_text)),
            *(match.end() for match in _SENTENCE_LINE_END.finditer(page_text)),
        }
    )
    bounded = [coarse[0]]
    for block_end in coarse[1:]:
        block_start = bounded[-1]
        while block_end - block_start > _MAX_PROVIDER_SEGMENT_CHARS:
            hard_end = block_start + _MAX_PROVIDER_SEGMENT_CHARS
            newline = page_text.rfind("\n", block_start + 1, hard_end + 1)
            split_at = newline + 1 if newline >= block_start + 1 else hard_end
            bounded.append(split_at)
            block_start = split_at
        if bounded[-1] != block_end:
            bounded.append(block_end)
    return tuple(bounded)


def generate_page_evidence_segments(
    extraction: ProtocolPdfExtraction,
    *,
    source_revision: str,
    page_number: int,
) -> tuple[ProtocolEvidenceSegment, ...]:
    """Split one page into exact, bounded numbered-action blocks."""

    if (
        not isinstance(source_revision, str)
        or not source_revision.strip()
        or not isinstance(page_number, int)
        or isinstance(page_number, bool)
        or page_number < 1
        or page_number > extraction.page_count
    ):
        raise ValueError("Evidence segment source identity is invalid.")
    page_text = extraction.pages[page_number - 1].text
    ordered_boundaries = _bounded_action_block_boundaries(
        page_text, extraction.pages[page_number - 1].bottom_band_offset
    )
    segment_texts = tuple(
        page_text[start:end]
        for start, end in zip(ordered_boundaries, ordered_boundaries[1:])
        if end > start
    )
    if "".join(segment_texts) != page_text:
        raise ValueError("Evidence segments do not reconstruct the source page.")
    page_hash = _page_text_sha256(extraction, page_number)
    segments: list[ProtocolEvidenceSegment] = []
    for segment_index, text in enumerate(segment_texts):
        identity = {
            "evidence_segment_version": EVIDENCE_SEGMENT_VERSION,
            "source_revision": source_revision,
            "source_sha256": extraction.sha256,
            "source_page_number": page_number,
            "page_text_sha256": page_hash,
            "segment_index": segment_index,
            "segment_text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }
        segment_id = "seg-" + hashlib.sha256(
            _canonical_json(identity).encode("utf-8")
        ).hexdigest()
        segments.append(
            ProtocolEvidenceSegment(
                segment_id=segment_id,
                source_revision=source_revision,
                source_sha256=extraction.sha256,
                source_page_number=page_number,
                page_text_sha256=page_hash,
                segment_index=segment_index,
                text=text,
            )
        )
    return tuple(segments)
