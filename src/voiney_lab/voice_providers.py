"""Speech-to-text and text-to-speech provider adapters (lane SV, 2026-10-05).

The Cascade voice path sends one finished utterance (16 kHz mono PCM16, cut
by the server's WebRTC VAD) to one batch STT request, and asks one TTS request
per reply segment for 16 kHz mono PCM16. This module lets the speech role pick
the provider for each of those two calls:

* ``xai``          -- the original path. Its request code stays in
                      ``server.py`` (``transcribe`` / ``synthesize``) so the
                      default behaviour is byte-for-byte what it was; it is
                      kept only so a deployment can switch back.
* ``google_cloud`` -- Cloud Speech-to-Text **v1** ``speech:recognize`` and
                      Cloud Text-to-Speech v1 ``text:synthesize``, both with
                      ``VOINEY_LAB_GOOGLE_SPEECH_API_KEY``. Speech-to-Text v2
                      (where ``chirp_3`` lives) refuses API keys -- it answered
                      401 "API keys are not supported by this API" when this
                      lane probed it -- so the STT side is limited to the v1
                      models until a service-account credential is decided.
* ``elevenlabs``   -- ``POST /v1/speech-to-text`` and
                      ``POST /v1/text-to-speech/{voice_id}`` with
                      ``ELEVENLABS_API_KEY``.

Every adapter keeps the existing failure contract: an HTTP error, a malformed
response or a timeout raises, so the caller's existing STT/TTS failure path
(re-ask, no state change) runs exactly as it does for xAI; an empty
recognition result returns an empty ``Transcription`` so the caller's
empty-transcript rejection runs. Nothing here advances a protocol step, and
the deterministic front rules and server validation see the transcript the
same way whichever provider produced it. The adapters never rewrite the
provider's transcript.

Provider calls in tests are fake-backed (contract-tested). The lane SV report
records which real calls succeeded in this environment.
"""

from __future__ import annotations

import base64
import io
import os
import wave
from collections.abc import Mapping
from dataclasses import dataclass

import requests

from voiney_lab.audio import SAMPLE_RATE, pcm_to_wav
from voiney_lab.configuration import ConfigurationError
from voiney_lab.language import Transcription, normalize_provider_language

XAI = "xai"
GOOGLE_CLOUD = "google_cloud"
ELEVENLABS = "elevenlabs"
PROVIDERS = (XAI, GOOGLE_CLOUD, ELEVENLABS)

STT_PROVIDER_ENV = "VOINEY_LAB_STT_PROVIDER"
STT_MODEL_ENV = "VOINEY_LAB_STT_MODEL"
STT_LANGUAGE_ENV = "VOINEY_LAB_STT_LANGUAGE"
STT_TIMEOUT_ENV = "VOINEY_LAB_STT_TIMEOUT_SECONDS"
TTS_PROVIDER_ENV = "VOINEY_LAB_TTS_PROVIDER"
TTS_MODEL_ENV = "VOINEY_LAB_TTS_MODEL"
TTS_VOICE_ENV = "VOINEY_LAB_TTS_VOICE"
TTS_TIMEOUT_ENV = "VOINEY_LAB_TTS_TIMEOUT_SECONDS"
GOOGLE_KEY_ENV = "VOINEY_LAB_GOOGLE_SPEECH_API_KEY"
ELEVENLABS_KEY_ENV = "ELEVENLABS_API_KEY"

#: The xAI STT and TTS endpoints take no model field; "" means "not sent".
DEFAULT_STT_MODELS = {XAI: "", GOOGLE_CLOUD: "latest_long", ELEVENLABS: "scribe_v2"}
DEFAULT_TTS_MODELS = {XAI: "", GOOGLE_CLOUD: "", ELEVENLABS: "eleven_v4_turbo"}
DEFAULT_TTS_VOICES = {
    XAI: "leo",
    GOOGLE_CLOUD: "ko-KR-Chirp3-HD-Charon",
    # "George", a premade voice in the ElevenLabs default library (it is the
    # voice the API reference's own examples use).
    ELEVENLABS: "JBFqnCBsd6RMkjVDRZzb",
}
DEFAULT_TIMEOUT_SECONDS = 120.0

GOOGLE_STT_URL = "https://speech.googleapis.com/v1/speech:recognize"
GOOGLE_TTS_URL = "https://texttospeech.googleapis.com/v1/text:synthesize"
ELEVENLABS_STT_URL = "https://api.elevenlabs.io/v1/speech-to-text"
ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"

