"""The re-ask runs after the cards are shown, and closes the cards it settles (lane AN5, decision 3).

Lane AN4 asked the refused items once more before storing the analysis: a
document it asked about waited a median 16 s longer, and 3-4 minutes when
the provider was slow -- while 15 of the 21 items it saved came from calls
of over 100 s, so cutting the call short would have saved little.

Human decision 2026-10-20 (the recommended option): the analysis is stored
right after its first answer is checked and its cards are shown; the re-ask
runs after. Each item it keeps closes its card as "원문 위치를 다시 찾아
확인했어요" -- one more ledger event, nothing stored before changes. A card a
person has answered meanwhile is left as they answered it. While it runs the
progress line says "원문 위치를 다시 찾는 중"; its calls are waited for five
minutes at most (past that the cards stay). Every model here is fake.
"""

from __future__ import annotations

import contextlib
import json
import threading
from concurrent.futures import Future
from typing import Any
from unittest.mock import patch

from voiney_lab import experiment_protocol_analysis as analysis_module
from voiney_lab.experiment_protocol_store import initialize_protocol_store
from voiney_lab.protocol_catalog import ProtocolCatalog
from tests.test_lane_an3_ocr_two_engines import KEEP, SCAN, SPIN, _ScanCase, provider, scan_answer
from tests.test_lane_an4_requote import (
    ANALYSIS_ID,
    STEP_TWO,
    _AskingModel,
    _RequoteCase,
    answer_with,
    first_item,
    found,
)

PARAPHRASE = "2. Incubate 10 minutes at 37 C."
SPIN_WRONG = "4. Spin at 500 x g for 15 min."


def _two_refused(case) -> str:
    """Step 2 quoted in other words (the re-ask finds it) and step 4 with a
    value the page does not print (the re-ask cannot)."""

    payload = json.loads(case.answer(step_two=PARAPHRASE, pages=(1, 2, 3)))
    spin = payload["protocol"]["sections"][1]["steps"][1]
    spin["evidence"]["source_excerpt"] = spin["instruction_source_text"] = SPIN_WRONG
    return json.dumps(payload)


def _finds_step_two(request: dict) -> dict:
    answers = []
    for item in request["items"]:
        if item["written"] == PARAPHRASE:
            answers.append(found(item["id"], 1, STEP_TWO))
        else:
            answers.append({"id": item["id"], "found": False, "page": None, "span": None})
    return answer_with(*answers)


@contextlib.contextmanager
def elsewhere(case):
    """The catalog as another server worker reads it while the re-ask is out:
    its own connection (the re-ask's calls run on a thread of their own)."""

    store = initialize_protocol_store(case.settings)
    try:
        yield ProtocolCatalog(store)
    finally:
        store.close()


