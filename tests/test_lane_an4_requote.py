"""Refused items are asked about once more, and kept only if the checks pass (lane AN4, 1).

Human decision 2026-10-19 (the recommended option): after the first analysis,
the items the evidence check refused -- 원문에 없음, 근거를 못 찾음, a quote that
does not open with its step number -- go in one small request with the text
of their pages only, asking for the stretch of the page that says each one,
character for character, or "not on the page". A stretch goes through the
same checks as any answer: it passes and the item is kept, recorded as "근거를
다시 찾아 확인", or the item stays a card as it was. Values never change: a
stretch with another number or unit leaves the item a card. Once per
analysis, once per piece of a long document; off by a setting. Every model
here is fake.
"""

from __future__ import annotations

import json
import os
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from tests.test_lane_ev2_cross_page import evidence, response, source, step
from tests.test_lane_rv2_many_items import with_made_up_notes
from tests.test_lane_rv_review_pending import _CatalogCase
from voiney_lab.experiment_protocol_analysis import (
    ANALYSIS_REQUOTE_SETTING,
    AnalysisPiece,
    OpenAICompatibleProtocolAnalysisModel,
    REQUOTE_RESPONSE_SCHEMA,
    REQUOTE_SYSTEM_PROMPT,
    analysis_requote_enabled,
    parse_protocol_analysis_response,
    requote_keeps_the_order,
    requote_shares_words,
    requote_stated_values,
    requote_words,
    requote_unverified_items,
)

STEP_TWO = "2. Incubate for 10 min at 37 C."
ANALYSIS_ID = "analysis-" + "a" * 32


def answer_with(*answers: dict) -> dict:
    return {"answers": list(answers)}


def found(question_id: str, page: int, span: str) -> dict:
    return {"id": question_id, "found": True, "page": page, "span": span}


class _AskingModel:
    """Analysis answers in turn; the re-ask answered by ``requote(request)``."""

    def __init__(self, *answers: str, requote=None, by_page: dict[int, list[str]] | None = None):
        self.answers = list(answers)
        self.by_page = by_page
        self.requote = requote
        self.requests: list[dict] = []
        self.analyses = 0

    def analyze(self, *, system_prompt: str, input_json: str, response_schema: dict) -> str:
        request = json.loads(input_json)
        if system_prompt == REQUOTE_SYSTEM_PROMPT:
            self.requests.append(request)
            assert response_schema == REQUOTE_RESPONSE_SCHEMA
            answer = self.requote(request) if self.requote is not None else RuntimeError("unasked")
            if isinstance(answer, Exception):
                raise answer
            return json.dumps(answer)
        self.analyses += 1
        if self.by_page is not None:
            return self.by_page[request["pages"][0]["source_page_number"]].pop(0)
        return self.answers.pop(0)


def first_item(request: dict) -> dict:
    return request["items"][0]


class _RequoteCase(_CatalogCase):
    def run_analysis(self, model, *, env: dict | None = None):
        self.catalog.request_analysis(self.protocol_id, ANALYSIS_ID)
        with patch.dict(os.environ, env or {}):
            try:
                return self.catalog.analyze(self.protocol_id, model, analysis_id=ANALYSIS_ID)
            except Exception as exc:  # noqa: BLE001 - the caller looks at it
                return exc

    def passed_protocol(self):
        return self.store.list_analysis_revisions(self.protocol_id, 1)[-1].protocol

    def step_two(self):
        return self.passed_protocol().sections[0].steps[1]

    def with_instruction(self, instruction: str, quote: str = STEP_TWO) -> str:
        payload = json.loads(self.answer(step_two=quote))
        payload["protocol"]["sections"][0]["steps"][1]["instruction_source_text"] = instruction
        return json.dumps(payload)


