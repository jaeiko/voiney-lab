"""Lane IA, decision ③ (2026-10-19): the protocols tab -- the list, and a protocol's own screen.

#/protocols is the list (each protocol with its state in plain words -- 바로
쓸 수 있음 · 확인할 것 n개 · 분석 중 · 분석 실패 · the server's 다시 분석 권장 line)
with [새 프로토콜 올리기]; #/protocols/<id> is that protocol's screen: its
state and [이 프로토콜로 실험 시작] above, and below, folded, the analysis
summary (the check before the start, open by default so the safety
statements are never behind a click), 원문 대조, the reagent tables, the
review cards and versions, and the analysis itself (refresh, OCR, again). A
row tapped on the list pushes the protocol's route, so the back button
returns to the list. Shares the fake page of tests/test_lane_ia_tabs.py.
"""

from __future__ import annotations

import re
import unittest

from tests.test_lane_ia_tabs import SETUP, panel, run
from tests.test_screen_cleanup import INDEX


class ProtocolsMarkupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.html = INDEX.read_text(encoding="utf-8")
        self.panel = panel(self.html, "protocols")

    def test_the_panel_holds_the_list_view_and_the_detail_view(self) -> None:
        order = [self.panel.index(marker) for marker in ('<div id="protocol-list-view"', '<div id="protocol-detail-view"')]
        self.assertEqual(order, sorted(order))
        self.assertRegex(self.panel, r'<div id="protocol-detail-view" class="protocol-detail-view"[^>]*\bhidden>')
        lists = self.panel[self.panel.index('<div id="protocol-list-view"'):self.panel.index('<div id="protocol-detail-view"')]
        for ident in ("protocol-library", "protocol-library-count", "protocol-list", "protocol-queue"):
            self.assertIn(f'id="{ident}"', lists, ident)
        for ident in ("protocol-review-panel", "protocol-start-summary", "protocol-compare", "protocol-review-editor", "protocol-reagent-tables"):
            self.assertNotIn(f'id="{ident}"', lists, ident)

    def test_the_detail_head_has_the_state_and_the_start_and_the_groups_below(self) -> None:
        detail = self.panel[self.panel.index('<div id="protocol-detail-view"'):]
        head = detail[:detail.index('<details id="protocol-detail-summary"')]
        self.assertIn('<a id="protocol-detail-back" class="protocol-detail-back" href="#/protocols">← 프로토콜 목록</a>', head)
        for ident in ("protocol-detail-title", "protocol-detail-badges", "protocol-detail-line", "protocol-start"):
            self.assertIn(f'id="{ident}"', head, ident)
        self.assertIn('<button id="protocol-start" type="button" hidden>이 프로토콜로 실험 시작</button>', head)
        groups = re.findall(r'<details id="(protocol-detail-summary|protocol-compare|protocol-detail-reagents|protocol-review-editor|protocol-detail-analysis)"', detail)
        self.assertEqual(groups, ["protocol-detail-summary", "protocol-compare", "protocol-detail-reagents", "protocol-review-editor", "protocol-detail-analysis"])
        self.assertRegex(detail, r'<details id="protocol-detail-summary" class="protocol-detail-group" open>')
        summary = detail[detail.index('<details id="protocol-detail-summary"'):detail.index('<details id="protocol-compare"')]
        self.assertIn('id="protocol-start-summary"', summary)
        self.assertNotIn('id="protocol-start"', summary.replace('id="protocol-start-', ""))
        reagents = detail[detail.index('<details id="protocol-detail-reagents"'):detail.index('<details id="protocol-review-editor"')]
        self.assertIn('<section id="protocol-reagent-tables" class="reagent-tables" aria-labelledby="reagent-tables-title">', reagents)
        analysis = detail[detail.index('<details id="protocol-detail-analysis"'):]
        for ident in ("protocol-review-panel", "protocol-review-status", "protocol-review-analysis-notice", "protocol-review-refresh", "protocol-ocr-run", "protocol-analysis-retry"):
            self.assertIn(f'id="{ident}"', analysis, ident)
        self.assertNotIn('id="protocol-reagent-tables"', analysis)
        for word in ("승인", "반려", "리비전"):
            self.assertNotIn(word, head)
        style = self.html.split("<style>", 1)[1].split("</style>", 1)[0]
        self.assertIn(".protocol-list-view[hidden],.protocol-detail-view[hidden]{display:none}", style)
        self.assertIn("min-height:var(--target-run)", style[style.index(".protocol-detail-actions button{"):style.index("}", style.index(".protocol-detail-actions button{"))])


