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
(``data/public_knowledge/glossary.json``, Wikidata labels, CC0, and the
hand-written list ``glossary_words.txt``): the Korean words of the question,
their particles taken off, are joined by their English names before the query
is embedded. A problem's help asks with the problem's own query
(``problem_query``) and attaches only pieces over the relevance floor
(``problem_relevance``); with a ``custom`` index the query's vector comes from
the pinned multilingual model (``EMBEDDING_MODEL``, lane KNOW2). That is
deterministic; no generative model sees the question here.

Every hit carries where it comes from -- source name, licence, address, the
source grade of additional decision 3 (A: public institution, standard or
peer-reviewed article; B: open textbook; C: wiki). Of the pieces only the
fixed sentence "공개 자료에 관련 내용이 있어요" is said; the pieces are on the
screen with their source (person's decision 2026-10-26, 1).
"""

from __future__ import annotations

import asyncio
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
    "GRADE_BONUS",
    "GRADE_WORDS",
    "Glossary",
    "GlossaryWords",
    "INDEX_SETTING",
    "KnowledgeContext",
    "KnowledgeHit",
    "KnowledgeUnavailable",
    "MOSS_PROJECT_ID_SETTING",
    "MOSS_PROJECT_KEY_SETTING",
    "MossSdkClient",
    "PUBLIC_LABEL",
    "PUBLIC_TIER",
    "ProblemQuery",
    "ProblemSearch",
    "PublicKnowledgeSettings",
    "PublicKnowledgeSource",
    "SCORE_FLOOR",
    "SearchClient",
    "Symptom",
    "default_glossary_path",
    "default_words_path",
    "grade_rank",
    "index_text_for",
    "load_glossary",
    "ordered_hits",
    "problem_query",
    "problem_relevance",
    "public_reference_label",
    "read_glossary_words",
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
class Symptom:
    """What went wrong, as the hand-written glossary's [현상] entry says it:
    the Korean a researcher says ("굳지 않") and the English material uses
    ("polymerization"). ``name`` is its first English word."""

    name: str
    korean: tuple[str, ...]
    english: tuple[str, ...]

    def said_in(self, text: str) -> bool:
        flat = " ".join(unicodedata.normalize("NFKC", text).casefold().split())
        return any(" ".join(form.casefold().split()) in flat for form in self.korean)

    def found_in(self, folded: str) -> bool:
        """Whether a piece's casefolded words say it: an English word (its
        ending may differ) or a Korean form of two letters or more."""

        if any(_english_pattern(word).search(folded) for word in self.english):
            return True
        return any(len(form.replace(" ", "")) >= 2 and form.casefold() in folded for form in self.korean)


@dataclass(frozen=True)
class Glossary:
    """Korean names → English names (Wikidata labels and aliases, CC0, and the
    hand-written list ``glossary_words.txt``).

    ``terms`` maps a normalised Korean term (NFKC, casefold, no spaces) to the
    English names of the same item. ``aliases`` maps an English title to its
    Korean names, for the pipeline to prefix English pieces with. ``general``
    holds words that never count as a meaning word (NFKC, casefolded) besides
    ``GENERAL_WORDS``; ``symptoms`` what went wrong (the list's [현상]);
    ``things`` the English names of reagents, materials, devices and hazards
    (the list's [물질], [소재], [장비], [안전]), a step's key words.
    """

    terms: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    aliases: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    general: frozenset[str] = frozenset()
    symptoms: tuple[Symptom, ...] = ()
    things: frozenset[str] = frozenset()

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

    def with_words(self, path: Path | str) -> "Glossary":
        """This glossary joined by the hand-written list: its English names
        first for a Korean word both know, its aliases, general words,
        symptoms and things added."""

        words = read_glossary_words(path)
        terms = dict(self.terms)
        for key, names in words.terms.items():
            terms[key] = tuple(dict.fromkeys((*names, *terms.get(key, ()))))
        aliases = dict(self.aliases)
        for name, koreans in words.aliases.items():
            aliases[name] = tuple(dict.fromkeys((*aliases.get(name, ()), *koreans)))
        return Glossary(
            terms=terms, aliases=aliases, general=self.general | words.general,
            symptoms=(*self.symptoms, *words.symptoms), things=self.things | words.things,
        )

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
    def _things_index(self) -> Mapping[str, tuple[str, ...]]:
        """The things' English names (NFKC, casefolded) by their first word, longest first."""

        index: dict[str, set[str]] = {}
        for name in self.things:
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

    def things_in(self, folded: str) -> list[tuple[str, int]]:
        """The things' English names a casefolded text holds as words, with where."""

        found: list[tuple[str, int]] = []
        for match in re.finditer(r"[a-z0-9][a-z0-9\-]*", folded):
            for name in self._things_index.get(match.group(0), ()):
                end = match.start() + len(name)
                if folded.startswith(name, match.start()) and not folded[end:end + 1].isalnum():
                    found.append((name, match.start()))
                    break
        return found

    def korean_names(self, english: str) -> tuple[str, ...]:
        """The Korean names of an English name, or ()."""

        return self._korean_by_english.get(english.casefold().strip(), ())

    def general_word(self, term: str) -> bool:
        """Whether ``term`` names nothing a piece could be about ("세포", "wash buffer")."""

        key = _plain(term)
        if not key:
            return True
        words = key.split()
        return (key in GENERAL_WORDS or key in self.general or _normalise_term(key) in self.general
                or all(word in GENERAL_WORDS or word in self.general for word in words))

    def symptoms_in(self, text: str) -> list[Symptom]:
        """The [현상] entries said in ``text``."""

        return [symptom for symptom in self.symptoms if symptom.said_in(text)]

    def expand(self, query: str) -> str:
        """``query`` followed by the English names of its Korean words, when any."""

        if not (self.terms or self.symptoms) or not _HANGUL.search(query):
            return query
        added: list[str] = []
        lower_query = query.casefold()
        names = [english[:2] for english in self.matches(query).values()]
        # What went wrong, in the list's words ("굳지 않" → polymerization).
        names += [symptom.english[:2] for symptom in self.symptoms_in(query)]
        for english in names:
            for name in english:
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
# never the whole sentence, and a piece is attached only over the relevance
# floor: it shares a name, says what went wrong in other words, speaks of a
# cause, an effect or a hazard rather than defining a thing, and the index
# scored it at least ``SCORE_FLOOR``. Under that floor no public layer is
# attached at all: nothing is better than a piece off the point. The
# vocabulary is general lab language (lane HELP's concepts, the state
# machine's lab lexicon, the glossary), never one protocol's words.

#: Words that name nothing a piece could be about: general laboratory nouns
#: and verbs a piece about anything carries. A piece sharing only these is not
#: about the problem. The hand-written glossary's [일반] words join them.
GENERAL_WORDS = frozenset({
    "add", "added", "addition", "amount", "buffer", "buffers", "cell", "cells", "dish", "gel", "gels",
    "incubate", "incubation", "lab", "laboratory", "liquid", "medium", "media", "min", "minute", "minutes",
    "mix", "mixture", "piece", "pieces", "plate", "plates", "protein", "proteins", "reagent", "reagents",
    "room", "sample", "samples", "solution", "solutions", "step", "temperature", "time", "tube", "tubes",
    "volume", "wash", "washes", "washing", "water", "well", "wells",
    # Carrier buffers nearly every washing step names.
    "pbs", "pbs-t", "pbst", "dpbs", "tbs", "tbs-t", "tbst", "tween", "tween-20", "tween 20", "saline",
    "젤", "겔", "세포", "단백질", "샘플", "시료", "용액", "버퍼", "튜브", "물", "온도", "시간", "단계",
})
#: What a step handles things with -- containers, hand tools and parts. Said
#: by the person they are what the problem is about ("피펫에서 새", "뚜껑이
#: 헐거워"); in the step's own words they are incidental ("using a pipette,
#: add …", "cap the tube", the "plug" of "gel plug").
STEP_TOOL_WORDS = frozenset({
    "tube", "tubes", "microcentrifuge tube", "centrifuge tube", "conical tube", "flask", "erlenmeyer flask",
    "beaker", "vial", "petri dish", "dish", "bottle", "pipette", "pipet", "micropipette", "tip", "tips",
    "syringe", "glove", "gloves", "balance", "thermometer", "plate", "microplate", "rack", "ice",
    "cap", "caps", "lid", "plug", "power", "lamp", "holder", "nozzle", "cabinet", "tank", "pump",
})
#: The lab lexicon's entries that name an action or a technique, not a thing a
#: step handles: they are not a step's key words.
_LEXICON_ACTIONS = frozenset({
    "destaining", "dehydration", "incubation", "contamination", "electrophoresis", "sds page", "mass spectrometry",
    "rpm", "calibration", "dilution", "blank", "hygroscopic", "waste", "vortex",
})
#: Lane HELP's concepts that say what a sentence is about, a colour, a
#: quantity or a time rather than what went wrong: alone they make no piece
#: about the problem ("temperature" is in every compound record).
VAGUE_CONCEPTS = frozenset({
    "still", "color", "liquid", "gel", "band", "cell", "protein", "sample", "medium", "time", "temperature",
    "volume", "change", "speed", "remove", "measure", "mix", "wall", "ph",
    "yellow", "brown", "red", "purple", "white", "transparent",
    # Topic words: a process named ("filtration", "digestion") or a word with
    # other senses ("light chain"); a piece with them is about the topic.
    "filter", "digest", "light",
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
#: The language of a cause, an effect, a failure or a hazard. A piece without
#: any of it only defines or describes a thing (lane KAUDIT: "Filter paper has
#: various properties", "T cells ..."), which does not help with a problem.
_EFFECT_WORDS = re.compile(
    r"(?<![a-z])(?:caus|due to|because|result(?:s|ed|ing)? in|lead(?:s|ing)? to|led to|prevent|avoid|reduc|"
    r"increas|decreas|improv|promot|enhanc|inhibit|impair|interfer|affect|fail|poor|insufficient|incomplete|"
    r"excess|too (?:much|little|long|short|high|low|fast|slow)|problem|troubleshoot|unless|"
    r"hazard|toxic|irritat|corros|flammab|explos|danger|warning|caution|harmful|fatal)"
    r"|(?<![a-z])if\b[^.]{0,80}\b(?:not|no|fails?|too)\b"
    r"|원인|때문|방지|피해야|피하|줄이|줄어|늘리|늘어|높이|낮추|촉진|억제|방해|실패|부족|과다|너무|문제|해결|주의"
    r"|위험|독성|유해|자극|화상|않으면|않을 때|않도록"
)
#: At most this many names go into a problem's query (the person's first, then the step's).
_MAX_QUERY_NAMES = 6
#: A word of letters that names a reagent, a cell line or a method by its form:
#: two capitals or more ("DTT", "TrypLE", "SDS-PAGE"), or letters and digits
#: together ("HEK293T", "LiPF6").
_NAME_TOKEN = re.compile(r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*")
#: Words in Latin letters said together ("dye front", "master mix") name one thing.
_LATIN_RUN = re.compile(r"[A-Za-z][A-Za-z0-9\-]*(?:[ \t]+[A-Za-z][A-Za-z0-9\-]*)*")
_UNIT_TOKENS = frozenset({"ml", "µl", "ul", "nl", "mm", "µm", "um", "nm", "mg", "µg", "ug", "ng", "rpm", "rcf",
                          "min", "sec", "hr", "h", "s", "kda", "bp", "kb", "od", "ph"})


def _name_like(token: str) -> bool:
    if token.casefold() in _UNIT_TOKENS or len(token) < 2:
        return False
    capitals = sum(1 for letter in token if letter.isupper())
    has_digit = any(letter.isdigit() for letter in token)
    return capitals >= 2 or (has_digit and capitals >= 1 and len(token) >= 3)


def _plain(term: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", term).casefold().split())


@lru_cache(maxsize=1)
def _ordinary_words() -> frozenset[str]:
    from voiney_lab.knowledge import _ENGLISH_STOP  # noqa: PLC0415 - lane HELP's stop words

    return frozenset(_ENGLISH_STOP) | _QUESTION_WORDS


@lru_cache(maxsize=1)
def _reagent_names() -> tuple[str, ...]:
    """The reagents the bundle holds compound records for (``pubchem_compounds.txt``):
    a step that names one names a thing it handles."""

    path = Path(__file__).resolve().parents[2] / "data" / "public_knowledge" / "pubchem_compounds.txt"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ()
    names = [line.split("\t")[0].strip().casefold() for line in lines if line.strip() and not line.startswith("#")]
    return tuple(name for name in dict.fromkeys(names) if len(name) >= 3)


@lru_cache(maxsize=1)
def _lexicon() -> tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...]:
    """The state machine's lab lexicon: (name, English surfaces, Korean forms)."""

    from voiney_lab.curated_protocol import _LAB_LEXICON  # noqa: PLC0415 - the language's own table

    return tuple((key.replace("_", " "), english, korean) for key, english, korean, _stem in _LAB_LEXICON)


@lru_cache(maxsize=4096)
def _english_pattern(term: str, *, any_ending: bool = False) -> re.Pattern[str]:
    """A name as a word, with its plural or ending ("centrifug" → "centrifuged");
    with ``any_ending`` every word it begins ("trypsin" → "trypsinization")."""

    words = term.casefold().split()
    tail = words[-1]
    stem = tail[:-1] if len(tail) > 5 and tail.endswith("e") else tail
    ending = r"[a-z]*" if any_ending else r"[a-z]{0,5}"
    body = r"\s+".join([*(re.escape(word) for word in words[:-1]), re.escape(stem) + ending])
    return re.compile(rf"(?<![a-z0-9]){body}(?![a-z0-9])")


def _has_term(haystack: str, term: str) -> bool:
    """Whether ``haystack`` (NFKC, casefolded) holds ``term``: a Korean name of
    two letters or more anywhere (particles attach to it), a Latin name as a
    word, an acronym or a formula ("PEB", "LiPF6") only as itself."""

    needle = _plain(term)
    if not needle:
        return False
    if _HANGUL.search(needle):
        return len(needle) >= 2 and needle in haystack
    if _name_like(term.strip()):
        return re.search(rf"(?<![a-z0-9]){re.escape(needle)}s?(?![a-z0-9])", haystack) is not None
    return bool(_english_pattern(needle).search(haystack))


@dataclass(frozen=True)
class ProblemQuery:
    """What the public tier is asked about a problem at the bench.

    ``problem`` names what went wrong -- lane HELP's concepts (``foam``,
    ``detach``) and the hand-written glossary's [현상] entries
    (``polymerization``); ``subjects`` the names it is about, the person's
    first and then the current step's key words; ``text`` what the index is
    queried with -- those words, never the whole sentence; ``symptoms`` the
    [현상] entries found, with their words; ``said`` the subjects the person
    named (a piece must share one of these when there are any).
    """

    text: str
    problem: tuple[str, ...]
    subjects: tuple[str, ...]
    symptoms: tuple["Symptom", ...] = ()
    said: tuple[str, ...] = ()


def _said_names(words: str, glossary: "Glossary", problem: set[str], korean_part: str) -> list[str]:
    """The names in what the person said: glossary words (and their English),
    the lab lexicon's Korean forms, and their own words in Latin letters.

    A word that is general ("단백질") or is itself the problem -- the only word
    that says it, as "침전" in "침전이 생겼어" -- brings none of its names;
    "뚜껑" in "뚜껑이 헐거워" is what is loose, not the looseness.
    """

    from voiney_lab.knowledge import concepts_in  # noqa: PLC0415

    def the_problem(form: str) -> bool:
        carried = set(concepts_in(form)) & problem
        return bool(carried) and not carried <= set(concepts_in(korean_part.replace(form, " ")))

    found: list[str] = []
    for korean, english in glossary.matches(words).items():
        stem = korean if " " in korean else _korean_stems(korean)[-1]
        if glossary.general_word(stem) or the_problem(stem) or glossary.symptoms_in(stem):
            continue
        found += [stem, *(name for name in english[:2] if not glossary.general_word(name))]
    folded = unicodedata.normalize("NFKC", words).casefold()
    for name, _english, korean in _lexicon():
        forms = [form for form in korean if len(form) >= 2 and form in folded]
        if forms and not any(the_problem(form) for form in forms):
            found.append(name)
    for run in _LATIN_RUN.findall(words):
        kept = [word for word in run.split() if word.casefold() not in _QUESTION_WORDS]
        phrase = " ".join(kept)
        if kept and (len(phrase) >= 3 or _name_like(phrase)):
            found.append(phrase)
    return found


def _step_names(step_text: str, glossary: "Glossary") -> list[str]:
    """The current step's key words: the reagents, materials and devices it
    names -- names by their form ("DTT", "LiPF6"), the bundle's reagents, the
    lab lexicon's things (English or Korean) and the hand-written glossary's
    things -- in the order the step says them. Its actions and techniques
    ("dilution", "fluorescence"), containers and hand tools, and a shouted run
    of ordinary words ("IN THE DARK") are not."""

    folded = unicodedata.normalize("NFKC", step_text).casefold()
    placed: list[tuple[int, str]] = []
    tokens = list(_NAME_TOKEN.finditer(step_text))
    shouted: set[int] = set()
    run: list[int] = []
    for index, match in enumerate([*tokens, None]):
        upper = match is not None and len(match.group(0)) >= 2 and match.group(0).isupper()
        joined = upper and run and not step_text[tokens[run[-1]].end():match.start()].strip()
        if upper and (not run or joined):
            run.append(index)
            continue
        if any(tokens[item].group(0).casefold() in _ordinary_words() for item in run) and len(run) > 1:
            shouted.update(run)
        run = [index] if upper else []
    for index, match in enumerate(tokens):
        if index not in shouted and _name_like(match.group(0)):
            placed.append((match.start(), match.group(0)))
    for name in _reagent_names():
        found = _english_pattern(name).search(folded)
        if found is not None:
            placed.append((found.start(), name))
    for name, english, korean in _lexicon():
        if name in _LEXICON_ACTIONS or name in STEP_TOOL_WORDS:
            continue
        at = min((found.start() for found in (_english_pattern(surface).search(folded) for surface in english)
                  if found is not None), default=-1)
        at = at if at >= 0 else min((folded.find(form) for form in korean if len(form) >= 2 and form in folded),
                                    default=-1)
        if at >= 0:
            placed.append((at, name))
    for name, at in glossary.things_in(folded):
        if name not in STEP_TOOL_WORDS:
            placed.append((at, name))
    return [name for _at, name in sorted(placed, key=lambda item: item[0])]


def problem_query(words: str, step_text: str, glossary: "Glossary") -> ProblemQuery | None:
    """The public query of a problem said at a step, or None when there is
    nothing to look for: no word of what went wrong, or nothing it is about.

    What went wrong is read from the Korean of what was said -- its words in
    Latin letters are names ("dye front", "TrypLE"), not problems.
    """

    from voiney_lab.knowledge import concepts_in  # noqa: PLC0415 - lane HELP's concept table

    korean_part = _LATIN_RUN.sub(" ", words) if _HANGUL.search(words) else words
    concepts = [concept for concept in concepts_in(korean_part)
                if concept not in VAGUE_CONCEPTS and concept in CONCEPT_QUERY_WORDS]
    symptoms = glossary.symptoms_in(korean_part)
    problem = tuple(dict.fromkeys([*concepts, *(symptom.name for symptom in symptoms)]))
    if not problem:
        return None
    subjects: list[str] = []
    named_by_person: list[str] = []
    seen: set[str] = set()
    said = _said_names(words, glossary, set(problem), korean_part)
    for index, name in enumerate([*said, *_step_names(step_text, glossary)]):
        key = _plain(name)
        if (not key or key in seen or glossary.general_word(name) or key in _UNIT_TOKENS
                or (index >= len(said) and key in STEP_TOOL_WORDS)
                or (not _HANGUL.search(key) and len(key) < 3 and not _name_like(name))):
            continue
        found = [name.strip()]
        seen.add(key)
        for korean in glossary.korean_names(name):
            if _plain(korean) not in seen and not glossary.general_word(korean):
                seen.add(_plain(korean))
                found.append(korean)
        subjects += found
        if index < len(said):
            named_by_person += found
    if not subjects:
        return None
    named: list[str] = []
    for name in subjects:
        # One Korean name of each thing in the query; all of them still count when a piece is read.
        if len(named) < _MAX_QUERY_NAMES and not (_HANGUL.search(name) and any(_HANGUL.search(n) for n in named[-1:])):
            named.append(name)
    words_of = [CONCEPT_QUERY_WORDS[name] for name in concepts]
    words_of += [" ".join(symptom.english[:2]) for symptom in symptoms]
    return ProblemQuery(text=" ".join([*named, *words_of]), problem=problem, subjects=tuple(subjects),
                        symptoms=tuple(symptoms), said=tuple(named_by_person))


@lru_cache(maxsize=1)
def _concept_patterns() -> tuple[tuple[str, re.Pattern[str] | None, tuple[str, ...]], ...]:
    """Lane HELP's concepts as a piece is read for them: a stem of three letters
    or fewer only as a whole word ("cap", not "capacity"), and a Korean stem of
    two letters or more (one letter, "녹", is inside "녹색")."""

    from voiney_lab.knowledge import _CONCEPTS  # noqa: PLC0415

    patterns = []
    for concept, english, korean in _CONCEPTS:
        parts = [re.escape(stem) + (r"s?(?![a-z0-9])" if len(stem) <= 3 else "")
                 for stem in english]
        pattern = re.compile(rf"(?<![a-z0-9])(?:{'|'.join(parts)})") if parts else None
        patterns.append((concept, pattern, tuple(stem for stem in korean if len(stem.replace(" ", "")) >= 2)))
    return tuple(patterns)


class Relevance(tuple):
    """(the problem's word the piece has, the name it shares, attached?, its word of an effect)."""

    __slots__ = ()

    def __new__(cls, problem: str, subject: str, attached: bool, effect: str) -> "Relevance":
        return super().__new__(cls, (problem, subject, attached, effect))


def problem_relevance(
    text: str, score: float, query: ProblemQuery, *, title: str = "", floor: float = SCORE_FLOOR,
) -> Relevance:
    """How a piece stands against the relevance floor of a problem.

    The piece must share a name of the query (one the person said, when they
    named any) and say what went wrong in the same sentence, in words other
    than the names ("trypsin" is a word of the ``detach`` concept and a
    reagent: a piece that only names it says nothing about cells not
    detaching) -- or be titled by the name itself, a record about that thing
    -- speak of a cause, an effect or a hazard, and have been scored at least
    ``floor`` (``SCORE_FLOOR``, or the index model's floor in ``SCORE_FLOORS``).
    """

    haystack = unicodedata.normalize("NFKC", text).casefold()
    # What the person named is what the problem is about; the step's key
    # words stand in only when they named nothing.
    names = query.said or query.subjects
    heading = _plain(title)
    titled = next((name for name in names if heading and (
        heading == _plain(name) or heading.startswith(f"{_plain(name)} ("))), "")
    subject = titled
    problem = ""
    for sentence in re.split(r"(?<=[.!?。;])[\s/]+|\n+", haystack):
        named = titled or next((name for name in names if _has_term(sentence, name)), "")
        if not named:
            continue
        subject = subject or named
        found = _problem_word(_masked(sentence, query), query)
        if found:
            subject, problem = named, found
            break
    if not subject:
        subject = next((name for name in names if _has_term(haystack, name)), "")
    effect = _EFFECT_WORDS.search(_masked(haystack, query))
    effect_word = effect.group(0) if effect else ""
    return Relevance(problem, subject, bool(problem and subject and effect_word and score >= floor),
                     effect_word)


def _masked(folded: str, query: ProblemQuery) -> str:
    """``folded`` with every name of the query and every word it begins taken out."""

    masked = folded
    for name in query.subjects:
        needle = _plain(name)
        if _HANGUL.search(needle):
            masked = masked.replace(needle, " ")
        elif needle:
            masked = _english_pattern(needle, any_ending=True).sub(" ", masked)
    return masked


def _problem_word(masked: str, query: ProblemQuery) -> str:
    """The query's concept or [현상] entry ``masked`` says, or ""."""

    for concept, pattern, korean in _concept_patterns():
        if concept in query.problem and ((pattern is not None and pattern.search(masked))
                                         or any(stem in masked for stem in korean)):
            return concept
    return next((symptom.name for symptom in query.symptoms if symptom.found_in(masked)), "")


def default_glossary_path() -> Path:
    """Where the pipeline writes the glossary: ``data/public_knowledge/glossary.json``."""

    return Path(__file__).resolve().parents[2] / "data" / "public_knowledge" / "glossary.json"


def default_words_path() -> Path:
    """The hand-written glossary: ``data/public_knowledge/glossary_words.txt``."""

    return Path(__file__).resolve().parents[2] / "data" / "public_knowledge" / "glossary_words.txt"


def load_glossary(path: Path | str | None = None, words_path: Path | str | None = None) -> Glossary:
    """The glossary the tier searches with: Wikidata's names (``path``, by
    default ``glossary.json``) and the hand-written list (``words_path``; by
    default ``glossary_words.txt`` when ``path`` is the default too). A file
    that is missing or unreadable adds nothing; the tier still searches."""

    if path is None and words_path is None:
        words_path = default_words_path()
    candidate = Path(path) if path is not None else default_glossary_path()
    glossary = Glossary.empty()
    if candidate.is_file():
        try:
            glossary = Glossary.load(candidate)
        except (OSError, ValueError):
            glossary = Glossary.empty()
    if words_path is not None and Path(words_path).is_file():
        try:
            glossary = glossary.with_words(words_path)
        except OSError:
            pass
    return glossary


# --- The hand-written glossary (person's decision 2026-10-26, 2) --------------------------
#
# Lane KAUDIT: 133 of the 151 Korean questions the public search missed held a
# word the Wikidata glossary did not know (흄후드, 시료, 튜브, 버퍼, 배지,
# 전기영동, 폐액, 보안경 ...). ``glossary_words.txt`` is a list a person reads
# and edits: "Korean forms = English names", by kind.

#: The list's kinds: a thing a step handles or a hazard (a step's key word), a
#: technique (a name, not a step's key word), what went wrong, a general word.
WORD_KINDS: Mapping[str, str] = {
    "장비": "thing", "물질": "thing", "소재": "thing", "안전": "thing",
    "기법": "name", "현상": "symptom", "일반": "general",
}
_KIND_LINE = re.compile(r"^\[(.+)\]$")
_INLINE_NOTE = re.compile(r"\s+#.*$")


@dataclass(frozen=True)
class GlossaryWords:
    """What the hand-written list says, and the lines it could not read."""

    terms: Mapping[str, tuple[str, ...]]
    aliases: Mapping[str, tuple[str, ...]]
    general: frozenset[str]
    symptoms: tuple[Symptom, ...]
    things: frozenset[str]
    problems: tuple[str, ...]


def read_glossary_words(path: Path | str) -> GlossaryWords:
    """Read ``glossary_words.txt``; a line it cannot read is left out and named in ``problems``."""

    terms: dict[str, list[str]] = {}
    aliases: dict[str, list[str]] = {}
    general: set[str] = set()
    things: set[str] = set()
    symptoms: list[Symptom] = []
    problems: list[str] = []
    kind: str | None = None
    for number, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = _INLINE_NOTE.sub("", raw).strip()
        if not line or line.startswith("#"):
            continue
        heading = _KIND_LINE.match(line)
        if heading:
            kind = WORD_KINDS.get(heading.group(1).strip())
            if kind is None:
                problems.append(f"line {number}: unknown kind [{heading.group(1).strip()}]")
            continue
        if kind is None:
            continue
        if "=" not in line:
            problems.append(f"line {number}: no '=' (한국어 = English)")
            continue
        left, _sep, right = line.partition("=")
        korean = [form.strip() for form in left.split(";") if form.strip()]
        english = [name.strip() for name in right.split(";") if name.strip()]
        if not korean or not english:
            problems.append(f"line {number}: a side is empty")
            continue
        if kind == "symptom":
            symptoms.append(Symptom(english[0].casefold(), tuple(korean), tuple(english)))
            continue
        for form in korean:
            known = terms.setdefault(_normalise_term(form), [])
            known += [name for name in english if name not in known]
        for name in english:
            known = aliases.setdefault(name, [])
            known += [form for form in korean if form not in known]
        if kind == "general":
            general.update(_plain(item) for item in (*korean, *english))
        elif kind == "thing":
            things.update(_plain(name) for name in english)
    return GlossaryWords(
        terms={key: tuple(values) for key, values in terms.items()},
        aliases={key: tuple(values) for key, values in aliases.items()},
        general=frozenset(general), symptoms=tuple(symptoms), things=frozenset(things - general),
        problems=tuple(problems),
    )


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

    # Optional (lane KNOW2): ``index_model(index_name)`` names the index's
    # embedding model ("custom" for vectors made here), and ``query`` takes
    # ``embedding=`` for such an index.


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

    async def index_model(self, index_name: str) -> str:
        """The index's embedding model ("custom" when its vectors were made outside Moss)."""

        try:
            info = await self._client.get_index(index_name)
        except self._moss.MossError as exc:
            code = getattr(exc, "code", None) or "INTERNAL"
            raise KnowledgeUnavailable(INDEX_SETTING, f"moss index info failed: {code}") from exc
        return str(info.model.id)

    async def query(
        self, index_name: str, text: str, *, top_k: int, alpha: float,
        embedding: Sequence[float] | None = None,
    ) -> list[FoundDocument]:
        options = (self._moss.QueryOptions(top_k=top_k, alpha=alpha) if embedding is None
                   else self._moss.QueryOptions(top_k=top_k, alpha=alpha, embedding=list(embedding)))
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


# --- Multilingual embeddings (person's decision 2026-10-26, 5) -----------------------------
#
# Moss's built-in models are English. With ``model_id="custom"`` an index
# holds vectors made outside Moss and every query brings its own; both are
# made here, in process on the CPU, by one pinned multilingual model, so a
# Korean question and English material meet in one vector space. The model
# is an int8 ONNX export with an MIT, Apache-2.0 or BSD licence, pinned to a
# Hugging Face commit and checked file by file by its SHA-256; a file that is
# missing or differs is fetched again from that commit, and this tier stays
# off by name when it cannot be had.


@dataclass(frozen=True)
class EmbeddingModel:
    """One pinned embedding model and how it reads text."""

    name: str
    repo: str
    revision: str
    licence: str
    files: Mapping[str, tuple[str, str]]  # role -> (path in the repository, SHA-256)
    dims: int
    query_prefix: str = ""
    passage_prefix: str = ""
    max_tokens: int = 512

    @property
    def key(self) -> str:
        """The name the index's pieces carry (``embedding`` metadata): name and commit."""

        return f"{self.name}@{self.revision[:12]}"

    def url(self, role: str) -> str:
        return f"https://huggingface.co/{self.repo}/resolve/{self.revision}/{self.files[role][0]}"


#: The pinned model (lane KNOW2, 2026-10-26): 384 dimensions, int8 weights,
#: trained for retrieval with "query: "/"passage: " prefixes, XLM-R's
#: sentencepiece vocabulary (Korean and English in one space).
EMBEDDING_MODELS: Mapping[str, EmbeddingModel] = {
    "multilingual-e5-small": EmbeddingModel(
        name="multilingual-e5-small", repo="intfloat/multilingual-e5-small",
        revision="614241f622f53c4eeff9890bdc4f31cfecc418b3", licence="MIT",
        files={
            "model": ("onnx/model_qint8_avx512_vnni.onnx",
                      "dd476dd0c2514e9b9be83aeb3853fac0763e0bdf4a71645407587d77c48a2d88"),
            "pieces": ("sentencepiece.bpe.model",
                       "cfc8146abe2a0488e9e2a0c56de7952f7c11ab059eca145a0a727afce0db2865"),
        },
        dims=384, query_prefix="query: ", passage_prefix="passage: ",
    ),
}
#: The model the server embeds queries with when its index is ``custom`` (lane
#: KNOW2's measurement, 2026-10-26; the pipeline's ``embed``/``upload --embedding``
#: build the index with the same one).
EMBEDDING_MODEL = EMBEDDING_MODELS["multilingual-e5-small"]
#: The relevance floor's score by the index's model: Moss's own (None) and the
#: custom one. The e5 cosine of a question and a piece lies between about 0.83
#: and 0.90: on the offline hybrid run of 2026-10-26 (lane KNOW2, 359
#: questions, top five) a tenth of the gold pieces scored under 0.84, as did a
#: tenth of the others, so this floor only cuts the low tail and the words do
#: the rest. Moss's scale for a custom index is not documented: a person's
#: ``moss-run`` shows it.
SCORE_FLOORS: Mapping[str | None, float] = {None: SCORE_FLOOR, EMBEDDING_MODEL.key: 0.84}
#: Where a model's files are kept under a cache folder.
MODELS_FOLDER = "models"


def model_directory(cache_dir: Path | str, model: EmbeddingModel) -> Path:
    return Path(cache_dir) / MODELS_FOLDER / f"{model.name}-{model.revision[:12]}"


def _sha256(path: Path) -> str:
    import hashlib  # noqa: PLC0415

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _fetch_file(url: str, destination: Path) -> None:
    """One file from its pinned address (a temporary name, then renamed)."""

    import urllib.request  # noqa: PLC0415

    request = urllib.request.Request(url, headers={
        "User-Agent": "VoineyLabPublicKnowledge/0.1 (https://github.com/jaeiko/voiney-lab)"})
    partial = destination.with_suffix(destination.suffix + ".part")
    with urllib.request.urlopen(request, timeout=600) as reply, partial.open("wb") as handle:  # noqa: S310
        for block in iter(lambda: reply.read(1 << 20), b""):
            handle.write(block)
    partial.replace(destination)


def ensure_model_files(
    model: EmbeddingModel, directory: Path | str, *,
    fetch: Callable[[str, Path], None] | None = None,
) -> dict[str, Path]:
    """The model's files in ``directory``, each checked by its SHA-256; one
    missing or different is fetched from the pinned commit (``fetch``).
    Raises ``KnowledgeUnavailable`` naming the index setting when a file
    cannot be had or does not match."""

    folder = Path(directory)
    folder.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for role, (path_in_repo, digest) in model.files.items():
        path = folder / Path(path_in_repo).name
        if not path.is_file() or _sha256(path) != digest:
            try:
                (fetch or _fetch_file)(model.url(role), path)
            except Exception as exc:  # noqa: BLE001 - any failure leaves this tier off
                raise KnowledgeUnavailable(
                    INDEX_SETTING, f"embedding model file unavailable: {model.name} {role} ({type(exc).__name__})",
                ) from exc
            if not path.is_file() or _sha256(path) != digest:
                raise KnowledgeUnavailable(INDEX_SETTING, f"embedding model file differs: {model.name} {role}")
        paths[role] = path
    return paths


class _XlmrPieces:
    """XLM-R's sentencepiece ids as the Hugging Face tokenizer gives them:
    ``<s>`` 0, ``</s>`` 2, ``<unk>`` 3 and every other piece one above its
    sentencepiece id (fairseq's offset). The same ids as the model's
    ``tokenizer.json`` on 1,503 texts of the bundle (2026-10-26) in a fifth of
    the memory (+66 MiB against +286 MiB)."""

    def __init__(self, path: Path, max_tokens: int) -> None:
        from sentencepiece import SentencePieceProcessor  # noqa: PLC0415

        self._processor = SentencePieceProcessor(model_file=str(path))
        self._max = max_tokens

    def ids(self, text: str) -> list[int]:
        body = [piece + 1 if piece else 3 for piece in self._processor.encode(text)]
        return [0, *body[: self._max - 2], 2]


class LocalEmbedder:
    """``model`` run in process on the CPU (onnxruntime, sentencepiece): text
    in, unit vectors out, mean-pooled over the tokens.

    ``lean`` (the server's) turns onnxruntime's graph optimisation, weight
    pre-packing and memory arena off: the int8 model then stays int8 in
    memory -- about +135 MiB against +410 MiB, measured 2026-10-26 -- for
    about 30 ms a query against 18 ms, with the same vectors (difference
    under 1e-7). The pipeline's bulk embedding uses the fast session.
    """

    def __init__(self, model: EmbeddingModel, paths: Mapping[str, Path], *, threads: int = 1,
                 lean: bool = True) -> None:
        try:
            import numpy  # noqa: PLC0415 - optional native dependencies, loaded on use
            import onnxruntime  # noqa: PLC0415
            import sentencepiece  # noqa: F401, PLC0415
        except ImportError as exc:
            raise KnowledgeUnavailable(
                "onnxruntime", "onnxruntime, sentencepiece or numpy is not installed (pip install -e .)") from exc
        self.model = model
        self._numpy = numpy
        self._pieces = _XlmrPieces(paths["pieces"], model.max_tokens)
        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = max(1, threads)
        options.inter_op_num_threads = 1
        if lean:
            options.graph_optimization_level = onnxruntime.GraphOptimizationLevel.ORT_DISABLE_ALL
            options.enable_cpu_mem_arena = False
            options.enable_mem_pattern = False
            options.add_session_config_entry("session.disable_prepacking", "1")
        self._session = onnxruntime.InferenceSession(
            str(paths["model"]), options, providers=["CPUExecutionProvider"])
        self._inputs = {item.name for item in self._session.get_inputs()}

    def _run(self, texts: Sequence[str]) -> Any:
        np = self._numpy
        rows = [self._pieces.ids(text) for text in texts]
        width = max(len(row) for row in rows)
        ids = np.array([row + [1] * (width - len(row)) for row in rows], dtype=np.int64)  # <pad> is 1
        mask = np.array([[1] * len(row) + [0] * (width - len(row)) for row in rows], dtype=np.int64)
        feed = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self._inputs:
            feed["token_type_ids"] = np.zeros_like(ids)
        hidden = self._session.run(None, feed)[0]
        weights = mask[..., None].astype(np.float32)
        pooled = (hidden * weights).sum(axis=1) / np.clip(weights.sum(axis=1), 1e-9, None)
        norms = np.linalg.norm(pooled, axis=1, keepdims=True)
        return pooled / np.clip(norms, 1e-12, None)

    def embed_passages(self, texts: Sequence[str], *, batch: int = 16) -> Any:
        """Unit vectors (rows, float32) of pieces of material."""

        np = self._numpy
        rows = [self._run([self.model.passage_prefix + text for text in texts[start:start + batch]])
                for start in range(0, len(texts), batch)]
        return np.concatenate(rows).astype(np.float32) if rows else np.zeros((0, self.model.dims), np.float32)

    def embed_query(self, text: str) -> list[float]:
        """A question's unit vector."""

        return [float(value) for value in self._run([self.model.query_prefix + text])[0]]


# --- The public tier -----------------------------------------------------------------------


@dataclass(frozen=True)
class LoadReport:
    """What loading the index took; numbers only, for the log and the report."""

    index_name: str
    elapsed_ms: float
    glossary_terms: int


class PublicKnowledgeSource:
    """The ``public`` tier: one loaded Moss index, searched in process.

    ``search`` returns at most ``limit`` hits by the index's score (the grade
    first only where scores are close); each hit's ``details`` carry the
    source name, grade, domain and the spoken label of decision 6. With an
    ``embedder`` (a ``custom`` index, lane KNOW2) every query carries its
    vector made by the same pinned model the index's pieces were made with.
    """

    tier = PUBLIC_TIER

    def __init__(
        self,
        settings: PublicKnowledgeSettings,
        client: SearchClient,
        *,
        glossary: Glossary | None = None,
        embedder: Any = None,
    ) -> None:
        self.settings = settings
        self._client = client
        self.glossary = glossary or Glossary.empty()
        self.embedder = embedder
        self._loaded = False
        self.load_report: LoadReport | None = None

    @property
    def embedding(self) -> str | None:
        """The key of the model the queries are embedded with, or None (Moss's own model)."""

        return self.embedder.model.key if self.embedder is not None else None

    @property
    def score_floor(self) -> float:
        """The relevance floor's score for this index's model (scores differ by model)."""

        return SCORE_FLOORS.get(self.embedding, SCORE_FLOOR)

    async def _ask(self, text: str, *, top_k: int) -> list[FoundDocument]:
        if self.embedder is None:
            return await self._client.query(self.settings.index_name, text, top_k=top_k, alpha=self.settings.alpha)
        vector = await asyncio.to_thread(self.embedder.embed_query, text)
        return await self._client.query(self.settings.index_name, text, top_k=top_k, alpha=self.settings.alpha,
                                        embedding=vector)

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
            "embedding": self.embedding,
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
        found = await self._ask(expanded, top_k=max(limit * CANDIDATES_PER_HIT, limit))
        terms = self.glossary.query_terms(text)
        return ordered_hits(self._hit_from(doc, terms) for doc in found)[:limit]

    def for_problem(self, query: ProblemQuery) -> "ProblemSearch":
        """This tier asked about one problem (``query``) in place of the sentence."""

        return ProblemSearch(self, query)

    async def search_problem(self, query: ProblemQuery, *, limit: int) -> list[KnowledgeHit]:
        """The pieces for a problem, best first, each marked by the relevance floor.

        ``details`` gain ``problem_shares`` (the word of what went wrong the
        piece has), ``subject_shares`` (the name it shares), ``effect_word``
        (its word of a cause, an effect or a hazard) and ``relevant`` ("yes"
        when all three are there and the score clears the floor; "" else). Only
        relevant pieces are attached to the help
        (``external_references.public_reference_part``).
        """

        if not self.settings.enabled:
            raise KnowledgeUnavailable(ENABLED_SETTING, f"{ENABLED_SETTING} is false")
        if not self._loaded:
            raise KnowledgeUnavailable(INDEX_SETTING, "public knowledge index is not loaded")
        if limit <= 0 or not query.text.strip():
            return []
        found = await self._ask(query.text, top_k=max(limit * CANDIDATES_PER_HIT, limit))
        hits: list[KnowledgeHit] = []
        for doc in found:
            hit = self._hit_from(doc)
            problem, subject, relevant, effect = problem_relevance(
                f"{hit.title}\n{hit.locator}\n{hit.text}", hit.score, query, title=hit.title,
                floor=self.score_floor)
            hits.append(replace(hit, details=(
                *hit.details, ("problem_shares", problem), ("subject_shares", subject),
                ("effect_word", effect), ("relevant", "yes" if relevant else ""))))
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
    words_path: Path | str | None = None,
    embedder_factory: Callable[[EmbeddingModel], Any] | None = None,
) -> PublicKnowledgeSource | None:
    """Build and load the tier for the server's start, or None when it is off.

    Raises ``KnowledgeUnavailable`` naming the setting when the credentials
    are missing or the load fails; the caller records that and goes on
    without this tier (rule 7: visible, bounded, non-mutating).

    A ``custom`` index (lane KNOW2) is queried with vectors of
    ``EMBEDDING_MODEL``, whose pinned files are kept (fetched once, checked by
    SHA-256) under the cache folder; the index must have been built with that
    model (its pieces carry its key), or the tier stays off.
    """

    if not settings.enabled:
        return None
    missing = settings.missing_setting()
    if missing:
        raise KnowledgeUnavailable(missing)
    factory = client_factory or (lambda s: MossSdkClient(
        s.project_id, s.project_key, cache_dir=s.cache_dir))
    client = factory(settings)
    source = PublicKnowledgeSource(settings, client, glossary=load_glossary(glossary_path, words_path))
    try:
        await source.load()
    except KnowledgeUnavailable:
        await _close_quietly(client)
        raise
    except Exception as exc:  # noqa: BLE001 - any load failure is this tier's alone
        await _close_quietly(client)
        raise KnowledgeUnavailable(INDEX_SETTING, f"public knowledge load failed: {type(exc).__name__}") from exc
    try:
        await _with_embedder(source, client, settings, embedder_factory)
    except KnowledgeUnavailable:
        await _close_quietly(client)
        raise
    except Exception as exc:  # noqa: BLE001 - an embedding failure is this tier's alone
        await _close_quietly(client)
        raise KnowledgeUnavailable(INDEX_SETTING, f"public knowledge embedding failed: {type(exc).__name__}") from exc
    return source


async def _with_embedder(
    source: PublicKnowledgeSource, client: Any, settings: PublicKnowledgeSettings,
    embedder_factory: Callable[[EmbeddingModel], Any] | None,
) -> None:
    """Give a ``custom`` index's source its query embedder, after checking the
    index was built with the same model."""

    index_model = getattr(client, "index_model", None)
    if index_model is None or await index_model(settings.index_name) != "custom":
        return
    model = EMBEDDING_MODEL

    def build(model: EmbeddingModel) -> Any:
        if not settings.cache_dir:
            raise KnowledgeUnavailable(INDEX_SETTING, "no cache folder for the embedding model")
        return LocalEmbedder(model, ensure_model_files(model, model_directory(settings.cache_dir, model)))

    embedder = await asyncio.to_thread(embedder_factory or build, model)
    probe = await client.query(settings.index_name, "", top_k=1, alpha=1.0,
                               embedding=await asyncio.to_thread(embedder.embed_query, "laboratory"))
    built_with = probe[0].metadata.get("embedding", "") if probe else model.key
    if built_with != model.key:
        raise KnowledgeUnavailable(
            INDEX_SETTING, f"index built with another embedding model ({built_with or 'unknown'})")
    source.embedder = embedder


async def _close_quietly(client: SearchClient) -> None:
    try:
        await client.close()
    except Exception:  # noqa: BLE001 - closing a client that failed to load
        pass
