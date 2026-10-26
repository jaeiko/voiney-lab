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
from dataclasses import dataclass, field, replace
from functools import cached_property, lru_cache
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

# The knowledge contract (lane rules 10/24, 7) is lane HELP's: this tier's
# hits, context and unavailability are those types, not copies of them.
from voiney_lab.knowledge import KnowledgeContext, KnowledgeHit, KnowledgeUnavailable


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
    "shared_term",
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
#: How many results are read from the index for ``limit`` hits: the ordering
#: and the relevance floor need a few more than are kept.
CANDIDATES_PER_HIT = 3
#: The grade goes first only where the index's scores are close (person's
#: decision 2026-10-26, 1): a hit is ordered by its score plus this much for
#: its grade, so an A piece passes a C piece scored up to 0.03 higher and no
#: more. Lane KAUDIT found 161 of 1,795 top-five hits scored 0.0 placed first
#: by grade alone.
GRADE_BONUS: Mapping[str, float] = {"A": 0.03, "B": 0.015, "C": 0.0}
#: The relevance floor's score part (decision 2026-10-26, 1): a piece the
#: index scores below this is not attached to a problem's help, whatever words
#: it shares. Moss's score comes from vector similarity alone, 0 to 1 (moss
#: 1.15.1 ``QueryResultDocumentInfo.score``). On lane KAUDIT's Moss run
#: (2026-10-26, default model) a tenth of the gold pieces in the top five
#: scored under 0.30 and so did a quarter of the others, the 161 zeros among
#: them.
SCORE_FLOOR = 0.30
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


def ordered_hits(hits: Iterable[KnowledgeHit]) -> list[KnowledgeHit]:
    """Best first: the index's score, the grade only where scores are close.

    Ties keep the index's own (hybrid) order.
    """

    return sorted(hits, key=lambda hit: -(hit.score + GRADE_BONUS.get(hit.detail("grade"), 0.0)))


# --- Settings -----------------------------------------------------------------------


class PublicKnowledgeConfigurationError(ValueError):
    """A setting has a value the server cannot use; the message names it."""


