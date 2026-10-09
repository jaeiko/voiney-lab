"""Lane WV, decision 3 (2026-10-09): a diagram drawn from the step's words, on request.

"그림으로 그려 줘": a text model writes an SVG of the step; the server
rebuilds it from an allow-list (no script, link, style, image or external
reference), takes out every number that is not one of the step's own
values and every safety instruction, writes "AI 가 그린 그림 — 실제와
다를 수 있음" into the picture, serves it same-origin and shows it only
for the step it was drawn for. The model is a fake here.
"""

from __future__ import annotations

import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests.protocol_vocabulary_support import build_fixture
from tests.test_screen_cleanup import run_page_script
from voiney_lab import drawn_diagrams as dd
from voiney_lab import server as server_module
from voiney_lab.curated_protocol import CuratedProtocolSession
from voiney_lab.runtime_routing import route_curated_runtime_turn

STEPS = (
    "1 Cut the stained band out of the gel and place it in a tube.",
    "2 Wash the band with 500 µL of solution B for 15 min at 37°C.",
    "3 Remove and discard the supernatant.",
)

RAW_SVG = """Here is the drawing:
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 400" width="640" height="400" onload="alert(1)">
  <style>text{fill:red}</style>
  <script>alert(1)</script>
  <defs><marker id="arrow" refX="5" refY="5" markerWidth="10" markerHeight="10" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#333"/></marker></defs>
  <rect x="40" y="60" width="120" height="200" fill="#dbe9ff" stroke="#334" stroke-width="2" style="fill:url(http://evil)"/>
  <image href="https://evil.example/x.png" x="0" y="0" width="10" height="10"/>
  <a href="https://evil.example"><text x="50" y="40" font-size="16">용액 B 500 µL</text></a>
  <text x="50" y="300" font-size="14">15분 동안 37°C 에서, 세게 흔들지 말고 2번 반복</text>
  <text x="50" y="330" font-size="14">장갑을 끼고 다루세요</text>
  <line x1="200" y1="150" x2="300" y2="150" stroke="#333" marker-end="url(#arrow)"/>
  <foreignObject x="0" y="0" width="10" height="10"><div>html</div></foreignObject>
  <g transform="translate(300,80)"><circle cx="40" cy="40" r="30" fill="#fff" stroke="#333"/></g>
</svg>
Done."""


def _response(text: str, model: str = "gpt-6-luna"):
    return SimpleNamespace(
        output=[SimpleNamespace(type="message", content=[SimpleNamespace(type="output_text", text=text, annotations=[])])],
        output_text=text, model=model, usage=SimpleNamespace(input_tokens=300, output_tokens=1800),
    )


class _FakeResponses:
    def __init__(self, reply, error: Exception | None = None):
        self.reply = reply
        self.error = error
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.reply


def _client(reply, **kwargs):
    return SimpleNamespace(responses=_FakeResponses(reply, **kwargs))


class SettingsTests(unittest.TestCase):
    def test_off_by_default_and_on_only_with_the_key(self) -> None:
        for name in ("VOINEY_LAB_DRAWN_DIAGRAMS_ENABLED", "VOINEY_LAB_DRAWN_DIAGRAM_TIMEOUT_SECONDS", "OPENAI_API_KEY"):
            os.environ.pop(name, None)
        self.assertFalse(dd.DrawnDiagramSettings.from_environment().enabled)
        os.environ["VOINEY_LAB_DRAWN_DIAGRAMS_ENABLED"] = "true"
        with self.assertRaises(ValueError):
            dd.DrawnDiagramSettings.from_environment()
        os.environ["OPENAI_API_KEY"] = "fake"
        os.environ["VOINEY_LAB_DRAWN_DIAGRAM_TIMEOUT_SECONDS"] = "25"
        settings = dd.DrawnDiagramSettings.from_environment()
        self.assertEqual((settings.enabled, settings.model, settings.timeout_seconds), (True, "gpt-6-luna", 25.0))
        self.assertEqual(settings.public_capability()["label"], dd.DRAWN_LABEL)
        for name in ("VOINEY_LAB_DRAWN_DIAGRAMS_ENABLED", "VOINEY_LAB_DRAWN_DIAGRAM_TIMEOUT_SECONDS", "OPENAI_API_KEY"):
            os.environ.pop(name, None)


