"""Lane VX, decision 4 (2026-10-09): a minimum, an approximate and a maximum time.

Lane PT made no timer for "a minimum of 2 hours" (a bound, not a length) and
read "~16 hours" as a plain 16 hours. Now, with the source's value only:

* a minimum ("a minimum of 2 hours", "at least 30 min", "최소 2시간") runs a
  timer of that value named "최소 2시간"; when it has run out the server says
  "최소 시간 2시간이 지났어요." -- never that the step is over;
* an approximate value ("~16 hours", "about 16 h", "약 16시간") runs 16 hours,
  said "약 16시간";
* a maximum ("up to 2 h", "no more than 2 h", "최대 2시간") runs a timer that,
  run out, is said "최대 시간 2시간이 됐어요.";
* the timer list on the start screen shows which is which.

A strict comparison ("more than 5 min", "less than 5 min") is still no timer:
the source states no value to run. Everything runs on a fictional protocol
built from text; no licensed PDF.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.lane_cb_support import Turns
from tests.test_protocol_catalog import write_text_pdf
from voiney_lab import experiment_protocol as domain
from voiney_lab.experiment_protocol_analysis import verify_step_timers
from voiney_lab.experiment_protocol_config import ProtocolPersistenceSettings
from voiney_lab.experiment_protocol_pdf import extract_protocol_pdf
from voiney_lab.experiment_protocol_store import initialize_protocol_store
from voiney_lab.protocol_catalog import ProtocolCatalog

TITLE = "Protocol Bounds"
STEPS: tuple[tuple[str, str | None], ...] = (
    # (step instruction as printed, the duration excerpt the analysis extracted)
    ("1. Heat the block at 95 C for a minimum of 2 hours.", "a minimum of 2 hours"),
    ("2. Grow the culture for ~16 hours at 37 C.", "~16 hours"),
    ("3. Stain the gel for up to 2 h on the rocker.", "up to 2 h"),
    ("4. Leave the plate for at least 30 min at room temperature.", None),
    ("5. Wash the pellet for 10 min.", "10 min"),
    ("6. Spin the tube for more than 5 min.", "more than 5 min"),
    ("7. Record the result.", None),
)
PAGE = "\n".join((TITLE, "Section bounds", *(text for text, _ in STEPS), "Wear gloves."))


def _evidence(excerpt: str) -> domain.SourceEvidence:
    return domain.SourceEvidence(1, excerpt)


def bounds_protocol(protocol_id: str, extraction) -> domain.ExperimentProtocol:
    steps = []
    for number, (text, duration) in enumerate(STEPS, 1):
        instruction = text.split(". ", 1)[1]
        action = domain.ProtocolSubAction(
            f"a{number}", instruction, _evidence(instruction),
            estimated_duration=domain.EstimatedDuration(duration) if duration else None,
        )
        steps.append(domain.ProtocolSourceStep(
            f"step-{number}", str(number), text, _evidence(text), sub_actions=(action,),
        ))
    protocol = domain.ExperimentProtocol(
        protocol_id,
        domain.ProtocolMetadata(extraction, TITLE, "en", evidence=_evidence(TITLE)),
        sections=(domain.ProtocolSection(
            "bounds", "Section bounds", _evidence("Section bounds"), tuple(steps),
        ),),
    )
    domain.validate_protocol(protocol)
    return protocol


class _Catalog(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.store = initialize_protocol_store(ProtocolPersistenceSettings(True, root / "catalog"))
        self.addCleanup(self.store.close)
        self.catalog = ProtocolCatalog(self.store)
        pdf = root / "bounds.pdf"
        write_text_pdf(pdf, PAGE, title=TITLE)
        self.extraction = extract_protocol_pdf(pdf)
        self.protocol_id = self.catalog.register(
            pdf, source_filename="bounds.pdf", media_type="application/pdf"
        ).entry.protocol_id
        self.protocol = bounds_protocol(self.protocol_id, self.extraction)
        self.store.append_analysis_revision(
            self.protocol_id, 1, "analysis-1", self.protocol,
            domain.assess_readiness(self.protocol), domain.P1_CAPABILITY_POLICY.profile_id,
        )
        self.fixture = self.catalog.load_executable_fixture(self.protocol_id)
        self.turns = Turns()


class ReadingTests(unittest.TestCase):

    def read(self, text: str) -> list[tuple[str, tuple[int, ...], str]]:
        return [(d.literal, d.seconds, d.bound) for d in domain.read_source_durations(text).durations]

    def test_a_minimum_is_a_timer_of_its_value(self) -> None:
        for text, literal, seconds in (
            ("for a minimum of 2 hours", "a minimum of 2 hours", 7200),
            ("Incubate for at least 30 min.", "at least 30 min", 1800),
            ("최소 2시간 둡니다", "최소 2시간", 7200),
            ("2시간 이상 둡니다", "2시간 이상", 7200),
            ("no less than 15 min", "no less than 15 min", 900),
            ("minimum 1 h", "minimum 1 h", 3600),
        ):
            with self.subTest(text=text):
                self.assertEqual(self.read(text), [(literal, (seconds,), "minimum")])

    def test_an_approximate_value_is_its_number(self) -> None:
        for text, literal, seconds in (
            ("after mixing, grow for ~16 hours", "~16 hours", 57600),
            ("about 16 h at 37 C", "about 16 h", 57600),
            ("약 16시간 배양", "약 16시간", 57600),
            ("approximately 15 min", "approximately 15 min", 900),
            ("16시간 정도 둡니다", "16시간 정도", 57600),
            ("around 10 min", "around 10 min", 600),
        ):
            with self.subTest(text=text):
                self.assertEqual(self.read(text), [(literal, (seconds,), "approximate")])

    def test_a_maximum_is_a_timer_of_its_value(self) -> None:
        for text, literal, seconds in (
            ("for up to 2 h", "up to 2 h", 7200),
            ("no more than 2 h", "no more than 2 h", 7200),
            ("최대 2시간", "최대 2시간", 7200),
            ("30분 이내로", "30분 이내", 1800),
            ("within 30 min of thawing", "within 30 min", 1800),
            ("maximum of 1 h", "maximum of 1 h", 3600),
        ):
            with self.subTest(text=text):
                self.assertEqual(self.read(text), [(literal, (seconds,), "maximum")])

    def test_an_exact_value_and_a_range_are_as_they_were(self) -> None:
        self.assertEqual(self.read("for 15 min"), [("15 min", (900,), "exact")])
        self.assertEqual(self.read("12~16시간"), [("12~16시간", (43200, 57600), "exact")])
        self.assertEqual(self.read("12-16 h"), [("12-16 h", (43200, 57600), "exact")])

    def test_what_is_still_no_timer(self) -> None:
        for text, reason in (
            ("for more than 5 min", "open_bound"),
            ("less than 5 min", "open_bound"),
            ("longer than 1 h", "open_bound"),
            ("at least 12-16 h", "open_bound"),
            ("after ~16 hours", "elapsed_reference"),
            ("at least 30 min after thawing", "open_bound"),
            ("at least 1 h or overnight", "with_unnumbered_alternative"),
            ("every ~10 min", "interval"),
        ):
            with self.subTest(text=text):
                reading = domain.read_source_durations(text)
                self.assertEqual(reading.durations, ())
                self.assertEqual({r.reason for r in reading.refused}, {reason})


class TheTableTests(_Catalog):

    def test_verified_with_their_kind(self) -> None:
        table = verify_step_timers(self.protocol, self.extraction)
        self.assertEqual(
            [(t.step_id, t.literal, t.seconds, t.bound) for t in table.verified],
            [
                ("step-1", "a minimum of 2 hours", (7200,), "minimum"),
                ("step-2", "~16 hours", (57600,), "approximate"),
                ("step-3", "up to 2 h", (7200,), "maximum"),
                ("step-4", "at least 30 min", (1800,), "minimum"),
                ("step-5", "10 min", (600,), "exact"),
            ],
        )
        self.assertEqual(
            {(r.step_id, r.reason) for r in table.refused}, {("step-6", "open_bound")})

    def test_the_executable_runs_them(self) -> None:
        self.assertEqual(
            self.fixture.timer_manifest,
            {"step-1": 7200, "step-2": 57600, "step-3": 7200, "step-4": 1800, "step-5": 600},
        )

    def test_the_start_screen_list_says_which_is_which(self) -> None:
        verified = self.catalog.review(self.protocol_id)["timers"]["verified"]
        self.assertEqual(
            [(t["step_label"], t["value_ko"], t["bound"], t["bound_ko"]) for t in verified],
            [
                ("1", "2시간", "minimum", "최소"),
                ("2", "16시간", "approximate", "약"),
                ("3", "2시간", "maximum", "최대"),
                ("4", "30분", "minimum", "최소"),
                ("5", "10분", "exact", ""),
            ],
        )


class TheSpokenTimerTests(_Catalog):

    def test_a_minimum_timer_is_named_and_its_end_is_not_called_done(self) -> None:
        session = self.turns.open(0, fixture=self.fixture)
        plan = self.turns.say("타이머 시작해줘")
        self.assertEqual(
            plan.speech_text,
            "원문 ‘a minimum of 2 hours’에 따라 최소 2시간 타이머를 시작했습니다. "
            "화면에서 남은 시간을 확인할 수 있습니다.")
        status = session.timer_status()
        self.assertEqual((status["state"], status["duration_seconds"]), ("running", 7200))
        self.assertEqual(status["name"], "최소 2시간")
        self.assertEqual(status["bound"], "minimum")
        session._timer_started_at -= 7201
        for said in ("타이머 얼마 남았어?", "몇 분 지났어?"):
            with self.subTest(said=said):
                plan = self.turns.say(said)
                self.assertEqual(plan.speech_text, "최소 시간 2시간이 지났어요.")
                self.assertNotIn("끝났", plan.speech_text)
                self.assertNotIn("완료", plan.speech_text)

    def test_an_approximate_timer_is_said_about(self) -> None:
        session = self.turns.open(1, fixture=self.fixture)
        plan = self.turns.say("타이머 시작해줘")
        self.assertEqual(
            plan.speech_text,
            "원문 ‘~16 hours’에 따라 약 16시간 타이머를 시작했습니다. "
            "화면에서 남은 시간을 확인할 수 있습니다.")
        self.assertEqual(session.timer_status()["duration_seconds"], 57600)
        self.assertEqual(session.timer_status()["name"], "약 16시간")

    def test_a_maximum_timer_says_the_maximum_is_reached(self) -> None:
        session = self.turns.open(2, fixture=self.fixture)
        plan = self.turns.say("타이머 시작해줘")
        self.assertIn("최대 2시간 타이머를 시작했습니다", plan.speech_text)
        session._timer_started_at -= 7201
        plan = self.turns.say("타이머 얼마 남았어?")
        self.assertEqual(plan.speech_text, "최대 시간 2시간이 됐어요.")

    def test_an_exact_timer_is_said_as_before(self) -> None:
        session = self.turns.open(4, fixture=self.fixture)
        plan = self.turns.say("타이머 시작해줘")
        self.assertEqual(
            plan.speech_text,
            "원문 ‘10 min’에 따라 10분 타이머를 시작했습니다. 화면에서 남은 시간을 확인할 수 있습니다.")
        self.assertEqual(session.timer_status()["name"], "10분")
        session._timer_started_at -= 601
        plan = self.turns.say("타이머 얼마 남았어?")
        self.assertEqual(
            plan.speech_text, "현재 5단계 타이머가 이미 완료되었습니다. 다음 작업으로 진행할 수 있습니다.")

    def test_how_long_says_the_kind(self) -> None:
        self.turns.open(0, fixture=self.fixture)
        plan = self.turns.say("몇 시간 가열해?")
        self.assertIn("1단계 원문에는 ‘a minimum of 2 hours’(최소 2시간)으로 적혀 있어요.", plan.speech_text)
        self.turns.open(1, fixture=self.fixture)
        plan = self.turns.say("몇 시간 배양해?")
        self.assertIn("‘~16 hours’(약 16시간)으로 적혀 있어요.", plan.speech_text)

    def test_a_strict_comparison_still_runs_nothing(self) -> None:
        session = self.turns.open(5, fixture=self.fixture)
        plan = self.turns.say("타이머 시작해줘")
        self.assertEqual(session.timer_status()["state"], "not_started")
        self.assertFalse(plan.state_changed)
        self.assertIn("'…보다 길게', '…보다 짧게' 같은 비교로 적혀 있어요", plan.speech_text)

    def test_moving_on_asks_while_the_minimum_has_not_passed(self) -> None:
        session = self.turns.open(0, fixture=self.fixture)
        self.turns.say("타이머 시작해줘")
        plan = self.turns.say("다음")
        self.assertFalse(plan.state_changed)
        self.assertEqual(plan.display_text, "1단계 완료하셨나요?")
        self.turns.say("아니")
        session._timer_started_at -= 7201
        plan = self.turns.say("다음")
        self.assertTrue(plan.state_changed)


class TheStartScreenRendersBoundsTests(unittest.TestCase):

    def test_the_start_summary_shows_the_kind_before_the_value(self) -> None:
        from tests.test_screen_cleanup import run_page_script

        result = run_page_script(r"""
