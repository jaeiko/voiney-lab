"""Public knowledge: licence-checked public material searched inside the server.

Lane KNOW (human decision 2026-10-24). A researcher's question during a run
may be answered with public experimental, safety and scientific material --
never with the lab's own documents -- found by Moss, an in-process search
runtime: the server loads one index at start-up and every query runs in
memory. What goes into that index is decided by
``scripts/public_knowledge_pipeline.py`` and the source list
``data/public_knowledge/sources.toml`` (public domain, CC0, CC BY and
CC BY-SA only, each with its licence evidence); this module only searches it.

This is the ``public`` tier of the knowledge contract (lane rules 10/24, 7):
``PublicKnowledgeSource.search`` returns ``KnowledgeHit`` rows and raises
``KnowledgeUnavailable`` naming the missing setting when it cannot search.
Nothing here changes workflow state, writes an answer or falls back to
another provider: with no ``MOSS_PROJECT_ID``/``MOSS_PROJECT_KEY`` or a load
that fails, this tier alone is off and the server says why by setting name.

A Korean question meets English material through the glossary
(``data/public_knowledge/glossary.json``, Wikidata labels, CC0): the Korean
words of the question, their particles taken off, are joined by their English
names before the query is embedded. That is deterministic; no model sees the
question here.

Every hit carries where it comes from -- source name, licence, address, the
source grade of additional decision 3 (A: public institution, standard or
peer-reviewed article; B: open textbook; C: wiki) -- and the answer rules of
decision 6 (``answer_checks.public_reference_violations``) hold whoever
phrases the answer.
"""

from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

try:  # The contract module arrives with lane HELP; until it is merged the
    # same shapes are defined here (lane rules 10/24, 7: lane KNOW follows it).
    from voiney_lab.knowledge import KnowledgeContext, KnowledgeHit, KnowledgeUnavailable
except ImportError:  # pragma: no cover - exercised only before lane HELP merges

    class KnowledgeUnavailable(RuntimeError):  # type: ignore[no-redef]
        """A source cannot be searched; ``setting`` names what is missing or off."""

        def __init__(self, setting: str, message: str = "") -> None:
            super().__init__(message or f"{setting} is not set (설정 필요)")
            self.setting = setting

    @dataclass(frozen=True)
    class KnowledgeContext:  # type: ignore[no-redef]
        organization_id: str | None
        account_id: str | None
        protocol_id: str
        revision_id: str | None
        step_id: str | None
        language: str = "ko"

    @dataclass(frozen=True)
    class KnowledgeHit:  # type: ignore[no-redef]
        tier: str
        title: str
        text: str
        locator: str
        source_url: str | None = None
        license: str | None = None
        language: str = "en"
        score: float = 0.0
        details: tuple[tuple[str, str], ...] = ()

        def detail(self, name: str, default: str = "") -> str:
            return next((value for key, value in self.details if key == name), default)


__all__ = [
    "DEFAULT_INDEX_NAME",
    "ENABLED_SETTING",
    "FoundDocument",
    "GRADES",
    "GRADE_WORDS",
    "Glossary",
    "INDEX_SETTING",
    "KnowledgeContext",
    "KnowledgeHit",
    "KnowledgeUnavailable",
    "MOSS_PROJECT_ID_SETTING",
    "MOSS_PROJECT_KEY_SETTING",
    "MossSdkClient",
    "PUBLIC_LABEL",
    "PUBLIC_TIER",
    "PublicKnowledgeSettings",
    "PublicKnowledgeSource",
    "SearchClient",
    "default_glossary_path",
    "grade_rank",
    "index_text_for",
    "public_reference_label",
    "start_public_knowledge",
    "wiki_only",
]

