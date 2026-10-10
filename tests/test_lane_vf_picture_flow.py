"""Lane VF, decision 8 (2026-10-10): a picture the source does not have is drawn at once.

In the voice test of 2026-10-10 "젤 플러그 그림 보여줄 수 있어?" was answered
"이 단계 원문에는 그림이 없어요. … '그림으로 그려 줘'라고 말해 주세요." and the
drawing came only on a second request. Decision: with no figure in the
source, draw at once. With drawing on, the server now turns such a turn into
a drawing -- "원문에는 그림이 없어서 그려 드릴게요." and lane WV's checks and
display as they are; a photograph asked for ("사진", "실제 모습") of a thing
named is looked up on the web instead when the web is on; with both off the
rules' guidance stands. Under a source figure the page now offers "그림 크게
보기" (the cut figure, large) and "원본 쪽 보기" (the page as an image, a new
same-origin route). Nothing here changes state.
"""

from __future__ import annotations

import unittest
from dataclasses import replace
from unittest.mock import patch

from tests.protocol_vocabulary_support import build_fixture
from tests.test_lane_wv_source_figures import STEPS, _SourcePdf
from tests.test_screen_cleanup import run_page_script
from voiney_lab import drawn_diagrams as dd
from voiney_lab import server as server_module
from voiney_lab import source_figures
from voiney_lab.curated_protocol import NO_FIGURE_WORDS, CuratedProtocolAction, CuratedProtocolSession, photo_asked
from voiney_lab.runtime_routing import route_curated_runtime_turn
from voiney_lab.web_explanations import WebExplanationSettings


class _Settings:
    """What the fallback reads of the session: the two picture settings."""

    def __init__(self, *, drawing: bool, web: bool) -> None:
        self.drawn_diagram_settings = dd.DrawnDiagramSettings(drawing, timeout_seconds=5.0)
        self.web_explanation_settings = WebExplanationSettings(web)


def _session(fixture=None, step_index: int = 1) -> CuratedProtocolSession:
    session = CuratedProtocolSession(fixture or build_fixture(
        protocol_id="lane-vf-no-figure", title="No figure", steps=STEPS))
    session.activate_configured()
    session.plan("프로토콜 시작해줘", turn_id=1, language="ko", configuration_id=1, generation=1)
    session.current_index = step_index
    return session


def _asked(session, said: str, turn_id: int = 2):
    return route_curated_runtime_turn(
        session, said, turn_id=turn_id, language="ko", configuration_id=1, generation=1).plan


class FallbackTests(unittest.TestCase):

    def test_with_drawing_on_a_missing_figure_is_drawn_at_once(self) -> None:
        session = _session()
        before = session.state()
        plan = _asked(session, "젤 플러그 그림 보여줄 수 있어?")
        self.assertIs(plan.action, CuratedProtocolAction.VISUAL_REQUEST)
        self.assertEqual(plan.visual_kind, "source_figure")
        self.assertEqual(plan.speech_text, NO_FIGURE_WORDS)
        drawn = server_module._picture_fallback(_Settings(drawing=True, web=True), session, plan, "젤 플러그 그림 보여줄 수 있어?", "ko")
        self.assertEqual(drawn.visual_kind, "drawn_diagram")
        self.assertTrue(drawn.speech_text.startswith("원문에는 그림이 없어서 그려 드릴게요. "), drawn.speech_text)
        self.assertIn(server_module._DRAWING_ON_WORDS, drawn.speech_text)
        self.assertNotIn("그려 줘'라고 말해 주세요", drawn.speech_text)
        self.assertIn("원문에는 그림이 없어서 그려 드릴게요.", drawn.display_text)
        self.assertFalse(drawn.state_changed)
        self.assertTrue(server_module._drawing_wanted(_Settings(drawing=True, web=True), session, drawn))
        # The lane WV words are not added a second time.
        self.assertEqual(server_module._drawing_words(drawn, "ko").speech_text, drawn.speech_text)
        self.assertEqual(session.state(), before)

    def test_with_drawing_off_the_guidance_stands(self) -> None:
        session = _session()
        plan = _asked(session, "그림 보여줘")
        same = server_module._picture_fallback(_Settings(drawing=False, web=False), session, plan, "그림 보여줘", "ko")
        self.assertIs(same, plan)
        self.assertEqual(same.speech_text, NO_FIGURE_WORDS)
        self.assertFalse(server_module._drawing_wanted(_Settings(drawing=False, web=False), session, same))

    def test_a_photograph_of_a_thing_named_is_looked_up_when_the_web_is_on(self) -> None:
        session = _session()
        said = "젤 밴드 사진 보여줘"
        self.assertTrue(photo_asked(said))
        self.assertFalse(photo_asked("젤 플러그 그림 보여줄 수 있어?"))
        plan = _asked(session, said)
        self.assertEqual(plan.visual_kind, "source_figure")
        web = server_module._picture_fallback(_Settings(drawing=True, web=True), session, plan, said, "ko")
        self.assertEqual(web.visual_kind, "web_lookup")
        self.assertEqual(web.requested_entities, ("젤 밴드",))
        self.assertTrue(web.speech_text.startswith("원문에는 사진이 없어서 젤 밴드는 웹에서 찾아볼게요."), web.speech_text)
        self.assertTrue(server_module._web_lookup_wanted(_Settings(drawing=True, web=True), session, web))
        self.assertEqual(server_module._web_lookup_words(session, web, said, "ko").speech_text, web.speech_text)
        # The web off: drawn instead. No thing named ("사진 보여줘"): drawn.
        drawn = server_module._picture_fallback(_Settings(drawing=True, web=False), session, plan, said, "ko")
        self.assertEqual(drawn.visual_kind, "drawn_diagram")
        bare = _asked(session, "사진 보여줘", 3)
        drawn = server_module._picture_fallback(_Settings(drawing=True, web=True), session, bare, "사진 보여줘", "ko")
        self.assertEqual(drawn.visual_kind, "drawn_diagram")
        # "웹 찾아보기" turned off by the experimenter: drawn.
        session.apply_experimenter_settings({"web_lookup": "off"})
        drawn = server_module._picture_fallback(_Settings(drawing=True, web=True), session, plan, said, "ko")
        self.assertEqual(drawn.visual_kind, "drawn_diagram")

    def test_a_value_question_a_drawing_request_or_another_turn_is_untouched(self) -> None:
        session = _session()
        for said in ("그림에 나온 용액 얼마나 넣어?", "그림으로 그려 줘", "이 단계 뭐야?", "써모믹서가 어떻게 생겼어?"):
            with self.subTest(said=said):
                plan = _asked(session, said)
                self.assertIs(server_module._picture_fallback(_Settings(drawing=True, web=True), session, plan, said, "ko"), plan)

    def test_the_server_reads_the_fallback_before_it_decides_what_to_run(self) -> None:
        import inspect

        source = inspect.getsource(server_module.run_turn)
        self.assertLess(source.index("plan=_picture_fallback(session,curated,plan,transcript,turn_language)"),
                        source.index("web_lookup_now=_web_lookup_wanted(session,curated,plan)"))


