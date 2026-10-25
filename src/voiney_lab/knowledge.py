"""Knowledge search for help with a problem during a run (lane HELP, 2026-10-24).

A researcher says a problem at the bench -- "젤이 아직 파래", "침전이 생겼어",
"왜 안 되지?" -- and is told, without anything changing, what the sources say
about it, each with where it comes from (human decision 2026-10-24):

1. the protocol: the current step, the steps either side and the same section,
   their notes, expected results, warnings, repeat conditions and the source's
   troubleshooting lines, with the page and the source's own words;
2. history: the same account's earlier runs of the same protocol, what was
   recorded at the same step and what was recorded after it (the record's own
   ownership rule: no other account's record);
3. public material, when a provider for it is configured (lane KNOW adds one;
   none here), else -- only when the supplemental role is on -- possible causes
   as "AI 일반 지식", said after the answer (``server._queue_problem_causes``);
4. the closing sentence, ``CLOSING``.

The contract every source keeps (lane rules 10/24, 7): ``KnowledgeHit``,
``KnowledgeContext`` and ``KnowledgeSource``; a source that cannot be used
raises ``KnowledgeUnavailable`` naming its setting and no other source is
tried in its place. The protocol and history sources here search SQLite FTS5
tables inside the server process (nothing is sent to an outside index). Their
terms keep a number with its unit ("37°C" → ``v37degc``), English word stems,
Korean words without their particles, and concept terms that join a Korean
problem word to the English the source writes ("파래" and "destain" both carry
``cstain``), so a Korean utterance finds an English source sentence.

Nothing here changes workflow state, and nothing here is written by a model:
the composer only arranges the sources' own words and fixed sentences. The
help's checks are ``answer_checks.problem_help_violations``.
"""

from __future__ import annotations

import asyncio
import math
import re
import sqlite3
import threading
import time
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable


#: The tiers a hit may come from, in the order the help says them.
TIERS = ("protocol", "history", "lab", "public")

#: ④: how every help ends (human decision 2026-10-24). A condition is changed
#: only by a person, through "오늘 바꾼 값" (lane EASY's confirmation path).
CLOSING = {
    "ko": "조건을 바꾸려면 담당자와 확인하세요. 바꾸기로 하면 '오늘 바꾼 값'으로 말해 주세요.",
    "en": "To change a condition, check with the person in charge first. "
          "If you decide to change it, say it as today's changed value.",
}
#: Said when neither the protocol nor the history has anything on the problem.
NOTHING_FOUND = {
    "ko": "원문과 지난 기록에서 이 문제와 관련된 내용을 찾지 못했어요.",
    "en": "Neither the protocol nor earlier records say anything about this problem.",
}
#: The screen's names for where a line comes from.
TIER_LABELS = {
    "protocol": "원문",
    "history": "지난 기록",
    "lab": "연구실 자료",
    "public": "공개 자료",
}
#: What a protocol hit is, as the screen names it.
KIND_LABELS = {
    "step": "단계",
    "sub_action": "단계",
    "note": "노트",
    "tip": "팁",
    "warning": "주의",
    "expected_result": "예상 결과",
    "required_observation": "관찰할 것",
    "repeat_condition": "반복 조건",
    "branch_condition": "조건",
    "troubleshooting": "문제 해결",
}
#: How many lines of each tier the screen shows and how many are said.
SHOWN = {"protocol": 3, "history": 2, "public": 2}
SPOKEN = {"protocol": 2, "history": 1}
#: With a past record to say, one protocol line is said (the rest are shown).
SPOKEN_PROTOCOL_WITH_HISTORY = 1
#: Characters of one spoken source sentence before the rest is left to the screen.
SPOKEN_QUOTE_CHARS = 90


class KnowledgeUnavailable(RuntimeError):
    """A source cannot be searched; ``setting`` names what is missing or off."""

    def __init__(self, setting: str, message: str = "") -> None:
        super().__init__(message or f"{setting} is not set (설정 필요)")
        self.setting = setting


@dataclass(frozen=True)
class KnowledgeContext:
    """Whose run a search is for: the scope every source must keep to."""

    organization_id: str | None
    account_id: str | None
    protocol_id: str
    revision_id: str | None
    step_id: str | None
    language: str = "ko"


@dataclass(frozen=True)
class KnowledgeHit:
    """One piece of found text with where it comes from.

    ``text`` is the source's own words, never rewritten. ``details`` carries
    what one tier needs beyond the contract's fields, as text pairs: for the
    protocol the page, the step and its checked Korean reading; for history
    the run's date and what was recorded after the line. (An addition to the
    contract of 10/24, optional and empty for a source that has none.)
    """

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


@runtime_checkable
class KnowledgeSource(Protocol):
    """A searchable source of one tier."""

    tier: str

    async def search(
        self, query: str, *, limit: int, context: KnowledgeContext,
    ) -> list[KnowledgeHit]:
        ...


# --- Index terms ------------------------------------------------------------------

