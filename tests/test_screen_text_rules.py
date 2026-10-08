"""Screen-text rules from the 2026-10-01 voice test (line N).

The reader's language is the body; the source sits under it in one folded
"원문" per block; no block carries a translation label; safety is one box that
holds every line, each Korean line checked for its numbers and its negation
and shown as its source when the check fails; the grounding boundary never
reaches the screen of a pilot run.

Page tests run the production ``<script>`` of ``static/index.html`` in Node
through ``tests.test_screen_cleanup.run_page_script``. Server tests drive
``curated_safety_items`` with a stand-in session, so they need no PDF; the
one turn test that does is skipped without it, like its neighbours.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.test_screen_cleanup import run_page_script
from voiney_lab.server import (
    _safety_translation,
    curated_safety_items,
    curated_screen_fields,
)

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "src" / "voiney_lab" / "static" / "index.html"
SOURCE_PDF = ROOT / "data" / "runtime" / "candidate-a-source" / "in-gel-digestion.pdf"

# A protocol.fixture.state the page accepts, Korean session, step 3 shown.
PAGE_SETUP = r"""
acceptedSessionConfiguration={configuration_id:7,mode:"cascade",language:"ko",protocol_id:"protocol-x",revision_id:"rev-1"};
const baseState={attached:true,protocol_id:"protocol-x",revision_id:"rev-1",display_name:"In-gel digestion",development_only:false,readiness_status:"guidance_ready",active:true,current_step_label:"3",current_step_id:"step-3",total_steps:9,at_final_step:false,block_reason:null,revision:1,
 display_summary:"3 Cut the band into 1 mm cubes.",primary_summary:"3단계: 밴드를 1 mm 크기로 자릅니다.",source_language:"en",spoken_summary:"3단계입니다.",
 warning_texts:["Do not touch the gel with bare hands."],warning_presentations:[{primary_text:"맨손으로 젤을 만지지 마세요.",source_text:"Do not touch the gel with bare hands.",source_page:3,evidence_id:"warning_1"}],
 visual_assets:[],visual_status:"unavailable",source_page_refs:[3],workflow_status:"active"};
const items=[
 {kind:"pdf_warning",origin:"PDF p.3",source_text:"Do not touch the gel with bare hands.",source_language:"en",primary_text:"맨손으로 젤을 만지지 마세요.",translation_check:"passed"},
 {kind:"pdf_warning",origin:"PDF p.3",source_text:"Never leave the scalpel uncapped.",source_language:"en",primary_text:null,translation_check:"negation_changed"},
 {kind:"safety_document",origin:"안전 문서 · 물질 SDS",source_text:"Acetonitrile is flammable. Keep away from heat.",source_language:"en",primary_text:"아세토니트릴은 인화성입니다. 열에서 멀리 두세요.",translation_check:"passed"},
 {kind:"safety_document",origin:"안전 문서 · 안전 SOP",source_text:"Wear nitrile gloves.",source_language:"en",primary_text:null,translation_check:"missing"},
 {kind:"safety_document",origin:"안전 문서 · 장비 매뉴얼",source_text:"Close the centrifuge lid before 12000 rpm.",source_language:"en",primary_text:"12000 rpm 전에 원심분리기 뚜껑을 닫으세요.",translation_check:"passed"},
 {kind:"safety_document",origin:"안전 문서 · 안전 SOP",source_text:"보안경을 착용합니다.",source_language:"ko",primary_text:"보안경을 착용합니다.",translation_check:"source_is_korean"}];
const send=(state,screen,extra={})=>onMessage({data:JSON.stringify({type:"protocol.fixture.state",configuration_id:7,state,screen,...extra})},sessionGeneration,socket);
const rows=()=>node("procedure-warning").children.filter(child=>child.className==="safety-item");
const kids=(item,cls)=>item.children.filter(child=>String(child.className||"").split(" ").includes(cls));
const everything=()=>["procedure-title","procedure-meta","procedure-translation-note","procedure-step-title","procedure-primary","procedure-source-toggle","procedure-instruction","procedure-warning","log"].map(id=>node(id).textContent).join("\n");
"""


class SafetyBoxPageTests(unittest.TestCase):
    """Principles 4 and 5: one box, every line, each failed line as its source."""

    def test_every_safety_line_is_in_the_one_box_with_its_origin(self):
        result = run_page_script(PAGE_SETUP + r"""
