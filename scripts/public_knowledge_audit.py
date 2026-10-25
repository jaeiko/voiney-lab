#!/usr/bin/env python3
"""Public knowledge coverage audit: what the public bundle holds and finds (lane KAUDIT).

    python scripts/public_knowledge_audit.py inventory [--out FILE] [--out-md FILE]
    python scripts/public_knowledge_audit.py map --backend fts5|moss [--out FILE] [--out-md FILE]
    python scripts/public_knowledge_audit.py help --backend fts5|moss --protocol-data-dir DIR [--out FILE]
    python scripts/public_knowledge_audit.py all --backend moss --protocol-data-dir DIR --out-dir DIR
    python scripts/public_knowledge_audit.py find TERM [TERM ...] [--source ID] [--limit N]
    python scripts/public_knowledge_audit.py search "질문" --backend fts5|moss

The person's aim (2026-10-25) is public safety, equipment and technique
material for the whole wet lab -- chemistry, biology and materials
(batteries). This tool measures how far the bundle lane KNOW collected
(``data/public_knowledge/sources.toml`` → ``data/runtime/public-knowledge/``)
covers that, and nothing else: it collects nothing, uploads nothing and
calls no model.

- ``inventory`` counts the bundle's pieces by source, grade, licence,
  language, domain and kind of document; finds exact and near duplicates
  and the editions each source holds; and draws a seeded random sample of
  pieces with flags for broken ones (a table run together, a menu or a
  reference list only, another language mixed in, too short).
- ``map`` reads the coverage table
  ``data/fixtures/evaluation/public_knowledge_coverage.json``: for each cell
  (가) the pieces whose title or words carry one of the cell's terms, by
  grade, and (나) the cell's questions searched the way the server searches
  -- ``PublicKnowledgeSource`` with the server's glossary
  (``data/public_knowledge/glossary.json``) and alpha 0.5 -- with recall@5
  against each question's gold pieces. A cell is ✓ when at least two thirds
  of its questions find a gold piece in the top five, ✗ when no question has
  any gold piece in the bundle, △ otherwise. Korean questions are scored
  apart, and the Korean words the glossary could not turn into English are
  listed.
- ``help`` runs lane HELP's evaluation set
  (``data/fixtures/evaluation/problem_help_eval.json``) through the server's
  turn (``server.run_turn``) with the public tier connected: STT, TTS and the
  supplemental model (possible causes, paraphrase) are fakes. It records
  which public pieces were attached to each help and whether any spoken
  public sentence breaks ``answer_checks.public_reference_violations``.
  The starter-pack protocols come from a protocol catalog folder
  (``--protocol-data-dir``, use a copy: opening a catalog may write to it);
  the in-gel rows need the licensed in-gel PDF (condition A).
- ``find`` and ``search`` help write the coverage table: pieces carrying a
  term, and what the server's search returns for a question.

Backends: ``--backend moss`` loads the server's Moss index once
(``VOINEY_LAB_PUBLIC_KNOWLEDGE_INDEX``, default ``voiney-public-knowledge``)
with ``MOSS_PROJECT_ID``/``MOSS_PROJECT_KEY`` from the shell or from
``--env-file`` (only those two names are read; values never printed) and
queries it in process -- no upload. ``--backend fts5`` puts the same
``PublicKnowledgeSource`` around an SQLite FTS5 index of the bundle, for
offline runs and tests (its ranking is not Moss's).
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import logging
import os
import random
import re
import statistics
import sys
import tempfile
import time
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from voiney_lab.knowledge import KnowledgeContext, KnowledgeHit
from voiney_lab.public_knowledge import (
    DEFAULT_INDEX_NAME,
    ENABLED_SETTING,
    INDEX_SETTING,
    MOSS_PROJECT_ID_SETTING,
    MOSS_PROJECT_KEY_SETTING,
    FoundDocument,
    Glossary,
    PublicKnowledgeSettings,
    PublicKnowledgeSource,
    default_glossary_path,
    start_public_knowledge,
)

ROOT = Path(__file__).resolve().parents[1]
PIPELINE_SCRIPT = ROOT / "scripts" / "public_knowledge_pipeline.py"
COVERAGE_FILE = ROOT / "data" / "fixtures" / "evaluation" / "public_knowledge_coverage.json"
HELP_SET_FILE = ROOT / "data" / "fixtures" / "evaluation" / "problem_help_eval.json"
STARTER_PACK_FILE = ROOT / "scripts" / "pilot_starter_pack.csv"
IN_GEL_FIXTURE_DIR = ROOT / "data" / "fixtures" / "development_protocols"
IN_GEL_PDF = ROOT / "data" / "runtime" / "candidate-a-source" / "in-gel-digestion.pdf"

#: recall@5, as lane KNOW measured it.
TOP_K = 5
#: A cell is ✓ when this share of its questions finds a gold piece in the top five.
SUFFICIENT_SHARE = 2 / 3
VERDICTS = ("✓", "△", "✗")
SAMPLE_SIZE = 200
SAMPLE_SEED = 20261025
#: A sampled piece is a near duplicate of another when this share of its
#: 8-word shingles is in that other piece.
NEAR_DUPLICATE_SHARE = 0.8
SHINGLE_WORDS = 8
#: What a source's ``kind`` (and grade) is as a document, for the inventory.
DOCUMENT_KINDS: Mapping[str, str] = {
    "niosh_npg": "chemical card",
    "pubchem": "compound record",
    "europepmc": "journal article",
    "wikipedia": "encyclopedia article",
    "html": "guideline",
    "pdf:A": "guideline",
    "pdf:B": "textbook",
}
#: Korean question words that name nothing to look up: endings, question
#: words and verbs of a bench question. The glossary is not expected to know
#: them, so they are not listed as its gaps.
KOREAN_FUNCTION_WORDS = frozenset("""
가 거 거야 거지 건 걸 게 겠지 고 그 그거 그냥 그럼 그래서 근데 기 까 꼭 나 나요 너무 넣어 넣고 넣으면 놔 놔도 놓고
뇌 는 니 다 다시 다음 대 대해 더 도 돼 돼요 될까 되나 되는 되는지 되면 되지 된 된다 됐어 됨 두 둬 둬도 들 등 따라 때 때는
때문에 또 랑 로 를 마 막 만 말 말고 맞아 맞지 먼저 몇 몇도 몇분 무슨 뭐 뭐야 뭐지 뭐가 뭘 뭔가 뭔지 므로 및 바로 방법 버려 버려도
보고 보다 봐 봐야 부터 빨리 뿐 사용 사용법 생겨 생겼어 생기면 생기는 생김 서 수 쉽게 써 써도 쓰고 쓰는 쓰면 쓸 아 아니 아직 안 않아 알려 알려줘
야 얘기 어 어느 어디 어디서 어때 어떡해 어떤 어떻게 언제 얼마 얼마나 없어 없이 에 에서 엔 여기 왜 요 우리 원래 위해 으로 은 을 의 이 이거 이건
이게 이런 이렇게 이유 인가 인지 일 있어 있나 있는 있는지 있을까 자꾸 잘 저 저거 전 정도 제 조금 좀 주의 주의할 줘 중 지 지금 진짜 처럼 할 할때
하고 하는 하는데 하는법 하나 하는지 하면 하면서 한 한번 할까 함 해 해도 해야 해야돼 해야해 해요 했어 했는데 혹시 확인 후 흔한 힘
""".split())

log = logging.getLogger("public_knowledge_audit")


# --- The pipeline's own pieces, reused -------------------------------------------------------


def _load_pipeline() -> Any:
    """``scripts/public_knowledge_pipeline.py`` as a module (bundle reading, FTS5 baseline)."""

    name = "public_knowledge_pipeline"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, PIPELINE_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


pipeline = _load_pipeline()


def _flat(text: str) -> str:
    """NFKC, casefolded, whitespace collapsed: how gold specs and terms are matched."""

    return " ".join(unicodedata.normalize("NFKC", str(text)).casefold().split())


# --- The bundle -----------------------------------------------------------------------------


@dataclass
class Bundle:
    """The bundle's pieces with their flattened words, read once."""

    records: list[dict[str, Any]]
    glossary: Glossary
    runtime_dir: Path
    _flat: dict[str, tuple[str, str, str]] = field(default_factory=dict, repr=False)

    @classmethod
    def read(cls, runtime_dir: Path) -> "Bundle":
        records = pipeline.read_bundle(runtime_dir)
        return cls(records=records, glossary=pipeline.load_bundle_glossary(runtime_dir), runtime_dir=runtime_dir)

    def flat(self, record: Mapping[str, Any]) -> tuple[str, str, str]:
        """(title, locator, text) flattened, cached by piece id."""

        key = str(record["id"])
        found = self._flat.get(key)
        if found is None:
            found = (_flat(record.get("title", "")), _flat(record.get("locator", "")), _flat(record.get("text", "")))
            self._flat[key] = found
        return found

    def by_id(self) -> dict[str, dict[str, Any]]:
        return {str(record["id"]): record for record in self.records}

    def gold_ids(self, specs: Sequence[Mapping[str, str]]) -> set[str]:
        """The pieces a question's gold specs match, as the pipeline's ``evaluate`` matches them.

        A spec names a source id (``*`` for any) and any of title, locator
        and text substrings (whitespace collapsed, case ignored); a piece
        matches when it holds all of them.
        """

        ids: set[str] = set()
        for spec in specs:
            source_id = spec.get("source_id", "*")
            wanted = [(index, _flat(spec[name])) for index, name in
                      enumerate(("title_contains", "locator_contains", "text_contains")) if spec.get(name)]
            for record in self.records:
                if source_id != "*" and record.get("source_id") != source_id:
                    continue
                parts = self.flat(record)
                if all(needle in parts[index] for index, needle in wanted):
                    ids.add(str(record["id"]))
        return ids


