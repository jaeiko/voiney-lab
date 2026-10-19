"""Lane ON, decision ③ (2026-10-18): two key values to confirm before the start.

Human decision: from the analysis (or review version) that is about to run,
the server picks the two values that matter most for the run (time,
temperature, speed, amount, in that order) and asks each as a multiple
choice beside its source excerpt: the analysed value and two other values
the server makes deterministically (the other OCR engine's reading where
there is one, a decimal shift ×10 / ÷10, another value of the same kind).
The server decides what is right; the screen draws only what the server
sends. A right choice moves on; a wrong one says "원문 조각을 다시 봐 주세요"
and offers [원문대로 이 값이 맞아요] [분석 값이 틀렸어요 → 그 값의 검토 카드
열기]. The result is recorded. A protocol without numeric values skips the
check. A setting turns it off (on by default). No provider is called.
"""

from __future__ import annotations

import asyncio
import json
import os
import unittest
from unittest.mock import patch

import httpx

from tests.test_lane_rv_review_pending import _CatalogCase
from voiney_lab import server
from voiney_lab import start_check as sc
from voiney_lab.experiment_protocol_store import initialize_protocol_store
from voiney_lab.protocol_catalog import ProtocolCatalog


class KeyValueExtractionTests(unittest.TestCase):
    def test_values_are_found_with_their_kind_in_order(self) -> None:
        found = sc.key_values_in("2. Incubate for 10 min at 37 C, then spin at 500 x g; add 2 mL.", label="2")
        self.assertEqual([(value.kind, value.text) for value in found],
                         [("time", "10 min"), ("temperature", "37 C"), ("speed", "500 x g"), ("amount", "2 mL")])
        self.assertEqual((found[0].number, found[0].unit), ("10", "min"))
        self.assertEqual(found[1].token, "37°c")
        self.assertEqual(found[2].token, "500×g")

    def test_a_steps_own_number_is_not_a_value_and_prose_numbers_are_not_either(self) -> None:
        self.assertEqual(sc.key_values_in("3. Wash the pellet twice.", label="3"), [])
        self.assertEqual(sc.key_values_in("(4) Record the volume.", label="4"), [])
        self.assertEqual([value.text for value in sc.key_values_in("Store at 4 °C overnight.")], ["4 °C"])
        self.assertEqual([value.text for value in sc.key_values_in("Heat to 95°C for 5 minutes")],
                         ["95°C", "5 minutes"])
        self.assertEqual([value.text for value in sc.key_values_in("Centrifuge 13,000 rpm for 1 h")],
                         ["13,000 rpm", "1 h"])
        self.assertEqual([(v.kind, v.text) for v in sc.key_values_in("Add 50 µL of 1 M Tris and 10 mg salt")],
                         [("amount", "50 µL"), ("amount", "1 M"), ("amount", "10 mg")])

    def test_decimal_shifts_keep_the_units_shape(self) -> None:
        self.assertEqual(sc.decimal_shifts("10"), ("100", "1"))
        self.assertEqual(sc.decimal_shifts("37"), ("370", "3.7"))
        self.assertEqual(sc.decimal_shifts("0.5"), ("5", "0.05"))
        self.assertEqual(sc.decimal_shifts("13,000"), ("130,000", "1,300"))
        self.assertEqual(sc.decimal_shifts("2.50"), ("25", "0.25"))


def _items(*texts: str) -> list[dict]:
    items = []
    for index, text in enumerate(texts, 1):
        items.append({
            "item_id": f"step:s{index}", "kind": "step", "kind_ko": "단계", "step_id": f"s{index}",
            "step_label": str(index), "text": text, "current": text, "needs_review": False,
            "source": {"page": 1, "excerpt": text, "verified": True,
                       "lines": {"page": 1, "first_line": index, "last_line": index}},
            "allowed_actions": ["confirm", "fix", "memo", "reset"], "decision": None, "reasons": [],
        })
    return items