GOOGLE_LOCALES = {"ko": "ko-KR", "en": "en-US", "vi": "vi-VN"}
#: ElevenLabs answers ISO 639-3 codes ("kor"); the server speaks ISO 639-1.
ISO_639_3 = {"kor": "ko", "eng": "en", "vie": "vi"}
LANGUAGES = frozenset(GOOGLE_LOCALES)


class SpeechProviderError(RuntimeError):
    """A speech provider refused the request or answered something unusable.

    The message names the provider and the HTTP status only; it never carries
    the API key, and at most a short, bounded slice of the provider's reply.
    """


def _choice(env: Mapping[str, str], name: str) -> str:
    raw = env.get(name, "").strip().casefold() or XAI
    if raw not in PROVIDERS:
        raise ConfigurationError(f"{name} must be one of: {', '.join(PROVIDERS)}")
    return raw


def _timeout(env: Mapping[str, str], name: str) -> float:
    raw = env.get(name, "").strip()
    if not raw:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a number of seconds") from exc
    if not 1.0 <= value <= 300.0:
        raise ConfigurationError(f"{name} must be between 1 and 300 seconds")
    return value


@dataclass(frozen=True)
class SttProviderSettings:
    """Which provider and model take the one batch STT request per utterance."""

    provider: str = XAI
    model: str = ""
    #: Used when the session's input language is ``auto``. ``None`` lets the
    #: provider detect it; Google v1 cannot, so it falls back to Korean.
    language: str | None = None
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None,
    ) -> "SttProviderSettings":
        env = os.environ if environment is None else environment
        provider = _choice(env, STT_PROVIDER_ENV)
        language = env.get(STT_LANGUAGE_ENV, "").strip().casefold() or None
        if language is not None and language not in LANGUAGES:
            raise ConfigurationError(
                f"{STT_LANGUAGE_ENV} must be one of: {', '.join(sorted(LANGUAGES))}")
        return cls(
            provider=provider,
            model=env.get(STT_MODEL_ENV, "").strip() or DEFAULT_STT_MODELS[provider],
            language=language,
            timeout_seconds=_timeout(env, STT_TIMEOUT_ENV),
        )


@dataclass(frozen=True)
class TtsProviderSettings:
    """Which provider, model and voice speak each reply segment."""

    provider: str = XAI
    model: str = ""
    voice: str = DEFAULT_TTS_VOICES[XAI]
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None,
    ) -> "TtsProviderSettings":
        env = os.environ if environment is None else environment
        provider = _choice(env, TTS_PROVIDER_ENV)
        return cls(
            provider=provider,
            model=env.get(TTS_MODEL_ENV, "").strip() or DEFAULT_TTS_MODELS[provider],
            voice=env.get(TTS_VOICE_ENV, "").strip() or DEFAULT_TTS_VOICES[provider],
            timeout_seconds=_timeout(env, TTS_TIMEOUT_ENV),
        )


