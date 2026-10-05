"""Which provider and model each role calls, and one client shape for all four.

Decision of 2026-10-04 (lane M1): the model behind each role is chosen per
role -- performance first, cost second -- from our own evaluation sets. A role
is set by three settings, ``VOINEY_LAB_<ROLE>_PROVIDER``, ``_MODEL`` and
``_REASONING``:

* router       -- the LLM router's one call per voice turn (tools)
* answer       -- the brain's answers, the approved-document answer, the
                  read-only multi-brain roles and the hand-off worker
* translation  -- a revision's Korean, and the reader translation of a step
* analysis     -- the structured PDF protocol analysis
* report       -- the experiment report's prose
* supplemental -- outside-the-PDF explanations and the web reference search

Every default is what the code called before: xAI, and the same model, so an
environment that sets none of these behaves exactly as before. Changing a
default is decided by people from the evaluation.

The adapters only move a provider's request and reply to and from the shape
the code already reads -- an OpenAI-style ``client.chat.completions.create``
(and, for the supplemental explanation, ``client.responses.create``). Nothing
here judges a reply: the server's validation of a tool proposal
(``apply_tool_proposal``), its answer checks and the translation checks are
the same whichever provider wrote it.

* xai       -- the OpenAI SDK against ``XAI_BASE_URL`` (unchanged).
* openai    -- the OpenAI SDK's Responses API: GPT-6.1 Sol takes tools only
               there, and GPT-6 Luna takes tools in Chat Completions only at
               reasoning "none" (developers.openai.com, latest-model guide).
* anthropic -- the Anthropic SDK's Messages API, with ``cache_control`` set
               on the stable system blocks.
* google    -- the google-genai SDK against the Gemini API
               (generativelanguage), with its implicit caching.

Provider keys keep their SDKs' standard names: XAI_API_KEY,
ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

PROVIDERS = ("xai", "anthropic", "openai", "google")
ROLES = ("router", "answer", "translation", "analysis", "report", "supplemental")
#: The reasoning levels a role may name; each adapter maps them (below).
REASONING_LEVELS = ("none", "low", "medium", "high", "xhigh", "max")

#: role -> (provider setting, model setting, reasoning setting)
ROLE_SETTINGS: Mapping[str, tuple[str, str, str]] = {
    "router": (
        "VOINEY_LAB_ROUTER_PROVIDER", "VOINEY_LAB_ROUTER_MODEL",
        "VOINEY_LAB_ROUTER_REASONING",
    ),
    "answer": (
        "VOINEY_LAB_ANSWER_PROVIDER", "VOINEY_LAB_ANSWER_MODEL",
        "VOINEY_LAB_ANSWER_REASONING",
    ),
    "translation": (
        "VOINEY_LAB_TRANSLATION_PROVIDER", "VOINEY_LAB_TRANSLATION_MODEL",
        "VOINEY_LAB_TRANSLATION_REASONING",
    ),
    "analysis": (
        "VOINEY_LAB_ANALYSIS_PROVIDER", "VOINEY_LAB_ANALYSIS_MODEL",
        "VOINEY_LAB_ANALYSIS_REASONING",
    ),
    "report": (
        "VOINEY_LAB_REPORT_PROVIDER", "VOINEY_LAB_REPORT_MODEL",
        "VOINEY_LAB_REPORT_REASONING",
    ),
    "supplemental": (
        "VOINEY_LAB_SUPPLEMENTAL_PROVIDER", "VOINEY_LAB_SUPPLEMENTAL_MODEL",
        "VOINEY_LAB_SUPPLEMENTAL_REASONING",
    ),
}
#: The model a role calls when its setting is empty -- what it called before
#: lane M1. None: the role has no single default; each caller keeps its own
#: (answer: the brain requires one, the worker uses grok-4 and the
#: multi-brain roles grok-4.6; analysis requires one; report falls back to
#: the supplemental model, then grok-4.6).
DEFAULT_MODELS: Mapping[str, str | None] = {
    "router": "grok-4.20-0309-non-reasoning",
    "answer": None,
    "translation": "grok-4.6",
    "analysis": None,
    "report": None,
    "supplemental": "grok-4.6",
}
API_KEY_SETTINGS: Mapping[str, str] = {
    "xai": "XAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google": "GEMINI_API_KEY",
}
#: Google: the Gemini API (GEMINI_API_KEY) by default, or Vertex AI -- now
#: "Gemini Enterprise Agent Platform" -- when the google-genai SDK's own
#: switch is on. These are the SDK's standard names (googleapis.github.io/
#: python-genai): GOOGLE_GENAI_USE_ENTERPRISE (GOOGLE_GENAI_USE_VERTEXAI is
#: its legacy name; the newer one wins), GOOGLE_CLOUD_PROJECT and
#: GOOGLE_CLOUD_LOCATION (default "global", where gemini-3.8-flash is served),
#: and GOOGLE_API_KEY for an express-mode Vertex key. Without a key, Vertex
#: authenticates with Application Default Credentials.
GOOGLE_VERTEX_SWITCHES = ("GOOGLE_GENAI_USE_ENTERPRISE", "GOOGLE_GENAI_USE_VERTEXAI")
GOOGLE_PROJECT_SETTING = "GOOGLE_CLOUD_PROJECT"
GOOGLE_LOCATION_SETTING = "GOOGLE_CLOUD_LOCATION"
GOOGLE_VERTEX_KEY_SETTING = "GOOGLE_API_KEY"
DEFAULT_GOOGLE_LOCATION = "global"
XAI_BASE_URL_SETTING = "XAI_BASE_URL"
DEFAULT_XAI_BASE_URL = "https://api.x.ai/v1"
#: Output room for thinking, where a provider counts it in the output limit
#: (Anthropic's max_tokens, OpenAI's max_output_tokens, Gemini's
#: max_output_tokens). Added only when the role thinks.
THINKING_ALLOWANCE_TOKENS = 4096
#: What a reply may be given when the caller sets no limit.
DEFAULT_MAX_OUTPUT_TOKENS = 8192
#: Gemini's thinking room by thinking level. Its max_output_tokens includes
#: the thought tokens (ai.google.dev, thinking guide), and at "high" one
#: translation batch thought ~11,800 tokens and was cut at 8,192 + 4,096
#: (finish reason MAX_TOKENS, lane G, live); the guide gives no number per
#: level, so these are rooms, not measurements of need. Gemini 3.x cannot stop
#: thinking ("none" thinks at low), and an unset level is the model's default
#: (not documented for 3.8 Flash), given the high room.
GEMINI_THINKING_ALLOWANCE_TOKENS = {"low": 4096, "medium": 16384, "high": 32768}
#: gemini-3.8-flash's output limit (model page, lane P3).
GEMINI_MAX_OUTPUT_TOKENS = 65536


class ModelProviderError(RuntimeError):
    """A provider call failed. Carries the provider, the HTTP status, a kind.

    ``kind`` is one of "payment_required" (a prepaid balance is spent:
    Gemini answers 402), "rate_limited", "timeout", "authentication",
    "invalid_request", "server", "connection" or "other". The message never
    carries a key or the prompt.
    """

    def __init__(self, provider: str, kind: str, status_code: int | None = None) -> None:
        super().__init__(f"{provider} {kind}" + (f" (HTTP {status_code})" if status_code else ""))
        self.provider = provider
        self.kind = kind
        self.status_code = status_code


@dataclass(frozen=True)
class RoleModel:
    """One role's provider, model and reasoning, as the settings give them."""

    role: str
    provider: str = "xai"
    model: str | None = None
    reasoning: str | None = None

    @classmethod
    def from_environment(
        cls, role: str, environment: Mapping[str, str] | None = None,
    ) -> "RoleModel":
        if role not in ROLE_SETTINGS:
            raise ValueError(f"unknown model role: {role}")
        env = os.environ if environment is None else environment
        provider_name, model_name, reasoning_name = ROLE_SETTINGS[role]
        provider = env.get(provider_name, "").strip().casefold() or "xai"
        if provider not in PROVIDERS:
            raise ValueError(
                f"{provider_name} must be one of {', '.join(PROVIDERS)}"
            )
        model = env.get(model_name, "").strip() or DEFAULT_MODELS[role]
        reasoning = env.get(reasoning_name, "").strip().casefold() or None
        if reasoning is not None and reasoning not in REASONING_LEVELS:
            raise ValueError(
                f"{reasoning_name} must be one of {', '.join(REASONING_LEVELS)}"
            )
        return cls(role, provider, model, reasoning)

    @property
    def api_key_setting(self) -> str:
        return API_KEY_SETTINGS[self.provider]

    def has_key(self, environment: Mapping[str, str] | None = None) -> bool:
        """Whether the role can authenticate (Vertex: a key, or a project for ADC)."""

        env = os.environ if environment is None else environment
        if self.provider == "google" and google_uses_vertex(env):
            return bool(
                env.get(GOOGLE_VERTEX_KEY_SETTING, "").strip()
                or env.get(GOOGLE_PROJECT_SETTING, "").strip()
            )
        return bool(env.get(self.api_key_setting, "").strip())