@dataclass(frozen=True)
class PublicKnowledgeSettings:
    enabled: bool
    index_name: str = DEFAULT_INDEX_NAME
    # The credentials never appear in a repr, a log line or an event.
    project_id: str = field(default="", repr=False)
    project_key: str = field(default="", repr=False)
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
#: A question's own Latin-letter words (``DTT``, ``stacking gel``, ``blue``)
#: are terms a piece may share with it; these name nothing to look for.
_LATIN_WORD = re.compile(r"[A-Za-z][A-Za-z0-9\-]+")
_QUESTION_WORDS = frozenset({
    "a", "after", "again", "an", "and", "any", "are", "at", "be", "been", "before", "but", "can",
    "could", "did", "do", "does", "doing", "done", "down", "enough", "for", "from", "get", "got",
    "had", "has", "have", "help", "here", "how", "i", "in", "is", "isn", "it", "its", "just", "like",
    "look", "looks", "maybe", "more", "much", "my", "no", "not", "now", "of", "off", "on", "or",
    "out", "over", "problem", "seem", "seems", "should", "so", "some", "still", "the", "then",
    "there", "this", "that", "to", "too", "up", "very", "was", "we", "were", "what", "when", "where",
    "which", "why", "will", "with", "won", "work", "working", "would", "wrong", "yet", "you",
})


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

    def query_terms(self, query: str) -> tuple[str, ...]:
        """The words a public piece must share with ``query`` to be about it.

        The Korean words the glossary knows (with their particles taken off),
        their English names and the other Korean names of those, and the
        query's own Latin-letter words other than question words. A question
        with none of these ("왜 안 되지?") names nothing a piece could be
        about.
        """

        terms: list[str] = []
        for words, english in self.matches(query).items():
            for word in words.split():
                stem = _korean_stems(word)[-1]  # the particle taken off, when one was
                if len(stem) >= 2:
                    terms.append(stem)
            for name in english:
                terms.append(name)
                terms += list(self.aliases.get(name, ()))
        terms += [word for word in _LATIN_WORD.findall(query) if word.casefold() not in _QUESTION_WORDS]
        seen: set[str] = set()
        unique: list[str] = []
        for term in terms:
            key = term.casefold().strip()
            if key and key not in seen:
                seen.add(key)
                unique.append(term.strip())
        return tuple(unique)

    @cached_property
    def _english_index(self) -> Mapping[str, tuple[str, ...]]:
        """English names (NFKC, casefolded) by their first word, longest first."""

        index: dict[str, set[str]] = {}
        for names in self.terms.values():
            for name in names:
                key = " ".join(unicodedata.normalize("NFKC", name).casefold().split())
                if len(key) >= 3:
                    index.setdefault(key.split()[0], set()).add(key)
        return {word: tuple(sorted(found, key=len, reverse=True)) for word, found in index.items()}

    @cached_property
    def _korean_by_english(self) -> Mapping[str, tuple[str, ...]]:
        """Korean names of an English name (casefolded): the terms read backwards and the aliases."""

        found: dict[str, list[str]] = {}
        for korean, names in self.terms.items():
            for name in names:
                found.setdefault(name.casefold(), []).append(korean)
        for name, koreans in self.aliases.items():
            found.setdefault(name.casefold(), []).extend(koreans)
        return {name: tuple(dict.fromkeys(items)) for name, items in found.items()}

    def english_names_in(self, folded: str) -> list[tuple[str, int]]:
        """The glossary's English names a casefolded text holds as words, with where."""

        found: list[tuple[str, int]] = []
        for match in re.finditer(r"[a-z0-9][a-z0-9\-]*", folded):
            for name in self._english_index.get(match.group(0), ()):
                end = match.start() + len(name)
                if folded.startswith(name, match.start()) and not folded[end:end + 1].isalnum():
                    found.append((name, match.start()))
                    break
        return found

    def korean_names(self, english: str) -> tuple[str, ...]:
        """The Korean names of an English name, or ()."""

        return self._korean_by_english.get(english.casefold().strip(), ())

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


# --- A problem's query and the relevance floor (person's decision 2026-10-26, 1) ---------
#
# Lane KAUDIT read the public pieces attached to a problem's help: most shared
# one general word with the problem ("cell", "Gel", "Ab") and none of what went
# wrong. So a problem's public query is the problem's own words and the names
# it is about -- what the person named and the current step's key words --
# never the whole sentence, and a piece is attached only when it shares both a
# word of what went wrong and a name, other words, and the index scored it at
# least ``SCORE_FLOOR``. Under that floor no public layer is attached at all:
# nothing is better than a piece off the point. The vocabulary is general lab
# language (lane HELP's concepts, the state machine's lab lexicon, the
# glossary), never one protocol's words.