class QuestionBuildingTests(unittest.TestCase):
    def test_two_questions_by_kind_priority_time_then_temperature(self) -> None:
        questions = sc.build_questions(
            _items("1. Add 2 mL of buffer A.", "2. Incubate for 10 min at 37 C.", "4. Spin at 500 x g for 5 min."),
            {}, seed="analysis-a")
        self.assertEqual([question["kind"] for question in questions], ["time", "temperature"])
        self.assertEqual(questions[0]["step_label"], "2")
        self.assertEqual(questions[0]["value"], "10 min")
        self.assertEqual(questions[0]["kind_ko"], "시간")
        self.assertIn("2단계", questions[0]["question_ko"])
        self.assertIn("시간", questions[0]["question_ko"])
        self.assertEqual(questions[0]["source"]["excerpt"], "2. Incubate for 10 min at 37 C.")
        self.assertEqual(questions[1]["value"], "37 C")
        for question in questions:
            labels = [choice["label_ko"] for choice in question["choices"]]
            self.assertEqual(len(labels), 3)
            self.assertEqual(len(set(labels)), 3)
            self.assertIn(question["value"], labels)
            self.assertEqual([choice["choice_id"] for choice in question["choices"]], ["c1", "c2", "c3"])

    def test_the_other_values_are_the_same_kind_from_the_protocol_then_decimal_shifts(self) -> None:
        questions = sc.build_questions(
            _items("2. Incubate for 10 min at 37 C.", "4. Spin at 500 x g for 5 min."), {}, seed="x")
        time_labels = {choice["label_ko"] for choice in questions[0]["choices"]}
        self.assertEqual(time_labels, {"10 min", "5 min", "100 min"})
        temperature_labels = {choice["label_ko"] for choice in questions[1]["choices"]}
        self.assertEqual(temperature_labels, {"37 C", "370 C", "3.7 C"})

    def test_the_other_ocr_engines_reading_comes_first_when_it_differs(self) -> None:
        pages = {1: {"source_page_number": 1, "provider": "google", "other_provider": "clova",
                     "value_readings": [{"token": "10min", "text": "10 min", "other_token": "16min", "other_text": "16 min"}]}}
        questions = sc.build_questions(_items("2. Incubate for 10 min at 37 C."), pages, seed="x")
        time_labels = {choice["label_ko"] for choice in questions[0]["choices"]}
        self.assertEqual(time_labels, {"10 min", "16 min", "100 min"})

    def test_the_order_of_choices_is_deterministic_and_not_always_the_value_first(self) -> None:
        items = _items("2. Incubate for 10 min at 37 C.", "4. Spin at 500 x g for 5 min.")
        first = sc.build_questions(items, {}, seed="analysis-a")
        again = sc.build_questions(items, {}, seed="analysis-a")
        self.assertEqual(first, again)
        positions = set()
        for seed in ("a", "b", "c", "d", "e", "f", "g", "h"):
            for question in sc.build_questions(items, {}, seed=seed):
                positions.add([choice["label_ko"] for choice in question["choices"]].index(question["value"]))
        self.assertGreater(len(positions), 1)

    def test_only_times_gives_two_different_times_and_one_value_gives_one_question(self) -> None:
        two = sc.build_questions(_items("1. Wait 10 min.", "2. Wait 5 min.", "3. Wait 10 min again."), {}, seed="x")
        self.assertEqual([question["value"] for question in two], ["10 min", "5 min"])
        one = sc.build_questions(_items("1. Add 2 mL of buffer A."), {}, seed="x")
        self.assertEqual(len(one), 1)
        self.assertEqual(one[0]["kind"], "amount")
        self.assertEqual(sc.build_questions(_items("1. Wash the pellet twice."), {}, seed="x"), [])

    def test_a_value_the_review_changed_is_asked_as_it_now_stands(self) -> None:
        items = _items("2. Incubate for 10 min at 37 C.")
        items[0]["current"] = "2. Incubate for 15 min at 37 C."
        items[0]["decision"] = {"action": "fix"}
        questions = sc.build_questions(items, {}, seed="x")
        self.assertEqual(questions[0]["value"], "15 min")

    def test_the_public_question_never_says_which_choice_is_right(self) -> None:
        questions = sc.build_questions(_items("2. Incubate for 10 min at 37 C."), {}, seed="x")
        public = sc.public_question(questions[0])
        self.assertNotIn("value", public)
        self.assertNotIn("answer", json.dumps(public))
        self.assertEqual(set(public), {"question_id", "kind", "kind_ko", "step_label", "item_id", "question_ko",
                                       "source", "choices", "position", "total", "progress_ko"})


