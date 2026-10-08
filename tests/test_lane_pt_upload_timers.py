"""Timers for an uploaded protocol, read from its own source (lane PT).

Human decisions of 2026-10-08:

1. A step's timer is a duration the analysis extracted *and the server
   verified*: the excerpt lies within that step's own source, on its pages,
   and the number and unit are read in the excerpt itself ("15 min",
   "00:15:00", "3 h", "30 s", "15분", "1시간 30분"). Those values become the
   executable's timer table -- the same step_id -> seconds shape the in-gel
   sidecar manifest loads into -- and ``timer_seconds_for_step`` reads it.
2. A range ("12-16 h") is asked when the timer starts: "12시간과 16시간 중 몇
   시간으로 맞출까요?", the source's values only. "overnight", "until ..."
   make no timer. Two durations in one step are chosen from in step order.
3. "몇 분 반응시켜?", "이 단계 몇 분이야?" are answered from the verified source
   time (a front rule); "몇 분 지났어?" and "얼마나 남았어?" from the running
   timer.
4. The start screen lists each step's timer (value, excerpt) and each time
   the server could not verify (reason).

The in-gel development fixture keeps its sidecar manifest and behaves as
before. Everything here runs on a fictional protocol built from text; no
licensed PDF.
"""

from __future__ import annotations

import json
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

TITLE = "Protocol Timer"
STEPS: tuple[tuple[str, str | None], ...] = (
    # (step instruction as printed, the duration excerpt the analysis extracted)
    ("1. Incubate the plate for 15 min at 37 C.", "15 min"),
    ("2. Incubate the tube overnight at 4 C.", "overnight"),
    ("3. Grow the culture for 12-16 h.", "12-16 h"),
    ("4. Incubate for 30 min, wash, then incubate for 1 h.", None),
    ("5. Heat the block at 95 C for a minimum of 2 hours.", "a minimum of 2 hours"),
    ("6. Shake the plate for 00:05:00 on the shaker.", "00:05:00"),
    ("7. Record the result.", None),
)
PAGE = "\n".join((TITLE, "Section culture", *(text for text, _ in STEPS), "Wear gloves."))


def _evidence(excerpt: str) -> domain.SourceEvidence:
    return domain.SourceEvidence(1, excerpt)


def _action(
    action_id: str, text: str, duration: str | None, parsed: int | None = None,
) -> domain.ProtocolSubAction:
    return domain.ProtocolSubAction(
        action_id,
        text,
        _evidence(text),
        estimated_duration=(
            domain.EstimatedDuration(duration, parsed) if duration else None
        ),
    )


def timer_protocol(
    protocol_id: str,
    extraction,
    *,
    step3_parsed: int | None = None,
    ambiguity_at: str | None = None,
) -> domain.ExperimentProtocol:
    steps = []
    for number, (text, duration) in enumerate(STEPS, 1):
        step_id = f"step-{number}"
        instruction = text.split(". ", 1)[1]
        if number == 4:
            actions = (
                _action("a4-1", "Incubate for 30 min", "30 min"),
                _action("a4-2", "then incubate for 1 h", "1 h"),
            )
        else:
            actions = (
                _action(
                    f"a{number}", instruction, duration,
                    step3_parsed if number == 3 else None,
                ),
            )
        steps.append(domain.ProtocolSourceStep(
            step_id, str(number), text, _evidence(text), sub_actions=actions,
            warnings=(
                (domain.SourceStatement("glove", "Wear gloves.", _evidence("Wear gloves.")),)
                if number == 1 else ()
            ),
        ))
    constructs = ()
    if ambiguity_at is not None:
        constructs = (domain.SourceAmbiguity(
            "ambiguous-time", "15 min", _evidence("15 min"), step_id=ambiguity_at,
        ),)
    protocol = domain.ExperimentProtocol(
        protocol_id,
        domain.ProtocolMetadata(extraction, TITLE, "en", evidence=_evidence(TITLE)),
        sections=(domain.ProtocolSection(
            "culture", "Section culture", _evidence("Section culture"), tuple(steps),
        ),),
        constructs=constructs,
    )
    domain.validate_protocol(protocol)
    return protocol


