"""Lane HEAR2, decision 6 (2026-10-26): the current screen names why 목소리 맞추기 failed.

The person's test met "맞추지 못했어요" ten times with no reason: the server
answered 500 (lane HEAR2 report, section 1-5). Through the production page
script, each refusal the server gives, and a server that could not keep the
measurement, is said in plain words with what to do.
"""

from __future__ import annotations

import unittest

from tests.test_screen_cleanup import run_page_script

SETUP = r"""
sessionActive=false;
recordCalibrationPcm=async()=>({pcm:new Int16Array(16000*5),micId:"mic-0011aabb"});
globalThis.btoa=value=>Buffer.from(value,"binary").toString("base64");
const line=()=>node("voice-calibration-setting-status").textContent;
"""


class CalibrationFailureWordsTests(unittest.TestCase):
    def test_each_refusal_is_named_in_plain_words(self) -> None:
        result = run_page_script(SETUP + r"""
globalThis.fetch=async()=>json({detail:{code:"too_little_voice"}},false);
assert(!(await measureVoiceInSettings()),"a refused measurement counted");
assert(line().includes("조용한 곳에서 문장을 끝까지 읽어 주세요"),`too little: ${line()}`);
globalThis.fetch=async()=>json({detail:{code:"audio_length"}},false);
assert(!(await measureVoiceInSettings()),"a refused measurement counted");
assert(line().includes("녹음이 너무 짧았어요"),`short: ${line()}`);
globalThis.fetch=async()=>({...json({detail:"workspace_error"},false),status:500});
assert(!(await measureVoiceInSettings()),"a failed measurement counted");
assert(line().includes("지금은 맞출 수 없어요")&&line().includes("잠시 뒤 다시 해 주세요"),`server: ${line()}`);
assert(!line().includes("workspace_error")&&!line().includes("500"),`a code reached the screen: ${line()}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