# --- The offline backend --------------------------------------------------------------------


class BundleSearchClient:
    """SQLite FTS5 over the bundle in Moss's place (``SearchClient``): offline runs and tests.

    The documents are the ones ``upload`` sends to Moss (``documents_for``:
    id, searched text, string metadata), so a hit reads as a Moss hit does;
    the ranking is FTS5's bm25 over trigrams, and ``alpha`` is not used.
    """

    def __init__(self, records: Sequence[Mapping[str, Any]], glossary: Glossary) -> None:
        self._documents = {doc["id"]: doc for doc in pipeline.documents_for(records, glossary)}
        self._index: Any = None
        self.queries: list[str] = []

    async def load(self, index_name: str) -> None:
        self._index = pipeline.Fts5Baseline(list(self._documents.values()))

    async def query(self, index_name: str, text: str, *, top_k: int, alpha: float) -> list[FoundDocument]:
        self.queries.append(text)
        ids = self._index.search(text, top_k=top_k) if self._index is not None else []
        return [
            FoundDocument(id=doc_id, text=self._documents[doc_id]["text"],
                          metadata=dict(self._documents[doc_id]["metadata"]), score=round(1 / rank, 4))
            for rank, doc_id in enumerate(ids, 1)
        ]

    async def close(self) -> None:
        self._index = None


def _moss_credentials(env_file: str | None) -> dict[str, str]:
    """``MOSS_PROJECT_ID``/``MOSS_PROJECT_KEY`` from the shell, else from the dotenv file.

    Only those two names are taken from the file; nothing is printed.
    """

    values = {name: os.environ.get(name, "").strip() for name in (MOSS_PROJECT_ID_SETTING, MOSS_PROJECT_KEY_SETTING)}
    if env_file and not all(values.values()):
        from dotenv import dotenv_values  # noqa: PLC0415

        read = dotenv_values(env_file)
        for name in values:
            values[name] = values[name] or str(read.get(name) or "").strip()
    return values


@dataclass
class OpenedSource:
    source: PublicKnowledgeSource
    backend: str
    load_seconds: float
    rss_before_mib: float | None
    rss_after_load_mib: float | None
    moss_loads: int


async def open_source(
    backend: str, bundle: Bundle, *, env_file: str | None = None, cache_dir: str | None = None,
    index_name: str | None = None, glossary_path: Path | None = None, client_factory: Any = None,
) -> OpenedSource:
    """The public tier as the server builds it, around Moss or the offline FTS5 client."""

    before = pipeline.rss_mib()
    started = time.perf_counter()
    if backend == "moss":
        environment = {
            ENABLED_SETTING: "true",
            INDEX_SETTING: index_name or os.environ.get(INDEX_SETTING, "") or DEFAULT_INDEX_NAME,
            **_moss_credentials(env_file),
        }
        settings = PublicKnowledgeSettings.from_environment(environment, cache_dir=cache_dir)
        source = await start_public_knowledge(settings, client_factory=client_factory, glossary_path=glossary_path)
        assert source is not None
        loads = 1
    elif backend == "fts5":
        settings = PublicKnowledgeSettings(enabled=True, index_name="offline-fts5")
        glossary = Glossary.load(glossary_path or default_glossary_path())
        source = PublicKnowledgeSource(settings, BundleSearchClient(bundle.records, bundle.glossary), glossary=glossary)
        await source.load()
        loads = 0
    else:
        raise SystemExit(f"unknown backend {backend!r} (fts5 or moss)")
    return OpenedSource(source=source, backend=backend, load_seconds=round(time.perf_counter() - started, 1),
                        rss_before_mib=before, rss_after_load_mib=pipeline.rss_mib(), moss_loads=loads)


AUDIT_CONTEXT = KnowledgeContext(None, None, "public-knowledge-audit", None, None, "ko")


async def search(source: PublicKnowledgeSource, text: str, *, language: str = "ko", limit: int = TOP_K) -> list[KnowledgeHit]:
    """One question searched as the server searches: glossary expansion, alpha, grade order."""

    context = KnowledgeContext(None, None, AUDIT_CONTEXT.protocol_id, None, None, language)
    return await source.search(text, limit=limit, context=context)


# --- Inventory ------------------------------------------------------------------------------


def document_kind(source: Any) -> str:
    kind = getattr(source, "kind", "")
    return DOCUMENT_KINDS.get(f"{kind}:{getattr(source, 'grade', '')}") or DOCUMENT_KINDS.get(kind, kind)


_HANGUL = re.compile(r"[가-힣]")
_LATIN = re.compile(r"[A-Za-z]")
_OTHER_SCRIPT = re.compile(r"[Ѐ-ӿ؀-ۿ぀-ヿ一-鿿฀-๿]")
_NUMBERISH = re.compile(r"^[\d.,%±×°+\-–/()<>=:;]+$")
_REFERENCE_MARK = re.compile(
    r"\bet al\.|\bdoi[:\s]|\bdoi\.org|\bJ\.\s|\bVol\.|\bpp\.\s|\bProc\.|\bISBN\b|\bPMID\b|\(\d{4}\)|\b\d{4};\d+|\bIbid\b", re.I)
_MENU_WORDS = re.compile(
    r"\b(?:jump to|skip to|main menu|navigation|cookie|privacy policy|sign in|log in|share this|print this|"
    r"back to top|edit source|see also|external links|further reading|table of contents)\b", re.I)
_SENTENCE = re.compile(r"[.!?。]\s|[다요죠]\.\s|[다요]\s*$")