class SanitizeTests(unittest.TestCase):
    """What the model wrote is rebuilt; only the step's own numbers survive."""

    def test_the_drawing_is_rebuilt_from_the_allow_list(self) -> None:
        numbers = dd.source_numbers(STEPS[1])
        self.assertEqual(numbers, frozenset({"2", "500", "15", "37"}))
        content, removed_numbers, removed_texts = dd.sanitize_svg(RAW_SVG, numbers)
        svg = content.decode("utf-8")
        for gone in ("script", "style", "onload", "image", "href", "foreignObject", "evil", "alert", "url(http"):
            self.assertNotIn(gone, svg, gone)
        self.assertIn("<rect", svg)
        self.assertIn("<circle", svg)
        self.assertIn("<marker", svg)
        self.assertIn('marker-end="url(#arrow)"', svg)
        # The step's own values stay; a number of the model's own goes.
        self.assertIn("용액 B 500 µL", svg)
        self.assertIn("15분 동안 37°C 에서, 세게 흔들지 말고 2번 반복", svg)
        self.assertEqual(removed_numbers, ())
        # The safety instruction is taken out of the picture.
        self.assertNotIn("장갑", svg)
        self.assertEqual(removed_texts, ("장갑을 끼고 다루세요",))
        # The label is in the picture itself, and the canvas is fixed.
        self.assertIn(dd.DRAWN_LABEL, svg)
        self.assertIn('width="640"', svg)
        self.assertIn('viewBox="0 0 640 400"', svg)
        self.assertTrue(svg.startswith("<svg"))
        self.assertIn('xmlns="http://www.w3.org/2000/svg"', svg)

    def test_a_number_the_step_does_not_say_is_taken_out(self) -> None:
        raw = '<svg xmlns="http://www.w3.org/2000/svg"><text x="1" y="2">800 rpm, 2 mL, 500 µL</text></svg>'
        content, removed_numbers, removed_texts = dd.sanitize_svg(raw, frozenset({"500"}))
        self.assertIn("rpm,  mL, 500 µL", content.decode("utf-8"))
        self.assertEqual(removed_numbers, ("800", "2"))
        self.assertEqual(removed_texts, ())

    def test_what_is_not_a_drawing_is_refused(self) -> None:
        for raw in ("no svg here", "<svg xmlns='http://www.w3.org/2000/svg'></svg>",
                    "<!DOCTYPE svg [<!ENTITY x 'y'>]><svg><rect/></svg>", "<svg><rect></svg>",
                    "<svg xmlns='http://www.w3.org/2000/svg'><script>x</script></svg>", ""):
            with self.subTest(raw=raw[:30]):
                with self.assertRaises(ValueError):
                    dd.sanitize_svg(raw, frozenset())
        with self.assertRaises(ValueError):
            dd.sanitize_svg("<svg xmlns='http://www.w3.org/2000/svg'><rect/>" + "x" * (dd.SVG_MAX_BYTES) + "</svg>", frozenset())