def google_uses_vertex(environment: Mapping[str, str] | None = None) -> bool:
    """Whether Google roles go to Vertex AI: the SDK's switch, newer name first."""

    env = os.environ if environment is None else environment
    for name in GOOGLE_VERTEX_SWITCHES:
        raw = env.get(name, "").strip().casefold()
        if raw:
            return raw in {"1", "true", "yes", "on"}
    return False


def google_client_options(environment: Mapping[str, str] | None = None) -> dict[str, Any]:
    """The google-genai Client arguments: the Gemini API, or Vertex AI.

    Vertex with GOOGLE_API_KEY is express mode (no project or location);
    otherwise GOOGLE_CLOUD_PROJECT (required) and GOOGLE_CLOUD_LOCATION, with
    Application Default Credentials.
    """

    env = os.environ if environment is None else environment
    if not google_uses_vertex(env):
        key = env.get(API_KEY_SETTINGS["google"], "").strip()
        if not key:
            raise RuntimeError(f"{API_KEY_SETTINGS['google']} is required")
        return {"enterprise": False, "api_key": key}
    key = env.get(GOOGLE_VERTEX_KEY_SETTING, "").strip()
    if key:
        return {"enterprise": True, "api_key": key}
    project = env.get(GOOGLE_PROJECT_SETTING, "").strip()
    if not project:
        raise RuntimeError(
            f"Vertex AI needs {GOOGLE_VERTEX_KEY_SETTING} or {GOOGLE_PROJECT_SETTING}"
        )
    return {
        "enterprise": True, "project": project,
        "location": env.get(GOOGLE_LOCATION_SETTING, "").strip() or DEFAULT_GOOGLE_LOCATION,
    }


def _api_key(role_model: RoleModel, environment: Mapping[str, str] | None) -> str:
    env = os.environ if environment is None else environment
    key = env.get(role_model.api_key_setting, "").strip()
    if not key:
        raise RuntimeError(f"{role_model.api_key_setting} is required")
    return key


def xai_base_url(environment: Mapping[str, str] | None = None) -> str:
    env = os.environ if environment is None else environment
    return env.get(XAI_BASE_URL_SETTING, DEFAULT_XAI_BASE_URL).rstrip("/")


def chat_client(
    role_model: RoleModel,
    *,
    asynchronous: bool = True,
    timeout: Any = None,
    max_retries: int = 0,
    environment: Mapping[str, str] | None = None,
    sdk_client: Any = None,
) -> Any:
    """A client for ``role_model`` with ``.chat.completions.create``.

    For xAI this is the OpenAI SDK client the code always built (same base
    URL, key, retries and timeout), so a default role sends exactly the
    request it sent before. ``sdk_client`` replaces the provider SDK's
    client (contract tests pass a fake one).
    """

    provider = role_model.provider
    if provider == "xai":
        client = sdk_client
        if client is None:
            from openai import AsyncOpenAI, OpenAI

            options: dict[str, Any] = {
                "base_url": xai_base_url(environment) + "/",
                "api_key": _api_key(role_model, environment),
                "max_retries": max_retries,
            }
            if timeout is not None:
                options["timeout"] = timeout
            client = (AsyncOpenAI if asynchronous else OpenAI)(**options)
        if role_model.reasoning is None:
            return client
        return _XaiReasoning(client, role_model.reasoning, asynchronous)
    if provider == "openai":
        client = sdk_client
        if client is None:
            from openai import AsyncOpenAI, OpenAI

            options = {"api_key": _api_key(role_model, environment), "max_retries": max_retries}
            if timeout is not None:
                options["timeout"] = timeout
            client = (AsyncOpenAI if asynchronous else OpenAI)(**options)
        return _Facade(_OpenAIResponsesBackend(client, role_model), asynchronous)
    if provider == "anthropic":
        client = sdk_client
        if client is None:
            import anthropic

            options = {"api_key": _api_key(role_model, environment), "max_retries": max_retries}
            if timeout is not None:
                options["timeout"] = timeout
            client = (anthropic.AsyncAnthropic if asynchronous else anthropic.Anthropic)(**options)
        return _Facade(_AnthropicBackend(client, role_model), asynchronous)
    if provider == "google":
        client = sdk_client
        if client is None:
            from google import genai
            from google.genai import types as genai_types

            options = {}
            if timeout is not None and isinstance(timeout, (int, float)):
                options["http_options"] = genai_types.HttpOptions(timeout=int(timeout * 1000))
            client = genai.Client(**google_client_options(environment), **options)
        return _Facade(_GeminiBackend(client, role_model), asynchronous)
    raise ValueError(f"unknown provider: {provider}")


