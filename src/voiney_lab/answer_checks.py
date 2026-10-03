"""Server checks on a model-written answer, shared by every answering role.

A model may phrase an answer; it may not decide what the answer is allowed to
contain. Each check here reads the answer text (and, where it needs one, the
question or the server's own values) and says whether the answer may be used.
None of them rewrites anything: a failed check means the caller drops the
model's answer and falls back to the server's own reply.

The number and mutation-claim checks moved here from ``multi_brain``, which
keeps using them exactly as before. The outside-PDF, display-label and
server-value checks are new for the lane R answer structure
(``~/reports/lane_r_design.md`` §5) and nothing calls them yet.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass


# --- Numbers -------------------------------------------------------------------

#: A number with a laboratory unit, or a clock time. Moved unchanged from
#: ``multi_brain``: an answer may only say the numbers its evidence says.
NUMERIC = re.compile(
    r"(?:\d{2}:\d{2}:\d{2}|\d+(?:\.\d+)?\s*"
    r"(?:mg/mL|ng/uL|mm3|mm³|µL|uL|mL|ml|mM|°C|rpm|min|v/v|C|h|%))",
    re.I,
)


def numbers_in(value: str) -> frozenset[str]:
    """The unit-bearing numbers in ``value``, spacing and µ/μ normalized."""

    return frozenset(
        item.casefold().replace(" ", "").replace("μ", "µ")
        for item in NUMERIC.findall(value)
    )


def introduces_numbers(answer: str, evidence: str) -> bool:
    """True when ``answer`` states a number with a unit that ``evidence`` does not."""

    return not numbers_in(answer).issubset(numbers_in(evidence))


#: Any number, whatever follows it: "15분", "37도", "800 rpm", "0.5", "1,000".
#: NUMERIC above knows only Latin units, so "20분" passed it unread.
_BARE_NUMBER = re.compile(r"(?<![\d.,])\d+(?:[.,]\d+)*")
#: A reference to a step by its number: "3단계", "3번째 단계", "step 3".
_STEP_REFERENCE = re.compile(r"(\d+)\s*(?:번째\s*)?단계|\bsteps?\s+(\d+)", re.I)


def _bare_numbers(value: str) -> set[str]:
    found = set()
    for match in _BARE_NUMBER.findall(unicodedata.normalize("NFKC", value)):
        number = match.replace(",", "")
        if "." in number:
            number = number.rstrip("0").rstrip(".") or "0"
        found.add(number.lstrip("0") or "0")
    return found


def introduces_bare_numbers(
    answer: str, evidence: str, *, step_labels: Iterable[str] = (),
) -> bool:
    """True when ``answer`` says any number ``evidence`` does not (lane R).

    Stricter than introduces_numbers: the unit does not matter, so "15분"
    needs a 15 in the evidence ("15min", "00:15:00"). A step named by a
    label the protocol has ("3단계") is a reference, not a quantity.
    """

    labels = {str(label) for label in step_labels}

    def drop_step_reference(match: re.Match[str]) -> str:
        label = next(group for group in match.groups() if group is not None)
        return "" if label in labels else match.group(0)

    said = _bare_numbers(
        _STEP_REFERENCE.sub(drop_step_reference, unicodedata.normalize("NFKC", answer))
    )
    return not said.issubset(_bare_numbers(evidence))


# --- Claims that state changed -------------------------------------------------

#: The answer says it saved, recorded or completed something. Moved unchanged
#: from ``multi_brain``'s Answer Brain gate, which still uses exactly this.
MUTATION_CLAIM = re.compile(
    r"(?:I|제가|내가).{0,40}(?:saved|recorded|persisted|저장|기록)"
    r"|(?:저장|기록)(?:했|됐|되었|했습니다)"
    r"|(?:step|단계).*(?:completed|완료 처리)",
    re.I,
)

#: The wider list of design §5-2, for the lane R answer: moving on, starting,
#: ending, pausing or starting a timer, said as done. Only the server says what
#: happened; an answer turn changes nothing, so any of these is false.
STATE_CHANGE_CLAIM = re.compile(
    r"넘어갔|넘어가겠습니다|넘어갈게요|다음\s*단계로\s*(?:이동|진행)했"
    r"|(?:시작|종료|일시\s*정지|일시\s*중지|재개)(?:했습니다|했어요|했어|했다|됐습니다|되었습니다)"
    r"|타이머(?:를|가)?\s*(?:시작|켰|맞췄|맞춰\s*두었)"
    r"|완료\s*처리(?:했|됐|되었|하였)"
    r"|\b(?:moved\s+(?:you\s+)?on|advanced|i(?:'|’)ve\s+(?:started|stopped|paused|resumed|ended))\b"
    r"|\b(?:timer\s+(?:is\s+)?(?:started|running\s+now))\b",
    re.I,
)


def claims_mutation(text: str) -> bool:
    """The Answer Brain's existing test: saved, recorded or completed, said as done."""

    return MUTATION_CLAIM.search(text) is not None


