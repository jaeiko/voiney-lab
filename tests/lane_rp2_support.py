"""Fake experiment records for the report's first pages (lane RP2).

A protocol double (no licensed PDF) whose steps name their source page, a
review version, and three records written through the store with times a
person would take: a short run, a run with deviations, an anomaly, a
correction and a lost connection, and a run that stopped with values left
unconfirmed. The sample reports under ~/reports/rp2/ are made from these.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from voiney_lab import experiment_reports as er


def _statement(text: str) -> SimpleNamespace:
    return SimpleNamespace(source_text=text)


def _step(step_id: str, label: str, text: str, page: int, expected: tuple[str, ...] = ()) -> SimpleNamespace:
    return SimpleNamespace(
        step_id=step_id, source_label=label, instruction_source_text=text,
        evidence=SimpleNamespace(source_page_number=page),
        expected_results=tuple(_statement(item) for item in expected), sub_actions=(),
    )


STEPS = (
    ("Gel band destaining 30m", (
        ("1", "Cut the band into 1 mm pieces.", 2),
        ("2", "Wash the pieces with 500 µL solution A at 37°C for 10 min.", 2),
        ("3", "Incubate at 37°C for 15 min.", 3),
    )),
    ("Dehydration 15m", (
        ("4", "Remove the solution and add 200 µL acetonitrile.", 3),
        ("5", "Dry the gel pieces.", 4),
    )),
)
KOREAN = {
    "p-step-01": "1단계: 밴드를 1 mm 조각으로 자릅니다.",
    "p-step-02": "2단계: 조각을 Solution A 500 µL로 37°C에서 10 min 동안 씻습니다.",
    "p-step-03": "3단계: 37°C에서 15 min 동안 둡니다.",
    "p-step-04": "4단계: 용액을 버리고 acetonitrile 200 µL를 넣습니다.",
    "p-step-05": "5단계: 겔 조각을 말립니다.",
}


def protocol_double(*, labels: tuple[str, ...] | None = None, review: bool = True) -> SimpleNamespace:
    """Five steps on pages 2-4, a 15-minute timer on step 3, a review version."""

    sections = []
    for title, steps in STEPS:
        kept = tuple(
            _step(f"p-step-0{label}", label, text, page)
            for label, text, page in steps if labels is None or label in labels)
        if kept:
            sections.append(SimpleNamespace(title_source_text=title, steps=kept))
    page = ("Test digestion protocol\nProtocol Citation: Kim 2025. Test digestion protocol. protocols.io\n"
            "Keywords: gel digestion\n")
    protocol = SimpleNamespace(
        sections=tuple(sections),
        materials=(SimpleNamespace(name_source_text="Acetonitrile"),),
        equipment=(SimpleNamespace(name_source_text="Thermomixer"),),
        metadata=SimpleNamespace(pdf=SimpleNamespace(pages=(SimpleNamespace(text=page),))),
    )
    version = SimpleNamespace(
        version=2, reviewer_name="김검토", registered_on="2026-10-17", registered_at="2026-10-17T05:00:00+00:00",
        changes=(),
    ) if review else None
    return SimpleNamespace(
        draft=SimpleNamespace(protocol=protocol, review_version=version),
        revision_id="rev-test-1",
        timer_manifest={"p-step-03": 900},
        localized_fact=lambda step_id, fact_id: KOREAN.get(step_id) if fact_id == "current_step" else None,
    )


class RecordWriter:
    """One experiment record in a temporary store, its clock set by each event."""

    def __init__(self, directory: str | Path, *, session: str = "session-rp2-1",
                 title: str = "Test digestion protocol") -> None:
        self.store = er.ExperimentReportStore(Path(directory) / f"{session}.sqlite")
        self.clock = "2026-10-19T00:30:00+00:00"
        self.turn = 0
        with mock.patch.object(er, "_now", lambda: self.clock):
            self.report_id = self.store.open_report(
                session_id=session, protocol_id="protocol-rp2", protocol_title=title,
                protocol_revision="rev-test-1", protocol_sha256="9" * 64,
                readiness_status="analysis_passed", development_only=False,
            )["report_id"]

    def at(self, clock: str) -> None:
        self.clock = f"2026-10-19T{clock}:00+00:00"

    def event(self, clock: str, event_type: str, label: str | None, *, key: str | None = None,
              **fields) -> str:
        self.at(clock)
        self.turn += 1
        key = key or f"turn-{self.turn}"
        if event_type in {"purpose_recorded", "result_recorded", "observation", "anomaly"}:
            fields.setdefault("confirmation_state", "user_reported")
        with mock.patch.object(er, "_now", lambda: self.clock):
            self.store.append_event(
                self.report_id, event_key=key, event_type=event_type,
                step_id=f"p-step-0{label}" if label else None, step_label=label, **fields,
            )
        return key

    def finalize(self, clock: str, status: str) -> None:
        self.at(clock)
        with mock.patch.object(er, "_now", lambda: self.clock):
            self.store.finalize(self.report_id, status=status, event_key=f"final-{status}")

    def confirm(self, clock: str, item_id: str, text: str) -> None:
        self.event(clock, er.REVIEW_CONFIRMED, None, payload={"item_id": item_id, "text": text})

    def doc(self) -> dict:
        return self.store.get_report(self.report_id)


def _before_start(record: RecordWriter) -> None:
    record.event("00:29", "experimenter_recorded", None, payload={"display_name": "이실험"})
    record.event("00:29", "safety_notices_acknowledged", None, payload={"notice_count": 2})


#: The check before the start of the deviation run, as the protocol's ledger
#: keeps it (lane ON, decision ③): a wrong choice taken as it stands, then a
#: right one.
START_CHECK = (
    {"question_id": "q1:i-3:15min", "kind": "time", "item_id": "i-3", "step_label": "3",
     "value": "15 min", "chosen": "150 min", "correct": False, "action": "answer",
     "recorded_at": "2026-10-19T00:27:10+00:00"},
    {"question_id": "q1:i-3:15min", "kind": "time", "item_id": "i-3", "step_label": "3",
     "value": "15 min", "chosen": None, "correct": None, "action": "accept",
     "recorded_at": "2026-10-19T00:27:30+00:00"},
    {"question_id": "q2:i-2:37°c", "kind": "temperature", "item_id": "i-2", "step_label": "2",
     "value": "37°C", "chosen": "37°C", "correct": True, "action": "answer",
     "recorded_at": "2026-10-19T00:28:00+00:00"},
)


def short_run(directory: str | Path) -> RecordWriter:
    """Two steps, done in order, one measurement confirmed when it was said."""

    record = RecordWriter(directory, session="session-rp2-short", title="Short wash protocol")
    _before_start(record)
    record.event("00:30", "session_started", "1")
    record.event("00:33", "observation", "1", user_wording="조각 크기 1 mm", category="measurement",
                 payload={"experimenter_confirmed": True})
    record.event("00:36", "step_completed", "1")
    record.event("00:47", "step_completed", "2")
    record.event("00:47", "workflow_completed", "2")
    record.finalize("00:47", "completed")
    return record


def deviation_run(directory: str | Path) -> RecordWriter:
    """All five steps: a deviation with its source value, a timer ended early
    after a lost connection, an anomaly, a corrected measurement."""

    record = RecordWriter(directory, session="session-rp2-deviation")
    _before_start(record)
    record.event("00:30", "session_started", "1")
    measured = record.event("00:34", "observation", "1", user_wording="조각 크기 2 mm", category="measurement")
    record.event("00:36", "step_completed", "1")
    deviation = record.event(
        "00:40", "observation", "2", user_wording="40도에서 씻었어", category="deviation",
        payload={"deviations": [{"kind": "temperature", "said": "40°C", "number": "40", "unit": "°C",
                                 "source": "37°C"}]})
    record.event("00:47", "step_completed", "2")
    record.event("00:48", "timer_started", "3", payload={"timer": {"duration_seconds": 900}})
    record.event("00:52", "connection_restored", "3", payload={
        "lost_at": "2026-10-19T00:50:30+00:00", "restored_at": "2026-10-19T00:52:00+00:00",
        "gap_seconds": 90.0, "timers_ended": 0, "reason": "heartbeat_lost", "status": "in_progress"})
    early = record.event("00:58", "step_completed", "3", payload={"timer": {
        "completion_state": "step_exited_before_timer_elapsed", "step_exited_before_timer_elapsed": True,
        "elapsed_seconds": 600, "source_duration_seconds": 900}})
    anomaly = record.event("01:00", "anomaly", "4", user_wording="튜브를 쏟았어",
                           category="spill_exposure_safety_event", severity="unknown")
    record.event("01:02", "record_corrected", "1", payload={"record_fix": {
        "target_event_key": measured, "text_before": "조각 크기 2 mm", "text_after": "조각 크기 1 mm",
        "source": "voice"}})
    record.event("01:05", "step_completed", "4")
    record.event("01:10", "step_completed", "5")
    record.event("01:10", "workflow_completed", "5")
    record.finalize("01:10", "completed")
    record.event("01:11", er.REVIEW_DEFERRED, None, payload={})
    record.confirm("01:11", f"r.{measured}", "조각 크기 1 mm")
    record.confirm("01:11", f"r.{deviation}", "40도에서 씻었어")
    record.confirm("01:12", f"r.{anomaly}", "튜브를 쏟았어")
    record.confirm("01:12", f"t.{early}", "원문 시간 15분 중 10분에 단계를 끝냄")
    return record


def unconfirmed_run(directory: str | Path) -> RecordWriter:
    """Stopped at step 4 with two values the experimenter has not confirmed."""

    record = RecordWriter(directory, session="session-rp2-unconfirmed")
    _before_start(record)
    record.event("00:30", "session_started", "1")
    record.event("00:36", "step_completed", "1")
    record.event("00:40", "observation", "2", user_wording="세척액 450 µL", category="measurement")
    record.event("00:44", "step_completed", "2")
    record.event("00:45", "timer_started", "3", payload={"timer": {"duration_seconds": 900}})
    record.event("00:59", "timer_last_minute_notice", "3", payload={"spoken": True})
    record.event("01:00", "timer_end_notice", "3", payload={"spoken": True})
    record.event("01:01", "step_completed", "3")
    record.event("01:04", "observation", "4", user_wording="acetonitrile 180 µL 넣음", category="measurement")
    record.event("01:06", "session_stopped", "4", payload={"stop_reason": "stopped_by_user"})
    record.finalize("01:06", "stopped")
    record.event("01:07", er.REVIEW_DEFERRED, None, payload={})
    return record


#: The three sample records (lane RP2, decision 4), by the name of their files.
SAMPLES = ("rp2_sample1_short", "rp2_sample2_deviations", "rp2_sample3_unconfirmed")


def write_samples(directory: str | Path, *, converter: str | None = None, run=None) -> list[Path]:
    """Each sample record's report as .docx, .pdf and .md in ``directory``.

    The protocol is the double above; the check before the start is given
    for the deviation run only. ``converter``/``run`` stand in for
    LibreOffice in tests; left out, the server's LibreOffice prints the PDF.
    """

    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    with tempfile.TemporaryDirectory() as records:
        for name, build, fixture, check in zip(SAMPLES, (short_run, deviation_run, unconfirmed_run), (
                protocol_double(labels=("1", "2")), protocol_double(), protocol_double()), ((), START_CHECK, ())):
            record = build(records)
            for suffix, made in (
                ("docx", lambda: record.store.export_docx(record.report_id, fixture=fixture, start_check=check)),
                ("pdf", lambda: record.store.export_pdf(record.report_id, fixture=fixture, start_check=check,
                                                        converter=converter, run=run)),
                ("md", lambda: record.store.export_markdown(record.report_id, fixture=fixture, start_check=check)),
            ):
                path = out / f"{name}.{suffix}"
                path.write_bytes(made())
                written.append(path)
    return written


def environment() -> mock._patch:
    return mock.patch.dict(os.environ, {"VOINEY_LAB_REPORT_TIMEZONE": "Asia/Seoul"})


def temporary() -> tempfile.TemporaryDirectory:
    return tempfile.TemporaryDirectory()