class ProtocolsRouteTests(unittest.TestCase):
    def test_the_list_route_shows_the_list_and_a_protocol_route_its_screen(self) -> None:
        result = run(r"""
await boot(base());
go("#/protocols");
assert(node("protocol-list-view").hidden===false&&node("protocol-detail-view").hidden===true,"the list route did not show the list alone");
go("#/protocols/p-1");await settle(8);
assert(node("protocol-list-view").hidden===true&&node("protocol-detail-view").hidden===false,"the protocol route did not show its screen alone");
assert(node("protocol-detail-title").textContent==="Gamma",node("protocol-detail-title").textContent);
const badges=node("protocol-detail-badges").children.map(badge=>badge.textContent);
assert(JSON.stringify(badges)===JSON.stringify(["바로 쓸 수 있음"]),`badges: ${badges}`);
assert(node("protocol-id").value==="p-1"&&node("protocol-start").hidden===false,"the protocol was not picked or its start is hidden");
go("#/protocols/p-2");await settle(8);
assert(node("protocol-detail-title").textContent==="Delta (검토 대기)",node("protocol-detail-title").textContent);
assert(node("protocol-detail-badges").children.some(badge=>badge.textContent==="확인할 것 2개"),`pending badges: ${node("protocol-detail-badges").children.map(b=>b.textContent)}`);
go("#/protocols");
assert(node("protocol-list-view").hidden===false&&node("protocol-detail-view").hidden===true,"the list did not come back");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_row_tapped_on_the_list_pushes_the_protocols_route_and_back_returns(self) -> None:
        result = run(r"""
await boot(base());
go("#/protocols");
const row=node("protocol-list").children.map(item=>item.firstChild).find(button=>button.dataset.protocolId==="p-2");
row.dispatch("click");await settle(8);
assert(globalThis.location.hash==="#/protocols/p-2",`hash: ${globalThis.location.hash}`);
assert(globalThis.history.replaced.at(-1)!=="#/protocols/p-2","the list's tap replaced the entry instead of pushing it (back could not return to the list)");
assert(node("protocol-detail-view").hidden===false,"the tap did not open the protocol's screen");
// From one protocol's screen to another's: replaced, not pushed.
const before=globalThis.history.replaced.length;
pickProtocolFromList("p-1");await settle(8);
assert(globalThis.location.hash==="#/protocols/p-1"&&globalThis.history.replaced.length===before+1,"switching protocols did not replace the entry");
// A review loaded while the list shows (another caller) opens that protocol's screen too.
go("#/protocols");
await loadProtocolReview("p-2");await settle(8);
assert(globalThis.location.hash==="#/protocols/p-2"&&node("protocol-detail-view").hidden===false,"a loaded review did not open the protocol's screen");
// Picking from the wizard on another tab leaves the route alone.
go("#/home");pickProtocolFromList("p-1");await settle(8);
assert(globalThis.location.hash==="#/home",`the home's pick moved the route: ${globalThis.location.hash}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_list_says_its_states_in_plain_words(self) -> None:
        result = run(r"""
await boot(base());
go("#/protocols");
const badges=walk(node("protocol-list")).filter(el=>el.className==="ui-badge").map(el=>el.textContent);
for(const word of ["분석 통과","검토 대기","실행 가능"])assert(!badges.some(badge=>badge.includes(word)),`${word} on the list: ${badges}`);
assert(JSON.stringify(badges)===JSON.stringify(["바로 쓸 수 있음","확인할 것 2개"]),`badges: ${badges}`);
assert(node("protocol-library-count").textContent==="프로토콜 2개 · 바로 쓸 수 있는 것 1개",node("protocol-library-count").textContent);
""")
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
