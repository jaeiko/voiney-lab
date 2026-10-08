"""Server checks on a model-written answer, shared by every answering role.

A model may phrase an answer; it may not decide what the answer is allowed to
contain. Each check here reads the answer text (and, where it needs one, the
question or the server's own values) and says whether the answer may be used.
None of them rewrites anything: a failed check means the caller drops the
model's answer and falls back to the server's own reply. The one exception is
a safety instruction with no source (lane RT, decision 7): the sentence is
found here, and the router takes that sentence out rather than the answer.

The number and mutation-claim checks moved here from ``multi_brain``, which
keeps using them exactly as before. The outside-PDF, display-label and
server-value checks are new for the lane R answer structure
(``~/reports/lane_r_design.md`` §5) and nothing calls them yet.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable
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
    # Promising a hand-off (lane R3, decision 1 with D8): nothing is sent
    # by voice, so "보내 드릴게요" is false.
    r"|(?:전달|전송|보내)\s*(?:해\s*)?(?:드릴게요|드리겠습니다|줄게요|할게요|하겠습니다|드렸습니다|했습니다)"
    r"|\b(?:moved\s+(?:you\s+)?on|advanced|i(?:'|’)ve\s+(?:started|stopped|paused|resumed|ended))\b"
    r"|\b(?:timer\s+(?:is\s+)?(?:started|running\s+now))\b",
    re.I,
)


#: A question only the server asks (lane R3, decision 5): whether a step is
#: done, whether to end, record, move on, start, pause or resume. The server
#: opens these and takes the "네" that answers them; an answer that asks one
#: is heard as the server's question, and the next "네" would answer nothing.
SERVER_QUESTION = re.compile(
    r"(?:완료|마치|끝내|끝마치)\S*\s*(?:하지\s*)?(?:않으셨|않았|하셨|셨|했|됐|되었)\S*(?:나요|까요|습니까|어요|니)\s*\?"
    r"|(?:완료|마무리)\s*(?:하셨|했|되었|됐)(?:나요|어요|습니까|니)"
    r"|(?:종료|기록|시작|재개|일시\s*정지|저장|이동|진행)\s*(?:을|를)?\s*(?:할|하실|해\s*드릴|해도\s*될)까요"
    r"|(?:종료|기록|시작|재개|일시\s*정지|저장|이동|진행)\s*하시겠(?:어요|습니까|나요)"
    r"|넘어갈까요|넘어가시겠(?:어요|습니까)|(?:으로|로)\s*기록할까요"
    # The hand-off question ("…로 보고서를 전송할까요?"), which only the
    # server asks -- and, by decision 1 (D8), no longer by voice.
    r"|(?:전달|전송|보내)\s*(?:해\s*)?(?:드릴|할|줄)까요|(?:전달|전송)\s*하시겠(?:어요|습니까)"
    r"|\b(?:did|have)\s+you\s+(?:finish|finished|complete|completed|done)\b"
    r"|\bis\s+(?:the\s+|this\s+)?step\s+(?:\d+\s+)?(?:done|complete|finished)\s*\?"
    r"|\b(?:shall|should)\s+i\s+(?:end|stop|record|log|start|move|go|pause|resume|save)\b"
    r"|\bdo\s+you\s+want\s+(?:me\s+)?to\s+(?:end|stop|record|log|start|move\s+on|pause|resume)\b",
    re.I,
)


def asks_server_question(text: str) -> bool:
    """The answer asks a question only the server asks (lane R3, decision 5)."""

    return SERVER_QUESTION.search(text) is not None


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


def _in_protocol_text(term: str, protocol_text: str) -> bool:
    """``term`` is a word (or words) of the active protocol's text, not a part of one."""

    key = _term_key(term)
    if len(key) < 2 or not protocol_text:
        return False
    text = _term_key(protocol_text)
    if re.fullmatch(r"[0-9a-z][0-9a-z .\-+/]*", key):
        return re.search(rf"(?<![0-9a-z]){re.escape(key)}(?![0-9a-z])", text) is not None
    return key in text