def piece_flags(record: Mapping[str, Any]) -> list[str]:
    """Flags for a piece that may be broken; empty when it reads as running text.

    - ``table``: mostly numbers and short tokens (a table run together);
    - ``references``: mostly reference marks (a reference list);
    - ``menu``: navigation words of a web page;
    - ``mixed_language``: an English piece with Hangul or another script, or
      a Korean piece with little Hangul;
    - ``short``: under the pipeline's minimum length.
    """

    text = str(record.get("text", ""))
    flags: list[str] = []
    tokens = text.split()
    if len(text.strip()) < pipeline.CHUNK_MIN_CHARS:
        flags.append("short")
    if tokens:
        numeric = sum(1 for token in tokens if _NUMBERISH.match(token))
        short = sum(1 for token in tokens if len(token) <= 2)
        sentences = len(_SENTENCE.findall(text))
        if numeric / len(tokens) >= 0.3 or (short / len(tokens) >= 0.5 and sentences <= 1):
            flags.append("table")
    references = len(_REFERENCE_MARK.findall(text))
    if references >= 4 and references * 120 >= len(text):
        flags.append("references")
    if len(_MENU_WORDS.findall(text)) >= 2:
        flags.append("menu")
    letters = len(_HANGUL.findall(text)) + len(_LATIN.findall(text))
    hangul = len(_HANGUL.findall(text))
    other = len(_OTHER_SCRIPT.findall(text))
    if record.get("language") == "en":
        if hangul >= 10 or (letters and other / max(1, letters + other) >= 0.05):
            flags.append("mixed_language")
    elif record.get("language") == "ko" and letters:
        if hangul / letters < 0.3:
            flags.append("mixed_language")
    return flags


def _shingles(text: str, size: int = SHINGLE_WORDS) -> set[int]:
    words = _flat(text).split()
    return {hash(" ".join(words[i:i + size])) for i in range(0, max(0, len(words) - size + 1))}


def inventory(bundle: Bundle, sources: Any, *, sample_size: int = SAMPLE_SIZE, seed: int = SAMPLE_SEED) -> dict[str, Any]:
    """Counts, duplicates, editions and a flagged random sample of the bundle."""

    records = bundle.records
    by_source = {source.id: source for source in sources.sources}
    rows: dict[str, dict[str, Any]] = {}
    for record in records:
        source_id = str(record.get("source_id", ""))
        row = rows.setdefault(source_id, {
            "name": record.get("source_name", ""), "grade": record.get("grade", ""),
            "kind": document_kind(by_source.get(source_id)), "language": record.get("language", ""),
            "pieces": 0, "chars": 0, "documents": set(), "domains": Counter(), "licences": Counter(),
            "versions": Counter(),
        })
        row["pieces"] += 1
        row["chars"] += len(str(record.get("text", "")))
        row["documents"].add(str(record.get("url") or record.get("title")))
        row["domains"][str(record.get("domain", ""))] += 1
        row["licences"][str(record.get("license", ""))] += 1
        row["versions"][str(record.get("version", ""))] += 1
    for row in rows.values():
        row["documents"] = len(row["documents"])
        row["domains"] = dict(row["domains"].most_common())
        row["licences"] = dict(row["licences"].most_common())
        row["versions"] = dict(row["versions"].most_common(3))
    totals = {
        "pieces": len(records),
        "chars": sum(len(str(r.get("text", ""))) for r in records),
        "by_grade": dict(Counter(str(r.get("grade", "")) for r in records).most_common()),
        "by_language": dict(Counter(str(r.get("language", "")) for r in records).most_common()),
        "by_domain": dict(Counter(str(r.get("domain", "")) for r in records).most_common()),
        "by_kind": dict(Counter(document_kind(by_source.get(str(r.get("source_id")))) for r in records).most_common()),
        "by_licence_family": dict(Counter(_licence_family(str(r.get("license", ""))) for r in records).most_common()),
        "domain_by_grade": {
            domain: dict(Counter(str(r.get("grade", "")) for r in records if r.get("domain") == domain).most_common())
            for domain in sorted({str(r.get("domain", "")) for r in records})
        },
    }
    # Exact duplicates (the pipeline drops these at collect; any left are counted).
    seen: dict[str, list[str]] = defaultdict(list)
    for record in records:
        seen[_flat(record.get("text", ""))].append(str(record["id"]))
    exact = [ids for ids in seen.values() if len(ids) > 1]
    # Same document in two pieces of one source under different titles (a source fetched twice).
    titles: dict[tuple[str, str], set[str]] = defaultdict(set)
    for record in records:
        titles[(str(record.get("source_id")), str(record.get("url", "")))].add(str(record.get("title", "")))
    # The random sample: flags and near duplicates.
    rng = random.Random(seed)
    sample = rng.sample(records, min(sample_size, len(records)))
    sample_shingles = [_shingles(str(r.get("text", ""))) for r in sample]
    owners: dict[int, list[int]] = defaultdict(list)
    for index, shingles in enumerate(sample_shingles):
        for value in shingles:
            owners[value].append(index)
    overlap: list[dict[str, int]] = [defaultdict(int) for _ in sample]
    sample_ids = [str(r["id"]) for r in sample]
    for record in records:
        record_id = str(record["id"])
        counts: dict[int, int] = defaultdict(int)
        for value in _shingles(str(record.get("text", ""))):
            for index in owners.get(value, ()):
                counts[index] += 1
        for index, count in counts.items():
            if record_id != sample_ids[index]:
                overlap[index][record_id] = count
    sampled: list[dict[str, Any]] = []
    for index, record in enumerate(sample):
        total = len(sample_shingles[index])
        near = sorted(
            ((other, count) for other, count in overlap[index].items() if total and count / total >= NEAR_DUPLICATE_SHARE),
            key=lambda item: -item[1])
        flags = piece_flags(record)
        if near:
            flags.append("near_duplicate")
        sampled.append({
            "id": record["id"], "source_id": record.get("source_id"), "grade": record.get("grade"),
            "language": record.get("language"), "title": record.get("title"), "locator": record.get("locator"),
            "chars": len(str(record.get("text", ""))), "flags": flags,
            "near_duplicates": [other for other, _count in near[:3]],
            "text": str(record.get("text", "")),
        })
    flag_counts = Counter(flag for row in sampled for flag in row["flags"])
    broken = sum(1 for row in sampled if set(row["flags"]) - {"near_duplicate"})
    editions = {
        source.id: {"version": str(source.option("version", "") or ""), "page_url": str(source.option("page_url", "") or ""),
                    "licence_checked": source.license_checked}
        for source in sources.sources
    }
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "bundle_manifest": _manifest_brief(bundle.runtime_dir),
        "sources": rows,
        "totals": totals,
        "duplicates": {
            "exact_groups": len(exact), "exact_extra_pieces": sum(len(ids) - 1 for ids in exact),
            "exact_examples": exact[:5],
            "urls_with_several_titles": sum(1 for names in titles.values() if len(names) > 1),
        },
        "editions": editions,
        "textbooks": textbook_chapters(records),
        "sample": {
            "size": len(sampled), "seed": seed,
            "flag_counts": dict(flag_counts.most_common()),
            "broken": broken, "broken_share": round(broken / len(sampled), 3) if sampled else 0.0,
            "near_duplicate": flag_counts.get("near_duplicate", 0),
            "by_source": dict(Counter(str(row["source_id"]) for row in sampled).most_common()),
            "rows": sampled,
        },
    }


_CHAPTER_FOOTER = re.compile(r"Chapter (\d{1,2}) \| ([A-Z][A-Za-z,\-–' ]+?)(?= \d{1,4}\b| This OpenStax|$)")
_PAGE = re.compile(r"p\. (\d+)")
#: The page footer each OpenStax page carries into its pieces.
OPENSTAX_FOOTER = "This OpenStax book is available for free"


