"""CLOVA OCR and Google Cloud Vision behind the ProtocolOcrProvider contract.

Two engines, because each is better at a different half of the corpus: NAVER
CLOVA OCR reads Korean print and handwriting, Google Cloud Vision reads
English. Both are called over REST with ``requests`` -- no vendor SDK -- with
the page rendered by PyMuPDF to a 300 dpi PNG in memory. Nothing is written to
disk, and neither the secret, the API key nor the invoke URL is ever logged,
returned or put in a warning.

Selection, per page that needs OCR:

* both engines configured: call both. When the share of Hangul among the
  letters CLOVA read is at least ``HANGUL_SELECTION_THRESHOLD``, CLOVA's text
  is used; otherwise Google's. If one engine fails or times out, the other's
  text is used. If the two disagree about the numbers or units on the page,
  the page is marked ``numeric_review_required`` -- in the result data only;
  it blocks nothing.
* one engine configured: that engine.
* none: no provider is built, and the catalog's existing "OCR not
  configured" path applies.

The result records which engine produced each page and its version. OCR text
is review evidence: the catalog still requires a person to accept it.

Contract-tested against fake transports in the test suite. Live-tested on
2026-10-05 (lane P2): both engines answered real requests for the scanned
reagent-kit guide (4 pages) and three ANKOM pages, and the catalog's
OCR -> accept -> re-analysis flow ran end to end once in a measurement store.
"""

from __future__ import annotations

import base64
import logging
import os
import re
import time
import unicodedata
import uuid
from collections import Counter
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from voiney_lab.experiment_protocol_pdf import (
    ProtocolPdfExtraction,
    extract_protocol_pdf,
)
from voiney_lab.pdf_text_engine import (
    ENGINE_NAME as PDF_ENGINE_NAME,
    OCR_RENDER_DPI,
    engine_version as pdf_engine_version,
    render_page_png,
)
from voiney_lab.protocol_ocr import (
    OcrPage,
    OcrResult,
    ProtocolOcrError,
    ProtocolOcrResultError,
)

log = logging.getLogger("voiney_lab.protocol_ocr")

OCR_PROVIDERS_ENV = "VOINEY_LAB_OCR_PROVIDERS"
CLOVA_INVOKE_URL_ENV = "VOINEY_LAB_CLOVA_OCR_INVOKE_URL"
CLOVA_SECRET_ENV = "VOINEY_LAB_CLOVA_OCR_SECRET"
GOOGLE_API_KEY_ENV = "VOINEY_LAB_GOOGLE_VISION_API_KEY"

CLOVA = "clova"
GOOGLE = "google"
TEXT_LAYER = "pdf-text-layer"

#: Starting value, set by the people who chose the two engines on
#: 2026-10-02. First real output (lane P2): CLOVA's Hangul share reached it
#: on the Korean reagent-kit pages 1-3 (CLOVA chosen), stayed below it on that
#: guide's English table page 4, and was 0.0 on ANKOM (Google chosen). Not
#: tuned further.
HANGUL_SELECTION_THRESHOLD = 0.30

#: (connect, read) seconds for one page request.
OCR_REQUEST_TIMEOUT = (10.0, 60.0)
GOOGLE_VISION_ENDPOINT = "https://vision.googleapis.com/v1/images:annotate"
CLOVA_VERSION = "clova-general-v2"
GOOGLE_VERSION = "vision-v1-document-text"
_MAX_RESPONSE_BYTES = 16 * 1024 * 1024


class ProtocolOcrProviderError(ProtocolOcrError):
    """An OCR engine call failed. Carries no credential or response body."""

    code = "protocol_ocr_provider_failed"


@dataclass(frozen=True)
class RecognizedPage:
    text: str
    confidence: float | None = None


class PageRecognizer(Protocol):
    name: str
    version: str

    def recognize_page(self, png: bytes, *, page_number: int) -> RecognizedPage: ...


