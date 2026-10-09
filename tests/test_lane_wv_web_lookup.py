"""Lane WV, decision 2 (2026-10-09): the web for an explanation or a photograph, after the source.

"X가 어떻게 생겼어?", "실제 사진 보여줘", "웹에서 찾아봐": the rules answer
from the uploaded protocol first; the server then asks the web (the OpenAI
Responses web_search tool), takes out every sentence with a value, a safety
instruction or a state claim, shows what is left with its sources, finds a
freely licensed Commons photograph, says one or two sentences while the
answer still plays, and keeps links only in the report. The experimenter
can turn the web off. Every provider here is a fake; nothing leaves the
machine.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import struct
import unittest
import zlib
from types import SimpleNamespace
from unittest.mock import patch

from tests.protocol_vocabulary_support import build_fixture
from tests.test_screen_cleanup import run_page_script
from voiney_lab import server as server_module
from voiney_lab import web_explanations as web
from voiney_lab.curated_protocol import (
    CuratedProtocolAction,
    CuratedProtocolSession,
    experimenter_setting_request,
)
from voiney_lab.runtime_routing import route_curated_runtime_turn

STEPS = (
    "1 Cut the stained band out of the gel and place it in a tube.",
    "2 Wash the band with 500 µL of solution B in the Thermomixer at 800 rpm.",
    "3 Remove and discard the supernatant.",
)


def _png(width: int = 4, height: int = 3) -> bytes:
    raw = b"".join(b"\x00" + b"\x80\x40\x20" * width for _ in range(height))
    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _response(text: str, *, citations=(), sources=(), searches: int = 1, model: str = "gpt-6-luna"):
    """A Responses API reply shaped like the SDK's: a search call, then a message."""

    output = []
    for _ in range(searches):
        output.append(SimpleNamespace(
            type="web_search_call",
            action=SimpleNamespace(type="search", query="q", sources=[
                SimpleNamespace(url=url, title=title) for url, title in sources
            ]),
        ))
    output.append(SimpleNamespace(type="message", content=[SimpleNamespace(
        type="output_text", text=text,
        annotations=[SimpleNamespace(type="url_citation", url=url, title=title, start_index=0, end_index=1)
                     for url, title in citations],
    )]))
    return SimpleNamespace(
        output=output, output_text=text, model=model,
        usage=SimpleNamespace(input_tokens=1234, output_tokens=56),
    )


class _FakeResponses:
    def __init__(self, reply, *, delay: float = 0.0, error: Exception | None = None):
        self.reply = reply
        self.delay = delay
        self.error = error
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.reply


def _client(reply, **kwargs):
    return SimpleNamespace(responses=_FakeResponses(reply, **kwargs))


GOOD = json.dumps({
    "english_term": "Eppendorf ThermoMixer C",
    "spoken": "시료를 섞으면서 온도를 맞추는 탁상형 장비예요. 위에 튜브를 꽂는 블록이 있어요. ([eppendorf.com](https://www.eppendorf.com/x?utm_source=openai))",
    "screen": "흰색 본체에 화면과 버튼이 있습니다. **블록**은 교체할 수 있어요. 보통 15분 정도 돌려요. 장갑을 끼고 다루세요.",
})