def outside_pdf_question_allowed(question: str) -> bool:
    """Whether D4 lets a question be given an outside-PDF explanation at all:
    it asks what a word means or why something is done, and asks no
    quantity, method, safety or completion (lane R6, decision 6)."""

    return bool(
        (_MEANING_QUESTION.search(question) or _PURPOSE_QUESTION.search(question))
        and not any(pattern.search(question) for _name, pattern in _FORBIDDEN_QUESTION_DIMENSIONS)
    )


def outside_pdf_violations(
    answer: str,
    *,
    question: str,
    term: str | None,
    protocol_terms: Iterable[str],
    protocol_text: str = "",
) -> tuple[str, ...]:
    """Why an outside-PDF explanation may not be used; empty when it may.

    Decision D4 (2026-10-02): an explanation the PDF does not give is allowed
    for what a word of the active Protocol means and, like it, for why a
    reagent or piece of equipment is used (its role or purpose) -- at most
    120 characters, with no number and nothing about quantities, method,
    safety or completion, and shown with the same outside-PDF mark (which the
    server adds, not the model). "A word of the active Protocol" is one of
    its terms or (lane R3, decision 6) any word of ``protocol_text``, its
    steps, materials and warnings.
    """

    violations: list[str] = []
    terms = {_term_key(item) for item in protocol_terms if item and item.strip()}
    # The term list, or (lane R3, decision 6) a word of the active
    # protocol's own text: its steps, materials and warnings.
    if not term or (
        _term_key(term) not in terms and not _in_protocol_text(term, protocol_text)
    ):
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
    if "answer_states_safety" not in violations and any(
            permissive_hazard_topics(sentence) for sentence in _SENTENCE_END.split(body)):
        # Lane TS, decision 3: a permission about a hazard ("맨눈으로 봐도
        # 되는 장비예요") is safety content too.
        violations.append("answer_states_safety")
    if display_label_violations(body):
        violations.append("answer_has_display_label")
    return tuple(violations)


# --- Safety instructions with no source (lane RT, decision 7) --------------------

