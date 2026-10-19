"""Lane UI3, decision 4 (2026-10-14): the picked protocol's reagent tables.

Lane AN (#79) keeps an .xlsx attached to a protocol as its reagent table,
cell by cell where the workbook prints it -- never analysed and never a
step's source (``POST``/``GET /api/protocols/{id}/reagent-tables``). The
picked protocol's box offers "시약 표(.xlsx) 붙이기", lists the tables
attached (sheets and cell counts) and opens one to read its cells as the
server keeps them.
"""

from __future__ import annotations

import unittest

from tests.test_screen_cleanup import INDEX, run_page_script

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

TABLES = r"""
const calls=[];
const XLSX="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
const cell=(ref,row,column,value,display=String(value),extra={})=>({ref,row,column,value,display,...extra});
const table={table_id:"reagent-table-aaa",filename:"reagents.xlsx",media_type:XLSX,sha256:"c".repeat(64),byte_size:9123,authority:"person_upload",cell_count:7,
 warnings:["계산값이 저장되지 않은 수식 셀 1개 — 엑셀에서 열어 저장한 뒤 다시 올리면 값이 보입니다."],
 sheets:[
  {name:"Reagents",state:"visible",max_row:3,max_column:3,merged:[],cells:[cell("A1",1,1,"Reagent"),cell("B1",1,2,"Conc."),cell("A2",2,1,"Tris-HCl"),cell("B2",2,2,0.5,"50%",{number_format:"0%"}),cell("C3",3,3,null,"",{formula:"=SUM(B2:B3)"})]},
  {name:"Notes",state:"hidden",max_row:1,max_column:1,merged:[],cells:[cell("A1",1,1,"Keep on ice"),cell("A2",2,1,"1.50","1.50")]}]};
let tables=[table];
function serve(extra=()=>null){globalThis.fetch=async(url,options={})=>{url=String(url);calls.push(`${options.method||"GET"} ${url} ${options.headers?.["Content-Type"]||""}`);const answer=extra(url,options);if(answer)return answer;
 if(url==="/api/protocols/p-1/reagent-tables"&&(options.method||"GET")==="GET")return json({reagent_tables:tables});
 throw new Error(`unexpected ${options.method||"GET"} ${url}`);};}
const list=()=>node("reagent-table-list");
const rows=()=>list().children.filter(item=>item.className==="reagent-table-row");
const kids=item=>item.children.flatMap(child=>[child,...kids(child)]);
const settle=()=>new Promise(resolve=>setTimeout(resolve,10));
"""


class MarkupTests(unittest.TestCase):

    def test_the_box_sits_in_the_picked_protocol_panel(self) -> None:
        # Lane IA decision ③ (2026-10-19): the tables are their own folded group on the protocol's screen.
        html = INDEX.read_text(encoding="utf-8")
        panel = html[html.index('id="protocol-detail-reagents"'):html.index("</details>", html.index('id="protocol-detail-reagents"'))]
        self.assertIn('<section id="protocol-reagent-tables" class="reagent-tables" aria-labelledby="reagent-tables-title">', panel)
        self.assertIn(
            f'<input id="reagent-table-file" type="file" accept=".xlsx,{XLSX}" class="visually-hidden-file-input" aria-label="시약 표(.xlsx) 붙이기" aria-describedby="reagent-table-status">',
            panel,
        )
        self.assertIn('<label id="reagent-table-pick" for="reagent-table-file" class="btn-file-select" role="button" tabindex="0">시약 표(.xlsx) 붙이기</label>', panel)
        for ident in ("reagent-table-status", "reagent-table-list", "reagent-table-view"):
            self.assertIn(f'id="{ident}"', panel)