def _post_json(
    session: Any,
    url: str,
    *,
    headers: Mapping[str, str],
    body: Mapping[str, Any],
    timeout: tuple[float, float],
    engine: str,
) -> dict[str, Any]:
    """POST one JSON request; errors name the engine and status, nothing else."""

    import requests

    try:
        response = session.post(url, headers=dict(headers), json=dict(body), timeout=timeout)
    except requests.Timeout as error:
        raise ProtocolOcrProviderError(f"{engine} OCR timed out.") from error
    except requests.RequestException as error:
        raise ProtocolOcrProviderError(f"{engine} OCR request failed.") from error
    status = getattr(response, "status_code", None)
    if not isinstance(status, int) or not 200 <= status < 300:
        raise ProtocolOcrProviderError(f"{engine} OCR returned HTTP {status}.")
    content = getattr(response, "content", b"") or b""
    if len(content) > _MAX_RESPONSE_BYTES:
        raise ProtocolOcrProviderError(f"{engine} OCR response is too large.")
    try:
        payload = response.json()
    except ValueError as error:
        raise ProtocolOcrProviderError(f"{engine} OCR returned invalid JSON.") from error
    if not isinstance(payload, dict):
        raise ProtocolOcrProviderError(f"{engine} OCR returned an invalid envelope.")
    return payload


def _mean(values: list[float]) -> float | None:
    usable = [value for value in values if 0.0 <= value <= 1.0]
    return sum(usable) / len(usable) if usable else None


class ClovaOcrRecognizer:
    """NAVER CLOVA OCR (General, V2 request format), one page per request."""

    name = CLOVA
    version = CLOVA_VERSION

    def __init__(
        self,
        invoke_url: str,
        secret: str,
        *,
        session: Any | None = None,
        timeout: tuple[float, float] = OCR_REQUEST_TIMEOUT,
        lang: str = "ko",
    ) -> None:
        if not invoke_url.startswith("https://"):
            raise ValueError("CLOVA OCR invoke URL must be https.")
        if not secret:
            raise ValueError("CLOVA OCR secret is empty.")
        self._invoke_url = invoke_url
        self._secret = secret
        self._session = session
        self._timeout = timeout
        self._lang = lang

    def __repr__(self) -> str:  # never show the URL or secret
        return "ClovaOcrRecognizer()"

    def _http(self) -> Any:
        if self._session is None:
            import requests

            self._session = requests.Session()
        return self._session

    def recognize_page(self, png: bytes, *, page_number: int) -> RecognizedPage:
        payload = _post_json(
            self._http(),
            self._invoke_url,
            headers={"X-OCR-SECRET": self._secret, "Content-Type": "application/json"},
            body={
                "version": "V2",
                "requestId": uuid.uuid4().hex,
                "timestamp": int(time.time() * 1000),
                "lang": self._lang,
                "images": [
                    {
                        "format": "png",
                        "name": f"page-{page_number}",
                        "data": base64.b64encode(png).decode("ascii"),
                    }
                ],
            },
            timeout=self._timeout,
            engine="CLOVA",
        )
        images = payload.get("images")
        if not isinstance(images, list) or len(images) != 1 or not isinstance(images[0], dict):
            raise ProtocolOcrProviderError("CLOVA OCR returned an invalid envelope.")
        image = images[0]
        if image.get("inferResult") != "SUCCESS":
            raise ProtocolOcrProviderError("CLOVA OCR did not read the page.")
        fields = image.get("fields") or []
        if not isinstance(fields, list):
            raise ProtocolOcrProviderError("CLOVA OCR returned an invalid envelope.")
        parts: list[str] = []
        confidences: list[float] = []
        for field in fields:
            if not isinstance(field, dict) or not isinstance(field.get("inferText"), str):
                raise ProtocolOcrProviderError("CLOVA OCR returned an invalid field.")
            parts.append(field["inferText"])
            parts.append("\n" if field.get("lineBreak") is True else " ")
            confidence = field.get("inferConfidence")
            if isinstance(confidence, (int, float)) and not isinstance(confidence, bool):
                confidences.append(float(confidence))
        text = "".join(parts).rstrip()
        return RecognizedPage(text=text, confidence=_mean(confidences))