class CardsFirstTests(_RequoteCase):
    def test_the_cards_are_stored_and_shown_before_the_re_ask_is_sent(self) -> None:
        seen: dict[str, object] = {}

        def requote(request: dict) -> dict:
            with elsewhere(self) as catalog:
                entry = catalog.get_entry(self.protocol_id)
                seen["status"] = entry.analysis_status
                seen["message"] = entry.review_pending["message_ko"]
                seen["requoting"] = entry.review_pending.get("requoting")
                seen["cards"] = [card["item_id"] for card in
                                 catalog.review_draft(self.protocol_id)["cards"]]
            return _finds_step_two(request)

        entry = self.run_analysis(_AskingModel(_two_refused(self), requote=requote))
        self.assertEqual(seen["status"], "review_pending")
        self.assertEqual(seen["cards"], ["step:s2", "step:s4"])
        self.assertTrue(seen["requoting"])
        self.assertTrue(str(seen["message"]).endswith(" · 원문 위치를 다시 찾는 중"))
        # Done: the line no longer says it.
        self.assertNotIn("원문 위치를 다시 찾는 중", entry.review_pending["message_ko"])
        self.assertNotIn("requoting", entry.review_pending)
        types = [event.event_type for event in self.store.list_events(self.protocol_id)]
        self.assertLess(types.index("protocol_analysis_review_pending"),
                        types.index("protocol_analysis_evidence_requote_started"))
        self.assertLess(types.index("protocol_analysis_evidence_requote_started"),
                        types.index("protocol_analysis_evidence_requote"))

    def test_an_item_found_closes_its_card_and_the_others_stay(self) -> None:
        entry = self.run_analysis(_AskingModel(_two_refused(self), requote=_finds_step_two))
        self.assertEqual(entry.analysis_status, "review_pending")
        self.assertEqual(entry.review_pending["needs_review_count"], 1)
        self.assertEqual(entry.review_pending["closed_by_requote"], 1)
        draft = self.catalog.review_draft(self.protocol_id)
        closed, open_card = draft["cards"]
        self.assertEqual((closed["item_id"], closed["type"], closed["decided"]),
                         ("step:s2", "requoted", True))
        self.assertEqual(closed["question_ko"], "원문 위치를 다시 찾아 확인했어요")
        self.assertEqual(closed["closed_ko"], "원문 위치를 다시 찾아 확인했어요")
        self.assertEqual(closed["choices"], [])
        self.assertEqual(closed["decision"]["action"], "requoted")
        self.assertEqual(closed["source"]["excerpt"], STEP_TWO)
        self.assertTrue(closed["source"]["verified"])
        self.assertEqual((open_card["item_id"], open_card["decided"]), ("step:s4", False))
        self.assertEqual(draft["next_card"], 1)
        self.assertEqual([blocker["item_id"] for blocker in draft["registration_blockers"]],
                         ["step:s4"])
        # The stored analysis is as it was; the record the review reads is the
        # one the re-ask wrote beside it.
        (pending,) = self.events("protocol_analysis_review_pending")
        self.assertNotIn("auto_resolved", pending)
        (closing,) = self.events("protocol_analysis_review_cards_closed")
        self.assertEqual(closing["closed"], [{
            "item": "step:s2", "field": "evidence", "page": 1, "reason": "evidence_requoted",
            "reason_ko": "원문 위치를 다시 찾아 확인했어요"}])
        protocol, _, flags, _ = self.catalog.review_pending_analysis(self.protocol_id)
        self.assertEqual(protocol.sections[0].steps[1].evidence.source_excerpt, STEP_TWO)
        self.assertEqual([flag.item_id for flag in flags], ["step:s4"])

    def test_registering_after_the_rest_is_decided_keeps_what_was_found(self) -> None:
        self.run_analysis(_AskingModel(_two_refused(self), requote=_finds_step_two))
        self.catalog.record_review_decision(self.protocol_id, {
            "item_id": "step:s4", "action": "confirm",
            "source_lines": {"page": 2, "first_line": 3, "last_line": 3}})
        self.catalog.register_review(self.protocol_id, {"reviewer_name": "김재준"})
        fixture = self.catalog.load_executable_fixture(self.protocol_id)
        self.assertEqual(fixture.revision_id, "pdf-1-review-1")
        steps = self.store.list_analysis_revisions(self.protocol_id, 1)[-1].protocol.sections
        self.assertEqual(steps[0].steps[1].evidence.source_excerpt, STEP_TWO)

    def test_every_card_closed_and_none_touched_passes_as_it_would_have(self) -> None:
        model = _AskingModel(
            self.answer(step_two=PARAPHRASE),
            requote=lambda request: answer_with(found(first_item(request)["id"], 1, STEP_TWO)))
        entry = self.run_analysis(model)
        self.assertTrue(entry.available_for_execution)
        self.assertIsNone(entry.review_pending)
        (ready,) = self.events("protocol_analysis_ready")
        self.assertIn("evidence_requoted", [item["reason"] for item in ready["auto_resolved"]])
        (event,) = self.events("protocol_analysis_evidence_requote")
        self.assertEqual((event["status"], event["closed"], event["items_requoted"]),
                         ("completed", "all", 1))
        self.assertEqual(self.events("protocol_analysis_review_cards_closed"), [])