PUBLIC_TIER = "public"
#: How public material is named in speech and on the screen (decision 6):
#: "공개 자료 · 일반 참고 — <출처 이름>".
PUBLIC_LABEL = "공개 자료 · 일반 참고"
#: Additional decision 3: the source grades, best first, and their words.
GRADES: tuple[str, ...] = ("A", "B", "C")
GRADE_WORDS: Mapping[str, str] = {
    "A": "공공기관·표준·심사 논문",
    "B": "공개 교재",
    "C": "위키백과",
}
#: Settings. The Moss project credentials keep the provider's own names,
#: unprefixed, like ``OPENAI_API_KEY`` (human decision 4).
ENABLED_SETTING = "VOINEY_LAB_PUBLIC_KNOWLEDGE_ENABLED"
INDEX_SETTING = "VOINEY_LAB_PUBLIC_KNOWLEDGE_INDEX"
MOSS_PROJECT_ID_SETTING = "MOSS_PROJECT_ID"
MOSS_PROJECT_KEY_SETTING = "MOSS_PROJECT_KEY"
DEFAULT_INDEX_NAME = "voiney-public-knowledge"
#: The hybrid blend (1.0 semantic, 0.0 keyword; Moss's default is 0.8). A
#: Korean question carries English glossary words, so the keyword side has
#: something to hold on to: on the 130-question set (2026-10-24) 0.5 gave
#: recall@5 0.638 / MRR 0.579 against 0.546 / 0.519 at 0.7 and 0.500 / 0.447
#: at 1.0 (lane KNOW report, section 5).
DEFAULT_ALPHA = 0.5
#: How many results are read from the index for ``limit`` hits: the grade
#: ordering needs a few more than are kept.
CANDIDATES_PER_HIT = 3
_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off", ""})


def public_reference_label(source_name: str) -> str:
    """The spoken and shown mark of a public-material ground (decision 6)."""

    return f"{PUBLIC_LABEL} — {source_name}" if source_name else PUBLIC_LABEL


def grade_rank(grade: str) -> int:
    """Where a grade sorts: A before B before C; an unknown grade last."""

    try:
        return GRADES.index(grade)
    except ValueError:
        return len(GRADES)


# --- Settings -----------------------------------------------------------------------


class PublicKnowledgeConfigurationError(ValueError):
    """A setting has a value the server cannot use; the message names it."""


@dataclass(frozen=True)
class PublicKnowledgeSettings:
    enabled: bool
    index_name: str = DEFAULT_INDEX_NAME
    project_id: str = ""
    project_key: str = ""
    cache_dir: str | None = None
    alpha: float = DEFAULT_ALPHA

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None, *, cache_dir: str | None = None,
    ) -> "PublicKnowledgeSettings":
        """Read the settings; off unless ``VOINEY_LAB_PUBLIC_KNOWLEDGE_ENABLED`` says so.

        A malformed boolean is refused by name rather than read as off. The
        credentials are read but not required here: ``start_public_knowledge``
        raises ``KnowledgeUnavailable`` naming the one that is missing, so
        that the server can say so and keep the rest running.
        """

        env = os.environ if environment is None else environment
        raw = env.get(ENABLED_SETTING, "").strip().casefold()
        if raw in _TRUE:
            enabled = True
        elif raw in _FALSE:
            enabled = False
        else:
            raise PublicKnowledgeConfigurationError(
                f"{ENABLED_SETTING} must be true or false (설정 확인 필요)")
        index_name = env.get(INDEX_SETTING, "").strip() or DEFAULT_INDEX_NAME
        if "/" in index_name or len(index_name.encode("utf-8")) > 256:
            raise PublicKnowledgeConfigurationError(
                f"{INDEX_SETTING} must be an index name without '/' (설정 확인 필요)")
        return cls(
            enabled=enabled,
            index_name=index_name,
            project_id=env.get(MOSS_PROJECT_ID_SETTING, "").strip(),
            project_key=env.get(MOSS_PROJECT_KEY_SETTING, "").strip(),
            cache_dir=cache_dir,
        )

    def missing_setting(self) -> str | None:
        """The name of the credential that is not set, or None when both are."""

        if not self.project_id:
            return MOSS_PROJECT_ID_SETTING
        if not self.project_key:
            return MOSS_PROJECT_KEY_SETTING
        return None


# --- The glossary: Korean question words to English names -------------------------------

#: Korean particles that follow a noun in a question, longest first, so that
#: "원심분리기를" and "인화점이" find "원심분리기" and "인화점".
_PARTICLES: tuple[str, ...] = tuple(sorted({
    "에서는", "에서도", "으로는", "으로도", "에게서", "으로부터", "이라고", "라고는",
    "에게", "한테", "께서", "부터", "까지", "처럼", "보다", "마다", "조차", "마저",
    "이나", "이랑", "이든", "에서", "으로", "에는", "에도", "과는", "와는", "이라", "이란",
    "은", "는", "이", "가", "을", "를", "의", "에", "로", "과", "와", "도", "만", "나", "랑", "든",
    "란", "요",
}, key=len, reverse=True))
_HANGUL = re.compile(r"[가-힣]")
_TOKEN_SPLIT = re.compile(r"[\s,.;:!?()\[\]{}\"'“”‘’·…/]+")
#: The most English names appended to a query; more would drown the question.
_MAX_EXPANSION_TERMS = 6