def claims_state_change(text: str) -> bool:
    """``claims_mutation`` or the wider design §5-2 list (lane R; not used yet)."""

    return claims_mutation(text) or STATE_CHANGE_CLAIM.search(text) is not None


# --- Display labels --------------------------------------------------------------

#: The block labels the screen reads as layout and prints none of (lane N,
#: ``ANSWER_LABEL_LINES`` in ``static/index.html`` and the label lists in
#: ``tests/test_screen_text_rules.py`` / ``tests/test_reader_speech.py``), and
#: the outside-PDF marks the server adds itself (design §5-1). The server
#: attaches source, citation and development information as separate values; a
#: model that writes them into its own text duplicates them or forges them.
DISPLAY_LABELS: tuple[str, ...] = (
    "직접 답변",
    "Direct answer",
    "답변 · 한국어",
    "한국어 참고 번역",
    "자동 번역",
    "한국어 안내 · ",
    "원문 · English",
    "원문 · ",
    "원문 p.",
    "출처 ·",
    "근거 경계",
    "Source boundary",
    "개발 정보",
    "안내 ·",
    "주의 ·",
    "PDF 밖",
    "PDF에는 따로 설명이 없어요",
)
#: A line that is nothing but a label ("출처", "원문", "Sources") is a label too.
_LABEL_LINE = re.compile(
    r"^(?:직접 답변|Direct answer|답변|원문|Original|출처|Sources?|근거 경계|"
    r"Source boundary|PDF 기준|세부 항목)$",
    re.I,
)


def display_label_violations(text: str) -> tuple[str, ...]:
    """The display labels found in ``text``, in table order; empty when none."""

    found = [label for label in DISPLAY_LABELS if label in text]
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and _LABEL_LINE.fullmatch(stripped) and stripped not in found:
            found.append(stripped)
    return tuple(found)


# --- Outside-PDF explanations (decision D4) -------------------------------------

#: The longest outside-PDF explanation, in characters (design §5-2, D4).
OUTSIDE_PDF_MAX_CHARS = 120

#: What an outside-PDF explanation may answer: what a word means, or (D4) why
#: a reagent or piece of equipment is there -- its role or purpose.
_MEANING_QUESTION = re.compile(
    r"뭐야|뭐예요|뭔가요|뭔데|무엇|뭘까|뜻|의미|란\s*게|라는\s*게|이란|란\??$|"
    r"\bwhat\s+(?:is|are|does)\b|\bmeaning\b|\bdefine\b",
    re.I,
)
_PURPOSE_QUESTION = re.compile(
    r"왜|역할|목적|용도|무슨\s*일|하는\s*거|쓰는\s*(?:거|이유)|넣는\s*이유|"
    r"\bwhy\b|\bpurpose\b|\brole\b|\bwhat\s+for\b|\bwhat\s+does\s+it\s+do\b",
    re.I,
)
#: What an outside-PDF explanation may never answer: a quantity, a method, a
#: safety question, or when a step is done. Those come from the approved
#: Protocol or approved safety documents only.
_FORBIDDEN_QUESTION_DIMENSIONS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("quantity", re.compile(
        r"얼마|몇|양은|양이|농도|온도|부피|용량|시간|분\s*동안|"
        r"\bhow\s+(?:much|many|long)\b|\btemperature\b|\bconcentration\b|\bvolume\b",
        re.I,
    )),
    ("method", re.compile(
        r"어떻게|방법|순서|절차|\bhow\s+(?:do|should|to|can)\b|\bprocedure\b|\bsteps?\s+to\b",
        re.I,
    )),
    ("safety", re.compile(
        r"안전|위험|독성|유해|해로|화상|보호구|장갑|환기|후드|"
        r"\bhazard|\bsafe(?:ty)?\b|\btoxic|\bppe\b|\bglove",
        re.I,
    )),
    ("completion", re.compile(
        r"완료|끝나|끝났|넘어가|다음\s*단계|언제까지|될\s*때까지|"
        r"\bdone\b|\bcomplete|\bfinish|\bmove\s+on\b",
        re.I,
    )),
)
#: The same four kinds of content, said in an answer.
_FORBIDDEN_ANSWER_CONTENT: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("method", re.compile(
        # An instruction: "넣으세요", "하십시오", "닫아야 합니다".
        r"(?:세요|십시오)(?=[\s.!?,]|$)|야\s*(?:합니다|해요|해|돼요|됩니다)|"
        r"\byou\s+(?:should|must|need\s+to)\b",
        re.I,
    )),
    ("safety", re.compile(
        r"안전|위험|독성|유해|해로|화상|보호구|장갑|환기|후드|"
        r"\bhazard|\bsafe(?:ty)?\b|\btoxic|\bppe\b|\bglove",
        re.I,
    )),
    ("completion", re.compile(
        r"완료\s*기준|될\s*때까지|넘어가|다음\s*단계로|"
        r"\buntil\b|\bmove\s+on\b|\bcomplete(?:d|ion)?\b",
        re.I,
    )),
)
_ANY_DIGIT = re.compile(r"[0-9０-９]")


