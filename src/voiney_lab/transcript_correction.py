"""Transcript correction between speech-to-text and understanding (lane STT1).

One deterministic stage runs on the recognizer's text after the language
admission and before arbitration and the state machine:

* a word or phrase the recognizer wrote twice in a row ("다음. 다음.",
  "침전 생겼어 침전 생겼어") is read once -- never a phrase holding a
  digit or a Korean count word, so no quantity can move;
* a word that sounds like a term of the active protocol's own text becomes
  that term as the protocol spells it, through the one vocabulary device
  (``ProtocolVocabulary.hear``, lane GEN1): a Korean sound of a term
  ("트립씬" -> trypsin), a Latin word one letter off ("trypsun"), letters
  said apart ("에이 엠 빅" -> AMBIC), or a lab word's Korean form one sound
  off. Tokens holding digits, units beside numbers, the workflow's command
  words and the hearing stop words are never rewritten.

The corrected text is for understanding and the screen's aid only. The
spoken text stays what the recognizer gave: the STT diagnostics and the
``transcript`` event keep it, and ``records_as_heard`` puts the heard words
back into what a turn's plan writes to the experiment record (AGENTS rules
1 and 9). Every change leaves a code (``codes``) for the metrics; no text.

No protocol-specific table lives here: the terms come from the uploaded
source's analysis alone, and with no protocol only repeats are collapsed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any

from voiney_lab.curated_protocol import (
    _HANGUL_PARTICLE,
    _HEARING_STOP_WORDS,
    _LATIN_WITH_PARTICLE,
    ProtocolVocabulary,
    _hangul_word,
    _spoken_token,
)

#: A word or phrase written twice in a row was read once.
REPEAT_COLLAPSED = "repeat_collapsed"
#: A Korean sound of one of the protocol's terms became the term.
TERM_SOUND = "term_sound"
#: A Latin word one letter off one of the protocol's terms became the term.
TERM_SPELLING = "term_spelling"
#: Letters said apart became the abbreviation the protocol writes.
TERM_LETTERS = "term_letters"
#: A lab word's Korean form one sound off became the form.
LEXICON_FORM = "lexicon_form"
#: A change would have moved a number or unit; nothing was changed.
NUMBERS_GUARD = "numbers_guard"
CODES = (REPEAT_COLLAPSED, TERM_SOUND, TERM_SPELLING, TERM_LETTERS, LEXICON_FORM, NUMBERS_GUARD)

#: Words a correction never rewrites, whatever they sound like: the fixed
#: workflow commands and answers (lane STT5's hint list and the front rules'
#: words), beside the vocabulary's own hearing stop words. Generic to every
#: protocol; no document's words are here.
PROTECTED_WORDS = frozenset({
    "다음", "네", "예", "응", "아니", "아니요", "아니야", "완료", "시작", "종료", "재개",
    "일시정지", "정지", "멈춰", "취소", "이전", "타이머", "기록", "메모", "처음부터",
    "계속", "잠깐", "잠시", "그만", "정정", "특이사항", "화면", "크게", "다시",
}) | _HEARING_STOP_WORDS

#: Korean count words: a repeat of one is a count ("하나 하나"), never a stutter.
_COUNT_WORDS = frozenset({
    "하나", "둘", "셋", "넷", "다섯", "여섯", "일곱", "여덟", "아홉", "열", "스물", "서른",
    "한", "두", "세", "영", "일", "이", "삼", "사", "오", "육", "칠", "팔", "구", "십", "백", "천", "만",
})
#: The longest phrase a repeat may span, in words.
MAX_REPEAT_WORDS = 5
_TRAILING_PUNCT = re.compile(r"[.,!?~…。，！？;:]+$")
_DIGIT = re.compile(r"\d")
_HANGUL = re.compile(r"[가-힣]")
_LATIN_START = re.compile(r"[a-z0-9]")


@dataclass(frozen=True)
class TranscriptChange:
    """One change: the words as heard, what they became, and its code."""

    heard: str
    replacement: str
    code: str
    #: What was written into the text: the replacement with the heard
    #: word's particle and punctuation kept ("trypsin을"); empty = replacement.
    written: str = ""


@dataclass(frozen=True)
class TranscriptCorrection:
    """The spoken text, the corrected text and every change between them."""

    spoken: str
    text: str
    changes: tuple[TranscriptChange, ...] = ()

    @property
    def changed(self) -> bool:
        return self.text != self.spoken

    @property
    def codes(self) -> tuple[str, ...]:
        """Each change's code once, in order -- the metrics' content-free trace."""

        return tuple(dict.fromkeys(change.code for change in self.changes))

    @property
    def pairs(self) -> tuple[tuple[str, str], ...]:
        """(heard, replacement) for the screen's "인식된 음성 · 문맥상 해석" aid."""

        return tuple(dict.fromkeys(
            (change.heard, change.replacement)
            for change in self.changes if change.code != NUMBERS_GUARD
        ))

    def as_heard(self, text: str | None) -> str | None:
        """``text`` with the corrected words put back as they were heard.

        For what a turn writes to the experiment record: the record keeps
        the recognizer's words, not the corrected ones. Each replacement is
        matched as whole words (a glued particle or punctuation stays).
        """

        if not text or not self.changes:
            return text
        restored = text
        for change in reversed(self.changes):
            written = change.written or change.replacement
            if change.code == NUMBERS_GUARD or not written:
                continue
            pattern = re.compile(r"(?<!\S)" + re.escape(written) + r"(?!\S)", re.IGNORECASE)
            restored = pattern.sub(lambda _match: change.heard, restored, count=1)
        return restored