await send(baseState,{safety_items:items,translation_source:"reviewed"});
const box=node("procedure-warning");
assert(!box.hidden,"safety box hidden");
assert(rows().length===items.length,`lines dropped: ${rows().length} of ${items.length}`);
rows().forEach((row,i)=>assert(kids(row,"safety-origin")[0].textContent===items[i].origin,`origin missing on line ${i}`));
assert(box.children[0].textContent==="안전","one box heading");
for(const item of items){const text=box.textContent;assert(text.includes(item.source_text)||text.includes(item.primary_text),"a line is missing: "+item.source_text);}
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_line_that_fails_its_check_shows_its_source_open(self):
        result = run_page_script(PAGE_SETUP + r"""
await send(baseState,{safety_items:items,translation_source:"reviewed"});
const [passed,negation,,missing,,korean]=rows();
// Passed: Korean body, the source open beside it (lane TS, decision 2 --
// it was folded closed under the Korean before).
assert(kids(passed,"safety-text")[0].textContent==="맨손으로 젤을 만지지 마세요.","Korean body missing");
assert(kids(passed,"safety-text")[1].textContent==="Do not touch the gel with bare hands."&&kids(passed,"source-toggle").length===0,"source not open beside the Korean line");
// Failed negation and missing Korean: the source is the body, nothing folded.
for(const [row,text] of [[negation,"Never leave the scalpel uncapped."],[missing,"Wear nitrile gloves."]]){
 const body=kids(row,"safety-text")[0];
 assert(body.textContent===text&&body.className.includes("source-as-body"),"failed line not shown as its source: "+body.textContent);
 assert(kids(row,"source-toggle").length===0,"failed line is folded away");
}
// A Korean document needs no fold.
assert(kids(korean,"safety-text")[0].textContent==="보안경을 착용합니다."&&kids(korean,"source-toggle").length===0,"Korean document folded");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_an_english_reader_sees_the_sources_and_no_translation(self):
        result = run_page_script(PAGE_SETUP + r"""
acceptedSessionConfiguration={...acceptedSessionConfiguration,input_language:"en"};
await send(baseState,{safety_items:items,translation_source:"reviewed"});
for(const row of rows())assert(kids(row,"source-toggle").length===0,"translation fold shown to an English reader");
const text=node("procedure-warning").textContent;
assert(!text.includes("맨손으로")&&!text.includes("아세토니트릴"),"Korean shown to an English reader: "+text);
assert(node("procedure-primary").textContent==="3 Cut the band into 1 mm cubes."&&node("procedure-source-toggle").hidden===true,"English step body wrong");
assert(node("procedure-translation-note").hidden===true,"note shown to an English reader");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_without_a_checked_list_every_source_is_shown(self):
        # An older server's state: no screen fields, so nothing is checked,
        # so no Korean is shown and no line is dropped.
        result = run_page_script(PAGE_SETUP + r"""
activeSafetyPack={step_guidance:[{step_label:"3",warnings:["Do not touch the gel with bare hands."],applicable_documents:[1,2,3,4].map(n=>({summary_text:`Document line ${n}.`}))}]};
await send(baseState,undefined);
const text=node("procedure-warning").textContent;
assert(text.includes("Do not touch the gel with bare hands.")&&!text.includes("맨손으로"),"unchecked Korean shown: "+text);
for(const n of [1,2,3,4])assert(text.includes(`Document line ${n}.`),`document line ${n} dropped`);
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_box_keeps_role_alert_and_is_the_only_safety_box(self):
        html = INDEX.read_text(encoding="utf-8")
        self.assertIn(
            '<div id="procedure-warning" class="step-warning" role="alert" hidden></div>',
            html,
        )
        self.assertNotIn('id="step-safety-guidance"', html)


class StepCardPageTests(unittest.TestCase):
    """Principles 1-3: Korean body, one closed "원문" fold, no block labels."""

    LABELS = ("참고 번역", "자동 번역", "검토", "원문 · English", "주의 원문", "출처 ·", "안내 ·", "주의 ·")

    def test_korean_body_with_the_source_folded_closed_and_no_labels(self):
        result = run_page_script(PAGE_SETUP + r"""
await send(baseState,{safety_items:items,translation_source:"reviewed"});
assert(node("procedure-primary").textContent==="3단계: 밴드를 1 mm 크기로 자릅니다.","Korean is not the body: "+node("procedure-primary").textContent);
const fold=node("procedure-source-toggle");
assert(fold.hidden===false&&fold.open===false,"source fold missing or open");
assert(node("procedure-instruction").textContent==="3 Cut the band into 1 mm cubes.","source missing from the fold");
assert(node("procedure-translation-note").hidden===true,"a reviewed translation got a note");
const shown=everything();
for(const label of """ + json.dumps(self.LABELS, ensure_ascii=False) + r""")assert(!shown.includes(label),`label "${label}" on screen: ${shown}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_step_with_no_korean_shows_its_source_and_says_so_once(self):
        result = run_page_script(PAGE_SETUP + r"""
await send({...baseState,primary_summary:null},{safety_items:[],translation_source:"none"});
assert(node("procedure-primary").textContent==="3 Cut the band into 1 mm cubes.","source is not the body");
assert(node("procedure-source-toggle").hidden===true,"empty fold shown");
assert(node("procedure-translation-note").textContent==="한국어 번역이 없어 원문으로 보여 드립니다."&&!node("procedure-translation-note").hidden,"head line missing");
assert(!everything().includes("검증된 한국어 참고 번역"),"old per-block line still shown");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_an_automatic_translation_is_said_once_at_the_head(self):
        result = run_page_script(PAGE_SETUP + r"""
await send(baseState,{safety_items:[],translation_source:"reviewed"});
await onMessage({data:JSON.stringify({type:"speech.start",turn_id:2,generation:0})},sessionGeneration,socket);
await onMessage({data:JSON.stringify({type:"reply.delta",configuration_id:7,turn_id:2,generation:0,segment_index:0,text:"1단계: 시료를 넣습니다.\n\n원문 · English\n1 Add the sample.",translation_status:"model_assisted_unreviewed"})},sessionGeneration,socket);
const note=node("procedure-translation-note");
assert(note.textContent==="한국어는 자동 번역입니다. 정확한 내용은 원문을 확인하세요."&&!note.hidden,"auto-translation line missing: "+note.textContent);
await send({...baseState,revision:2},{safety_items:[],translation_source:"machine"});
assert(note.textContent==="한국어는 자동 번역입니다. 정확한 내용은 원문을 확인하세요.","machine source not noted");
""")
        self.assertEqual(result.returncode, 0, result.stderr)


class AnswerTextPageTests(unittest.TestCase):
    """Principles 2, 3 and 6 for chat answers."""

    def _reply(self, text: str, note: str | None) -> str:
        # The protocol is development-only: that alone must not show
        # development info (there is no test mode any more, lane DI).
        return PAGE_SETUP + r"""
await send({...baseState,development_only:true},{safety_items:[],translation_source:"reviewed"});
await onMessage({data:JSON.stringify({type:"speech.start",turn_id:4,generation:0})},sessionGeneration,socket);
const message={configuration_id:7,turn_id:4,generation:0,text:""" + json.dumps(text, ensure_ascii=False) + r""",development_note:""" + json.dumps(note, ensure_ascii=False) + r"""};
await onMessage({data:JSON.stringify({type:"reply.delta",segment_index:0,...message})},sessionGeneration,socket);
await onMessage({data:JSON.stringify({type:"reply.complete",configuration_id:7,turn_id:4,generation:0,text:message.text})},sessionGeneration,socket);
const reply=turnNode(4).querySelector(".reply");
const folds=[];const walk=item=>{if(String(item.className||"").split(" ").includes("source-toggle"))folds.push(item);item.children.forEach(walk)};walk(reply);
const devs=[];const walkDev=item=>{if(String(item.className||"").split(" ").includes("dev-info"))devs.push(item);item.children.forEach(walkDev)};walkDev(reply);
"""

    def test_a_pilot_screen_never_shows_the_grounding_boundary(self):
        boundary = "근거 경계: 활성 프로토콜의 확인된 내용이며, 활성화된 경우에만 부족한 설명을 읽기 전용 참고자료에서 확인합니다."
        result = run_page_script(self._reply(
            "직접 답변\nHPLC water는 2단계에 나옵니다.\n\n근거 경계\n활성 프로토콜의 확인된 내용입니다.", boundary) + r"""
assert(!reply.textContent.includes("근거 경계")&&!reply.textContent.includes("활성 프로토콜의 확인된 내용"),"boundary on a pilot screen: "+reply.textContent);
assert(!reply.textContent.includes("직접 답변"),"label on screen");
assert(reply.textContent.includes("HPLC water는 2단계에 나옵니다."),"answer missing");
assert(devs.length===0,"development info shown in a pilot run");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_answer_labels_go_and_the_source_is_one_closed_fold(self):
        text = (
            "답변 · 한국어 참고 번역\n2단계: 두 세척 용액을 준비합니다.\n\n"
            "원문 · English\n2 Prepare two wash solutions.\n\n출처\ncurrent_step · 원문 p.3"
        )
        result = run_page_script(self._reply(text, None) + r"""
for(const label of ["답변 · 한국어","참고 번역","원문 · English"])assert(!reply.textContent.includes(label),`label "${label}" on screen: ${reply.textContent}`);
assert(reply.textContent.includes("2단계: 두 세척 용액을 준비합니다."),"Korean body missing");
assert(folds.length===1&&folds[0].open===false&&folds[0].children[0].textContent==="원문","source not one closed 원문 fold");
assert(folds[0].textContent.includes("2 Prepare two wash solutions.")&&folds[0].textContent.includes("current_step · 원문 p.3"),"source or citation missing from the fold");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_an_answer_with_no_korean_shows_its_source_open(self):
        text = (
            "실험 목적\n원문에 적힌 실험 목적입니다.\n\n원문 · English · PDF p.2\n"
            "This protocol describes in-gel digestion of proteins."
        )
        result = run_page_script(self._reply(text, None) + r"""
assert(!reply.textContent.includes("원문 · English")&&!reply.textContent.includes("원문에 적힌"),"label left: "+reply.textContent);
assert(folds.length===1&&folds[0].open===true&&folds[0].textContent.includes("in-gel digestion of proteins"),"untranslated source not open");
""")
        self.assertEqual(result.returncode, 0, result.stderr)


class SafetyTranslationCheckTests(unittest.TestCase):
    """The check each Korean safety line passes: numbers with units, negation."""

    def test_negation_kept_passes(self):
        for source, korean in (
            ("Do not touch the gel with bare hands.", "맨손으로 젤을 만지지 마세요."),
            ("Never heat above 60 °C.", "60 °C 이상으로 가열하면 안 됩니다."),
            ("You must not pipette by mouth.", "입으로 피펫팅하는 것은 금지입니다."),
            ("Avoid contact with skin.", "피부에 닿지 않게 하세요."),
            ("Wear gloves.", "장갑을 착용하세요."),
        ):
            with self.subTest(source=source):
                self.assertEqual(_safety_translation(source, korean), (korean, "passed"))

    def test_negation_dropped_or_added_fails(self):
        for source, korean in (
            ("Do not touch the gel with bare hands.", "맨손으로 젤을 만지세요."),
            ("Never leave the scalpel uncapped.", "메스 뚜껑을 열어 두세요."),
            ("You must not pipette by mouth.", "입으로 피펫팅하세요."),
            ("Avoid contact with skin.", "피부에 접촉하세요."),
            ("Wear gloves.", "장갑을 착용하지 마세요."),
        ):
            with self.subTest(source=source):
                self.assertEqual(_safety_translation(source, korean), (None, "negation_changed"))

    def test_a_changed_number_fails_and_missing_korean_is_said(self):
        self.assertEqual(
            _safety_translation("Incubate at 37 °C.", "25 °C에서 배양하세요."),
            (None, "quantities_changed"),
        )
        self.assertEqual(_safety_translation("Wear gloves.", None), (None, "missing"))
        self.assertEqual(_safety_translation("Wear gloves.", "  "), (None, "missing"))


def _stand_in_session(*, warnings, localized, documents, pack=True):
    steps = (SimpleNamespace(
        step_id="step-3", source_label="3",
        evidence=SimpleNamespace(source_page_number=3),
        warnings=tuple(
            SimpleNamespace(source_text=text, evidence=SimpleNamespace(source_page_number=3))
            for text in warnings
        ),
    ),)
    fixture = SimpleNamespace(
        steps=steps,
        localizations=localized or None,
        localized_fact=lambda step_id, fact_id: (localized or {}).get(f"{step_id}/{fact_id}"),
    )
    guidance = SimpleNamespace(warnings=tuple(warnings), applicable_documents=tuple(documents))
    safety_pack = SimpleNamespace(guidance_for_step=lambda step, index: guidance) if pack else None
    return SimpleNamespace(
        fixture=fixture, active=True, workflow_status="active", current_index=0,
        safety_pack=safety_pack,
    )


def _document(title, text, *, language="en", korean=None, kind="facility_sop"):
    return SimpleNamespace(
        document_type=kind, title=title, language=language, summary_text=text,
        reviewed_translations=(("ko", korean),) if korean else (),
    )


class SafetyItemsServerTests(unittest.TestCase):
    """The server's list: nothing dropped, each line checked on its own."""

    def test_every_warning_and_every_document_is_listed(self):
        documents = [
            _document(f"SOP {n}", f"Safety line {n}.", korean=f"안전 문장 {n}.")
            for n in range(1, 6)
        ]
        curated = _stand_in_session(
            warnings=("Do not touch the gel.", "Keep the lid closed."),
            localized={"step-3/warning_1": "젤을 만지지 마세요."},
            documents=documents,
        )
        items = curated_safety_items(curated)
        # Two PDF warnings and five documents: more than the old three-line card.
        self.assertEqual(len(items), 7)
        self.assertEqual(
            [item["source_text"] for item in items],
            ["Do not touch the gel.", "Keep the lid closed.",
             *(f"Safety line {n}." for n in range(1, 6))],
        )
        self.assertEqual([item["origin"] for item in items[:2]], ["PDF p.3", "PDF p.3"])
        self.assertTrue(all(item["origin"] == "안전 문서 · 안전 SOP" for item in items[2:]))
        self.assertEqual(items[0]["primary_text"], "젤을 만지지 마세요.")
        self.assertEqual(items[1]["translation_check"], "missing")
        self.assertIsNone(items[1]["primary_text"])

    def test_a_failed_check_withholds_only_that_lines_korean(self):
        curated = _stand_in_session(
            warnings=("Do not touch the gel.",),
            localized={"step-3/warning_1": "젤을 만지세요."},
            documents=[
                _document("SDS", "Store at 4 °C.", korean="25 °C에 보관하세요.", kind="supplier_sds"),
                _document("SOP", "Wear gloves.", korean="장갑을 착용하세요."),
                _document("국문 SOP", "보안경을 착용합니다.", language="ko"),
            ],
        )
        items = curated_safety_items(curated)
        self.assertEqual(
            [(item["primary_text"], item["translation_check"]) for item in items],
            [(None, "negation_changed"), (None, "quantities_changed"),
             ("장갑을 착용하세요.", "passed"),
             ("보안경을 착용합니다.", "source_is_korean")],
        )
        self.assertEqual(items[1]["origin"], "안전 문서 · 물질 SDS")

    def test_no_pack_still_lists_the_pdf_warnings_and_no_step_lists_nothing(self):
        curated = _stand_in_session(
            warnings=("Keep the lid closed.",), localized={}, documents=[], pack=False)
        self.assertEqual([item["source_text"] for item in curated_safety_items(curated)],
                         ["Keep the lid closed."])
        self.assertEqual(curated_screen_fields(curated)["translation_source"], "none")
        curated.active = False
        curated.workflow_status = "ready_to_start"
        self.assertEqual(curated_safety_items(curated), [])


@unittest.skipUnless(SOURCE_PDF.is_file(), "requires the licensed Candidate A source PDF")
class PilotAnswerServerTests(unittest.TestCase):
    """A related-question reply carries no boundary text and no development note."""

    def test_no_boundary_text_and_no_development_note(self):
        from dataclasses import replace

        from tests.test_curated_protocol_cascade import CuratedProtocolServerCascadeTests, Socket
        from voiney_lab.server import Transcription, run_turn

        CuratedProtocolServerCascadeTests.setUpClass()
        cases = (
            # (usage scope, development-only). Lane DI (2026-10-08): there is
            # no test mode any more, so no scope and no fixture ever gets the
            # folded development note.
            ({"VOINEY_LAB_USAGE_SCOPE": "demo"}, True),
            ({"VOINEY_LAB_USAGE_SCOPE": "reference_only"}, True),
            ({"VOINEY_LAB_USAGE_SCOPE": "reference_only"}, False),
        )
        for environment, development_only in cases:
            with self.subTest(scope=environment["VOINEY_LAB_USAGE_SCOPE"], development_only=development_only), \
                    patch.dict("os.environ", environment):
                harness = CuratedProtocolServerCascadeTests()
                harness.fixture = replace(
                    CuratedProtocolServerCascadeTests.fixture,
                    development_only=development_only)
                session = harness.make_session(index=1)
                socket = Socket()

                async def immediate(function, *args, **kwargs):
                    return function(*args, **kwargs)

                async def unsupported(*args, **kwargs):
                    return SimpleNamespace(
                        intent="unsupported", primary_text="", evidence_ids=(),
                        inference_labels=(), unsupported_parts=("definition",),
                    )

                with patch(
                    "voiney_lab.server.transcribe",
                    return_value=Transcription("HPLC water가 뭐야?", "ko"),
                ), patch(
                    "voiney_lab.server.synthesize", return_value=b"\0\0",
                ), patch(
                    "voiney_lab.server.answer_curated_protocol_question",
                    side_effect=unsupported,
                ), patch(
                    "voiney_lab.server.search_approved_lab_references",
                    return_value={
                        "status": "no_admissible_evidence", "answerable": False,
                        "matches": [], "retrieval": {"backend": "sqlite"},
                    },
                ), patch(
                    "voiney_lab.server.asyncio.to_thread", side_effect=immediate,
                ):
                    asyncio.run(run_turn(socket, session, b"\0\0", 1, 1))
                reply = next(item for item in socket.text if item["type"] == "reply.delta")
                complete = next(item for item in socket.text if item["type"] == "reply.complete")
                for text in (reply["text"], complete["text"]):
                    self.assertNotIn("근거 경계", text)
                    self.assertNotIn("직접 답변", text)
                self.assertIn("HPLC water", reply["text"])
                self.assertIsNone(reply["development_note"])
                state = next(
                    item for item in socket.text if item["type"] == "protocol.fixture.state")
                self.assertIn("screen", state)
                self.assertNotIn("safety_items", state["state"])


if __name__ == "__main__":
    unittest.main()