#: Concepts that join a researcher's Korean problem words to the English a
#: source writes. Each is (concept, English stems, Korean stems). General lab
#: language, not one protocol's words: an entry only matches where both sides
#: use it. A stem matches a word that starts with it ("destain" in
#: "destained"); a Korean stem anywhere in a word ("파래" in "파래요").
_CONCEPTS: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("stain", ("stain", "destain", "coomassie", "blue", "dye"),
     ("파래", "파랗", "파란", "푸르", "탈색", "염색", "색이 안 빠", "색이 남", "색 안 빠", "염료", "진해", "진하", "짙")),
    ("color", ("colo", "hue"), ("색",)),
    ("yellow", ("yellow",), ("노랗", "노래", "노란", "황색")),
    ("brown", ("brown",), ("갈색",)),
    ("red", ("red", "pink"), ("빨갛", "빨간", "붉", "분홍")),
    ("purple", ("purple", "violet"), ("보라",)),
    ("change", ("react", "chang", "turn", "develop"), ("변해", "변하", "변했", "변색", "바뀌", "반응")),
    ("transparent", ("transparent", "clear", "translucent"), ("투명", "맑")),
    ("white", ("white", "whitish", "opaque"), ("하얗", "하얘", "흰", "하얀", "불투명")),
    ("dehydrate", ("dehydrat", "dry", "dried", "shrink", "shrunk"),
     ("탈수", "말라", "마르", "말랐", "건조", "쪼그라", "줄어")),
    ("rehydrate", ("rehydrat", "swell", "swollen"), ("부풀", "불어", "팽창")),
    ("precipitate", ("precipit", "crystal", "particul", "sediment"),
     ("침전", "결정", "가라앉", "부유물", "알갱이")),
    ("clump", ("clump", "aggregat", "dissociat", "single cell", "single-cell"),
     ("덩어리", "뭉쳐", "뭉치", "응집", "엉겨")),
    ("residue", ("residu", "leftover", "left over", "carry-over", "carryover"),
     ("찌꺼기", "잔여", "남아 있", "남은 게")),
    ("cloudy", ("cloud", "turbid", "milky", "hazy"), ("탁해", "탁하", "뿌옇", "뿌연", "흐려", "흐릿")),
    ("foam", ("foam", "bubbl", "froth"), ("거품", "기포")),
    ("dissolve", ("dissolv", "solubil", "resolubil", "undissolved"), ("녹", "용해", "안 풀", "풀리")),
    ("mix", ("mix", "vortex", "homogen", "agitat", "shak", "invert"), ("섞", "혼합", "흔들", "볼텍스")),
    ("still", ("still", "until", "repeat", "again", "remain"),
     ("아직", "여전히", "계속", "그대로", "남아", "반복", "다시")),
    ("contamination", ("contamin", "keratin", "dust", "clean"), ("오염", "먼지", "케라틴", "이물질")),
    ("temperature", ("temperat", "°c", "heat", "warm", "cool", "cold"), ("온도", "뜨거", "차가", "식", "데워")),
    ("time", ("time", "min", "hour", "overnight", "incubat"), ("시간", "분 동안", "오래", "밤새", "배양")),
    ("volume", ("volume", "amount", "cover", "level", "fill"),
     ("부피", "모자라", "모자란", "모자랄", "부족", "덮", "채워", "채우")),
    ("light", ("light", "dark", "expos"), ("빛", "햇빛", "조명", "어두", "노출")),
    ("migrate", ("migrat", "reached the end", "dye front", "travel", "run "), ("내려가", "내려와", "이동", "끝까지", "움직")),
    ("gel", ("gel",), ("젤", "겔")),
    ("band", ("band",), ("밴드",)),
    ("protein", ("protein",), ("단백질",)),
    ("sample", ("sample",), ("샘플", "시료")),
    ("medium", ("media", "medium"), ("배지", "미디어")),
    ("signal", ("signal", "background", "fluoresc", "intensity", "faint"),
     ("신호", "배경", "형광", "세기", "희미", "흐린 밴드")),
    ("cell", ("cell", "confluen", "monolayer"), ("세포", "컨플루언")),
    ("detach", ("detach", "trypsin", "dissociat", "dislodg", "lift"), ("떨어", "안 떨어", "분리가 안")),
    ("attach", ("attach", "adher"), ("붙", "부착")),
    ("efficiency", ("efficien", "yield", "transfect", "express"), ("효율", "수율", "발현")),
    ("amplification", ("amplif", "ct value", "cq", "curve", "melt"), ("증폭", "커브", "곡선")),
    ("ph", ("ph",), ("피에이치", "산도")),
    ("measure", ("absorb", "reading", "standard", "calibrat", "curve"), ("흡광", "측정값", "검량", "표준")),
    ("leak", ("leak", "spill", "overflow", "crack", "broke"), ("새고", "새는", "샜", "흘렸", "넘쳤", "깨졌", "터졌")),
    ("loose", ("loose", "tight", "seal", "cap"), ("헐거", "헐겁", "느슨", "헐렁", "뚜껑", "밀폐", "잠가", "잠그")),
    ("wall", ("wall",), ("벽",)),
    ("filter", ("filter", "filtrat"), ("여과", "필터", "거름")),
    ("speed", ("slow", "fast", "speed", "quick"), ("느려", "느리", "빨라", "빠르", "속도", "더디")),
    ("distribution", ("distribut", "even", "uniform", "spread"), ("몰려", "쏠려", "고르", "균일", "골고루", "한쪽")),
    ("remove", ("remov", "aspirat", "discard", "drain"), ("빠져", "빠지", "빼", "제거", "버려", "흡입")),
    ("digest", ("digest",), ("분해",)),
    ("liquid", ("liquid", "solution", "supernatant", "buffer"), ("용액", "상층액", "버퍼", "액체")),
)
_ENGLISH_STOP = frozenset({
    "a", "an", "the", "of", "to", "and", "or", "is", "are", "be", "was", "were", "in",
    "on", "at", "by", "for", "from", "with", "as", "it", "its", "this", "that", "these",
    "those", "you", "your", "we", "our", "can", "will", "should", "may", "if", "then",
    "not", "no", "so", "do", "does", "did", "but", "into", "than", "there", "here",
    "what", "why", "how", "when", "which", "who", "my", "me", "i", "step", "steps",
    "note", "use", "using", "used", "make", "made", "up", "out", "all", "any", "each",
    "about", "approximately", "around", "usually", "really",
})
_KOREAN_STOP = frozenset({
    "이거", "그거", "저거", "이게", "그게", "이건", "그건", "뭐야", "뭐지", "뭐가", "어떡해", "어떻게",
    "어떡하지", "왜", "지금", "근데", "그런데", "그리고", "좀", "진짜", "너무", "완전",
    "있어", "없어", "됐어", "안돼", "안", "돼", "되", "되지", "하지", "해", "했어", "같아", "것",
    "생겼어", "생겼는데", "보여", "나와", "단계", "해야", "하는", "되는", "왜지",
    "문제", "문제야", "문제가", "문제지", "이상해", "이상하", "이상한", "잘", "이러지", "이래", "그래",
    "그러지", "어떡", "어쩌지", "어쩌", "하면", "해야", "될까", "되나", "거야", "건데", "는데", "다",
})
_HANGUL_PARTICLE = re.compile(
    r"(?:이랑|랑|이란|란|이에요|예요|입니다|이야|으로|에서|에게|한테|까지|부터|"
    r"처럼|마다|조차|이나|나|도|만|의|이|가|은|는|을|를|로|에|과|와|야|요)$"
)
#: A number with its unit, kept whole: "0.5 mL", "37°C", "800 rpm", "15min".
_VALUE = re.compile(
    r"(?<![\w.])(\d+(?:[.,]\d+)?)\s*"
    r"(µl|μl|ul|ml|nl|l|mg/ml|ng/ul|µg|μg|ug|mg|ng|g|mm3|mm³|mmol|mm|µm|μm|um|nm|m|"
    r"%|°c|℃|rpm|x\s*g|×\s*g|min|minutes?|h|hr|hours?|s|sec|seconds?|분|시간|초|도|배)"
    r"(?![a-z])",
)


