"""Fake experiment records for the lab notebook (lane RP3, decision ①, 2026-10-20).

Records written through the store with times a person would take, on the
lane RP2 protocol double (five steps on pages 2-4, a 15-minute timer on step
3, a review version) and on two protocol doubles of this lane: a cell
viability protocol (biology) and a sol-gel coating protocol (chemistry and
materials). Besides what lane RP2's records hold, these carry what decision
② records during the run -- the purpose and samples said at the start, the
result and what to change next said at the end -- so the notebook's reading
of them is tested before the voice rules write them.

The sample notebooks under ~/reports/rp3/ are made from these records.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from voiney_lab import experiment_reports as er

from tests.lane_rp2_support import protocol_double  # noqa: F401 -- re-exported for the tests


def _statement(text: str) -> SimpleNamespace:
    return SimpleNamespace(source_text=text)


def _step(step_id: str, label: str, text: str, page: int, expected: tuple[str, ...] = ()) -> SimpleNamespace:
    return SimpleNamespace(
        step_id=step_id, source_label=label, instruction_source_text=text,
        evidence=SimpleNamespace(source_page_number=page),
        expected_results=tuple(_statement(item) for item in expected), sub_actions=(),
    )


def _double(title: str, sections: tuple, korean: dict, materials: tuple, equipment: tuple,
            timers: dict, citation: str, doi: str) -> SimpleNamespace:
    built = []
    for section_title, steps in sections:
        built.append(SimpleNamespace(
            title_source_text=section_title,
            steps=tuple(_step(f"p-{label}", label, text, page, expected) for label, text, page, expected in steps)))
    page = f"{title}\nDOI: {doi}\nProtocol Citation: {citation}\nKeywords: test\n"
    protocol = SimpleNamespace(
        sections=tuple(built),
        materials=tuple(SimpleNamespace(name_source_text=name) for name in materials),
        equipment=tuple(SimpleNamespace(name_source_text=name) for name in equipment),
        metadata=SimpleNamespace(pdf=SimpleNamespace(pages=(SimpleNamespace(text=page),))),
    )
    return SimpleNamespace(
        draft=SimpleNamespace(protocol=protocol, review_version=None),
        revision_id="rev-rp3-1",
        timer_manifest=timers,
        localized_fact=lambda step_id, fact_id: korean.get(step_id) if fact_id == "current_step" else None,
    )


def cell_protocol() -> SimpleNamespace:
    """A cell viability protocol: seeding, treatment, an MTT read-out (biology)."""

    return _double(
        "Cell viability assay after drug treatment",
        (
            ("Seeding 1d", (
                ("1", "Seed A549 cells at 5000 cells per well in a 96-well plate in 100 µL medium.", 2, ()),
                ("2", "Incubate at 37°C for 24 h.", 2, ()),
            )),
            ("Treatment 1d", (
                ("3", "Add the compound at 0.5 µM and 5 µM; add vehicle (0.1% DMSO) to the control wells.", 3, ()),
                ("4", "Incubate at 37°C for 24 h.", 3, ()),
            )),
            ("Read-out 3h", (
                ("5", "Add 10 µL MTT reagent to each well and incubate at 37°C for 3 h.", 4,
                 ("Purple formazan crystals form in viable cells.",)),
                ("6", "Remove the medium, add 100 µL DMSO and read the absorbance at 570 nm.", 4, ()),
            )),
        ),
        {
            "p-1": "1단계: A549 세포를 96-well plate 에 well 당 5000 cells, 배지 100 µL 로 심습니다.",
            "p-2": "2단계: 37°C 에서 24 h 둡니다.",
            "p-3": "3단계: 화합물을 0.5 µM 과 5 µM 로 넣고, 대조군 well 에는 vehicle(0.1% DMSO)을 넣습니다.",
            "p-4": "4단계: 37°C 에서 24 h 둡니다.",
            "p-5": "5단계: well 마다 MTT 시약 10 µL 를 넣고 37°C 에서 3 h 둡니다.",
            "p-6": "6단계: 배지를 걷어 내고 DMSO 100 µL 를 넣은 뒤 570 nm 흡광도를 읽습니다.",
        },
        ("A549 cells", "MTT reagent Sigma-Aldrich Catalog #M5655", "DMSO"),
        ("96-well plate", "Plate reader NAME SpectraMax TYPE Molecular Devices BRAND 123456789 SKU"),
        {"p-5": 10800},
        "Park 2026. Cell viability assay after drug treatment. protocols.io",
        "https://dx.doi.org/10.1/cell.v1",
    )


def coating_protocol() -> SimpleNamespace:
    """A sol-gel coating protocol: solution, dip coating, curing (chemistry and materials)."""

    return _double(
        "Sol-gel silica coating on glass slides",
        (
            ("Sol preparation 1h", (
                ("1", "Mix 5 mL TEOS with 20 mL ethanol and stir at 300 rpm for 30 min.", 2, ()),
                ("2", "Add 2 mL of 0.1 M HCl dropwise and stir for 1 h at 25°C.", 2, ()),
            )),
            ("Coating 30m", (
                ("3", "Dip the cleaned slide into the sol and withdraw at 2 mm/s.", 3, ()),
                ("4", "Dry the slide at 60°C for 10 min.", 3, ("The film should be clear and uniform.",)),
            )),
            ("Curing 2h", (
                ("5", "Cure the slides at 400°C for 2 h in the furnace.", 4, ()),
                ("6", "Measure the film thickness with the profilometer.", 4, ()),
            )),
        ),
        {
            "p-1": "1단계: TEOS 5 mL 와 ethanol 20 mL 를 섞고 300 rpm 으로 30 min 젓습니다.",
            "p-2": "2단계: 0.1 M HCl 2 mL 를 한 방울씩 넣고 25°C 에서 1 h 젓습니다.",
            "p-3": "3단계: 씻은 슬라이드를 sol 에 담갔다가 2 mm/s 로 꺼냅니다.",
            "p-4": "4단계: 슬라이드를 60°C 에서 10 min 말립니다.",
            "p-5": "5단계: 슬라이드를 400°C 로에서 2 h 굽습니다.",
            "p-6": "6단계: profilometer 로 막 두께를 잽니다.",
        },
        ("TEOS (tetraethyl orthosilicate)", "Ethanol", "HCl 0.1 M"),
        ("Dip coater", "Furnace", "Profilometer"),
        {"p-4": 600, "p-5": 7200},
        "Lee 2026. Sol-gel silica coating on glass slides. protocols.io",
        "https://dx.doi.org/10.1/coating.v1",
    )


class RecordWriter:
    """One experiment record, its clock set by each event, in a store that may
    hold earlier runs of the same protocol (for "지난번과 다른 점")."""

    def __init__(self, directory: str | Path, *, session: str, title: str, protocol_id: str,
                 day: str = "2026-10-20", store: er.ExperimentReportStore | None = None,
                 revision: str = "rev-rp3-1", prefix: str = "p-") -> None:
        self.store = store or er.ExperimentReportStore(Path(directory) / "notebook.sqlite")
        self.day = day
        self.prefix = prefix
        self.clock = f"{day}T00:30:00+00:00"
        self.turn = 0
        with mock.patch.object(er, "_now", lambda: self.clock):
            self.report_id = self.store.open_report(
                session_id=session, protocol_id=protocol_id, protocol_title=title,
                protocol_revision=revision, protocol_sha256="7" * 64,
                readiness_status="analysis_passed", development_only=False,
            )["report_id"]

    def at(self, clock: str) -> None:
        self.clock = f"{self.day}T{clock}:00+00:00"

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
                step_id=f"{self.prefix}{label}" if label else None, step_label=label, **fields,
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


def _start(record: RecordWriter, name: str, notices: int = 1) -> None:
    record.event("00:29", "experimenter_recorded", None, payload={"display_name": name})
    record.event("00:29", "safety_notices_acknowledged", None, payload={"notice_count": notices})


#: What the experimenter said at the start and the end of the cell run, as
#: decision ② records them: the one utterance after each question.
CELL_PURPOSE = ("오늘은 A549 세포에 약물 X를 0.5랑 5 µM로 처리해서 생존율을 보는 실험이에요. "
                "1번 2번 샘플이 처리군이고 3번은 대조군")
CELL_SAMPLES = (
    {"name": "1번 샘플", "condition": "처리군", "control": False},
    {"name": "2번 샘플", "condition": "처리군", "control": False},
    {"name": "3번 샘플", "condition": "대조군", "control": True},
)
CELL_RESULT = "처리군 생존율이 대조군보다 낮았어. 다음에는 5 µM 농도를 2.5로 낮춰 볼게"


def cell_run(directory: str | Path, *, store: er.ExperimentReportStore | None = None,
             day: str = "2026-10-20", session: str = "session-rp3-cell") -> RecordWriter:
    """The cell viability run: purpose and samples said at the start, a
    measurement and an observation tied to samples, a deviation with its
    reason, a timer ended early, a correction, a result and a change for
    next time said at the end; every value confirmed."""

    record = RecordWriter(directory, session=session, title="Cell viability assay after drug treatment",
                          protocol_id="protocol-rp3-cell", day=day, store=store)
    _start(record, "이실험", notices=2)
    record.event("00:30", "session_started", "1")
    record.event("00:30", "purpose_recorded", "1", user_wording=CELL_PURPOSE,
                 payload={"samples": list(CELL_SAMPLES)})
    seeded = record.event("00:40", "observation", "1", user_wording="well 당 5000 cells 심었어",
                          category="measurement", payload={"experimenter_confirmed": True})
    record.event("00:42", "step_completed", "1")
    record.event("00:43", "step_completed", "2")
    deviation = record.event(
        "00:50", "observation", "3", user_wording="항온조가 안 내려가서 40도에서 넣었어", category="deviation",
        payload={"deviations": [{"kind": "temperature", "said": "40°C", "number": "40", "unit": "°C",
                                 "source": "37°C"}]})
    record.event("00:52", "step_completed", "3")
    record.event("00:53", "step_completed", "4")
    record.event("00:54", "timer_started", "5", payload={"timer": {"duration_seconds": 10800}})
    density = record.event("01:20", "observation", "5", user_wording="3번 샘플에서 세포 밀도가 예상보다 낮게 관찰됨",
                           category="appearance")
    early = record.event("03:30", "step_completed", "5", payload={"timer": {
        "completion_state": "step_exited_before_timer_elapsed", "step_exited_before_timer_elapsed": True,
        "elapsed_seconds": 9360, "source_duration_seconds": 10800}})
    viability = record.event("03:50", "observation", "6", user_wording="1번 샘플 생존율 72%",
                             category="measurement")
    record.event("03:51", "observation", "6", user_wording="2번 샘플 생존율 41%", category="measurement",
                 payload={"experimenter_confirmed": True})
    record.event("03:52", "record_corrected", "6", payload={"record_fix": {
        "target_event_key": viability, "text_before": "1번 샘플 생존율 72%", "text_after": "1번 샘플 생존율 82%",
        "source": "voice"}})
    record.event("03:53", "photo_attached", "6", user_wording="570 nm 판독 화면")
    record.event("03:55", "step_completed", "6")
    record.event("03:55", "workflow_completed", "6")
    record.event("03:55", "result_recorded", "6", user_wording=CELL_RESULT,
                 payload={"result": "처리군 생존율이 대조군보다 낮았어.",
                          "next_change": "5 µM 농도를 2.5로 낮춰 볼게"})
    record.finalize("03:55", "completed")
    record.event("03:56", er.REVIEW_DEFERRED, None, payload={})
    record.confirm("03:56", f"r.{seeded}", "well 당 5000 cells 심었어")
    record.confirm("03:56", f"r.{deviation}", "항온조가 안 내려가서 40도에서 넣었어")
    record.confirm("03:57", f"t.{early}", "원문 시간 3시간 중 2시간 36분에 단계를 끝냄")
    record.confirm("03:57", f"r.{viability}", "1번 샘플 생존율 82%")
    record.confirm("03:57", f"r.{density}", "3번 샘플에서 세포 밀도가 예상보다 낮게 관찰됨")
    return record


def earlier_cell_run(directory: str | Path, store: er.ExperimentReportStore) -> RecordWriter:
    """The run of the same protocol the day before: everything as the source
    says, other observations, a result in other words."""

    record = RecordWriter(directory, session="session-rp3-cell-before",
                          title="Cell viability assay after drug treatment",
                          protocol_id="protocol-rp3-cell", day="2026-10-19", store=store)
    _start(record, "이실험", notices=2)
    record.event("00:30", "session_started", "1")
    record.event("00:30", "purpose_recorded", "1",
                 user_wording="A549 세포에 약물 X 5 µM 처리 뒤 생존율, 시료 2개에 대조군 1개",
                 payload={"samples": [{"name": "1번 샘플", "condition": "약물 X 5 µM", "control": False},
                                      {"name": "2번 샘플", "condition": "약물 X 5 µM", "control": False},
                                      {"name": "대조군", "condition": "", "control": True}]})
    record.event("00:40", "observation", "1", user_wording="well 당 5000 cells 심었어", category="measurement",
                 payload={"experimenter_confirmed": True})
    record.event("00:42", "step_completed", "1")
    record.event("00:43", "step_completed", "2")
    record.event("00:52", "step_completed", "3")
    record.event("00:53", "step_completed", "4")
    record.event("00:54", "timer_started", "5", payload={"timer": {"duration_seconds": 10800}})
    record.event("03:55", "step_completed", "5")
    record.event("04:10", "observation", "6", user_wording="1번 샘플 생존율 65%", category="measurement",
                 payload={"experimenter_confirmed": True})
    record.event("04:12", "step_completed", "6")
    record.event("04:12", "workflow_completed", "6")
    record.event("04:12", "result_recorded", "6", user_wording="생존율이 65%로 예상 범위였어",
                 payload={"result": "생존율이 65%로 예상 범위였어", "next_change": ""})
    record.finalize("04:12", "completed")
    return record


COATING_PURPOSE = "졸겔 코팅 두께가 인출 속도에 따라 어떻게 달라지는지 보려고요. 시편 S1은 2 mm/s, S2는 4 mm/s"
COATING_SAMPLES = (
    {"name": "S1", "condition": "2 mm/s", "control": False},
    {"name": "S2", "condition": "4 mm/s", "control": False},
)


def coating_run(directory: str | Path, *, store: er.ExperimentReportStore | None = None) -> RecordWriter:
    """The coating run: two specimens, a measurement each, a timer run to its
    end, an anomaly, the run stopped before the last step, nothing yet
    confirmed, and the end questions skipped (no result said)."""

    record = RecordWriter(directory, session="session-rp3-coating", title="Sol-gel silica coating on glass slides",
                          protocol_id="protocol-rp3-coating", store=store)
    _start(record, "박재료", notices=1)
    record.event("00:30", "session_started", "1")
    record.event("00:30", "purpose_recorded", "1", user_wording=COATING_PURPOSE,
                 payload={"samples": list(COATING_SAMPLES)})
    record.event("01:02", "step_completed", "1")
    record.event("02:05", "step_completed", "2")
    record.event("02:10", "observation", "3", user_wording="S2는 4 mm/s로 꺼냈어", category="measurement")
    record.event("02:12", "step_completed", "3")
    record.event("02:13", "timer_started", "4", payload={"timer": {"duration_seconds": 600}})
    record.event("02:22", "timer_last_minute_notice", "4", payload={"spoken": True})
    record.event("02:23", "timer_end_notice", "4", payload={"spoken": False})
    record.event("02:24", "observation", "4", user_wording="S1 막은 맑은데 S2는 가장자리가 뿌옇게 보여",
                 category="appearance")
    record.event("02:25", "step_completed", "4")
    record.event("02:30", "anomaly", "5", user_wording="로 온도가 380도에서 안 올라가",
                 category="equipment_fault", severity="unknown")
    record.event("02:35", "session_stopped", "5", payload={"stop_reason": "stopped_by_user"})
    record.finalize("02:35", "stopped")
    record.event("02:36", er.REVIEW_DEFERRED, None, payload={})
    return record


#: The three 10/06 flows the person judged (lane RP2's docx samples) as fake
#: records on the lane RP2 double -- the in-gel ones (A, D, E) are rebuilt
#: from the original scenario script by the sample tool, so only the
#: headspace flow (H) needs a double here.
def headspace_run(directory: str | Path) -> RecordWriter:
    """Lane RP2's H flow: one observation at step 1, steps 1-4 done, a stop at 5."""

    record = RecordWriter(directory, session="session-rp3-headspace",
                          title="Using dynamic headspace collections for bacterial volatile sampling",
                          protocol_id="protocol-rp3-headspace")
    _start(record, "Researcher A", notices=0)
    record.event("00:30", "session_started", "1")
    record.event("00:42", "observation", "1", user_wording="LB 배지가 맑고 덩어리 없이 다 녹았어",
                 category="appearance")
    record.event("00:43", "step_completed", "1")
    record.event("00:53", "step_completed", "2")
    record.event("01:07", "step_completed", "3")
    record.event("01:12", "step_completed", "4")
    record.event("01:14", "session_stopped", "5", payload={"stop_reason": "stopped_by_user"})
    record.finalize("01:14", "stopped")
    return record