class SettingsTests(unittest.TestCase):
    def test_off_by_default_and_on_only_with_the_key(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VOINEY_LAB_WEB_EXPLANATIONS_ENABLED", None)
            self.assertFalse(web.WebExplanationSettings.from_environment().enabled)
            os.environ["VOINEY_LAB_WEB_EXPLANATIONS_ENABLED"] = "true"
            os.environ.pop("OPENAI_API_KEY", None)
            with self.assertRaises(ValueError):
                web.WebExplanationSettings.from_environment()
            os.environ["OPENAI_API_KEY"] = "fake"
            os.environ["VOINEY_LAB_WEB_EXPLANATION_TIMEOUT_SECONDS"] = "12"
            os.environ["VOINEY_LAB_WEB_PHOTOS_ENABLED"] = "false"
            settings = web.WebExplanationSettings.from_environment()
            self.assertEqual((settings.enabled, settings.model, settings.timeout_seconds, settings.photos),
                             (True, "gpt-6-luna", 12.0, False))
            self.assertEqual(settings.public_capability()["backend"], web.WEB_SEARCH_BACKEND)
            os.environ["VOINEY_LAB_WEB_EXPLANATION_TIMEOUT_SECONDS"] = "99"
            with self.assertRaises(ValueError):
                web.WebExplanationSettings.from_environment()
        for name in ("VOINEY_LAB_WEB_EXPLANATIONS_ENABLED", "VOINEY_LAB_WEB_EXPLANATION_TIMEOUT_SECONDS",
                     "VOINEY_LAB_WEB_PHOTOS_ENABLED", "OPENAI_API_KEY"):
            os.environ.pop(name, None)


class ChecksTests(unittest.TestCase):
    """A value, a safety instruction or a state claim never reaches the person."""

    def test_sentences_with_values_or_safety_are_taken_out(self) -> None:
        kept, removed = web.check_sentences(
            "탁상형 장비예요. 보통 15분 정도 돌려요. 장갑을 끼고 다루세요. "
            "온도는 37도로 맞춰요. 위에 블록이 있어요. 다음 단계로 넘어갔어요."
        )
        self.assertEqual(kept, "탁상형 장비예요. 위에 블록이 있어요.")
        self.assertEqual(removed, (
            "보통 15분 정도 돌려요.", "장갑을 끼고 다루세요.", "온도는 37도로 맞춰요.", "다음 단계로 넘어갔어요.",
        ))

    def test_markdown_links_and_marks_are_stripped(self) -> None:
        kept, removed = web.check_sentences(
            "탁상형 장비예요. ([eppendorf.com](https://www.eppendorf.com/x?utm_source=openai)) **블록**이 있어요."
        )
        self.assertEqual(kept, "탁상형 장비예요. 블록이 있어요.")
        self.assertEqual(removed, ())


class ExplainTests(unittest.TestCase):
    def test_the_answer_is_checked_and_cited(self) -> None:
        client = _client(_response(GOOD, citations=(
            ("https://www.eppendorf.com/x?utm_source=openai", "Eppendorf ThermoMixer C"),
            ("https://www.eppendorf.com/x", "duplicate after cleaning"),
        )))
        result = asyncio.run(web.explain_with_web(
            client, web.WebExplanationSettings(True), subject="써모믹서", question="써모믹서가 어떻게 생겼어?",
            step_text=STEPS[1], protocol_names="Thermomixer C", language="ko",
        ))
        self.assertEqual(result.status, "success")
        self.assertTrue(result.shown)
        self.assertEqual(result.spoken, "시료를 섞으면서 온도를 맞추는 탁상형 장비예요. 위에 튜브를 꽂는 블록이 있어요.")
        self.assertEqual(result.screen, "흰색 본체에 화면과 버튼이 있습니다. 블록은 교체할 수 있어요.")
        self.assertEqual(result.removed_sentences, ("보통 15분 정도 돌려요.", "장갑을 끼고 다루세요."))
        self.assertEqual(result.english_term, "Eppendorf ThermoMixer C")
        self.assertEqual([c.url for c in result.citations], ["https://www.eppendorf.com/x"])
        self.assertEqual(result.citations[0].domain, "www.eppendorf.com")
        self.assertEqual(result.search_count, 1)
        self.assertEqual(result.usage, {"input_tokens": 1234, "output_tokens": 56})
        call = client.responses.calls[0]
        self.assertEqual(call["tools"], [{"type": "web_search", "search_context_size": "low"}])
        self.assertEqual(call["model"], "gpt-6-luna")
        self.assertFalse(call["store"])
        self.assertIn("Never state a quantity", call["input"][0]["content"])
        self.assertIn("써모믹서", call["input"][1]["content"])

    def test_no_citation_or_nothing_left_is_not_shown(self) -> None:
        settings = web.WebExplanationSettings(True)
        bare = asyncio.run(web.explain_with_web(_client(_response(GOOD)), settings, subject="s", question="q", step_text="t"))
        self.assertEqual(bare.status, "no_citations")
        self.assertFalse(bare.shown)
        # Sources consulted stand in for citations when the answer carries none.
        sourced = asyncio.run(web.explain_with_web(
            _client(_response(GOOD, sources=(("https://example.org/a", "A"),))), settings, subject="s", question="q", step_text="t"))
        self.assertEqual(sourced.status, "success")
        self.assertEqual(sourced.citations[0].url, "https://example.org/a")
        empty = asyncio.run(web.explain_with_web(
            _client(_response(json.dumps({"english_term": "x", "spoken": "15분 돌려요.", "screen": "37도예요."}),
                              citations=(("https://example.org/a", "A"),))), settings, subject="s", question="q", step_text="t"))
        self.assertEqual(empty.status, "empty")
        self.assertEqual(len(empty.removed_sentences), 2)

    def test_a_late_or_failing_provider_is_reported_not_raised(self) -> None:
        settings = web.WebExplanationSettings(True, timeout_seconds=3.0)
        def too_late(awaitable, timeout):
            awaitable.close()
            raise asyncio.TimeoutError

        with patch.object(web.asyncio, "wait_for", side_effect=too_late):
            late = asyncio.run(web.explain_with_web(_client(_response(GOOD)), settings, subject="s", question="q", step_text="t"))
        self.assertEqual(late.status, "timeout")
        failed = asyncio.run(web.explain_with_web(
            _client(_response(GOOD), error=RuntimeError("down")), settings, subject="s", question="q", step_text="t"))
        self.assertEqual(failed.status, "provider_error")
        plain = asyncio.run(web.explain_with_web(
            _client(_response("not json at all.", citations=(("https://example.org/a", "A"),))), settings,
            subject="s", question="q", step_text="t"))
        self.assertEqual(plain.status, "success")
        self.assertEqual(plain.screen, "not json at all.")


class _FakeHttp:
    """httpx.AsyncClient with two answers: the API page list, then the file bytes."""

    def __init__(self, pages, files):
        self.pages = pages
        self.files = files
        self.calls: list[tuple[str, dict]] = []

    async def get(self, url, params=None, headers=None):
        self.calls.append((url, {"params": params, "headers": headers}))
        if url == web.COMMONS_API:
            return SimpleNamespace(status_code=200, json=lambda: {"query": {"pages": self.pages}})
        if url in self.files:
            return SimpleNamespace(status_code=200, content=self.files[url])
        return SimpleNamespace(status_code=404, content=b"")

    async def aclose(self):
        pass


def _page(title, thumb, licence, *, mime="image/png", artist="<p>Courtesy of NIAID</p>", licence_url=""):
    return {"title": f"File:{title}", "imageinfo": [{
        "mime": mime, "width": 1000, "height": 800,
        "thumburl": thumb, "descriptionurl": f"https://commons.wikimedia.org/wiki/File:{title}",
        "extmetadata": {
            **({"LicenseShortName": {"value": licence}} if licence else {}),
            "Artist": {"value": artist},
            **({"LicenseUrl": {"value": licence_url}} if licence_url else {}),
        },
    }]}


class PhotoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = web.PhotoRegistry()
        self.safe = patch.object(web, "_is_safe_remote_url",
                                 lambda url, hosts: url.startswith("https://") and any(f"//{h}/" in url for h in hosts))
        self.safe.start()

    def tearDown(self) -> None:
        self.safe.stop()

    def test_only_a_freely_licensed_picture_from_wikimedia_is_fetched_and_kept(self) -> None:
        png = _png()
        thumb = "https://thumb.wikimedia.org/wikipedia/commons/thumb/x/y/T.png/640px-T.png?utm_source=commons"
        http = _FakeHttp([
            _page("Unlicensed.png", "https://thumb.wikimedia.org/a.png", ""),
            _page("Fair.png", "https://thumb.wikimedia.org/b.png", "Fair use"),
            _page("Elsewhere.png", "https://example.com/c.png", "CC BY-SA 4.0"),
            _page("Vector.svg", "https://thumb.wikimedia.org/d.png", "CC0", mime="image/svg+xml"),
            _page("Thermomixer.png", thumb, "Public domain", licence_url="https://creativecommons.org/publicdomain/mark/1.0/"),
        ], {"https://thumb.wikimedia.org/wikipedia/commons/thumb/x/y/T.png/640px-T.png": png})
        photo = asyncio.run(web.find_commons_photo("Thermomixer", http_client=http, registry=self.registry))
        self.assertIsNotNone(photo)
        self.assertEqual(photo.title, "Thermomixer.png")
        self.assertEqual(photo.licence, "Public domain")
        self.assertEqual(photo.author, "Courtesy of NIAID")
        self.assertEqual(photo.mime_type, "image/png")
        self.assertEqual((photo.width_px, photo.height_px), (4, 3))
        self.assertEqual(photo.asset_id, hashlib.sha256(png).hexdigest())
        self.assertEqual(photo.public_dict()["url"], f"/api/web-visuals/{photo.asset_id}")
        self.assertIs(self.registry.get(photo.asset_id), photo)
        self.assertIsNone(self.registry.get("../x"))
        # The API etiquette: a named User-Agent on every request.
        for _url, call in http.calls:
            self.assertEqual(call["headers"]["User-Agent"], web.COMMONS_USER_AGENT)
        self.assertEqual(http.calls[0][1]["params"]["gsrsearch"], "filetype:bitmap Thermomixer")
        # Only the one acceptable file was fetched.
        self.assertEqual(len(http.calls), 2)

    def test_bad_bytes_or_an_empty_term_give_no_photo(self) -> None:
        http = _FakeHttp([_page("T.png", "https://thumb.wikimedia.org/t.png", "CC BY 4.0")],
                         {"https://thumb.wikimedia.org/t.png": b"<html>not a picture</html>"})
        self.assertIsNone(asyncio.run(web.find_commons_photo("T", http_client=http, registry=self.registry)))
        self.assertIsNone(asyncio.run(web.find_commons_photo("   ", http_client=http, registry=self.registry)))


class _Session:
    def __init__(self, *, enabled=True, held=True):
        self.web_explanation_settings = web.WebExplanationSettings(enabled, timeout_seconds=5.0)
        self.accepted_configuration_id = 7
        self.held_audio_complete = {(2, 1)} if held else set()
        self.experiment_report_store = None
        self.spoken: list[str] = []
        self.current = True

    def is_current(self, turn_id, generation):
        return self.current

    def remember_spoken(self, text):
        self.spoken.append(text)


class _Sender:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []
        self.segments: list[tuple[int, int, int]] = []

    async def text(self, kind, **fields):
        self.events.append((kind, fields))

    async def segment(self, turn_id, index, frames, generation=None):
        self.segments.append((turn_id, index, len(frames)))


def _curated(step_index: int = 1) -> CuratedProtocolSession:
    session = CuratedProtocolSession(build_fixture(
        protocol_id="lane-wv-web", title="Fictional lane WV protocol", steps=STEPS,
        equipment=("Thermomixer C",),
    ))
    session.activate_configured()
    session.plan("프로토콜 시작해줘", turn_id=1, language="ko", configuration_id=1, generation=1)
    session.current_index = step_index
    return session


class ServerWiringTests(unittest.TestCase):
    def test_the_web_is_asked_only_when_on_and_asked_for(self) -> None:
        curated = _curated()
        plan = route_curated_runtime_turn(curated, "써모믹서가 어떻게 생겼어?", turn_id=2, language="ko",
                                          configuration_id=1, generation=1).plan
        self.assertEqual(plan.visual_kind, "web_lookup")
        self.assertTrue(server_module._web_lookup_wanted(_Session(), curated, plan))
        self.assertFalse(server_module._web_lookup_wanted(_Session(enabled=False), curated, plan))
        curated.apply_experimenter_settings({"web_lookup": "off"})
        self.assertFalse(server_module._web_lookup_wanted(_Session(), curated, plan))
        curated.apply_experimenter_settings({"web_lookup": "on"})
        figure = route_curated_runtime_turn(curated, "그림 보여줘", turn_id=3, language="ko",
                                            configuration_id=1, generation=1).plan
        self.assertFalse(server_module._web_lookup_wanted(_Session(), curated, figure))

    def test_the_words_say_the_web_is_being_asked(self) -> None:
        curated = _curated()
        plan = route_curated_runtime_turn(curated, "써모믹서가 어떻게 생겼어?", turn_id=2, language="ko",
                                          configuration_id=1, generation=1).plan
        self.assertIn("꺼져 있어", plan.speech_text)
        rewritten = server_module._web_lookup_words(curated, plan, "써모믹서가 어떻게 생겼어?", "ko")
        self.assertEqual(
            rewritten.speech_text,
            "써모믹서는 웹에서 찾아볼게요. 찾으면 화면에 출처와 함께 띄울게요. 값과 안전 지시는 원문만 따라요.",
        )
        self.assertNotIn("꺼져 있어", rewritten.display_text)
        self.assertIn("웹에서 찾아볼게요", rewritten.display_text)

    def test_the_lookup_sends_its_events_speaks_once_and_keeps_links_only(self) -> None:
        curated = _curated()
        plan = route_curated_runtime_turn(curated, "써모믹서가 어떻게 생겼어?", turn_id=2, language="ko",
                                          configuration_id=1, generation=1).plan
        session = _Session()
        appended: list[dict] = []
        session.experiment_report_store = SimpleNamespace(append_event=lambda report_id, **fields: appended.append({"report_id": report_id, **fields}))
        sender = _Sender()
        png = _png()
        photo = web.WebPhoto(hashlib.sha256(png).hexdigest(), "image/png", png, 4, 3, "Thermomixer.png",
                             "https://commons.wikimedia.org/wiki/File:Thermomixer.png", "Courtesy of NIAID",
                             "Public domain", "", "https://thumb.wikimedia.org/t.png")
        client = _client(_response(GOOD, citations=(("https://www.eppendorf.com/x", "ThermoMixer"),)))

        async def fake_photo(term, **kwargs):
            self.assertEqual(term, "Eppendorf ThermoMixer C")
            return photo

        with patch.object(server_module, "AsyncOpenAI", return_value=client), \
             patch.object(server_module, "require_env", return_value="fake"), \
             patch.object(server_module, "find_commons_photo", fake_photo), \
             patch.object(server_module, "synthesize", return_value=b"\0\0" * 320), \
             patch.object(server_module, "_open_experiment_report", return_value={"report_id": "report-1"}), \
             patch.object(server_module, "_SPEAKING_SESSION") as speaking:
            speaking.get.return_value = session
            asyncio.run(server_module._run_web_lookup(
                session=session, sender=sender, turn_id=2, generation=1, curated=curated, plan=plan,
                transcript="써모믹서가 어떻게 생겼어?", language="ko", speak_by=1e12, clock=lambda: 0.0,
            ))
        kinds = [kind for kind, _ in sender.events]
        self.assertEqual(kinds[0], "protocol.web.state")
        self.assertEqual(sender.events[0][1]["subject"], "써모믹서")
        self.assertIn("protocol.web.result", kinds)
        result = dict(sender.events[kinds.index("protocol.web.result")][1])
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["screen"], "흰색 본체에 화면과 버튼이 있습니다. 블록은 교체할 수 있어요.")
        self.assertEqual(result["citations"], [{"title": "ThermoMixer", "url": "https://www.eppendorf.com/x", "domain": "www.eppendorf.com"}])
        self.assertEqual(result["photo"]["licence"], "Public domain")
        self.assertEqual(result["removed_count"], 2)
        self.assertTrue(result["spoken_aloud"])
        self.assertEqual(result["configuration_id"], 7)
        # Said once, as segment 1, with the "on the screen" tail, and audio.complete after it.
        self.assertEqual(sender.segments, [(2, 1, 1)])
        self.assertEqual(session.spoken, [
            "시료를 섞으면서 온도를 맞추는 탁상형 장비예요. 위에 튜브를 꽂는 블록이 있어요. 자세한 건 화면에 출처와 함께 띄웠어요."])
        self.assertEqual([f["segment_count"] for k, f in sender.events if k == "audio.complete"], [2])
        self.assertEqual(session.held_audio_complete, set())
        # The report keeps links, not words.
        self.assertEqual(len(appended), 1)
        self.assertEqual(appended[0]["event_type"], "web_reference")
        self.assertEqual(appended[0]["payload"]["links"], ["https://www.eppendorf.com/x"])
        self.assertFalse(appended[0]["payload"]["text_kept"])
        self.assertNotIn("블록", json.dumps(appended[0], ensure_ascii=False))

    def test_a_late_answer_is_screen_only_and_a_failure_releases_the_audio(self) -> None:
        curated = _curated()
        plan = route_curated_runtime_turn(curated, "써모믹서가 어떻게 생겼어?", turn_id=2, language="ko",
                                          configuration_id=1, generation=1).plan
        session = _Session()
        sender = _Sender()
        client = _client(_response(GOOD, citations=(("https://www.eppendorf.com/x", "ThermoMixer"),)))
        async def no_photo(term, **kwargs):
            return None

        with patch.object(server_module, "AsyncOpenAI", return_value=client), \
             patch.object(server_module, "require_env", return_value="fake"), \
             patch.object(server_module, "find_commons_photo", no_photo):
            asyncio.run(server_module._run_web_lookup(
                session=session, sender=sender, turn_id=2, generation=1, curated=curated, plan=plan,
                transcript="써모믹서가 어떻게 생겼어?", language="ko", speak_by=0.0, clock=lambda: 1.0,
            ))
        result = next(f for k, f in sender.events if k == "protocol.web.result")
        self.assertEqual(result["status"], "success")
        self.assertFalse(result["spoken_aloud"])
        self.assertIsNone(result["photo"])
        self.assertEqual(sender.segments, [])
        self.assertEqual([f["segment_count"] for k, f in sender.events if k == "audio.complete"], [1])
        # A provider failure: a result that says so, and the held audio released.
        session = _Session()
        sender = _Sender()
        with patch.object(server_module, "AsyncOpenAI", side_effect=RuntimeError("no client")), \
             patch.object(server_module, "require_env", return_value="fake"):
            asyncio.run(server_module._run_web_lookup(
                session=session, sender=sender, turn_id=2, generation=1, curated=curated, plan=plan,
                transcript="써모믹서가 어떻게 생겼어?", language="ko", speak_by=1e12, clock=lambda: 0.0,
            ))
        result = next(f for k, f in sender.events if k == "protocol.web.result")
        self.assertEqual(result["status"], "provider_error")
        self.assertEqual([f["segment_count"] for k, f in sender.events if k == "audio.complete"], [1])

    def test_the_photo_route_serves_the_checked_bytes(self) -> None:
        from fastapi.testclient import TestClient

        png = _png()
        photo = web.WebPhoto(hashlib.sha256(png).hexdigest(), "image/png", png, 4, 3, "T.png",
                             "https://commons.wikimedia.org/wiki/File:T.png", "A", "CC0", "", "https://thumb.wikimedia.org/t.png")
        web.WEB_PHOTOS.keep(photo)
        try:
            client = TestClient(server_module.app)
            response = client.get(f"/api/web-visuals/{photo.asset_id}")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content, png)
            self.assertEqual(response.headers["content-type"], "image/png")
            self.assertIn("sandbox", response.headers["content-security-policy"])
            self.assertEqual(response.headers["x-web-visual-licence"], "CC0")
            self.assertEqual(client.get("/api/web-visuals/" + "0" * 64).status_code, 404)
            self.assertEqual(client.get("/api/web-visuals/not-an-id").status_code, 404)
        finally:
            web.WEB_PHOTOS.clear()

    def test_the_experimenter_setting_is_kept_and_read_back(self) -> None:
        with patch.object(server_module, "_workspace_settings", return_value=SimpleNamespace(enabled=False)), \
             patch.dict(server_module._EXPERIMENTER_SETTINGS_MEMORY, {}, clear=True):
            self.assertEqual(server_module._load_experimenter_settings()["web_lookup"], "on")
            saved = server_module._save_experimenter_settings({"web_lookup": "off"}, "screen")
            self.assertEqual(saved["web_lookup"], "off")
            self.assertEqual(server_module._load_experimenter_settings()["web_lookup"], "off")
            with self.assertRaises(Exception):
                server_module._save_experimenter_settings({"web_lookup": "maybe"}, "screen")


