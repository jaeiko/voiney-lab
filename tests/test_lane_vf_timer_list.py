"""Lane VF, decision 7 (2026-10-10): one line per timer in the check before the start.

The in-gel source states most step times twice -- "15min" in the step line
and "00:15:00" in the duration field -- and the server verifies both, so the
start screen said "단계 타이머 13개" and listed the same step and length on two
lines (ten timers, really). Now the page groups the same step, bound and
length into one line and names every source wording beside it ("3단계 · 15분 ·
원문 ‘15min’, ‘00:15:00’"); the count is the grouped count. The server's list
and the verified data are untouched: this is display only.
"""

from __future__ import annotations

import unittest

from tests.test_screen_cleanup import run_page_script


class TimerListTests(unittest.TestCase):

    def test_the_same_step_and_length_is_one_line_with_every_source_wording(self) -> None:
        result = run_page_script(r"""
const review={protocol_id:"p",title:"In-gel",available_for_execution:true,analysis_available:true,step_count:25,
 execution_blockers:[],execution_notices:[],safety_notices:[],
 timers:{verified:[
  {step_label:"3",value_ko:"15분",bound_ko:"",choice:false,source_literal:"15min",source_page_number:4,source_excerpt:"Wash the band … for 15min at 37°C"},
  {step_label:"3",value_ko:"15분",bound_ko:"",choice:false,source_literal:"00:15:00",source_page_number:4,source_excerpt:"00:15:00"},
  {step_label:"5",value_ko:"15분",bound_ko:"",choice:false,source_literal:"15 min",source_page_number:5,source_excerpt:"15 min"},
  {step_label:"23",value_ko:"16시간",bound_ko:"",choice:false,source_literal:"overnight (16:00:00)",source_page_number:7,source_excerpt:"16:00:00"},
  {step_label:"23",value_ko:"16시간",bound_ko:"약",choice:false,source_literal:"~16 h",source_page_number:7,source_excerpt:"~16 h"},
  {step_label:"9",value_ko:"12시간 또는 16시간",bound_ko:"",choice:true,source_literal:"12-16 h",source_page_number:6,source_excerpt:"12-16 h"}],
  refused:[{step_label:"2",source_literal:"overnight",reason:"no_number",reason_ko:"숫자로 적힌 시간이 없어요(overnight, until … 같은 표현)."}]}};
renderStartSummary(review);
const host=node("protocol-step-timers");
assert(host.hidden===false,"the timer list stayed hidden");
const text=visibleText(host);
// Six verified rows, five timers: step 3's two wordings are one line.
assert(text.includes("단계 타이머 5개"),`count: ${text}`);
assert(text.includes("3단계 · 15분 · 원문 ‘15min’, ‘00:15:00’ · p.4 · 발췌 “Wash the band … for 15min at 37°C” / “00:15:00”"),`step 3 line: ${text}`);
const lines=host.children[0].children.filter(item=>item.tagName==="p").map(item=>item.textContent);
assert(lines.filter(line=>line.startsWith("3단계 · 15분")).length===1,`step 3 lines: ${lines.join(" | ")}`);
// A different step, a different bound or a choice stays its own line.
assert(lines.some(line=>line.startsWith("5단계 · 15분 · 원문 ‘15 min’")),`step 5 line: ${lines.join(" | ")}`);
assert(lines.filter(line=>line.startsWith("23단계")).length===2,`step 23 lines (16시간 and 약 16시간): ${lines.join(" | ")}`);
assert(lines.some(line=>line.startsWith("9단계 · 12시간 또는 16시간 (시작할 때 고름) · 원문 ‘12-16 h’")),`choice line: ${lines.join(" | ")}`);
// The refusals are as before.
assert(text.includes("타이머로 만들지 않은 시간 표현 1건")&&text.includes("2단계 · ‘overnight’ · 숫자로 적힌 시간이 없어요"),`refusals: ${text}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
