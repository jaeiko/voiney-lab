"""Lane UI2, decision 1 (2026-10-13): the 실험 전 view after the drafts' P1 and M1.

The protocol list draws the catalog the server sent, every row's state in
words (분석 통과, 분석 중, 분석 실패, the server's "다시 분석 권장" line, or the
catalog's word for any other state) with a failure's reason; the row of the
run the page watches carries the same analysis line as its box, the time
taken included, rewritten in place. A row is picked through the select
("실행할 프로토콜"), the same path as before, and not while a session holds
the configuration. Beside the list sit the upload box and what is still
being processed; under the list the picked protocol's analysis line and its
buttons. The M1 row puts the check before the start beside the voice
console, the basis card and the microphone, said in words.
"""

from __future__ import annotations

import re
import unittest

from tests.test_lane_ui0_tokens import _contrast, _tokens
from tests.test_screen_cleanup import INDEX, run_page_script

SETUP = r"""
const entry=(id,extra={})=>({protocol_id:id,title:`Protocol ${id}`,source_filename:`${id}.pdf`,source_sha256:"a".repeat(64),revision_id:`pdf-1-analysis-1`,readiness_status:"guidance_ready",analysis_status:"review_required",step_count:12,created_at:"2026-10-05T01:00:00+00:00",available_for_execution:false,development_only:false,lifecycle_state:"ready",execution_blocker_codes:[],analysis_outdated_codes:[],analysis_outdated_ko:null,analysis_run:{state:"review_required",failure_code:null,reanalysis:false},...extra});
const catalog=[
 entry("p-pass",{available_for_execution:true}),
 entry("p-dev",{available_for_execution:true,development_only:true,created_at:null}),
 entry("p-going",{lifecycle_state:"analyzing",analysis_status:"structured_analysis_ready",step_count:0,analysis_run:{state:"analyzing",failure_code:null,reanalysis:false}}),
 entry("p-failed",{lifecycle_state:"blocked",analysis_status:"analysis_failed",step_count:0,analysis_run:{state:"analysis_failed",failure_code:"protocol_analysis_timeout",reanalysis:false}}),
 entry("p-old",{available_for_execution:true,analysis_outdated_codes:["repeat_count_kind_missing"],analysis_outdated_ko:"분석기 개선 전 분석 · 다시 분석 권장"}),
 entry("p-again",{available_for_execution:true,analysis_run:{state:"analysis_failed",failure_code:"protocol_analysis_invalid_response",reanalysis:true}}),
 entry("p-ocr",{lifecycle_state:"uploaded",analysis_status:"ocr_required",step_count:0,analysis_run:{state:"ocr_required",failure_code:null,reanalysis:false}}),
];
const calls=[];
globalThis.fetch=async(url,options={})=>{url=String(url);calls.push(`${options.method||"GET"} ${url}`);
 if(url==="/api/protocols")return json({protocols:catalog});
 const review=url.match(/^\/api\/protocols\/([^/]+)\/review$/);
 if(review){const id=decodeURIComponent(review[1]);return json({...catalog.find(item=>item.protocol_id===id),analysis_available:true,source:{filename:`${id}.pdf`,sha256:"a".repeat(64),page_count:3},readiness:{status:"guidance_ready",reasons:[]},sections:[],execution_blockers:[],execution_notices:[],safety_notices:[]});}
 if(url.startsWith("/api/protocols/")&&url.endsWith("/analysis/status"))return json({state:"analyzing",requested_at:new Date(Date.now()-7000).toISOString()});
 throw new Error(`unexpected ${options.method||"GET"} ${url}`);};
const settle=()=>new Promise(resolve=>setTimeout(resolve,20));
const rows=()=>node("protocol-list").children.map(item=>item.firstChild).filter(Boolean);
const row=id=>rows().find(item=>item.dataset.protocolId===id);
const badges=id=>row(id).children.find(child=>child.className==="protocol-row-badges").children.map(badge=>`${badge.dataset.kind}:${badge.textContent}`);
const line=id=>row(id).children.find(child=>child.className==="protocol-row-line").textContent;
const queue=()=>node("protocol-queue-list").children.map(item=>item.firstChild.dataset.protocolId);
await loadProtocolCatalog();
"""