#: An instruction to the researcher, by its form: a request ("…하세요",
#: "…주십시오"), a must or a should ("…해야 합니다", "…는 것이 좋아요"), a
#: don't ("…하지 마세요"), a recommendation ("권장", "필요합니다"), or the
#: English imperative and modals.
_DIRECTIVE = re.compile(
    r"(?:세요|십시오|십시요|주세요|바랍니다)(?=[\s.!?,·~)]|$)"
    r"|(?:해야|하여야|어야|아야)\s*(?:만\s*)?(?:합니다|해요|해|돼요|됩니다|하며|하고)"
    r"|(?:는\s*것이|는\s*게)\s*(?:좋|안전|바람직)"
    r"|하지\s*마|마십시오|금지|권장|필요(?:합니다|해요|해)|필수"
    r"|\b(?:you\s+(?:should|must|need\s+to|have\s+to)|make\s+sure|be\s+sure\s+to"
    r"|do\s+not|don(?:'|’)t|never|please)\b"
    r"|^\s*(?:wear|follow|use|evacuate|ventilate|rinse|wash|flush|dispose|contact|call"
    r"|consult|avoid|keep|leave|clean|wipe|absorb|put\s+on)\b",
    re.I | re.M,
)
#: What a safety instruction is about: each topic with the words that name it
#: in an answer, and the words that would ground it in the protocol's text or
#: an approved safety document (English source, Korean readings). The topics
#: are those of decision 7 -- what to follow, wear, ventilate, evacuate, wash,
#: clean up, dispose of or keep away from, and whom to call.
#: English words are edged by letters, not by \b: "SDS와", "gloves를" -- a
#: Hangul particle is a word character, so \b does not hold before it.
SAFETY_INSTRUCTION_TOPICS: tuple[tuple[str, re.Pattern[str], re.Pattern[str]], ...] = (
    ("safety_data_sheet",
     re.compile(r"(?<![a-z])m?sds(?![a-z])|물질\s*안전\s*보건\s*자료|안전\s*보건\s*자료|safety\s+data\s+sheet", re.I),
     re.compile(r"(?<![a-z])m?sds(?![a-z])|safety\s+data\s+sheet|물질\s*안전", re.I)),
    ("spill_response",
     re.compile(r"유출\s*(?:대응|처리|키트|사고)|스필\s*키트|흡착\s*(?:패드|포|재)|"
                r"(?<![a-z])spill\s+(?:kit|response|procedure|control)", re.I),
     re.compile(r"(?<![a-z])spill|유출|흡착", re.I)),
    # "기관의 화재 대응 지침", "실험실 안전 규정", "안전관리자": a rule or a person
    # of the institution, a word or two after what it is about.
    ("safety_rules",
     re.compile(r"(?:안전|실험실|연구실|기관|시설|화재|비상|사고|응급)\S*\s*(?:\S+\s*){0,2}?"
                r"(?:지침|규정|수칙|매뉴얼|절차|요령|관리자|담당자)|"
                r"(?<![a-z])safety\s+(?:officer|manager|guidelines?|rules?|procedures?|protocols?)(?![a-z])|"
                r"(?<![a-z])(?:institutional|emergency)\s+(?:procedures?|guidelines?|rules?)(?![a-z])|"
                r"(?<![a-z])sops?(?![a-z])", re.I),
     re.compile(r"(?<![a-z])safety\s+(?:officer|manager|guidelines?|rules?|procedures?)(?![a-z])|(?<![a-z])sops?(?![a-z])|"
                r"(?:안전|실험실|연구실)\s*(?:지침|규정|수칙|관리자)", re.I)),
    ("gloves",
     re.compile(r"장갑|글러브|(?<![a-z])gloves?(?![a-z])", re.I),
     re.compile(r"(?<![a-z])gloves?(?![a-z])|장갑", re.I)),
    ("eye_protection",
     re.compile(r"보안경|고글|안면\s*보호|보호\s*안경|(?<![a-z])goggles?(?![a-z])|eye\s+protection|face\s+shield|"
                r"safety\s+glasses", re.I),
     re.compile(r"(?<![a-z])goggles?(?![a-z])|eye\s+protection|face\s+shield|safety\s+glasses|보안경|고글", re.I)),
    ("lab_coat",
     re.compile(r"실험복|실험\s*가운|(?<![a-z])lab\s+coats?(?![a-z])", re.I),
     re.compile(r"(?<![a-z])lab\s+coats?(?![a-z])|실험복|(?<![a-z])gowns?(?![a-z])", re.I)),
    ("protective_equipment",
     re.compile(r"보호구|보호\s*장비|보호\s*장구|(?<![a-z])ppe(?![a-z])|protective\s+(?:equipment|clothing|gear)", re.I),
     re.compile(r"(?<![a-z])ppe(?![a-z])|protective|보호구", re.I)),
    ("ventilation",
     re.compile(r"환기|후드|(?<![a-z])fume\s+hood|(?<![a-z])hood(?![a-z])|ventilat", re.I),
     re.compile(r"(?<![a-z])hood(?![a-z])|ventilat|환기|후드", re.I)),
    ("evacuation",
     re.compile(r"대피|(?<![a-z])evacuat", re.I),
     re.compile(r"(?<![a-z])evacuat|대피", re.I)),
    ("rinse_body",
     re.compile(r"(?:피부|눈|손|얼굴)\S*\s*(?:\S+\s*){0,3}?(?:씻|세척|헹구|헹궈|흐르는\s*물)|세안|"
                r"아이\s*워시|eye\s*wash|(?<![a-z])(?:rinse|flush)\s+(?:your\s+)?(?:eyes?|skin|hands?)(?![a-z])", re.I),
     re.compile(r"(?<![a-z])(?:rinse|flush|eye\s*wash)|세안|흐르는\s*물", re.I)),
    ("spill_cleanup",
     re.compile(r"(?:흘린|쏟은|엎지른|유출된|넘친)\S*\s*(?:\S+\s*){0,3}?(?:닦|흡수|치우|청소|제거)|"
                r"(?<![a-z])(?:wipe|absorb|clean)\s+(?:it\s+|the\s+spill\s+)?up(?![a-z])", re.I),
     re.compile(r"(?<![a-z])spill|(?<![a-z])wipe(?![a-z])|(?<![a-z])absorb|유출|흡수", re.I)),
    # Waste handling, not a step's own "discard the supernatant" ("상층액을
    # 폐기하세요" is the protocol's method, and stays as such).
    ("disposal",
     re.compile(r"폐액|폐기물|지정(?:된)?\s*(?:폐기\s*)?(?:용기|통)|(?<![a-z])hazardous\s+waste(?![a-z])|"
                r"(?<![a-z])waste\s+(?:container|bin|stream)(?![a-z])|(?<![a-z])dispos(?:e|al)\s+of(?![a-z])", re.I),
     re.compile(r"(?<![a-z])dispos|(?<![a-z])waste(?![a-z])|폐액|폐기물", re.I)),
    ("medical_help",
     re.compile(r"119|응급|병원|진료|의료진|의사(?:의|에게|와)|비상\s*(?:연락|벨|버튼|샤워)|긴급\s*연락|신고|"
                r"(?<![a-z])medical(?![a-z])|(?<![a-z])emergency(?![a-z])|"
                r"(?<![a-z])physician(?![a-z])|(?<![a-z])doctor(?![a-z])", re.I),
     re.compile(r"(?<![a-z])medical(?![a-z])|(?<![a-z])emergency(?![a-z])|(?<![a-z])physician(?![a-z])|응급|의료", re.I)),
    # "소화" alone is also digestion ("소화물", in-gel's digest).
    ("fire",
     re.compile(r"화재|소화기|불이\s*(?:나|났|붙)|불을\s*끄|(?<![a-z])fire(?![a-z])|(?<![a-z])extinguish", re.I),
     re.compile(r"(?<![a-z])fire(?![a-z])|extinguish|소화기|화재", re.I)),
    ("exposure",
     re.compile(r"(?:피부|눈|호흡|흡입|접촉|증기|흄)\S*\s*(?:\S+\s*){0,2}?(?:피하|피해|막|조심|주의)|"
                r"(?<![a-z])avoid\s+(?:skin|eye|contact|inhal|breathing)", re.I),
     # Contact and exposure, not any "avoid" or "skin" ("avoid keratin
     # contamination", "skin cells" in in-gel's dust warning).
     re.compile(r"(?<![a-z])avoid\s+(?:skin|eye|contact|inhal|breath|exposure)|(?<![a-z])inhal|"
                r"(?:skin|eye)\s+contact|(?:on|with)\s+(?:the\s+)?(?:skin|eyes)(?![a-z])|irritat|"
                r"피부\s*(?:접촉|에\s*(?:닿|묻))|흡입", re.I)),
)
#: The sentences of a text: split after a sentence end or at a line break.
_SENTENCE_END = re.compile(r"(?<=[.!?。！？])\s+|\n+")