def _key_token(token: str) -> str:
    """The token as the vocabulary hears it: casefolded, trailing punctuation off."""

    return _TRAILING_PUNCT.sub("", token).casefold()


def _heard_form(key_token: str) -> str:
    """The token as ``ProtocolVocabulary.hear`` writes it back in a correction."""

    return _spoken_token(key_token) if _LATIN_START.match(key_token) else key_token


def _suffix(token: str, key_token: str) -> tuple[str, str]:
    """A token's glued Korean particle and trailing punctuation, kept on a replacement."""

    punctuation = token[len(_TRAILING_PUNCT.sub("", token)):]
    particle = ""
    stem = _hangul_word(key_token)
    if stem is not None and key_token.startswith(stem) and len(key_token) > len(stem):
        particle = key_token[len(stem):]
    else:
        latin = _LATIN_WITH_PARTICLE.match(key_token)
        if latin is not None and _HANGUL_PARTICLE.fullmatch(latin.group(2)):
            particle = latin.group(2)
    return particle, punctuation


def _digit_tokens(text: str) -> list[str]:
    return sorted(token for token in text.split() if _DIGIT.search(token))


def _collapse_repeats(tokens: list[str], changes: list[TranscriptChange]) -> list[str]:
    """Drop the second copy of a word or phrase written twice in a row."""

    keys = [_key_token(token) for token in tokens]
    index = 0
    while index < len(tokens):
        collapsed = False
        longest = min(MAX_REPEAT_WORDS, (len(tokens) - index) // 2)
        for size in range(longest, 0, -1):
            first = keys[index:index + size]
            second = keys[index + size:index + 2 * size]
            if first != second or not all(first):
                continue
            if any(_DIGIT.search(key) for key in first):
                continue
            if size == 1:
                word = _hangul_word(first[0]) or first[0]
                if word in _COUNT_WORDS:
                    continue
                # A command word said twice inside a sentence may be the
                # phrase itself ("다음 다음 단계"): read once only when a
                # sentence end separates the copies ("다음. 다음.") or the
                # two words are the whole utterance ("다음 다음").
                if word in PROTECTED_WORDS and not (
                    _TRAILING_PUNCT.search(tokens[index]) or len(tokens) == 2
                ):
                    continue
            heard = " ".join(tokens[index:index + 2 * size])
            kept = tokens[index:index + size]
            dropped_last = tokens[index + 2 * size - 1]
            if not _TRAILING_PUNCT.search(kept[-1]) and _TRAILING_PUNCT.search(dropped_last):
                kept[-1] = kept[-1] + dropped_last[len(_TRAILING_PUNCT.sub("", dropped_last)):]
            del tokens[index + size:index + 2 * size]
            del keys[index + size:index + 2 * size]
            tokens[index:index + size] = kept
            changes.append(TranscriptChange(heard=heard, replacement=" ".join(kept), code=REPEAT_COLLAPSED))
            collapsed = True
            break
        if not collapsed:
            index += 1
    return tokens


def _code(heard: str, replacement: str) -> str:
    if _HANGUL.search(replacement):
        return LEXICON_FORM
    parts = heard.split()
    if all(_HANGUL.fullmatch(part[0]) is not None and not _LATIN_START.match(part) for part in parts) and len(parts) == 1:
        return TERM_SOUND
    if len(parts) > 1 and all(len(part) <= 3 for part in parts):
        return TERM_LETTERS
    if all(_HANGUL.search(part) for part in parts):
        return TERM_SOUND
    return TERM_SPELLING


def _hear_terms(tokens: list[str], vocabulary: ProtocolVocabulary, changes: list[TranscriptChange]) -> list[str]:
    """Rewrite the tokens the vocabulary heard as one of the protocol's terms."""

    keys = [_key_token(token) for token in tokens]
    _key, _spans, corrections = vocabulary.hear(" ".join(keys))
    if not corrections:
        return tokens
    taken: set[int] = set()
    for heard, replacement in corrections:
        heard_tokens = heard.split()
        size = len(heard_tokens)
        for start in range(len(tokens) - size + 1):
            positions = range(start, start + size)
            if any(position in taken for position in positions):
                continue
            window = keys[start:start + size]
            if [_heard_form(key) for key in window] != heard_tokens:
                continue
            if any(_DIGIT.search(key) for key in window):
                break
            if any(((_hangul_word(key) or key) in PROTECTED_WORDS) or key in PROTECTED_WORDS for key in window):
                break
            particle, punctuation = _suffix(tokens[start + size - 1], keys[start + size - 1])
            new_tokens = replacement.split()
            new_tokens[-1] = new_tokens[-1] + particle + punctuation
            original = " ".join(tokens[start:start + size])
            tokens[start:start + size] = new_tokens
            keys[start:start + size] = [_key_token(token) for token in new_tokens]
            shift = len(new_tokens) - size
            taken = {position + shift if position >= start + size else position for position in taken}
            taken.update(range(start, start + len(new_tokens)))
            changes.append(TranscriptChange(
                heard=original, replacement=replacement, code=_code(heard, replacement),
                written=" ".join(new_tokens),
            ))
            break
    return tokens


def correct_transcript(text: str, vocabulary: ProtocolVocabulary | None = None) -> TranscriptCorrection:
    """The text with repeats read once and the protocol's terms heard.

    ``vocabulary`` is the active protocol's own (``_protocol_vocabulary``);
    None -- no protocol -- hears no terms. Whitespace is normalised in the
    corrected text; the spoken text is returned as given. If any change
    would alter a token holding a digit, nothing is changed and the single
    code ``numbers_guard`` says so.
    """

    tokens = text.split()
    if not tokens:
        return TranscriptCorrection(spoken=text, text=text)
    changes: list[TranscriptChange] = []
    tokens = _collapse_repeats(tokens, changes)
    if vocabulary is not None:
        tokens = _hear_terms(tokens, vocabulary, changes)
    corrected = " ".join(tokens)
    if not changes:
        return TranscriptCorrection(spoken=text, text=text)
    if _digit_tokens(text) != _digit_tokens(corrected):
        return TranscriptCorrection(
            spoken=text, text=text,
            changes=(TranscriptChange(heard="", replacement="", code=NUMBERS_GUARD),),
        )
    return TranscriptCorrection(spoken=text, text=corrected, changes=tuple(changes))


_RECORD_TEXT_KEYS = {
    "note_record": ("content",),
    "notebook_record": ("text", "result", "next_change"),
    "record_fix": ("before", "after"),
}


def records_as_heard(plan: Any, correction: TranscriptCorrection | None) -> Any:
    """The plan with the words its records carry put back as they were heard.

    ``anomaly_text``, ``observation_outcome``, the note's content, the
    notebook answer's text and a record fix's words are what the server
    appends to the experiment record; each goes back to the recognizer's
    words. Understanding -- the action, the step, the answer -- is untouched.
    """

    if correction is None or not correction.changed:
        return plan
    fields: dict[str, Any] = {}
    for name in ("anomaly_text", "observation_outcome", "normalized_transcript"):
        value = getattr(plan, name, None)
        if isinstance(value, str) and name != "normalized_transcript":
            restored = correction.as_heard(value)
            if restored != value:
                fields[name] = restored
    for name, keys in _RECORD_TEXT_KEYS.items():
        record = getattr(plan, name, None)
        if not isinstance(record, dict):
            continue
        updated = dict(record)
        touched = False
        for key in keys:
            value = updated.get(key)
            if isinstance(value, str):
                restored = correction.as_heard(value)
                if restored != value:
                    updated[key] = restored
                    touched = True
        if touched:
            fields[name] = updated
    return replace(plan, **fields) if fields else plan
