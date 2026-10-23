"""'검토 완료하고 등록': a review becomes the version that runs (lane RV, decision c).

Human decision 2026-10-12: the reviewer writes their name (required) and
presses "검토 완료하고 등록"; the review is stored as version N -- append-only,
the analysis it was reviewed from and the versions before it kept -- and an
item that waited for a person must be decided first. An analysis that passed
every check still runs without a review (the review is optional); reviewed
again, it becomes v2 and v1 stays on record.
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from tests.test_lane_rv_review_api import STEP_TWO
from tests.test_lane_rv_review_pending import MADE_UP, MISSED_PAGES_2_3, MISSED_WITH_MADE_UP, _CatalogCase
from voiney_lab import experiment_protocol as domain
from voiney_lab import server
from voiney_lab.experiment_protocol_store import initialize_protocol_store
from voiney_lab.protocol_catalog import (
    ProtocolCatalog,
    ProtocolReviewBlockedError,
    ProtocolReviewItemsPendingError,
    ProtocolReviewNameRequiredError,
)

LINES_OF_STEP_TWO = {"page": 1, "first_line": 4, "last_line": 4}


def with_constructs(answer: str, *constructs: dict) -> str:
    payload = json.loads(answer)
    payload["protocol"]["constructs"] = list(constructs)
    return json.dumps(payload)


def wash_twice(count: int | None = 2) -> dict:
    return {
        "type": "fixed_range_repetition", "repetition_id": "wash-twice",
        "start_step_id": "s3", "end_step_id": "s3",
        "range_source_text": "3. Wash the pellet twice.",
        "evidence": {"source_page_number": 2, "source_excerpt": "3. Wash the pellet twice."},
        "repeat_count": count, "repeat_count_kind": "total",
        "section_id": "wash", "step_id": "s3",
    }


class _VersionCase(_CatalogCase):
    def decide(self, **request) -> dict:
        return self.catalog.record_review_decision(self.protocol_id, request)

    def register(self, name: object = "김재준", **extra) -> dict:
        return self.catalog.register_review(self.protocol_id, {"reviewer_name": name, **extra})

    def versions(self) -> list[dict]:
        return list(self.catalog.get_entry(self.protocol_id).review_versions)


class RegistrationTests(_VersionCase):
    def setUp(self) -> None:
        super().setUp()
        self.analyze(self.answer(step_two=MADE_UP))
        # Lane MISS: the lines no quote covers (page 1's real step 2, pages
        # 2-3's steps) are cards too; a person says they are no step.
        self.assertEqual(self.confirm_possibly_missed(), MISSED_WITH_MADE_UP)

    def test_no_name_no_registration(self) -> None:
        self.decide(item_id="step:s2", action="confirm", source_lines=LINES_OF_STEP_TWO)
        for name in (None, "", "   "):
            with self.subTest(name=name), self.assertRaises(ProtocolReviewNameRequiredError):
                self.register(name)
        self.assertEqual(self.versions(), [])

    def test_an_undecided_item_that_waits_for_a_person_blocks_the_registration(self) -> None:
        with self.assertRaises(ProtocolReviewItemsPendingError) as refused:
            self.register()
        self.assertEqual(refused.exception.item_ids, ("step:s2",))
        self.assertFalse(self.catalog.get_entry(self.protocol_id).available_for_execution)

    def test_the_registered_version_runs_with_the_reviewer_s_words(self) -> None:
        self.decide(item_id="step:s2", action="fix", text=STEP_TWO,
                    source_lines=LINES_OF_STEP_TWO)
        result = self.register("  김재준 ")
        version = result["review_version"]
        self.assertEqual((version["version"], version["version_ko"]), (1, "검토본 v1"))
        self.assertEqual(version["revision_id"], "pdf-1-review-1")
        self.assertEqual(version["reviewer_name"], "김재준")
        (change,) = version["changes"]
        self.assertEqual(
            (change["item_id"], change["action"], change["original"], change["reviewed"],
             change["origin"]),
            ("step:s2", "fix", MADE_UP, STEP_TWO, "reviewer_input"))
        entry = self.catalog.get_entry(self.protocol_id)
        self.assertTrue(entry.available_for_execution)
        self.assertEqual(entry.revision_id, "pdf-1-review-1")
        self.assertEqual(entry.lifecycle_state, "ready")
        self.assertEqual(entry.analysis_status, "review_registered")
        self.assertIsNone(entry.review_pending)
        self.assertEqual(entry.public_dict()["current_review_version"]["version"], 1)
        fixture = self.catalog.load_executable_fixture(self.protocol_id)
        self.assertEqual(fixture.revision_id, "pdf-1-review-1")
        step = fixture.steps[1]
        self.assertEqual(step.instruction_source_text, STEP_TWO)
        # The quote is the line a person pointed at, as the page prints it.
        self.assertEqual(step.evidence.source_excerpt, STEP_TWO)
        info = fixture.draft.review_version
        self.assertEqual((info.version, info.reviewer_name), (1, "김재준"))
        self.assertEqual(info.changes[0].original, MADE_UP)
        # The timer the reviewer's sentence states is read from the page as
        # any other: the line pointed at prints it.
        self.assertEqual(fixture.timer_manifest, {"s2": 600})

    def test_nothing_is_changed_in_place(self) -> None:
        (pending_before,) = [e for e in self.store.list_events(self.protocol_id)
                             if e.event_type == "protocol_analysis_review_pending"]
        self.decide(item_id="step:s2", action="confirm", source_lines=LINES_OF_STEP_TWO)
        self.register()
        (pending_after,) = [e for e in self.store.list_events(self.protocol_id)
                            if e.event_type == "protocol_analysis_review_pending"]
        self.assertEqual(pending_before, pending_after)
        (analysis,) = self.store.list_analysis_revisions(self.protocol_id, 1)
        self.assertTrue(analysis.analysis_id.startswith("review-v1-"))
        # The stored version is a valid protocol against its source.
        domain.validate_protocol(analysis.protocol)
        # A confirmed AI suggestion is recorded as one.
        (event,) = [e.payload for e in self.store.list_events(self.protocol_id)
                    if e.event_type == "protocol_review_version_registered"]
        self.assertEqual(event["origins"], {"step:s2": "ai_suggestion"})
        self.assertEqual(event["base"]["kind"], "review_pending")

    def test_a_registered_version_is_not_analysed_again_by_a_plain_request(self) -> None:
        self.decide(item_id="step:s2", action="confirm", source_lines=LINES_OF_STEP_TWO)
        self.register()
        self.catalog.request_analysis(self.protocol_id, "analysis-" + "9" * 32)
        requests = [e for e in self.store.list_events(self.protocol_id)
                    if e.event_type == "protocol_analysis_requested"]
        self.assertEqual(len(requests), 1)
        # "다시 분석" still asks.
        self.catalog.request_analysis(self.protocol_id, "analysis-" + "8" * 32, reanalysis=True)
        self.assertEqual(len([e for e in self.store.list_events(self.protocol_id)
                              if e.event_type == "protocol_analysis_requested"]), 2)

    def test_reviewed_again_it_becomes_v2_and_v1_stays(self) -> None:
        self.decide(item_id="step:s2", action="confirm", source_lines=LINES_OF_STEP_TWO)
        self.register("김재준")
        draft = self.catalog.review_draft(self.protocol_id)
        self.assertEqual(draft["target"], {
            "kind": "review_version", "version": 1, "recorded_at": draft["target"]["recorded_at"],
            "revision_id": "pdf-1-review-1", "analysis_id": draft["target"]["analysis_id"]})
        # v1's confirmed AI suggestion keeps its badge in the next review.
        self.assertEqual(
            next(i for i in draft["items"] if i["item_id"] == "step:s2")["origin"],
            "ai_suggestion")
        self.decide(item_id="step:s1", action="fix", text="1. Add 2.5 mL of buffer A.")
        second = self.register("최수진")["review_version"]
        self.assertEqual((second["version"], second["revision_id"]), (2, "pdf-1-review-2"))
        self.assertEqual([v["version"] for v in self.versions()], [1, 2])
        self.assertEqual(self.catalog.get_entry(self.protocol_id).revision_id, "pdf-1-review-2")
        fixture = self.catalog.load_executable_fixture(self.protocol_id)
        self.assertEqual(fixture.steps[0].instruction_source_text, "1. Add 2.5 mL of buffer A.")
        self.assertEqual(fixture.steps[1].evidence.source_excerpt, STEP_TWO)
        first = self.catalog.review_version(self.protocol_id, 1)
        self.assertEqual(first["reviewer_name"], "김재준")
        self.assertEqual([c["item_id"] for c in first["changes"]], ["step:s2"])
        (fix,) = self.catalog.review_version(self.protocol_id, 2)["changes"]
        self.assertEqual((fix["original"], fix["reviewed"]),
                         ("1. Add 2 mL of buffer A.", "1. Add 2.5 mL of buffer A."))
        self.assertEqual(fix["spoken"], "2.5 mL")


class ValuesTimersAndCountsTests(_VersionCase):
    def setUp(self) -> None:
        super().setUp()
        self.analyze(with_constructs(self.answer(pages=(1, 2, 3)), wash_twice()))

    def test_an_analysis_that_passed_runs_without_a_review(self) -> None:
        entry = self.catalog.get_entry(self.protocol_id)
        self.assertTrue(entry.available_for_execution)
        self.assertEqual(entry.revision_id, "pdf-1-analysis-1")
        self.assertEqual(entry.review_versions, ())

    def test_registered_with_no_change_it_is_the_analysis_as_it_came(self) -> None:
        analysis = self.store.list_analysis_revisions(self.protocol_id, 1)[0]
        version = self.register()["review_version"]
        self.assertEqual(version["changes"], [])
        fixture = self.catalog.load_executable_fixture(self.protocol_id)
        self.assertEqual(fixture.draft.protocol.sections, analysis.protocol.sections)
        self.assertEqual(fixture.revision_id, "pdf-1-review-1")

    def test_a_reviewer_s_timer_runs_as_the_reviewer_s(self) -> None:
        self.decide(item_id="timer:s2", action="fix", seconds=900)
        self.decide(item_id="timer:s4", action="clear")
        self.decide(item_id="timer:s1", action="enter", seconds=120)
        self.register()
        fixture = self.catalog.load_executable_fixture(self.protocol_id)
        self.assertEqual(fixture.timer_manifest, {"s1": 120, "s2": 900})
        reviewer = [timer for timer in fixture.timer_table.verified
                    if timer.source == "reviewer_input"]
        self.assertEqual([(t.step_id, t.literal) for t in reviewer], [("s1", "2분"), ("s2", "15분")])
        spoken = fixture.draft.review_version.spoken_for_step("s2")
        self.assertEqual(spoken, ("15분",))
        review = self.catalog.review(self.protocol_id)
        self.assertEqual(review["review_version"]["version"], 1)
        verified = {timer["step_id"]: timer for timer in review["timers"]["verified"]}
        self.assertEqual(set(verified), {"s1", "s2"})

    def test_a_repeat_count_is_fixed_or_left_for_the_start(self) -> None:
        item = "construct:wash-twice/repeat_count"
        self.decide(item_id=item, action="fix", count=3)
        self.register()
        (construct,) = self.catalog.load_executable_fixture(
            self.protocol_id).draft.protocol.constructs
        self.assertEqual((construct.repeat_count, construct.repeat_count_kind), (3, "total"))
        self.decide(item_id=item, action="clear")
        self.register("최수진")
        (construct,) = self.catalog.load_executable_fixture(
            self.protocol_id).draft.protocol.constructs
        self.assertIsNone(construct.repeat_count)


class FlaggedValuesTests(_VersionCase):
    def test_a_value_left_empty_leaves_the_version(self) -> None:
        payload = json.loads(self.answer())
        payload["protocol"]["sections"][0]["steps"][0]["sub_actions"] = [{
            "action_id": "add-buffer", "instruction_source_text": "1. Add 2 mL of buffer A.",
            "evidence": {"source_page_number": 1, "source_excerpt": "1. Add 2 mL of buffer A."},
            "quantities": [{"source_text": "3 mL"}, {"source_text": "2 mL"}],
        }]
        self.analyze(json.dumps(payload))
        draft = self.catalog.review_draft(self.protocol_id)
        # Lane MISS: pages 2-3's steps, which this answer leaves out, too.
        self.assertEqual(draft["needs_review"], ["action:add-buffer/quantities/0", *MISSED_PAGES_2_3])
        self.decide(item_id="action:add-buffer/quantities/0", action="clear")
        self.confirm_possibly_missed()
        self.register()
        (action,) = self.catalog.load_executable_fixture(
            self.protocol_id).draft.protocol.sections[0].steps[0].sub_actions
        self.assertEqual([q.source_text for q in action.quantities], ["2 mL"])
        (change,) = self.catalog.review_version(self.protocol_id, 1)["changes"]
        self.assertEqual((change["action"], change["original"], change["reviewed"]),
                         ("clear", "3 mL", None))


class SourceWordsOnlyTests(_VersionCase):
    def test_a_confirmed_safety_statement_runs_in_the_source_s_words(self) -> None:
        # "안전 주의와 완료 기준은 AI 가 새로 써 넣지 않는다" (2026-10-12): the
        # analysis worded the warning its own way; confirmed, the run says
        # the source's line, never the analysis's wording.
        payload = json.loads(self.answer())
        payload["protocol"]["sections"][0]["steps"][1]["warnings"][0]["source_text"] = (
            "Always wear two pairs of gloves.")
        self.analyze(json.dumps(payload))
        draft = self.catalog.review_draft(self.protocol_id)
        # Lane MISS: pages 2-3's steps, which this answer leaves out, too.
        self.assertEqual(draft["needs_review"], ["statement:gloves", *MISSED_PAGES_2_3])
        self.decide(item_id="statement:gloves", action="confirm")
        self.confirm_possibly_missed()
        version = self.register()["review_version"]
        (change,) = version["changes"]
        self.assertEqual(
            (change["original"], change["reviewed"], change["origin"]),
            ("Always wear two pairs of gloves.", "Wear gloves.", "source"))
        fixture = self.catalog.load_executable_fixture(self.protocol_id)
        self.assertEqual(fixture.steps[1].warnings[0].source_text, "Wear gloves.")
        self.assertNotIn("two pairs", json.dumps(server.fixture_safety_notices(fixture)))


class BlockedVersionTests(_VersionCase):
    def test_a_version_that_would_not_run_is_not_registered(self) -> None:
        conflict = {
            "type": "protocol_conflict", "conflict_id": "gloves-or-not", "level": "safety_critical",
            "source_text": "Wear gloves.",
            "evidence": {"source_page_number": 1, "source_excerpt": "Wear gloves."},
            "section_id": "prep", "step_id": "s2",
        }
        self.analyze(with_constructs(self.answer(), conflict))
        self.assertFalse(self.catalog.get_entry(self.protocol_id).available_for_execution)
        with self.assertRaises(ProtocolReviewBlockedError) as refused:
            self.register()
        self.assertEqual(refused.exception.codes, ("safety_critical_conflict",))
        self.assertEqual(self.versions(), [])


class RegistrationRouteTests(_VersionCase):
    def call(self, function, *args):
        self.store.close()

        def open_catalog():
            opened = initialize_protocol_store(self.settings)
            return ProtocolCatalog(opened), opened

        try:
            with patch.object(server, "_open_protocol_catalog", side_effect=open_catalog):
                return function(*args)
        finally:
            self.store = initialize_protocol_store(self.settings)
            self.catalog = ProtocolCatalog(self.store)

    def test_the_route_registers_and_refuses_in_the_server_s_words(self) -> None:
        self.analyze(self.answer(step_two=MADE_UP))
        with self.assertRaises(HTTPException) as pending:
            self.call(server.post_protocol_review_version, self.protocol_id,
                      {"reviewer_name": "김재준"})
        self.assertEqual((pending.exception.status_code, pending.exception.detail),
                         (409, "review_items_pending"))
        with self.assertRaises(HTTPException) as nameless:
            self.call(server.post_protocol_review_version, self.protocol_id, {})
        self.assertEqual((nameless.exception.status_code, nameless.exception.detail),
                         (422, "reviewer_name_required"))
        self.call(server.post_protocol_review_decision, self.protocol_id,
                  {"item_id": "step:s2", "action": "confirm", "source_lines": LINES_OF_STEP_TWO})
        # Lane MISS: the possibly-missed cards are answered too.
        for item_id in MISSED_WITH_MADE_UP:
            self.call(server.post_protocol_review_decision, self.protocol_id,
                      {"item_id": item_id, "action": "confirm"})
        registered = self.call(server.post_protocol_review_version, self.protocol_id,
                               {"reviewer_name": "김재준"})
        self.assertEqual(registered["protocol"]["revision_id"], "pdf-1-review-1")
        row = self.catalog.get_entry(self.protocol_id).public_dict()
        self.assertEqual(row["current_review_version"]["reviewer_name"], "김재준")
        one = self.call(server.get_protocol_review_version, self.protocol_id, 1)
        self.assertEqual(one["version_ko"], "검토본 v1")


if __name__ == "__main__":
    unittest.main()