class AskedAgainAndKeptTests(_RequoteCase):
    def test_a_step_quoted_in_other_words_is_asked_again_and_the_analysis_runs(self) -> None:
        paraphrase = "2. Incubate 10 minutes at 37 C."
        model = _AskingModel(
            self.answer(step_two=paraphrase),
            requote=lambda request: answer_with(found(first_item(request)["id"], 1, STEP_TWO)))
        entry = self.run_analysis(model)
        self.assertTrue(entry.available_for_execution)
        self.assertIsNone(entry.review_pending)
        self.assertEqual((model.analyses, len(model.requests)), (1, 1))
        (request,) = model.requests
        # Only the item's page and the item: no other page's text.
        self.assertEqual([page["source_page_number"] for page in request["pages"]], [1])
        (item,) = request["items"]
        self.assertEqual(
            {key: item[key] for key in ("ask", "record", "pages", "written", "label")},
            {"ask": "numbered_quote", "record": "step", "pages": [1],
             "written": paraphrase, "label": "2"})
        protocol = self.passed_protocol()
        step_two = protocol.sections[0].steps[1]
        self.assertEqual(step_two.evidence.source_excerpt, STEP_TWO)
        # The step's words were the refused quote's own: they follow it.
        self.assertEqual(step_two.instruction_source_text, STEP_TWO)
        (ready,) = self.events("protocol_analysis_ready")
        self.assertIn({
            "reason": "evidence_requoted", "reason_ko": "근거를 다시 찾아 확인",
            "item": "step:s2", "field": "evidence", "was": "not_in_source", "page": 1,
        }, ready["auto_resolved"])
        (event,) = self.events("protocol_analysis_evidence_requote")
        self.assertEqual(
            {key: event[key] for key in ("status", "analysis_id", "questions", "items",
                                         "items_requoted", "by_outcome")},
            {"status": "completed", "analysis_id": ANALYSIS_ID, "questions": 1, "items": 1,
             "items_requoted": 1, "by_outcome": {"requoted": 1}})
        (call,) = event["calls"]
        self.assertEqual(
            {key: call[key] for key in ("first_page", "last_page", "pages", "questions", "status")},
            {"first_page": 1, "last_page": 1, "pages": 1, "questions": 1, "status": "answered"})

    def test_a_step_s_words_written_differently_take_the_page_s_words(self) -> None:
        model = _AskingModel(
            self.with_instruction("2. Incubate 10 minutes at 37 C."),
            requote=lambda request: answer_with(found(first_item(request)["id"], 1, STEP_TWO)))
        entry = self.run_analysis(model)
        self.assertTrue(entry.available_for_execution)
        (item,) = model.requests[0]["items"]
        self.assertEqual((item["ask"], item["written"]), ("words", "2. Incubate 10 minutes at 37 C."))
        self.assertEqual(self.step_two().instruction_source_text, STEP_TWO)

    def test_a_stretch_whose_words_stand_in_another_order_leaves_the_item_a_card(self) -> None:
        # The page's words in another order than the written text's: on a page
        # whose lines run out of order (an OCR page read across columns) such a
        # stretch no longer says the step, so it is not taken.
        model = _AskingModel(
            self.with_instruction("2. Incubate at 37 C for 10 min."),
            requote=lambda request: answer_with(found(first_item(request)["id"], 1, STEP_TWO)))
        entry = self.run_analysis(model)
        self.assertEqual(entry.analysis_status, "review_pending")
        (event,) = self.events("protocol_analysis_evidence_requote")
        self.assertEqual(event["by_outcome"], {"words_out_of_order": 1})
        protocol = self.catalog.review_pending_analysis(self.protocol_id)[0]
        self.assertEqual(protocol.sections[0].steps[1].instruction_source_text,
                         "2. Incubate at 37 C for 10 min.")

    def test_a_quote_that_does_not_open_with_its_step_number_is_asked_for_with_it(self) -> None:
        # The quote is the page's own words from the middle of step 3's line,
        # so the page does not print "3." right before it.
        payload = json.loads(self.answer(pages=(1, 2, 3)))
        third = payload["protocol"]["sections"][1]["steps"][0]
        third["evidence"]["source_excerpt"] = "the pellet twice."
        model = _AskingModel(
            json.dumps(payload),
            requote=lambda request: answer_with(
                found(first_item(request)["id"], 2, "3. Wash the pellet twice.")))
        entry = self.run_analysis(model)
        self.assertTrue(entry.available_for_execution)
        (item,) = model.requests[0]["items"]
        self.assertEqual((item["ask"], item["label"]), ("numbered_quote", "3"))
        third = self.passed_protocol().sections[1].steps[0]
        self.assertEqual(third.evidence.source_excerpt, "3. Wash the pellet twice.")
        (ready,) = self.events("protocol_analysis_ready")
        kept = [entry for entry in ready["auto_resolved"] if entry["reason"] == "evidence_requoted"]
        self.assertEqual([(entry["item"], entry["field"]) for entry in kept],
                         [("step:s3", "source_label")])

    def test_kept_items_leave_the_others_waiting_with_their_cards(self) -> None:
        payload = json.loads(self.answer(step_two="2. Incubate 10 minutes at 37 C.", pages=(1, 2, 3)))
        payload["protocol"]["sections"][1]["steps"][1]["evidence"]["source_excerpt"] = \
            "4. Spin at 500 x g for 15 min."
        payload["protocol"]["sections"][1]["steps"][1]["instruction_source_text"] = \
            "4. Spin at 500 x g for 15 min."

        def requote(request):
            by_written = {item["written"]: item["id"] for item in request["items"]}
            return answer_with(
                found(by_written["2. Incubate 10 minutes at 37 C."], 1, STEP_TWO),
                {"id": by_written["4. Spin at 500 x g for 15 min."], "found": False,
                 "page": None, "span": None})

        model = _AskingModel(json.dumps(payload), requote=requote)
        entry = self.run_analysis(model)
        self.assertEqual(entry.analysis_status, "review_pending")
        self.assertEqual(entry.review_pending["needs_review_count"], 1)
        # One request for both items, with both pages.
        (request,) = model.requests
        self.assertEqual([page["source_page_number"] for page in request["pages"]], [1, 2])
        # Lane AN5, decision 3: the cards are shown before the re-ask; the one
        # it settled stays in its place, closed, and the record it closed it
        # with holds the settlement.
        cards = self.catalog.review_draft(self.protocol_id)["cards"]
        self.assertEqual([(card["item_id"], card["decided"]) for card in cards],
                         [("step:s2", True), ("step:s4", False)])
        (closing,) = self.events("protocol_analysis_review_cards_closed")
        self.assertEqual(
            [entry["item"] for entry in closing["auto_resolved"]
             if entry["reason"] == "evidence_requoted"], ["step:s2"])
        (event,) = self.events("protocol_analysis_evidence_requote")
        self.assertEqual(event["by_outcome"], {"requoted": 1, "model_found_nothing": 1})

    def test_rescued_items_count_before_the_cap_so_nothing_is_read_again(self) -> None:
        payload = json.loads(with_made_up_notes(self.answer(), 11))
        payload["protocol"]["sections"][0]["steps"][1]["warnings"][0].update(
            source_text="Wear the gloves.", evidence=evidence("Wear the gloves."))
        payload["protocol"]["sections"][0]["steps"][1]["evidence"]["source_excerpt"] = \
            "2. Incubate 10 minutes at 37 C."
        payload["protocol"]["sections"][0]["steps"][1]["instruction_source_text"] = \
            "2. Incubate 10 minutes at 37 C."

        def requote(request):
            answers = []
            for item in request["items"]:
                span = {"Wear the gloves.": "Wear gloves.",
                        "2. Incubate 10 minutes at 37 C.": STEP_TWO}.get(item["written"])
                answers.append(found(item["id"], 1, span) if span else
                               {"id": item["id"], "found": False, "page": None, "span": None})
            return answer_with(*answers)

        model = _AskingModel(json.dumps(payload), requote=requote)
        entry = self.run_analysis(model)
        self.assertEqual(model.analyses, 1)
        self.assertEqual(entry.review_pending["needs_review_count"], 11)
        self.assertEqual(self.events("protocol_analysis_auto_reanalysis"), [])


