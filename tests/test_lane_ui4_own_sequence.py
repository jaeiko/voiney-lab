"""Lane UI4 (2026-10-23): lane OWN's screen hand-over ③ (PR #114, ~/reports/lane_own_report.md 5절).

A record is named "실험 기록 · {제목} · {날짜 시각}" and, from a day's second record, " · N번째"
(lane U3). Without the server's count (a record whose owner is not known has none since lane OWN,
and an older server sends none) or when the server's UTC day is not the local day, the page
counted the records this browser had seen -- across accounts and protocols, since the browser
does not know whose they were. Only the server's count is shown now; without it, no number.

Shares tests/test_screen_cleanup.py's fake DOM.
"""

from __future__ import annotations

import unittest

from tests.test_screen_cleanup import INDEX, run_page_script


class ServerSequenceOnlyTests(unittest.TestCase):

    def test_records_without_the_servers_count_are_not_numbered_by_the_browser(self) -> None:
        result = run_page_script(r"""
process.env.TZ="Asia/Seoul";
const first={report_id:"ER-1",protocol_id:"p-1",protocol_title:"Gamma",started_at:"2026-10-06T01:00:00+00:00"};
const second={report_id:"ER-2",protocol_id:"p-1",protocol_title:"Gamma",started_at:"2026-10-06T03:00:00+00:00"};
experimentReportLabel(first,[first]);
const label=experimentReportLabel(second,[second]);
assert(label==="실험 기록 · Gamma · 2026-10-06 12:00",`label: ${label}`);
// The server's UTC day not the local one: no number either.
const early={...second,report_id:"ER-3",started_at:"2026-10-05T23:30:00+00:00",day_sequence:3,day_sequence_date:"2026-10-05"};
assert(experimentReportLabel(early,[early])==="실험 기록 · Gamma · 2026-10-06 08:30",experimentReportLabel(early,[early]));
// The server's count on the local day is shown as before.
const counted={...second,day_sequence:2,day_sequence_date:"2026-10-06"};
assert(experimentReportLabel(counted,[counted])==="실험 기록 · Gamma · 2026-10-06 12:00 · 2번째",experimentReportLabel(counted,[counted]));
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_page_keeps_no_count_of_its_own(self) -> None:
        script = INDEX.read_text(encoding="utf-8").split("<script>", 1)[1]
        self.assertNotIn("experimentReportsSeen", script)


if __name__ == "__main__":
    unittest.main()
