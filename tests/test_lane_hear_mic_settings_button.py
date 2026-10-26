"""Lane HEAR, decision 6 (2026-10-25): "마이크 확인"'s [마이크 설정] opens the microphone setting.

The person's test: the button did nothing. It scrolled to and focused the
microphone list, which lives in the settings tab -- a hidden panel from the
experiment tab -- so nothing could be seen. It now opens the settings: the
settings tab before a run, the settings sheet over a run, and then puts the
focus on the microphone list.
"""

from __future__ import annotations

import unittest

from tests.test_screen_cleanup import run_page_script


class MicSettingsButtonTests(unittest.TestCase):
    def test_before_a_run_the_button_opens_the_settings_tab_at_the_microphone(self) -> None:
        result = run_page_script(r"""
let focused=0;node("mic-device").focus=()=>{focused+=1;};
globalThis.location.hash="#/experiment";readRoute();
node("mic-check-settings").dispatch("click");
await new Promise(resolve=>setTimeout(resolve,0));
assert(shownTab()==="settings",`the tab shown is ${shownTab()}`);
assert(focused>=1,"the microphone list did not get the focus");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_over_a_run_the_button_opens_the_settings_sheet(self) -> None:
        result = run_page_script(r"""
let focused=0;node("mic-device").focus=()=>{focused+=1;};
screenView=()=> "during";
node("mic-check-settings").dispatch("click");
await new Promise(resolve=>setTimeout(resolve,0));
assert(runSheet==="settings",`the run sheet is ${JSON.stringify(runSheet)}`);
assert(focused>=1,"the microphone list did not get the focus");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