def _normalise_term(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).casefold().split())


def _korean_stems(token: str) -> list[str]:
    """The token and the forms a trailing particle leaves: "인화점이" → ["인화점이", "인화점"]."""

    stems = [token]
    for particle in _PARTICLES:
        if token.endswith(particle) and len(token) > len(particle) + 1:
            stems.append(token[: -len(particle)])
    return stems


@dataclass(frozen=True)
class Glossary:
    """Korean names → English names (Wikidata labels and aliases, CC0).

    ``terms`` maps a normalised Korean term (NFKC, casefold, no spaces) to the
    English names of the same item. ``aliases`` maps an English title to its
    Korean names, for the pipeline to prefix English pieces with.
    """

    terms: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    aliases: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | str) -> "Glossary":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        terms = {
            _normalise_term(key): tuple(str(item) for item in values if str(item).strip())
            for key, values in dict(payload.get("terms", {})).items()
            if str(key).strip()
        }
        aliases = {
            str(key): tuple(str(item) for item in values if str(item).strip())
            for key, values in dict(payload.get("aliases", {})).items()
        }
        return cls(terms={k: v for k, v in terms.items() if v}, aliases=aliases)

    @classmethod
    def empty(cls) -> "Glossary":
        return cls()

    def __len__(self) -> int:
        return len(self.terms)

    def lookup(self, korean: str) -> tuple[str, ...]:
        """English names for one Korean word: exact, a particle taken off, or a prefix."""

        for stem in _korean_stems(korean):
            found = self.terms.get(_normalise_term(stem))
            if found:
                return found
        key = _normalise_term(korean)
        if len(key) >= 3:
            # "피펫팅" → "피펫": the longest glossary term the word starts with,
            # when it is most of the word.
            best = ""
            for term in self.terms:
                if len(term) >= 2 and key.startswith(term) and len(term) > len(best):
                    best = term
            if best and len(best) * 10 >= len(key) * 6:
                return self.terms[best]
        return ()

    def matches(self, query: str) -> dict[str, tuple[str, ...]]:
        """The Korean words of ``query`` the glossary knows, with their English names.

        Two neighbouring words are tried together first ("기준 전극"), then
        each on its own.
        """

        tokens = [token for token in _TOKEN_SPLIT.split(query) if token and _HANGUL.search(token)]
        found: dict[str, tuple[str, ...]] = {}
        skip = False
        for index, token in enumerate(tokens):
            if skip:
                skip = False
                continue
            if index + 1 < len(tokens):
                for stem in _korean_stems(tokens[index + 1]):
                    joined = self.terms.get(_normalise_term(f"{token}{stem}"))
                    if joined:
                        found[f"{token} {stem}"] = joined
                        skip = True
                        break
                if skip:
                    continue
            english = self.lookup(token)
            if english:
                found[token] = english
        return found

    def expand(self, query: str) -> str:
        """``query`` followed by the English names of its Korean words, when any."""

        if not self.terms or not _HANGUL.search(query):
            return query
        added: list[str] = []
        lower_query = query.casefold()
        for english in self.matches(query).values():
            for name in english[:2]:
                if name.casefold() in lower_query or name in added:
                    continue
                added.append(name)
                if len(added) >= _MAX_EXPANSION_TERMS:
                    break
            if len(added) >= _MAX_EXPANSION_TERMS:
                break
        return f"{query} {' '.join(added)}" if added else query


def default_glossary_path() -> Path:
    """Where the pipeline writes the glossary: ``data/public_knowledge/glossary.json``."""

    return Path(__file__).resolve().parents[2] / "data" / "public_knowledge" / "glossary.json"


def _load_glossary(path: Path | str | None) -> Glossary:
    candidate = Path(path) if path is not None else default_glossary_path()
    if not candidate.is_file():
        return Glossary.empty()
    try:
        return Glossary.load(candidate)
    except (OSError, ValueError):
        return Glossary.empty()