def textbook_chapters(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Pieces per chapter of each OpenStax textbook, from the page footers ("Chapter 32 | Plant Reproduction").

    A page takes the chapter of the last footer seen on or before it (front
    and back matter fall to the first and the last chapter), so the counts
    are estimates for planning which chapters a lab needs.
    """

    books: dict[str, Any] = {}
    for source_id in sorted({str(r.get("source_id")) for r in records if str(r.get("source_id", "")).startswith("openstax-")}):
        pages: dict[int, int] = defaultdict(int)
        chapter_at: dict[int, tuple[int, str]] = {}
        footer = 0
        for record in records:
            if record.get("source_id") != source_id:
                continue
            match = _PAGE.match(str(record.get("locator", "")))
            page = int(match.group(1)) if match else 0
            pages[page] += 1
            text = str(record.get("text", ""))
            footer += OPENSTAX_FOOTER in text
            for found in _CHAPTER_FOOTER.finditer(text):
                chapter_at[page] = (int(found.group(1)), found.group(2).strip())
        counts: Counter[str] = Counter()
        current: tuple[int, str] | None = None
        for page in sorted(pages):
            current = chapter_at.get(page, current)
            counts[f"{current[0]:02d} {current[1]}" if current else "00 front matter"] += pages[page]
        books[source_id] = {"pieces": sum(pages.values()), "pieces_with_page_footer": footer,
                            "chapters": dict(sorted(counts.items()))}
    return books


def _licence_family(licence: str) -> str:
    lowered = licence.casefold()
    families = []
    if "public domain" in lowered:
        families.append("public domain")
    if "by-sa" in lowered:
        families.append("CC BY-SA")
    elif "cc by" in lowered:
        families.append("CC BY")
    if "cc0" in lowered:
        families.append("CC0")
    return " + ".join(families) or licence


def _manifest_brief(runtime_dir: Path) -> dict[str, Any]:
    path = runtime_dir / "bundle" / "manifest.json"
    if not path.is_file():
        return {}
    manifest = json.loads(path.read_text(encoding="utf-8"))
    return {"generated_at": manifest.get("generated_at"), "totals": manifest.get("totals", {})}


def inventory_markdown(result: Mapping[str, Any]) -> str:
    lines = ["| 출처 | 등급 | 종류 | 언어 | 조각 | 글자 | 문서 | 분야 | 라이선스 |", "|---|---|---|---|---|---|---|---|---|"]
    for source_id, row in sorted(result["sources"].items(), key=lambda item: -item[1]["pieces"]):
        domains = ", ".join(f"{name} {count}" for name, count in row["domains"].items())
        licences = "; ".join(f"{name} {count}" for name, count in list(row["licences"].items())[:2])
        lines.append(f"| {source_id} | {row['grade']} | {row['kind']} | {row['language']} | {row['pieces']:,} | "
                     f"{row['chars']:,} | {row['documents']:,} | {domains} | {licences} |")
    totals = result["totals"]
    lines.append(f"| **합계** | {_counts(totals['by_grade'])} | {_counts(totals['by_kind'])} | "
                 f"{_counts(totals['by_language'])} | **{totals['pieces']:,}** | {totals['chars']:,} | | "
                 f"{_counts(totals['by_domain'])} | {_counts(totals['by_licence_family'])} |")
    sample = result["sample"]
    lines += ["", f"표본 {sample['size']} 조각(씨앗 {sample['seed']}): 깨진 것으로 표시 {sample['broken']} "
                  f"({sample['broken_share']:.1%}), 거의 같은 조각이 따로 있음 {sample['near_duplicate']}; "
                  f"표시별 {_counts(sample['flag_counts'])}",
              f"정확히 같은 글: {result['duplicates']['exact_groups']} 묶음 "
              f"(남는 조각 {result['duplicates']['exact_extra_pieces']})"]
    return "\n".join(lines)


def _counts(counter: Mapping[str, int]) -> str:
    return " · ".join(f"{name} {count:,}" for name, count in counter.items())


# --- The coverage table ---------------------------------------------------------------------


@dataclass(frozen=True)
class CoverageQuestion:
    id: str
    text: str
    language: str
    gold: tuple[Mapping[str, str], ...]
    no_answer: str = ""


@dataclass(frozen=True)
class CoverageCell:
    id: str
    area: str
    name: str
    terms: tuple[str, ...]
    questions: tuple[CoverageQuestion, ...]
    safety: int = 0
    use: int = 0


@dataclass(frozen=True)
class CoverageTable:
    areas: Mapping[str, str]
    cells: tuple[CoverageCell, ...]


def load_coverage(path: Path = COVERAGE_FILE) -> CoverageTable:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    areas = {str(area["id"]): str(area["name"]) for area in payload["areas"]}
    cells = []
    for cell in payload["cells"]:
        importance = dict(cell.get("importance") or {})
        cells.append(CoverageCell(
            id=str(cell["id"]), area=str(cell["area"]), name=str(cell["name"]),
            terms=tuple(str(term) for term in cell.get("terms", ())),
            safety=int(importance.get("safety", 0)), use=int(importance.get("use", 0)),
            questions=tuple(CoverageQuestion(
                id=str(q["id"]), text=str(q["text"]), language=str(q.get("language", "ko")),
                gold=tuple(dict(g) for g in q.get("gold", ())), no_answer=str(q.get("no_answer", "")),
            ) for q in cell["questions"]),
        ))
    return CoverageTable(areas=areas, cells=tuple(cells))


def _term_pattern(terms: Iterable[str]) -> tuple[re.Pattern[str] | None, tuple[str, ...]]:
    """A Latin-letter terms regex (whole words) and the Hangul terms (substrings)."""

    latin, hangul = [], []
    for term in terms:
        flat = _flat(term)
        if not flat:
            continue
        (hangul if _HANGUL.search(flat) else latin).append(flat)
    pattern = None
    if latin:
        alternatives = "|".join(re.escape(term) for term in sorted(set(latin), key=len, reverse=True))
        pattern = re.compile(rf"(?<![a-z0-9])(?:{alternatives})(?![a-z0-9])")
    return pattern, tuple(sorted(set(hangul)))


def related_pieces(bundle: Bundle, terms: Sequence[str]) -> list[dict[str, Any]]:
    """The pieces whose title or words carry one of ``terms``."""

    pattern, hangul = _term_pattern(terms)
    found = []
    for record in bundle.records:
        title, _locator, text = bundle.flat(record)
        haystack = f"{title}\n{text}"
        if (pattern is not None and pattern.search(haystack)) or any(term in haystack for term in hangul):
            found.append(record)
    return found


def verdict(found: int, answerable: int, total: int) -> str:
    """✓ two thirds found in the top five; ✗ no gold piece in the bundle at all; △ between."""

    if total and found / total >= SUFFICIENT_SHARE - 1e-9:
        return "✓"
    if found or answerable:
        return "△"
    return "✗"


def glossary_gaps(glossary: Glossary, text: str) -> list[str]:
    """The Korean words of ``text`` (particles off) the glossary has no English name for."""

    known = set()
    for words in glossary.matches(text):
        known.update(words.split())
    gaps = []
    for token in re.split(r"[\s,.;:!?()\[\]{}\"'“”‘’·…/~]+", text):
        if not token or not _HANGUL.search(token) or token in known:
            continue
        stem = token
        for _ in range(2):  # "흄후드에서만" → "흄후드에서" → "흄후드"
            for particle in sorted(_PARTICLES_FOR_GAPS, key=len, reverse=True):
                if stem.endswith(particle) and len(stem) > len(particle) + 1:
                    stem = stem[: -len(particle)]
                    break
        if len(stem) < 2 or stem in KOREAN_FUNCTION_WORDS or token in KOREAN_FUNCTION_WORDS:
            continue
        if token.endswith(_SPOKEN_ENDINGS):
            continue
        if glossary.lookup(token):
            continue
        gaps.append(stem)
    return gaps


#: Endings of a spoken verb or adjective ("괜찮아", "돌리는데", "보관할"): such a word is not a
#: name the glossary should hold. A few nouns end so too; the list is a candidate list for a person.
_SPOKEN_ENDINGS = (
    "는데", "던데", "는지", "으면", "하면", "되면", "려면", "면서", "다며", "했어", "었어", "았어", "겠어",
    "해", "돼", "야", "지", "까", "니", "요", "어", "아", "줘", "워", "봐", "써", "껴", "둬", "놔", "데", "며",
    "래", "냐", "죠", "네", "려", "할", "될", "볼", "쓸",
)
_PARTICLES_FOR_GAPS = (
    "에서는", "으로는", "이랑", "에서", "으로", "에는", "에도", "이나", "하고", "까지", "부터", "보다",
    "은", "는", "이", "가", "을", "를", "의", "에", "로", "와", "과", "도", "만", "랑",
)


async def coverage_map(
    bundle: Bundle, table: CoverageTable, source: PublicKnowledgeSource, *, top_k: int = TOP_K,
) -> dict[str, Any]:
    """Each cell's related pieces and its questions searched as the server searches."""

    grade_of = {str(r["id"]): str(r.get("grade", "")) for r in bundle.records}
    cells: list[dict[str, Any]] = []
    unresolved: list[str] = []
    latencies: list[float] = []
    gaps_all: Counter[str] = Counter()
    gaps_missed: Counter[str] = Counter()
    for cell in table.cells:
        related = related_pieces(bundle, cell.terms)
        grades = Counter(str(r.get("grade", "")) for r in related)
        rows = []
        for question in cell.questions:
            gold = bundle.gold_ids(question.gold) if question.gold else set()
            if question.gold and not gold:
                unresolved.append(question.id)
            started = time.perf_counter()
            hits = await search(source, question.text, language=question.language, limit=top_k)
            latencies.append((time.perf_counter() - started) * 1000)
            ids = [hit.detail("doc_id") for hit in hits]
            rank = next((index for index, doc_id in enumerate(ids, 1) if doc_id in gold), None)
            gold_hit = hits[rank - 1] if rank else None
            gaps = glossary_gaps(source.glossary, question.text) if question.language == "ko" else []
            gaps_all.update(gaps)
            if rank is None and question.language == "ko":
                gaps_missed.update(gaps)
            rows.append({
                "id": question.id, "text": question.text, "language": question.language,
                "gold_pieces": len(gold), "gold_grades": dict(Counter(grade_of[i] for i in gold).most_common()),
                "no_answer": question.no_answer, "rank": rank, "found": rank is not None,
                "shown": bool(gold_hit and gold_hit.detail("shares")),
                "top": [{"id": hit.detail("doc_id"), "source_id": hit.detail("source_id"), "grade": hit.detail("grade"),
                         "title": hit.title, "locator": hit.locator, "shares": hit.detail("shares")} for hit in hits],
                "glossary_gaps": gaps,
            })
        total = len(rows)
        found = sum(1 for row in rows if row["found"])
        answerable = sum(1 for row in rows if row["gold_pieces"])
        korean = [row for row in rows if row["language"] == "ko"]
        gold_grades: Counter[str] = Counter()
        for row in rows:
            gold_grades.update(row["gold_grades"].keys())
        cells.append({
            "id": cell.id, "area": cell.area, "area_name": table.areas.get(cell.area, cell.area), "name": cell.name,
            "importance": {"safety": cell.safety, "use": cell.use},
            "related": len(related), "related_by_grade": {grade: grades.get(grade, 0) for grade in ("A", "B", "C")},
            "related_wiki_only": bool(related) and grades.get("A", 0) + grades.get("B", 0) == 0,
            "questions": total, "answerable": answerable, "found": found,
            "shown": sum(1 for row in rows if row["shown"]),
            "recall_at_5": round(found / total, 3) if total else 0.0,
            "korean_questions": len(korean), "korean_found": sum(1 for row in korean if row["found"]),
            "korean_recall_at_5": round(sum(1 for row in korean if row["found"]) / len(korean), 3) if korean else None,
            "gold_wiki_only": bool(gold_grades) and set(gold_grades) == {"C"},
            "verdict": verdict(found, answerable, total),
            "rows": rows,
        })
    all_rows = [row for cell in cells for row in cell["rows"]]
    korean_rows = [row for row in all_rows if row["language"] == "ko"]
    english_rows = [row for row in all_rows if row["language"] != "ko"]
    by_area: dict[str, dict[str, Any]] = {}
    for cell in cells:
        area = by_area.setdefault(cell["area"], {"name": cell["area_name"], "cells": 0, "✓": 0, "△": 0, "✗": 0,
                                                 "questions": 0, "found": 0})
        area["cells"] += 1
        area[cell["verdict"]] += 1
        area["questions"] += cell["questions"]
        area["found"] += cell["found"]
    for area in by_area.values():
        area["recall_at_5"] = round(area["found"] / area["questions"], 3) if area["questions"] else 0.0
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "top_k": top_k, "alpha": source.settings.alpha, "index": source.settings.index_name,
        "glossary_terms": len(source.glossary),
        "cells": cells,
        "unresolved_gold": unresolved,
        "totals": {
            "cells": len(cells), **{mark: sum(1 for c in cells if c["verdict"] == mark) for mark in VERDICTS},
            "questions": len(all_rows), "answerable": sum(1 for r in all_rows if r["gold_pieces"]),
            "found": sum(1 for r in all_rows if r["found"]),
            "recall_at_5": round(sum(1 for r in all_rows if r["found"]) / len(all_rows), 3) if all_rows else 0.0,
            "answerable_recall_at_5": _share([r for r in all_rows if r["gold_pieces"]]),
            "korean_questions": len(korean_rows), "korean_recall_at_5": _share(korean_rows),
            "korean_answerable_recall_at_5": _share([r for r in korean_rows if r["gold_pieces"]]),
            "english_questions": len(english_rows), "english_recall_at_5": _share(english_rows),
            "english_answerable_recall_at_5": _share([r for r in english_rows if r["gold_pieces"]]),
            "latency_ms_p50": round(statistics.median(latencies), 1) if latencies else None,
            "latency_ms_p95": round(pipeline._percentile(latencies, 0.95), 1) if latencies else None,
        },
        "by_area": by_area,
        "glossary_gaps": {"all": dict(gaps_all.most_common()), "in_missed_questions": dict(gaps_missed.most_common())},
    }


