"""One json_schema response format, sent in the form each provider takes (lane P3).

Human decision 2026-10-05 (lane P3): the adapters may change so the analysis
role works on the providers lane M1 added. On the real APIs all three refused
the PDF analysis schema outright (Anthropic: "Schema type 'oneOf' is not
supported"; OpenAI strict: "'required' ... must include every key"; Gemini:
400 INVALID_ARGUMENT), and the analysis request named no output limit, so the
adapters' chat-sized default would have cut a 21-68 KB reply.

These tests pin the request shapes and the reply restoration against fake SDK
clients. They are contract-tested, not live-tested.
"""

from __future__ import annotations

import copy
import json
import unittest
from types import SimpleNamespace
from typing import Any

from voiney_lab import model_providers as mp
from voiney_lab.experiment_protocol_analysis import (
    ANALYSIS_MAX_OUTPUT_TOKENS,
    ANALYSIS_RESPONSE_SCHEMA,
    ANALYSIS_SYSTEM_PROMPT,
    OpenAICompatibleProtocolAnalysisModel,
    build_protocol_analysis_chat_request,
    parse_protocol_analysis_response,
)
from voiney_lab.experiment_protocol_pdf import (
    ProtocolPdfExtraction,
    ProtocolPdfMetadata,
    ProtocolPdfPage,
)
from voiney_lab.model_providers import RoleModel, chat_client

SMALL = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "korean": {"type": "string"},
        "note": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "tags": {"type": "array", "items": {"type": "string"}},
        "kind": {"oneOf": [
            {"type": "object", "additionalProperties": False,
             "properties": {"kind": {"type": "string", "const": "a"}, "x": {"type": "integer"}},
             "required": ["kind"]},
            {"type": "object", "additionalProperties": False,
             "properties": {"kind": {"type": "string", "const": "b"}, "y": {"type": "string"}},
             "required": ["kind"]},
        ]},
    },
    "required": ["korean"],
}


def response_format(schema: dict, strict: bool = True) -> dict:
    return {"type": "json_schema", "json_schema": {"name": "test_v1", "strict": strict, "schema": schema}}


class SyncFake:
    """A synchronous SDK client: records each request, answers ``text``."""

    def __init__(self, provider: str, text: str = "{}") -> None:
        self.requests: list[dict[str, Any]] = []
        self.text = text
        if provider == "openai":
            self.responses = SimpleNamespace(create=self._openai)
        elif provider == "anthropic":
            self.messages = SimpleNamespace(create=self._anthropic)
        else:
            self.models = SimpleNamespace(generate_content=self._gemini)

    def _openai(self, **params):
        self.requests.append(params)
        return SimpleNamespace(model="gpt", usage=None, output=[SimpleNamespace(
            type="message", content=[SimpleNamespace(type="output_text", text=self.text)])])

    def _anthropic(self, **params):
        self.requests.append(params)
        return SimpleNamespace(model="claude", usage=None,
                               content=[SimpleNamespace(type="text", text=self.text)])

    def _gemini(self, **params):
        self.requests.append(params)
        return SimpleNamespace(model_version="gemini", usage_metadata=None, candidates=[
            SimpleNamespace(content=SimpleNamespace(parts=[
                SimpleNamespace(thought=False, function_call=None, text=self.text)]))])


def call(provider: str, model: str, schema: dict, text: str = "{}", reasoning: str | None = "high",
         **extra) -> tuple[SyncFake, str]:
    fake = SyncFake(provider, text)
    client = chat_client(RoleModel("analysis", provider, model, reasoning),
                         asynchronous=False, sdk_client=fake)
    response = client.chat.completions.create(
        model=model, messages=[{"role": "system", "content": "RULES"},
                               {"role": "user", "content": "DOC"}],
        response_format=response_format(schema), **extra)
    return fake, response.choices[0].message.content


