"""Build a protocol store that has a history, not one that was just created.

Three defects of the same shape have now reached a running system, and each
time a green suite of well over a thousand tests said nothing:

* STEP 14 -- a decoder that demanded an exact field set stopped opening a
  curated analysis written months earlier, because a field had been added
  since.
* STEP 22-B -- ``development_fixture_is_materialized`` asked whether the
  protocol had exactly one analysis revision.  Editing the fixture materialized
  a second one at the next start, and ``GET /api/protocols`` returned 503 from
  then on.

The common cause is not the individual check.  It is that every test starts
from an empty temporary store, so "a store that has been running for a while"
is a state the suite cannot reach.  A fresh store is the one case where a
uniqueness assumption always holds and an old payload never exists.

This module builds the missing state deliberately: a protocol whose analysis
revisions accumulated over two different fixture versions, and payloads written
the way an earlier version of this code would have written them.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

from voice_workflow_agent import experiment_protocol as domain
from voice_workflow_agent.curated_protocol import CuratedProtocolFixture
from voice_workflow_agent.experiment_protocol_analysis import (
    ProtocolAnalysisDraft,
)
from voice_workflow_agent.experiment_protocol_pdf import extract_protocol_pdf
from voice_workflow_agent.experiment_protocol_store import (
    ANALYSIS_SCHEMA_VERSION,
    serialize_analysis,
)

#: Every key ``serialize_analysis`` may emit.  A key that appears here without
#: appearing in ``TOLERATED_ABSENT_KEYS`` is one an older payload could not
#: have carried, so adding one is a decision about old stores, not a detail.
KNOWN_PAYLOAD_KEYS = frozenset(
    {
        "analysis_schema_version",
        "capability_policy_id",
        "protocol",
        "readiness",
        "page_coverage",
    }
)

#: Keys the decoder must still accept the absence of, because payloads written
#: before the key existed do not have it.
TOLERATED_ABSENT_KEYS = frozenset({"page_coverage"})

#: The analysis schema this corpus was written for.  Raising
#: ANALYSIS_SCHEMA_VERSION without revisiting this file leaves every stored
#: analysis in every existing deployment unreadable, so the mismatch is a test
#: failure rather than a runtime discovery.
CORPUS_ANALYSIS_SCHEMA_VERSION = 1


def curated_fixture_for(
    draft: ProtocolAnalysisDraft,
    *,
    marker: bytes,
    source_pdf: Path,
) -> CuratedProtocolFixture:
    """One development fixture, identified by its own content hash."""

    labels = tuple(
        step.source_label
        for section in draft.protocol.sections
        for step in section.steps
    )
    return CuratedProtocolFixture(
        draft=draft,
        status="development_only_not_final_acceptance",
        ordered_step_labels=labels,
        fixture_sha256=hashlib.sha256(marker).hexdigest(),
        development_only=True,
        source_pdf_path=source_pdf,
        source_pdf_sha256=draft.protocol.metadata.file_checksum,
        source_filename=draft.extraction.original_filename,
    )


def edited_fixture(
    fixture: CuratedProtocolFixture, *, marker: bytes
) -> CuratedProtocolFixture:
    """The same protocol after the fixture file was edited.

    Editing the fixture is an ordinary maintenance act -- STEP 22 rebound the
    step timers to the source and changed this file -- and the only thing the
    store sees is a different fixture hash, which names a different analysis.
    """

    return replace(fixture, fixture_sha256=hashlib.sha256(marker).hexdigest(),
                   revision_id="")


def sample_page_coverage(draft: ProtocolAnalysisDraft) -> tuple[dict, ...]:
    """One well-formed page-coverage record for the draft's own first page."""

    page = draft.extraction.pages[0]
    return (
        {
            "source_revision": "pdf-1",
            "source_sha256": draft.extraction.sha256,
            "source_page_number": page.source_page_number,
            "page_text_sha256": hashlib.sha256(
                page.text.encode("utf-8")
            ).hexdigest(),
            "status": "complete",
            "evidence_item_ids": [],
            "declined_segment_ids": [],
            "unaccounted_segment_ids": [],
        },
    )


def analysis_payload_as_written_before(
    draft: ProtocolAnalysisDraft, *, without: str
) -> str:
    """Serialize an analysis the way a version lacking ``without`` wrote it.

    The current serializer is asked for a payload that *does* carry the key,
    which is then removed.  Writing a payload that simply never had the key --
    by not supplying the data it comes from -- would produce the same bytes a
    current writer produces on a quiet day, and would prove nothing about
    older stores.
    """

    encoded, _ = serialize_analysis(
        draft.protocol,
        draft.readiness,
        draft.capability_policy_id,
        page_coverage=sample_page_coverage(draft),
    )
    payload = json.loads(encoded)
    if without not in payload:
        raise AssertionError(
            f"{without} is not a key this serializer emits, so removing it "
            f"does not describe any payload an older version wrote"
        )
    payload.pop(without)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def draft_for(source_pdf: Path, protocol_id: str) -> ProtocolAnalysisDraft:
    """A minimal analysis draft bound to one exact local PDF."""

    from tests.test_protocol_catalog import analysis_draft

    return analysis_draft(source_pdf, protocol_id, "Protocol Test")


__all__ = [
    "ANALYSIS_SCHEMA_VERSION",
    "CORPUS_ANALYSIS_SCHEMA_VERSION",
    "KNOWN_PAYLOAD_KEYS",
    "TOLERATED_ABSENT_KEYS",
    "analysis_payload_as_written_before",
    "curated_fixture_for",
    "domain",
    "draft_for",
    "sample_page_coverage",
    "edited_fixture",
    "extract_protocol_pdf",
]