def _key(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is not set")
    return value


def _failure(provider: str, kind: str, response: requests.Response) -> SpeechProviderError:
    detail = response.text[:300] if response.content else "empty response"
    return SpeechProviderError(f"{provider} {kind} failed ({response.status_code}): {detail}")


def _json(provider: str, kind: str, response: requests.Response) -> dict:
    if not response.ok:
        raise _failure(provider, kind, response)
    try:
        payload = response.json()
    except ValueError as exc:
        raise SpeechProviderError(f"{provider} {kind} returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise SpeechProviderError(f"{provider} {kind} returned an unexpected payload")
    return payload


def _bounded_keyterms(
    keyterms: tuple[str, ...], *, max_chars: int, max_words: int, limit: int,
) -> tuple[str, ...]:
    return tuple(dict.fromkeys(
        value.strip() for value in keyterms
        if isinstance(value, str) and 1 <= len(value.strip()) <= max_chars
        and len(value.split()) <= max_words
    ))[:limit]


def _duration_seconds(pcm: bytes) -> float:
    """The utterance length the server sent, measured from the PCM itself."""

    return round(len(pcm) / 2 / SAMPLE_RATE, 3)


def stt_language(settings: SttProviderSettings, language: str | None) -> str | None:
    """The session's language when it chose one, else the configured fallback."""

    return language if language is not None else settings.language


# --- STT --------------------------------------------------------------------


def transcribe_google_cloud(
    pcm: bytes, settings: SttProviderSettings, *,
    language: str | None, keyterms: tuple[str, ...],
) -> Transcription:
    """Cloud Speech-to-Text v1 ``speech:recognize`` on one 16 kHz utterance."""

    selected = stt_language(settings, language) or "ko"
    config: dict[str, object] = {
        "encoding": "LINEAR16",
        "sampleRateHertz": SAMPLE_RATE,
        "audioChannelCount": 1,
        "languageCode": GOOGLE_LOCALES[selected],
        "enableAutomaticPunctuation": True,
        "enableWordTimeOffsets": True,
        "maxAlternatives": 1,
    }
    if settings.model:
        config["model"] = settings.model
    phrases = _bounded_keyterms(keyterms, max_chars=100, max_words=10, limit=500)
    if phrases:
        config["speechContexts"] = [{"phrases": list(phrases)}]
    response = requests.post(
        GOOGLE_STT_URL,
        headers={"X-goog-api-key": _key(GOOGLE_KEY_ENV)},
        json={"config": config,
              "audio": {"content": base64.b64encode(pcm).decode("ascii")}},
        timeout=settings.timeout_seconds,
    )
    payload = _json(GOOGLE_CLOUD, "STT", response)
    results = payload.get("results", [])
    texts: list[str] = []
    words: list[dict[str, object]] = []
    detected: str | None = None
    if isinstance(results, list):
        for result in results:
            if not isinstance(result, dict):
                continue
            alternatives = result.get("alternatives") or []
            if not isinstance(alternatives, list) or not alternatives:
                continue
            best = alternatives[0] if isinstance(alternatives[0], dict) else {}
            text = best.get("transcript")
            if isinstance(text, str) and text.strip():
                texts.append(text.strip())
            for item in best.get("words") or ():
                if isinstance(item, dict) and isinstance(item.get("word"), str):
                    words.append({
                        "word": item["word"],
                        "start": _google_seconds(item.get("startTime")),
                        "end": _google_seconds(item.get("endTime")),
                    })
            detected = detected or normalize_provider_language(result.get("languageCode"))
    return Transcription(
        " ".join(texts),
        detected,
        duration_seconds=_duration_seconds(pcm),
        words=tuple(words[:500]),
        response_status=response.status_code,
    )


def _google_seconds(value: object) -> float | None:
    if isinstance(value, str) and value.endswith("s"):
        try:
            return float(value[:-1])
        except ValueError:
            return None
    return None


def transcribe_elevenlabs(
    pcm: bytes, settings: SttProviderSettings, *,
    language: str | None, keyterms: tuple[str, ...],
) -> Transcription:
    """ElevenLabs ``POST /v1/speech-to-text`` on one 16 kHz utterance."""

    selected = stt_language(settings, language)
    fields: list[tuple[str, tuple]] = [
        ("model_id", (None, settings.model or DEFAULT_STT_MODELS[ELEVENLABS])),
        ("tag_audio_events", (None, "false")),
        ("timestamps_granularity", (None, "word")),
    ]
    if selected is not None:
        fields.append(("language_code", (None, selected)))
    # Documented limits: under 50 characters and at most 5 words per term.
    fields.extend(
        ("keyterms", (None, value))
        for value in _bounded_keyterms(keyterms, max_chars=49, max_words=5, limit=100))
    fields.append(("file", ("utterance.wav", pcm_to_wav(pcm), "audio/wav")))
    response = requests.post(
        ELEVENLABS_STT_URL,
        headers={"xi-api-key": _key(ELEVENLABS_KEY_ENV)},
        files=fields,
        timeout=settings.timeout_seconds,
    )
    payload = _json(ELEVENLABS, "STT", response)
    text = payload.get("text", "")
    code = payload.get("language_code")
    detected = normalize_provider_language(
        ISO_639_3.get(code.casefold(), code) if isinstance(code, str) else None)
    raw_words = payload.get("words", ())
    words = tuple(
        {"word": item["text"], "start": item.get("start"), "end": item.get("end")}
        for item in (raw_words[:500] if isinstance(raw_words, list) else ())
        if isinstance(item, dict) and item.get("type", "word") == "word"
        and isinstance(item.get("text"), str)
    )
    return Transcription(
        text.strip() if isinstance(text, str) else "",
        detected,
        duration_seconds=_duration_seconds(pcm),
        words=words,
        response_status=response.status_code,
    )


def transcribe(
    pcm: bytes, settings: SttProviderSettings, *,
    language: str | None = None, keyterms: tuple[str, ...] = (),
) -> Transcription:
    """Send one utterance to the configured non-xAI STT provider."""

    if language not in {None, *LANGUAGES}:
        raise ValueError("STT language is invalid")
    if settings.provider == GOOGLE_CLOUD:
        return transcribe_google_cloud(pcm, settings, language=language, keyterms=keyterms)
    if settings.provider == ELEVENLABS:
        return transcribe_elevenlabs(pcm, settings, language=language, keyterms=keyterms)
    raise ValueError(f"STT provider {settings.provider!r} is not handled here")


# --- TTS --------------------------------------------------------------------


def google_voice_for(voice: str, language: str) -> tuple[str, str]:
    """(languageCode, voice name), moving a Chirp 3 HD name to the turn's locale.

    Chirp 3 HD voices share their star names across locales
    ("ko-KR-Chirp3-HD-Charon", "en-US-Chirp3-HD-Charon"), so an English turn
    keeps the same speaker instead of failing on a Korean voice name.
    """

    locale = GOOGLE_LOCALES.get(language, "ko-KR")
    parts = voice.split("-", 2)
    if len(parts) == 3 and parts[2].startswith("Chirp3-HD-"):
        return locale, f"{locale}-{parts[2]}"
    return locale, voice


def wav_to_pcm16(wav_bytes: bytes) -> bytes:
    """The 16 kHz mono PCM16 frames of a WAV, refusing any other shape."""

    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as source:
            if (source.getnchannels(), source.getsampwidth(), source.getframerate()) != (
                    1, 2, SAMPLE_RATE):
                raise SpeechProviderError("TTS audio is not 16 kHz mono PCM16")
            return source.readframes(source.getnframes())
    except (wave.Error, EOFError) as exc:
        raise SpeechProviderError("TTS audio is not a readable WAV") from exc


def _checked_pcm(provider: str, pcm: bytes) -> bytes:
    if not pcm or len(pcm) % 2:
        raise SpeechProviderError(f"{provider} TTS returned invalid PCM")
    return pcm


def synthesize_google_cloud(text: str, language: str, settings: TtsProviderSettings) -> bytes:
    """Cloud Text-to-Speech v1 ``text:synthesize`` as 16 kHz LINEAR16."""

    locale, name = google_voice_for(settings.voice, language)
    voice: dict[str, str] = {"languageCode": locale, "name": name}
    if settings.model:
        voice["modelName"] = settings.model
    response = requests.post(
        GOOGLE_TTS_URL,
        headers={"X-goog-api-key": _key(GOOGLE_KEY_ENV)},
        json={
            "input": {"text": text},
            "voice": voice,
            "audioConfig": {"audioEncoding": "LINEAR16", "sampleRateHertz": SAMPLE_RATE},
        },
        timeout=settings.timeout_seconds,
    )
    payload = _json(GOOGLE_CLOUD, "TTS", response)
    content = payload.get("audioContent")
    if not isinstance(content, str) or not content:
        raise SpeechProviderError("google_cloud TTS returned no audio")
    try:
        audio = base64.b64decode(content, validate=True)
    except ValueError as exc:
        raise SpeechProviderError("google_cloud TTS returned invalid base64") from exc
    # LINEAR16 arrives with a WAV header; the voice path wants bare frames.
    pcm = wav_to_pcm16(audio) if audio[:4] == b"RIFF" else audio
    return _checked_pcm(GOOGLE_CLOUD, pcm)


def synthesize_elevenlabs(text: str, language: str, settings: TtsProviderSettings) -> bytes:
    """ElevenLabs ``POST /v1/text-to-speech/{voice_id}`` as raw ``pcm_16000``."""

    response = requests.post(
        ELEVENLABS_TTS_URL.format(voice_id=settings.voice),
        headers={"xi-api-key": _key(ELEVENLABS_KEY_ENV)},
        params={"output_format": "pcm_16000"},
        json={"text": text, "model_id": settings.model or DEFAULT_TTS_MODELS[ELEVENLABS],
              "language_code": language},
        timeout=settings.timeout_seconds,
    )
    if not response.ok:
        raise _failure(ELEVENLABS, "TTS", response)
    if "json" in response.headers.get("content-type", "").lower():
        raise SpeechProviderError("elevenlabs TTS returned JSON instead of raw PCM")
    return _checked_pcm(ELEVENLABS, response.content)


def synthesize(text: str, language: str, settings: TtsProviderSettings) -> bytes:
    """Speak one cleaned reply segment with the configured non-xAI provider."""

    if settings.provider == GOOGLE_CLOUD:
        return synthesize_google_cloud(text, language, settings)
    if settings.provider == ELEVENLABS:
        return synthesize_elevenlabs(text, language, settings)
    raise ValueError(f"TTS provider {settings.provider!r} is not handled here")