# --- The shape the code reads -------------------------------------------------------

def _usage(
    prompt: int = 0, completion: int = 0, *, cached: int = 0, cache_write: int = 0,
    reasoning: int = 0,
) -> SimpleNamespace:
    """OpenAI-style usage. ``prompt_tokens`` counts every input token,
    cached or not; ``cache_write_tokens`` and ``reasoning_tokens`` ride along
    for cost accounting."""

    return SimpleNamespace(
        prompt_tokens=prompt, completion_tokens=completion,
        total_tokens=prompt + completion,
        prompt_tokens_details=SimpleNamespace(cached_tokens=cached),
        cache_write_tokens=cache_write, reasoning_tokens=reasoning,
    )


def _tool_call(index: int, call_id: str, name: str, arguments: str) -> SimpleNamespace:
    return SimpleNamespace(
        index=index, id=call_id, type="function",
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def _completion(
    *, content: str, tool_calls: Sequence[SimpleNamespace], usage: Any, model: str,
    finish_reason: str = "stop",
) -> SimpleNamespace:
    message = SimpleNamespace(
        role="assistant", content=content, tool_calls=list(tool_calls) or None,
    )
    return SimpleNamespace(
        model=model, usage=usage,
        choices=[SimpleNamespace(index=0, message=message, finish_reason=finish_reason)],
        # What a Responses-API reader (the supplemental explanation) reads.
        output_text=content,
        output=[SimpleNamespace(
            type="message",
            content=[SimpleNamespace(type="output_text", text=content)],
        )],
    )


def _chunk(
    model: str, *, content: str | None = None, tool_calls: list | None = None,
    usage: Any = None,
) -> SimpleNamespace:
    choices = [] if content is None and tool_calls is None else [
        SimpleNamespace(index=0, delta=SimpleNamespace(content=content, tool_calls=tool_calls))
    ]
    return SimpleNamespace(model=model, choices=choices, usage=usage)


_ACCEPTED = frozenset({
    "model", "messages", "tools", "tool_choice", "response_format", "temperature",
    "max_tokens", "max_completion_tokens", "stream", "stream_options",
    "reasoning_effort", "timeout", "parallel_tool_calls",
})


@dataclass(frozen=True)
class _Request:
    """One chat-completions request, read once for every adapter."""

    model: str
    messages: tuple[Mapping[str, Any], ...]
    tools: tuple[Mapping[str, Any], ...]
    tool_choice: Any
    response_format: Mapping[str, Any] | None
    temperature: float | None
    max_tokens: int | None
    stream: bool
    reasoning: str | None
    timeout: Any
    parallel_tool_calls: bool | None

    @classmethod
    def read(cls, role_model: RoleModel, kwargs: Mapping[str, Any]) -> "_Request":
        unknown = set(kwargs) - _ACCEPTED
        if unknown:
            raise TypeError(f"unsupported request arguments: {', '.join(sorted(unknown))}")
        model = kwargs.get("model") or role_model.model
        if not model:
            raise ValueError(f"no model for the {role_model.role} role")
        reasoning = kwargs.get("reasoning_effort") or role_model.reasoning
        timeout = kwargs.get("timeout")
        if timeout is not None and not isinstance(timeout, (int, float)):
            # An httpx.Timeout from a caller written for the OpenAI SDK: the
            # other SDKs take seconds, and the read limit is the one that binds.
            timeout = getattr(timeout, "read", None) or getattr(timeout, "timeout", None)
        return cls(
            model=str(model),
            messages=tuple(kwargs.get("messages") or ()),
            tools=tuple(kwargs.get("tools") or ()),
            tool_choice=kwargs.get("tool_choice"),
            response_format=kwargs.get("response_format"),
            temperature=kwargs.get("temperature"),
            max_tokens=kwargs.get("max_completion_tokens") or kwargs.get("max_tokens"),
            stream=bool(kwargs.get("stream")),
            reasoning=reasoning,
            timeout=timeout,
            parallel_tool_calls=kwargs.get("parallel_tool_calls"),
        )

    def output_limit(self, thinks: bool) -> int:
        limit = self.max_tokens or DEFAULT_MAX_OUTPUT_TOKENS
        return limit + (THINKING_ALLOWANCE_TOKENS if thinks else 0)

    def json_schema(self) -> tuple[str, Mapping[str, Any], bool] | None:
        """(name, schema, strict) of a json_schema response format, or None."""

        if not self.response_format or self.response_format.get("type") != "json_schema":
            return None
        spec = self.response_format.get("json_schema") or {}
        return (
            str(spec.get("name") or "response"), spec.get("schema") or {},
            bool(spec.get("strict", False)),
        )

    @property
    def wants_json_object(self) -> bool:
        return bool(self.response_format) and self.response_format.get("type") == "json_object"


# --- Structured output across providers ---------------------------------------
#
# A json_schema response format is written once, for xAI's OpenAI-compatible
# strict mode. The other three grammars each take a different subset, and on
# the PDF analysis schema (196 properties, 28 definitions, oneOf, 86 optional
# properties) all three refused the request outright (lane P3, 2026-10-05):
# Anthropic "Schema type 'oneOf' is not supported", OpenAI "'required' ... must
# include every key in properties", Gemini a bare 400 INVALID_ARGUMENT. So
# each adapter sends the same schema in the form its provider takes, and where
# no grammar can hold it, sends it as an instruction instead. That is a
# request-shape translation only: the server still validates every reply
# against the original contract, exactly as before.

#: Anthropic's documented grammar limits (platform.claude.com, structured
#: outputs, "Numeric complexity limits"): optional parameters and parameters
#: with union types, counted across one request.
ANTHROPIC_MAX_OPTIONAL_PROPERTIES = 24
ANTHROPIC_MAX_UNION_PROPERTIES = 16
#: Gemini publishes no number ("the API may reject very large or deeply nested
#: schemas"). Measured on Vertex AI, lane P3: a 26-property, 2-definition
#: schema accepted, the 196-property analysis schema refused; the small
#: schemas the other roles send stay well under this.
GEMINI_MAX_SCHEMA_PROPERTIES = 100

_SCHEMA_INSTRUCTION = (
    "Return exactly one JSON object, with no prose, Markdown or code fences, "
    "that conforms to the JSON Schema named {name} below. Omit an optional "
    "property instead of inventing a value.\n"
)


def _definition(root: Mapping[str, Any], node: Any) -> Any:
    seen = 0
    while isinstance(node, Mapping) and "$ref" in node and seen < 64:
        name = str(node["$ref"]).rsplit("/", 1)[-1]
        node = (root.get("$defs") or {}).get(name, {})
        seen += 1
    return node


def _schema_counts(root: Mapping[str, Any]) -> tuple[int, int, int]:
    """(properties, optional properties, union-typed properties), each
    definition counted once however often it is referenced."""

    counts = [0, 0, 0]
    seen: set[str] = set()

    def is_union(node: Any) -> bool:
        node = _definition(root, node)
        return isinstance(node, Mapping) and (
            "anyOf" in node or "oneOf" in node or isinstance(node.get("type"), list)
        )

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, Mapping):
            return
        if "$ref" in node:
            name = str(node["$ref"]).rsplit("/", 1)[-1]
            if name not in seen:
                seen.add(name)
                walk((root.get("$defs") or {}).get(name, {}))
            return
        properties = node.get("properties")
        if isinstance(properties, Mapping):
            required = set(node.get("required") or ())
            counts[0] += len(properties)
            counts[1] += sum(1 for key in properties if key not in required)
            counts[2] += sum(1 for value in properties.values() if is_union(value))
        for key, value in node.items():
            if key != "$defs":
                walk(value)

    walk(root)
    return counts[0], counts[1], counts[2]


