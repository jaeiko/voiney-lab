"""NOTE2: source-evidenced figures and checked Korean in the notebook."""

from __future__ import annotations

import base64
import io
from types import SimpleNamespace

from docx import Document

from tests.lane_rp3_support import (
    RecordWriter,
    _start,
    cell_protocol,
    cell_run,
    coating_protocol,
    coating_run,
    temporary,
)
from voiney_lab import experiment_reports as er
from voiney_lab.report_projection import project_step_for_report


def _steps(fixture: SimpleNamespace) -> tuple[SimpleNamespace, ...]:
    return tuple(step for section in fixture.draft.protocol.sections for step in section.steps)


def test_missing_source_instruction_is_left_empty_instead_of_invented() -> None:
    section = SimpleNamespace(title_source_text="Preparation")
    step = SimpleNamespace(step_id="step-empty", source_label="A", evidence=None)

    projected = project_step_for_report(section, step)

    assert projected.instruction_source_text == ""


def test_methods_show_checked_korean_first_and_name_rejected_or_missing_translation() -> None:
    with temporary() as directory:
        record = cell_run(directory, session="session-note2-translations")
        fixture = cell_protocol()
        fixture.translation_failures = {"p-2/current_step": "quantities_changed"}
        checked = fixture.localized_fact
        fixture.localized_fact = lambda step_id, fact_id: (
            None if step_id in {"p-2", "p-3"} else checked(step_id, fact_id)
        )
        fixture.localization_source = lambda step_id, fact_id: (
            "reviewed" if fixture.localized_fact(step_id, fact_id) else None
        )

        book = record.store._narrative(
            record.report_id, None, fixture, start_check=None, previous=None,
        ).facts.notebook

    assert book is not None
    assert book.method_rows[0].text_ko.startswith("A549 세포를")
    assert book.method_rows[0].translation_status == "passed"
    assert book.method_rows[0].source_text.startswith("Seed A549 cells")
    assert book.method_rows[1].translation_status == "rejected"
    assert book.method_rows[1].text_ko == "번역 검사를 통과하지 못해 원문을 보여 드려요."
    assert book.method_rows[2].translation_status == "missing"
    assert book.method_rows[2].text_ko == "번역이 없어 원문을 보여 드려요."


def test_only_explicit_step_visual_evidence_is_attached_and_never_repeated() -> None:
    with temporary() as directory:
        record = cell_run(directory, session="session-note2-visual-evidence")
        fixture = cell_protocol()
        fixture.protocol_id = "protocol-rp3-cell"
        fixture.source_pdf_sha256 = "7" * 64
        fixture.steps = _steps(fixture)
        verified = SimpleNamespace(
            asset_id="source-crop-2-object-1",
            source_document_id="7" * 64,
            source_page=2,
            normalized_bounding_box=(0.1, 0.2, 0.3, 0.4),
            caption_primary="세포 배치 그림",
            caption_source="Figure 1. Cell layout.",
            mime_type="image/png",
            sha256="8" * 64,
        )
        # A malformed manifest that repeats one selected crop is still
        # projected only once.  Every other completed step has no visual
        # evidence and therefore receives no picture.
        fixture.visual_for_step = lambda index: verified if index in {0, 1} else None
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
        fixture.visual_content = lambda index: (verified, png)

        book = record.store._narrative(
            record.report_id, None, fixture, start_check=None, previous=None,
        ).facts.notebook
        document = Document(io.BytesIO(record.store.export_docx(
            record.report_id, fixture=fixture, start_check=None, previous=None,
        )))

    assert book is not None
    figures = [image for image in book.images if image.kind == "source_figure"]
    assert len(figures) == 1
    assert figures[0].step_label == "1"
    assert figures[0].asset_id == verified.asset_id
    assert figures[0].source_page == 2
    assert figures[0].source_document_id == "7" * 64
    assert figures[0].bounding_box == verified.normalized_bounding_box
    assert figures[0].caption == "세포 배치 그림"
    assert figures[0].caption_source == "Figure 1. Cell layout."
    assert len(document.inline_shapes) == 1