class LeftAsACardTests(_RequoteCase):
    def run_with_answer(self, written: str, answer, *, page: int = 1):
        model = _AskingModel(
            self.answer(step_two=written),
            requote=lambda request: answer_with(answer(first_item(request)["id"])))
        return self.run_analysis(model), model

    def assert_still_a_card(self, entry, outcome: str) -> None:
        self.assertEqual(entry.analysis_status, "review_pending")
        (flag,) = self.catalog.review_pending_analysis(self.protocol_id)[2]
        self.assertEqual((flag.item_id, flag.field, flag.reason),
                         ("step:s2", "evidence", "not_in_source"))
        (event,) = self.events("protocol_analysis_evidence_requote")
        self.assertEqual((event["items_requoted"], event["by_outcome"]), (0, {outcome: 1}))
        (pending,) = self.events("protocol_analysis_review_pending")
        self.assertNotIn("auto_resolved", pending)
        protocol = self.catalog.review_pending_analysis(self.protocol_id)[0]
        self.assertNotEqual(protocol.sections[0].steps[1].evidence.source_excerpt, STEP_TWO)

    def test_a_stretch_with_another_number_leaves_the_item_a_card(self) -> None:
        entry, _ = self.run_with_answer(
            "2. Incubate for 15 min at 37 C.", lambda qid: found(qid, 1, STEP_TWO))
        self.assert_still_a_card(entry, "value_differs")

    def test_a_stretch_with_another_unit_leaves_the_item_a_card(self) -> None:
        entry, _ = self.run_with_answer(
            "2. Incubate for 10 h at 37 C.", lambda qid: found(qid, 1, STEP_TWO))
        self.assert_still_a_card(entry, "value_differs")

    def test_not_on_the_page_leaves_the_item_a_card(self) -> None:
        entry, _ = self.run_with_answer(
            "2. Incubate 10 minutes at 37 C.",
            lambda qid: {"id": qid, "found": False, "page": None, "span": None})
        self.assert_still_a_card(entry, "model_found_nothing")

    def test_a_stretch_the_page_does_not_print_is_refused_by_the_same_check(self) -> None:
        entry, _ = self.run_with_answer(
            "2. Incubate 10 minutes at 37 C.",
            lambda qid: found(qid, 1, "2. Incubate for 10 min at 37 C, gently."))
        self.assert_still_a_card(entry, "still_refused")

    def test_a_page_the_item_was_not_offered_is_refused(self) -> None:
        entry, _ = self.run_with_answer(
            "2. Incubate 10 minutes at 37 C.", lambda qid: found(qid, 2, STEP_TWO))
        self.assert_still_a_card(entry, "invalid_answer")

    def test_another_passage_with_the_same_numbers_is_not_this_item(self) -> None:
        # Same numbers, other words: the stretch is another passage of the
        # page, not this item's.
        entry, _ = self.run_with_answer(
            "2. Leave the plate 10 minutes in the 37 C room.",
            lambda qid: found(qid, 1, STEP_TWO))
        self.assert_still_a_card(entry, "other_passage")

    def test_an_item_with_no_answer_is_left_as_it_was(self) -> None:
        entry, _ = self.run_with_answer(
            "2. Incubate 10 minutes at 37 C.", lambda qid: {"id": "q99", "found": False})
        self.assert_still_a_card(entry, "no_answer")

    def test_a_failed_call_leaves_the_cards_and_is_recorded(self) -> None:
        model = _AskingModel(self.answer(step_two="2. Incubate 10 minutes at 37 C."),
                             requote=lambda request: TimeoutError("late"))
        entry = self.run_analysis(model)
        self.assertEqual(entry.analysis_status, "review_pending")
        (event,) = self.events("protocol_analysis_evidence_requote")
        self.assertEqual(event["by_outcome"], {"call_failed": 1})
        (call,) = event["calls"]
        self.assertEqual((call["status"], call["failure_code"]), ("failed", "protocol_analysis_timeout"))
        self.assertEqual(self.events("protocol_analysis_failed"), [])

    def test_an_answer_not_in_the_answer_s_shape_leaves_the_cards(self) -> None:
        model = _AskingModel(self.answer(step_two="2. Incubate 10 minutes at 37 C."),
                             requote=lambda request: ["not", "an", "object"])
        entry = self.run_analysis(model)
        self.assertEqual(entry.analysis_status, "review_pending")
        (event,) = self.events("protocol_analysis_evidence_requote")
        self.assertEqual(event["calls"][0]["failure_code"], "protocol_analysis_invalid_response")

    def test_a_kept_quote_that_makes_its_values_refused_is_put_back(self) -> None:
        # The action's quote was refused, so its value was never read against
        # it; with the quote kept, "38 C" is not on the page: the action's
        # answer is put back and nothing new is asked of a person.
        payload = json.loads(self.answer())
        payload["protocol"]["sections"][0]["steps"][1]["sub_actions"] = [{
            "action_id": "warm", "instruction_source_text": "Incubate for 10 min at 37 C",
            "evidence": evidence("Incubate for 10 min at 37 C, gently"),
            "quantities": [{"source_text": "38 C"}],
        }]
        model = _AskingModel(
            json.dumps(payload),
            requote=lambda request: answer_with(
                found(first_item(request)["id"], 1, "Incubate for 10 min at 37 C")))
        entry = self.run_analysis(model)
        self.assertEqual(entry.analysis_status, "review_pending")
        flags = self.catalog.review_pending_analysis(self.protocol_id)[2]
        self.assertEqual([(flag.item_id, flag.field) for flag in flags], [("action:warm", "evidence")])
        (event,) = self.events("protocol_analysis_evidence_requote")
        self.assertEqual(event["by_outcome"], {"still_refused": 1})


