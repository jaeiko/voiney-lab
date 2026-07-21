"""The single, deterministic M4 SafeBridge tool."""

from __future__ import annotations

import json
from pathlib import Path
from time import perf_counter
from typing import Any, Callable

DATA_PATH = Path(__file__).with_name("data") / "approved_safety_manual.demo.json"
TOOL_NAME = "search_approved_safety_manual"

SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "Use this whenever a worker asks about a safety procedure or approved "
            "site safety information. Search the locally approved demo manual before "
            "answering; do not use it for greetings or ordinary conversation."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "minLength": 1,
                    "description": "The worker's non-empty safety question or keywords.",
                },
                "language": {
                    "type": "string",
                    "enum": ["ko", "vi"],
                    "description": "Language used by the worker.",
                },
            },
            "required": ["query", "language"],
            "additionalProperties": False,
        },
    },
}


def _result(status: str, matches: list[dict[str, Any]] | None = None,
            message: str | None = None) -> dict[str, Any]:
    value: dict[str, Any] = {"status": status, "matches": matches or []}
    if message:
        value["message"] = message
    return value


def search_approved_safety_manual(query: Any, language: Any,
                                  data_path: Path = DATA_PATH) -> dict[str, Any]:
    """Return structured local matches; expected bad input/misses never raise."""
    if (not isinstance(query, str) or not query.strip()
            or language not in ("ko", "vi")):
        return _result("invalid_arguments", message="query and language must be valid")
    try:
        records = json.loads(data_path.read_text(encoding="utf-8"))
        if not isinstance(records, list):
            raise ValueError("demo data must be a list")
        terms = {term.casefold() for term in query.split() if len(term) >= 2}
        scored: list[tuple[int, dict[str, Any]]] = []
        allowed = ("document_id", "title", "section", "guidance", "source_label", "demo_only")
        for record in records:
            localized = record["translations"][language]
            haystack = " ".join((localized["title"], localized["section"],
                                 localized["guidance"], " ".join(record.get("keywords", [])))).casefold()
            score = sum(term in haystack for term in terms)
            if score:
                item = {
                    "document_id": record["document_id"],
                    "title": localized["title"],
                    "section": localized["section"],
                    "guidance": localized["guidance"],
                    "source_label": record["source_label"],
                    "demo_only": bool(record["demo_only"]),
                }
                scored.append((score, {key: item[key] for key in allowed}))
        matches = [item for _, item in sorted(scored, key=lambda pair: (-pair[0], pair[1]["document_id"]))[:3]]
        return _result("success" if matches else "not_found", matches)
    except Exception:
        return _result("error", message="local demo safety data could not be read")


REGISTERED_TOOLS: dict[str, Callable[..., dict[str, Any]]] = {
    TOOL_NAME: search_approved_safety_manual,
}


def execute_tool(name: str, arguments: Any) -> dict[str, Any]:
    if name not in REGISTERED_TOOLS:
        return _result("invalid_arguments", message="unknown tool")
    if not isinstance(arguments, dict) or set(arguments) != {"query", "language"}:
        return _result("invalid_arguments", message="unexpected or missing arguments")
    return REGISTERED_TOOLS[name](arguments["query"], arguments["language"])
