"""Lane UI2, decision 2 (2026-10-13): the check before the start as one card.

The card's badges stand where the draft had its approved version: the
source PDF, the day it was uploaded and the analysis state (from the
server's catalog and review), and the server's own "분석기 개선 전 분석 · 다시
분석 권장" line. Inside, the source's safety statements beside their Korean
come first, then the step timers, the notices and the lines the server asks
to check (비운 칸, 반복 확인), and the start is a 64px button. Once the
experiment runs the card folds to one line as before (lane VF, decision 6).
"""

from __future__ import annotations

import re
import unittest

from tests.test_screen_cleanup import INDEX, run_page_script

REVIEW = r"""
const review=(extra={})=>({protocol_id:"p-1",title:"In-gel digestion",revision_id:"pdf-1-analysis-1",available_for_execution:true,development_only:false,analysis_available:true,analysis_payload_sha256:"b".repeat(64),lifecycle_state:"ready",analysis_status:"review_required",created_at:"2026-10-05T01:00:00+00:00",
 source:{filename:"in-gel.pdf",sha256:"a".repeat(64),page_count:9},readiness:{status:"guidance_ready",reasons:[]},sections:[],constructs:[],step_count:12,
 execution_blockers:[],execution_notices:[{code:"unresolved_ambiguity",kind:"source_note",message_ko:"원문에 서로 다른 두 서술이 있습니다.",source_page_number:5}],
 safety_notices:[{step_label:"2",step_id:"step-2",source_page_number:4,source_text:"Wear gloves.",primary_text:"장갑을 착용하세요."}],
 timers:{verified:[{step_label:"2",value_ko:"15분",source_literal:"15 min",source_page_number:4,source_excerpt:"for 15 min"}],refused:[]},
 cleared_fields_ko:"원문에서 확인하지 못해 비운 칸: 저자",repeat_count_checks_ko:["4단계 · 세척을 3회 반복"],analysis_outdated_ko:null,...extra});
const basis=()=>node("protocol-start-basis");
"""


class StartCardBadgeTests(unittest.TestCase):

    def test_the_basis_badge_names_the_pdf_the_day_and_the_analysis(self) -> None:
        result = run_page_script(REVIEW + r"""
renderStartSummary(review());
assert(!basis().hidden&&basis().textContent==="in-gel.pdf · 2026년 10월 5일 올림 · 분석 통과"&&basis().dataset.kind==="done",`${basis().hidden} ${basis().textContent}`);
renderStartSummary(review({development_only:true,created_at:null}));
assert(basis().textContent==="in-gel.pdf · 올린 날짜 기록 없음 · 개발용 큐레이션 분석",basis().textContent);
renderStartSummary(review({available_for_execution:false,lifecycle_state:"blocked",execution_blocker_codes:["no_executable_steps"],execution_blockers:[{code:"no_executable_steps",kind:"blocking",message_ko:"실행할 단계를 원문에서 찾지 못했습니다."}]}));
assert(basis().textContent==="in-gel.pdf · 2026년 10월 5일 올림 · 실행을 막는 사유 있음"&&basis().dataset.kind==="missing",basis().textContent);
for(const word of ["승인","리비전","검토 대기"])assert(!basis().textContent.includes(word),`${word} in the badge`);
renderStartSummary(null);
assert(node("protocol-start-summary").hidden===true,"no review left the card on screen");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_the_server_line_on_an_older_analysis_is_a_badge_of_its_own(self) -> None:
        result = run_page_script(REVIEW + r"""
renderStartSummary(review({analysis_outdated_ko:"분석기 개선 전 분석 · 다시 분석 권장"}));
const outdated=node("protocol-start-outdated");
assert(!outdated.hidden&&outdated.textContent==="분석기 개선 전 분석 · 다시 분석 권장",`${outdated.hidden} ${outdated.textContent}`);
renderStartSummary(review());
assert(outdated.hidden&&outdated.textContent==="","the line stayed on a current analysis");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_the_card_folds_after_the_start_and_keeps_its_badges(self) -> None:
        result = run_page_script(REVIEW + r"""
acceptedSessionConfiguration={configuration_id:3,mode:"cascade",language:"ko",protocol_id:"p-1",revision_id:"pdf-1-analysis-1"};sessionActive=true;
curatedProtocolState={attached:true,active:true,protocol_id:"p-1",timers:{experiment:{started_at:"2026-10-13T01:02:00Z"}}};
renderStartSummary(review());
assert(node("protocol-start-body").hidden===true&&node("protocol-start-done").hidden===false,"the card did not fold once the experiment ran");
assert(!basis().hidden&&basis().textContent.includes("분석 통과"),"the folded card lost its badge");
assert(node("protocol-start").hidden===true,"the start stayed during the run");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


class StartCardMarkupTests(unittest.TestCase):

    def setUp(self) -> None:
        self.html = INDEX.read_text(encoding="utf-8")
        self.style = self.html.split("<style>", 1)[1].split("</style>", 1)[0]

    def test_safety_first_then_timers_notices_lines_and_the_start(self) -> None:
        begin = self.html.index('<section id="protocol-start-summary"')
        card = self.html[begin:self.html.index("</section>", begin)]
        head = card[:card.index('id="protocol-start-body"')]
        for ident in ("protocol-start-summary-title", "protocol-start-basis", "protocol-start-outdated"):
            self.assertIn(f'id="{ident}"', head, ident)
        body = card[card.index('id="protocol-start-body"'):]
        # Lane IA decision ③ (2026-10-19): the start button stands in the protocol screen's head, above the card.
        order = [body.index(f'id="{ident}"') for ident in (
            "protocol-start-overview", "protocol-execution-blockers", "protocol-safety-notices",
            "protocol-step-timers", "protocol-execution-notices", "protocol-start-cleared",
            "protocol-start-repeat-checks")]
        self.assertEqual(order, sorted(order))
        html = INDEX.read_text(encoding="utf-8")
        self.assertLess(html.index('<button id="protocol-start" type="button" hidden>'), begin)

    def test_the_start_is_a_64px_button_and_the_safety_box_is_never_folded(self) -> None:
        rule = re.search(r"\.protocol-start-summary #protocol-start\{([^}]*)\}", self.style).group(1)
        self.assertIn("min-height:var(--target-run)", rule)
        self.assertIn(".protocol-start-summary .protocol-safety-group{padding:var(--space-4);border:var(--stroke-state) solid var(--warning-ink);background:var(--warning-soft)}", self.style)
        self.assertIn(".ui-badge[hidden]{display:none}", self.style)
        safety = self.html.split("function renderSafetyNotices", 1)[1].split("\nfunction ", 1)[0]
        self.assertNotIn('createElement("details")', safety)


if __name__ == "__main__":
    unittest.main()