def safety_instruction_topics(sentence: str) -> tuple[str, ...]:
    """The safety topics a sentence instructs about; empty when it is no instruction."""

    if not _DIRECTIVE.search(sentence):
        return ()
    return tuple(name for name, said, _ground in SAFETY_INSTRUCTION_TOPICS if said.search(sentence))


# --- Permissive safety sentences with no source (lane TS, decision 3) ------------

#: A sentence that permits rather than instructs: "~없이 해도 돼요", "~안 해도
#: 돼요", "~해도 괜찮아요", "맨손으로", "맨눈으로", "필요 없어요", "독성이 없어요",
#: and their English kin ("without gloves", "not necessary", "it's fine to",
#: "with the naked eye", "non-toxic"). "맨손으로 만지지 마세요" forbids, so a
#: 맨손·맨눈 followed by a prohibition in the same sentence permits nothing.
_PERMISSIVE = re.compile(
    r"[가-힣]도\s*(?:돼|되|됩|된다|괜찮|무방|상관\s*없|문제\s*(?:없|안\s*(?:돼|되|됩)))"
    r"|맨\s*(?:손|눈|살)\s*으?로(?![^.!?\n]*(?:마세요|마십시오|말아|말고|않|금지|안\s*(?:돼|되|됩)))"
    r"|없이\s+(?:\S+\s+){0,2}?\S*\s*수\s*있"
    r"|필요\s*(?:없|하지\s*않|는\s*없|치\s*않)"
    r"|(?:독성|위험|유해성?)\s*(?:이|가|은|는|도)?\s*(?:없|적|낮)|무해|무독|해롭지\s*않|위험하지\s*않"
    r"|안전(?:해요|합니다|하다|해서)"
    r"|\bwithout\s+(?:\w+\s+){0,2}?(?:gloves?|goggles?|(?:fume\s+)?hoods?|protection|ppe|masks?|ventilation)\b"
    r"|\bno\s+need\s+(?:for|to)\b|\b(?:not|n['’]t)\s+(?:required|necessary|needed)\b"
    r"|\bunnecessary\b|\boptional\b"
    r"|\b(?:it['’]s|it\s+is|is|are)\s+(?:fine|ok(?:ay)?|safe|harmless)\b|\bsafe\s+to\b"
    r"|\bbare\s+hands?\b|\bnaked\s+eyes?\b|\bnon[-\s]?(?:toxic|hazardous)\b|\bharmless\b"
    r"|\b(?:do|does)\s+not\s+need\b|\b(?:don|doesn)['’]t\s+need\b|\bneed\s+not\b"
    r"|\bcan\s+(?:skip|omit)\b|\bon\s+the\s+open\s+bench\b"
    r"|\b(?:you\s+)?(?:can|may)\s+(?:\w+\s+){0,3}?without\b",
    re.I,
)
#: What turns a permission into a question about one: "다뤄도 되는지에 대한
#: 정보가 없어요", "봐도 안전한지는 확인할 수 없어요", "whether it is safe to".
#: Measured on the lane TS live run (2026-10-08): four such sentences were
#: taken out before this, each saying only that the source does not tell.
_ASKS_WHETHER_KO = re.compile(r"(?:는지|은지|한지|ㄴ지|지는|지를|지에|여부|냐|나요|는가|니까|까요)")
_ASKS_WHETHER_EN = re.compile(r"\b(?:whether|if)\b[^.!?]{0,40}$", re.I)