def test_terminal_timeline_item_is_completed_or_stopped() -> None:
    with temporary() as directory:
        completed = cell_run(directory, session="session-note2-completed")
        stopped = coating_run(directory)
        completed_book = completed.store._narrative(
            completed.report_id, None, cell_protocol(), start_check=None, previous=None,
        ).facts.notebook
        stopped_book = stopped.store._narrative(
            stopped.report_id, None, coating_protocol(), start_check=None, previous=None,
        ).facts.notebook

    assert completed_book is not None and completed_book.timeline[-1].kind == "completed"
    assert stopped_book is not None and stopped_book.timeline[-1].kind == "stopped"


def test_readback_and_correction_states_keep_the_original_record() -> None:
    with temporary() as directory:
        record = RecordWriter(
            directory, session="session-note2-record-state",
            title="Cell viability assay after drug treatment",
            protocol_id="protocol-rp3-cell",
        )
        _start(record, "기록자")
        record.event("00:30", "session_started", "1")
        purpose_key = record.event(
            "00:31", "purpose_recorded", "1", user_wording="처음 목적",
            confirmation_state="read_back", payload={"samples": []},
        )
        record.event("00:32", "record_corrected", "1", payload={"record_fix": {
            "target_event_key": purpose_key, "text_before": "처음 목적",
            "text_after": "고친 목적", "source": "voice",
        }})
        observation_key = record.event(
            "00:33", "observation", "1", user_wording="처음 관찰",
            category="appearance", confirmation_state="read_back",
        )
        record.event("00:34", "record_corrected", "1", payload={"record_fix": {
            "target_event_key": observation_key, "text_before": "처음 관찰",
            "text_after": "고친 관찰", "source": "voice",
        }})
        result_key = record.event(
            "00:35", "result_recorded", "1", user_wording="처음 결과",
            confirmation_state="read_back", payload={"result": "처음 결과", "next_change": ""},
        )
        record.event("00:36", "record_corrected", "1", payload={"record_fix": {
            "target_event_key": result_key, "text_before": "처음 결과",
            "text_after": "고친 결과", "source": "voice",
        }})
        record.event("00:37", "session_stopped", "1")
        record.finalize("00:37", "stopped")
        narrative = record.store._narrative(
            record.report_id, None, cell_protocol(), start_check=None, previous=None,
        )
        book = narrative.facts.notebook
        markdown = er.render_markdown(narrative)

    assert book is not None
    assert (book.purpose_words, book.purpose_state.code, book.purpose_state.original) == (
        "고친 목적", "corrected", "처음 목적",
    )
    assert (book.result_words, book.result_state.code, book.result_state.original) == (
        "고친 결과", "corrected", "처음 결과",
    )
    assert (book.observations[0].text, book.observations[0].state.code,
            book.observations[0].state.original) == (
        "고친 관찰", "corrected", "처음 관찰",
    )
    assert "정정됨" in markdown
    assert "처음 기록: 처음 목적" in markdown
    assert "처음 기록: 처음 결과" in markdown
    assert "처음 기록: 처음 관찰" in markdown


def test_empty_notebook_sections_use_one_honest_line_instead_of_blank_blocks() -> None:
    with temporary() as directory:
        record = RecordWriter(
            directory, session="session-note2-empty",
            title="Cell viability assay after drug treatment",
            protocol_id="protocol-rp3-cell",
        )
        _start(record, "기록자")
        record.event("00:30", "session_started", "1")
        record.event("00:31", "session_stopped", "1")
        record.finalize("00:31", "stopped")
        narrative = record.store._narrative(
            record.report_id, None, cell_protocol(), start_check=None, previous=None,
        )
        blocks = er.report_blocks(narrative)

    assert not any(kind == "p" and value == "" for kind, value in blocks)
    assert not any(kind == "sign" for kind, _value in blocks)
    assert sum(kind == "note" and value == "기록 없음" for kind, value in blocks) >= 4