def _share(rows: Sequence[Mapping[str, Any]]) -> float | None:
    return round(sum(1 for r in rows if r["found"]) / len(rows), 3) if rows else None


def map_markdown(result: Mapping[str, Any]) -> str:
    lines = ["| 분야 | 칸 | 판정 | 관련 조각 A/B/C | 질문 | 정답 있음 | 찾음@5 | 한국어 찾음 | 비고 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for cell in result["cells"]:
        grades = cell["related_by_grade"]
        notes = []
        if cell["related_wiki_only"]:
            notes.append("관련 조각 위키만")
        if cell["gold_wiki_only"]:
            notes.append("정답 위키만")
        if cell["answerable"] and not cell["found"]:
            notes.append("자료는 있으나 못 찾음")
        korean = (f"{cell['korean_found']}/{cell['korean_questions']}" if cell["korean_questions"] else "—")
        lines.append(f"| {cell['area_name']} | {cell['name']} | {cell['verdict']} | {grades['A']}/{grades['B']}/{grades['C']} | "
                     f"{cell['questions']} | {cell['answerable']} | {cell['found']} | {korean} | {', '.join(notes)} |")
    totals = result["totals"]
    lines += ["", f"칸 {totals['cells']}: ✓ {totals['✓']} · △ {totals['△']} · ✗ {totals['✗']}; 질문 {totals['questions']} "
                  f"(정답 자료 있음 {totals['answerable']}), recall@5 {totals['recall_at_5']} "
                  f"(정답 있는 질문만 {totals['answerable_recall_at_5']}); 한국어 {totals['korean_questions']}문 "
                  f"{totals['korean_recall_at_5']} · 영어 {totals['english_questions']}문 {totals['english_recall_at_5']}"]
    return "\n".join(lines)


def summary_markdown(result: Mapping[str, Any]) -> str:
    """The one-page ✓·△·✗ picture: one line per area, the cells' marks in order."""

    lines = ["| 분야 | ✓ | △ | ✗ | recall@5 | 칸별 |", "|---|---|---|---|---|---|"]
    for area_id, area in result["by_area"].items():
        marks = " ".join(f"{cell['verdict']}{cell['name']}" for cell in result["cells"] if cell["area"] == area_id)
        lines.append(f"| {area['name']} | {area['✓']} | {area['△']} | {area['✗']} | {area['recall_at_5']} | {marks} |")
    return "\n".join(lines)


# --- The help through the server's turn -----------------------------------------------------


class RecordingSource:
    """The public tier with what each search returned kept for the report."""

    def __init__(self, source: PublicKnowledgeSource) -> None:
        self.tier = source.tier
        self._source = source
        self.calls: list[dict[str, Any]] = []

    async def search(self, query: str, *, limit: int, context: KnowledgeContext) -> list[KnowledgeHit]:
        started = time.perf_counter()
        hits = await self._source.search(query, limit=limit, context=context)
        self.calls.append({"query": query, "limit": limit, "ms": round((time.perf_counter() - started) * 1000, 1),
                           "hits": hits})
        return hits


def starter_pack_protocol_ids(protocol_data_dir: Path) -> dict[str, str]:
    """Starter-pack row id → protocol id in a catalog folder, by the source file's checksum."""

    import csv  # noqa: PLC0415
    import sqlite3  # noqa: PLC0415

    database = protocol_data_dir / "protocol_workspace.sqlite"
    if not database.is_file():
        return {}
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        by_checksum = {checksum: experiment_id for experiment_id, checksum in connection.execute(
            "SELECT experiment_id, pdf_checksum FROM protocol_revisions")}
    finally:
        connection.close()
    with STARTER_PACK_FILE.open(encoding="utf-8") as handle:
        return {row["id"]: by_checksum[row["sha256"]] for row in csv.DictReader(handle) if row["sha256"] in by_checksum}


def _help_environment(protocol_data_dir: Path) -> None:
    """The server's settings for an offline help run: no provider, no workspace, no repository .env."""

    os.environ.update({
        "PYTEST_VERSION": os.environ.get("PYTEST_VERSION", "public-knowledge-audit"),
        "VOINEY_LAB_PROTOCOL_ENABLED": "true",
        "VOINEY_LAB_PROTOCOL_DATA_DIR": str(protocol_data_dir),
        "VOINEY_LAB_WORKSPACE_ENABLED": "false",
        "VOINEY_LAB_EXPERIMENT_REPORTS_ENABLED": "false",
        "VOINEY_LAB_LLM_ROUTER_ENABLED": "false",
        "VOINEY_LAB_STT_PROVIDER": "xai", "VOINEY_LAB_TTS_PROVIDER": "xai",
        "VOINEY_LAB_USAGE_SCOPE": "demo",
    })


def _load_help_fixture(server: Any, name: str, protocol_ids: Mapping[str, str]) -> Any:
    if name == "in_gel":
        from voiney_lab.curated_protocol import load_curated_protocol_fixture  # noqa: PLC0415

        return load_curated_protocol_fixture(
            IN_GEL_FIXTURE_DIR / "candidate_a_curated_analysis.json",
            IN_GEL_FIXTURE_DIR / "candidate_a_curated_analysis.provenance.json",
            IN_GEL_PDF)
    catalog, store = server._open_protocol_catalog()
    try:
        fixture = catalog.load_executable_fixture(protocol_ids[name])
    finally:
        store.close()
    return server._with_revision_translations(fixture, start_missing=False)


FAKE_CAUSES = "가짜 원인 후보입니다."


async def _help_case(server: Any, case: Mapping[str, Any], fixture: Any, public: RecordingSource) -> dict[str, Any]:
    """One case of lane HELP's set through ``server.run_turn``; fakes for STT, TTS and the supplemental model."""

    from types import SimpleNamespace  # noqa: PLC0415
    from unittest.mock import patch  # noqa: PLC0415

    from voiney_lab.answer_checks import public_reference_violations  # noqa: PLC0415
    from voiney_lab.curated_protocol import CuratedProtocolSession  # noqa: PLC0415
    from voiney_lab.experiment_reports import ExperimentReportStore  # noqa: PLC0415
    from voiney_lab.external_references import SupplementalKnowledgeSettings, public_reference_part  # noqa: PLC0415
    from voiney_lab.language import Transcription  # noqa: PLC0415
    from voiney_lab.public_knowledge import PUBLIC_LABEL  # noqa: PLC0415
    from voiney_lab.tools import ToolContext  # noqa: PLC0415
    from voiney_lab.vad import TurnState  # noqa: PLC0415

    language = str(case.get("language") or "ko")
    with tempfile.TemporaryDirectory() as directory:
        store = ExperimentReportStore(Path(directory) / "reports.sqlite")
        workflow = CuratedProtocolSession(fixture)
        workflow.activate_configured()
        workflow.plan("시작" if language == "ko" else "start", turn_id=1, language=language, configuration_id=1, generation=1)
        step = str(case["step"])
        workflow.current_index = (int(step[1:]) if step.startswith("#")
                                  else [s.source_label for s in fixture.steps].index(step))
        workflow._notebook_question = None
        report = store.open_report(
            session_id="audit", protocol_id=fixture.protocol_id, protocol_title=fixture.title,
            protocol_revision=fixture.revision_id, protocol_sha256="a" * 64, readiness_status="ready",
            development_only=True, organization_id="lab-audit", owner_principal_id="account-audit")
        session = server.ListenerSession(
            tool_context=ToolContext(Path("/unused/offline-catalog"), None, "ko", "test_only"),
            curated_protocol_session=workflow, experiment_report_store=store)
        session.experiment_report_id = report["report_id"]
        session.active = True
        session.active_turn_id = 2
        session.next_turn_id = 3
        session.turn_generations[2] = session.generation
        session.accept_configuration(41, "cascade", language, fixture.protocol_id)
        session.detector.state = TurnState.PROCESSING
        session.supplemental_knowledge_settings = SupplementalKnowledgeSettings(True, "offline-supplement", 2.0)
        session.public_knowledge = public
        sent: list[dict[str, Any]] = []
        spoken: list[str] = []

        class Socket:
            async def send_text(self, value: str) -> None:
                sent.append(json.loads(value))

            async def send_bytes(self, value: bytes) -> None:
                pass

        class Explainer:
            def __init__(self, *args: Any) -> None:
                pass

            async def explain_possible_causes(self, problem: str, **kwargs: Any) -> dict[str, Any]:
                return {"status": "success", "answer": FAKE_CAUSES, "backend": "supplemental_possible_causes"}

        async def paraphrase(*args: Any, **kwargs: Any) -> dict[str, Any]:
            # The model's sentence is not measured here: the server's own form is said.
            return {"status": "provider_error"}

        def synthesize(text: str, *args: Any, **kwargs: Any) -> bytes:
            spoken.append(text)
            return b"\0\0" * 1600

        calls_before = len(public.calls)
        dropped: list[str] = []

        class Watch(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                if "problem help dropped" in record.getMessage():
                    dropped.append(record.getMessage())

        watch = Watch()
        logging.getLogger("voiney_lab").addHandler(watch)
        try:
            with patch("voiney_lab.server.transcribe", return_value=Transcription(str(case["utterance"]), language)), \
                    patch("voiney_lab.server.synthesize", side_effect=synthesize), \
                    patch("voiney_lab.server.XaiSupplementalKnowledge", Explainer), \
                    patch("voiney_lab.server.paraphrase_public_reference", side_effect=paraphrase), \
                    patch("voiney_lab.server._role_client", return_value=SimpleNamespace()), \
                    patch.dict("os.environ", {"VOINEY_LAB_SUPPLEMENTAL_PROVIDER": "xai",
                                              "VOINEY_LAB_SUPPLEMENTAL_MODEL": "offline-supplement"}):
                session.endpoint_at = time.perf_counter()
                await server.run_turn(Socket(), session, b"\0\0", 2, 1)
        finally:
            logging.getLogger("voiney_lab").removeHandler(watch)
        reply = next((item["text"] for item in reversed(sent) if item["type"] == "reply.complete"), "")
        calls = public.calls[calls_before:]
        hits = list(calls[-1]["hits"]) if calls else []
        part = public_reference_part(hits, language=language)
        shown = [hit for hit in hits if hit.detail("shares")][:2]
        said = " ".join(spoken)
        sentence = _spoken_fact(part, language) if part else ""
        # The server reads a piece's sentence only when it passes the rule; checked again here.
        violations = list(public_reference_violations(sentence, evidence=part.first_text)) if sentence else []
        return {
            "id": case["id"], "set": case.get("set", ""), "protocol": case["protocol"], "step": step,
            "utterance": case["utterance"], "expect_help": bool(case["expect_help"]),
            "help_shown": any(text in reply for text in ("조건을 바꾸려면 담당자와 확인하세요.",
                                                          "To change a condition, check with the person in charge first.")),
            "searched": bool(calls), "search_ms": calls[-1]["ms"] if calls else None,
            "public_found": len(hits), "public_shown": len(shown) if PUBLIC_LABEL in reply else 0,
            "public_in_reply": PUBLIC_LABEL in reply, "public_said": PUBLIC_LABEL in said,
            "pieces": [{"source_id": hit.detail("source_id"), "grade": hit.detail("grade"), "title": hit.title,
                        "locator": hit.locator, "shares": hit.detail("shares"), "text": hit.text[:240]} for hit in shown],
            "spoken_public": part.spoken if part else "",
            "spoken_kind": ("fact" if sentence else "pointer") if part else "",
            "public_rule_violations": violations,
            "help_dropped": dropped,
        }


def _spoken_fact(part: Any, language: str) -> str:
    """The piece's sentence in the spoken public form ("<표시>: <문장>"), or "" when it points to the screen."""

    from voiney_lab.external_references import PUBLIC_WIKI_ONLY  # noqa: PLC0415
    from voiney_lab.public_knowledge import public_reference_label  # noqa: PLC0415

    head = f"{public_reference_label(part.first_source_name)}: "
    if not part.spoken.startswith(head):
        return ""
    sentence = part.spoken[len(head):]
    note = PUBLIC_WIKI_ONLY.get(language if language in PUBLIC_WIKI_ONLY else "ko", "")
    return sentence[: -len(note)].strip() if note and sentence.endswith(note) else sentence.strip()


async def help_run(
    source: PublicKnowledgeSource, *, protocol_data_dir: Path, cases_file: Path = HELP_SET_FILE,
    only_protocols: set[str] | None = None,
) -> dict[str, Any]:
    """Lane HELP's set through the server's turn with the public tier on (offline fakes elsewhere)."""

    _help_environment(protocol_data_dir)
    from voiney_lab import server  # noqa: PLC0415

    for name in ("XAI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        os.environ.pop(name, None)
    protocol_ids = starter_pack_protocol_ids(protocol_data_dir)
    cases = json.loads(Path(cases_file).read_text(encoding="utf-8"))["cases"]
    public = RecordingSource(source)
    fixtures: dict[str, Any] = {}
    rows: list[dict[str, Any]] = []
    skipped: Counter[str] = Counter()
    for case in cases:
        name = str(case["protocol"])
        if only_protocols is not None and name not in only_protocols:
            continue
        if name == "in_gel" and not IN_GEL_PDF.is_file():
            skipped["in_gel_pdf_missing"] += 1
            continue
        if name != "in_gel" and name not in protocol_ids:
            skipped[f"{name}_not_in_catalog"] += 1
            continue
        if name not in fixtures:
            fixtures[name] = _load_help_fixture(server, name, protocol_ids)
        rows.append(await _help_case(server, case, fixtures[name], public))
    helped = [row for row in rows if row["expect_help"]]
    latencies = [row["search_ms"] for row in rows if row["search_ms"] is not None]
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "index": source.settings.index_name, "alpha": source.settings.alpha,
        "cases": len(rows), "skipped": dict(skipped), "help_cases": len(helped),
        "help_shown": sum(1 for row in helped if row["help_shown"]),
        "searched": sum(1 for row in helped if row["searched"]),
        "public_attached": sum(1 for row in helped if row["public_in_reply"]),
        "public_attached_share": round(sum(1 for row in helped if row["public_in_reply"]) / len(helped), 3) if helped else None,
        "not_help_with_public": sum(1 for row in rows if not row["expect_help"] and row["public_in_reply"]),
        "spoken_fact": sum(1 for row in helped if row["public_in_reply"] and row["spoken_kind"] == "fact"),
        "spoken_pointer": sum(1 for row in helped if row["public_in_reply"] and row["spoken_kind"] == "pointer"),
        "public_rule_violations": sum(1 for row in rows if row["public_rule_violations"]),
        "help_dropped": sum(1 for row in rows if row["help_dropped"]),
        "search_ms_p50": round(statistics.median(latencies), 1) if latencies else None,
        "search_ms_max": round(max(latencies), 1) if latencies else None,
        "rows": rows,
    }


def help_markdown(result: Mapping[str, Any], judgements: Mapping[str, Mapping[str, str]] | None = None) -> str:
    """The table a person reads: each help row, the public pieces attached, and a judgement column."""

    judgements = judgements or {}
    lines = ["| id | 프로토콜 | 단계 | 말 | 붙은 공개 조각 (출처 · 제목 · 겹친 낱말) | 판단 | 근거 |",
             "|---|---|---|---|---|---|---|"]
    for row in result["rows"]:
        if not row["expect_help"]:
            continue
        pieces = "<br>".join(f"{p['source_id']}({p['grade']}) · {p['title']} · “{p['shares']}”" for p in row["pieces"]) \
            if row["public_in_reply"] else "—"
        judged = judgements.get(row["id"], {})
        lines.append(f"| {row['id']} | {row['protocol']} | {row['step']} | {row['utterance']} | {pieces} | "
                     f"{judged.get('verdict', '')} | {judged.get('reason', '')} |")
    return "\n".join(lines)


# --- find / search (writing the table) ------------------------------------------------------


def find(bundle: Bundle, terms: Sequence[str], *, source_id: str | None = None, limit: int = 40) -> list[str]:
    """One line per piece carrying any of ``terms``: id, source, grade, title, locator, the words around it."""

    pattern, hangul = _term_pattern(terms)
    lines: list[str] = []
    count = 0
    for record in bundle.records:
        if source_id and record.get("source_id") != source_id:
            continue
        title, _locator, text = bundle.flat(record)
        haystack = f"{title}\n{text}"
        match = pattern.search(haystack) if pattern is not None else None
        position = match.start() if match else next((haystack.find(t) for t in hangul if t in haystack), -1)
        if position < 0:
            continue
        count += 1
        if len(lines) < limit:
            start = max(0, position - 60)
            snippet = haystack[start:position + 140].replace("\n", " ")
            lines.append(f"{record['id']} | {record.get('source_id')} {record.get('grade')} | {record.get('title')} | "
                         f"{record.get('locator')} | …{snippet}…")
    lines.append(f"({count} pieces)")
    return lines


# --- The command line -----------------------------------------------------------------------


def _write(path: str | None, text: str) -> None:
    if path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(text, encoding="utf-8")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=1, default=lambda item: sorted(item) if isinstance(item, set) else str(item))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="공개 지식 점검: 재고·덮는 범위 지도·도움에서의 쓰임 (새 수집 없음)")
    parser.add_argument("--runtime-dir", default=str(pipeline.DEFAULT_RUNTIME_DIR),
                        help="공개 지식 묶음 폴더 (기본 data/runtime/public-knowledge; 읽기만)")
    parser.add_argument("--sources", default=str(pipeline.SOURCES_FILE), help="출처 목록 TOML")
    # The same two options after the command too ("map --runtime-dir DIR ..."); given there, they win.
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--runtime-dir", default=argparse.SUPPRESS, help="공개 지식 묶음 폴더 (읽기만)")
    shared.add_argument("--sources", default=argparse.SUPPRESS, help="출처 목록 TOML")
    commands = parser.add_subparsers(dest="command", required=True)

    def backend_options(command: argparse.ArgumentParser) -> None:
        command.add_argument("--backend", choices=("fts5", "moss"), required=True,
                             help="moss: 서버의 색인을 한 번 불러와 프로세스 안에서 질의 / fts5: 묶음의 FTS5 (오프라인)")
        command.add_argument("--index", help="Moss 색인 이름 (기본: 서버 설정, 없으면 voiney-public-knowledge)")
        command.add_argument("--env-file", help="MOSS_PROJECT_ID·MOSS_PROJECT_KEY 만 읽을 dotenv 파일 (값은 출력하지 않음)")
        command.add_argument("--cache-dir", help="Moss 색인 디스크 캐시 폴더")

    inv = commands.add_parser("inventory", parents=[shared], help="출처·등급·라이선스·언어·분야·종류별 조각 수, 중복, 무작위 표본")
    inv.add_argument("--sample-size", type=int, default=SAMPLE_SIZE)
    inv.add_argument("--seed", type=int, default=SAMPLE_SEED)
    inv.add_argument("--out", help="결과 JSON")
    inv.add_argument("--out-md", help="표 (Markdown)")
    mapping = commands.add_parser("map", parents=[shared], help="덮는 범위 지도: 칸마다 관련 조각 수와 질문 검색 시험")
    backend_options(mapping)
    mapping.add_argument("--coverage", default=str(COVERAGE_FILE), help="덮는 범위 표 JSON")
    mapping.add_argument("--out", help="결과 JSON")
    mapping.add_argument("--out-md", help="표 (Markdown)")
    helping = commands.add_parser("help", parents=[shared], help="줄 HELP 평가 세트를 공개 자료 층을 켠 채 서버 턴으로")
    backend_options(helping)
    helping.add_argument("--protocol-data-dir", required=True, help="스타터 팩 프로토콜 카탈로그 폴더 (사본)")
    helping.add_argument("--cases", default=str(HELP_SET_FILE))
    helping.add_argument("--out", help="결과 JSON")
    helping.add_argument("--out-md", help="표 (Markdown)")
    everything = commands.add_parser("all", parents=[shared], help="map 과 help 를 색인 한 번 불러와서")
    backend_options(everything)
    everything.add_argument("--coverage", default=str(COVERAGE_FILE))
    everything.add_argument("--protocol-data-dir", required=True)
    everything.add_argument("--cases", default=str(HELP_SET_FILE))
    everything.add_argument("--out-dir", required=True)
    finding = commands.add_parser("find", parents=[shared], help="낱말을 가진 조각 (표 쓰기용)")
    finding.add_argument("terms", nargs="+")
    finding.add_argument("--source", help="이 출처 id 만")
    finding.add_argument("--limit", type=int, default=40)
    searching = commands.add_parser("search", parents=[shared], help="서버처럼 찾은 상위 조각 (표 쓰기용)")
    backend_options(searching)
    searching.add_argument("queries", nargs="+", help="질문 (여럿이면 색인 한 번에 차례로)")
    searching.add_argument("--language", default="ko")
    searching.add_argument("--limit", type=int, default=TOP_K)
    return parser