def _permits(sentence: str) -> bool:
    """Whether a sentence permits something, not merely asks whether it may be done."""

    for match in _PERMISSIVE.finditer(sentence):
        rest = sentence[match.end():]
        word = re.match(r"\S*(?:\s+\S+)?", rest)
        if word is not None and _ASKS_WHETHER_KO.search(word.group()):
            continue
        if match.group().startswith("맨") and re.search(r"[가-힣]지는|는지|한지|은지|여부", rest):
            continue
        if _ASKS_WHETHER_EN.search(sentence[:match.start()]):
            continue
        return True
    return False


def _either(korean: str, english: str) -> re.Pattern[str]:
    """Korean words anywhere, English ones edged by letters ("gloves를")."""

    return re.compile(rf"{korean}|(?<![a-z])(?:{english})(?![a-z])", re.I)


_PROTECTIVE = _either(
    r"장갑|글러브|보안경|고글|안면\s*보호|보호\s*안경|실험복|가운|보호구|보호\s*장비|마스크|"
    r"맨\s*손|맨\s*눈|맨\s*살",
    r"gloves?|goggles?|eye\s+protection|face\s+shields?|safety\s+glasses|lab\s+coats?|ppe|"
    r"protective|protection|bare\s+hands?|naked\s+eyes?|masks?",
)
_VENTILATION = _either(r"환기|후드|통풍", r"fume\s*hoods?|hoods?|ventilat\w*")
_EXPOSURE = _either(
    r"흡입|들이마|들이쉬|냄새를?\s*맡|증기|피부에?\s*(?:닿|묻)|눈에\s*(?:들어|튀)",
    r"inhal\w*|breath\w*|vapou?rs?|fumes?|skin\s+contact|on\s+(?:the|your)\s+skin",
)
_UV_RADIATION = _either(
    r"자외선|방사선|방사성|트랜스\s*일루미네이터|레이저",
    r"uv|ultraviolet|transillumin\w*|radiation|radioactiv\w*|lasers?",
)
_BIOHAZARD = _either(
    r"생물\s*(?:학적\s*)?(?:위해|안전)|병원(?:체|균|성)|감염",
    r"biohazard\w*|biosafety|pathogen\w*|infectious|bsl[-\s]?\d",
)
_SHARPS = _either(
    r"날카|바늘|주사기|메스|칼날|면도날|깨진\s*유리",
    r"sharps?|needles?|syringes?|scalpels?|razors?|blades?|broken\s+glass",
)
_CHEMICAL_KO = (
    r"시약|화학|독성|유해|부식|인화|발암|산성|염기성|강산|강염기|용매|휘발|페놀|클로로포름|"
    r"아크릴아마이드|포름알데히드|에티디움"
)
_CHEMICAL_EN = (
    r"chemicals?|reagents?|toxic|hazardous|corrosive|flammable|carcinogen\w*|acids?|solvents?|"
    r"volatile|phenol|chloroform|acrylamide|formaldehyde|ethidium|mercaptoethanol|trizol"
)
_CHEMICAL = _either(_CHEMICAL_KO, _CHEMICAL_EN)
_HANDLING = _either(
    r"만지|만져|다루|다뤄|취급|손으로|손에|묻|접촉|마시|마셔|맛|붓|부어|따르|따라|흘",
    r"touch\w*|handl\w*|contact|pour\w*|spill\w*|pipett\w*\s+by\s+mouth",
)


