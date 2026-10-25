"""The page range on the upload screen and on "다시 분석" (lane EASY, decision 1).

The screen sends the two page numbers a person typed with the upload (and
the first analysis request it makes for a text PDF) and with "다시 분석";
empty inputs send nothing, as before. It never decides what the pages are:
the server checks them against the document, and the detail screen shows
the range the server says the analysis read.
"""

from __future__ import annotations

import unittest

from tests.test_lane_ia_tabs import panel
from tests.test_lane_ia_upload import run_flow
from tests.test_screen_cleanup import INDEX


class PageRangeMarkupTests(unittest.TestCase):
    def test_the_upload_and_the_detail_screen_hold_the_page_inputs(self) -> None:
        html = INDEX.read_text(encoding="utf-8")
        upload = panel(html, "upload")
        for ident in ("protocol-page-range", "protocol-first-page", "protocol-last-page"):
            self.assertIn(f'id="{ident}"', upload, ident)
        self.assertIn("비우면 문서 전체를 분석해요.", upload)
        protocols = panel(html, "protocols")
        for ident in ("protocol-analysis-range", "reanalysis-first-page", "reanalysis-last-page"):
            self.assertIn(f'id="{ident}"', protocols, ident)


class PageRangeRequestTests(unittest.TestCase):
    def test_the_upload_and_its_first_analysis_carry_the_typed_pages(self) -> None:
        result = run_flow(r"""
await boot(flowAnswers());go("#/upload");
node("protocol-first-page").value="2";node("protocol-last-page").value="3";
await pickFile();await settle();
const upload=calls.find(call=>call.url.startsWith("/api/protocols?filename="));
assert(upload&&upload.url.endsWith("&first_page=2&last_page=3"),`upload url ${upload&&upload.url}`);
const analysis=calls.filter(call=>call.url.startsWith("/api/protocols/p-9/analysis")&&!call.url.includes("/status")&&call.method==="POST");
assert(analysis.length===1&&analysis[0].url==="/api/protocols/p-9/analysis?first_page=2&last_page=3",`analysis ${analysis.map(c=>c.url)}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_empty_inputs_send_no_range(self) -> None:
        result = run_flow(r"""
await boot(flowAnswers());go("#/upload");
await pickFile();await settle();
const upload=calls.find(call=>call.url.startsWith("/api/protocols?filename="));
assert(upload&&!upload.url.includes("first_page"),`upload url ${upload&&upload.url}`);
assert(calls.some(call=>call.url==="/api/protocols/p-9/analysis"&&call.method==="POST"),"the analysis was not asked for");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_one_end_alone_or_a_backward_range_is_said_and_nothing_is_sent(self) -> None:
        result = run_flow(r"""
await boot(flowAnswers());go("#/upload");
for(const [first,last] of [["3",""],["5","2"],["0","2"]]){
 node("protocol-first-page").value=first;node("protocol-last-page").value=last;
 const before=calls.length;await pickFile();await settle();
 assert(!calls.slice(before).some(call=>call.url.startsWith("/api/protocols?filename=")),`sent ${first}-${last}`);
 assert(node("protocol-upload-status").textContent==="고른 쪽이 이 문서에 없어요. 처음과 끝 쪽 번호를 확인해 주세요.",node("protocol-upload-status").textContent);
}
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_link_import_carries_the_typed_pages(self) -> None:
        result = run_flow(r"""
await boot(flowAnswers());go("#/upload");
serve([["/api/protocols/import-link",call=>{catalogEntry=REGISTERED("structured_analysis_ready");return {protocol:catalogEntry,deduplicated:false};}],...flowAnswers()]);
node("protocol-first-page").value="4";node("protocol-last-page").value="6";
node("protocol-link-url").value="https://docs.google.com/document/d/synthetic-public/view";
await registerProtocolLink();await settle();
const imported=calls.find(call=>call.url==="/api/protocols/import-link");
assert(imported.body.first_page===4&&imported.body.last_page===6,JSON.stringify(imported.body));
assert(calls.some(call=>call.url==="/api/protocols/p-9/analysis?first_page=4&last_page=6"),"the first analysis lost the range");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_re_analysis_sends_the_detail_screens_pages_and_shows_the_servers_range(self) -> None:
        result = run_flow(r"""
await boot(flowAnswers());
renderProtocolReview({...REVIEW("p-9"),protocol_id:"p-9",analysis_available:true,analysis_page_range:{first_page:2,last_page:3,page_count:12,pages_ko:"2–3쪽"}});
assert(node("protocol-analysis-range").hidden===false,"the range line is hidden");
assert(node("protocol-analysis-range").textContent==="분석한 쪽 · 2–3쪽 (전체 12쪽)",node("protocol-analysis-range").textContent);
renderProtocolReview({...REVIEW("p-9"),protocol_id:"p-9",analysis_available:true,analysis_page_range:null});
assert(node("protocol-analysis-range").textContent==="분석한 쪽 · 문서 전체",node("protocol-analysis-range").textContent);
serve([["/api/protocols/p-9/reanalysis",{analysis_request_accepted:true,analysis_run:{state:"analyzing"}}],...flowAnswers()]);
node("reanalysis-first-page").value="5";node("reanalysis-last-page").value="9";
await analyzeRegisteredProtocol("p-9",{reanalysis:true});await settle();
assert(calls.some(call=>call.url==="/api/protocols/p-9/reanalysis?first_page=5&last_page=9"&&call.method==="POST"),calls.map(c=>c.url).join(" "));
node("reanalysis-first-page").value="";node("reanalysis-last-page").value="";
await analyzeRegisteredProtocol("p-9",{reanalysis:true});await settle();
assert(calls.some(call=>call.url==="/api/protocols/p-9/reanalysis"&&call.method==="POST"),"the whole document was not asked for");
""")
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