async def _with_source(args: argparse.Namespace, bundle: Bundle, job: Any) -> tuple[Any, OpenedSource]:
    opened = await open_source(args.backend, bundle, env_file=args.env_file, cache_dir=args.cache_dir, index_name=args.index)
    try:
        return await job(opened.source), opened
    finally:
        await opened.source.close()


def _load_line(opened: OpenedSource) -> dict[str, Any]:
    return {"backend": opened.backend, "load_s": opened.load_seconds, "moss_loads": opened.moss_loads,
            "rss_before_mib": opened.rss_before_mib, "rss_after_load_mib": opened.rss_after_load_mib}


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    bundle = Bundle.read(Path(args.runtime_dir))
    if args.command == "inventory":
        result = inventory(bundle, pipeline.load_sources(Path(args.sources)), sample_size=args.sample_size, seed=args.seed)
        _write(args.out, _json(result))
        markdown = inventory_markdown(result)
        _write(args.out_md, markdown)
        print(markdown)
        return 0
    if args.command == "find":
        print("\n".join(find(bundle, args.terms, source_id=args.source, limit=args.limit)))
        return 0
    if args.command == "search":
        async def job(source: PublicKnowledgeSource) -> list[list[KnowledgeHit]]:
            return [await search(source, query, language=args.language, limit=args.limit) for query in args.queries]

        found, _opened = asyncio.run(_with_source(args, bundle, job))
        for query, hits in zip(args.queries, found):
            print(f"## {query}")
            for rank, hit in enumerate(hits, 1):
                print(f"{rank}. {hit.detail('doc_id')} | {hit.detail('source_id')} {hit.detail('grade')} | {hit.title} | "
                      f"{hit.locator} | shares={hit.detail('shares')!r} | {hit.text[:160]!r}")
        return 0
    if args.command == "map":
        table = load_coverage(Path(args.coverage))

        async def job(source: PublicKnowledgeSource) -> dict[str, Any]:
            return await coverage_map(bundle, table, source)

        result, opened = asyncio.run(_with_source(args, bundle, job))
        result["load"] = _load_line(opened)
        _write(args.out, _json(result))
        markdown = f"{summary_markdown(result)}\n\n{map_markdown(result)}"
        _write(args.out_md, markdown)
        print(markdown)
        print(json.dumps({"unresolved_gold": result["unresolved_gold"], "load": result["load"]}, ensure_ascii=False))
        return 1 if result["unresolved_gold"] else 0
    if args.command == "help":
        async def job(source: PublicKnowledgeSource) -> dict[str, Any]:
            return await help_run(source, protocol_data_dir=Path(args.protocol_data_dir), cases_file=Path(args.cases))

        result, opened = asyncio.run(_with_source(args, bundle, job))
        result["load"] = _load_line(opened)
        _write(args.out, _json(result))
        _write(args.out_md, help_markdown(result))
        print(json.dumps({key: value for key, value in result.items() if key != "rows"}, ensure_ascii=False))
        return 0
    if args.command == "all":
        table = load_coverage(Path(args.coverage))
        out_dir = Path(args.out_dir)

        async def job(source: PublicKnowledgeSource) -> tuple[dict[str, Any], dict[str, Any]]:
            mapped = await coverage_map(bundle, table, source)
            helped = await help_run(source, protocol_data_dir=Path(args.protocol_data_dir), cases_file=Path(args.cases))
            return mapped, helped

        (mapped, helped), opened = asyncio.run(_with_source(args, bundle, job))
        mapped["load"] = helped["load"] = _load_line(opened)
        _write(str(out_dir / "map.json"), _json(mapped))
        _write(str(out_dir / "map.md"), f"{summary_markdown(mapped)}\n\n{map_markdown(mapped)}")
        _write(str(out_dir / "help.json"), _json(helped))
        _write(str(out_dir / "help.md"), help_markdown(helped))
        print(summary_markdown(mapped))
        print(json.dumps({key: value for key, value in helped.items() if key != "rows"}, ensure_ascii=False))
        return 1 if mapped["unresolved_gold"] else 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