def _any_of(node: Any) -> Any:
    """A copy with every oneOf as anyOf: the grammars take anyOf only. The
    analysis schema's oneOf branches each carry a distinct const kind, so the
    two accept the same documents."""

    if isinstance(node, list):
        return [_any_of(item) for item in node]
    if not isinstance(node, Mapping):
        return node
    return {("anyOf" if key == "oneOf" else key): _any_of(value) for key, value in node.items()}


#: Keywords a grammar refuses outright, so a schema that carries one is sent
#: without it (the server still checks the reply against the original).
#: OpenAI strict: "'uniqueItems' is not permitted" (HTTP 400, lane G, live,
#: the answer brain's schema). Anthropic: the structured-outputs page's list
#: of unsupported constraints (numeric, string length, array constraints
#: beyond minItems 0 or 1); "For 'array' type, property 'uniqueItems' is not
#: supported" was the live 400 (lane G).
OPENAI_STRICT_UNSUPPORTED_KEYWORDS = frozenset({"uniqueItems"})
ANTHROPIC_UNSUPPORTED_KEYWORDS = frozenset({
    "uniqueItems", "maxItems", "minLength", "maxLength", "minimum", "maximum",
    "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
})


def _without_keywords(node: Any, keywords: frozenset[str]) -> Any:
    """A copy without ``keywords`` (and, for Anthropic, without a minItems
    above 1, which its grammar does not take)."""

    if isinstance(node, list):
        return [_without_keywords(item, keywords) for item in node]
    if not isinstance(node, Mapping):
        return node
    out = {}
    for key, value in node.items():
        if key in keywords and not isinstance(value, Mapping):
            continue
        if (
            key == "minItems" and keywords is ANTHROPIC_UNSUPPORTED_KEYWORDS
            and isinstance(value, int) and value > 1
        ):
            continue
        out[key] = _without_keywords(value, keywords)
    return out


def _allows_null(root: Mapping[str, Any], node: Any) -> bool:
    node = _definition(root, node)
    if not isinstance(node, Mapping):
        return False
    kind = node.get("type")
    if kind == "null" or (isinstance(kind, list) and "null" in kind):
        return True
    return any(_allows_null(root, branch) for branch in node.get("anyOf") or node.get("oneOf") or ())


def _openai_strict(root: Mapping[str, Any]) -> dict[str, Any]:
    """OpenAI strict form: every property required, an optional one nullable."""

    def convert(node: Any) -> Any:
        if isinstance(node, list):
            return [convert(item) for item in node]
        if not isinstance(node, Mapping):
            return node
        out = {key: convert(value) for key, value in node.items()}
        properties = node.get("properties")
        if isinstance(properties, Mapping):
            required = set(node.get("required") or ())
            out["properties"] = {
                key: (
                    out["properties"][key]
                    if key in required or _allows_null(root, value)
                    else {"anyOf": [out["properties"][key], {"type": "null"}]}
                )
                for key, value in properties.items()
            }
            out["required"] = list(properties)
        return out

    return convert(_any_of(root))


def _object_branch(root: Mapping[str, Any], node: Any, value: Mapping[str, Any]) -> Any:
    node = _definition(root, node)
    if not isinstance(node, Mapping):
        return None
    if isinstance(node.get("properties"), Mapping):
        return node
    for branch in node.get("anyOf") or node.get("oneOf") or ():
        branch = _definition(root, branch)
        properties = branch.get("properties") if isinstance(branch, Mapping) else None
        if not isinstance(properties, Mapping) or not set(value) <= set(properties):
            continue
        if all(
            "const" not in spec or value.get(key) == spec["const"]
            for key, spec in properties.items()
            if isinstance(spec, Mapping)
        ):
            return branch
    return None


def _array_items(root: Mapping[str, Any], node: Any) -> Any:
    node = _definition(root, node)
    if not isinstance(node, Mapping):
        return None
    if "items" in node:
        return node["items"]
    for branch in node.get("anyOf") or node.get("oneOf") or ():
        branch = _definition(root, branch)
        if isinstance(branch, Mapping) and "items" in branch:
            return branch["items"]
    return None


def _drop_unasked_nulls(root: Mapping[str, Any], node: Any, value: Any) -> Any:
    """Remove ``null`` where the original schema asked for no null: an optional
    property a strict grammar forced the model to spell out (OpenAI), or one
    an instruction-only reply wrote as null. Absent is what it means."""

    if isinstance(value, list):
        items = _array_items(root, node)
        return value if items is None else [
            _drop_unasked_nulls(root, items, item) for item in value
        ]
    if not isinstance(value, dict):
        return value
    branch = _object_branch(root, node, value)
    if branch is None:
        return value
    properties = branch["properties"]
    required = set(branch.get("required") or ())
    out: dict[str, Any] = {}
    for key, item in value.items():
        spec = properties.get(key)
        if spec is None:
            out[key] = item
        elif item is None and key not in required and not _allows_null(root, spec):
            continue
        else:
            out[key] = _drop_unasked_nulls(root, spec, item)
    return out


def _restore_structured_reply(content: str, request: "_Request") -> str:
    schema = request.json_schema()
    if schema is None or not content:
        return content
    try:
        parsed = json.loads(content)
    except ValueError:
        return content  # the server's own parser reports it
    restored = _drop_unasked_nulls(schema[1], schema[1], parsed)
    if restored == parsed:
        return content
    return json.dumps(restored, ensure_ascii=False)


def _schema_instruction(name: str, schema: Mapping[str, Any]) -> str:
    return _SCHEMA_INSTRUCTION.format(name=name) + json.dumps(
        schema, ensure_ascii=False, separators=(",", ":"),
    )