class StartCheckOnTheCatalogTests(_CatalogCase):
    def setUp(self) -> None:
        super().setUp()
        self.analyze(self.answer(pages=(1, 2, 3)))
        self.check = sc.StartCheck(self.catalog)

    def test_the_runnable_analysis_gives_two_questions_with_the_source(self) -> None:
        shown = self.check.questions(self.protocol_id)
        self.assertTrue(shown["enabled"])
        self.assertIsNone(shown["skipped_reason"])
        self.assertEqual(shown["target"]["kind"], "analysis")
        self.assertEqual([question["kind"] for question in shown["questions"]], ["time", "temperature"])
        first = shown["questions"][0]
        self.assertEqual((first["position"], first["total"], first["progress_ko"]), (1, 2, "1 / 2"))
        self.assertEqual(first["source"]["page"], 1)
        self.assertEqual(first["source"]["excerpt"], "2. Incubate for 10 min at 37 C.")
        self.assertEqual(first["source"]["lines"]["first_line"], 4)
        self.assertNotIn("value", first)

    def test_a_right_choice_moves_on_and_is_recorded(self) -> None:
        shown = self.check.questions(self.protocol_id)
        question = shown["questions"][0]
        built = self.check._built(self.protocol_id)[1][0]  # noqa: SLF001 - the test needs the right label
        right = next(choice for choice in question["choices"] if choice["label_ko"] == built["value"])
        result = self.check.answer(self.protocol_id, question["question_id"], choice_id=right["choice_id"],
                                   actor_principal_id="acct-1")
        self.assertTrue(result["correct"])
        self.assertTrue(result["proceed"])
        self.assertEqual(result["next_question_id"], shown["questions"][1]["question_id"])
        self.assertEqual(result["followups"], [])
        self.assertIsNone(result["card"])
        (event,) = self.events("protocol_start_check_answered")
        self.assertEqual((event["question_id"], event["correct"], event["action"]),
                         (question["question_id"], True, "answer"))
        self.assertEqual(event["value"], "10 min")
        self.assertEqual(event["chosen"], "10 min")
        self.assertEqual(event["actor_principal_id"], "acct-1")
        self.assertEqual(event["kind"], "time")

    def test_a_wrong_choice_says_look_again_and_offers_two_ways_on(self) -> None:
        shown = self.check.questions(self.protocol_id)
        question = shown["questions"][1]
        built = self.check._built(self.protocol_id)[1][1]  # noqa: SLF001
        wrong = next(choice for choice in question["choices"] if choice["label_ko"] != built["value"])
        result = self.check.answer(self.protocol_id, question["question_id"], choice_id=wrong["choice_id"])
        self.assertFalse(result["correct"])
        self.assertFalse(result["proceed"])
        self.assertIn("원문 조각을 다시 봐 주세요", result["message_ko"])
        self.assertEqual([item["label_ko"] for item in result["followups"]],
                         ["원문대로 이 값이 맞아요", "분석 값이 틀렸어요 → 검토 카드 열기"])
        accept = result["followups"][0]
        self.assertEqual(accept["request"], {"question_id": question["question_id"], "action": "accept"})
        card = result["card"]
        self.assertEqual(card["item_id"], "step:s2")
        self.assertEqual(card["shown"], "2. Incubate for 10 min at 37 C.")
        self.assertEqual(card["source"]["page"], 1)
        self.assertEqual([choice["choice_id"] for choice in card["choices"]], ["fix", "keep"])
        fix = card["choices"][0]
        self.assertEqual(fix["request"], {"item_id": "step:s2", "action": "fix"})
        self.assertEqual(fix["needs"], "text")
        self.assertTrue(fix["primary"])
        self.assertEqual(card["choices"][1]["route"], "back")
        # The right label is not given away.
        self.assertNotIn(built["value"], json.dumps({key: value for key, value in result.items() if key != "card"}))
        (event,) = self.events("protocol_start_check_answered")
        self.assertFalse(event["correct"])
        self.assertEqual(event["chosen"], wrong["label_ko"])

    def test_accepting_the_value_after_a_wrong_choice_moves_on_and_is_recorded(self) -> None:
        shown = self.check.questions(self.protocol_id)
        question = shown["questions"][0]
        result = self.check.answer(self.protocol_id, question["question_id"], action="accept")
        self.assertTrue(result["proceed"])
        self.assertTrue(result["correct"] is None)
        (event,) = self.events("protocol_start_check_answered")
        self.assertEqual(event["action"], "accept")

    def test_the_card_s_fix_changes_the_value_the_next_questions_ask(self) -> None:
        self.catalog.record_review_decision(self.protocol_id, {
            "item_id": "step:s2", "action": "fix", "text": "2. Incubate for 15 min at 37 C.",
            "reviewer_name": "김재준"})
        self.catalog.register_review(self.protocol_id, {"reviewer_name": "김재준"})
        shown = self.check.questions(self.protocol_id)
        self.assertEqual(shown["target"]["kind"], "review_version")
        built = self.check._built(self.protocol_id)[1]  # noqa: SLF001
        self.assertEqual(built[0]["value"], "15 min")
        labels = [choice["label_ko"] for choice in shown["questions"][0]["choices"]]
        self.assertIn("15 min", labels)
        self.assertNotIn("10 min", labels)

    def test_an_unknown_question_or_choice_is_refused(self) -> None:
        shown = self.check.questions(self.protocol_id)
        with self.assertRaises(sc.StartCheckError):
            self.check.answer(self.protocol_id, "q9:nothing", choice_id="c1")
        with self.assertRaises(sc.StartCheckError):
            self.check.answer(self.protocol_id, shown["questions"][0]["question_id"], choice_id="c9")
        with self.assertRaises(sc.StartCheckError):
            self.check.answer(self.protocol_id, shown["questions"][0]["question_id"])
        self.assertEqual(self.events("protocol_start_check_answered"), [])

    def test_switched_off_by_the_setting(self) -> None:
        off = sc.StartCheck(self.catalog, enabled=False)
        shown = off.questions(self.protocol_id)
        self.assertFalse(shown["enabled"])
        self.assertEqual(shown["questions"], [])
        self.assertEqual(shown["skipped_reason"], "disabled")