class GoogleVisionRecognizer:
    """Google Cloud Vision DOCUMENT_TEXT_DETECTION, one page per request.

    The API key travels in the ``X-Goog-Api-Key`` header, never in the URL, so
    it cannot end up in a request log line or an exception message.
    """

    name = GOOGLE
    version = GOOGLE_VERSION

    def __init__(
        self,
        api_key: str,
        *,
        session: Any | None = None,
        timeout: tuple[float, float] = OCR_REQUEST_TIMEOUT,
        endpoint: str = GOOGLE_VISION_ENDPOINT,
    ) -> None:
        if not api_key:
            raise ValueError("Google Vision API key is empty.")
        self._api_key = api_key
        self._session = session
        self._timeout = timeout
        self._endpoint = endpoint

    def __repr__(self) -> str:  # never show the key
        return "GoogleVisionRecognizer()"

    def _http(self) -> Any:
        if self._session is None:
            import requests

            self._session = requests.Session()
        return self._session

    def recognize_page(self, png: bytes, *, page_number: int) -> RecognizedPage:
        del page_number
        payload = _post_json(
            self._http(),
            self._endpoint,
            headers={"X-Goog-Api-Key": self._api_key, "Content-Type": "application/json"},
            body={
                "requests": [
                    {
                        "image": {"content": base64.b64encode(png).decode("ascii")},
                        "features": [{"type": "DOCUMENT_TEXT_DETECTION"}],
                    }
                ]
            },
            timeout=self._timeout,
            engine="Google Vision",
        )
        responses = payload.get("responses")
        if not isinstance(responses, list) or len(responses) != 1 or not isinstance(responses[0], dict):
            raise ProtocolOcrProviderError("Google Vision returned an invalid envelope.")
        response = responses[0]
        if response.get("error"):
            raise ProtocolOcrProviderError("Google Vision did not read the page.")
        annotation = response.get("fullTextAnnotation") or {}
        if not isinstance(annotation, dict):
            raise ProtocolOcrProviderError("Google Vision returned an invalid envelope.")
        text = annotation.get("text", "")
        if not isinstance(text, str):
            raise ProtocolOcrProviderError("Google Vision returned invalid text.")
        confidences = [
            float(page["confidence"])
            for page in annotation.get("pages") or []
            if isinstance(page, dict)
            and isinstance(page.get("confidence"), (int, float))
            and not isinstance(page.get("confidence"), bool)
        ]
        return RecognizedPage(text=text.rstrip(), confidence=_mean(confidences))


_HANGUL_RANGES = (
    (0xAC00, 0xD7A3),  # syllables
    (0x1100, 0x11FF),  # jamo
    (0x3130, 0x318F),  # compatibility jamo
    (0xA960, 0xA97F),  # jamo extended-A
    (0xD7B0, 0xD7FF),  # jamo extended-B
)


def hangul_ratio(text: str) -> float:
    """Share of the letters in ``text`` that are Hangul. Digits and symbols are not letters."""

    letters = [character for character in text if character.isalpha()]
    if not letters:
        return 0.0
    hangul = sum(
        1
        for character in letters
        if any(low <= ord(character) <= high for low, high in _HANGUL_RANGES)
    )
    return hangul / len(letters)


_UNITS = (
    "µl", "ul", "ml", "dl", "l", "ng", "µg", "ug", "mg", "kg", "g",
    "nm", "µm", "um", "mm", "cm", "m", "pm", "µmol", "mmol", "mol",
    "°c", "rpm", "×g", "xg", "min", "hr", "h", "sec", "s", "%",
    "kda", "da", "bp", "kb", "mv", "v", "ma", "w",
)
# "u" for micro is how both engines often read a "µ"; it is not a different unit.
_UNIT_SPELLINGS = {"ul": "µl", "ug": "µg", "um": "µm", "xg": "×g", "hr": "h", "sec": "s"}
_NUMBER_WITH_UNIT = re.compile(
    r"(?<![\d.,:])(\d+(?:[.,:]\d+)*)\s?("
    + "|".join(re.escape(unit) for unit in sorted(_UNITS, key=len, reverse=True))
    + r")?(?![a-zµ\d])"
)