#: Words that name nothing a piece could be about: general laboratory nouns
#: and verbs a piece about anything carries. A piece sharing only these is not
#: about the problem.
GENERAL_WORDS = frozenset({
    "add", "added", "addition", "amount", "buffer", "buffers", "cell", "cells", "dish", "gel", "gels",
    "incubate", "incubation", "lab", "laboratory", "liquid", "medium", "media", "min", "minute", "minutes",
    "mix", "mixture", "piece", "pieces", "plate", "plates", "protein", "proteins", "reagent", "reagents",
    "room", "sample", "samples", "solution", "solutions", "step", "temperature", "time", "tube", "tubes",
    "volume", "wash", "washes", "washing", "water", "well", "wells",
    "젤", "겔", "세포", "단백질", "샘플", "시료", "용액", "버퍼", "튜브", "물", "온도", "시간", "단계",
})
#: Lane HELP's concepts that say what a sentence is about, a colour, a
#: quantity or a time rather than what went wrong: alone they make no piece
#: about the problem ("temperature" is in every compound record).
VAGUE_CONCEPTS = frozenset({
    "still", "color", "liquid", "gel", "band", "cell", "protein", "sample", "medium", "time", "temperature",
    "volume", "change", "speed", "remove", "measure", "mix", "wall", "ph",
    "yellow", "brown", "red", "purple", "white", "transparent",
})
#: The words a concept is searched by (lane HELP's table holds stems, not words).
CONCEPT_QUERY_WORDS: Mapping[str, str] = {
    "stain": "staining destaining", "dehydrate": "dehydration drying", "rehydrate": "swelling rehydration",
    "precipitate": "precipitate precipitation", "clump": "clumping aggregation", "residue": "residue",
    "cloudy": "cloudy turbid", "foam": "foam bubbles", "dissolve": "dissolve solubility",
    "contamination": "contamination", "detach": "detach detachment", "attach": "attachment adhesion",
    "efficiency": "low yield efficiency", "amplification": "amplification", "light": "light exposure",
    "signal": "weak signal background", "leak": "leak spill", "loose": "seal leak", "filter": "filtration",
    "distribution": "uneven distribution", "digest": "digestion", "migrate": "migration",
}
#: At most this many names go into a problem's query (the person's first, then the step's).
_MAX_QUERY_NAMES = 6
#: A word of letters that names a reagent, a cell line or a method by its form:
#: two capitals or more ("DTT", "TrypLE", "SDS-PAGE"), or letters and digits
#: together ("HEK293T", "LiPF6").
_NAME_TOKEN = re.compile(r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*")
_UNIT_TOKENS = frozenset({"ml", "µl", "ul", "nl", "mm", "µm", "um", "nm", "mg", "µg", "ug", "ng", "rpm", "rcf",
                          "min", "sec", "hr", "h", "s", "kda", "bp", "kb", "od", "ph"})


def _name_like(token: str) -> bool:
    if token.casefold() in _UNIT_TOKENS or len(token) < 2:
        return False
    capitals = sum(1 for letter in token if letter.isupper())
    has_digit = any(letter.isdigit() for letter in token)
    return capitals >= 2 or (has_digit and capitals >= 1 and len(token) >= 3)


def _general(term: str) -> bool:
    key = unicodedata.normalize("NFKC", term).casefold().strip()
    return key in GENERAL_WORDS or all(word in GENERAL_WORDS for word in key.split())


@lru_cache(maxsize=1)
def _lexicon() -> tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...]:
    """The state machine's lab lexicon: (name, English surfaces, Korean forms)."""

    from voiney_lab.curated_protocol import _LAB_LEXICON  # noqa: PLC0415 - the language's own table

    return tuple((key.replace("_", " "), english, korean) for key, english, korean, _stem in _LAB_LEXICON)


def _english_pattern(term: str) -> re.Pattern[str]:
    """A name as a word, with its plural or ending ("centrifug" → "centrifuged")."""

    words = term.casefold().split()
    tail = words[-1]
    stem = tail[:-1] if len(tail) > 5 and tail.endswith("e") else tail
    body = r"\s+".join([*(re.escape(word) for word in words[:-1]), re.escape(stem) + r"[a-z]{0,5}"])
    return re.compile(rf"(?<![a-z0-9]){body}(?![a-z0-9])")


def _has_term(haystack: str, term: str) -> bool:
    """Whether ``haystack`` (NFKC, casefolded) holds ``term``: a Korean name of
    two letters or more anywhere (particles attach to it), a Latin name as a word."""

    needle = unicodedata.normalize("NFKC", term).casefold().strip()
    if not needle:
        return False
    if _HANGUL.search(needle):
        return len(needle) >= 2 and needle in haystack
    return bool(_english_pattern(needle).search(haystack))


@dataclass(frozen=True)
class ProblemQuery:
    """What the public tier is asked about a problem at the bench.

    ``problem`` are lane HELP's concepts of what went wrong (``foam``,
    ``detach``); ``subjects`` the names it is about, the person's first and
    then the current step's key words; ``text`` what the index is queried
    with -- those words, never the whole sentence.
    """

    text: str
    problem: tuple[str, ...]
    subjects: tuple[str, ...]


