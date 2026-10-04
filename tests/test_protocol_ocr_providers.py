"""CLOVA OCR + Google Cloud Vision, selected per page, against fake transports.

Contract-tested only: every HTTP call here goes to a fake session. No test
needs a credential, and none reaches the network.
"""

from __future__ import annotations

import base64
import json
import logging
import tempfile
import unittest
from pathlib import Path

import requests

from voiney_lab.experiment_protocol_pdf import clear_protocol_pdf_cache, extract_protocol_pdf
from voiney_lab.protocol_ocr import ocr_result_payload, validate_ocr_result
from voiney_lab.protocol_ocr_providers import (
    CLOVA,
    GOOGLE,
    GOOGLE_VISION_ENDPOINT,
    HANGUL_SELECTION_THRESHOLD,
    TEXT_LAYER,
    ClovaOcrRecognizer,
    DualEngineOcrProvider,
    GoogleVisionRecognizer,
    ProtocolOcrProviderError,
    RecognizedPage,
    hangul_ratio,
    numeric_tokens,
    ocr_provider_from_environment,
)

from tests.test_pdf_text_engine import _write_raw_pages

CLOVA_URL = "https://clova.invalid/custom/v1/0000/general"
CLOVA_SECRET = "clova-secret-value-for-tests"
GOOGLE_KEY = "google-api-key-value-for-tests"
PNG = b"\x89PNG\r\n\x1a\n" + b"fake"

KOREAN = "1. 안정화를 위해서 분석기를 켜서 37°C 에서 예열합니다. 500 µL 를 넣습니다."
ENGLISH = "3 Wash the band with 500 uL of solution A at 37 °C for 15 min."


class _Response:
    def __init__(self, status: int, payload: object) -> None:
        self.status_code = status
        self._payload = payload
        self.content = json.dumps(payload).encode("utf-8")

    def json(self) -> object:
        return self._payload


class _Session:
    """Records each request and answers with the next scripted reply."""

    def __init__(self, *replies: object) -> None:
        self.replies = list(replies)
        self.calls: list[dict] = []

    def post(self, url, *, headers, json, timeout):  # noqa: A002 - requests' name
        self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply


def _clova_reply(*fields: tuple[str, bool, float]) -> _Response:
    return _Response(
        200,
        {
            "version": "V2",
            "images": [
                {
                    "inferResult": "SUCCESS",
                    "fields": [
                        {"inferText": text, "lineBreak": line_break, "inferConfidence": confidence}
                        for text, line_break, confidence in fields
                    ],
                }
            ],
        },
    )


def _google_reply(text: str, confidence: float = 0.9) -> _Response:
    return _Response(
        200,
        {"responses": [{"fullTextAnnotation": {"text": text, "pages": [{"confidence": confidence}]}}]},
    )


class _FakeEngine:
    def __init__(self, name: str, text: str | None = None, error: Exception | None = None) -> None:
        self.name = name
        self.version = f"{name}-fake-1"
        self.text = text
        self.error = error
        self.calls: list[tuple[bytes, int]] = []

    def recognize_page(self, png: bytes, *, page_number: int) -> RecognizedPage:
        self.calls.append((png, page_number))
        if self.error is not None:
            raise self.error
        return RecognizedPage(text=self.text or "", confidence=0.8)