def headspace_protocol() -> SimpleNamespace:
    """The first steps of the headspace protocol as a double (no catalog here)."""

    return _double(
        "Using dynamic headspace collections for bacterial volatile sampling",
        (
            ("Materials to autoclave before experiment", (
                ("1", "Prepare 400 mL of LB broth (25 g per litre).", 2, ()),
                ("2", "Prepare 500 mL of Ringer's solution, by placing one tablet of Ringer's solution into 500 mL distilled water.", 2, ()),
                ("3", "Prepare 400 mL of LB agar (37 g per litre).", 2, ()),
                ("4", "100 µL Pipette tips.", 2, ()),
            )),
            ("Preparing overnight cultures of bacteria", (
                ("5", "Inoculate a single colony into 10 mL LB broth.", 3, ()),
                ("6", "Incubate overnight at 30°C with shaking.", 3, ()),
            )),
        ),
        {},
        ("Lysogeny Broth (LB) (Sigma-Aldrich)", "Lysogeny Agar (Sigma-Aldrich)", "Ringer's Solution (Sigma-Aldrich)"),
        ("shaking incubator", "spectrophotometer"),
        {},
        "Novakova 2024. Using dynamic headspace collections for bacterial volatile sampling. protocols.io",
        "https://dx.doi.org/10.17504/protocols.io.kqdg3q4eev25/v1",
    )


def environment() -> mock._patch:
    return mock.patch.dict(os.environ, {"VOINEY_LAB_REPORT_TIMEZONE": "Asia/Seoul"})


def temporary() -> tempfile.TemporaryDirectory:
    return tempfile.TemporaryDirectory()