class SessionSettingTests(unittest.TestCase):
    def test_the_setting_is_said_and_changes_the_words(self) -> None:
        self.assertEqual(experimenter_setting_request("웹 찾아보기 꺼 줘"), {"web_lookup": "off"})
        self.assertEqual(experimenter_setting_request("웹 검색은 켜 줘"), {"web_lookup": "on"})
        self.assertIsNone(experimenter_setting_request("웹 찾아보기 꺼도 돼?"))
        curated = _curated()
        plan = curated.plan("웹 찾아보기 꺼 줘", turn_id=2, language="ko", configuration_id=1, generation=1)
        self.assertEqual(plan.setting_change, {"web_lookup": "off"})
        self.assertEqual(curated.last_front_rule, "experimenter_setting")
        self.assertEqual(curated.experimenter_settings()["web_lookup"], "off")
        self.assertFalse(plan.state_changed)
        asked = route_curated_runtime_turn(curated, "써모믹서가 어떻게 생겼어?", turn_id=3, language="ko",
                                           configuration_id=1, generation=1).plan
        self.assertIs(asked.action, CuratedProtocolAction.VISUAL_REQUEST)
        self.assertEqual(asked.speech_text, "웹 찾아보기를 꺼 두셔서 써모믹서는 찾아보지 않았어요. 설정에서 켤 수 있어요.")
        again = curated.plan("웹 찾아보기 꺼 줘", turn_id=4, language="ko", configuration_id=1, generation=1)
        self.assertEqual(again.intent_kind, "experimenter_setting_unchanged")
        back = curated.plan("웹 찾아보기 켜 줘", turn_id=5, language="ko", configuration_id=1, generation=1)
        self.assertEqual(back.setting_change, {"web_lookup": "on"})