# --- What one piece looks like in the index ---------------------------------------------

#: The metadata fields a piece carries into the index (strings only, as Moss
#: stores them). The piece's own words go in ``payload``; ``text`` is what is
#: searched: the title, its Korean names, the locator and the words.
METADATA_FIELDS: tuple[str, ...] = (
    "title", "locator", "url", "license", "language", "domain",
    "source_id", "source_name", "grade", "version", "fetched_at",
)


def index_text_for(record: Mapping[str, Any], aliases: Sequence[str] = ()) -> str:
    """The searched text of a bundle record: title (Korean names) · locator, then the words."""

    title = str(record.get("title", "")).strip()
    names = [alias for alias in aliases if alias and alias not in title]
    head = f"{title} ({' · '.join(names)})" if names else title
    locator = str(record.get("locator", "")).strip()
    if locator:
        head = f"{head} · {locator}"
    return f"{head}\n{str(record.get('text', '')).strip()}".strip()


# --- The search client seam ---------------------------------------------------------------


@dataclass(frozen=True)
class FoundDocument:
    """One index result as the server reads it (the SDK's own type stays outside)."""

    id: str
    text: str
    metadata: Mapping[str, str]
    score: float
    payload: str | None = None


@runtime_checkable
class SearchClient(Protocol):
    """What the source needs of Moss: load one index, query it, close."""

    async def load(self, index_name: str) -> None: ...

    async def query(
        self, index_name: str, text: str, *, top_k: int, alpha: float,
    ) -> list[FoundDocument]: ...

    async def close(self) -> None: ...


class MossSdkClient:
    """The real client: ``moss.MossClient`` imported only when it is built.

    The package (``moss``, pinned in ``pyproject.toml``) brings a native core;
    a missing or broken install is reported by the package name, not hidden.
    """

    def __init__(self, project_id: str, project_key: str, *, cache_dir: str | None = None) -> None:
        try:
            import moss  # noqa: PLC0415 - optional native dependency, loaded on use
        except ImportError as exc:
            raise KnowledgeUnavailable(
                "moss", "moss package is not installed (pip install -e .)") from exc
        self._moss = moss
        self._client = moss.MossClient(project_id, project_key, cache_path=cache_dir)

    async def load(self, index_name: str) -> None:
        try:
            await self._client.load_index(index_name)
        except self._moss.MossError as exc:
            code = getattr(exc, "code", None) or "INTERNAL"
            raise KnowledgeUnavailable(INDEX_SETTING, f"moss load failed: {code}") from exc

    async def query(
        self, index_name: str, text: str, *, top_k: int, alpha: float,
    ) -> list[FoundDocument]:
        options = self._moss.QueryOptions(top_k=top_k, alpha=alpha)
        result = await self._client.query(index_name, text, options)
        found: list[FoundDocument] = []
        for doc in result.docs:
            metadata = {str(k): str(v) for k, v in (doc.metadata or {}).items()}
            found.append(FoundDocument(
                id=str(doc.id), text=str(doc.text), metadata=metadata,
                score=float(doc.score or 0.0), payload=doc.payload,
            ))
        return found

    async def close(self) -> None:
        await self._client.close()


# --- The public tier -----------------------------------------------------------------------


@dataclass(frozen=True)
class LoadReport:
    """What loading the index took; numbers only, for the log and the report."""

    index_name: str
    elapsed_ms: float
    glossary_terms: int


