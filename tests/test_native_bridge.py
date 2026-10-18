"""Lane APP0: the store app's native bridge, run from pytest.

``src/voiney_lab/static/native-bridge.js`` turns the server's step timer into
device notifications (one minute before and at the end). Its schedule
calculation is pinned by ``tests/native_bridge/timer_alerts.test.js`` under
``node --test``; this runs that file so the Python suite (and CI) covers it.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE_TESTS = ROOT / "tests" / "native_bridge" / "timer_alerts.test.js"


def test_native_bridge_timer_alerts_under_node() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed; run `node --test tests/native_bridge/timer_alerts.test.js`")
    result = subprocess.run(
        [node, "--test", str(NODE_TESTS)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "# fail 0" in result.stdout