PAGE_SETUP = r"""
acceptedSessionConfiguration={configuration_id:7,mode:"cascade",language:"ko",protocol_id:"protocol-x",revision_id:"rev-1"};
const state={attached:true,protocol_id:"protocol-x",revision_id:"rev-1",display_name:"Fictional",development_only:false,readiness_status:"guidance_ready",active:true,current_step_label:"2",current_step_id:"step-2",total_steps:3,at_final_step:false,block_reason:null,revision:1,
 display_summary:"2 Wash the band.",primary_summary:"2단계: 밴드를 씻습니다.",source_language:"en",spoken_summary:"2단계입니다.",source_sha256:"a".repeat(64),source_filename:"source.pdf",
 warning_texts:[],warning_presentations:[],visual_assets:[],visual_status:"unavailable",source_page_refs:[1],workflow_status:"active"};
await onMessage({data:JSON.stringify({type:"protocol.fixture.state",configuration_id:7,state,screen:{source_figures:[]}})},sessionGeneration,socket);
await onMessage({data:JSON.stringify({type:"speech.start",turn_id:2,generation:sessionGeneration})},sessionGeneration,socket);
turnServerGenerations.set(turnBrowserKey(2,sessionGeneration),sessionGeneration);
const sendWeb=(type,fields)=>onMessage({data:JSON.stringify({type,configuration_id:7,turn_id:2,generation:sessionGeneration,...fields})},sessionGeneration,socket);
const photo={asset_id:"b".repeat(64),url:"/api/web-visuals/"+"b".repeat(64),mime_type:"image/png",width_px:640,height_px:480,title:"Thermomixer.png",page_url:"https://commons.wikimedia.org/wiki/File:Thermomixer.png",author:"Courtesy of NIAID",licence:"Public domain",licence_url:"",source:"Wikimedia Commons",label:"웹 자료 · 출처 Wikimedia Commons"};
const result={status:"success",subject:"써모믹서",spoken:"탁상형 장비예요.",screen:"흰색 본체에 화면과 버튼이 있습니다.",english_term:"ThermoMixer",citations:[{title:"ThermoMixer C",url:"https://www.eppendorf.com/x",domain:"www.eppendorf.com"}],photo,removed_count:2,spoken_aloud:true,model:"gpt-6-luna",elapsed_ms:7000,search_count:1};
const panel=()=>node("procedure-web");
const deep=item=>(item._text||"")+" "+item.children.map(deep).join(" ");
"""