class APersonAnsweredFirstTests(_RequoteCase):
    def test_a_card_a_person_answered_meanwhile_stays_theirs(self) -> None:
        def requote(request: dict) -> dict:
            # The person answers step 2's card while the re-ask is out.
            with elsewhere(self) as catalog:
                catalog.record_review_decision(self.protocol_id, {
                    "item_id": "step:s2", "action": "fix", "text": "2. Incubate for 10 min at 37 C.",
                    "source_lines": {"page": 1, "first_line": 4, "last_line": 4}})
            return _finds_step_two(request)

        entry = self.run_analysis(_AskingModel(_two_refused(self), requote=requote))
        self.assertEqual(entry.analysis_status, "review_pending")
        draft = self.catalog.review_draft(self.protocol_id)
        card = next(card for card in draft["cards"] if card["item_id"] == "step:s2")
        self.assertNotEqual(card["type"], "requoted")
        self.assertEqual(card["decision"]["action"], "fix")
        self.assertEqual(self.events("protocol_analysis_review_cards_closed"), [])
        (event,) = self.events("protocol_analysis_evidence_requote")
        self.assertEqual(event["answered_by_person"], ["step:s2"])
        self.assertEqual(event["by_outcome"], {"answered_by_person": 1, "model_found_nothing": 1})

    def test_a_memo_alone_keeps_the_analysis_from_passing_by_itself(self) -> None:
        def requote(request: dict) -> dict:
            with elsewhere(self) as catalog:
                catalog.record_review_decision(self.protocol_id, {
                    "item_id": "step:s2", "action": "memo", "memo": "원문 다시 볼 것"})
            return answer_with(found(first_item(request)["id"], 1, STEP_TWO))

        entry = self.run_analysis(_AskingModel(self.answer(step_two=PARAPHRASE), requote=requote))
        self.assertEqual(entry.analysis_status, "review_pending")
        draft = self.catalog.review_draft(self.protocol_id)
        # Lane MISS: pages 2-3's steps, which this answer leaves out, wait too.
        self.assertEqual([(card["item_id"], card["type"]) for card in draft["cards"]],
                         [("step:s2", "requoted"), ("missed:2:2-3", "possibly_missed"),
                          ("missed:3:2-3", "possibly_missed")])
        self.assertFalse(draft["registrable"])
        self.confirm_possibly_missed()
        self.assertTrue(self.catalog.review_draft(self.protocol_id)["registrable"])

    def test_a_review_registered_meanwhile_is_not_touched(self) -> None:
        def requote(request: dict) -> dict:
            with elsewhere(self) as catalog:
                for item_id, lines in (("step:s2", 4), ("step:s4", 3)):
                    catalog.record_review_decision(self.protocol_id, {
                        "item_id": item_id, "action": "confirm",
                        "source_lines": {"page": 1 if item_id == "step:s2" else 2,
                                         "first_line": lines, "last_line": lines}})
                catalog.register_review(self.protocol_id, {"reviewer_name": "김재준"})
            return _finds_step_two(request)

        entry = self.run_analysis(_AskingModel(_two_refused(self), requote=requote))
        self.assertTrue(entry.available_for_execution)
        self.assertEqual(self.events("protocol_analysis_review_cards_closed"), [])
        (event,) = self.events("protocol_analysis_evidence_requote")
        self.assertEqual(event["closed"], "superseded")


