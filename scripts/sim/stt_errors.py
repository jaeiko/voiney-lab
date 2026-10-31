"""Transcription errors the live rows showed, generalised, for every public protocol (lane SAFE2).

The 2026-10-31 live rows (development split, Chirp 3, centrifuge 10 dB) bent
a number and its unit, cut the end of a sentence, wrote a command word as a
sound-alike and lost particles and polite endings. Each shape is applied
here as a rule on the case's own words -- no protocol's words, values or
sentences are written into the rules -- so the same garbles reach the ten
protocols of four fields alike. A variant keeps its case's family, so the
family's expected actions still read it; a clarification is a right answer
to a garble; and nothing outside the original case's allowed state, plus
the confirmation a question opens, may change. The runner compares any
value applied or recorded with the words that were *spoken* (the original
case), never with the garble.
"""
from __future__ import annotations

import re
from dataclasses import replace

from scripts.sim.corpus import Case
from scripts.sim.measurements import source_values

ERROR_KINDS = ('number_unit', 'tail_cut', 'command_sound', 'particle_drop')
#: Families whose words are not spoken Korean commands or records.
SKIPPED_FAMILIES = frozenset({'noise', 'emergency'})
#: The confirmation a garble may open instead of acting.
CLARIFICATIONS = ('clarify_completion', 'clarify_parameter', 'clarify_reference')

_INITIALS = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
_MEDIALS = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
_FINALS = ("", *"ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ")
#: A consonant heard as its aspirated or plain pair, a vowel as its neighbour.
_INITIAL_SWAPS = {'ㄱ': 'ㅋ', 'ㅋ': 'ㄱ', 'ㄷ': 'ㅌ', 'ㅌ': 'ㄷ', 'ㅂ': 'ㅍ', 'ㅍ': 'ㅂ', 'ㅈ': 'ㅊ', 'ㅊ': 'ㅈ', 'ㅅ': 'ㅆ', 'ㅆ': 'ㅅ'}
_MEDIAL_SWAPS = {'ㅐ': 'ㅔ', 'ㅔ': 'ㅐ', 'ㅗ': 'ㅓ', 'ㅓ': 'ㅗ', 'ㅜ': 'ㅡ', 'ㅡ': 'ㅜ', 'ㅏ': 'ㅓ', 'ㅣ': 'ㅢ', 'ㅕ': 'ㅛ', 'ㅛ': 'ㅕ'}
#: Units as the recognizer has written them in Korean, or near them.
_UNIT_HEARD = {
    'mL': '미리', 'ml': '미리', 'µL': '마이크로', 'uL': '유엘', 'L': '엘', 'g': '지', 'mg': '미리그램',
    'µg': '마이크로그램', 'ug': '마이크로그램', 'M': '엠', 'mM': '밀리 엠', 'µM': '마이크로 엠', 'uM': '마이크로 엠',
    'cm': '센치', 'mm': '미리', 'nm': '나노', '°C': '도씨', '℃': '도씨', 'min': '민', 'minutes': '분',
    'minute': '분', 'h': '시간', 'hours': '시간', 's': '초', 'rpm': '알피엠', '%': '프로', 'x g': '지',
}
_DIGIT_WORDS = "영일이삼사오육칠팔구"
_PARTICLE = re.compile(r"^(?P<stem>[가-힣]{2,}?)(?P<particle>으로|로|를|을|이|가|은|는|에|도)$")
_POLITE_END = re.compile(r"^(?:줘|주세요|요|해줘|해\s*줘|줄래)$")


def _syllable(initial: str, medial: str, final: str) -> str:
    return chr(0xAC00 + (_INITIALS.index(initial) * 21 + _MEDIALS.index(medial)) * 28 + _FINALS.index(final))