def _subjects_said(words: str, glossary: "Glossary") -> list[str]:
    """The names in what the person said: glossary words (and their English),
    the lab lexicon's Korean forms, and their own words in Latin letters."""

    found: list[str] = []
    for korean, english in glossary.matches(words).items():
        found += [korean if " " in korean else _korean_stems(korean)[-1], *english[:2]]
    folded = unicodedata.normalize("NFKC", words).casefold()
    for name, _english, korean in _lexicon():
        if any(form in folded for form in korean if len(form) >= 2):
            found.append(name)
    found += [word for word in _LATIN_WORD.findall(words)
              if word.casefold() not in _QUESTION_WORDS and (len(word) >= 3 or _name_like(word))]
    return found


def _subjects_of_step(step_text: str, glossary: "Glossary") -> list[str]:
    """The current step's key words: what it names -- names by their form, the
    lab lexicon's English, the glossary's English names, a Korean step's
    glossary words -- in the order the step says them."""

    folded = unicodedata.normalize("NFKC", step_text).casefold()
    placed: list[tuple[int, str]] = []
    for match in _NAME_TOKEN.finditer(step_text):
        if _name_like(match.group(0)):
            placed.append((match.start(), match.group(0)))
    for name, english, _korean in _lexicon():
        at = min((found.start() for found in (_english_pattern(surface).search(folded) for surface in english)
                  if found is not None), default=-1)
        if at >= 0:
            placed.append((at, name))
    for name, at in glossary.english_names_in(folded):
        placed.append((at, name))
    for korean, english in glossary.matches(step_text).items():
        placed.append((max(0, folded.find(korean.split()[0].casefold())), english[0]))
    return [name for _at, name in sorted(placed, key=lambda item: item[0])]


def problem_query(words: str, step_text: str, glossary: "Glossary") -> ProblemQuery | None:
    """The public query of a problem said at a step, or None when there is
    nothing to look for: no word of what went wrong, or nothing it is about."""

    from voiney_lab.knowledge import concepts_in  # noqa: PLC0415 - lane HELP's concept table

    problem = tuple(concept for concept in concepts_in(words)
                    if concept not in VAGUE_CONCEPTS and concept in CONCEPT_QUERY_WORDS)
    if not problem:
        return None
    # A name the person said that is itself a word of what went wrong ("오염"
    # → contamination) is the problem, not what it is about.
    said = [name for name in _subjects_said(words, glossary) if not set(concepts_in(name)) & set(problem)]
    subjects: list[str] = []
    seen: set[str] = set()
    for name in (*said, *_subjects_of_step(step_text, glossary)):
        key = unicodedata.normalize("NFKC", name).casefold().strip()
        if (not key or key in seen or _general(name) or key in _UNIT_TOKENS
                or (not _HANGUL.search(key) and len(key) < 3 and not _name_like(name))):
            continue
        seen.add(key)
        subjects.append(name.strip())
        for korean in glossary.korean_names(name):
            if korean.casefold() not in seen and not _general(korean):
                seen.add(korean.casefold())
                subjects.append(korean)
    if not subjects:
        return None
    named = subjects[:_MAX_QUERY_NAMES]
    text = " ".join([*named, *(CONCEPT_QUERY_WORDS[concept] for concept in problem)])
    return ProblemQuery(text=text, problem=problem, subjects=tuple(subjects))