class OnceAndOnlyWhenAskedTests(_RequoteCase):
    def test_an_analysis_with_nothing_refused_asks_nothing(self) -> None:
        model = _AskingModel(self.answer())
        entry = self.run_analysis(model)
        self.assertTrue(entry.available_for_execution)
        self.assertEqual(model.requests, [])
        self.assertEqual(self.events("protocol_analysis_evidence_requote"), [])

    def test_the_setting_turns_it_off(self) -> None:
        model = _AskingModel(self.answer(step_two="2. Incubate 10 minutes at 37 C."),
                             requote=lambda request: answer_with())
        entry = self.run_analysis(model, env={ANALYSIS_REQUOTE_SETTING: "false"})
        self.assertEqual(entry.analysis_status, "review_pending")
        self.assertEqual(model.requests, [])
        self.assertEqual(self.events("protocol_analysis_evidence_requote"), [])

    def test_the_setting_is_on_unless_turned_off(self) -> None:
        self.assertTrue(analysis_requote_enabled({}))
        self.assertTrue(analysis_requote_enabled({ANALYSIS_REQUOTE_SETTING: "true"}))
        for off in ("false", "0", "no", "off", " FALSE "):
            self.assertFalse(analysis_requote_enabled({ANALYSIS_REQUOTE_SETTING: off}), off)

    def test_an_analysis_read_again_for_too_many_items_is_not_asked_about_again(self) -> None:
        levels: list[str | None] = []
        asked: list[str] = []
        answers = [with_made_up_notes(self.answer(), 13), with_made_up_notes(self.answer(), 2)]

        def create(**request):
            levels.append(request.get("reasoning_effort"))
            if request["messages"][0]["content"] == REQUOTE_SYSTEM_PROMPT:
                asked.append(request["reasoning_effort"])
                items = json.loads(request["messages"][1]["content"].split("\n")[1])["items"]
                content = json.dumps(answer_with(*(
                    {"id": item["id"], "found": False, "page": None, "span": None} for item in items)))
            else:
                content = answers.pop(0)
            message = SimpleNamespace(content=content)
            return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")])

        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        entry = self.run_analysis(OpenAICompatibleProtocolAnalysisModel(client, "model-test", "low"))
        self.assertEqual(levels, ["low", "low", "medium"])
        self.assertEqual(asked, ["low"])
        self.assertEqual(entry.review_pending["needs_review_count"], 2)
        (event,) = self.events("protocol_analysis_evidence_requote")
        self.assertEqual((event["questions"], event["items_requoted"]), (13, 0))


