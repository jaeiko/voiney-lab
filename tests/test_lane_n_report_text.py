"""The report's text: Korean steps and rounds (lane N, decisions 6 and 8).

6. A step's stored translation is read for the report when there is one;
   one that fails its check is not stored as usable, and the source is
   used. Lane RP3, decision ①: the notebook copies no step sentence, so the
   marked Korean cell is gone from the exports; decision ③: the model reads
   the notebook's facts, not the steps.
7. Lane N's labelled general-knowledge background is gone with lane RP3,
   decision ③: the notebook has no background section.
8. An AI-written sentence that gives a number of rounds or repetitions must
   say "말로 확인한 돌아가기 기준"; otherwise it is dropped (lane RP3,
   decision ③ keeps lane N's rule for the notes).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


from tests.lane_n_support import notes_fixture
from voiney_lab import experiment_reports as er
from voiney_lab.protocol_translation import translation_revision_key, translation_units
from voiney_lab.workspace_store import (
    FactTranslationRecord,
    WorkspaceSettings,
    initialize_workspace_store,
)

KOREAN_STEP_1 = "1 염색된 밴드를 조각으로 잘라 1.5 mL 튜브에 넣는다."


def _report(*events: dict, status: str = "stopped") -> dict:
    return {
        "protocol_id": "lane-n-wash", "protocol_revision": "lane-n-wash-v1",
        "protocol_title": "Fictional wash", "status": status,
        "started_at": "2026-10-07T00:00:00+00:00", "ended_at": "2026-10-07T01:00:00+00:00",
        "events": list(events),
    }


def _done(label: str, key: str, **payload) -> dict:
    return {"event_key": key, "event_type": "step_completed", "step_label": label,
            "step_id": f"step-{label}", "user_wording": None, "category": None,
            "payload": payload, "created_at": "2026-10-07T00:10:00+00:00"}


class KoreanStepTableTests(unittest.TestCase):
    """Decision 6: the stored translation is read for the report, never started by it."""

    def test_the_report_reads_stored_translations_without_starting_one(self) -> None:
        import voiney_lab.server as server

        fixture = notes_fixture()
        unit = next(u for u in translation_units(fixture) if u.fact_key == "step-1/current_step")
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            store = initialize_workspace_store(WorkspaceSettings(True, workspace))
            try:
                store.record_fact_translations([
                    FactTranslationRecord(
                        revision_id=translation_revision_key(fixture), fact_key=unit.fact_key,
                        language="ko", source_sha256=unit.source_sha256,
                        translated_text=KOREAN_STEP_1, status="machine", check_result="passed",
                        model="fake-translator", model_version="v1",
                        created_at="2026-10-07T00:00:00+00:00",
                    ),
                    FactTranslationRecord(
                        revision_id=translation_revision_key(fixture), fact_key="step-2/current_step",
                        language="ko", source_sha256="0" * 64,
                        translated_text="2 조각을 씻는다.", status="machine",
                        check_result="quantities_changed", model="fake-translator",
                        model_version="v1", created_at="2026-10-07T00:00:00+00:00",
                    ),
                ])
            finally:
                store.close()
            with patch.dict("os.environ", {
                "VOINEY_LAB_WORKSPACE_ENABLED": "true",
                "VOINEY_LAB_WORKSPACE_DATA_DIR": str(workspace),
            }), patch.object(server, "server_config", return_value=None), \
                    patch.object(server, "_configured_candidate_fixture", return_value=fixture), \
                    patch.object(server, "_revision_translation_runner",
                                 side_effect=AssertionError("a report never starts a translation")):
                loaded, problem = er.report_protocol_fixture(
                    {"protocol_id": "lane-n-wash", "protocol_revision": "lane-n-wash-v1"})
        self.assertEqual(problem, "")
        self.assertEqual(loaded.localized_fact("step-1", "current_step"), KOREAN_STEP_1)
        self.assertEqual(loaded.localization_source("step-1", "current_step"), "machine")
        self.assertIsNone(loaded.localized_fact("step-2", "current_step"))


class RoundBasisTests(unittest.TestCase):
    """Decision 8: a number of rounds is said with its basis, or not at all."""

    def facts(self) -> er.ReportFacts:
        events = [
            _done("2", "d2a"), _done("3", "d3a"), _done("4", "d4a"),
            {"event_key": "back", "event_type": "repeat_returned", "step_label": "2",
             "step_id": "step-2", "user_wording": None, "category": None,
             "payload": {"step_record": {"kind": "repeat_return", "from_step": "5",
                                         "to_step": "2", "round": 2}},
             "created_at": "2026-10-07T00:20:00+00:00"},
            _done("2", "d2b", step_record={"kind": "repeat_round_completion", "round": 2}),
        ]
        return er.build_report_facts(_report(*events), fixture=notes_fixture())

    def dropped(self, sentence: str) -> list[str]:
        # Lane RP3, decision ③: the rule now reads an AI-written sentence.
        return [why for _, why in er.check_ai_notes({"result_summary": sentence}, self.facts())[2]]

    def test_a_round_count_without_its_basis_is_refused(self) -> None:
        for sentence in ("2–4단계 세척을 총 2회 반복 수행했다.", "2단계를 2회차까지 수행했다.",
                         "세척을 두 번 반복했다."):
            with self.subTest(sentence=sentence):
                self.assertEqual(self.dropped(sentence), ["회차를 ‘말로 확인한 돌아가기 기준’ 없이 씀"])

    def test_a_round_count_with_its_basis_passes(self) -> None:
        self.assertEqual(self.dropped("5단계에서 2단계로 돌아가 2회차(말로 확인한 돌아가기 기준)를 수행했다."), [])

    def test_the_sources_own_count_is_not_a_round(self) -> None:
        self.assertEqual(self.dropped("2–4단계를 수행했다."), [])
        self.assertIsNone(er._ROUND_COUNT.search("원문은 2회 세척 후 투명해진다고 적는다."))


if __name__ == "__main__":
    unittest.main()