class TimeLimitTests(_RequoteCase):
    def test_past_the_limit_the_cards_stay_and_nobody_waits_for_the_call(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        pending: list[Future[Any]] = []

        def slow(request: dict) -> dict:
            entered.set()
            release.wait()
            return _finds_step_two(request)

        def expire(
            futures: list[Future[Any]], *, timeout: float,
        ) -> tuple[set[Future[Any]], set[Future[Any]]]:
            # Advance the wait boundary, not the machine's wall clock. The
            # model is still blocked when analyze returns its timeout result.
            self.assertEqual(timeout, 0.2)
            pending.extend(futures)
            self.assertTrue(entered.wait(300), "re-ask worker did not start")
            self.assertTrue(all(not future.done() for future in futures))
            return set(), set(futures)

        watchdog = threading.Timer(300, release.set)
        watchdog.daemon = True
        watchdog.start()
        try:
            with patch("voiney_lab.protocol_catalog.REQUOTE_TIME_LIMIT_SECONDS", 0.2), patch.object(
                analysis_module, "wait_for_futures", expire
            ):
                entry = self.run_analysis(_AskingModel(_two_refused(self), requote=slow))
            self.assertFalse(release.is_set())
            self.assertTrue(pending)
            self.assertTrue(all(not future.done() for future in pending))
        finally:
            watchdog.cancel()
            release.set()
            for future in pending:
                future.result(timeout=300)
            watchdog.join(300)
        self.assertEqual(entry.review_pending["needs_review_count"], 2)
        self.assertEqual(self.events("protocol_analysis_review_cards_closed"), [])
        (event,) = self.events("protocol_analysis_evidence_requote")
        (call,) = event["calls"]
        self.assertEqual((call["status"], call["failure_code"]), ("failed", "requote_time_limit"))
        self.assertEqual(event["by_outcome"], {"call_failed": 2})
        self.assertEqual(self.catalog.get_entry(self.protocol_id).review_pending["needs_review_count"], 2)
        (started_event,) = self.events("protocol_analysis_evidence_requote_started")
        self.assertEqual(started_event["time_limit_seconds"], 0.2)

    def test_the_limit_is_five_minutes(self) -> None:
        from voiney_lab import protocol_catalog

        self.assertEqual(protocol_catalog.REQUOTE_TIME_LIMIT_SECONDS, 300.0)

    def test_a_re_ask_cut_off_by_a_restart_is_no_longer_said_to_run(self) -> None:
        from voiney_lab import protocol_catalog

        def requote(request: dict) -> dict:
            raise KeyboardInterrupt  # stands for the process stopping under it

        self.catalog.request_analysis(self.protocol_id, ANALYSIS_ID)
        with self.assertRaises(KeyboardInterrupt):
            self.catalog.analyze(self.protocol_id, _AskingModel(_two_refused(self), requote=requote),
                                 analysis_id=ANALYSIS_ID)
        self.assertTrue(self.catalog.get_entry(self.protocol_id).review_pending.get("requoting"))
        with patch.object(protocol_catalog, "_REQUOTE_STALE_SECONDS", -1):
            summary = self.catalog.get_entry(self.protocol_id).review_pending
        self.assertNotIn("requoting", summary)


class OverTheCapTests(_RequoteCase):
    def test_more_items_than_cards_are_asked_about_first_as_before(self) -> None:
        from tests.test_lane_rv2_many_items import with_made_up_notes

        payload = json.loads(with_made_up_notes(self.answer(step_two=PARAPHRASE), 12))
        model = _AskingModel(
            json.dumps(payload),
            requote=lambda request: answer_with(*(
                found(item["id"], 1, STEP_TWO) if item["written"] == PARAPHRASE
                else {"id": item["id"], "found": False, "page": None, "span": None}
                for item in request["items"])))
        entry = self.run_analysis(model)
        self.assertEqual(model.analyses, 1)
        # Lane MISS: and pages 2-3's steps, never counted toward the bound.
        self.assertEqual(entry.review_pending["needs_review_count"], 14)
        types = [event.event_type for event in self.store.list_events(self.protocol_id)]
        self.assertLess(types.index("protocol_analysis_evidence_requote"),
                        types.index("protocol_analysis_review_pending"))
        self.assertNotIn("protocol_analysis_evidence_requote_started", types)
        self.assertEqual(self.events("protocol_analysis_auto_reanalysis"), [])


class OcrPageTests(_ScanCase):
    """An item found again on an OCR page now states values the first pass
    left to its card: they are read again as every OCR value is."""

    def analyze_with(self, other: list[str]):
        written = "2. Spin the tube at 4000 rpm, 5 min."
        answer = scan_answer(self.sha256, steps=[("s2", written), ("s3", KEEP)])
        payload = json.loads(answer)
        spin = payload["protocol"]["sections"][1]["steps"][0]
        spin["notes"] = [{"statement_id": "ref", "source_text": "Note: see reference 12.",
                          "evidence": {"source_page_number": 2,
                                       "source_excerpt": "Note: see reference 12."}}]
        model = _AskingModel(json.dumps(payload), requote=lambda request: answer_with(*(
            found(item["id"], 2, SPIN) for item in request["items"])))
        self.catalog.run_ocr(self.protocol_id, provider(SCAN, other), ocr_id="ocr-" + "a" * 32)
        self.catalog.request_analysis(self.protocol_id, ANALYSIS_ID)
        return self.catalog.analyze(self.protocol_id, model, analysis_id=ANALYSIS_ID)

    def test_found_again_where_both_engines_read_its_values_alike_it_runs(self) -> None:
        entry = self.analyze_with(list(SCAN))
        self.assertTrue(entry.available_for_execution)
        (ready,) = self.events("protocol_analysis_ready")
        settled = {(item["page"], item["token"]) for item in ready["auto_resolved"]
                   if item.get("kind") == "ocr_value"}
        self.assertIn((2, "4000rpm"), settled)
        self.assertIn("evidence_requoted", [item.get("reason") for item in ready["auto_resolved"]])

    def test_found_again_where_the_engines_read_its_value_differently_it_stays_a_card(self) -> None:
        other = [line.replace("4000 rpm", "400 rpm") for line in SCAN]
        entry = self.analyze_with(other)
        self.assertEqual(entry.analysis_status, "review_pending")
        self.assertEqual(self.events("protocol_analysis_review_cards_closed"), [])
        (event,) = self.events("protocol_analysis_evidence_requote")
        self.assertEqual(event["by_outcome"], {"ocr_values_unsettled": 1})
        self.assertEqual([card["item_id"] for card in self.cards()], ["step:s2"])