class ClovaRequestTests(unittest.TestCase):
    def test_request_shape_and_text_assembly(self) -> None:
        session = _Session(
            _clova_reply(("1.", False, 0.9), ("안정화", True, 0.7), ("37°C", True, 0.8))
        )
        page = ClovaOcrRecognizer(CLOVA_URL, CLOVA_SECRET, session=session).recognize_page(
            PNG, page_number=2
        )
        self.assertEqual(page.text, "1. 안정화\n37°C")
        self.assertAlmostEqual(page.confidence, 0.8)
        call = session.calls[0]
        self.assertEqual(call["url"], CLOVA_URL)
        self.assertEqual(call["headers"]["X-OCR-SECRET"], CLOVA_SECRET)
        body = call["json"]
        self.assertEqual(body["version"], "V2")
        self.assertEqual(body["lang"], "ko")
        self.assertEqual(body["images"][0]["format"], "png")
        self.assertEqual(base64.b64decode(body["images"][0]["data"]), PNG)
        self.assertEqual(body["images"][0]["name"], "page-2")

    def test_failures_carry_no_secret_or_url(self) -> None:
        for reply in (
            _Response(401, {"code": "0002"}),
            _Response(200, {"images": [{"inferResult": "FAILURE"}]}),
            _Response(200, {"images": "nope"}),
            requests.Timeout("timed out"),
            requests.ConnectionError(CLOVA_URL),
        ):
            with self.subTest(reply=type(reply).__name__):
                recognizer = ClovaOcrRecognizer(CLOVA_URL, CLOVA_SECRET, session=_Session(reply))
                with self.assertRaises(ProtocolOcrProviderError) as caught:
                    recognizer.recognize_page(PNG, page_number=1)
                message = str(caught.exception)
                self.assertNotIn(CLOVA_SECRET, message)
                self.assertNotIn("clova.invalid", message)
        self.assertNotIn(CLOVA_SECRET, repr(ClovaOcrRecognizer(CLOVA_URL, CLOVA_SECRET)))

    def test_a_plain_http_invoke_url_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            ClovaOcrRecognizer("http://clova.invalid/general", CLOVA_SECRET)


class GoogleRequestTests(unittest.TestCase):
    def test_request_shape_and_key_in_header_not_url(self) -> None:
        session = _Session(_google_reply("Wash the band\n37 °C\n", 0.95))
        page = GoogleVisionRecognizer(GOOGLE_KEY, session=session).recognize_page(PNG, page_number=1)
        self.assertEqual(page.text, "Wash the band\n37 °C")
        self.assertAlmostEqual(page.confidence, 0.95)
        call = session.calls[0]
        self.assertEqual(call["url"], GOOGLE_VISION_ENDPOINT)
        self.assertNotIn(GOOGLE_KEY, call["url"])
        self.assertEqual(call["headers"]["X-Goog-Api-Key"], GOOGLE_KEY)
        request = call["json"]["requests"][0]
        self.assertEqual(request["features"], [{"type": "DOCUMENT_TEXT_DETECTION"}])
        self.assertEqual(base64.b64decode(request["image"]["content"]), PNG)

    def test_failures_carry_no_key(self) -> None:
        for reply in (
            _Response(403, {"error": {"message": GOOGLE_KEY}}),
            _Response(200, {"responses": [{"error": {"code": 3, "message": "bad image"}}]}),
            requests.Timeout("timed out"),
        ):
            with self.subTest(reply=type(reply).__name__):
                recognizer = GoogleVisionRecognizer(GOOGLE_KEY, session=_Session(reply))
                with self.assertRaises(ProtocolOcrProviderError) as caught:
                    recognizer.recognize_page(PNG, page_number=1)
                self.assertNotIn(GOOGLE_KEY, str(caught.exception))
        self.assertNotIn(GOOGLE_KEY, repr(GoogleVisionRecognizer(GOOGLE_KEY)))

    def test_an_empty_page_is_empty_text_not_an_error(self) -> None:
        session = _Session(_Response(200, {"responses": [{}]}))
        page = GoogleVisionRecognizer(GOOGLE_KEY, session=session).recognize_page(PNG, page_number=1)
        self.assertEqual(page.text, "")


class _ScannedPdf(unittest.TestCase):
    """A two-page PDF: page 1 has a text layer, page 2 is blank (a 'scan')."""

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        clear_protocol_pdf_cache()
        self.addCleanup(clear_protocol_pdf_cache)
        self.pdf = Path(self._temp.name) / "mixed.pdf"
        _write_raw_pages(
            self.pdf,
            ("BT /F1 12 Tf 72 720 Td (1 Add 5 ml of buffer and mix.) Tj ET", None),
        )
        self.extraction = extract_protocol_pdf(self.pdf)
        self.rendered: list[tuple[int, int]] = []

    def _render(self, path, page_number, *, dpi):
        self.rendered.append((page_number, dpi))
        return PNG

    def _recognize(self, *engines, threshold=HANGUL_SELECTION_THRESHOLD):
        provider = DualEngineOcrProvider(tuple(engines), render=self._render, threshold=threshold)
        result = provider.recognize(
            self.pdf,
            source_sha256=self.extraction.sha256,
            page_count=self.extraction.page_count,
        )
        return validate_ocr_result(
            result,
            expected_sha256=self.extraction.sha256,
            expected_page_count=self.extraction.page_count,
        )