class PublicKnowledgeSource:
    """The ``public`` tier: one loaded Moss index, searched in process.

    ``search`` returns at most ``limit`` hits, best grade first and within a
    grade in the index's hybrid order; each hit's ``details`` carry the
    source name, grade, domain and the spoken label of decision 6.
    """

    tier = PUBLIC_TIER

    def __init__(
        self,
        settings: PublicKnowledgeSettings,
        client: SearchClient,
        *,
        glossary: Glossary | None = None,
    ) -> None:
        self.settings = settings
        self._client = client
        self.glossary = glossary or Glossary.empty()
        self._loaded = False
        self.load_report: LoadReport | None = None

    @property
    def loaded(self) -> bool:
        return self._loaded

    async def load(self) -> LoadReport:
        started = time.perf_counter()
        await self._client.load(self.settings.index_name)
        self._loaded = True
        self.load_report = LoadReport(
            index_name=self.settings.index_name,
            elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
            glossary_terms=len(self.glossary),
        )
        return self.load_report

    async def close(self) -> None:
        self._loaded = False
        await self._client.close()

    def status(self) -> dict[str, Any]:
        """Content-free status for ``ready`` and the health route."""

        return {
            "enabled": self.settings.enabled,
            "loaded": self._loaded,
            "index": self.settings.index_name,
            "glossary_terms": len(self.glossary),
            "load_ms": self.load_report.elapsed_ms if self.load_report else None,
        }

    async def search(
        self, query: str, *, limit: int, context: KnowledgeContext,
    ) -> list[KnowledgeHit]:
        if not self.settings.enabled:
            raise KnowledgeUnavailable(ENABLED_SETTING, f"{ENABLED_SETTING} is false")
        if not self._loaded:
            raise KnowledgeUnavailable(INDEX_SETTING, "public knowledge index is not loaded")
        text = query.strip()
        if not text or limit <= 0:
            return []
        expanded = self.glossary.expand(text)
        found = await self._client.query(
            self.settings.index_name, expanded,
            top_k=max(limit * CANDIDATES_PER_HIT, limit), alpha=self.settings.alpha,
        )
        hits = [self._hit_from(doc) for doc in found]
        ordered = sorted(hits, key=lambda hit: grade_rank(hit.detail("grade")))  # stable
        return ordered[:limit]

    @staticmethod
    def _hit_from(doc: FoundDocument) -> KnowledgeHit:
        meta = doc.metadata
        body = doc.payload if doc.payload else _body_of(doc.text)
        source_name = meta.get("source_name", "")
        grade = meta.get("grade", "")
        details = (
            ("source_id", meta.get("source_id", "")),
            ("source_name", source_name),
            ("grade", grade),
            ("grade_words", GRADE_WORDS.get(grade, "")),
            ("domain", meta.get("domain", "")),
            ("version", meta.get("version", "")),
            ("fetched_at", meta.get("fetched_at", "")),
            ("label", public_reference_label(source_name)),
            ("doc_id", doc.id),
        )
        return KnowledgeHit(
            tier=PUBLIC_TIER,
            title=meta.get("title", "") or doc.id,
            text=body,
            locator=meta.get("locator", ""),
            source_url=meta.get("url") or None,
            license=meta.get("license") or None,
            language=meta.get("language", "en") or "en",
            score=doc.score,
            details=details,
        )


def _body_of(index_text: str) -> str:
    """The piece's words when only the searched text is at hand: after the head line."""

    head, _sep, rest = index_text.partition("\n")
    return rest.strip() or head.strip()


def wiki_only(hits: Iterable[KnowledgeHit]) -> bool:
    """Whether every public hit is grade C (a wiki): the answer must say so (decision 3)."""

    grades = [hit.detail("grade") for hit in hits if hit.tier == PUBLIC_TIER]
    return bool(grades) and all(grade == "C" for grade in grades)


async def start_public_knowledge(
    settings: PublicKnowledgeSettings,
    *,
    client_factory: Callable[[PublicKnowledgeSettings], SearchClient] | None = None,
    glossary_path: Path | str | None = None,
) -> PublicKnowledgeSource | None:
    """Build and load the tier for the server's start, or None when it is off.

    Raises ``KnowledgeUnavailable`` naming the setting when the credentials
    are missing or the load fails; the caller records that and goes on
    without this tier (rule 7: visible, bounded, non-mutating).
    """

    if not settings.enabled:
        return None
    missing = settings.missing_setting()
    if missing:
        raise KnowledgeUnavailable(missing)
    factory = client_factory or (lambda s: MossSdkClient(
        s.project_id, s.project_key, cache_dir=s.cache_dir))
    client = factory(settings)
    source = PublicKnowledgeSource(settings, client, glossary=_load_glossary(glossary_path))
    try:
        await source.load()
    except KnowledgeUnavailable:
        await _close_quietly(client)
        raise
    except Exception as exc:  # noqa: BLE001 - any load failure is this tier's alone
        await _close_quietly(client)
        raise KnowledgeUnavailable(INDEX_SETTING, f"public knowledge load failed: {type(exc).__name__}") from exc
    return source


async def _close_quietly(client: SearchClient) -> None:
    try:
        await client.close()
    except Exception:  # noqa: BLE001 - closing a client that failed to load
        pass