class DrawTests(unittest.TestCase):
    def test_a_step_is_drawn_once_and_checked(self) -> None:
        registry = dd.DiagramRegistry()
        client = _client(_response(RAW_SVG))
        settings = dd.DrawnDiagramSettings(True)
        result = asyncio.run(dd.draw_step_diagram(
            client, settings, step_id="step-2", step_label="2", step_text=STEPS[1],
            source_sha256="a" * 64, extra_texts=("Vortex twice.",), registry=registry))
        self.assertEqual(result.status, "success")
        diagram = result.diagram
        self.assertIsNotNone(diagram)
        self.assertEqual(diagram.removed_texts, ("장갑을 끼고 다루세요",))
        self.assertEqual(diagram.public_dict()["url"], f"/api/drawn-visuals/{diagram.asset_id}")
        self.assertEqual(diagram.public_dict()["label"], dd.DRAWN_LABEL)
        self.assertEqual(result.usage, {"input_tokens": 300, "output_tokens": 1800})
        call = client.responses.calls[0]
        self.assertFalse(call["store"])
        self.assertIn("copied exactly: 15, 2, 37, 500", call["input"][0]["content"])
        self.assertIn("Step 2:", call["input"][1]["content"])
        # Drawn once per step and run: the second request is the kept drawing.
        again = asyncio.run(dd.draw_step_diagram(
            client, settings, step_id="step-2", step_label="2", step_text=STEPS[1],
            source_sha256="a" * 64, registry=registry))
        self.assertEqual(again.reason, "cached")
        self.assertIs(again.diagram, diagram)
        self.assertEqual(len(client.responses.calls), 1)
        self.assertIs(registry.get(diagram.asset_id), diagram)
        self.assertIsNone(registry.get("nope"))

    def test_a_bad_or_late_or_failing_drawing_is_reported(self) -> None:
        settings = dd.DrawnDiagramSettings(True)
        bad = asyncio.run(dd.draw_step_diagram(
            _client(_response("I cannot draw that.")), settings, step_id="s", step_label="1",
            step_text=STEPS[0], source_sha256="a" * 64, registry=dd.DiagramRegistry()))
        self.assertEqual(bad.status, "unusable")
        failed = asyncio.run(dd.draw_step_diagram(
            _client(_response(RAW_SVG), error=RuntimeError("down")), settings, step_id="s", step_label="1",
            step_text=STEPS[0], source_sha256="a" * 64, registry=dd.DiagramRegistry()))
        self.assertEqual(failed.status, "provider_error")

        def too_late(awaitable, timeout):
            awaitable.close()
            raise asyncio.TimeoutError

        with patch.object(dd.asyncio, "wait_for", side_effect=too_late):
            late = asyncio.run(dd.draw_step_diagram(
                _client(_response(RAW_SVG)), settings, step_id="s", step_label="1",
                step_text=STEPS[0], source_sha256="a" * 64, registry=dd.DiagramRegistry()))
        self.assertEqual(late.status, "timeout")


class _Session:
    def __init__(self, enabled=True):
        self.drawn_diagram_settings = dd.DrawnDiagramSettings(enabled, timeout_seconds=5.0)
        self.accepted_configuration_id = 7
        self.current = True

    def is_current(self, turn_id, generation):
        return self.current


class _Sender:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    async def text(self, kind, **fields):
        self.events.append((kind, fields))


def _curated(step_index: int = 1) -> CuratedProtocolSession:
    session = CuratedProtocolSession(build_fixture(
        protocol_id="lane-wv-drawn", title="Fictional lane WV protocol", steps=STEPS))
    session.activate_configured()
    session.plan("프로토콜 시작해줘", turn_id=1, language="ko", configuration_id=1, generation=1)
    session.current_index = step_index
    return session