def _names(pattern: re.Pattern[str]) -> Callable[[str], bool]:
    return lambda text: pattern.search(text) is not None


#: The hazard topics of decision 3: each with what names it in an answer and
#: what names it in a grounding sentence. Handling a chemical is named by a
#: chemical and a word of handling together ("시약은 실온에 둬도 돼요"
#: handles nothing); a permission about it is grounded by a permissive
#: sentence naming a chemical, a buffer or a solution.
PERMISSIVE_HAZARD_TOPICS: tuple[tuple[str, Callable[[str], bool], re.Pattern[str]], ...] = (
    ("protective_equipment", _names(_PROTECTIVE), _PROTECTIVE),
    ("ventilation", _names(_VENTILATION), _VENTILATION),
    ("exposure", _names(_EXPOSURE), _EXPOSURE),
    ("uv_radiation", _names(_UV_RADIATION), _UV_RADIATION),
    ("biohazard", _names(_BIOHAZARD), _BIOHAZARD),
    ("sharps", _names(_SHARPS), _SHARPS),
    ("chemical_handling",
     lambda text: _CHEMICAL.search(text) is not None and _HANDLING.search(text) is not None,
     _either(_CHEMICAL_KO + r"|버퍼|용액", _CHEMICAL_EN + r"|buffers?|solutions?")),
)


