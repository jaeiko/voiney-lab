"""The v5 common-noun table applies to stored and reader Korean alike."""
from __future__ import annotations

import pytest

from tests.protocol_vocabulary_support import build_fixture
from tests.test_lane_safe_questions import workflow_checkpoint
from voiney_lab import protocol_translation as pt
from voiney_lab.curated_protocol import CuratedProtocolSession, reader_translation_issue
from voiney_lab.workspace_store import FactTranslationRecord


@pytest.mark.parametrize(("term", "reading"), [
    ("band", "밴드를 옮겨요."), ("bands", "밴드를 옮겨요."),
    ("gel", "젤을 옮겨요."), ("vials", "바이알을 옮겨요."),
    ("tube", "튜브를 옮겨요."), ("beads", "비즈를 옮겨요."),
    ("supernatant", "상층액을 옮겨요."), ("pipette", "피펫을 옮겨요."),
    ("agarose gel", "agarose 젤을 옮겨요."),
    ("PCR tube", "PCR 튜브를 옮겨요."),
])
def test_common_nouns_are_equivalent_in_both_translation_checks(term: str, reading: str) -> None:
    source = f"Transfer the {term}."
    assert reader_translation_issue(source, reading, required_terms=(term,)) is None
    assert pt.statement_issue(source, reading, required_terms=(term,)) is None


@pytest.mark.parametrize(("term", "reading", "reason"), [
    ("band", "옮겨요.", "term_missing"), ("PCR tube", "튜브를 옮겨요.", "term_missing"),
    ("agarose gel", "젤을 옮겨요.", "term_missing"),
    ("Band100", "밴드100을 옮겨요.", "term_missing"),
    ("Gel-100", "젤-100을 옮겨요.", "quantities_changed"),
    ("SupernatantPro", "상층액Pro를 옮겨요.", "term_missing"),
    ("DTT", "디티티를 옮겨요.", "term_missing"),
    ("SpeedVac", "스피드백을 옮겨요.", "term_missing"),
    ("SYBR Safe Gel Stain", "SYBR Safe 젤 Stain을 옮겨요.", "term_missing"),
])
def test_equivalence_keeps_names_identifiers_and_the_common_noun_required(
    term: str, reading: str, reason: str,
) -> None:
    source = f"Transfer the {term}."
    assert reader_translation_issue(source, reading, required_terms=(term,)) == reason
    assert pt.statement_issue(source, reading, required_terms=(term,)) == reason


@pytest.mark.parametrize(("source", "reading", "reason"), [
    ("Use 1.0 mL in the tube.", "튜브에 1 mL를 사용해요.", "quantities_changed"),
    ("Use 10 µM in the tube.", "튜브에 10 mM를 사용해요.", "quantities_changed"),
    ("Do not move the tube.", "튜브를 옮겨요.", "negation_changed"),
    ("The gel is toxic.", "젤이에요.", "hazard_missing"),
])
def test_common_nouns_do_not_relax_quantity_negation_or_hazard_checks(
    source: str, reading: str, reason: str,
) -> None:
    term = "gel" if "gel" in source else "tube"
    assert pt.statement_issue(source, reading, required_terms=(term,)) == reason


@pytest.mark.parametrize(("field", "term", "reading"), [
    ("nucleic_acid", "agarose gel", "1단계: agarose 젤을 옮겨요."),
    ("analytical_chemistry", "headspace vial", "1단계: headspace 바이알을 옮겨요."),
    ("battery_materials", "centrifuge tube", "1단계: centrifuge 튜브를 옮겨요."),
])
def test_stored_v5_rows_are_rechecked_without_a_call_or_workflow_change(
    field: str, term: str, reading: str,
) -> None:
    fixture = build_fixture(protocol_id=f"fictional-{field}", title=field,
                            steps=(f"1. Transfer the {term}.",), equipment=(term,))
    unit = next(u for u in pt.translation_units(fixture) if u.kind == "step")
    glossary = (pt.GlossaryEntry(term, term, True),)
    row = FactTranslationRecord(
        pt.translation_revision_key(fixture), unit.fact_key, "ko", unit.source_sha256,
        reading, "machine", "term_missing", "fake", "fake", "2026-10-27T00:00:00+00:00", 5)
    assert pt.check_translation(unit, reading, glossary) == "passed"
    attached = pt.with_stored_translations(fixture, [row], glossaries={5: glossary})
    assert attached.localized_fact(*unit.fact_key.split("/", 1)) == reading
    session = CuratedProtocolSession(attached)
    before = workflow_checkpoint(session)
    state = session.state()
    assert state["translation_missing"] is False
    assert term.split()[0] in state["primary_summary"]
    assert workflow_checkpoint(session) == before
    assert row.check_result == "term_missing"  # immutable historical verdict
    assert row.style_version == pt.STYLE_VERSION == 5


def test_negation_exception_path_uses_the_same_term_equivalence() -> None:
    assert pt.statement_issue(
        "Prevent loss of the agarose gel.", "agarose 젤의 손실을 방지해요.",
        required_terms=("agarose gel",)) is None
