"""Lane UI4 (2026-10-23): lane QA's QA-19 -- during the run the rail's [설정] darkened the
screen and showed nothing: the settings sheet lives in the settings tab (lane IA decision ⑥),
which is hidden while the run's tab shows, and a fixed sheet cannot leave a hidden panel. The
tab's panel now shows while that sheet is open -- only its sheet, over the run -- and is put
away when the sheet closes or the run ends.

Shares tests/test_lane_ui4_views.py's harness.
"""

from __future__ import annotations

import unittest

from tests.test_lane_ui4_views import run_views, style


class RunSettingsSheetTests(unittest.TestCase):
    """QA-19: the run's [설정] shows the settings sheet over the run."""

    def test_the_rails_settings_shows_the_settings_tabs_panel_while_its_sheet_is_open(self) -> None:
        result = run_views(r"""
await boot(base());
startRun();
assert(tab()==="experiment"&&document.body.dataset.view==="during","not on the run");
assert(node("tab-settings").hidden===true,"the settings tab shows before the sheet is opened");
node("run-open-settings").dispatch("click");
assert(document.body.dataset.sheet==="settings",`sheet: ${document.body.dataset.sheet}`);
assert(node("tab-settings").hidden===false,"the settings sheet's panel stays hidden");
assert(node("tab-experiment").hidden===false,"the run's tab was hidden");
for(const name of ["home","protocols","upload"])assert(node(`tab-${name}`).hidden===true,`${name} shows with the sheet`);
assert(node("run-open-settings").getAttribute("aria-expanded")==="true","the opener does not say it is open");
node("run-close-settings").dispatch("click");
assert(document.body.dataset.sheet===""&&node("tab-settings").hidden===true,`after closing: ${document.body.dataset.sheet} ${node("tab-settings").hidden}`);
// A redraw of the route keeps the open sheet's panel; the run's end puts it away.
node("run-open-settings").dispatch("click");renderRoute();
assert(node("tab-settings").hidden===false,"a redraw hid the open sheet");
sessionActive=false;curatedProtocolState={...runState(),active:false};renderScreenView();
assert(document.body.dataset.sheet===""&&node("tab-settings").hidden===true,"the sheet outlived the run");
// On the settings tab itself the panel is the tab's, whatever the sheet.
go("#/settings");
assert(node("tab-settings").hidden===false,"the settings tab is hidden");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_with_the_sheet_open_the_settings_tab_shows_only_its_sheet(self) -> None:
        css = style()
        self.assertIn(
            'body[data-view="during"][data-sheet="settings"]:not([data-tab="settings"]) #tab-settings>:not(#settings-sheet){display:none}',
            css)
        # The sheet itself is the one lane UI0 drew over the run.
        self.assertIn('body[data-view="during"][data-sheet="settings"] #settings-sheet,', css)


if __name__ == "__main__":
    unittest.main()
