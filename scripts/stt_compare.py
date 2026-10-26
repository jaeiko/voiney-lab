#!/usr/bin/env python3
"""Compare speech-to-text providers on recorded diagnostic turns (lane HEAR, offline).

Input: the folder the opt-in STT diagnostic recording writes
(``VOINEY_LAB_STT_DIAGNOSTICS_ENABLED=true``; ``<identity>.wav`` beside
``<identity>.json``), and a reference file a person wrote by listening --
one JSON object per line: ``{"identity": "...", "reference": "...",
"terms": ["AMBIC", ...]}`` (``terms`` optional; the recording's own
keyterms are used when absent).

For each provider it scores the transcripts against the references:

* word error rate (WER) over whitespace words, and character error rate
  (CER) over Hangul/letters/digits -- Korean is often one word to a space;
* number-and-unit accuracy: the share of the reference's numbers with their
  unit ("37도", "15 min", "1.5 mL") written the same in the transcript;
* term accuracy: the share of the protocol terms in the reference that the
  transcript also has.

By default no provider is called: the recording's own transcript (the
provider that ran at the time, ``raw_transcript`` in the JSON) is scored as
``recorded``. ``--live google_cloud_v2,google_cloud,elevenlabs,xai`` sends each WAV to those
providers -- one request per turn per provider, counted and printed -- and
needs the provider's credentials in the environment; only a person runs that.
V2 uses the product's service-account JSON settings, not an API key. OAuth
refresh requests (when the cached token expires) are additional to the
recognition requests counted here.

    python scripts/stt_compare.py data/runtime/stt-diagnostics references.jsonl
    python scripts/stt_compare.py DIR REFS --live google_cloud,elevenlabs --language ko

Prints a JSON summary per provider (and ``--details`` per turn). Never
prints audio; transcripts appear only with ``--details``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

_CHAR = re.compile(r"[0-9A-Za-z가-힣]")
_NUMBER_UNIT = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(°\s*C|℃|도|분|초|시간|min|sec|s|h|%|rpm|x\s*g|g|mg|µg|ug|kg|"
    r"µL|uL|μL|mL|ml|L|l|mM|µM|uM|nM|M|배|회|번)?",
    re.IGNORECASE,
)
_UNIT_ALIASES = {"ul": "µl", "μl": "µl", "ml": "ml", "℃": "°c", "도": "°c", "sec": "s", "초": "s",
                 "min": "분", "h": "시간", "ug": "µg", "um": "µm"}

PROVIDERS = ("google_cloud_v2", "google_cloud", "elevenlabs", "xai")


def edit_distance(left: list[str], right: list[str]) -> int:
    previous = list(range(len(right) + 1))
    for i, a in enumerate(left, 1):
        current = [i]
        for j, b in enumerate(right, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (a != b)))
        previous = current
    return previous[-1]


def words(text: str) -> list[str]:
    return [w for w in re.split(r"\s+", re.sub(r"[.,!?~…。，！？\"'()]", " ", text.casefold())) if w]


def characters(text: str) -> list[str]:
    return _CHAR.findall(text.casefold())


def number_units(text: str) -> list[tuple[str, str]]:
    found = []
    for number, unit in _NUMBER_UNIT.findall(text):
        unit_key = re.sub(r"\s+", "", unit).casefold()
        found.append((number.replace(",", "."), _UNIT_ALIASES.get(unit_key, unit_key)))
    return found


@dataclass
class Score:
    turns: int = 0
    word_errors: int = 0
    reference_words: int = 0
    char_errors: int = 0
    reference_chars: int = 0
    numbers_right: int = 0
    numbers_total: int = 0
    terms_right: int = 0
    terms_total: int = 0
    empty: int = 0
    failed: int = 0
    details: list[dict] = field(default_factory=list)

    def add(self, identity: str, reference: str, hypothesis: str | None, terms: list[str]) -> None:
        self.turns += 1
        if hypothesis is None:
            self.failed += 1
            hypothesis = ""
        if not hypothesis.strip():
            self.empty += 1
        ref_w, hyp_w = words(reference), words(hypothesis)
        ref_c, hyp_c = characters(reference), characters(hypothesis)
        self.word_errors += edit_distance(ref_w, hyp_w)
        self.reference_words += len(ref_w)
        self.char_errors += edit_distance(ref_c, hyp_c)
        self.reference_chars += len(ref_c)
        heard = number_units(hypothesis)
        for item in number_units(reference):
            self.numbers_total += 1
            if item in heard:
                self.numbers_right += 1
                heard.remove(item)
        lowered = hypothesis.casefold().replace(" ", "")
        for term in terms:
            key = term.casefold().replace(" ", "")
            if key and key in reference.casefold().replace(" ", ""):
                self.terms_total += 1
                self.terms_right += key in lowered
        self.details.append({"identity": identity, "reference": reference, "hypothesis": hypothesis})

    def summary(self) -> dict:
        def rate(a: int, b: int) -> float | None:
            return round(a / b, 4) if b else None
        return {
            "turns": self.turns,
            "wer": rate(self.word_errors, self.reference_words),
            "cer": rate(self.char_errors, self.reference_chars),
            "number_unit_accuracy": rate(self.numbers_right, self.numbers_total),
            "number_units": self.numbers_total,
            "term_accuracy": rate(self.terms_right, self.terms_total),
            "terms": self.terms_total,
            "empty_transcripts": self.empty,
            "failed_requests": self.failed,
        }


def load_references(path: Path) -> dict[str, dict]:
    references: dict[str, dict] = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        item = json.loads(line)
        if not isinstance(item, dict) or not isinstance(item.get("identity"), str) or not isinstance(item.get("reference"), str):
            raise ValueError(f"{path}:{number}: needs identity and reference strings")
        references[item["identity"]] = item
    return references


def read_pcm(wav_path: Path) -> bytes:
    with wave.open(str(wav_path), "rb") as handle:
        if handle.getnchannels() != 1 or handle.getsampwidth() != 2 or handle.getframerate() != 16000:
            raise ValueError(f"{wav_path.name}: not 16 kHz mono PCM16")
        return handle.readframes(handle.getnframes())


Transcriber = Callable[[bytes, str, str | None, tuple[str, ...]], str]


def live_transcriber() -> Transcriber:
    """One real request per call through the product's own adapters (a person runs this).

    Each provider is called with its default model (``DEFAULT_STT_MODELS``)
    and the key the server would use; the session's language and the
    recording's keyterms are sent as the server sends them.
    """

    import os
    from unittest.mock import patch

    from voiney_lab import voice_providers

    def run(pcm: bytes, name: str, language: str | None, keyterms: tuple[str, ...]) -> str:
        if name == voice_providers.XAI:
            from voiney_lab.server import transcribe as server_transcribe
            with patch.dict(os.environ, {voice_providers.STT_PROVIDER_ENV: voice_providers.XAI}):
                return server_transcribe(pcm, language=language, keyterms=keyterms).text
        # Parse the requested provider's settings, even when the server normally
        # uses another one (V2 alone requires its own credential configuration).
        settings = voice_providers.SttProviderSettings.from_environment({
            **os.environ, voice_providers.STT_PROVIDER_ENV: name,
            voice_providers.STT_MODEL_ENV: ""})
        return voice_providers.transcribe(pcm, settings, language=language, keyterms=keyterms).text

    return run


def compare(
    directory: Path, references: dict[str, dict], *, providers: tuple[str, ...] = (),
    language: str | None = "ko", transcriber: Transcriber | None = None,
) -> tuple[dict[str, Score], int]:
    scores: dict[str, Score] = {"recorded": Score()}
    for name in providers:
        scores[name] = Score()
    calls = 0
    live = transcriber or (live_transcriber() if providers else None)
    for identity, item in sorted(references.items()):
        record_path = directory / f"{identity}.json"
        wav_path = directory / f"{identity}.wav"
        if not record_path.is_file():
            continue
        record = json.loads(record_path.read_text(encoding="utf-8"))
        keyterms = tuple(term for term in record.get("keyterms") or () if isinstance(term, str))
        terms = [t for t in item.get("terms") or keyterms if isinstance(t, str)]
        scores["recorded"].add(identity, item["reference"], str(record.get("raw_transcript") or ""), terms)
        if not providers:
            continue
        pcm = read_pcm(wav_path)
        for name in providers:
            calls += 1
            try:
                text = (transcriber or live)(pcm, name, language, keyterms)
            except Exception as exc:  # noqa: BLE001 - counted as a failed request
                print(f"{name} {identity}: {type(exc).__name__}", file=sys.stderr)
                text = None
            scores[name].add(identity, item["reference"], text, terms)
    return scores, calls


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score STT providers on recorded diagnostic turns (offline by default).")
    parser.add_argument("directory", type=Path, help="the STT diagnostic folder (<identity>.wav + .json)")
    parser.add_argument("references", type=Path, help="JSON lines: identity, reference[, terms]")
    parser.add_argument("--live", default="", help=f"comma-separated providers to call: {', '.join(PROVIDERS)}")
    parser.add_argument("--language", default="ko", help="the language sent to the providers (ko, en, vi or auto)")
    parser.add_argument("--details", action="store_true", help="also print each turn's reference and transcripts")
    args = parser.parse_args(argv)
    providers = tuple(name for name in (part.strip() for part in args.live.split(",")) if name)
    unknown = [name for name in providers if name not in PROVIDERS]
    if unknown:
        parser.error(f"unknown provider: {', '.join(unknown)}")
    references = load_references(args.references)
    scores, calls = compare(
        args.directory, references, providers=providers,
        language=None if args.language == "auto" else args.language)
    output = {name: score.summary() for name, score in scores.items()}
    output["provider_requests"] = calls
    if args.details:
        output["details"] = {name: score.details for name, score in scores.items()}
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