def keywords(node: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(node, dict):
        found.update(node)
        for value in node.values():
            found |= keywords(value)
    elif isinstance(node, list):
        for value in node:
            found |= keywords(value)
    return found


class SchemaCountTests(unittest.TestCase):
    def test_the_analysis_schema_is_over_every_grammar_limit_it_was_refused_for(self) -> None:
        properties, optional, unions = mp._schema_counts(mp._any_of(ANALYSIS_RESPONSE_SCHEMA))
        self.assertGreater(properties, mp.GEMINI_MAX_SCHEMA_PROPERTIES)
        self.assertGreater(optional, mp.ANTHROPIC_MAX_OPTIONAL_PROPERTIES)
        self.assertGreater(unions, mp.ANTHROPIC_MAX_UNION_PROPERTIES)

    def test_a_definition_is_counted_once_however_often_it_is_referenced(self) -> None:
        schema = {"type": "object", "$defs": {"E": {"type": "object", "properties": {
            "a": {"type": "string"}, "b": {"type": "string"}}, "required": ["a"]}},
            "properties": {"x": {"$ref": "#/$defs/E"}, "y": {"$ref": "#/$defs/E"}},
            "required": ["x", "y"]}
        self.assertEqual(mp._schema_counts(schema), (4, 1, 0))


class OpenAIStrictTests(unittest.TestCase):
    def test_every_property_is_required_and_an_optional_one_nullable(self) -> None:
        fake, _ = call("openai", "gpt-6.1-sol", SMALL)
        sent = fake.requests[0]["text"]["format"]
        self.assertTrue(sent["strict"])
        schema = sent["schema"]
        self.assertEqual(schema["required"], ["korean", "note", "tags", "kind"])
        self.assertEqual(schema["properties"]["tags"]["anyOf"][1], {"type": "null"})
        # Already nullable: unchanged, not wrapped twice.
        self.assertEqual(schema["properties"]["note"], SMALL["properties"]["note"])
        self.assertNotIn("oneOf", keywords(schema))
        self.assertEqual(schema["properties"]["kind"]["anyOf"][0]["anyOf"][0]["required"], ["kind", "x"])

    def test_the_analysis_schema_becomes_strict_everywhere(self) -> None:
        strict = mp._openai_strict(ANALYSIS_RESPONSE_SCHEMA)

        def check(node):
            if isinstance(node, dict):
                if isinstance(node.get("properties"), dict):
                    self.assertEqual(set(node["required"]), set(node["properties"]))
                for value in node.values():
                    check(value)
            elif isinstance(node, list):
                for value in node:
                    check(value)

        check(strict)
        self.assertNotIn("oneOf", keywords(strict))
        self.assertIsNot(strict, ANALYSIS_RESPONSE_SCHEMA)
        self.assertIn("oneOf", keywords(ANALYSIS_RESPONSE_SCHEMA))  # the original is untouched

    def test_a_non_strict_format_is_sent_as_given(self) -> None:
        fake = SyncFake("openai")
        client = chat_client(RoleModel("analysis", "openai", "gpt-6.1-sol", "high"),
                             asynchronous=False, sdk_client=fake)
        client.chat.completions.create(model="gpt-6.1-sol", messages=[{"role": "user", "content": "x"}],
                                       response_format=response_format(SMALL, strict=False))
        self.assertEqual(fake.requests[0]["text"]["format"]["schema"], SMALL)

    def test_the_nulls_the_strict_form_forced_are_dropped_and_asked_ones_kept(self) -> None:
        reply = {"korean": "세척", "note": None, "tags": None, "kind": {"kind": "b", "y": None}}
        _, content = call("openai", "gpt-6.1-sol", SMALL, json.dumps(reply))
        self.assertEqual(json.loads(content), {"korean": "세척", "note": None, "kind": {"kind": "b"}})

    def test_a_reply_that_is_not_json_is_passed_through_for_the_server_to_refuse(self) -> None:
        _, content = call("openai", "gpt-6.1-sol", SMALL, "not json")
        self.assertEqual(content, "not json")


class AnthropicTests(unittest.TestCase):
    def test_a_schema_inside_the_documented_limits_is_a_grammar_with_any_of(self) -> None:
        fake, _ = call("anthropic", "claude-opus-5-5", SMALL)
        request = fake.requests[0]
        self.assertEqual(request["output_config"]["format"]["schema"], mp._any_of(SMALL))
        self.assertEqual(len(request["system"]), 1)

    def test_a_schema_over_the_limits_goes_as_an_instruction(self) -> None:
        fake, _ = call("anthropic", "claude-opus-5-5", ANALYSIS_RESPONSE_SCHEMA)
        request = fake.requests[0]
        self.assertNotIn("format", request.get("output_config", {}))
        self.assertEqual(request["system"][0]["text"], "RULES")
        instruction = request["system"][1]["text"]
        self.assertIn("test_v1", instruction)
        self.assertEqual(json.loads(instruction.split("\n", 1)[1]), ANALYSIS_RESPONSE_SCHEMA)

    def test_an_unasked_null_in_an_instruction_reply_is_dropped(self) -> None:
        _, content = call("anthropic", "claude-opus-5-5", SMALL, '{"korean": "a", "tags": null}')
        self.assertEqual(json.loads(content), {"korean": "a"})


class GeminiTests(unittest.TestCase):
    def test_a_small_schema_is_a_response_schema_with_any_of(self) -> None:
        fake, _ = call("google", "gemini-3.8-flash", SMALL)
        config = fake.requests[0]["config"]
        self.assertEqual(config["response_json_schema"], mp._any_of(SMALL))
        self.assertEqual(config["system_instruction"], "RULES")

    def test_a_schema_over_the_measured_size_is_json_mode_and_an_instruction(self) -> None:
        fake, _ = call("google", "gemini-3.8-flash", ANALYSIS_RESPONSE_SCHEMA)
        config = fake.requests[0]["config"]
        self.assertNotIn("response_json_schema", config)
        self.assertEqual(config["response_mime_type"], "application/json")
        first, schema_part = config["system_instruction"].split("\n\n", 1)
        self.assertEqual(first, "RULES")
        self.assertEqual(json.loads(schema_part.split("\n", 1)[1]), ANALYSIS_RESPONSE_SCHEMA)


class AnalysisOutputLimitTests(unittest.TestCase):
    def request(self) -> dict:
        return build_protocol_analysis_chat_request(
            model="m", reasoning_effort="high", system_prompt=ANALYSIS_SYSTEM_PROMPT,
            input_json="{}", response_schema=ANALYSIS_RESPONSE_SCHEMA)

    def test_the_analysis_request_names_its_output_limit(self) -> None:
        self.assertEqual(self.request()["max_completion_tokens"], ANALYSIS_MAX_OUTPUT_TOKENS)
        self.assertGreater(ANALYSIS_MAX_OUTPUT_TOKENS, mp.DEFAULT_MAX_OUTPUT_TOKENS)

    def test_each_adapter_passes_it_on_within_the_models_output_limit(self) -> None:
        request = self.request()
        sent = {}
        for provider, model, key in (("openai", "gpt-6.1-sol", "max_output_tokens"),
                                     ("anthropic", "claude-opus-5-5", "max_tokens")):
            fake = SyncFake(provider)
            chat_client(RoleModel("analysis", provider, model, "high"), asynchronous=False,
                        sdk_client=fake).chat.completions.create(**{**request, "model": model})
            sent[provider] = fake.requests[0][key]
        fake = SyncFake("google")
        chat_client(RoleModel("analysis", "google", "gemini-3.8-flash", "high"), asynchronous=False,
                    sdk_client=fake).chat.completions.create(**{**request, "model": "gemini-3.8-flash"})
        sent["google"] = fake.requests[0]["config"]["max_output_tokens"]
        thinking = ANALYSIS_MAX_OUTPUT_TOKENS + mp.THINKING_ALLOWANCE_TOKENS
        # Gemini's room follows its thinking level (lane G): high, capped at
        # the model's output limit.
        gemini = min(ANALYSIS_MAX_OUTPUT_TOKENS + mp.GEMINI_THINKING_ALLOWANCE_TOKENS["high"],
                     mp.GEMINI_MAX_OUTPUT_TOKENS)
        self.assertEqual(sent, {"openai": thinking, "anthropic": thinking, "google": gemini})
        self.assertLessEqual(sent["google"], 65_536)  # Gemini 3.8 Flash's output limit


class AnalysisThroughEveryAdapterTests(unittest.TestCase):
    """The analysis model, through each adapter, still parses as before."""

    PAGE = "Probe Protocol\n1. Add 500 µL of buffer A to the tube.\nWear gloves."

    def extraction(self) -> ProtocolPdfExtraction:
        return ProtocolPdfExtraction(
            original_filename="probe.pdf", byte_size=1, sha256="d" * 64,
            media_type="application/pdf", page_count=1, encrypted=False,
            metadata=ProtocolPdfMetadata(None, None, None, None, None, None, None),
            pages=(ProtocolPdfPage(1, self.PAGE, False),))

    def reply(self, *, strict_nulls: bool) -> str:
        evidence = {"source_page_number": 1, "source_excerpt": "Probe Protocol"}
        step_evidence = {"source_page_number": 1,
                         "source_excerpt": "1. Add 500 µL of buffer A to the tube."}
        if strict_nulls:
            evidence = {**evidence, "location_detail": None}
        protocol = {
            "protocol_id": "probe",
            "metadata": {"title": "Probe Protocol", "original_language": "en", "evidence": evidence,
                         **({"authors": None, "created_date": None} if strict_nulls else {})},
            "before_start": [], "materials": [], "equipment": [], "constructs": [],
            "description": None,
            "sections": [{"section_id": "s1", "title_source_text": "Probe Protocol",
                          "evidence": evidence, "steps": [{
                              "step_id": "step-1", "source_label": "1",
                              "instruction_source_text": "Add 500 µL of buffer A to the tube.",
                              "evidence": step_evidence,
                              **({"sub_actions": None, "notes": None} if strict_nulls else {})}]}],
        }
        return json.dumps({"analysis_schema_version": 1, "pdf_sha256": "d" * 64,
                           "capability_policy_id": "p1-conservative", "protocol": protocol})

    def test_a_strict_openai_reply_with_forced_nulls_parses(self) -> None:
        # The nulls are what a strict grammar makes a model write for an
        # optional list; the domain decoder refuses a null list.
        with self.assertRaises(Exception):
            parse_protocol_analysis_response(self.reply(strict_nulls=True), self.extraction())
        for provider, model in (("openai", "gpt-6.1-sol"), ("anthropic", "claude-opus-5-5"),
                                ("google", "gemini-3.8-flash")):
            with self.subTest(provider=provider):
                fake = SyncFake(provider, self.reply(strict_nulls=True))
                client = chat_client(RoleModel("analysis", provider, model, "high"),
                                     asynchronous=False, sdk_client=fake)
                content = OpenAICompatibleProtocolAnalysisModel(client, model, "high").analyze(
                    system_prompt=ANALYSIS_SYSTEM_PROMPT, input_json="{}",
                    response_schema=copy.deepcopy(ANALYSIS_RESPONSE_SCHEMA))
                draft = parse_protocol_analysis_response(content, self.extraction())
                self.assertEqual(draft.protocol.sections[0].steps[0].source_label, "1")


if __name__ == "__main__":
    unittest.main()


ANSWER_LIKE = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "spoken_answer": {"type": "string", "minLength": 1, "maxLength": 400},
        "evidence_ids": {"type": "array", "items": {"type": "string", "enum": ["e1", "e2"]},
                         "uniqueItems": True, "minItems": 1},
        "pair": {"type": "array", "items": {"type": "integer", "minimum": 1, "maximum": 999},
                 "minItems": 2, "maxItems": 2},
        # A property that happens to be named like a keyword is kept.
        "minimum": {"type": "string"},
    },
    "required": ["spoken_answer", "evidence_ids", "pair", "minimum"],
}