const review={protocol_id:"p",title:"Protocol Bounds",available_for_execution:true,analysis_available:true,step_count:7,
 execution_blockers:[],execution_notices:[],safety_notices:[],
 timers:{verified:[
  {step_label:"1",value_ko:"2시간",bound:"minimum",bound_ko:"최소",choice:false,source_literal:"a minimum of 2 hours",source_page_number:1,source_excerpt:"a minimum of 2 hours"},
  {step_label:"2",value_ko:"16시간",bound:"approximate",bound_ko:"약",choice:false,source_literal:"~16 hours",source_page_number:1,source_excerpt:"~16 hours"},
  {step_label:"3",value_ko:"2시간",bound:"maximum",bound_ko:"최대",choice:false,source_literal:"up to 2 h",source_page_number:1,source_excerpt:"up to 2 h"},
  {step_label:"5",value_ko:"10분",bound:"exact",bound_ko:"",choice:false,source_literal:"10 min",source_page_number:1,source_excerpt:"10 min"}],
  refused:[]}};
renderStartSummary(review);
const text=visibleText(node("protocol-step-timers"));
for(const want of ["1단계 · 최소 2시간 · 원문 ‘a minimum of 2 hours’","2단계 · 약 16시간 · 원문 ‘~16 hours’","3단계 · 최대 2시간 · 원문 ‘up to 2 h’","5단계 · 10분 · 원문 ‘10 min’"])
 assert(text.includes(want),`missing: ${want} in ${text}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