def _stem(word: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def _value_term(number: str, unit: str) -> str:
    unit = (unit.replace(" ", "").replace("μ", "µ").replace("µ", "u").replace("℃", "°c")
            .replace("°", "deg").replace("%", "pct").replace("/", "per").replace("×", "x")
            .replace("³", "3"))
    unit = {"minutes": "min", "minute": "min", "hours": "h", "hour": "h", "hr": "h",
            "seconds": "s", "second": "s", "sec": "s", "분": "min", "시간": "h", "초": "s",
            "도": "degc"}.get(unit, unit)
    number = number.replace(",", ".")
    if "." in number:
        number = number.rstrip("0").rstrip(".") or "0"
    return f"v{number.replace('.', 'p')}{unit}"


def concepts_in(text: str) -> tuple[str, ...]:
    """The concept terms ``text`` carries, in the table's order."""

    said = unicodedata.normalize("NFKC", text).casefold()
    words = re.findall(r"[a-z][a-z0-9°]*", said)
    found: list[str] = []
    for concept, english, korean in _CONCEPTS:
        if any(any(word.startswith(stem) for word in words) for stem in english if " " not in stem) or any(
            stem in said for stem in english if " " in stem
        ) or any(stem in said for stem in korean):
            found.append(concept)
    return tuple(found)


#: What a value's unit says it is about: "60°C" is about temperature.
_UNIT_CONCEPTS = (
    ("temperature", re.compile(r"°c|℃|도$")),
    ("time", re.compile(r"^(?:min|minutes?|h|hr|hours?|s|sec|seconds?|분|시간|초)$")),
    ("volume", re.compile(r"^(?:µl|ul|ml|nl|l)$")),
)


@lru_cache(maxsize=1)
def _lab_words() -> tuple[tuple[str, re.Pattern[str], tuple[str, ...]], ...]:
    """The state machine's lab lexicon (Korean forms of the English a
    protocol writes), read and compiled once: "원심분리" finds "centrifuge"."""

    from voiney_lab.curated_protocol import _LAB_LEXICON

    return tuple(
        (key.replace("_", ""),
         re.compile("(?<![a-z])(?:" + "|".join(re.escape(surface) for surface in english) + ")"),
         korean)
        for key, english, korean, _stem in _LAB_LEXICON
    )


def index_terms(text: str) -> tuple[str, ...]:
    """The FTS terms of a text: values with units, English stems, Korean words, concepts."""

    said = unicodedata.normalize("NFKC", text).casefold().replace("μ", "µ")
    terms: list[str] = []
    for match in _VALUE.finditer(said):
        terms.append(_value_term(match.group(1), match.group(2)))
        terms.append(f"n{match.group(1).replace(',', '.').replace('.', 'p')}")
        unit = match.group(2).replace(" ", "")
        terms.extend(f"c{concept}" for concept, pattern in _UNIT_CONCEPTS if pattern.search(unit))
    for key, english, korean in _lab_words():
        if english.search(said) or any(form in said for form in korean):
            terms.append(f"l{key}")
    for word in re.findall(r"[a-z][a-z0-9]*", said):
        if len(word) >= 2 and word not in _ENGLISH_STOP:
            terms.append(_stem(word))
    for word in re.findall(r"[가-힣]+", said):
        stripped = _HANGUL_PARTICLE.sub("", word) if len(word) > 2 else word
        stripped = stripped or word
        if stripped not in _KOREAN_STOP and word not in _KOREAN_STOP:
            terms.append(stripped)
    terms.extend(f"c{concept}" for concept in concepts_in(said))
    return tuple(dict.fromkeys(term for term in terms if term))


def _match_query(terms: Iterable[str]) -> str:
    return " OR ".join(f'"{term}"' for term in terms)


class _TermIndex:
    """An in-memory SQLite FTS5 table of term strings.

    FTS5 finds the lines that hold any of the query's terms; they are ranked
    here by the terms they share, each weighted by its kind and by a smoothed
    inverse document frequency (bm25's own idf is near zero on a corpus of a
    few dozen lines, where a word in half of them counts for nothing). One
    connection, used under a lock from any thread; nothing is written to disk
    and nothing outside the process is asked.
    """

    def __init__(self, documents: Sequence[tuple[str, ...]]) -> None:
        self._lock = threading.Lock()
        self._connection = sqlite3.connect(":memory:", check_same_thread=False)
        self._connection.execute(
            "CREATE VIRTUAL TABLE terms USING fts5(body, tokenize='unicode61 remove_diacritics 0')"
        )
        self._connection.executemany(
            "INSERT INTO terms(rowid, body) VALUES(?, ?)",
            [(index + 1, " ".join(document)) for index, document in enumerate(documents)],
        )
        self._documents = tuple(frozenset(document) for document in documents)
        frequency: dict[str, int] = {}
        for document in self._documents:
            for term in document:
                frequency[term] = frequency.get(term, 0) + 1
        self._frequency = frequency

    def _weight(self, term: str) -> float:
        if term.startswith("c") and term[1:] in _CONCEPT_NAMES:
            kind = 0.5 if term[1:] in _SUBJECT_CONCEPTS else 2.0
        elif term.startswith("v") and term[1:2].isdigit():
            kind = 1.5
        elif term.startswith("n") and term[1:2].isdigit():
            kind = 0.5
        else:
            kind = 1.0
        count = len(self._documents) or 1
        return kind * math.log(1.0 + count / (self._frequency.get(term, 0) + 0.5))

    def rare(self, terms: Iterable[str]) -> frozenset[str]:
        """The names among ``terms`` few lines hold: a Latin word of four
        letters or more, or a lab word, in at most two lines."""

        return frozenset(
            term for term in terms
            if 0 < self._frequency.get(term, 0) <= 2
            and ((term[:1].isascii() and term.isalpha() and len(term) >= 4 and not (
                term.startswith("c") and term[1:] in _CONCEPT_NAMES))
                 or (term.startswith("l") and not term[1:2].isdigit()))
        )

    def search(self, terms: Sequence[str], *, limit: int = 200) -> list[tuple[int, float, frozenset[str]]]:
        """(document index, relevance > 0, the query terms it holds), best first."""

        wanted = tuple(dict.fromkeys(terms))
        if not wanted:
            return []
        with self._lock:
            rows = self._connection.execute(
                "SELECT rowid FROM terms WHERE terms MATCH ? LIMIT ?",
                (_match_query(wanted), limit),
            ).fetchall()
        found: list[tuple[int, float, frozenset[str]]] = []
        for (rowid,) in rows:
            document = self._documents[rowid - 1]
            matched = frozenset(wanted) & document
            relevance = sum(self._weight(term) for term in matched) / (1.0 + 0.02 * len(document))
            found.append((rowid - 1, relevance, matched))
        found.sort(key=lambda item: -item[1])
        return found


_CONCEPT_NAMES = frozenset(concept for concept, _english, _korean in _CONCEPTS)
#: Concepts that name what a sentence is about rather than what went wrong:
#: alone they do not make a sentence about the problem ("the gel is still
#: blue" is not about "the gel should look white").
_SUBJECT_CONCEPTS = frozenset({"still", "color", "liquid", "gel", "band", "cell"})


def _concepts(terms: Iterable[str]) -> set[str]:
    return {term[1:] for term in terms if term.startswith("c") and term[1:] in _CONCEPT_NAMES}


def _significant(matched: frozenset[str], rare: frozenset[str] = frozenset()) -> bool:
    """A match is one problem concept, two words or values that are not
    concepts, or one rare name ("speedvac", a lab word) the problem says."""

    concepts = _concepts(matched)
    words = {term for term in matched if not (term.startswith("c") and term[1:] in _CONCEPT_NAMES)}
    return bool(concepts - _SUBJECT_CONCEPTS) or len(words) >= 2 or bool(words & rare)


def problem_concepts(text: str) -> set[str]:
    """The concepts of a text that say what went wrong, not what it is about."""

    return set(concepts_in(text)) - _SUBJECT_CONCEPTS


def has_problem_terms(query: str) -> bool:
    """Whether the words name anything a source could be searched for."""

    terms = index_terms(query)
    return bool(_concepts(terms) - _SUBJECT_CONCEPTS) or len(
        [term for term in terms if not (term.startswith("c") and term[1:] in _CONCEPT_NAMES)]) >= 2


def _same_words(left: str, right: str) -> bool:
    """Two lines that say the same, numbers and a leading "Expected result" aside."""

    def plain(text: str) -> str:
        text = re.sub(r"^\s*(?:expected\s+result|note|tip)\s*", "", text.casefold())
        return re.sub(r"[\d\s.,:;()\-–]+", " ", text).strip()

    a, b = plain(left), plain(right)
    return bool(a and b and (a in b or b in a))


# --- The protocol -----------------------------------------------------------------

_TROUBLESHOOTING_CUE = re.compile(
    r"troubleshoot|\bif\s+(?:the|no|you|there|your|a|it|any|cells?|bands?)\b|\bproblem|\bpoor\b|\bweak\b"
    r"|\bhigh\s+background|\bavoid\b|\bimportant\b|\bcritical\b|\bdo\s+not\b|\bmake\s+sure\b"
    r"|주의|문제|해결|만약|경우",
    re.I,
)


@dataclass(frozen=True)
class _ProtocolUnit:
    kind: str
    text: str
    page: int
    step_index: int | None
    section_index: int | None
    reading: str = ""
    reading_source: str = ""


def _clean(text: str) -> str:
    return " ".join(str(text).split())


class ProtocolKnowledge:
    """The running protocol's own sentences, searched by FTS5 (tier "protocol").

    Built once per fixture from what the analysis kept -- each step's
    instruction, sub-actions, notes, tips, warnings, expected results and
    required observations, the repeat and branch conditions -- and the page
    lines the analysis kept nowhere that read as troubleshooting. Each hit is
    the source's own words with its page and step, and the checked Korean
    reading of a fact where the fixture has one.
    """

    tier = "protocol"

    def __init__(self, fixture: Any) -> None:
        started = time.perf_counter()
        self.fixture = fixture
        self.protocol_id = str(fixture.protocol_id)
        self.revision_id = str(getattr(fixture, "revision_id", "") or "")
        self.language = str(getattr(fixture.draft.protocol.metadata, "original_language", "") or "en")
        steps = tuple(fixture.steps)
        self._steps = steps
        self._step_index = {step.step_id: index for index, step in enumerate(steps)}
        section_of: dict[str, int] = {}
        for section_index, section in enumerate(fixture.draft.protocol.sections):
            for step in section.steps:
                section_of[step.step_id] = section_index
        self._section_of = section_of
        units: list[_ProtocolUnit] = []
        seen: set[str] = set()

        def add(kind: str, text: str, page: int, step_index: int | None, *,
                reading: str = "", reading_source: str = "") -> None:
            clean = _clean(text)
            if not clean or clean.casefold() in seen:
                return
            seen.add(clean.casefold())
            section = section_of.get(steps[step_index].step_id) if step_index is not None else None
            units.append(_ProtocolUnit(kind, clean, int(page or 0), step_index, section,
                                       _clean(reading), reading_source))

        for index, step in enumerate(steps):
            for fact in fixture.facts_for_step(index):
                if fact.kind not in {"step", "sub_action", "warning", "note", "expected_result"}:
                    continue
                kind = "step" if fact.kind == "step" else fact.kind
                add(kind, fact.text, fact.source_page, index,
                    reading=fixture.localized_fact(step.step_id, fact.fact_id) or "",
                    reading_source=fixture.localization_source(step.step_id, fact.fact_id) or "")
            for tip in step.tips:
                add("tip", tip.source_text, tip.evidence.source_page_number, index)
            for action in step.sub_actions:
                for kind, statements in (
                    ("note", action.notes), ("tip", action.tips), ("warning", action.warnings),
                    ("expected_result", action.expected_results),
                    ("required_observation", action.required_observations),
                ):
                    for item in statements:
                        add(kind, item.source_text, item.evidence.source_page_number, index)
        for construct in fixture.draft.protocol.constructs:
            condition = getattr(construct, "condition_source_text", None)
            if not condition:
                continue
            owner = getattr(construct, "step_id", None)
            repeated = tuple(getattr(construct, "repeated_step_ids", ()) or ())
            target = owner if owner in self._step_index else (
                repeated[-1] if repeated and repeated[-1] in self._step_index else None)
            kind = "repeat_condition" if repeated else "branch_condition"
            add(kind, condition, construct.evidence.source_page_number,
                self._step_index.get(target) if target else None)
        kept = " ".join(unit.text.casefold() for unit in units)
        first_step_on_page: dict[int, int] = {}
        for index, step in enumerate(steps):
            first_step_on_page.setdefault(step.evidence.source_page_number, index)
        extraction = getattr(fixture.draft, "extraction", None)
        for number, page in enumerate(getattr(extraction, "pages", ()) or (), 1):
            for line in re.split(r"(?<=[.!?])\s+|\n+", str(getattr(page, "text", "") or "")):
                clean = _clean(line)
                if (len(clean) < 25 or clean.casefold() in kept
                        or not _TROUBLESHOOTING_CUE.search(clean)):
                    continue
                add("troubleshooting", clean, number, first_step_on_page.get(number))
        self._units = tuple(units)
        self._index = _TermIndex([index_terms(f"{unit.text} {unit.reading}") for unit in units])
        self.build_ms = round((time.perf_counter() - started) * 1000, 2)

    @property
    def size(self) -> int:
        return len(self._units)

    def _nearness(self, unit: _ProtocolUnit, current: int | None) -> float:
        if current is None or unit.step_index is None:
            return 0.0
        if unit.step_index == current:
            return 1.0
        if abs(unit.step_index - current) == 1:
            return 0.6
        if unit.section_index is not None and unit.section_index == self._section_of.get(
                self._steps[current].step_id):
            return 0.4
        return 0.0

    def search_sync(
        self, query: str, *, limit: int, context: KnowledgeContext,
    ) -> list[KnowledgeHit]:
        if context.protocol_id != self.protocol_id:
            return []
        current = self._step_index.get(context.step_id or "")
        terms = index_terms(query)
        rare = self._index.rare(terms)
        scored: list[tuple[float, _ProtocolUnit]] = []
        if not has_problem_terms(query) and not rare:
            # "왜 안 되지?": nothing to search for, so what the source says at
            # this step about how it should go -- its notes, expected results,
            # warnings, tips and repeat conditions.
            scored = [
                (0.1, unit) for unit in self._units
                if current is not None and unit.step_index == current
                and unit.kind not in {"step", "sub_action"}
            ]
            terms = ()
        for document, relevance, matched in self._index.search(terms):
            unit = self._units[document]
            if not _significant(matched, rare):
                continue
            nearness = self._nearness(unit, current)
            advice = unit.kind not in {"step", "sub_action"}
            if not advice and nearness < 0.6:
                # A step's own instruction is said only for this step and the
                # steps either side; farther, what it says is no help here.
                continue
            if nearness == 0.0 and not (advice and (_concepts(matched) - _SUBJECT_CONCEPTS or matched & rare)):
                # Far from the step, only a note, result or warning that shares
                # a concept with the problem is said.
                continue
            # A line the source writes for when things go wrong is what the
            # help is for; a note, result or warning next; the steps last.
            bonus = 0.9 if unit.kind == "troubleshooting" else 0.5 if advice else 0.2
            score = relevance * (1.0 + nearness + bonus)
            scored.append((score, unit))
        scored.sort(key=lambda item: -item[0])
        if scored:
            best = scored[0][0]
            scored = [item for item in scored if item[0] >= 0.3 * best]
        chosen: list[tuple[float, _ProtocolUnit]] = []
        for score, unit in scored:
            if any(_same_words(unit.text, other.text) for _score, other in chosen):
                continue
            chosen.append((score, unit))
        hits: list[KnowledgeHit] = []
        for score, unit in chosen[:limit]:
            step = self._steps[unit.step_index] if unit.step_index is not None else None
            label = step.source_label if step is not None else ""
            where = f"p.{unit.page}" + (f" · {label}단계" if label else "")
            hits.append(KnowledgeHit(
                tier="protocol",
                title=(f"{label}단계 " if label else "") + KIND_LABELS.get(unit.kind, unit.kind),
                text=unit.text,
                locator=where,
                source_url=(
                    f"/api/protocols/{self.protocol_id}/revisions/{self.revision_id}/source-pages/{unit.page}"
                    if self.revision_id and unit.page else None),
                license=None,
                language=self.language,
                score=round(score, 4),
                details=(
                    ("kind", unit.kind), ("page", str(unit.page)), ("step_label", label),
                    ("step_id", step.step_id if step is not None else ""),
                    ("reading", unit.reading), ("reading_source", unit.reading_source),
                ),
            ))
        return hits

    async def search(
        self, query: str, *, limit: int, context: KnowledgeContext,
    ) -> list[KnowledgeHit]:
        return self.search_sync(query, limit=limit, context=context)


# --- History: the same account's earlier runs of this protocol ----------------------

#: The record events a past problem or observation is read from.
_HISTORY_EVENTS = frozenset({"anomaly", "observation"})
#: What may follow a past problem at its step, and how it is named.
_AFTER_EVENTS = {
    "observation": "관찰", "anomaly": "이상 사항", "record_corrected": "기록 정정",
    "repeat_returned": "반복 구간으로 돌아감", "step_completed": "단계 완료",
    "step_reverted": "이전 단계로 되돌림", "experimenter_recorded": "실험자 기록",
}


@dataclass(frozen=True)
class _HistoryUnit:
    kind: str
    words: str
    step_id: str
    step_label: str
    started_at: str
    report_id: str
    after: tuple[str, ...]


class HistoryKnowledge:
    """Earlier runs of the same protocol by the same account (tier "history").

    The scope is the current record's own: the same organisation, the same
    owner and the same protocol, started before this run -- the rule the
    notebook's "지난번" follows (``ExperimentReportStore.earlier_reports``).
    A record with an organisation and no owner has no history. A context
    naming another organisation, account or protocol finds nothing.
    """

    tier = "history"

    def __init__(self, store: Any, report_id: str, *, step_labels: Mapping[str, str] | None = None,
                 max_reports: int = 20) -> None:
        self.store = store
        self.report_id = report_id
        self.step_labels = dict(step_labels or {})
        self.max_reports = max_reports
        self._lock = threading.Lock()
        self._loaded: tuple[dict[str, Any], tuple[_HistoryUnit, ...], _TermIndex | None] | None = None

    def _load(self) -> tuple[dict[str, Any], tuple[_HistoryUnit, ...], _TermIndex | None]:
        with self._lock:
            if self._loaded is not None:
                return self._loaded
            report, earlier = self.store.earlier_reports(self.report_id, limit=self.max_reports)
            units: list[_HistoryUnit] = []
            for past in earlier:
                events = list(past.get("events") or ())
                for position, event in enumerate(events):
                    words = _clean(event.get("user_wording") or "")
                    if event.get("event_type") not in _HISTORY_EVENTS or not words:
                        continue
                    step_id = str(event.get("step_id") or "")
                    label = str(event.get("step_label") or "")
                    after: list[str] = []
                    for later in events[position + 1:]:
                        if (str(later.get("step_id") or "") != step_id
                                and str(later.get("step_label") or "") != label):
                            continue
                        kind = str(later.get("event_type") or "")
                        if kind not in _AFTER_EVENTS:
                            continue
                        said = _clean(later.get("user_wording") or "")
                        if kind == "record_corrected":
                            said = _clean(((later.get("payload") or {}).get("record_fix") or {}).get("text_after") or said)
                        after.append(f"{_AFTER_EVENTS[kind]}: {said}" if said else _AFTER_EVENTS[kind])
                        if len(after) == 2:
                            break
                    units.append(_HistoryUnit(
                        str(event.get("event_type")), words, step_id, label,
                        str(past.get("started_at") or ""), str(past.get("report_id") or ""), tuple(after),
                    ))
            index = _TermIndex([index_terms(unit.words) for unit in units]) if units else None
            self._loaded = (report, tuple(units), index)
            return self._loaded

    def scope(self) -> tuple[str | None, str | None]:
        """The current record's organisation and owner: the only scope searched."""

        report, _units, _index = self._load()
        return report.get("organization_id") or None, report.get("owner_principal_id") or None

    def _in_scope(self, report: Mapping[str, Any], context: KnowledgeContext) -> bool:
        return bool(
            str(report.get("protocol_id") or "") == context.protocol_id
            and (report.get("organization_id") or None) == (context.organization_id or None)
            and (report.get("owner_principal_id") or None) == (context.account_id or None)
            and not (report.get("organization_id") and not report.get("owner_principal_id"))
        )

    def search_sync(
        self, query: str, *, limit: int, context: KnowledgeContext,
    ) -> list[KnowledgeHit]:
        report, units, index = self._load()
        if index is None or not self._in_scope(report, context):
            return []
        label = self.step_labels.get(context.step_id or "", "")

        def same_step(unit: _HistoryUnit) -> bool:
            return bool((context.step_id and unit.step_id == context.step_id)
                        or (label and unit.step_label == label))

        chosen: list[tuple[float, _HistoryUnit, str]] = []
        taken: set[int] = set()
        terms = index_terms(query)
        rare = index.rare(terms)
        for document, relevance, matched in index.search(terms):
            unit = units[document]
            if same_step(unit) and _significant(matched, rare):
                chosen.append((relevance + 1.0, unit, "words"))
                taken.add(document)
        # A problem recorded at the same step is told even in other words.
        for document, unit in enumerate(units):
            if document not in taken and unit.kind == "anomaly" and same_step(unit):
                chosen.append((0.5, unit, "step"))
        # Matched by words first, then the most recent run.
        chosen.sort(key=lambda item: (item[2] != "words", -_date_key(item[1].started_at), -item[0]))
        hits: list[KnowledgeHit] = []
        for score, unit, why in chosen[:limit]:
            day = _day_words(unit.started_at)
            hits.append(KnowledgeHit(
                tier="history",
                title=f"지난 실험 {day} · {unit.step_label}단계 {'이상 사항' if unit.kind == 'anomaly' else '관찰'}",
                text=unit.words,
                locator=f"{day} · {unit.step_label}단계",
                source_url=None,
                license=None,
                language="ko" if re.search(r"[가-힣]", unit.words) else "en",
                score=round(score, 4),
                details=(
                    ("kind", unit.kind), ("day", day), ("step_label", unit.step_label),
                    ("report_id", unit.report_id), ("matched_by", why),
                    *(("after", line) for line in unit.after),
                ),
            ))
        return hits

    async def search(
        self, query: str, *, limit: int, context: KnowledgeContext,
    ) -> list[KnowledgeHit]:
        return await asyncio.to_thread(self.search_sync, query, limit=limit, context=context)


def _moment(started_at: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _date_key(started_at: str) -> float:
    moment = _moment(started_at)
    return moment.timestamp() if moment is not None else 0.0


def _day_words(started_at: str) -> str:
    """"10월 20일" in the report's time zone, or "날짜 모름"."""

    from voiney_lab.experiment_reports import report_timezone

    moment = _moment(started_at)
    if moment is None:
        return "날짜 모름"
    moment = moment.astimezone(report_timezone())
    return f"{moment.month}월 {moment.day}일"


# --- Searching every tier ----------------------------------------------------------


@dataclass(frozen=True)
class KnowledgeSearch:
    """What one problem's search found, tier by tier, and how long each took."""

    hits: tuple[KnowledgeHit, ...]
    elapsed_ms: Mapping[str, float]
    unavailable: tuple[str, ...] = ()
    failed: tuple[str, ...] = ()

    def tier(self, name: str) -> tuple[KnowledgeHit, ...]:
        return tuple(hit for hit in self.hits if hit.tier == name)


async def search_tiers(
    sources: Sequence[KnowledgeSource],
    query: str,
    *,
    context: KnowledgeContext,
    limit: int = 4,
    timeout_seconds: float = 1.5,
) -> KnowledgeSearch:
    """Search each source at once; a source that is unavailable, fails or is
    late is skipped and named, and none is tried in its place."""

    async def one(source: KnowledgeSource) -> tuple[str, list[KnowledgeHit], float, str]:
        started = time.perf_counter()
        try:
            found = await asyncio.wait_for(
                source.search(query, limit=limit, context=context), timeout=timeout_seconds)
            outcome = "ok"
        except KnowledgeUnavailable as exc:
            found, outcome = [], f"unavailable:{exc.setting}"
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a failed source is skipped and named
            found, outcome = [], f"failed:{type(exc).__name__}"
        tier = str(getattr(source, "tier", "unknown"))
        kept = [hit for hit in found if isinstance(hit, KnowledgeHit) and hit.tier == tier]
        return tier, kept, round((time.perf_counter() - started) * 1000, 2), outcome

    results = await asyncio.gather(*(one(source) for source in sources))
    order = {tier: index for index, tier in enumerate(TIERS)}
    hits = [hit for _tier, found, _ms, _outcome in sorted(results, key=lambda r: order.get(r[0], 99))
            for hit in found]
    return KnowledgeSearch(
        hits=tuple(hits),
        elapsed_ms={tier: ms for tier, _found, ms, _outcome in results},
        unavailable=tuple(outcome.split(":", 1)[1] for _t, _f, _m, outcome in results
                          if outcome.startswith("unavailable:")),
        failed=tuple(f"{tier}:{outcome.split(':', 1)[1]}" for tier, _f, _m, outcome in results
                     if outcome.startswith("failed:")),
    )


# --- The help, put together --------------------------------------------------------


@dataclass(frozen=True)
class ProblemHelp:
    """The help said and shown after a problem, built from the hits alone.

    ``display`` and ``speech`` hold ①, ② and the public lines (③ from a model
    comes later, after the answer); ``closing`` is ④, said at the end of
    ``speech`` unless ``closing_held`` (then after ③). ``sources`` names each
    line's tier and place for the screen and the record.
    """

    display: str
    speech: str
    closing: str
    closing_held: bool
    sources: tuple[Mapping[str, str], ...] = ()
    found: Mapping[str, int] = field(default_factory=dict)
    quoted_text: str = ""

    @property
    def spoken(self) -> str:
        return self.speech if self.closing_held else f"{self.speech} {self.closing}".strip()

    @property
    def shown(self) -> str:
        return f"{self.display}\n\n{self.closing}".strip()


def _first_sentence(text: str) -> str:
    match = re.match(r"(.+?[.!?。])(?:\s|$)", text)
    return match.group(1) if match else text


def _spoken_quote(hit: KnowledgeHit, language: str) -> tuple[str, str]:
    """How a protocol line is said -- its checked Korean reading, or the page
    only -- and the part of the source's words it says (a quote too)."""

    page = hit.detail("page")
    reading = hit.detail("reading")
    if language != "ko":
        sentence = _first_sentence(hit.text)
        return f"The protocol, page {page}: {sentence}", sentence
    if not reading:
        return f"원문 {page}쪽에 관련 문장이 있어요. 화면에서 확인해 주세요.", ""
    said = reading if len(reading) <= SPOKEN_QUOTE_CHARS else _first_sentence(reading)
    machine = " 자동 번역이에요." if hit.detail("reading_source") == "machine" else ""
    rest = " 나머지는 화면에 있어요." if said != reading else ""
    return f"원문 {page}쪽: {said}{machine}{rest}", said


def _spoken_history(hit: KnowledgeHit, language: str) -> str:
    after = [value for key, value in hit.details if key == "after"]
    if language != "ko":
        line = f"Earlier, on {hit.detail('day')} at step {hit.detail('step_label')}, the record says: {hit.text}."
        return line + (f" After that: {after[0]}." if after else "")
    line = f"지난 {hit.detail('day')} 실험 {hit.detail('step_label')}단계 기록: “{hit.text}”."
    if after:
        line += f" 그 뒤 기록: {after[0]}."
    return line


def compose_problem_help(
    found: KnowledgeSearch | Sequence[KnowledgeHit],
    *,
    language: str,
    causes_follow: bool = False,
) -> ProblemHelp:
    """①, ② and the public lines from the hits, then ④ (or ④ held for ③).

    Only the sources' own words and fixed sentences: a protocol line as the
    source prints it with its page (its checked Korean reading said), a past
    record as it was said with its date and what followed it, a public line
    with its address and licence. Nothing found says so.
    """

    hits = found.hits if isinstance(found, KnowledgeSearch) else tuple(found)
    language = "ko" if language not in {"ko", "en"} else language
    protocol = [hit for hit in hits if hit.tier == "protocol"][: SHOWN["protocol"]]
    history = [hit for hit in hits if hit.tier == "history"][: SHOWN["history"]]
    public = [hit for hit in hits if hit.tier == "public"][: SHOWN["public"]]
    shown: list[str] = []
    said: list[str] = []
    sources: list[dict[str, str]] = []
    quoted: list[str] = []
    for hit in protocol:
        shown.append(f"{TIER_LABELS['protocol']} · {hit.locator} · {KIND_LABELS.get(hit.detail('kind'), '')}"
                     f"\n“{hit.text}”" + (f"\n{hit.detail('reading')}" if hit.detail("reading") else ""))
        sources.append({"tier": "protocol", "locator": hit.locator, "kind": hit.detail("kind"),
                        "page": hit.detail("page"), "step_label": hit.detail("step_label")})
        quoted += [hit.text, hit.detail("reading")]
    spoken_protocol = SPOKEN_PROTOCOL_WITH_HISTORY if history else SPOKEN["protocol"]
    for hit in protocol[:spoken_protocol]:
        sentence, part = _spoken_quote(hit, language)
        said.append(sentence)
        quoted.append(part)
    for hit in history:
        after = [value for key, value in hit.details if key == "after"]
        shown.append(f"{TIER_LABELS['history']} · {hit.locator}\n“{hit.text}”"
                     + "".join(f"\n그 뒤 {line}" for line in after))
        sources.append({"tier": "history", "locator": hit.locator, "kind": hit.detail("kind"),
                        "day": hit.detail("day"), "step_label": hit.detail("step_label")})
        quoted += [hit.text, *after]
    for hit in history[: SPOKEN["history"]]:
        said.append(_spoken_history(hit, language))
    for hit in public:
        shown.append(f"{TIER_LABELS['public']} · {hit.title} · {hit.locator}\n“{hit.text}”"
                     + (f"\n{hit.source_url}" if hit.source_url else "")
                     + (f" · {hit.license}" if hit.license else ""))
        sources.append({"tier": "public", "locator": hit.locator, "kind": "public",
                        "url": hit.source_url or "", "license": hit.license or ""})
        quoted.append(hit.text)
    if public:
        said.append("공개 자료에도 관련 내용이 있어요. 화면에서 확인해 주세요."
                    if language == "ko" else "Public material says something on it too; see the screen.")
    if not protocol and not history and not public:
        shown.append(NOTHING_FOUND[language])
        said.append(NOTHING_FOUND[language])
    return ProblemHelp(
        display="\n\n".join(shown),
        speech=" ".join(said),
        closing=CLOSING[language],
        closing_held=causes_follow,
        sources=tuple(sources),
        found={"protocol": len(protocol), "history": len(history), "public": len(public)},
        quoted_text="\n".join(item for item in quoted if item),
    )