def _with_content(completion: SimpleNamespace, content: str) -> SimpleNamespace:
    completion.choices[0].message.content = content
    completion.output_text = content
    completion.output[0].content[0].text = content
    return completion


def _text(content: Any) -> str:
    """A message's content as text (a string, or text parts)."""

    if isinstance(content, str):
        return content
    if isinstance(content, Sequence):
        return "".join(
            str(part.get("text", "")) for part in content
            if isinstance(part, Mapping) and part.get("type") in {"text", "input_text"}
        )
    return "" if content is None else str(content)


def _call_parts(message: Mapping[str, Any]) -> list[tuple[str, str, str]]:
    """An assistant message's tool calls as (id, name, arguments JSON)."""

    calls = []
    for call in message.get("tool_calls") or ():
        function = call.get("function") or {}
        calls.append((
            str(call.get("id") or ""), str(function.get("name") or ""),
            str(function.get("arguments") or "{}"),
        ))
    return calls


def _arguments(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


class _Facade:
    """``client.chat.completions.create`` and ``client.responses.create``."""

    def __init__(self, backend: Any, asynchronous: bool) -> None:
        self._backend = backend
        self.model = backend.role_model.model
        completions = SimpleNamespace(
            create=backend.acreate if asynchronous else backend.create,
        )
        self.chat = SimpleNamespace(completions=completions)
        self.responses = SimpleNamespace(
            create=self._aresponses if asynchronous else self._responses,
        )

    @staticmethod
    def _as_chat(kwargs: Mapping[str, Any]) -> dict[str, Any]:
        unknown = set(kwargs) - {"model", "input", "instructions", "max_output_tokens", "timeout"}
        if unknown:
            raise TypeError(f"unsupported request arguments: {', '.join(sorted(unknown))}")
        messages: list[dict[str, Any]] = []
        if kwargs.get("instructions"):
            messages.append({"role": "system", "content": kwargs["instructions"]})
        items = kwargs.get("input")
        if isinstance(items, str):
            messages.append({"role": "user", "content": items})
        else:
            messages.extend(
                {"role": item.get("role", "user"), "content": _text(item.get("content"))}
                for item in items or ()
            )
        chat = {"model": kwargs.get("model"), "messages": messages}
        if kwargs.get("max_output_tokens"):
            chat["max_tokens"] = kwargs["max_output_tokens"]
        if kwargs.get("timeout") is not None:
            chat["timeout"] = kwargs["timeout"]
        return chat

    def _responses(self, **kwargs: Any) -> Any:
        return self._backend.create(**self._as_chat(kwargs))

    async def _aresponses(self, **kwargs: Any) -> Any:
        return await self._backend.acreate(**self._as_chat(kwargs))


class _XaiReasoning:
    """The xAI client with the role's reasoning effort added when not given."""

    def __init__(self, client: Any, reasoning: str, asynchronous: bool) -> None:
        self._client = client
        self._reasoning = reasoning
        self.model = getattr(client, "model", None)
        create = client.chat.completions.create

        def with_reasoning(kwargs: dict[str, Any]) -> dict[str, Any]:
            kwargs.setdefault("reasoning_effort", reasoning)
            return kwargs

        if asynchronous:
            async def acreate(**kwargs: Any) -> Any:
                return await create(**with_reasoning(kwargs))
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=acreate))
        else:
            def screate(**kwargs: Any) -> Any:
                return create(**with_reasoning(kwargs))
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=screate))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


# --- OpenAI: the Responses API ------------------------------------------------------