class LongDocumentTests(_RequoteCase):
    PIECES = (AnalysisPiece(1, 3, 1, 1), AnalysisPiece(2, 3, 2, 2), AnalysisPiece(3, 3, 3, 3))

    def test_each_piece_with_refused_items_is_asked_once_with_its_own_pages(self) -> None:
        one = self.answer(pages=(1,), step_two="2. Incubate 10 minutes at 37 C.")
        three = json.loads(self.answer(pages=(3,)))
        three["protocol"]["sections"][0]["steps"][0]["evidence"]["source_excerpt"] = \
            "5. Keep the tube at 4 C."
        three["protocol"]["sections"][0]["steps"][0]["instruction_source_text"] = \
            "5. Keep the tube at 4 C."

        def requote(request):
            item = first_item(request)
            span = {1: STEP_TWO, 3: "5. Store the tube at 4 C."}[item["pages"][0]]
            return answer_with(found(item["id"], item["pages"][0], span))

        model = _AskingModel(
            by_page={1: [one], 2: [self.answer(pages=(2,))], 3: [json.dumps(three)]},
            requote=requote)
        with patch("voiney_lab.protocol_catalog.analysis_pieces", return_value=self.PIECES):
            entry = self.run_analysis(model)
        self.assertTrue(entry.available_for_execution, entry.review_pending)
        self.assertEqual(
            sorted([page["source_page_number"] for page in request["pages"]]
                   for request in model.requests), [[1], [3]])
        (event,) = self.events("protocol_analysis_evidence_requote")
        self.assertEqual((event["questions"], event["items_requoted"], len(event["calls"])),
                         (2, 2, 2))
        labels = [step.instruction_source_text
                  for section in self.passed_protocol().sections
                  for step in section.steps]
        self.assertIn(STEP_TWO, labels)
        self.assertIn("5. Store the tube at 4 C.", labels)