class SelectionTests(_ScannedPdf):
    def test_mostly_hangul_uses_clova(self) -> None:
        clova, google = _FakeEngine(CLOVA, KOREAN), _FakeEngine(GOOGLE, "1. garbled 37°C 500 µL")
        result = self._recognize(clova, google)
        page = result.pages[1]
        self.assertEqual(page.text, KOREAN)
        self.assertEqual((page.provider, page.provider_version), (CLOVA, "clova-fake-1"))
        self.assertEqual(result.provider, CLOVA)
        self.assertEqual(result.provider_version, "clova-fake-1")

    def test_english_uses_google(self) -> None:
        clova, google = _FakeEngine(CLOVA, ENGLISH), _FakeEngine(GOOGLE, ENGLISH)
        result = self._recognize(clova, google)
        self.assertEqual(result.pages[1].provider, GOOGLE)
        self.assertEqual(result.provider, GOOGLE)
        self.assertFalse(result.pages[1].numeric_review_required)

    def test_only_one_engine_configured_is_used_alone(self) -> None:
        for engine in (_FakeEngine(CLOVA, ENGLISH), _FakeEngine(GOOGLE, KOREAN)):
            with self.subTest(engine=engine.name):
                result = self._recognize(engine)
                self.assertEqual(result.pages[1].provider, engine.name)
                self.assertEqual(len(engine.calls), 1)

    def test_one_engine_failing_uses_the_other(self) -> None:
        for failing, working in ((CLOVA, GOOGLE), (GOOGLE, CLOVA)):
            with self.subTest(failing=failing):
                engines = {
                    failing: _FakeEngine(failing, error=ProtocolOcrProviderError("timed out")),
                    working: _FakeEngine(working, KOREAN),
                }
                result = self._recognize(engines[CLOVA], engines[GOOGLE])
                self.assertEqual(result.pages[1].provider, working)
                self.assertFalse(result.pages[1].numeric_review_required)
                self.assertTrue(any("failed" in warning for warning in result.warnings))

    def test_an_unexpected_engine_fault_still_falls_back(self) -> None:
        clova = _FakeEngine(CLOVA, error=RuntimeError("boom"))
        result = self._recognize(clova, _FakeEngine(GOOGLE, ENGLISH))
        self.assertEqual(result.pages[1].provider, GOOGLE)

    def test_both_engines_failing_is_an_ocr_failure(self) -> None:
        with self.assertRaises(ProtocolOcrProviderError) as caught:
            self._recognize(
                _FakeEngine(CLOVA, error=ProtocolOcrProviderError("x")),
                _FakeEngine(GOOGLE, error=ProtocolOcrProviderError("y")),
            )
        self.assertEqual(caught.exception.code, "protocol_ocr_provider_failed")

    def test_different_numbers_are_marked_and_not_blocked(self) -> None:
        clova = _FakeEngine(CLOVA, KOREAN)
        google = _FakeEngine(GOOGLE, KOREAN.replace("500 µL", "600 µL"))
        result = self._recognize(clova, google)
        page = result.pages[1]
        self.assertEqual(page.provider, CLOVA)
        self.assertTrue(page.numeric_review_required)
        self.assertTrue(any("numbers or units" in warning for warning in result.warnings))
        payload = ocr_result_payload(result)
        self.assertIs(payload["pages"][1]["numeric_review_required"], True)
        self.assertEqual(payload["pages"][1]["provider"], CLOVA)
        self.assertEqual(payload["review_state"], "review_required")

    def test_only_pages_needing_ocr_are_rendered_and_sent(self) -> None:
        clova, google = _FakeEngine(CLOVA, KOREAN), _FakeEngine(GOOGLE, ENGLISH)
        result = self._recognize(clova, google)
        self.assertEqual(self.rendered, [(2, 300)])
        self.assertEqual([call[1] for call in clova.calls], [2])
        self.assertEqual([call[1] for call in google.calls], [2])
        first = result.pages[0]
        self.assertEqual(first.provider, TEXT_LAYER)
        self.assertEqual(first.text, self.extraction.pages[0].text)
        self.assertTrue(any("kept it instead of OCR: 1" in w for w in result.warnings))

    def test_a_source_identity_mismatch_is_refused(self) -> None:
        provider = DualEngineOcrProvider((_FakeEngine(GOOGLE, ENGLISH),), render=self._render)
        with self.assertRaises(Exception) as caught:
            provider.recognize(self.pdf, source_sha256="0" * 64, page_count=2)
        self.assertEqual(getattr(caught.exception, "code", None), "protocol_ocr_result_invalid")

    def test_the_page_is_rendered_by_the_real_engine_in_memory(self) -> None:
        engine = _FakeEngine(GOOGLE, ENGLISH)
        provider = DualEngineOcrProvider((engine,), dpi=72)
        provider.recognize(
            self.pdf, source_sha256=self.extraction.sha256, page_count=self.extraction.page_count
        )
        png, number = engine.calls[0]
        self.assertEqual(number, 2)
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(sorted(p.name for p in Path(self._temp.name).iterdir()), ["mixed.pdf"])


