"""Lane UI4 item 5 (2026-10-23): one experiment's record links to its reports.

The records tab had no way to an experiment's report (lane IA's hand-over: "서버에 실험 → 보고서 id
를 주는 길이 먼저 필요"). Lane QA2 added GET /api/workspace/experiments/{session_id}/reports behind
the same OWN read gates as the experiment (the owner and the lab's administrator; 404 otherwise;
``reports: []`` when there is none or the reports are off). A record picked from the list now
names its reports -- "실험 기록 · {제목} · {날짜 시각}" and the server's state, as the end screen
names them -- each with the end screen's links and only those formats (Word, Markdown, JSON,
CSV), read only. The request goes through serverFetch. The last test feeds the merged route's real
answer to the page and fetches back every link it draws.

Shares tests/test_lane_ia_stage3.py's harness and tests/test_lane_ui4_owner.py's accounts.
"""

from __future__ import annotations

import json
import unittest

import pytest

from tests.test_lane_ia_stage3 import STAGE3
from tests.test_lane_ia_tabs import run
from tests.test_lane_own_reports import records  # noqa: F401 -- lane OWN's report fixture, used below
from tests.test_lane_ui4_owner import OWNER
from tests.test_screen_cleanup import run_page_script

REPORTS = r"""
const ER={report_id:"ER-0000000000000000AAAA",session_id:"s-9",protocol_id:"p-1",protocol_title:"Gamma",status:"completed",started_at:"2026-10-19T01:00:00+00:00",ended_at:"2026-10-19T02:00:00+00:00",anomaly_count:0,blocker_count:0,finalization_version:1,development_only:false,day_sequence:1,day_sequence_date:"2026-10-19"};
const reportRows=()=>walk(node("records-report-list")).filter(el=>hasClass(el,"records-report"));
const linksOf=row=>walk(row).filter(el=>el.tagName==="a").map(el=>[el.textContent,el.href]);
const reportsAsks=()=>calls.filter(call=>call.url.endsWith("/reports"));
"""


def run_reports(body: str):
    return run(STAGE3 + OWNER + REPORTS + body)


class RecordReportsTests(unittest.TestCase):

    def test_a_picked_record_names_its_reports_with_the_end_screens_links(self) -> None:
        result = run_reports(r"""
await boot([["/api/workspace/experiments/s-9/reports",{session_id:"s-9",reports:[ER]}],...withExperiments([RESUMABLE])]);
go("#/records");
assert(node("records-detail-reports").hidden===true,"the reports show on the list");
go("#/records/s-9");await settle(8);
assert(node("records-detail-reports").hidden===false,"the record does not show its reports");
const rows=reportRows();
assert(rows.length===1,`rows: ${rows.length}`);
assert(rows[0].textContent.startsWith("실험 기록 · Gamma · ")&&rows[0].textContent.includes("완료"),`row: ${rows[0].textContent}`);
const links=linksOf(rows[0]);
assert(JSON.stringify(links.map(link=>link[0]))===JSON.stringify(["보고서 만들기 (Word)","Markdown 내보내기","JSON 내보내기","CSV 내보내기"]),`links: ${JSON.stringify(links)}`);
assert(JSON.stringify(links.map(link=>link[1]))===JSON.stringify(["docx","md","json","csv"].map(format=>`/api/experiment-reports/ER-0000000000000000AAAA.${format}`)),`hrefs: ${JSON.stringify(links)}`);
// Read once per record; back to the list hides them.
go("#/records/s-9");await settle(4);
assert(reportsAsks().length===1,`asked ${reportsAsks().length} times`);
go("#/records");
assert(node("records-detail-reports").hidden===true,"the reports stayed on the list");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_none_a_failure_and_the_run_are_said(self) -> None:
        result = run_reports(r"""
let answer={session_id:"s-9",reports:[]};
await boot([["/api/workspace/experiments/s-9/reports",()=>answer],...withExperiments([RESUMABLE])]);
go("#/records/s-9");await settle(8);
assert(reportRows().length===0&&node("records-detail-reports-note").textContent.includes("보고서가 아직 없어요"),node("records-detail-reports-note").textContent);
answer={ok:false,status:500,headers:{get:()=>"application/json"},json:async()=>({detail:"unexpected_server_error"})};
node("experiment-timeline-refresh").dispatch("click");await settle(8);
assert(node("records-detail-reports-note").textContent.includes("불러오지 못했어요"),`failure: ${node("records-detail-reports-note").textContent}`);
// During the run the records tab is the run's own record: no report links there.
go("#/experiment");startRun();go("#/records");
assert(node("records-detail-reports").hidden===true,"report links during the run");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_an_administrator_reads_a_members_reports_through_server_fetch(self) -> None:
        result = run_reports(r"""
await boot([["/api/workspace/experiments/s-theirs/reports",{session_id:"s-theirs",reports:[{...ER,report_id:"ER-0000000000000000BBBB",session_id:"s-theirs"}]}],...asAdmin([THEIRS,MINE])]);
serverOrigin=()=>"https://lab.example";
go("#/records/s-theirs");await settle(8);
assert(reportsAsks().some(call=>call.url==="https://lab.example/api/workspace/experiments/s-theirs/reports"),`asked: ${JSON.stringify(reportsAsks().map(call=>call.url))}`);
const links=linksOf(reportRows()[0]||{children:[]});
assert(links.length===4&&links.every(link=>link[1].startsWith("https://lab.example/api/experiment-reports/ER-0000000000000000BBBB.")),`links: ${JSON.stringify(links)}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()


# The boundary with the merged server (lane QA2's route on dev 3c44109): the route's real answer for
# lane OWN's fixture is what the page draws from, and every link it draws is one the server serves to
# the owner and to the lab's administrator.
@pytest.mark.parametrize("who", ["owner", "admin"])
def test_the_links_drawn_from_the_servers_answer_are_served(records, who):  # noqa: F811
    devices, _, rid = records
    answer = devices[who].get("/api/workspace/experiments/session-owned/reports")
    assert answer.status_code == 200, answer.text
    result = run_page_script(r"""
const payload=""" + json.dumps(answer.json()) + r""";
globalThis.fetch=async url=>{if(String(url)==="/api/workspace/experiments/session-owned/reports")return json(payload);throw new Error(`unexpected ${url}`);};
screenRoute={tab:"records",id:"session-owned",path:"#/records/session-owned"};
await loadRecordReports("session-owned");
const links=[];const walk=el=>{if(el.tagName==="a")links.push(el.href);for(const child of el.children||[])walk(child);};walk(node("records-report-list"));
console.log(JSON.stringify(links));
""")
    assert result.returncode == 0, result.stderr
    links = json.loads(result.stdout.strip().splitlines()[-1])
    assert links == [f"/api/experiment-reports/{rid}.{format_name}" for format_name in ("docx", "md", "json", "csv")]
    for link in links:
        served = devices[who].get(link)
        assert served.status_code == 200, (link, served.status_code, served.text[:200])