class OcrPageTests(unittest.TestCase):
    """The page's own text is what the stretch is checked against, OCR or not."""

    def test_a_quote_on_an_ocr_page_is_kept_in_the_page_s_own_words(self) -> None:
        extraction = source()
        pages = list(extraction.pages)
        pages[1] = replace(pages[1], ocr_derived=True)
        extraction = replace(extraction, pages=tuple(pages))
        line = ("2. Transfer the thawed nuclei suspension in aliquots of no more than "
                "50,000 cells to each tube.")
        written = line.replace("50,000", "50000").replace("suspension in", "suspension, in")
        draft = parse_protocol_analysis_response(
            json.dumps(response(step("1", "1. Thaw a frozen aliquot of nuclei at room temperature.",
                                     "1. Thaw a frozen aliquot of nuclei at room temperature."),
                                step("2", written, written, page=2))),
            extraction, review_unverified=True)
        (flag,) = draft.review_flags
        self.assertEqual(flag.reason, "ocr_unclear")

        class Model:
            requests: list[dict] = []

            def analyze(self, *, system_prompt, input_json, response_schema):
                request = json.loads(input_json)
                self.requests.append(request)
                return json.dumps(answer_with(found(first_item(request)["id"], 2, line)))

        model = Model()
        result = requote_unverified_items(draft, model)
        self.assertEqual(result.draft.review_flags, ())
        self.assertEqual(result.kept_items, ("step:step-2-2",))
        (request,) = model.requests
        self.assertEqual([page["source_page_number"] for page in request["pages"]], [2])
        self.assertEqual(request["pages"][0]["text"], extraction.pages[1].text)


class ValuesAndWordsTests(unittest.TestCase):
    """What "the same value" and "the same passage" mean here."""

    def test_the_same_value_written_another_way(self) -> None:
        for written, page in (
            ("5 minutes", "5 min"), ("37°C", "37 °C"), ("37 C", "37 ℃"),
            ("12,000 x g", "12000 ×g"), ("4-8 h", "4–8 hr"), ("−20 °C", "-20 °C"),
            ("10 μl", "10 µL"), ("5 분", "5분"),
        ):
            self.assertEqual(requote_stated_values(written), requote_stated_values(page),
                             (written, page))

    def test_another_value(self) -> None:
        for written, page in (
            ("5 mL", "5 µL"), ("12.000", "12000"), ("-20 °C", "20 °C"), ("10 min", "10 h"),
            ("2.2", "22"), ("1 tube", "1 tube 2"), ("5 min", "5"),
        ):
            self.assertNotEqual(requote_stated_values(written), requote_stated_values(page),
                                (written, page))

    def test_lines_read_out_of_order_are_not_the_written_order(self) -> None:
        # Lane AN4's live run: an OCR page that ran two columns' lines into
        # each other gave the same words and numbers in another order.
        written = ("The live and dead cells were harvested by centrifugation at 7,000 × g for 7 min "
                   "at room temperature and the pellets were resuspended in saline at "
                   "approximately 1 × 108 CFU/ml (an OD 600 of 0.255).")
        scrambled = ("The live and dead cells\nwere harvested by centrifugation at 7,000 × g for 7 min at\n"
                     "approximately 1 × 108 CFU/ml (an OD 600 of 0.255). Live cells\n"
                     "room temperature and the pellets were resuspended in saline at")
        self.assertEqual(requote_stated_values(written), requote_stated_values(scrambled))
        self.assertTrue(requote_shares_words(written, scrambled))
        self.assertFalse(requote_keeps_the_order(written, scrambled))

    def test_a_word_broken_at_the_line_end_is_the_same_word_in_the_same_place(self) -> None:
        written = "All culture incubations are performed in a 37°C, 5% CO2 incubator unless otherwise specified."
        printed = "All culture incubations are performed in a 37°C, 5% CO2 incubator unless oth-\nerwise specified."
        self.assertTrue(requote_keeps_the_order(written, printed))
        self.assertEqual(requote_words(written), requote_words(printed))

    def test_the_same_passage_shares_half_its_words(self) -> None:
        self.assertTrue(requote_shares_words("Wear the gloves.", "Wear gloves."))
        self.assertTrue(requote_shares_words("시료를 4 °C 에 보관한다", "시료는 4 °C에 보관합니다."))
        self.assertFalse(requote_shares_words("Record the volume.", "Wash the pellet twice."))


if __name__ == "__main__":
    unittest.main()
