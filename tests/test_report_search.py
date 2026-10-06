"""Google Search grounding for the report's purpose and background (lane RP, decision 4).

Contract-tested against fake SDK clients only: the adapter turns the option
into Gemini's google_search tool and hands back the pages a reply used, the
other adapters refuse it, and the report writer numbers those pages, cites
them, and says so when it could not search.
"""

from __future__ import annotations

import asyncio
import json
import os
import unittest
from types import SimpleNamespace
from unittest import mock

from voiney_lab import experiment_reports as er
from voiney_lab.model_providers import RoleModel, chat_client
from tests.test_lab_report import GOOD_REPLY, _Client, _ReportCase
from tests.test_model_providers import FakeAnthropic, FakeGemini, FakeOpenAI


def grounded_response(text: str) -> SimpleNamespace:
    web = [SimpleNamespace(web=SimpleNamespace(
               uri="https://vertexaisearch.cloud.google.com/grounding-api-redirect/AAA",
               title="wikipedia.org", domain="wikipedia.org")),
           SimpleNamespace(web=SimpleNamespace(
               uri="https://vertexaisearch.cloud.google.com/grounding-api-redirect/BBB",
               title="promega.com", domain="promega.com"))]
    supports = [
        SimpleNamespace(segment=SimpleNamespace(text="젤 속 단백질 소화는 질량분석으로 단백질을 확인하려고 한다."),
                        grounding_chunk_indices=[0]),
        SimpleNamespace(segment=SimpleNamespace(text="트립신은 단백질을 펩타이드로 자른다."),
                        grounding_chunk_indices=[1, 0]),
    ]
    return SimpleNamespace(
        model_version="gemini-test",
        candidates=[SimpleNamespace(
            content=SimpleNamespace(parts=[SimpleNamespace(text=text, thought=False, function_call=None)]),
            grounding_metadata=SimpleNamespace(
                grounding_chunks=web, grounding_supports=supports,
                web_search_queries=["in-gel digestion purpose"]),
        )],
        usage_metadata=None,
    )


class AdapterTests(unittest.TestCase):
    def test_gemini_sends_the_search_tool_and_returns_the_pages(self) -> None:
        fake = FakeGemini(reply=grounded_response("설명"))
        client = chat_client(RoleModel("report", "google", "gemini-test", None), sdk_client=fake)
        reply = asyncio.run(client.chat.completions.create(
            messages=[{"role": "user", "content": "왜?"}], web_search=True))
        self.assertIn({"google_search": {}}, fake.requests[0]["config"]["tools"])
        self.assertEqual([s.title for s in reply.grounding.sources], ["wikipedia.org", "promega.com"])
        self.assertEqual(reply.grounding.supports[1].source_indices, (1, 0))
        self.assertEqual(reply.grounding.queries, ["in-gel digestion purpose"])
        self.assertEqual(reply.choices[0].message.content, "설명")

    def test_without_the_option_gemini_sends_no_search_tool(self) -> None:
        fake = FakeGemini(reply=grounded_response("설명"))
        client = chat_client(RoleModel("report", "google", "gemini-test", None), sdk_client=fake)
        asyncio.run(client.chat.completions.create(messages=[{"role": "user", "content": "왜?"}]))
        self.assertNotIn("tools", fake.requests[0]["config"])

    def test_the_other_adapters_refuse_a_search_request(self) -> None:
        for provider, fake in (("openai", FakeOpenAI(reply=None)), ("anthropic", FakeAnthropic(reply=None))):
            client = chat_client(RoleModel("report", provider, "m", None), sdk_client=fake)
            with self.assertRaises(TypeError, msg=provider):
                asyncio.run(client.chat.completions.create(
                    messages=[{"role": "user", "content": "왜?"}], web_search=True))
            self.assertEqual(fake.requests, [], provider)


class _SearchClient:
    def __init__(self, response: object = None, error: BaseException | None = None) -> None:
        self.calls: list[dict] = []

        async def create(**kwargs):
            self.calls.append(kwargs)
            if error is not None:
                raise error
            return response

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


def _gemini_search() -> object:
    return chat_client(RoleModel("report", "google", "gemini-test", None),
                       sdk_client=FakeGemini(reply=grounded_response("설명")))