class ServerWiringTests(unittest.TestCase):
    def test_the_drawing_is_made_only_when_on_and_asked_for(self) -> None:
        curated = _curated()
        plan = route_curated_runtime_turn(curated, "그림으로 그려 줘", turn_id=2, language="ko",
                                          configuration_id=1, generation=1).plan
        self.assertEqual(plan.visual_kind, "drawn_diagram")
        self.assertIn("그림 그리기가 꺼져 있어", plan.speech_text)
        self.assertTrue(server_module._drawing_wanted(_Session(), curated, plan))
        self.assertFalse(server_module._drawing_wanted(_Session(enabled=False), curated, plan))
        figure = route_curated_runtime_turn(curated, "그림 보여줘", turn_id=3, language="ko",
                                            configuration_id=1, generation=1).plan
        self.assertFalse(server_module._drawing_wanted(_Session(), curated, figure))
        rewritten = server_module._drawing_words(plan, "ko")
        self.assertEqual(rewritten.speech_text, server_module._DRAWING_ON_WORDS)
        self.assertNotIn("꺼져 있어", rewritten.display_text)
        self.assertIn("그리고 있어요", rewritten.display_text)

    def test_the_drawing_task_sends_its_events_for_the_step(self) -> None:
        curated = _curated()
        session = _Session()
        sender = _Sender()
        dd.DRAWN_DIAGRAMS.clear()
        try:
            with patch.object(server_module, "AsyncOpenAI", return_value=_client(_response(RAW_SVG))), \
                 patch.object(server_module, "require_env", return_value="fake"):
                asyncio.run(server_module._run_drawing(
                    session=session, sender=sender, turn_id=2, generation=1, curated=curated, language="ko"))
            kinds = [kind for kind, _ in sender.events]
            self.assertEqual(kinds, ["protocol.drawing.state", "protocol.drawing.result"])
            state = sender.events[0][1]
            self.assertEqual((state["status"], state["step_id"], state["configuration_id"]),
                             ("running", curated.fixture.steps[1].step_id, 7))
            result = sender.events[1][1]
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["diagram"]["step_id"], curated.fixture.steps[1].step_id)
            self.assertEqual(result["diagram"]["label"], dd.DRAWN_LABEL)
            self.assertIsNotNone(dd.DRAWN_DIAGRAMS.get(result["diagram"]["asset_id"]))
            # A failing client: a result that says so, nothing raised.
            sender = _Sender()
            with patch.object(server_module, "AsyncOpenAI", side_effect=RuntimeError("no client")), \
                 patch.object(server_module, "require_env", return_value="fake"):
                asyncio.run(server_module._run_drawing(
                    session=session, sender=sender, turn_id=3, generation=1, curated=curated, language="ko"))
            self.assertEqual(sender.events[-1][1]["status"], "provider_error")
        finally:
            dd.DRAWN_DIAGRAMS.clear()

    def test_the_drawing_route_serves_the_checked_svg(self) -> None:
        from fastapi.testclient import TestClient

        content, _n, _t = dd.sanitize_svg(RAW_SVG, frozenset({"500", "15", "37", "2"}))
        import hashlib
        diagram = dd.DrawnDiagram(hashlib.sha256(content).hexdigest(), content, "step-2", "2", "a" * 64, (), (), "gpt-6-luna")
        dd.DRAWN_DIAGRAMS.keep(diagram)
        try:
            client = TestClient(server_module.app)
            response = client.get(f"/api/drawn-visuals/{diagram.asset_id}")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content, content)
            self.assertEqual(response.headers["content-type"], "image/svg+xml")
            self.assertIn("sandbox", response.headers["content-security-policy"])
            self.assertEqual(client.get("/api/drawn-visuals/" + "0" * 64).status_code, 404)
        finally:
            dd.DRAWN_DIAGRAMS.clear()