class StartCheckWithoutNumbersTests(_CatalogCase):
    def test_a_protocol_without_numeric_values_is_skipped(self) -> None:
        from tests.test_lane_rv_review_pending import write_pages_pdf
        from voiney_lab.experiment_protocol_pdf import extract_protocol_pdf

        pdf = self.pdf.with_name("delta.pdf")
        write_pages_pdf(pdf, [["Protocol Delta", "Section preparation", "1. Add buffer A.", "2. Mix gently."]],
                        title="Protocol Delta")
        sha256 = extract_protocol_pdf(pdf).sha256
        protocol_id = self.catalog.register(pdf, source_filename="delta.pdf", media_type="application/pdf").entry.protocol_id
        ev = lambda excerpt: {"source_page_number": 1, "source_excerpt": excerpt}  # noqa: E731
        answer = json.dumps({
            "analysis_schema_version": 1, "pdf_sha256": sha256, "capability_policy_id": "p1-conservative",
            "protocol": {
                "protocol_id": "delta",
                "metadata": {"title": "Protocol Delta", "original_language": "en", "evidence": ev("Protocol Delta")},
                "before_start": [], "materials": [], "equipment": [], "constructs": [], "description": None,
                "sections": [{"section_id": "prep", "title_source_text": "Section preparation",
                              "evidence": ev("Section preparation"),
                              "steps": [{"step_id": "s1", "source_label": "1", "instruction_source_text": "1. Add buffer A.",
                                         "evidence": ev("1. Add buffer A.")},
                                        {"step_id": "s2", "source_label": "2", "instruction_source_text": "2. Mix gently.",
                                         "evidence": ev("2. Mix gently.")}]}],
            },
        })
        self.catalog.request_analysis(protocol_id, "analysis-" + "d" * 32)
        from tests.test_lane_rv_review_pending import _RawModel

        entry = self.catalog.analyze(protocol_id, _RawModel(answer), analysis_id="analysis-" + "d" * 32)
        self.assertTrue(entry.available_for_execution)
        shown = sc.StartCheck(self.catalog).questions(protocol_id)
        self.assertTrue(shown["enabled"])
        self.assertEqual(shown["questions"], [])
        self.assertEqual(shown["skipped_reason"], "no_numeric_values")

    def test_an_analysis_that_waits_for_review_has_no_questions_yet(self) -> None:
        from tests.test_lane_rv_review_pending import MADE_UP

        self.analyze(self.answer(step_two=MADE_UP))
        shown = sc.StartCheck(self.catalog).questions(self.protocol_id)
        self.assertEqual(shown["questions"], [])
        self.assertEqual(shown["skipped_reason"], "not_executable")