class _Catalog(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.store = initialize_protocol_store(
            ProtocolPersistenceSettings(True, root / "catalog")
        )
        self.addCleanup(self.store.close)
        self.catalog = ProtocolCatalog(self.store)
        self.pdf = root / "timer.pdf"
        write_text_pdf(self.pdf, PAGE, title=TITLE)
        self.extraction = extract_protocol_pdf(self.pdf)
        self.protocol_id = self.catalog.register(
            self.pdf, source_filename="timer.pdf", media_type="application/pdf"
        ).entry.protocol_id

    def store_analysis(self, **kwargs) -> domain.ExperimentProtocol:
        protocol = timer_protocol(self.protocol_id, self.extraction, **kwargs)
        self.store.append_analysis_revision(
            self.protocol_id, 1, "analysis-1", protocol,
            domain.assess_readiness(protocol), domain.P1_CAPABILITY_POLICY.profile_id,
        )
        return protocol


class TheUploadedExecutableHasSourceTimersTests(_Catalog):
    """Decision 1: the reproduction -- before this lane every step said no timer."""

    def setUp(self) -> None:
        super().setUp()
        self.store_analysis()
        self.fixture = self.catalog.load_executable_fixture(self.protocol_id)

    def test_the_timer_table_has_the_sidecar_shape(self) -> None:
        self.assertEqual(self.fixture.timer_manifest, {"step-1": 900, "step-6": 300})
        self.assertIsNotNone(self.fixture.timer_table)

    def test_timer_seconds_for_step_reads_it(self) -> None:
        turns = Turns()
        session = turns.open(0, fixture=self.fixture)
        self.assertEqual(session.timer_seconds_for_step(0), 900)
        self.assertEqual(session.timer_seconds_for_step(1), 0)
        self.assertEqual(session.timer_seconds_for_step(5), 300)

    def test_start_timer_on_the_server_path_runs_the_source_value(self) -> None:
        turns = Turns()
        session = turns.open(0, fixture=self.fixture)
        plan = turns.say("타이머 시작해줘")
        self.assertIs(plan.action, domain_action("START_TIMER"))
        self.assertIn("15분 타이머를 시작했습니다", plan.speech_text)
        self.assertIn("15 min", plan.speech_text)
        status = session.timer_status()
        self.assertEqual((status["state"], status["duration_seconds"]), ("running", 900))

    def test_a_step_whose_source_states_no_number_has_no_timer_and_is_read(self) -> None:
        turns = Turns()
        session = turns.open(1, fixture=self.fixture)
        plan = turns.say("타이머 시작해줘")
        self.assertEqual(session.timer_status()["state"], "not_started")
        self.assertIn("원문에서 확인한 타이머가 없습니다", plan.speech_text)
        self.assertIn("Incubate the tube overnight at 4 C", plan.speech_text)
        # Nothing started, so nothing is said to have changed: the durable
        # session would refuse it and roll the reply back to a save failure.
        self.assertFalse(plan.state_changed)

    def test_a_bound_is_not_a_length(self) -> None:
        turns = Turns()
        session = turns.open(4, fixture=self.fixture)
        turns.say("타이머 시작해줘")
        self.assertEqual(session.timer_status()["state"], "not_started")


class TheServerVerificationTests(_Catalog):
    """Decision 1: what makes a verified timer, and every reason one is refused."""

    def table(self, **kwargs) -> domain.StepTimerTable:
        return verify_step_timers(
            timer_protocol(self.protocol_id, self.extraction, **kwargs), self.extraction
        )

    def test_verified_and_refused(self) -> None:
        table = self.table()
        self.assertEqual(
            [(t.step_id, t.literal, t.seconds) for t in table.verified],
            [
                ("step-1", "15 min", (900,)),
                ("step-3", "12-16 h", (43200, 57600)),
                ("step-4", "30 min", (1800,)),
                ("step-4", "1 h", (3600,)),
                ("step-6", "00:05:00", (300,)),
            ],
        )
        self.assertEqual(
            {(r.step_id, r.literal, r.reason) for r in table.refused},
            {
                ("step-2", "overnight", "no_number"),
                ("step-5", "2 hours", "open_bound"),
            },
        )
        self.assertEqual(
            {k: [c.seconds for c in v] for k, v in table.choices().items()},
            {"step-3": [43200, 57600], "step-4": [1800, 3600]},
        )

    def test_an_excerpt_outside_the_steps_own_source_is_refused(self) -> None:
        protocol = timer_protocol(self.protocol_id, self.extraction)
        step = protocol.sections[0].steps[6]
        moved = domain.ProtocolSubAction(
            "a7", "Record the result.", _evidence("Record the result."),
            estimated_duration=domain.EstimatedDuration("15 min"),
        )
        protocol = _with_step(protocol, 6, _replace(step, sub_actions=(moved,)))
        table = verify_step_timers(protocol, self.extraction)
        self.assertIn(
            ("step-7", "not_in_step_text"),
            {(r.step_id, r.reason) for r in table.refused},
        )
        self.assertNotIn("step-7", table.manifest())

    def test_seconds_the_analysis_wrote_must_be_a_source_value(self) -> None:
        table = self.table(step3_parsed=50400)  # 14 h: not printed
        self.assertNotIn("step-3", table.choices())
        self.assertIn(
            ("step-3", "analysis_value_mismatch"),
            {(r.step_id, r.reason) for r in table.refused},
        )
        self.assertIn("step-3", self.table(step3_parsed=43200).choices())

    def test_a_step_the_analysis_marked_ambiguous_keeps_no_timer(self) -> None:
        table = self.table(ambiguity_at="step-1")
        self.assertNotIn("step-1", table.manifest())
        self.assertIn(
            ("step-1", "step_time_ambiguity"),
            {(r.step_id, r.reason) for r in table.refused},
        )

    def test_the_forms_the_decision_names(self) -> None:
        for text, seconds in (
            ("15 min", (900,)), ("00:15:00", (900,)), ("3 h", (10800,)),
            ("30 s", (30,)), ("15분", (900,)), ("1시간 30분", (5400,)),
            ("12–16 h", (43200, 57600)), ("12~16시간", (43200, 57600)),
            ("15 or 30 min", (900, 1800)), ("10 minutes", (600,)),
        ):
            with self.subTest(text=text):
                reading = domain.read_source_durations(text)
                self.assertEqual([d.seconds for d in reading.durations], [seconds])
        for text, reason in (
            ("Incubate overnight", "no_number"),
            ("stir until dissolved", "no_number"),
            ("1 h at RT or overnight at 4 C", "with_unnumbered_alternative"),
            ("for at least 30 min", "open_bound"),
            ("30분 이상", "open_bound"),
            ("every 10 min", "interval"),
            ("After 2 hours, remove", "elapsed_reference"),
            ("3000 - 5 min", "unclear_range"),
        ):
            with self.subTest(text=text):
                reading = domain.read_source_durations(text)
                self.assertEqual(reading.durations, ())
                self.assertEqual({r.reason for r in reading.refused}, {reason})
        self.assertEqual(domain.read_source_durations("5분의 1, 10 mL").durations, ())


class TheChoiceTests(_Catalog):
    """Decision 2: a range or several durations are asked, source values only."""

    def setUp(self) -> None:
        super().setUp()
        self.store_analysis()
        self.fixture = self.catalog.load_executable_fixture(self.protocol_id)
        self.turns = Turns()

    def test_a_range_is_asked_and_the_chosen_end_runs(self) -> None:
        session = self.turns.open(2, fixture=self.fixture)
        plan = self.turns.say("타이머 시작해줘")
        self.assertEqual(plan.intent_kind, "timer_choice_required")
        self.assertEqual(
            plan.speech_text,
            "원문에는 ‘12-16 h’로 적혀 있어요. 12시간과 16시간 중 몇 시간으로 맞출까요?",
        )
        self.assertEqual(session.timer_status()["state"], "not_started")
        plan = self.turns.say("16시간으로 해줘")
        self.assertIn("16시간 타이머를 시작했습니다", plan.speech_text)
        status = session.timer_status()
        self.assertEqual((status["state"], status["duration_seconds"]), ("running", 57600))

    def test_a_value_the_source_does_not_print_is_not_run(self) -> None:
        session = self.turns.open(2, fixture=self.fixture)
        self.turns.say("타이머 시작해줘")
        plan = self.turns.say("14시간")
        self.assertEqual(session.timer_status()["state"], "not_started")
        self.assertTrue(plan.speech_text.startswith("원문에 적힌 값으로만 맞출 수 있어요."))
        plan = self.turns.say("첫 번째")
        self.assertEqual(session.timer_status()["duration_seconds"], 43200)

    def test_two_durations_of_one_step_are_chosen_in_step_order(self) -> None:
        session = self.turns.open(3, fixture=self.fixture)
        plan = self.turns.say("타이머 시작해줘")
        self.assertEqual(
            plan.speech_text,
            "이 단계 원문에는 시간이 여럿 적혀 있어요(첫째 ‘30 min’, 둘째 ‘1 h’). "
            "30분과 1시간 중 어느 것으로 맞출까요?",
        )
        self.turns.say("두 번째")
        self.assertEqual(session.timer_status()["duration_seconds"], 3600)

    def test_no_to_the_choice_starts_nothing(self) -> None:
        session = self.turns.open(2, fixture=self.fixture)
        self.turns.say("타이머 시작해줘")
        plan = self.turns.say("아니")
        self.assertIn("타이머를 시작하지 않았습니다", plan.speech_text)
        self.assertEqual(session.timer_status()["state"], "not_started")

    def test_a_chosen_value_must_be_offered(self) -> None:
        session = self.turns.open(2, fixture=self.fixture)
        ok, _, _ = session.start_timer(duration_seconds=50400)
        self.assertFalse(ok)
        ok, duration, _ = session.start_timer(duration_seconds=43200)
        self.assertEqual((ok, duration), (True, 43200))


class TheRouterLineTests(_Catalog):
    """With the router on, a start_timer proposal at a choice step is asked too."""

    def test_a_proposed_timer_at_a_range_step_asks_which_value(self) -> None:
        from voiney_lab.llm_router import ToolProposal

        self.store_analysis()
        fixture = self.catalog.load_executable_fixture(self.protocol_id)
        turns = Turns()
        session = turns.open(2, fixture=fixture)
        said = "타이머 맞춰 줘"
        self.assertIsNone(session.front_plan(
            said, turn_id=50, language="ko", configuration_id=1, generation=1,
        ))
        applied = session.apply_tool_proposal(
            [ToolProposal(tool="change_state", action="start_timer", evidence=said)],
            transcript=said, basis=session.proposal_basis(turn_id=50, generation=1),
            turn_id=50, language="ko", configuration_id=1, generation=1,
        )
        self.assertEqual(applied.plan.intent_kind, "timer_choice_required")
        self.assertEqual(session.timer_status()["state"], "not_started")
        plan = session.front_plan(
            "12시간", turn_id=51, language="ko", configuration_id=1, generation=1,
        )
        self.assertIsNotNone(plan)
        self.assertEqual(session.timer_status()["duration_seconds"], 43200)


class TheServedPathTests(_Catalog):
    """server.run_turn with an uploaded protocol's executable: the served timer."""

    def test_start_timer_on_the_served_path_runs_the_source_value_and_is_reported(self) -> None:
        import os
        from unittest.mock import patch

        from tests.test_candidate_a_websocket_integration import (
            CandidateAWebSocketIntegrationTests,
            _ScriptedSocket,
        )
        from voiney_lab.curated_protocol import CuratedProtocolSession
        from voiney_lab.experiment_reports import ExperimentReportStore
        from voiney_lab.server import ListenerSession

        self.store_analysis()
        fixture = self.catalog.load_executable_fixture(self.protocol_id)
        run = CandidateAWebSocketIntegrationTests._run_curated_turn
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"VOINEY_LAB_WORKSPACE_ENABLED": "false"}
        ), patch("voiney_lab.server._prepare_report_prose", return_value="skipped"):
            curated = CuratedProtocolSession(fixture)
            listener = ListenerSession(curated_protocol_session=curated)
            listener.start()
            curated.activate_configured()
            listener.accept_configuration(
                1, "cascade", "ko", fixture.protocol_id, fixture.revision_id,
            )
            listener.experiment_report_store = ExperimentReportStore(Path(tmp) / "r.sqlite")
            socket = _ScriptedSocket([])
            said = (
                "프로토콜 시작해줘", "타이머 시작해줘",          # step 1: 15 min
                "1단계 완료했어", "타이머 시작해줘",             # step 2: overnight
                "2단계 완료했어", "타이머 시작해줘", "12시간",  # step 3: 12-16 h
            )
            states = []
            for turn, text in enumerate(said, 1):
                run(listener, socket, text, turn_id=turn)
                status = curated.timer_status()
                states.append((status.get("state"), status.get("duration_seconds")))
            self.assertEqual(states[1], ("running", 900))
            self.assertEqual(states[3][0], "not_started")
            self.assertEqual(states[5][0], "not_started")
            self.assertEqual(states[6], ("running", 43200))
            report = listener.experiment_report_store.list_reports()[0]
            events = listener.experiment_report_store.get_report(report["report_id"])["events"]
            started = [e for e in events if e["event_type"] == "timer_started"]
            self.assertEqual([e["step_label"] for e in started], ["1", "3"])
            sent = json.dumps(socket.sent, ensure_ascii=False)
            self.assertIn("원문 ‘15 min’에 따라 15분 타이머를 시작했습니다", sent)
            self.assertIn("12시간과 16시간 중 몇 시간으로 맞출까요?", sent)


class TheInGelSidecarIsUnchangedTests(unittest.TestCase):
    def test_the_sidecar_fixture_has_no_analysis_timer_table(self) -> None:
        from tests.lane_cb_support import headspace_fixture

        fixture = headspace_fixture()
        self.assertIsNone(fixture.timer_table)
        self.assertIsNone(fixture.timer_choices)
        turns = Turns()
        session = turns.open(0, fixture=fixture)
        self.assertFalse(session.analysis_timers)
        plan = turns.say("몇 분 동안 해?")
        self.assertNotEqual(plan.intent_kind, "step_duration_question")


def domain_action(name: str):
    from voiney_lab.curated_protocol import CuratedProtocolAction

    return getattr(CuratedProtocolAction, name)


def _replace(item, **changes):
    import dataclasses

    return dataclasses.replace(item, **changes)


def _with_step(protocol, index, step):
    section = protocol.sections[0]
    steps = list(section.steps)
    steps[index] = step
    return _replace(protocol, sections=(_replace(section, steps=tuple(steps)),))


if __name__ == "__main__":
    unittest.main()
