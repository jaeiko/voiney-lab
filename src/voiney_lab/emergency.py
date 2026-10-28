"""Conservative deterministic recognition of immediate emergency utterances."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

KOREAN_EMERGENCY_RESPONSE = (
    "즉시 작업을 멈추고 위험 구역에서 벗어나세요. 현장 안전관리자 또는 기존 비상 연락 절차를 통해 "
    "즉시 도움을 요청하세요. 이 응답만을 근거로 작업을 재개하지 마세요."
)
ENGLISH_EMERGENCY_RESPONSE = (
    "Stop work immediately and move away from the hazard. Immediately contact the "
    "on-site safety manager or use the established emergency contact procedure. "
    "Do not resume work based on this response."
)


@dataclass(frozen=True)
class EmergencyMatch:
    language: str
    response: str


_TERMINAL_PUNCTUATION = re.compile(r"[\s.!?。？！]+$")
_REPEATED_WHITESPACE = re.compile(r"\s+")

_KOREAN_EXCLUSIONS = re.compile(
    r"(?:알려|설명|절차|샤워|경우|만약|발생하면|났다면|"
    r"과거|예전|어제|지난|작년|사례|훈련|가정)"
)
_ENGLISH_EXCLUSIONS = re.compile(
    r"\b(?:procedure|explain|shower|if|would|could|hypothetical|"
    r"yesterday|previously|historical|history|last\s+(?:week|month|year)|drill)\b"
)

_KO_HELP_FOLLOWUP = (
    r"(?:\s*[.!?。？！]+\s*(?:어떻게\s+해야\s+(?:해요|돼요)|무엇을\s+해야\s+하나요|"
    r"뭘\s+해야\s+해요|도와\s*주세요))?"
)
_EN_HELP_FOLLOWUP = r"(?:\s*[,.!?]+\s*(?:what\s+should\s+i\s+do|how\s+do\s+we\s+get\s+help))?"

_KOREAN_IMMEDIATE = (
    re.compile(r"^도와\s*줘$"),
    re.compile(r"^도와\s*주세요$"),
    re.compile(r"^(?:지금\s*)?(?:불|화재)(?:이|가)?\s*(?:났|발생했|나고\s*있|발생하고\s*있)(?:어|어요|습니다|다)?" + _KO_HELP_FOLLOWUP + r"$"),
    re.compile(r"^(?:지금\s*)?폭발(?:(?:이|가)?\s*(?:났|발생했|일어났|하고\s*있)|했)(?:어|어요|습니다|다)?" + _KO_HELP_FOLLOWUP + r"$"),
    re.compile(r"^(?:지금\s*)?(?:가스|화학물질|액체|용액)?\s*누출(?:이|가)?\s*(?:됐|발생했|되고\s*있|발생하고\s*있)(?:어|어요|습니다|다)?" + _KO_HELP_FOLLOWUP + r"$"),
    re.compile(r"^(?:사람이\s*)?(?:크게|심하게|심각하게)\s*다쳤(?:어|어요|습니다|다)?$"),
    re.compile(r"^(?:지금\s*)?(?:즉시|매우|너무)?\s*위험(?:한\s*상황)?(?:입니다|이에요|해요|합니다|하다|에\s*처해\s*있(?:어요|습니다)?)$"),
)

# Current incident statements, grouped by the hazard described by the speaker.
# These recognise words only; they never infer a chemical's properties or
# supply first aid. The response remains the existing fixed emergency text.
_KO_INCIDENT_END = r"(?:어|어요|요|습니다|다|네|네요|는데|는데요)?"
_KO_MATERIAL = r"(?:시약|화학물질|용액|액체|용매|산|염기|증기|가스)"
_KO_BODY = r"(?:눈|피부|얼굴|손|팔)"
_KOREAN_INCIDENTS = tuple(re.compile(r"^(?:지금\s*|갑자기\s*)?" + body + _KO_INCIDENT_END + r"$") for body in (
    # Eyes/skin: both word orders and omitted particles after an exposure.
    r"(?:[^.!?]{0,30})?(?:눈|피부|얼굴)(?:에|으로)\s*(?:[^.!?]{0,25})?(?:튀었|튀어|들어갔|묻었|닿았|들어가서\s*따가워)",
    rf"(?:[^.!?]{{0,20}})?(?:{_KO_MATERIAL}[^.!?]{{0,20}}{_KO_BODY}(?:에|으로)|{_KO_BODY}(?:에|으로)[^.!?]{{0,20}}{_KO_MATERIAL})[^.!?]{{0,15}}(?:튀었|튀어|들어갔|묻었|닿았)",
    # Inhalation, obstruction and acute dizziness.
    r"(?:[^.!?]{0,30})?(?:숨(?:을|이)?\s*(?:못\s*쉬겠|못\s*쉬어|막혀|막혔)|숨\s*쉬기\s*(?:힘들|어려워)|어지러워|어지럽고\s*숨\s*쉬기\s*힘들)",
    rf"(?:[^.!?]{{0,30}})?{_KO_MATERIAL}(?:을|를)?\s*(?:들이마셨|마셨|흡입했)",
    # Fire/smoke/sparks are stated events, not a mention of fire equipment.
    r"(?:[^.!?]{0,30})?(?:불(?:이)?\s*붙었|연기(?:가)?\s*(?:나|나고\s*있|났)|불꽃(?:이)?\s*(?:튀어|튀고\s*있))",
    # A spill needs an explicit large extent; a routine small spill is still
    # handled by the existing anomaly path.
    rf"(?=[^.!?]*{_KO_MATERIAL})(?=[^.!?]*(?:많이|대량|잔뜩|전부|큰\s*(?:병|용기)))[^.!?]{{0,65}}(?:쏟았|쏟아졌|흘렀)",
    # Cuts and significant/uncontrolled bleeding.
    r"(?:[^.!?]{0,30})?(?:손|손가락|팔)(?:을|이|에)?\s*(?:베었|베였)",
    r"(?:[^.!?]{0,20})?(?:피(?:가)?\s*멈추지\s*않아|피(?:가)?\s*많이\s*나|출혈(?:이)?\s*심해)",
    # Collapse and consciousness.
    r"(?:[^.!?]{0,30})?(?:쓰러졌|기절했|정신(?:을)?\s*잃었|의식(?:이)?\s*없|(?:사람|동료)(?:이|가)?\s*반응(?:이)?\s*없)",
    # Leak/abnormal smell: ordinary smell or equipment names are insufficient.
    r"(?:[^.!?]{0,20})?가스(?:가)?\s*(?:새고\s*있|새|누출됐)",
    r"(?:[^.!?]{0,20})?(?:이상한|독한|타는)\s*냄새(?:가)?\s*(?:갑자기\s*)?나",
    # Burns/electric shock/ingestion require a reported incident.
    r"(?:[^.!?]{0,30})?(?:화상(?:을)?\s*입었|데었|데였)",
    r"(?:[^.!?]{0,30})?(?:감전됐|감전당했|전기(?:가)?\s*통해서\s*손(?:이)?\s*떨려)",
    rf"(?:[^.!?]{{0,30}})?{_KO_MATERIAL}(?:을|를)?\s*(?:잘못\s*|실수로\s*)?(?:삼켰|먹었|마셨)",
    rf"(?:[^.!?]{{0,20}})?{_KO_MATERIAL}(?:이|가)?\s*입(?:에|으로)\s*들어갔",
))

_KO_INCIDENT_EXCLUSIONS = re.compile(
    r"(?:만약|경우|과거|예전|어제|지난|작년|사례|훈련|가정|"
    r"(?:튀|들어가|쏟|베|쓰러|감전|삼키|어지러|없으)[^\s.!?]{0,6}면|"
    r"아니|않았|않고|없었|예방|절차|설명|알려|[?？])"
)
_ENGLISH_IMMEDIATE = (
    re.compile(r"^emergency$"),
    re.compile(r"^help$"),
    re.compile(r"^help(?:\s*[,!?])?\s+(?:there(?:['’]s| is)\s+(?:a\s+)?fire|we have\s+(?:a\s+)?fire)(?:\s+right\s+now)?$"),
    re.compile(r"^(?:there(?:['’]s| is)\s+(?:a\s+)?fire|(?:a\s+)?fire\s+(?:is\s+)?burning)(?:\s+right\s+now)?" + _EN_HELP_FOLLOWUP + r"$"),
    re.compile(r"^(?:(?:an?\s+)?explosion\s+(?:just\s+happened|is\s+happening)|there\s+was\s+(?:an?\s+)?explosion\s+just\s+now)$"),
    re.compile(r"^(?:there(?:['’]s| is)\s+)?(?:an?\s+)?active\s+(?:gas\s+|chemical\s+)?leak(?:\s+(?:right\s+)?now)?" + _EN_HELP_FOLLOWUP + r"$"),
    re.compile(r"^(?:gas|chemical|liquid|solution|it)\s+is\s+leaking(?:\s+(?:right\s+)?now)?$"),
    re.compile(r"^(?:someone|a\s+person|i|we)\s+(?:is|am|are)\s+(?:seriously|badly|critically)\s+injured$"),
    re.compile(r"^(?:we|i|someone)\s+(?:are|am|is)\s+in\s+immediate\s+danger$"),
)


def normalize_emergency_text(text: str) -> str:
    """Normalize only representation, whitespace, English case, and terminal marks."""
    normalized = unicodedata.normalize("NFKC", text)
    normalized = _REPEATED_WHITESPACE.sub(" ", normalized).strip().casefold()
    return _TERMINAL_PUNCTUATION.sub("", normalized)


def recognize_emergency(text: str) -> EmergencyMatch | None:
    """Return a fixed-response language only for an explicit current emergency."""
    normalized = normalize_emergency_text(text)
    if not normalized:
        return None
    if re.search(r"[가-힣]", normalized):
        if not _KOREAN_EXCLUSIONS.search(normalized) and any(
            pattern.fullmatch(normalized) for pattern in _KOREAN_IMMEDIATE
        ):
            return EmergencyMatch("ko", KOREAN_EMERGENCY_RESPONSE)
        # A current incident followed by a help question remains an incident.
        # Exclusions apply to its own clause, so "...튀었어. 알려줘" is not
        # hidden by the later request. Questions/hypotheticals alone never match.
        for clause in re.split(r"[.!。！,;]+\s*", normalized):
            if _KO_INCIDENT_EXCLUSIONS.search(clause):
                continue
            if any(pattern.fullmatch(clause.strip()) for pattern in _KOREAN_INCIDENTS):
                return EmergencyMatch("ko", KOREAN_EMERGENCY_RESPONSE)
        return None
    if _ENGLISH_EXCLUSIONS.search(normalized):
        return None
    if any(pattern.fullmatch(normalized) for pattern in _ENGLISH_IMMEDIATE):
        return EmergencyMatch("en", ENGLISH_EMERGENCY_RESPONSE)
    return None
