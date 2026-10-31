"""Lane SAFE2: the SIM's transcription-error axis (``--stt-error-matrix``).

The garbles are rules on each case's own words -- a number bent and its
unit as heard, the end cut, a word one letter off, particles and the polite
ending gone -- the shapes of the 2026-10-31 live rows, applied to every
public protocol alike. A variant keeps its family and its spoken case's
allowed state plus the confirmation a question opens; the runner checks any
value applied or record stored against the spoken words. Offline; no
provider is called.
"""

from __future__ import annotations

import asyncio
import json
from argparse import Namespace

from scripts.sim.corpus import Case, generate_cases, load_corpus
from scripts.sim.metrics import aggregate, score_case
from scripts.sim.run import prepare_cases
from scripts.sim.runner import run_case
from scripts.sim.stt_errors import (
    ERROR_KINDS, bent_number, case_texts, error_kind, korean_number, original_code, sound_alike, variants,
)

COMMON = dict(corpus=None, catalog_copy=None, translations=None, development_fixture=False,
              split_seed=1027, holdout_fraction=.3, emergency_matrix=False, protocol=None, family=None,
              limit=None, case_ids=None)


def test_each_garble_shape_is_a_rule_on_the_words_alone() -> None:
    assert sound_alike("타이머") == "다이머"
    assert sound_alike("재개") == "채개"
    assert sound_alike("다음") == "타음"
    assert sound_alike("pH") is None
    assert bent_number("37") == "73" and bent_number("100") == "10" and bent_number("2") == "20"
    assert bent_number("11.3") == "11.30" or bent_number("11.3") == "1.13" or bent_number("11.3").startswith("11")
    assert korean_number("100") == "일 영 영" and korean_number("1.5") == "일 점 오"
    case = Case("p.timer_resume.0", "timer_resume", "타이머 재개", 2, "timer_paused", ("timer_resume",), (), ("timer", "revision"))
    made = variants(case)
    kinds = {error_kind(v.code) for v in made}
    assert kinds == {"tail_cut", "command_sound"}, kinds
    assert {v.text for v in made} == {"타이머 재", "타이머", "다이머 재개", "타이머 채개"}
    for v in made:
        assert v.family == case.family and v.setup == case.setup and v.index == case.index
        assert "confirmation" in v.allowed and set(case.allowed) <= set(v.allowed)
        assert not v.must_change and v.intents == ()
        assert "clarify_completion" in v.actions and "timer_resume" in v.actions
        assert original_code(v.code) == case.code


def test_a_measurement_note_gets_its_number_unit_and_lead_word_garbled() -> None:
    case = Case("p.observation.1", "observation", "관찰 기록해 줘: 측정값 100 mL", 1, "active",
                ("clarify_completion",), ("note_confirmation_required",), ("confirmation",))
    texts = {error_kind(v.code): [] for v in variants(case)}
    for v in variants(case):
        texts[error_kind(v.code)].append(v.text)
    assert "관찰 기록해 줘: 측정값 10 mL" in texts["number_unit"]
    assert "관찰 기록해 줘: 측정값 100 미리" in texts["number_unit"]
    assert "관찰 기록해 줘: 측정값 일 영 영" in texts["number_unit"]
    assert "관찰 기록해 줘: 측정값 100" in texts["tail_cut"]
    assert "관찰 기록해 측정값 100 mL" in texts["particle_drop"]
    assert set(texts) <= set(ERROR_KINDS)


def test_a_garble_that_is_another_cases_words_is_not_a_garble() -> None:
    question = Case("p.step_question.2", "step_question", "다음 뭐야?", 0, "active", ("next_information",))
    move = Case("p.complete.2", "complete", "다음", 0, "active", ("next",), (), ("step",), 1, True)
    alone = {v.text for v in variants(question)}
    assert "다음" in alone
    with_corpus = {v.text for v in variants(question, other_texts=case_texts([question, move]))}
    assert "다음" not in with_corpus and "다음 뭐" in with_corpus


def test_noise_and_emergency_cases_take_no_garble() -> None:
    assert variants(Case("p.noise.0", "noise", "", 0)) == ()
    assert variants(Case("p.emergency.0", "emergency", "불이 났어", 0)) == ()


def test_the_axis_replaces_the_development_cases_with_their_garbles_and_names_each() -> None:
    args = Namespace(split="development", stt_error_matrix=True, **COMMON)
    from scripts.sim.corpus import CORPUS
    args.corpus = CORPUS
    cases, split, _catalog, _translations = prepare_cases(args)
    assert split["stt_error_variants"] == len(cases) > 1500
    assert split["stt_error_originals"] > 600
    assert all(error_kind(c.code) in ERROR_KINDS for _p, c in cases)
    assert set(args._spoken_by_code) == {c.code for _p, c in cases}
    assert all(args._spoken_by_code[c.code] != c.text for _p, c in cases)
    fields = {p.field for p, _c in cases}
    assert {"biology", "chemistry", "materials", "soil"} <= fields


def test_a_value_applied_from_the_garble_is_a_wrong_state_against_the_spoken_words() -> None:
    protocol = next(p for p in load_corpus() if p.code == "fda_eam_2_3_solution_prep")
    spoken = next(c for c in generate_cases(protocol) if c.family == "value_change")
    garbled = next(v for v in variants(spoken) if error_kind(v.code) == "number_unit")
    result = asyncio.run(run_case(protocol, garbled, spoken_text=spoken.text))
    row = score_case(garbled, result)
    # Either nothing was applied (a question, a refusal) or the bent value was: the latter is the error.
    if "values" in result["changed_fields"]:
        assert "value_or_step" in result["invalid_state"] and row["wrong_state"]
    else:
        assert not row["wrong_state"], row


def test_the_tables_have_the_error_kind_dimension() -> None:
    rows = [{"case": "p.x.0~tail_cut.0", "family": "complete", "understood": False, "wrong_state": False,
             "english_copy": False, "duplicate_label": False, "korean": True, "drops": [], "interrupted": False,
             "incomplete": False, "timings_ms": {}, "stt_error": "tail_cut"}]
    tables = aggregate(rows)["tables"]
    assert tables["stt_error"]["tail_cut"]["cases"] == 1


def test_the_run_refuses_to_mix_the_axis_with_other_axes(tmp_path, monkeypatch) -> None:
    from scripts.sim import run as sim_run
    monkeypatch.setattr(sim_run.Path, "home", classmethod(lambda cls: tmp_path))
    (tmp_path / "reports/sim").mkdir(parents=True)
    out = tmp_path / "reports/sim/x.json"
    assert sim_run.main(["--stt-error-matrix", "--impact-matrix", "--output", str(out)]) == 2
    assert not out.exists()
    doc = json.dumps({"schema": 1})
    assert doc