class SelectionRuleTests(unittest.TestCase):
    def test_hangul_ratio_counts_letters_only(self) -> None:
        self.assertEqual(hangul_ratio("123 456"), 0.0)
        self.assertEqual(hangul_ratio("안녕"), 1.0)
        self.assertAlmostEqual(hangul_ratio("안녕 ab"), 0.5)
        self.assertGreaterEqual(hangul_ratio(KOREAN), HANGUL_SELECTION_THRESHOLD)
        self.assertLess(hangul_ratio(ENGLISH), HANGUL_SELECTION_THRESHOLD)

    def test_numeric_tokens_ignore_spelling_of_the_same_value(self) -> None:
        self.assertEqual(numeric_tokens("500 uL at 37 °C, 15 min"), numeric_tokens("500 µL at 37℃, 15min"))
        self.assertNotEqual(numeric_tokens("500 µL"), numeric_tokens("500 mL"))
        self.assertNotEqual(numeric_tokens("1.5 h"), numeric_tokens("15 h"))


class EnvironmentTests(unittest.TestCase):
    def _env(self, providers: str, **overrides: str) -> dict[str, str]:
        env = {
            "VOINEY_LAB_OCR_PROVIDERS": providers,
            "VOINEY_LAB_CLOVA_OCR_INVOKE_URL": CLOVA_URL,
            "VOINEY_LAB_CLOVA_OCR_SECRET": CLOVA_SECRET,
            "VOINEY_LAB_GOOGLE_VISION_API_KEY": GOOGLE_KEY,
        }
        env.update(overrides)
        return env

    def test_both_one_or_none(self) -> None:
        both = ocr_provider_from_environment(self._env("clova,google"))
        self.assertEqual(both.engine_names, (CLOVA, GOOGLE))
        self.assertEqual(ocr_provider_from_environment(self._env("google")).engine_names, (GOOGLE,))
        self.assertEqual(ocr_provider_from_environment(self._env(" Google , clova ")).engine_names, (CLOVA, GOOGLE))
        self.assertIsNone(ocr_provider_from_environment(self._env("")))
        self.assertIsNone(ocr_provider_from_environment({}))

    def test_an_engine_without_credentials_is_left_out_and_not_logged(self) -> None:
        with self.assertLogs("voiney_lab.protocol_ocr", level=logging.WARNING) as logs:
            provider = ocr_provider_from_environment(
                self._env("clova,google", VOINEY_LAB_CLOVA_OCR_SECRET="")
            )
        self.assertEqual(provider.engine_names, (GOOGLE,))
        joined = "\n".join(logs.output)
        self.assertNotIn(GOOGLE_KEY, joined)
        self.assertNotIn("clova.invalid", joined)
        self.assertIsNone(
            ocr_provider_from_environment(
                self._env("google", VOINEY_LAB_GOOGLE_VISION_API_KEY="")
            )
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