def numeric_tokens(text: str) -> Counter[str]:
    """Every number on the page with the unit written right after it, if any."""

    normalized = unicodedata.normalize("NFKC", text).casefold().replace("μ", "µ")
    return Counter(
        number + _UNIT_SPELLINGS.get(unit, unit)
        for number, unit in _NUMBER_WITH_UNIT.findall(normalized)
    )


@dataclass(frozen=True)
class _EngineOutcome:
    engine: PageRecognizer
    page: RecognizedPage | None
    error: str | None


def _call(engine: PageRecognizer, png: bytes, page_number: int) -> _EngineOutcome:
    try:
        return _EngineOutcome(engine, engine.recognize_page(png, page_number=page_number), None)
    except ProtocolOcrError as error:
        return _EngineOutcome(engine, None, str(error) or "OCR failed.")
    except Exception:  # noqa: BLE001 - an engine fault must not hide the other engine
        return _EngineOutcome(engine, None, f"{engine.name} OCR failed.")


def _label(engine: PageRecognizer) -> str:
    return "CLOVA" if engine.name == CLOVA else "Google Vision" if engine.name == GOOGLE else engine.name


@dataclass(frozen=True)
class PageSelection:
    page: OcrPage
    warnings: tuple[str, ...]


def select_page_text(
    page_number: int,
    outcomes: tuple[_EngineOutcome, ...],
    *,
    threshold: float = HANGUL_SELECTION_THRESHOLD,
) -> PageSelection:
    """Pick one engine's text for one page, following the module's rules."""

    succeeded = [outcome for outcome in outcomes if outcome.page is not None]
    warnings = [
        f"Page {page_number}: {_label(outcome.engine)} failed; "
        + ("the other engine's text was used." if succeeded else "no text.")
        for outcome in outcomes
        if outcome.page is None
    ]
    if not succeeded:
        raise ProtocolOcrProviderError(
            f"OCR failed on page {page_number} with every configured engine."
        )
    by_name = {outcome.engine.name: outcome for outcome in succeeded}
    numeric_review = False
    if CLOVA in by_name and GOOGLE in by_name:
        clova, google = by_name[CLOVA], by_name[GOOGLE]
        chosen = clova if hangul_ratio(clova.page.text) >= threshold else google
        if numeric_tokens(clova.page.text) != numeric_tokens(google.page.text):
            numeric_review = True
            warnings.append(
                f"Page {page_number}: CLOVA and Google Vision read different "
                "numbers or units; check the numbers against the page."
            )
    else:
        chosen = succeeded[0]
    return PageSelection(
        page=OcrPage(
            source_page_number=page_number,
            text=chosen.page.text,
            confidence=chosen.page.confidence,
            provider=chosen.engine.name,
            provider_version=chosen.engine.version,
            numeric_review_required=numeric_review,
        ),
        warnings=tuple(warnings),
    )