class ProtocolListRowsTests(unittest.TestCase):

    def test_each_row_says_its_state_in_words_from_the_catalog(self) -> None:
        result = run_page_script(SETUP + r"""
assert(rows().length===7,`rows: ${rows().length}`);
// Lane IA decision ③ (2026-10-19): the list's words are plain -- "바로 쓸 수 있음" for an analysis that passed.
const expect={"p-pass":["done:바로 쓸 수 있음"],"p-dev":["done:바로 쓸 수 있음","quiet:개발용 큐레이션 분석"],"p-going":["pending:분석 중"],"p-failed":["danger:분석 실패"],"p-old":["done:바로 쓸 수 있음","warning:분석기 개선 전 분석 · 다시 분석 권장"],"p-again":["done:바로 쓸 수 있음","danger:다시 분석 실패"],"p-ocr":["missing:OCR 필요"]};
for(const [id,want] of Object.entries(expect))assert(JSON.stringify(badges(id))===JSON.stringify(want),`${id}: ${badges(id)}`);
assert(line("p-failed")==="분석 실패 · 이유: 분석 시간 초과(제한 시간 안에 끝나지 않음)",`failure reason: ${line("p-failed")}`);
assert(line("p-again").startsWith("다시 분석 실패 · 이유: 분석 모델 응답 형식이 올바르지 않음"),`re-analysis reason: ${line("p-again")}`);
assert(line("p-pass")==="",`a passed row has a line: ${line("p-pass")}`);
const text=visibleText(row("p-pass"));
assert(text.includes("Protocol p-pass")&&text.includes("원문: p-pass.pdf")&&text.includes("12단계")&&text.includes("2026년 10월 5일 올림"),`row words: ${text}`);
assert(visibleText(row("p-dev")).includes("올린 날짜 기록 없음")&&visibleText(row("p-going")).includes("—"),"a missing date or step count was guessed");
assert(node("protocol-library-count").textContent==="프로토콜 7개 · 바로 쓸 수 있는 것 4개",node("protocol-library-count").textContent);
for(const word of ["승인","리비전","검토 대기"])assert(!visibleText(node("protocol-list")).includes(word),`${word} on the list`);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_an_empty_or_unreadable_catalog_says_so(self) -> None:
        result = run_page_script(SETUP + r"""
catalog.length=0;await loadProtocolCatalog();
assert(node("protocol-list").children.length===1&&node("protocol-list").textContent.includes("아직 올린 프로토콜이 없습니다"),node("protocol-list").textContent);
assert(node("protocol-queue").hidden===true,"an empty catalog shows a queue");
globalThis.fetch=async()=>({ok:false,json:async()=>({})});await loadProtocolCatalog();
assert(node("protocol-list").textContent.includes("불러오지 못했습니다"),node("protocol-list").textContent);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


class PickingARowTests(unittest.TestCase):

    def test_a_row_picks_the_select_and_loads_that_review(self) -> None:
        result = run_page_script(SETUP + r"""
row("p-failed").dispatch("click");await settle();
assert(node("protocol-id").value==="p-failed",`select: ${node("protocol-id").value}`);
assert(calls.includes("GET /api/protocols/p-failed/review"),`review not loaded: ${calls}`);
assert(currentProtocolReviewId==="p-failed"&&!node("protocol-review-panel").hidden,"the picked protocol's box is not on screen");
assert(row("p-failed").attributes["aria-current"]==="true"&&row("p-pass").attributes["aria-current"]==="false","the picked row is not marked");
const before=calls.length;row("p-failed").dispatch("click");await settle();
assert(calls.length===before,"picking the same row again asked the server again");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_no_row_is_picked_while_a_session_holds_the_configuration(self) -> None:
        result = run_page_script(SETUP + r"""
node("protocol-id").value="p-pass";renderState("LISTENING");
assert(rows().every(item=>item.disabled===true),"rows stayed pickable during a session");
assert(pickProtocolFromList("p-failed")===false&&node("protocol-id").value==="p-pass","a row changed the protocol during a session");
renderState("IDLE");
assert(rows().every(item=>item.disabled===false),"rows stayed locked after the session");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_the_queue_lists_what_is_still_processed_and_picks_it(self) -> None:
        result = run_page_script(SETUP + r"""
assert(JSON.stringify(queue())===JSON.stringify(["p-going","p-failed","p-again","p-ocr"]),`queue: ${queue()}`);
assert(node("protocol-queue").hidden===false,"the queue is hidden");
const item=node("protocol-queue-list").children[1].firstChild;
assert(visibleText(item).includes("p-failed.pdf")&&visibleText(item).includes("분석 실패 · 이유"),visibleText(item));
assert(visibleText(node("protocol-queue-list").children[3].firstChild).includes("OCR 필요 · 실행 불가"),"a waiting row lost the catalog's words");
item.dispatch("click");await settle();
assert(node("protocol-id").value==="p-failed","a queue row did not pick its protocol");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


class WatchedRunLineTests(unittest.TestCase):

    def test_the_watched_run_line_and_its_time_are_rewritten_in_place(self) -> None:
        result = run_page_script(SETUP + r"""
const first=row("p-going"),queued=node("protocol-queue-list").children[0].firstChild;
currentProtocolReviewId="p-going";
showProtocolAnalysisNotice("p-going",PROTOCOL_ANALYSIS_RUNNING_NOTICE,"분석 중 · 걸린 시간 0분 07초 · 실행 상태 변경 없음");
assert(row("p-going")===first,"the row was drawn again");
assert(line("p-going").startsWith("분석 중 · 원문 근거를 확인하고 있습니다")&&line("p-going").includes("걸린 시간 0분 07초"),`row line: ${line("p-going")}`);
assert(queued.children.find(child=>child.className==="queue-row-line").textContent===line("p-going"),"the queue line differs from the row's");
assert(node("protocol-review-analysis-notice").textContent===line("p-going"),"the row and the box say different things");
showProtocolAnalysisNotice("p-going",PROTOCOL_ANALYSIS_RUNNING_NOTICE,"분석 중 · 걸린 시간 0분 08초 · 실행 상태 변경 없음");
assert(line("p-going").includes("0분 08초"),`the time did not move: ${line("p-going")}`);
assert(line("p-pass")==="","another row took the watched line");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


class MicCheckTests(unittest.TestCase):

    def test_the_microphone_is_said_in_words_from_this_browser(self) -> None:
        result = run_page_script(r"""
node("mic-recovery").hidden=true;// as in the page markup
renderMicCheck();
assert(node("mic-check-state").textContent==="마이크 꺼짐"&&node("mic-check-hint").textContent==="실험을 시작하면 켜집니다",`${node("mic-check-state").textContent}|${node("mic-check-hint").textContent}`);
assert(node("mic-check-device").textContent==="마이크 · 기본 마이크 (시스템 설정)",node("mic-check-device").textContent);
await renderMicDevices([{deviceId:"usb-1",label:"USB 헤드셋"}]);node("mic-device").value="usb-1";
micWorkletNode={port:{}};renderMicCheck();
assert(node("mic-check-state").textContent==="마이크 켜짐"&&node("mic-check-hint").textContent===""&&node("mic-check-device").textContent==="마이크 · USB 헤드셋",`${node("mic-check-state").textContent}|${node("mic-check-device").textContent}`);
node("mic-recovery").hidden=false;renderMicCheck();
assert(node("mic-check-state").textContent==="마이크 끊김"&&node("mic-check-hint").textContent.includes("마이크 다시 연결"),node("mic-check-state").textContent);
setText("voice-calibration-setting-status","건너뜀 · 모든 말을 들어요");renderMicCheck();
assert(node("mic-check-voice").textContent==="목소리 맞추기 · 건너뜀 · 모든 말을 들어요",node("mic-check-voice").textContent);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


class MarkupTests(unittest.TestCase):

    def setUp(self) -> None:
        self.html = INDEX.read_text(encoding="utf-8")
        self.style = self.html.split("<style>", 1)[1].split("</style>", 1)[0]

    def block(self, start: str, end: str) -> str:
        begin = self.html.index(start)
        return self.html[begin:self.html.index(end, begin)]

    def test_the_list_the_upload_box_and_the_picked_box_sit_together(self) -> None:
        # Lane IA decision ① (2026-10-19): the list, the upload box and the picked box are the protocols tab;
        # the voice console, the context card and the microphone stand on the experiment tab. Decision ③:
        # the list is one view; the picked protocol's box, its check before the start, the compare view and
        # the review are the protocol's own screen (#/protocols/<id>) in the same tab.
        library = self.block('<section id="protocol-library"', '<div id="protocol-detail-view"')
        main = library[library.index('class="library-main"'):library.index('class="library-side"')]
        side = library[library.index('class="library-side"'):]
        self.assertIn('id="protocol-list"', main)
        # Decision ③: the upload box is the upload panel's; the side offers it and keeps what is still processed.
        for ident in ("protocol-upload-link", "protocol-queue"):
            self.assertIn(f'id="{ident}"', side, ident)
        upload = self.block('<section id="tab-upload"', '<section id="tab-experiment"')
        for ident in ("protocol-pdf", "protocol-upload-status", "protocol-duplicate-notice"):
            self.assertIn(f'id="{ident}"', upload, ident)
        detail = self.block('<div id="protocol-detail-view"', '<section id="tab-experiment"')
        for ident in ("protocol-review-panel", "protocol-review-analysis-notice", "protocol-analysis-retry", "protocol-start-summary"):
            self.assertIn(f'id="{ident}"', detail, ident)
        prestart = self.block('<div class="prestart-side">', '<section class="workflow-grid"')
        order = [prestart.index(marker) for marker in ('class="voice-console"', 'class="experiment-context-card"', 'id="mic-check"')]
        self.assertEqual(order, sorted(order))
        self.assertLess(self.html.index('id="protocol-library"'), self.html.index('class="prestart-side"'))

    def test_wide_screens_put_the_list_beside_the_upload_box_and_rows_are_touch_sized(self) -> None:
        self.assertIn("@media (min-width:900px){.library-grid{grid-template-columns:minmax(0,1fr) 360px}}", self.style)
        self.assertIn(".library-grid{display:grid;grid-template-columns:minmax(0,1fr);", self.style)
        row = re.search(r"\n    \.protocol-row\{([^}]*)\}", self.style).group(1)
        self.assertIn("min-height:var(--target-run)", row)
        self.assertIn('body[data-view="during"] .protocol-library,body[data-view="during"] .mic-check{display:none}', self.style)

    def test_every_badge_keeps_its_words_readable(self) -> None:
        tokens = _tokens(self.style)

        def value(name: str) -> str:
            seen = tokens[name]
            while seen.startswith("var("):
                seen = tokens[seen[4:-1]]
            return seen

        rules = dict(re.findall(r'\.ui-badge\[data-kind="([a-z]+)"\]\{([^}]*)\}', self.style))
        # The list's kinds; any kind added later (the after view's
        # "provisional", decision 4) is checked the same way.
        self.assertLessEqual({"danger", "done", "missing", "pending", "quiet", "warning"}, set(rules))
        base = re.search(r"\n    \.ui-badge\{([^}]*)\}", self.style).group(1)
        for kind, rule in rules.items():
            colour = re.search(r"(?:^|;)color:var\((--[a-z-]+)\)", rule) or re.search(r"(?:^|;)color:var\((--[a-z-]+)\)", base)
            background = re.search(r"background:var\((--[a-z-]+)\)", rule) or re.search(r"background:var\((--[a-z-]+)\)", base)
            ratio = _contrast(value(colour.group(1)), value(background.group(1)))
            self.assertGreaterEqual(ratio, 4.5, f"{kind}: {ratio:.2f}")


if __name__ == "__main__":
    unittest.main()