def problem_relevance(text: str, score: float, query: ProblemQuery) -> tuple[str, str, bool]:
    """(the problem's concept the piece has, the name it shares, attached?).

    The concept must be in words other than the names: "trypsin" is a word of
    the ``detach`` concept and a reagent, and a piece that only names it says
    nothing about cells not detaching.
    """

    from voiney_lab.knowledge import concepts_in  # noqa: PLC0415

    haystack = unicodedata.normalize("NFKC", text).casefold()
    subject = next((name for name in query.subjects if _has_term(haystack, name)), "")
    masked = haystack
    for name in query.subjects:
        needle = unicodedata.normalize("NFKC", name).casefold().strip()
        if _HANGUL.search(needle):
            masked = masked.replace(needle, " ")
        elif needle:
            masked = _english_pattern(needle).sub(" ", masked)
    concepts = set(concepts_in(masked))
    problem = next((concept for concept in query.problem if concept in concepts), "")
    return problem, subject, bool(problem and subject and score >= SCORE_FLOOR)


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
        terms = self.glossary.query_terms(text)
        return ordered_hits(self._hit_from(doc, terms) for doc in found)[:limit]

    def for_problem(self, query: ProblemQuery) -> "ProblemSearch":
        """This tier asked about one problem (``query``) in place of the sentence."""

        return ProblemSearch(self, query)

    async def search_problem(self, query: ProblemQuery, *, limit: int) -> list[KnowledgeHit]:
        """The pieces for a problem, best first, each marked by the relevance floor.

        ``details`` gain ``problem_shares`` (the concept of what went wrong the
        piece has), ``subject_shares`` (the name it shares) and ``relevant``
        ("yes" when both and the score clear the floor; "" else). Only
        relevant pieces are attached to the help
        (``external_references.public_reference_part``).
        """

        if not self.settings.enabled:
            raise KnowledgeUnavailable(ENABLED_SETTING, f"{ENABLED_SETTING} is false")
        if not self._loaded:
            raise KnowledgeUnavailable(INDEX_SETTING, "public knowledge index is not loaded")
        if limit <= 0 or not query.text.strip():
            return []
        found = await self._client.query(
            self.settings.index_name, query.text,
            top_k=max(limit * CANDIDATES_PER_HIT, limit), alpha=self.settings.alpha,
        )
        hits: list[KnowledgeHit] = []
        for doc in found:
            hit = self._hit_from(doc)
            problem, subject, relevant = problem_relevance(
                f"{hit.title}\n{hit.locator}\n{hit.text}", hit.score, query)
            hits.append(replace(hit, details=(
                *hit.details, ("problem_shares", problem), ("subject_shares", subject),
                ("relevant", "yes" if relevant else ""))))
        return ordered_hits(hits)[:limit]

    @staticmethod
    def _hit_from(doc: FoundDocument, terms: Sequence[str] = ()) -> KnowledgeHit:
        meta = doc.metadata
        body = doc.payload if doc.payload else _body_of(doc.text)
        source_name = meta.get("source_name", "")
        grade = meta.get("grade", "")
        title = meta.get("title", "") or doc.id
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
            # The question's word this piece also has ("" when none): Moss
            # always returns its nearest pieces, and one that shares no word
            # with the question is not shown as being about it.
            ("shares", shared_term(f"{title}\n{meta.get('locator', '')}\n{body}", terms)),
        )
        return KnowledgeHit(
            tier=PUBLIC_TIER,
            title=title,
            text=body,
            locator=meta.get("locator", ""),
            source_url=meta.get("url") or None,
            license=meta.get("license") or None,
            language=meta.get("language", "en") or "en",
            score=doc.score,
            details=details,
        )


class ProblemSearch:
    """The public tier as a knowledge source for one problem (``search_tiers``).

    The help searches every tier with the person's sentence; this source asks
    the index with the problem's query instead (decision 2026-10-26, 1).
    """

    tier = PUBLIC_TIER

    def __init__(self, source: PublicKnowledgeSource, query: ProblemQuery) -> None:
        self.source = source
        self.query = query

    async def search(self, query: str, *, limit: int, context: KnowledgeContext) -> list[KnowledgeHit]:
        return await self.source.search_problem(self.query, limit=limit)


def _body_of(index_text: str) -> str:
    """The piece's words when only the searched text is at hand: after the head line."""

    head, _sep, rest = index_text.partition("\n")
    return rest.strip() or head.strip()


def shared_term(text: str, terms: Sequence[str]) -> str:
    """The first of ``terms`` that ``text`` contains, or "".

    A Latin-letter term must stand as a word ("Ab" is not in "about"); a
    Korean term of two or more letters may sit inside a longer word, as
    Korean particles and endings attach to it.
    """

    haystack = unicodedata.normalize("NFKC", text).casefold()
    for term in terms:
        needle = unicodedata.normalize("NFKC", term).casefold().strip()
        if not needle:
            continue
        if _HANGUL.search(needle):
            if len(needle) >= 2 and needle in haystack:
                return term
        elif re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", haystack):
            return term
    return ""


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