class _FakeStore:
    def close(self):
        pass


class StartCheckRoutesTests(_CatalogCase):
    """The routes: the questions, an answer judged by the server, a refusal."""

    def setUp(self) -> None:
        super().setUp()
        self.analyze(self.answer(pages=(1, 2, 3)))
        def open_catalog():
            # The route runs in a worker thread: a connection of its own, as the server opens one.
            store = initialize_protocol_store(self.settings)
            return ProtocolCatalog(store), store

        self._patches = [
            patch.object(server, "_open_protocol_catalog", open_catalog),
            patch.object(server, "_scope_catalog_resource", lambda *args, **kwargs: None),
            patch.dict(os.environ, {"VOINEY_LAB_WORKSPACE_ENABLED": "false", "VOINEY_LAB_START_CHECK_ENABLED": "true"}),
        ]
        for item in self._patches:
            item.start()
            self.addCleanup(item.stop)

    def request(self, method: str, path: str, payload: dict | None = None):
        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://test") as client:
                return await client.request(method, path, json=payload)
        return asyncio.run(run())

    def test_the_questions_and_an_answer_through_the_routes(self) -> None:
        shown = self.request("GET", f"/api/protocols/{self.protocol_id}/start-check")
        self.assertEqual(shown.status_code, 200, shown.text)
        body = shown.json()
        self.assertTrue(body["enabled"])
        self.assertEqual(len(body["questions"]), 2)
        self.assertNotIn("value", body["questions"][0])
        question = body["questions"][0]
        built = sc.StartCheck(self.catalog)._built(self.protocol_id)[1][0]  # noqa: SLF001
        wrong = next(choice for choice in question["choices"] if choice["label_ko"] != built["value"])
        answered = self.request("POST", f"/api/protocols/{self.protocol_id}/start-check/answers",
                                {"question_id": question["question_id"], "choice_id": wrong["choice_id"]})
        self.assertEqual(answered.status_code, 200, answered.text)
        self.assertFalse(answered.json()["correct"])
        self.assertEqual(len(answered.json()["followups"]), 2)
        accepted = self.request("POST", f"/api/protocols/{self.protocol_id}/start-check/answers",
                                {"question_id": question["question_id"], "action": "accept"})
        self.assertEqual(accepted.status_code, 200)
        self.assertTrue(accepted.json()["proceed"])
        refused = self.request("POST", f"/api/protocols/{self.protocol_id}/start-check/answers",
                               {"question_id": "q9:nothing", "choice_id": "c1"})
        self.assertEqual(refused.status_code, 422)
        self.assertEqual(refused.json(), {"detail": "start_check_invalid"})
        self.assertEqual(len(self.events("protocol_start_check_answered")), 2)

    def test_the_setting_turns_the_routes_off(self) -> None:
        with patch.dict(os.environ, {"VOINEY_LAB_START_CHECK_ENABLED": "false"}):
            shown = self.request("GET", f"/api/protocols/{self.protocol_id}/start-check")
            self.assertEqual(shown.status_code, 200)
            self.assertFalse(shown.json()["enabled"])
            self.assertEqual(shown.json()["questions"], [])
            refused = self.request("POST", f"/api/protocols/{self.protocol_id}/start-check/answers",
                                   {"question_id": "q1:x", "choice_id": "c1"})
            self.assertEqual(refused.status_code, 422)


class SettingTests(unittest.TestCase):
    def test_on_by_default_off_by_the_setting(self) -> None:
        self.assertTrue(sc.start_check_enabled({}))
        self.assertFalse(sc.start_check_enabled({"VOINEY_LAB_START_CHECK_ENABLED": "false"}))
        self.assertTrue(sc.start_check_enabled({"VOINEY_LAB_START_CHECK_ENABLED": "TRUE"}))


if __name__ == "__main__":
    unittest.main()