class _OpenAIResponsesBackend:
    provider = "openai"

    def __init__(self, client: Any, role_model: RoleModel) -> None:
        self.client = client
        self.role_model = role_model

    def _params(self, request: _Request) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        for message in request.messages:
            role = message.get("role")
            if role == "tool":
                items.append({
                    "type": "function_call_output",
                    "call_id": str(message.get("tool_call_id") or ""),
                    "output": _text(message.get("content")),
                })
                continue
            if role == "assistant" and message.get("tool_calls"):
                text = _text(message.get("content"))
                if text:
                    items.append({"role": "assistant", "content": text})
                items.extend(
                    {"type": "function_call", "call_id": call_id, "name": name, "arguments": arguments}
                    for call_id, name, arguments in _call_parts(message)
                )
                continue
            items.append({"role": role or "user", "content": _text(message.get("content"))})
        thinks = request.reasoning not in (None, "none")
        params: dict[str, Any] = {
            "model": request.model,
            "input": items,
            "store": False,
            "max_output_tokens": request.output_limit(thinks or request.reasoning is None),
            "prompt_cache_key": f"voiney-lab-{self.role_model.role}",
        }
        if request.reasoning is not None:
            params["reasoning"] = {"effort": request.reasoning}
        # Sampling settings are accepted only at reasoning "none"
        # (developers.openai.com, latest-model guide).
        if request.temperature is not None and request.reasoning == "none":
            params["temperature"] = request.temperature
        if request.tools:
            params["tools"] = [
                {
                    "type": "function",
                    "name": tool["function"]["name"],
                    "description": tool["function"].get("description", ""),
                    "parameters": tool["function"].get("parameters", {}),
                    "strict": False,
                }
                for tool in request.tools
            ]
            if request.tool_choice is not None:
                params["tool_choice"] = request.tool_choice
            if request.parallel_tool_calls is not None:
                params["parallel_tool_calls"] = request.parallel_tool_calls
        schema = request.json_schema()
        if schema is not None:
            name, body, strict = schema
            params["text"] = {"format": {
                "type": "json_schema", "name": name,
                "schema": (
                    _openai_strict(_without_keywords(body, OPENAI_STRICT_UNSUPPORTED_KEYWORDS))
                    if strict else body
                ),
                "strict": strict,
            }}
        elif request.wants_json_object:
            params["text"] = {"format": {"type": "json_object"}}
        if request.timeout is not None:
            params["timeout"] = request.timeout
        return params

    @staticmethod
    def _reply(response: Any, model: str) -> SimpleNamespace:
        texts: list[str] = []
        calls: list[SimpleNamespace] = []
        for item in getattr(response, "output", None) or ():
            kind = getattr(item, "type", None)
            if kind == "message":
                texts.extend(
                    getattr(part, "text", "") or "" for part in getattr(item, "content", None) or ()
                    if getattr(part, "type", None) == "output_text"
                )
            elif kind == "function_call":
                calls.append(_tool_call(
                    len(calls), str(getattr(item, "call_id", "") or ""),
                    str(getattr(item, "name", "") or ""),
                    str(getattr(item, "arguments", "") or "{}"),
                ))
        return _completion(
            content="".join(texts), tool_calls=calls,
            usage=_OpenAIResponsesBackend._usage(getattr(response, "usage", None)),
            model=str(getattr(response, "model", None) or model),
            finish_reason="tool_calls" if calls else "stop",
        )

    @staticmethod
    def _usage(usage: Any) -> SimpleNamespace | None:
        if usage is None:
            return None
        details = getattr(usage, "input_tokens_details", None)
        output_details = getattr(usage, "output_tokens_details", None)
        return _usage(
            int(getattr(usage, "input_tokens", 0) or 0),
            int(getattr(usage, "output_tokens", 0) or 0),
            cached=int(getattr(details, "cached_tokens", 0) or 0),
            cache_write=int(getattr(details, "cache_write_tokens", 0) or 0),
            reasoning=int(getattr(output_details, "reasoning_tokens", 0) or 0),
        )

    def create(self, **kwargs: Any) -> Any:
        request = _Request.read(self.role_model, kwargs)
        if request.stream:
            raise TypeError("streaming needs the asynchronous client")
        try:
            response = self.client.responses.create(**self._params(request))
        except Exception as exc:  # noqa: BLE001 -- typed below
            raise _openai_error(exc) from exc
        return self._restored(self._reply(response, request.model), request)

    @staticmethod
    def _restored(completion: SimpleNamespace, request: _Request) -> SimpleNamespace:
        content = completion.choices[0].message.content
        return _with_content(completion, _restore_structured_reply(content, request))

    async def acreate(self, **kwargs: Any) -> Any:
        request = _Request.read(self.role_model, kwargs)
        params = self._params(request)
        if not request.stream:
            try:
                response = await self.client.responses.create(**params)
            except Exception as exc:  # noqa: BLE001
                raise _openai_error(exc) from exc
            return self._restored(self._reply(response, request.model), request)
        try:
            stream = await self.client.responses.create(**params, stream=True)
        except Exception as exc:  # noqa: BLE001
            raise _openai_error(exc) from exc
        return self._stream(stream, request.model)

    async def _stream(self, stream: Any, model: str):
        slots: dict[int, int] = {}
        served = model
        try:
            async for event in stream:
                kind = getattr(event, "type", "")
                if kind == "response.output_text.delta":
                    yield _chunk(served, content=getattr(event, "delta", "") or "")
                elif kind == "response.output_item.added":
                    item = getattr(event, "item", None)
                    if getattr(item, "type", None) == "function_call":
                        index = slots.setdefault(int(getattr(event, "output_index", 0)), len(slots))
                        yield _chunk(served, tool_calls=[_tool_call(
                            index, str(getattr(item, "call_id", "") or ""),
                            str(getattr(item, "name", "") or ""),
                            str(getattr(item, "arguments", "") or ""),
                        )])
                elif kind == "response.function_call_arguments.delta":
                    index = slots.setdefault(int(getattr(event, "output_index", 0)), len(slots))
                    yield _chunk(served, tool_calls=[_tool_call(
                        index, "", "", getattr(event, "delta", "") or "",
                    )])
                elif kind == "response.completed":
                    response = getattr(event, "response", None)
                    served = str(getattr(response, "model", None) or served)
                    yield _chunk(served, usage=self._usage(getattr(response, "usage", None)))
        except Exception as exc:  # noqa: BLE001
            raise _openai_error(exc) from exc


def _status_kind(status: int | None) -> str:
    if status == 402:
        return "payment_required"
    if status == 429:
        return "rate_limited"
    if status in (401, 403):
        return "authentication"
    if status in (400, 404, 409, 422):
        return "invalid_request"
    if isinstance(status, int) and status >= 500:
        return "server"
    return "other"


def _openai_error(exc: Exception) -> ModelProviderError:
    if isinstance(exc, ModelProviderError):
        return exc
    name = type(exc).__name__
    status = getattr(exc, "status_code", None)
    if "Timeout" in name:
        return ModelProviderError("openai", "timeout", status)
    if "Connection" in name:
        return ModelProviderError("openai", "connection", status)
    return ModelProviderError("openai", _status_kind(status), status)


# --- Anthropic: the Messages API -----------------------------------------------------

#: Models that refuse forced tool use (tool_choice any/tool) with a 400; the
#: request asks with "auto" and the router's prompt asks for one call
#: (platform.claude.com, build-with-claude/thinking).
_ANTHROPIC_NO_FORCED_TOOL = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-1", "claude-mythos-5-1")
#: Models that take no thinking or effort settings and do accept temperature
#: (Sonnet 5.5 and Opus 5.5 refuse a non-default one with a 400).
_ANTHROPIC_PLAIN = ("claude-haiku-",)