def _term_key(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def outside_pdf_violations(
    answer: str,
    *,
    question: str,
    term: str | None,
    protocol_terms: Iterable[str],
) -> tuple[str, ...]:
    """Why an outside-PDF explanation may not be used; empty when it may.

    Decision D4 (2026-10-02): an explanation the PDF does not give is allowed
    for what a word of the active Protocol means and, like it, for why a
    reagent or piece of equipment is used (its role or purpose) -- at most
    120 characters, with no number and nothing about quantities, method,
    safety or completion, and shown with the same outside-PDF mark (which the
    server adds, not the model).
    """

    violations: list[str] = []
    terms = {_term_key(item) for item in protocol_terms if item and item.strip()}
    if not term or _term_key(term) not in terms:
        violations.append("term_not_in_active_protocol")
    if not (_MEANING_QUESTION.search(question) or _PURPOSE_QUESTION.search(question)):
        violations.append("question_not_meaning_or_purpose")
    for dimension, pattern in _FORBIDDEN_QUESTION_DIMENSIONS:
        if pattern.search(question):
            violations.append(f"question_asks_{dimension}")
    body = answer.strip()
    if not body:
        violations.append("empty_answer")
    if len(body) > OUTSIDE_PDF_MAX_CHARS:
        violations.append("answer_too_long")
    if _ANY_DIGIT.search(body) or NUMERIC.search(body):
        violations.append("answer_has_number")
    for dimension, pattern in _FORBIDDEN_ANSWER_CONTENT:
        if pattern.search(body):
            violations.append(f"answer_states_{dimension}")
    if display_label_violations(body):
        violations.append("answer_has_display_label")
    return tuple(violations)


# --- Values only the server owns -------------------------------------------------

@dataclass(frozen=True)
class ServerValues:
    """The identifiers an answer may only repeat exactly as the server has them."""

    title: str
    revision_id: str
    hashes: tuple[str, ...]
    step_count: int
    current_step_label: str | None


#: A run of hex long enough to be (part of) a hash: 8 or more, with a digit.
_HEX_RUN = re.compile(r"(?<![0-9A-Za-z])(?=[0-9a-fA-F]*[0-9])[0-9a-fA-F]{8,}(?![0-9A-Za-z])")
_STEP_COUNT_CLAIM = re.compile(
    r"(?:총|전체|모두)\s*(\d+)\s*(?:개의?\s*)?단계|(\d+)\s*개의?\s*단계"
    r"|\b(\d+)\s+steps?\s+in\s+total\b|\b(?:has|have|of)\s+(\d+)\s+steps\b|\b(\d+)-step\b",
    re.I,
)
_CURRENT_STEP_CLAIM = re.compile(
    r"(?:현재|지금)(?:은|는)?\s*(\d+)\s*단계|현재\s*단계(?:는|가)?\s*(\d+)\s*단계"
    r"|\bcurrent(?:ly)?\s+(?:at\s+|on\s+)?step\s+(?:is\s+)?(\d+)\b"
    r"|\bcurrent\s+step\s+is\s+(?:step\s+)?(\d+)\b",
    re.I,
)
_REVISION_CLAIM = re.compile(
    r"(?:실행\s*버전|리비전|revision)\s*[:은는]?\s*([0-9A-Za-z][0-9A-Za-z._-]*)",
    re.I,
)
_TITLE_CLAIM = re.compile(r"(?:제목|title)\s*[:：]\s*([^\n]+)", re.I)


def _first_group(match: re.Match[str]) -> str:
    return next(group for group in match.groups() if group is not None)


def server_value_violations(text: str, values: ServerValues) -> tuple[str, ...]:
    """Server-owned values the answer states differently; empty when none.

    Protocol number (revision), hash, title, step count and current step are
    the server's strings (design §5-2): an answer that names one must name it
    exactly as the snapshot does, never a near copy.
    """

    violations: list[str] = []
    known_hashes = tuple(item.casefold() for item in values.hashes if item)
    for match in _HEX_RUN.finditer(text):
        if not any(match.group(0).casefold() in known for known in known_hashes):
            violations.append("hash_not_server_value")
            break
    for match in _STEP_COUNT_CLAIM.finditer(text):
        if int(_first_group(match)) != values.step_count:
            violations.append("step_count_not_server_value")
            break
    for match in _CURRENT_STEP_CLAIM.finditer(text):
        if values.current_step_label is None or _first_group(match) != values.current_step_label:
            violations.append("current_step_not_server_value")
            break
    for match in _REVISION_CLAIM.finditer(text):
        if match.group(1).rstrip(".") != values.revision_id:
            violations.append("revision_not_server_value")
            break
    for match in _TITLE_CLAIM.finditer(text):
        stated = match.group(1).strip().strip("\"'“”‘’「」『』")
        if not stated.startswith(values.title):
            violations.append("title_not_server_value")
            break
    return tuple(violations)