class WriterSearchTests(_ReportCase):
    def write(self, reply: dict, search_client: object, **brain) -> tuple[er.ReportNarrative, _Client]:
        client = _Client(reply)
        writer = er.ReportWriterBrain(
            client=client, model="gemini-test", timeout_seconds=5, search_client=search_client,
            resolve_url=lambda url: url.replace(
                "https://vertexaisearch.cloud.google.com/grounding-api-redirect/", "https://example.org/"),
            **brain,
        )
        doc = self.doc()
        return asyncio.run(writer.generate_narrative(doc, list(doc["events"]), fixture=self.fixture)), client

    def test_grounded_pages_become_numbered_references_the_model_may_cite(self) -> None:
        self.run_steps_one_to_four_then_stop()
        fake = FakeGemini(reply=grounded_response("설명"))
        search = chat_client(RoleModel("report", "google", "gemini-test", None), sdk_client=fake)
        reply = dict(GOOD_REPLY,
                     purpose="이 실험은 질량분석으로 단백질을 확인하려고 한다[1][2].",
                     background_external="트립신은 단백질을 펩타이드로 자른다[3].")
        narrative, client = self.write(reply, search)
        self.assertIn({"google_search": {}}, fake.requests[0]["config"]["tools"])
        self.assertNotIn("15 min", json.dumps(fake.requests[0]["contents"], ensure_ascii=False))
        sent = json.loads(client.calls[0]["messages"][1]["content"])
        self.assertEqual(sent["외부 자료"], [{"번호": 2, "사이트": "wikipedia.org"},
                                         {"번호": 3, "사이트": "promega.com"}])
        self.assertIn("트립신은 단백질을 펩타이드로 자른다. [2, 3]", sent["외부 자료 내용"])
        self.assertEqual(narrative.section_origin["background_external"], "모델")
        self.assertEqual(narrative.section_origin["purpose"], "모델")
        text = er.render_markdown(narrative)
        self.assertIn("- [2] wikipedia.org — https://example.org/AAA (구글 검색)", text)
        self.assertIn("- [3] promega.com — https://example.org/BBB (구글 검색)", text)
        self.assertIn("| 외부 검색 | 검색 사용 |", text)
        self.assertIn("| 검색어 | in-gel digestion purpose |", text)

    def test_a_citation_to_a_page_that_was_not_found_is_refused(self) -> None:
        self.run_steps_one_to_four_then_stop()
        narrative, _ = self.write(dict(GOOD_REPLY, background_external="트립신은 자른다[7]."),
                                  _gemini_search())
        self.assertEqual(narrative.section_origin["background_external"], "대체")
        self.assertIn("없는 출처 번호", narrative.rejected["background_external"])

    def test_an_uncited_external_sentence_is_refused(self) -> None:
        self.run_steps_one_to_four_then_stop()
        narrative, _ = self.write(
            dict(GOOD_REPLY, background_external="트립신은 자른다[2]. 그래서 펩타이드가 생긴다."),
            _gemini_search())
        self.assertIn("출처 번호가 없는 외부 지식 문장", narrative.rejected["background_external"])

    def test_a_failed_search_writes_from_the_pdf_and_says_so(self) -> None:
        self.run_steps_one_to_four_then_stop()
        narrative, client = self.write(GOOD_REPLY, _SearchClient(error=TimeoutError()))
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(json.loads(client.calls[0]["messages"][1]["content"])["외부 자료"], [])
        self.assertIn("외부 자료를 확인하지 못했다(검색 실패(TimeoutError))", er.render_markdown(narrative))

    def test_search_is_used_only_when_the_report_role_is_google(self) -> None:
        brain = er.ReportWriterBrain(client=None)
        with mock.patch.dict(os.environ, {"VOINEY_LAB_REPORT_PROVIDER": "anthropic",
                                          "ANTHROPIC_API_KEY": "test-key"}):
            client, why = brain._search_client()
        self.assertIsNone(client)
        self.assertEqual(why, "보고서 역할 공급자가 Google 이 아님")
        with mock.patch.dict(os.environ, {"VOINEY_LAB_REPORT_PROVIDER": "google",
                                          "GEMINI_API_KEY": "test-key",
                                          "VOINEY_LAB_REPORT_WEB_SEARCH_ENABLED": "false"}):
            client, why = er.ReportWriterBrain(client=None)._search_client()
        self.assertIsNone(client)
        self.assertEqual(why, "검색을 쓰지 않도록 설정됨")
        with mock.patch.dict(os.environ, {"VOINEY_LAB_REPORT_PROVIDER": "google",
                                          "GEMINI_API_KEY": "test-key"}):
            client, why = er.ReportWriterBrain(client=None)._search_client()
        self.assertIsNotNone(client)
        self.assertEqual(why, "")


if __name__ == "__main__":
    unittest.main()