class _AnthropicBackend:
    provider = "anthropic"

    def __init__(self, client: Any, role_model: RoleModel) -> None:
        self.client = client
        self.role_model = role_model

    def _params(self, request: _Request) -> dict[str, Any]:
        system: list[dict[str, Any]] = []
        messages: list[dict[str, Any]] = []
        for message in request.messages:
            role = message.get("role")
            if role in {"system", "developer"}:
                system.append({"type": "text", "text": _text(message.get("content"))})
            elif role == "tool":
                block = {
                    "type": "tool_result",
                    "tool_use_id": str(message.get("tool_call_id") or ""),
                    "content": _text(message.get("content")),
                }
                if messages and messages[-1]["role"] == "user" and isinstance(messages[-1]["content"], list):
                    messages[-1]["content"].append(block)
                else:
                    messages.append({"role": "user", "content": [block]})
            elif role == "assistant" and message.get("tool_calls"):
                blocks: list[dict[str, Any]] = []
                text = _text(message.get("content"))
                if text:
                    blocks.append({"type": "text", "text": text})
                blocks.extend(
                    {"type": "tool_use", "id": call_id, "name": name, "input": _arguments(arguments)}
                    for call_id, name, arguments in _call_parts(message)
                )
                messages.append({"role": "assistant", "content": blocks})
            else:
                messages.append({
                    "role": "assistant" if role == "assistant" else "user",
                    "content": _text(message.get("content")),
                })
        if system:
            # The stable part comes first: the standing instructions and the
            # protocol context. The cache breakpoint sits on the second system
            # block (or the only one); the tools, rendered before the system
            # prompt, share the cached prefix.
            system[min(1, len(system) - 1)]["cache_control"] = {"type": "ephemeral"}
        model = request.model
        plain = model.startswith(_ANTHROPIC_PLAIN)
        thinks = False
        params: dict[str, Any] = {"model": model, "messages": messages}
        if system:
            params["system"] = system
        output_config: dict[str, Any] = {}
        if not plain and request.reasoning is not None:
            if request.reasoning == "none":
                if model.startswith("claude-sonnet-5-5"):
                    # Thinking off on Sonnet 5.5 is "between_tools", accepted
                    # at effort high or below; the lowest effort with it.
                    params["thinking"] = {"type": "between_tools"}
                output_config["effort"] = "low"
            else:
                output_config["effort"] = request.reasoning
                thinks = True
        elif not plain:
            thinks = True  # these models think by default
        if plain and request.temperature is not None:
            # The Anthropic SDK 1.x has no sampling keywords; the API still
            # takes temperature on these models, so it rides in the body.
            params["extra_body"] = {"temperature": request.temperature}
        params["max_tokens"] = request.output_limit(thinks)
        if request.tools:
            params["tools"] = [
                {
                    "name": tool["function"]["name"],
                    "description": tool["function"].get("description", ""),
                    "input_schema": tool["function"].get("parameters", {"type": "object"}),
                }
                for tool in request.tools
            ]
            choice = request.tool_choice
            if choice == "required" and not model.startswith(_ANTHROPIC_NO_FORCED_TOOL):
                params["tool_choice"] = {"type": "any"}
            elif choice == "none":
                params["tool_choice"] = {"type": "none"}
            else:
                params["tool_choice"] = {"type": "auto"}
            if request.parallel_tool_calls is False:
                params["tool_choice"]["disable_parallel_tool_use"] = True
        schema = request.json_schema()
        if schema is not None:
            body = _without_keywords(_any_of(schema[1]), ANTHROPIC_UNSUPPORTED_KEYWORDS)
            _, optional, unions = _schema_counts(body)
            if (
                optional <= ANTHROPIC_MAX_OPTIONAL_PROPERTIES
                and unions <= ANTHROPIC_MAX_UNION_PROPERTIES
            ):
                output_config["format"] = {"type": "json_schema", "schema": body}
            else:
                # No grammar can hold it: the schema goes as an instruction,
                # in its own system block after the standing prompt.
                params.setdefault("system", []).append(
                    {"type": "text", "text": _schema_instruction(schema[0], schema[1])}
                )
        if output_config:
            params["output_config"] = output_config
        if request.timeout is not None:
            params["timeout"] = request.timeout
        return params

    @staticmethod
    def _usage(usage: Any, output_tokens: int | None = None) -> SimpleNamespace | None:
        if usage is None:
            return None
        cached = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        written = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
        fresh = int(getattr(usage, "input_tokens", 0) or 0)
        out = int(output_tokens if output_tokens is not None else getattr(usage, "output_tokens", 0) or 0)
        return _usage(fresh + cached + written, out, cached=cached, cache_write=written)

    @classmethod
    def _reply(cls, response: Any, model: str) -> SimpleNamespace:
        texts: list[str] = []
        calls: list[SimpleNamespace] = []
        for block in getattr(response, "content", None) or ():
            kind = getattr(block, "type", None)
            if kind == "text":
                texts.append(getattr(block, "text", "") or "")
            elif kind == "tool_use":
                calls.append(_tool_call(
                    len(calls), str(getattr(block, "id", "") or ""),
                    str(getattr(block, "name", "") or ""),
                    json.dumps(getattr(block, "input", None) or {}, ensure_ascii=False),
                ))
        return _completion(
            content="".join(texts), tool_calls=calls,
            usage=cls._usage(getattr(response, "usage", None)),
            model=str(getattr(response, "model", None) or model),
            finish_reason="tool_calls" if calls else "stop",
        )

    def create(self, **kwargs: Any) -> Any:
        request = _Request.read(self.role_model, kwargs)
        if request.stream:
            raise TypeError("streaming needs the asynchronous client")
        try:
            response = self.client.messages.create(**self._params(request))
        except Exception as exc:  # noqa: BLE001
            raise _anthropic_error(exc) from exc
        return _OpenAIResponsesBackend._restored(self._reply(response, request.model), request)

    async def acreate(self, **kwargs: Any) -> Any:
        request = _Request.read(self.role_model, kwargs)
        params = self._params(request)
        try:
            if not request.stream:
                return _OpenAIResponsesBackend._restored(
                    self._reply(await self.client.messages.create(**params), request.model), request,
                )
            stream = await self.client.messages.create(**params, stream=True)
        except Exception as exc:  # noqa: BLE001
            raise _anthropic_error(exc) from exc
        return self._stream(stream, request.model)

    async def _stream(self, stream: Any, model: str):
        served = model
        start_usage: Any = None
        tool_slots: dict[int, int] = {}
        try:
            async for event in stream:
                kind = getattr(event, "type", "")
                if kind == "message_start":
                    message = getattr(event, "message", None)
                    served = str(getattr(message, "model", None) or served)
                    start_usage = getattr(message, "usage", None)
                elif kind == "content_block_start":
                    block = getattr(event, "content_block", None)
                    if getattr(block, "type", None) == "tool_use":
                        index = tool_slots.setdefault(int(getattr(event, "index", 0)), len(tool_slots))
                        yield _chunk(served, tool_calls=[_tool_call(
                            index, str(getattr(block, "id", "") or ""),
                            str(getattr(block, "name", "") or ""), "",
                        )])
                elif kind == "content_block_delta":
                    delta = getattr(event, "delta", None)
                    delta_kind = getattr(delta, "type", None)
                    if delta_kind == "text_delta":
                        yield _chunk(served, content=getattr(delta, "text", "") or "")
                    elif delta_kind == "input_json_delta":
                        index = tool_slots.get(int(getattr(event, "index", 0)))
                        if index is not None:
                            yield _chunk(served, tool_calls=[_tool_call(
                                index, "", "", getattr(delta, "partial_json", "") or "",
                            )])
                elif kind == "message_delta":
                    usage = getattr(event, "usage", None)
                    yield _chunk(served, usage=self._usage(
                        start_usage, getattr(usage, "output_tokens", None),
                    ) if start_usage is not None else None)
        except Exception as exc:  # noqa: BLE001
            raise _anthropic_error(exc) from exc


def _anthropic_error(exc: Exception) -> ModelProviderError:
    if isinstance(exc, ModelProviderError):
        return exc
    name = type(exc).__name__
    status = getattr(exc, "status_code", None)
    if "Timeout" in name:
        return ModelProviderError("anthropic", "timeout", status)
    if "Connection" in name:
        return ModelProviderError("anthropic", "connection", status)
    return ModelProviderError("anthropic", _status_kind(status), status)


# --- Google: the Gemini API --------------------------------------------------------