class UnsupportedKeywordTests(unittest.TestCase):
    """Lane G (2026-10-05): the answer brain's schema carries ``uniqueItems``.

    On the real APIs OpenAI strict answered HTTP 400 "'uniqueItems' is not
    permitted" and Anthropic HTTP 400 "For 'array' type, property
    'uniqueItems' is not supported"; Gemini took it. Each adapter now drops
    what its grammar refuses (Anthropic: the structured-outputs page's list).
    Contract-tested against fake SDK clients.
    """

    def test_openai_strict_drops_unique_items_only(self) -> None:
        fake, _ = call("openai", "gpt-6-luna", ANSWER_LIKE)
        schema = fake.requests[0]["text"]["format"]["schema"]
        self.assertNotIn("uniqueItems", keywords(schema))
        evidence = schema["properties"]["evidence_ids"]
        self.assertEqual(evidence["items"]["enum"], ["e1", "e2"])
        self.assertEqual(evidence["minItems"], 1)
        self.assertEqual(schema["properties"]["pair"]["maxItems"], 2)
        self.assertEqual(schema["properties"]["spoken_answer"]["maxLength"], 400)
        self.assertIn("uniqueItems", keywords(ANSWER_LIKE))  # the original is untouched

    def test_anthropic_drops_the_documented_unsupported_constraints(self) -> None:
        fake, _ = call("anthropic", "claude-sonnet-5-5", ANSWER_LIKE)
        schema = fake.requests[0]["output_config"]["format"]["schema"]
        for keyword in ("uniqueItems", "maxItems", "minLength", "maxLength", "maximum"):
            self.assertNotIn(keyword, keywords(schema))
        self.assertEqual(schema["properties"]["evidence_ids"]["minItems"], 1)
        self.assertNotIn("minItems", schema["properties"]["pair"])
        self.assertEqual(schema["properties"]["minimum"], {"type": "string"})
        self.assertEqual(schema["required"], ANSWER_LIKE["required"])

    def test_gemini_sends_the_schema_as_before(self) -> None:
        fake, _ = call("google", "gemini-3.8-flash", ANSWER_LIKE)
        self.assertEqual(fake.requests[0]["config"]["response_json_schema"], mp._any_of(ANSWER_LIKE))

    def test_the_answer_brain_reply_still_passes_the_brains_own_gate(self) -> None:
        import asyncio

        from voiney_lab.multi_brain import (
            BrainFact, BrainSnapshot, HybridMultiBrain, MultiBrainSettings,
        )

        snapshot = BrainSnapshot(
            configuration_id=1, session_id="s", turn_id=1, generation_id=1, workflow_revision=1,
            protocol_id="p", document_sha256="", step_id="step-3", step_index=2, language="ko",
            transcript="용액 A 얼마나 넣어?", intent_kind="related_question", question_kind=None,
            requested_entities=(), question_dimensions=(),
            facts=(BrainFact("e1", "step", "Wash the band with 500 µL of solution A.", 1),),
        )
        reply = json.dumps({"spoken_answer": "용액 A 500 µL를 넣어요.", "display_answer": "500 µL",
                            "evidence_ids": ["e1"], "limitations": []}, ensure_ascii=False)
        for provider, model, where in (("openai", "gpt-6-luna", "text"),
                                       ("anthropic", "claude-sonnet-5-5", "output_config")):
            with self.subTest(provider=provider):
                fake = SyncFake(provider, reply)

                class AsyncFake:
                    pass

                async_fake = AsyncFake()
                if provider == "openai":
                    async def create(**params):
                        return fake._openai(**params)
                    async_fake.responses = SimpleNamespace(create=create)
                else:
                    async def create(**params):
                        return fake._anthropic(**params)
                    async_fake.messages = SimpleNamespace(create=create)
                client = chat_client(RoleModel("answer", provider, model, "none"), sdk_client=async_fake)
                brain = HybridMultiBrain(client, MultiBrainSettings(True, model))
                output = asyncio.run(brain._answer(snapshot))
                self.assertEqual(output.evidence_ids, ("e1",))
                self.assertNotIn("uniqueItems", keywords(fake.requests[0][where]))