class DualEngineOcrProvider:
    """A ProtocolOcrProvider over one or two page recognizers."""

    def __init__(
        self,
        engines: tuple[PageRecognizer, ...],
        *,
        threshold: float = HANGUL_SELECTION_THRESHOLD,
        render: Callable[..., bytes] = render_page_png,
        extract: Callable[[Path], ProtocolPdfExtraction] = extract_protocol_pdf,
        dpi: int = OCR_RENDER_DPI,
    ) -> None:
        names = [engine.name for engine in engines]
        if not engines or len(set(names)) != len(names) or not set(names) <= {CLOVA, GOOGLE}:
            raise ValueError("OCR engines must be one or both of clova and google.")
        self._engines = tuple(sorted(engines, key=lambda engine: (CLOVA, GOOGLE).index(engine.name)))
        self._threshold = threshold
        self._render = render
        self._extract = extract
        self._dpi = dpi

    @property
    def engine_names(self) -> tuple[str, ...]:
        return tuple(engine.name for engine in self._engines)

    def recognize(
        self,
        source_pdf: Path,
        *,
        source_sha256: str,
        page_count: int,
    ) -> OcrResult:
        extraction = self._extract(Path(source_pdf))
        if extraction.sha256 != source_sha256 or extraction.page_count != page_count:
            raise ProtocolOcrResultError("OCR source identity does not match the PDF.")
        # The pages the extraction marked. A document the catalog sends for
        # OCR without any marked page (too little text overall) is read whole.
        wanted = set(extraction.ocr_required_page_numbers) or set(range(1, page_count + 1))
        pages: list[OcrPage] = []
        warnings: list[str] = []
        used: dict[str, str] = {}
        layer_version = f"{PDF_ENGINE_NAME}-{pdf_engine_version()}"
        with ThreadPoolExecutor(max_workers=len(self._engines)) as pool:
            for page in extraction.pages:
                number = page.source_page_number
                if number not in wanted:
                    pages.append(
                        OcrPage(
                            source_page_number=number,
                            text=page.text,
                            provider=TEXT_LAYER,
                            provider_version=layer_version,
                        )
                    )
                    continue
                png = self._render(Path(source_pdf), number, dpi=self._dpi)
                outcomes = tuple(
                    pool.map(lambda engine: _call(engine, png, number), self._engines)
                )
                selection = select_page_text(number, outcomes, threshold=self._threshold)
                pages.append(selection.page)
                warnings.extend(selection.warnings)
                engine = next(e for e in self._engines if e.name == selection.page.provider)
                used[engine.name] = engine.version
        kept = [number for number in range(1, page_count + 1) if number not in wanted]
        if kept:
            warnings.append(
                "Pages with a usable text layer kept it instead of OCR: "
                + ", ".join(str(number) for number in kept)
                + "."
            )
        names = [name for name in (CLOVA, GOOGLE) if name in used]
        return OcrResult(
            source_sha256=source_sha256,
            provider="-".join(names) or TEXT_LAYER,
            provider_version="_".join(used[name] for name in names) or layer_version,
            pages=tuple(pages),
            warnings=tuple(dict.fromkeys(warning[:500] for warning in warnings)),
        )


def ocr_provider_from_environment(
    environ: Mapping[str, str] | None = None,
    *,
    session_factory: Callable[[], Any] | None = None,
) -> DualEngineOcrProvider | None:
    """Build the provider the deployment configured, or None.

    ``VOINEY_LAB_OCR_PROVIDERS`` lists the engines (``clova``,
    ``google``, or both, comma-separated). An engine listed without its
    credentials is left out and logged by name only. None means OCR is not
    configured, and the catalog reports ``protocol_ocr_not_configured``.
    """

    env = os.environ if environ is None else environ
    requested = [
        item.strip().casefold()
        for item in (env.get(OCR_PROVIDERS_ENV) or "").split(",")
        if item.strip()
    ]
    engines: list[PageRecognizer] = []
    session = session_factory() if session_factory is not None else None
    for name in dict.fromkeys(requested):
        if name == CLOVA:
            url = (env.get(CLOVA_INVOKE_URL_ENV) or "").strip()
            secret = (env.get(CLOVA_SECRET_ENV) or "").strip()
            if url.startswith("https://") and secret:
                engines.append(ClovaOcrRecognizer(url, secret, session=session))
            else:
                log.warning("protocol_ocr engine=clova skipped: invoke URL or secret not set")
        elif name == GOOGLE:
            key = (env.get(GOOGLE_API_KEY_ENV) or "").strip()
            if key:
                engines.append(GoogleVisionRecognizer(key, session=session))
            else:
                log.warning("protocol_ocr engine=google skipped: API key not set")
        else:
            log.warning("protocol_ocr unknown engine name ignored")
    if not engines:
        return None
    return DualEngineOcrProvider(tuple(engines))
