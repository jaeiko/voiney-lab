"""Lane UI3, decision 2 (2026-10-14): the safety scope line, from the server only.

Under lane PL's ``source_protocol_only`` scope the server says, in its own
words, that the run has no institution safety document and only the source's
own safety statements: ``safety_scope.notice`` on ``GET
/api/protocols/{id}/review`` and ``scope_notice`` on ``session.safety_pack``.
The check before the start and the run's safety box show that sentence as it
is, and nothing when the server sends none.
"""

from __future__ import annotations

import unittest

from tests.test_lane_ui0_run_view import SETUP
from tests.test_lane_ui2_start_check import REVIEW
from tests.test_screen_cleanup import INDEX, run_page_script

NOTICE = "기관 안전 문서 없음 · 원문 안전 주의만"


class StartCardScopeTests(unittest.TestCase):

    def test_the_start_card_says_the_server_sentence_above_the_safety_statements(self) -> None:
        html = INDEX.read_text(encoding="utf-8")
        # Lane IA decision ③: the start button stands in the protocol screen's head, above the card.
        body = html[html.index('id="protocol-start-body"'):html.index('id="protocol-start-repeat-checks"')]
        self.assertIn('<p id="protocol-safety-scope" class="safety-scope-line" role="note" hidden></p>', body)
        self.assertLess(body.index('id="protocol-safety-scope"'), body.index('id="protocol-safety-notices"'))
        result = run_page_script(REVIEW + r"""
const scope=node("protocol-safety-scope");
renderStartSummary(review({safety_scope:{usage_scope:"source_protocol_only",source_protocol_only:true,notice:"기관 안전 문서 없음 · 원문 안전 주의만"}}));
assert(scope.hidden===false&&scope.textContent==="기관 안전 문서 없음 · 원문 안전 주의만",`${scope.hidden} ${scope.textContent}`);
renderStartSummary(review({safety_scope:{usage_scope:"institution",source_protocol_only:false,notice:null}}));
assert(scope.hidden===true&&scope.textContent==="","a line the server did not send");
renderStartSummary(review({safety_scope:{usage_scope:"source_protocol_only",source_protocol_only:true,notice:"기관 안전 문서 없음 · 원문 안전 주의만"}}));
renderStartSummary(review());
assert(scope.hidden===true,"a review without the field kept the line");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


class SessionBoxScopeTests(unittest.TestCase):

    def test_the_run_safety_box_carries_the_pack_sentence(self) -> None:
        result = run_page_script(SETUP + r"""
const box=()=>node("procedure-warning");
const items=[{kind:"pdf_warning",origin:"PDF p.4",source_text:"Wear gloves.",source_language:"en",primary_text:"장갑을 착용하세요.",translation_check:"passed"}];
const screen=extra=>({safety_items:items,translation_source:"reviewed",translation_pending:false,...extra});
await say({type:"session.safety_pack",configuration_id,safety_pack:{coverage_status:"source_protocol_only",scope_notice:"기관 안전 문서 없음 · 원문 안전 주의만",sop_documents:[],sds_documents:[],equipment_documents:[],total_document_count:0}});
await say({type:"protocol.fixture.state",configuration_id,action:"start",state:state(),screen:screen()});
assert(box().hidden===false,"the box is hidden");
const lines=box().children.map(child=>`${child.className}:${child.textContent}`);
assert(lines[0]==="safety-box-head:안전"&&lines[1]==="safety-scope:기관 안전 문서 없음 · 원문 안전 주의만",lines.join(" | "));
assert(box().textContent.includes("장갑을 착용하세요."),"the step's statement is gone");
await say({type:"protocol.fixture.state",configuration_id,action:"current",state:state(),screen:{...screen(),safety_items:[]}});
assert(box().hidden===true,"a box with only the scope line was drawn for a step with no statement");
await say({type:"session.safety_pack",configuration_id,safety_pack:{coverage_status:"available",scope_notice:null,sop_documents:[],sds_documents:[],equipment_documents:[],total_document_count:2}});
await say({type:"protocol.fixture.state",configuration_id,action:"current",state:state(),screen:screen()});
assert(!box().children.some(child=>child.className==="safety-scope"),"a line the server did not send");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_without_a_checked_list_the_sentence_heads_the_source_lines(self) -> None:
        result = run_page_script(SETUP + r"""
await say({type:"session.safety_pack",configuration_id,safety_pack:{coverage_status:"source_protocol_only",scope_notice:"기관 안전 문서 없음 · 원문 안전 주의만",sop_documents:[],sds_documents:[],equipment_documents:[],total_document_count:0}});
await say({type:"protocol.fixture.state",configuration_id,action:"start",state:state({warning_texts:["Wear gloves."]})});
assert(node("procedure-warning").textContent==="기관 안전 문서 없음 · 원문 안전 주의만\nPDF · Wear gloves.",JSON.stringify(node("procedure-warning").textContent));
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