class GeminiThinkingRoomTests(unittest.TestCase):
    """Lane G (2026-10-05): Gemini counts thought tokens in max_output_tokens.

    Live, a gemini-3.8-flash translation batch at "high" thought ~11,800
    tokens and stopped with finish reason MAX_TOKENS at 8,192 + 4,096, so its
    JSON was cut. The room now follows the thinking level. Contract-tested.
    """

    def sent(self, reasoning, **extra) -> dict:
        fake = SyncFake("google")
        chat_client(RoleModel("translation", "google", "gemini-3.8-flash", reasoning), asynchronous=False,
                    sdk_client=fake).chat.completions.create(
            model="gemini-3.8-flash", messages=[{"role": "user", "content": "x"}], **extra)
        return fake.requests[0]["config"]

    def test_the_room_follows_the_thinking_level(self) -> None:
        base = mp.DEFAULT_MAX_OUTPUT_TOKENS
        for reasoning, level, room in (("none", "low", 4096), ("low", "low", 4096),
                                       ("medium", "medium", 16384), ("high", "high", 32768),
                                       ("xhigh", "high", 32768)):
            with self.subTest(reasoning=reasoning):
                config = self.sent(reasoning)
                self.assertEqual(config["thinking_config"], {"thinking_level": level})
                self.assertEqual(config["max_output_tokens"], base + room)

    def test_an_unset_level_sends_no_thinking_config_and_gets_the_high_room(self) -> None:
        config = self.sent(None)
        self.assertNotIn("thinking_config", config)
        self.assertEqual(config["max_output_tokens"], mp.DEFAULT_MAX_OUTPUT_TOKENS + 32768)

    def test_a_caller_limit_is_kept_and_the_total_capped(self) -> None:
        self.assertEqual(self.sent("low", max_tokens=400)["max_output_tokens"], 400 + 4096)
        self.assertEqual(self.sent("high", max_completion_tokens=60_000)["max_output_tokens"],
                         mp.GEMINI_MAX_OUTPUT_TOKENS)