def permissive_hazard_topics(sentence: str) -> tuple[str, ...]:
    """The hazard topics a sentence permits something about; empty when it permits nothing."""

    if not _permits(sentence):
        return ()
    return tuple(name for name, said, _ground in PERMISSIVE_HAZARD_TOPICS if said(sentence))


def _permitted_topics(grounding: str) -> set[str]:
    """The topics a sentence of the grounding itself permits something about."""

    permitted: set[str] = set()
    for sentence in _SENTENCE_END.split(grounding):
        if _permits(sentence):
            permitted.update(
                name for name, _said, ground in PERMISSIVE_HAZARD_TOPICS if ground.search(sentence))
    return permitted


def ungrounded_safety_instructions(text: str, grounding: str) -> tuple[str, ...]:
    """The sentences of ``text`` that give a safety instruction or permission with no source.

    Lane RT, decision 7. A sentence counts when its form is an instruction
    and it names a safety topic (what to follow, wear, ventilate, evacuate,
    wash, clean up or dispose of, whom to call). It stands only when every
    topic it names is named in ``grounding`` -- the protocol's own text with
    its reviewed readings, and the approved safety documents; otherwise it
    is returned, to be taken out. Read per topic, over the whole of the
    grounding: an instruction the source gives for another step still stands.

    Lane TS, decision 3: a sentence that permits something about a hazard
    topic (``permissive_hazard_topics``: "장갑 없이 만져도 돼요", "UV를 맨눈으로
    봐도 돼요") counts too. Naming the topic is no ground for it -- "wear
    gloves" permits nothing -- so it stands only when, for every topic it
    names, a sentence of ``grounding`` itself permits something about that
    topic ("Gloves are not required", "보안경은 필요하지 않습니다").
    """

    grounded = {
        name for name, _said, ground in SAFETY_INSTRUCTION_TOPICS if ground.search(grounding)
    }
    permitted: set[str] | None = None
    flagged: list[str] = []
    for sentence in _SENTENCE_END.split(text):
        topics = safety_instruction_topics(sentence)
        if topics and not set(topics) <= grounded:
            flagged.append(sentence.strip())
            continue
        permits = permissive_hazard_topics(sentence)
        if permits:
            if permitted is None:
                permitted = _permitted_topics(grounding)
            if not set(permits) <= permitted:
                flagged.append(sentence.strip())
    return tuple(flagged)


def without_sentences(text: str, sentences: Iterable[str]) -> str:
    """``text`` with the given sentences taken out, its lines kept."""

    drop = {item.strip() for item in sentences if item.strip()}
    lines: list[str] = []
    for line in text.splitlines():
        kept = [part for part in re.split(r"(?<=[.!?。！？])\s+", line) if part.strip() not in drop]
        if kept or not line.strip():
            lines.append(" ".join(kept))
    return "\n".join(lines).strip()


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
#: "프로토콜 번호는 25단계" (lane R3, decision 3): the protocol's number or id
#: said as anything with a digit in it that is not the server's revision.
_PROTOCOL_NUMBER_CLAIM = re.compile(
    r"(?:프로토콜|실험|절차)\s*(?:의\s*)?(?:번호|아이디|ID|버전)\s*(?:은|는|이|가|:)?\s*(\S+)"
    r"|\bprotocol\s+(?:number|id|version)\s+(?:is\s+|:\s*)?(\S+)",
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
    for match in _PROTOCOL_NUMBER_CLAIM.finditer(text):
        stated = _first_group(match).strip(".,;:!?\"'“”‘’")
        if re.search(r"\d", stated) and values.revision_id not in stated:
            violations.append("protocol_number_not_server_value")
            break
    for match in _TITLE_CLAIM.finditer(text):
        stated = match.group(1).strip().strip("\"'“”‘’「」『』")
        if not stated.startswith(values.title):
            violations.append("title_not_server_value")
            break
    return tuple(violations)