class WithAFigureTests(_SourcePdf):

    def test_a_step_with_a_figure_on_its_page_is_not_drawn(self) -> None:
        session = _session(self.fixture(), step_index=0)
        plan = _asked(session, "그림 보여줘")
        self.assertTrue(plan.speech_text.startswith("화면에 원문 그림을 띄웠어요."))
        same = server_module._picture_fallback(_Settings(drawing=True, web=True), session, plan, "그림 보여줘", "ko")
        self.assertIs(same, plan)

    def test_the_page_image_route_serves_the_page_as_png(self) -> None:
        from fastapi.testclient import TestClient

        fixture = self.fixture()
        with patch.object(server_module, "_scope_catalog_resource", lambda protocol_id: None), \
             patch.object(server_module, "server_config", lambda: None), \
             patch.object(server_module, "_configured_candidate_fixture", lambda config: fixture):
            client = TestClient(server_module.app)
            response = client.get(
                f"/api/protocols/{fixture.protocol_id}/revisions/{fixture.revision_id}/source-pages/1/image")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.headers["content-type"], "image/png")
            self.assertTrue(response.content.startswith(b"\x89PNG\r\n\x1a\n"))
            self.assertEqual(response.headers["x-protocol-source-sha256"], self.sha256)
            self.assertEqual(response.headers["x-protocol-source-page"], "1")
            self.assertIn("sandbox", response.headers["content-security-policy"])
            # Cached: the same bytes, no second render.
            self.assertEqual(client.get(
                f"/api/protocols/{fixture.protocol_id}/revisions/{fixture.revision_id}/source-pages/1/image").content, response.content)
            self.assertEqual(client.get(
                f"/api/protocols/{fixture.protocol_id}/revisions/{fixture.revision_id}/source-pages/99/image").status_code, 404)
            self.assertEqual(client.get(
                f"/api/protocols/{fixture.protocol_id}/revisions/other/source-pages/1/image").status_code, 404)
        self.assertIsNone(source_figures.page_image(self.pdf, "c" * 64, 1))
        self.assertIsNone(source_figures.page_image(None, self.sha256, 1))


class PageTests(unittest.TestCase):

    def test_two_links_under_a_source_figure(self) -> None:
        from tests.test_lane_wv_source_figures import PAGE_SETUP

        result = run_page_script(PAGE_SETUP + r"""
await send(state,{source_figures:[figure]});
const anchors=panel().children.flatMap(child=>child.tagName==="figure"?child.children.flatMap(item=>item.className==="source-figure-links"?item.children:[]):[]);
assert(anchors.length===2,`links: ${anchors.length}`);
assert(anchors[0].textContent==="그림 크게 보기"&&anchors[0].href===figure.url&&anchors[0].target==="_blank"&&anchors[0].rel==="noopener",`figure link: ${anchors[0].textContent} ${anchors[0].href}`);
assert(anchors[1].textContent==="원본 쪽 보기"&&anchors[1].href==="/api/protocols/protocol-x/revisions/rev-1/source-pages/1/image"&&anchors[1].target==="_blank",`page link: ${anchors[1].textContent} ${anchors[1].href}`);
assert(!panel().textContent.includes("원문 페이지 크게 보기"),"the old link is still there");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