class PageTests(unittest.TestCase):
    def test_the_setting_is_on_the_screen_and_saved_through_the_server(self) -> None:
        html = server_module.PROJECT_ROOT.joinpath("src/voiney_lab/static/index.html").read_text(encoding="utf-8")
        self.assertIn('id="web-lookup"', html)
        self.assertIn('saveExperimenterSetting("web_lookup"', html)
        result = run_page_script(r"""
renderExperimenterSettings({confirm_mode:"readback",question_timing:"before_start",web_lookup:"off"});
assert(node("web-lookup").value==="off","the setting was not rendered");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_web_result_is_drawn_with_its_sources_and_licence(self) -> None:
        result = run_page_script(PAGE_SETUP + r"""
await sendWeb("protocol.web.state",{status:"running",subject:"써모믹서"});
assert(turnNode(2,sessionGeneration).querySelector(".filler-status").textContent.includes("웹에서 '써모믹서'를 찾아보는 중"),"running line missing");
await sendWeb("protocol.web.result",result);
const text=deep(panel());
assert(!panel().hidden&&text.includes("흰색 본체에 화면과 버튼이 있습니다."),`web text missing: ${text}`);
assert(text.includes("웹 자료 · 출처"),"web label missing");
assert(text.includes("ThermoMixer C · www.eppendorf.com"),"citation link missing");
assert(text.includes("Public domain")&&text.includes("Courtesy of NIAID")&&text.includes("실험 PDF 원문 사진이 아니에요"),`licence line missing: ${text}`);
assert(text.includes("문장 2개는 뺐어요"),"removed count missing");
const imgs=[];const walk=item=>{if(item.tagName==="img")imgs.push(item);item.children.forEach(walk)};walk(panel());
assert(imgs.length===1&&imgs[0].src===photo.url,"photo not drawn from the same-origin route");
assert(turnNode(2,sessionGeneration).querySelector(".turn-visual").textContent.includes("웹 자료 · 출처와 함께"),"turn line missing");
assert(!turnNode(2,sessionGeneration).querySelector(".filler-status").textContent,"running line not cleared");
await sendWeb("protocol.web.result",{...result,screen:"다른 글"});
assert(!deep(panel()).includes("다른 글"),"a second terminal result was accepted");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_photo_from_elsewhere_or_without_a_licence_is_not_drawn(self) -> None:
        result = run_page_script(PAGE_SETUP + r"""
await sendWeb("protocol.web.result",{...result,photo:{...photo,url:"https://thumb.wikimedia.org/t.png"}});
const imgs=[];const walk=item=>{if(item.tagName==="img")imgs.push(item);item.children.forEach(walk)};walk(panel());
assert(imgs.length===0,"a remote photo url was trusted");
assert(deep(panel()).includes("흰색 본체"),"the text was lost with the photo");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_failed_lookup_says_so_in_the_turn_and_a_new_step_clears_the_panel(self) -> None:
        result = run_page_script(PAGE_SETUP + r"""
await sendWeb("protocol.web.result",{status:"timeout",subject:"써모믹서"});
assert(turnNode(2,sessionGeneration).querySelector(".turn-visual").textContent.includes("시간 초과"),"timeout line missing");
assert(panel().hidden,"panel shown without a result");
await onMessage({data:JSON.stringify({type:"speech.start",turn_id:3,generation:sessionGeneration})},sessionGeneration,socket);
turnServerGenerations.set(turnBrowserKey(3,sessionGeneration),sessionGeneration);
await onMessage({data:JSON.stringify({type:"protocol.web.result",configuration_id:7,turn_id:3,generation:sessionGeneration,...result})},sessionGeneration,socket);
assert(!panel().hidden,"result not drawn");
await onMessage({data:JSON.stringify({type:"protocol.fixture.state",configuration_id:7,state:{...state,revision:2,current_step_label:"3",current_step_id:"step-3"},screen:{source_figures:[]}})},sessionGeneration,socket);
assert(panel().hidden&&panel().children.length===0,"the next step kept the web result");
""")
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