PAGE_SETUP = r"""
acceptedSessionConfiguration={configuration_id:7,mode:"cascade",language:"ko",protocol_id:"protocol-x",revision_id:"rev-1"};
const state={attached:true,protocol_id:"protocol-x",revision_id:"rev-1",display_name:"Fictional",development_only:false,readiness_status:"guidance_ready",active:true,current_step_label:"2",current_step_id:"step-2",total_steps:3,at_final_step:false,block_reason:null,revision:1,
 display_summary:"2 Wash the band.",primary_summary:"2단계: 밴드를 씻습니다.",source_language:"en",spoken_summary:"2단계입니다.",source_sha256:"a".repeat(64),source_filename:"source.pdf",
 warning_texts:[],warning_presentations:[],visual_assets:[],visual_status:"unavailable",source_page_refs:[1],workflow_status:"active"};
await onMessage({data:JSON.stringify({type:"protocol.fixture.state",configuration_id:7,state,screen:{source_figures:[]}})},sessionGeneration,socket);
await onMessage({data:JSON.stringify({type:"speech.start",turn_id:2,generation:sessionGeneration})},sessionGeneration,socket);
turnServerGenerations.set(turnBrowserKey(2,sessionGeneration),sessionGeneration);
const sendDrawing=(type,fields)=>onMessage({data:JSON.stringify({type,configuration_id:7,turn_id:2,generation:sessionGeneration,protocol_id:"protocol-x",revision_id:"rev-1",step_id:"step-2",step_label:"2",...fields})},sessionGeneration,socket);
const diagram={asset_id:"c".repeat(64),url:"/api/drawn-visuals/"+"c".repeat(64),mime_type:"image/svg+xml",step_id:"step-2",step_label:"2",label:"AI 가 그린 그림 — 실제와 다를 수 있음",removed_numbers:["800"],removed_texts:0,model:"gpt-6-luna",width_px:640,height_px:400};
const panel=()=>node("procedure-drawing");
const deep=item=>(item._text||"")+" "+item.children.map(deep).join(" ");
const imgs=()=>{const found=[];const walk=item=>{if(item.tagName==="img")found.push(item);item.children.forEach(walk)};walk(panel());return found};
"""


class PageTests(unittest.TestCase):
    def test_the_drawing_is_shown_with_its_label_for_the_step_shown(self) -> None:
        result = run_page_script(PAGE_SETUP + r"""
await sendDrawing("protocol.drawing.state",{status:"running",label:diagram.label});
assert(turnNode(2,sessionGeneration).querySelector(".filler-status").textContent.includes("그림을 그리는 중"),"running line missing");
await sendDrawing("protocol.drawing.result",{status:"success",diagram,label:diagram.label});
assert(!panel().hidden&&imgs().length===1&&imgs()[0].src===diagram.url,"drawing not drawn from the same-origin route");
const text=deep(panel());
assert(text.includes("AI 가 그린 그림 — 실제와 다를 수 있음"),`label missing: ${text}`);
assert(text.includes("원문에 없는 숫자 1개는 뺐어요"),`removed numbers missing: ${text}`);
assert(turnNode(2,sessionGeneration).querySelector(".turn-visual").textContent.includes("AI 가 그린 그림"),"turn line missing");
await onMessage({data:JSON.stringify({type:"protocol.fixture.state",configuration_id:7,state:{...state,revision:2,current_step_label:"3",current_step_id:"step-3"},screen:{source_figures:[]}})},sessionGeneration,socket);
assert(panel().hidden,"the next step kept the drawing");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_drawing_for_another_step_or_route_is_not_shown(self) -> None:
        result = run_page_script(PAGE_SETUP + r"""
await sendDrawing("protocol.drawing.result",{status:"success",diagram:{...diagram,step_id:"step-3"},label:diagram.label});
assert(imgs().length===0&&panel().hidden,"a drawing of another step was shown");
await onMessage({data:JSON.stringify({type:"speech.start",turn_id:3,generation:sessionGeneration})},sessionGeneration,socket);
turnServerGenerations.set(turnBrowserKey(3,sessionGeneration),sessionGeneration);
await onMessage({data:JSON.stringify({type:"protocol.drawing.result",configuration_id:7,turn_id:3,generation:sessionGeneration,status:"success",diagram:{...diagram,url:"https://evil.example/x.svg"},label:diagram.label})},sessionGeneration,socket);
assert(imgs().length===0,"a remote drawing url was trusted");
await onMessage({data:JSON.stringify({type:"speech.start",turn_id:4,generation:sessionGeneration})},sessionGeneration,socket);
turnServerGenerations.set(turnBrowserKey(4,sessionGeneration),sessionGeneration);
await onMessage({data:JSON.stringify({type:"protocol.drawing.result",configuration_id:7,turn_id:4,generation:sessionGeneration,status:"timeout",diagram:null,label:diagram.label})},sessionGeneration,socket);
assert(turnNode(4,sessionGeneration).querySelector(".turn-visual").textContent.includes("시간 초과"),"timeout line missing");
""")
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