class _GeminiBackend:
    provider = "google"

    def __init__(self, client: Any, role_model: RoleModel) -> None:
        self.client = client
        self.role_model = role_model

    def _params(self, request: _Request) -> dict[str, Any]:
        system: list[str] = []
        contents: list[dict[str, Any]] = []
        names: dict[str, str] = {}
        for message in request.messages:
            role = message.get("role")
            if role in {"system", "developer"}:
                system.append(_text(message.get("content")))
            elif role == "tool":
                call_id = str(message.get("tool_call_id") or "")
                contents.append({"role": "user", "parts": [{"function_response": {
                    "name": names.get(call_id, "tool"),
                    "response": {"result": _text(message.get("content"))},
                }}]})
            elif role == "assistant" and message.get("tool_calls"):
                parts: list[dict[str, Any]] = []
                text = _text(message.get("content"))
                if text:
                    parts.append({"text": text})
                for call_id, name, arguments in _call_parts(message):
                    names[call_id] = name
                    parts.append({"function_call": {"name": name, "args": _arguments(arguments)}})
                contents.append({"role": "model", "parts": parts})
            else:
                contents.append({
                    "role": "model" if role == "assistant" else "user",
                    "parts": [{"text": _text(message.get("content"))}],
                })
        # Gemini 3.x thinks at low, medium or high and cannot turn it off
        # (ai.google.dev, thinking guide): "none" asks for the least.
        level = None if request.reasoning is None else (
            "low" if request.reasoning == "none" else
            "high" if request.reasoning in {"xhigh", "max"} else request.reasoning
        )
        config: dict[str, Any] = {"max_output_tokens": min(
            (request.max_tokens or DEFAULT_MAX_OUTPUT_TOKENS)
            + GEMINI_THINKING_ALLOWANCE_TOKENS[level or "high"],
            GEMINI_MAX_OUTPUT_TOKENS,
        )}
        if request.temperature is not None:
            config["temperature"] = request.temperature
        if level is not None:
            config["thinking_config"] = {"thinking_level": level}
        if request.tools:
            config["tools"] = [{"function_declarations": [
                {
                    "name": tool["function"]["name"],
                    "description": tool["function"].get("description", ""),
                    "parameters_json_schema": tool["function"].get("parameters", {"type": "object"}),
                }
                for tool in request.tools
            ]}]
            mode = {"required": "ANY", "none": "NONE"}.get(request.tool_choice, "AUTO")
            config["tool_config"] = {"function_calling_config": {"mode": mode}}
        schema = request.json_schema()
        if schema is not None:
            config["response_mime_type"] = "application/json"
            body = _any_of(schema[1])
            if _schema_counts(body)[0] <= GEMINI_MAX_SCHEMA_PROPERTIES:
                config["response_json_schema"] = body
            else:
                # JSON mode, and the schema as an instruction (see above).
                system.append(_schema_instruction(schema[0], schema[1]))
        elif request.wants_json_object:
            config["response_mime_type"] = "application/json"
        if system:
            config["system_instruction"] = "\n\n".join(system)
        return {"model": request.model, "contents": contents, "config": config}

    @staticmethod
    def _usage(metadata: Any) -> SimpleNamespace | None:
        if metadata is None:
            return None
        thoughts = int(getattr(metadata, "thoughts_token_count", 0) or 0)
        return _usage(
            int(getattr(metadata, "prompt_token_count", 0) or 0),
            int(getattr(metadata, "candidates_token_count", 0) or 0) + thoughts,
            cached=int(getattr(metadata, "cached_content_token_count", 0) or 0),
            reasoning=thoughts,
        )

    @staticmethod
    def _parts(response: Any) -> list[Any]:
        candidates = getattr(response, "candidates", None) or ()
        if not candidates:
            return []
        content = getattr(candidates[0], "content", None)
        return list(getattr(content, "parts", None) or ())

    @classmethod
    def _reply(cls, response: Any, model: str) -> SimpleNamespace:
        texts: list[str] = []
        calls: list[SimpleNamespace] = []
        for part in cls._parts(response):
            if getattr(part, "thought", False):
                continue
            call = getattr(part, "function_call", None)
            if call is not None:
                calls.append(_tool_call(
                    len(calls), str(getattr(call, "id", None) or f"call_{len(calls)}"),
                    str(getattr(call, "name", "") or ""),
                    json.dumps(dict(getattr(call, "args", None) or {}), ensure_ascii=False),
                ))
            elif getattr(part, "text", None):
                texts.append(part.text)
        return _completion(
            content="".join(texts), tool_calls=calls,
            usage=cls._usage(getattr(response, "usage_metadata", None)),
            model=str(getattr(response, "model_version", None) or model),
            finish_reason="tool_calls" if calls else "stop",
        )

    def create(self, **kwargs: Any) -> Any:
        request = _Request.read(self.role_model, kwargs)
        if request.stream:
            raise TypeError("streaming needs the asynchronous client")
        try:
            response = self.client.models.generate_content(**self._params(request))
        except Exception as exc:  # noqa: BLE001
            raise _gemini_error(exc) from exc
        return _OpenAIResponsesBackend._restored(self._reply(response, request.model), request)

    async def acreate(self, **kwargs: Any) -> Any:
        request = _Request.read(self.role_model, kwargs)
        params = self._params(request)
        try:
            if not request.stream:
                return _OpenAIResponsesBackend._restored(self._reply(
                    await self.client.aio.models.generate_content(**params), request.model,
                ), request)
            stream = await self.client.aio.models.generate_content_stream(**params)
        except Exception as exc:  # noqa: BLE001
            raise _gemini_error(exc) from exc
        return self._stream(stream, request.model)

    async def _stream(self, stream: Any, model: str):
        served = model
        calls = 0
        usage: Any = None
        try:
            async for response in stream:
                served = str(getattr(response, "model_version", None) or served)
                usage = getattr(response, "usage_metadata", None) or usage
                for part in self._parts(response):
                    if getattr(part, "thought", False):
                        continue
                    call = getattr(part, "function_call", None)
                    if call is not None:
                        yield _chunk(served, tool_calls=[_tool_call(
                            calls, str(getattr(call, "id", None) or f"call_{calls}"),
                            str(getattr(call, "name", "") or ""),
                            json.dumps(dict(getattr(call, "args", None) or {}), ensure_ascii=False),
                        )])
                        calls += 1
                    elif getattr(part, "text", None):
                        yield _chunk(served, content=part.text)
        except Exception as exc:  # noqa: BLE001
            raise _gemini_error(exc) from exc
        yield _chunk(served, usage=self._usage(usage))


def _gemini_error(exc: Exception) -> ModelProviderError:
    if isinstance(exc, ModelProviderError):
        return exc
    status = getattr(exc, "code", None)
    if not isinstance(status, int):
        status = getattr(exc, "status_code", None)
    name = type(exc).__name__
    if "Timeout" in name or "timeout" in str(exc).casefold() and status is None:
        return ModelProviderError("google", "timeout", status)
    if "Connect" in name:
        return ModelProviderError("google", "connection", status)
    return ModelProviderError("google", _status_kind(status), status)