def _split(char: str) -> tuple[str, str, str] | None:
    code = ord(char) - 0xAC00
    if not 0 <= code < 11172:
        return None
    return _INITIALS[code // 588], _MEDIALS[(code % 588) // 28], _FINALS[code % 28]


def sound_alike(word: str) -> str | None:
    """One letter of the word heard as another: "타이머" -> "다이머", "재개" -> "제개"."""

    letters = [_split(char) for char in word]
    if any(item is None for item in letters) or len(letters) < 2:
        return None
    for index, (initial, medial, final) in enumerate(letters):
        if initial in _INITIAL_SWAPS:
            return word[:index] + _syllable(_INITIAL_SWAPS[initial], medial, final) + word[index + 1:]
    for index, (initial, medial, final) in enumerate(letters):
        if medial in _MEDIAL_SWAPS:
            return word[:index] + _syllable(initial, _MEDIAL_SWAPS[medial], final) + word[index + 1:]
    for index, (initial, medial, final) in enumerate(letters):
        if final:
            return word[:index] + _syllable(initial, medial, "") + word[index + 1:]
    return None


def korean_number(number: str) -> str | None:
    """A number as digits read one by one ("100" -> "일 영 영", "1.5" -> "일 점 오"), as a recognizer may write it."""

    digits = number.replace(',', '.').replace('−', '-').lstrip('-')
    if not digits or not re.fullmatch(r"\d+(?:\.\d+)?", digits):
        return None
    return " ".join("점" if char == "." else _DIGIT_WORDS[int(char)] for char in digits)


def bent_number(number: str) -> str | None:
    """The number with two digits swapped, or a one-digit number ten times over."""

    digits = number.replace(',', '.').replace('−', '-')
    core = digits.lstrip('-')
    positions = [index for index, char in enumerate(core) if char.isdigit()]
    if len(positions) >= 2:
        first, second = positions[0], positions[1]
        if core[first] != core[second]:
            swapped = list(core)
            swapped[first], swapped[second] = swapped[second], swapped[first]
            bent = "".join(swapped).lstrip('0') or '0'
            return ('-' if digits.startswith('-') else '') + bent
        return ('-' if digits.startswith('-') else '') + core + '0'
    if len(positions) == 1:
        return ('-' if digits.startswith('-') else '') + core + '0'
    return None


def number_unit_variants(text: str) -> list[tuple[str, str]]:
    """The first quantity's number bent, its unit as heard, and the quantity as digit words without its unit."""

    values = [(number, unit) for number, unit in source_values(text) if unit]
    if not values:
        return []
    number, unit = values[0]
    quantity = re.compile(re.escape(number) + r"\s*" + re.escape(unit))
    found = quantity.search(text)
    if found is None:
        return []
    variants: list[tuple[str, str]] = []
    bent = bent_number(number)
    if bent is not None:
        variants.append(('number_unit', text[:found.start()] + f"{bent} {unit}" + text[found.end():]))
    heard = _UNIT_HEARD.get(unit)
    if heard is not None:
        variants.append(('number_unit', text[:found.start()] + f"{number} {heard}" + text[found.end():]))
    words = korean_number(number)
    if words is not None:
        variants.append(('number_unit', text[:found.start()] + words + text[found.end():]))
    return variants


def tail_cut_variants(text: str) -> list[tuple[str, str]]:
    """The last syllable gone, and the last word gone."""

    stripped = text.rstrip(" .?!。！？")
    tokens = stripped.split()
    variants: list[tuple[str, str]] = []
    if tokens and len(tokens[-1]) >= 2 and re.search(r"[가-힣]$", tokens[-1]):
        variants.append(('tail_cut', " ".join(tokens[:-1] + [tokens[-1][:-1]])))
    if len(tokens) >= 2:
        variants.append(('tail_cut', " ".join(tokens[:-1])))
    return variants


def command_sound_variants(text: str) -> list[tuple[str, str]]:
    """The first and the last Korean word of two syllables or more heard one letter off."""

    tokens = text.split()
    eligible = [index for index, token in enumerate(tokens)
                if re.fullmatch(r"[가-힣]{2,}", token.rstrip(",.:：?!"))]
    variants: list[tuple[str, str]] = []
    seen: set[str] = set()
    for index in (eligible[:1] + eligible[-1:]):
        token = tokens[index]
        bare = token.rstrip(",.:：?!")
        alike = sound_alike(bare)
        if alike is None or alike in seen:
            continue
        seen.add(alike)
        changed = list(tokens)
        changed[index] = alike + token[len(bare):]
        variants.append(('command_sound', " ".join(changed)))
    return variants


def particle_drop_variants(text: str) -> list[tuple[str, str]]:
    """Particles and the polite ending dropped, as a recognizer drops short syllables."""

    tokens = text.split()
    if not tokens:
        return []
    changed = []
    dropped = False
    for token in tokens:
        bare = token.rstrip(",.:：?!")
        tail = token[len(bare):]
        if _POLITE_END.fullmatch(bare):
            dropped = True
            continue
        match = _PARTICLE.fullmatch(bare)
        if match is not None:
            dropped = True
            changed.append(match.group('stem') + tail)
            continue
        changed.append(token)
    if not dropped or not changed:
        return []
    return [('particle_drop', " ".join(changed))]


def _words(text: str) -> str:
    return " ".join(text.casefold().replace("?", " ").replace(".", " ").split())


def variants(case: Case, *, other_texts: frozenset[str] = frozenset()) -> tuple[Case, ...]:
    """The garbled copies of one case, each a case of the same family with the garble's name in its code.

    A garble whose words are another case's words ("다음 뭐야?" cut to
    "다음") is left out: the recognizer then wrote a different, complete
    command, which nothing reading words alone could tell from one spoken.
    ``other_texts`` holds those words (normalised by ``_words``).
    """

    if case.family in SKIPPED_FAMILIES or not case.text.strip():
        return ()
    produced: list[tuple[str, str]] = []
    produced += number_unit_variants(case.text)
    produced += tail_cut_variants(case.text)
    produced += command_sound_variants(case.text)
    produced += particle_drop_variants(case.text)
    result = []
    seen = {case.text}
    counts: dict[str, int] = {}
    from scripts.sim.metrics import EXPECTED_ACTIONS
    actions = tuple(dict.fromkeys(
        (*(case.actions or EXPECTED_ACTIONS.get(case.family, ())), *CLARIFICATIONS)))
    for kind, text in produced:
        text = " ".join(text.split())
        if not text or text in seen or _words(text) in other_texts:
            continue
        seen.add(text)
        counts[kind] = counts.get(kind, 0) + 1
        result.append(replace(
            case,
            code=f"{case.code}~{kind}.{counts[kind] - 1}",
            text=text,
            actions=actions,
            intents=(),
            allowed=tuple(dict.fromkeys((*case.allowed, 'confirmation'))),
            must_change=False,
        ))
    return tuple(result)


def case_texts(cases) -> frozenset[str]:
    """The spoken words of every case, for ``variants(other_texts=...)``."""

    return frozenset(_words(c.text) for c in cases if c.text.strip())


def error_kind(case_code: str) -> str | None:
    """The garble's name in a variant's code, or None for an original case."""

    match = re.search(r"~([a-z_]+)\.\d+$", case_code)
    return match.group(1) if match else None


def original_code(case_code: str) -> str:
    return re.sub(r"~[a-z_]+\.\d+$", "", case_code)