class ListTests(unittest.TestCase):

    def test_the_tables_are_listed_with_their_sheets_and_cells(self) -> None:
        result = run_page_script(TABLES + r"""
serve();
const shown=await loadReagentTables("p-1");
assert(Array.isArray(shown)&&shown.length===1,"the tables were not read");
assert(calls.join("|")==="GET /api/protocols/p-1/reagent-tables ",calls.join("|"));
assert(rows().length===1,`rows: ${rows().length}`);
const text=rows()[0].textContent;
assert(text.includes("reagents.xlsx")&&text.includes("시트 2개 · 셀 7개")&&text.includes("Reagents 셀 5개")&&text.includes("Notes 셀 2개 (숨긴 시트)"),text);
assert(text.includes("계산값이 저장되지 않은 수식 셀 1개"),`the server's warning is gone: ${text}`);
const open=kids(rows()[0]).find(item=>item.tagName==="button");
assert(open&&open.textContent==="열어 보기"&&open.attributes["aria-expanded"]==="false",`button: ${open?.textContent}`);
tables=[];await loadReagentTables("p-1");
assert(rows().length===0&&list().textContent==="붙인 시약 표 없음",list().textContent);
serve(()=>({ok:false,status:404,headers:{get:()=>"application/json"},json:async()=>({detail:"protocol_not_found"})}));
await loadReagentTables("p-1");
assert(list().textContent==="시약 표 목록을 불러오지 못했습니다.",list().textContent);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_the_picked_protocol_reads_its_tables_once(self) -> None:
        result = run_page_script(TABLES + r"""
serve();
const review={protocol_id:"p-1",title:"In-gel",revision_id:"pdf-1-analysis-1",available_for_execution:true,analysis_available:true,lifecycle_state:"ready",source:{filename:"in-gel.pdf",page_count:9},readiness:{status:"guidance_ready",reasons:[]},sections:[],constructs:[],execution_blockers:[],execution_notices:[],safety_notices:[]};
renderProtocolReview(review);await settle();
renderProtocolReview(review);await settle();
assert(calls.filter(call=>call.includes("/reagent-tables")).length===1,`asked again for the same protocol: ${calls}`);
assert(rows().length===1,"the list is empty");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


class OpenTests(unittest.TestCase):

    def test_a_table_opens_cell_by_cell_as_the_workbook_prints_it(self) -> None:
        result = run_page_script(TABLES + r"""
serve();await loadReagentTables("p-1");
const view=node("reagent-table-view");view.hidden=true;
const open=kids(rows()[0]).find(item=>item.tagName==="button");
open.dispatch("click");
assert(view.hidden===false&&open.textContent==="닫기"&&open.attributes["aria-expanded"]==="true",`${view.hidden} ${open.textContent}`);
const sheets=view.children.filter(item=>item.className==="reagent-sheet");
assert(sheets.length===2,`sheets: ${sheets.length}`);
assert(sheets[0].children[0].textContent==="Reagents · 셀 5개",sheets[0].children[0].textContent);
const first=kids(sheets[0]).find(item=>item.tagName==="table");
const grid=kids(first).filter(item=>item.tagName==="tr").map(tr=>tr.children.map(td=>td.textContent).join("|"));
assert(grid.join("\n")==="|A|B|C\n1|Reagent|Conc.|\n2|Tris-HCl|50%|\n3|||=SUM(B2:B3)",grid.join("\n"));
const formula=kids(first).find(item=>String(item.className||"").includes("reagent-formula"));
assert(formula&&formula.textContent==="=SUM(B2:B3)","a formula cell without a saved value is not marked");
const notes=kids(kids(sheets[1]).find(item=>item.tagName==="table")).filter(item=>item.tagName==="tr").map(tr=>tr.children.map(td=>td.textContent).join("|"));
assert(notes.join("\n")==="|A\n1|Keep on ice\n2|1.50",notes.join("\n"));
open.dispatch("click");
assert(view.hidden===true&&open.textContent==="열어 보기"&&open.attributes["aria-expanded"]==="false","the table did not close");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


class AttachTests(unittest.TestCase):

    def test_an_xlsx_is_attached_with_its_type_and_the_list_redrawn(self) -> None:
        result = run_page_script(TABLES + r"""
currentProtocolReviewId="p-1";
const added={table_id:"reagent-table-bbb",filename:"buffers.xlsx",sha256:"d".repeat(64),byte_size:100,cell_count:2,warnings:[],sheets:[{name:"Sheet1",cells:2}]};
serve((url,options)=>{if(url.startsWith("/api/protocols/p-1/reagent-tables?filename=")){tables=[table,{...added,sheets:[{name:"Sheet1",state:"visible",cells:[cell("A1",1,1,"PBS"),cell("B1",1,2,1)]}]}];return json({reagent_table:added,deduplicated:false});}return null;});
node("reagent-table-file").files=[{name:"buffers.xlsx",type:""}];
await attachReagentTable();
assert(calls[0]===`POST /api/protocols/p-1/reagent-tables?filename=buffers.xlsx ${XLSX}`,calls.join("|"));
assert(node("reagent-table-status").textContent==="시약 표를 붙였습니다 · buffers.xlsx · 셀 2개",node("reagent-table-status").textContent);
assert(rows().length===2,"the list was not read again");
serve((url)=>url.includes("?filename=")?json({reagent_table:added,deduplicated:true}):null);
node("reagent-table-file").files=[{name:"buffers.xlsx",type:XLSX}];
await attachReagentTable();
assert(node("reagent-table-status").textContent==="이미 붙인 같은 표입니다 · buffers.xlsx",node("reagent-table-status").textContent);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_refusals_read_in_korean_and_other_files_are_not_sent(self) -> None:
        result = run_page_script(TABLES + r"""
currentProtocolReviewId="p-1";
const status=()=>node("reagent-table-status").textContent;
serve(()=>{throw new Error("sent")});
for(const [name,type] of [["reagents.xls","application/vnd.ms-excel"],["reagents.csv","text/csv"],["protocol.pdf","application/pdf"]]){
 calls.length=0;node("reagent-table-file").files=[{name,type}];await attachReagentTable();
 assert(calls.length===0&&status()==="시약 표는 엑셀 .xlsx 파일만 붙일 수 있습니다.",`${name}: ${calls} ${status()}`);
}
for(const [code,http,words] of [["reagent_table_too_large",413,"셀 수"],["invalid_reagent_table",422,".xlsx 통합 문서로 읽을 수 없습니다"],["unsupported_reagent_table_media_type",415,".xlsx 파일만"],["protocol_catalog_unavailable",503,"사용할 수 없습니다"]]){
 serve(()=>({ok:false,status:http,headers:{get:()=>"application/json"},json:async()=>({detail:code})}));
 node("reagent-table-file").files=[{name:"big.xlsx",type:XLSX}];await attachReagentTable();
 assert(status().includes(words)&&!/[a-z]+_[a-z_]+/.test(status()),`${code}: ${status()}`);
}
currentProtocolReviewId=null;node("reagent-table-file").files=[{name:"a.xlsx",type:XLSX}];await attachReagentTable();
assert(status()==="먼저 프로토콜을 고르세요.",status());
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
