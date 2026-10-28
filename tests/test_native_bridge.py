"""Lane APP0: the store app's native bridge, run from pytest.

``src/voiney_lab/static/native-bridge.js`` turns the server's step timer into
device notifications (one minute before and at the end). Its schedule
calculation is pinned by ``tests/native_bridge/timer_alerts.test.js`` under
``node --test``; this runs that file so the Python suite (and CI) covers it.

Node prints its closing summary in one of two shapes: TAP (``# fail 0``) or
spec (``ℹ fail 0``). Which one depends on the Node version: Node 22 prints TAP
when its output is not a terminal, while the Node 24 on the GitHub runner
image ubuntu-26.04 prints spec (lane CIFIX, 2026-10-25). The verdict reads
either shape and needs the exit code too, so a failing node test fails here
whatever the version. No ``--test-reporter`` is passed: Node refuses one on
the command line beside one in ``NODE_OPTIONS`` (their counts must match).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from voiney_lab import curated_protocol

ROOT = Path(__file__).resolve().parents[1]
#: Every node test of the bridge (lane APP1 added the alarm clock, the
#: background microphone and the app login beside the timer alerts).
NODE_TESTS = sorted((ROOT / "tests" / "native_bridge").glob("*.test.js"))
# Parametrizing an empty list would skip the body and hide a missing suite.
assert NODE_TESTS, "native bridge suite contains no test files"
#: A summary line of ``node --test``, TAP (``# pass 43``) or spec (``ℹ pass 43``),
#: at the start of a line (nested TAP subtests are indented).
SUMMARY_LINE = re.compile(r"^(?:#|\u2139)[ \t]+(tests|pass|fail|cancelled)[ \t]+(\d+)[ \t]*$", re.MULTILINE)
#: Terminal colour codes, which Node adds to the spec lines when colour is forced.
COLOUR_CODE = re.compile(r"\x1b\[[0-9;]*m")


def node_test_summary(output: str) -> dict[str, int]:
    """The counts in the closing summary of ``node --test``, in either shape."""

    counts: dict[str, int] = {}
    for name, value in SUMMARY_LINE.findall(COLOUR_CODE.sub("", output)):
        counts[name] = int(value)  # the closing summary is the last one
    return counts


def node_tests_failure(returncode: int, output: str) -> str | None:
    """Why a ``node --test`` run did not pass, or None when every test passed."""

    counts = node_test_summary(output)
    if returncode != 0:
        return f"node --test exited with {returncode}"
    missing = [name for name in ("tests", "pass", "fail", "cancelled") if name not in counts]
    if missing:
        return f"no summary line for {', '.join(missing)} (neither TAP '# …' nor spec 'ℹ …')"
    if counts["tests"] == 0 or counts["pass"] == 0:
        return "node --test ran no passing test"
    if counts["fail"] or counts["cancelled"]:
        return f"{counts['fail']} failed and {counts['cancelled']} cancelled node tests"
    return None


@pytest.mark.parametrize("node_test", NODE_TESTS, ids=lambda path: path.stem)
def test_native_bridge_timer_alerts_under_node(node_test: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed; run `node --test tests/native_bridge/*.test.js`")
    # Bound each file independently; an entire suite of successful files
    # may take over 120 seconds on a contended host.
    result = subprocess.run(
        [node, "--test", str(node_test)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    failure = node_tests_failure(result.returncode, result.stdout)
    assert failure is None, f"{failure}\n{result.stdout}{result.stderr}"


TAP_PASSED = "ok 43 - the notifier\n1..43\n# tests 43\n# suites 0\n# pass 43\n# fail 0\n# cancelled 0\n# skipped 0\n"
SPEC_PASSED = "\u2714 the notifier (6.1ms)\n\u2139 tests 43\n\u2139 suites 0\n\u2139 pass 43\n\u2139 fail 0\n\u2139 cancelled 0\n"
#: The spec summary when colour is forced (``FORCE_COLOR=1``): each line is wrapped
#: in escape codes.
SPEC_PASSED_IN_COLOUR = "".join(
    f"\x1b[34m\u2139 {name} {value}\x1b[39m\n"
    for name, value in (("tests", 43), ("suites", 0), ("pass", 43), ("fail", 0), ("cancelled", 0))
)
TAP_FAILED = (
    "not ok 1 - one alert\n  ---\n  # fail 0\n  ...\n1..43\n"
    "# tests 43\n# pass 42\n# fail 1\n# cancelled 0\n"
)
SPEC_FAILED = "\u2716 one alert (2ms)\n\u2139 tests 43\n\u2139 pass 42\n\u2139 fail 1\n\u2139 cancelled 0\n"


@pytest.mark.parametrize(
    "output", [TAP_PASSED, SPEC_PASSED, SPEC_PASSED_IN_COLOUR], ids=["tap", "spec", "spec-in-colour"]
)
def test_node_summary_passes_in_either_shape(output: str) -> None:
    """The CI runner's Node prints spec, a local Node 22 prints TAP: both pass."""

    assert node_tests_failure(0, output) is None


@pytest.mark.parametrize("output", [TAP_FAILED, SPEC_FAILED], ids=["tap", "spec"])
def test_node_summary_fails_on_a_failed_node_test(output: str) -> None:
    """A failed node test fails in either shape, even if the exit code said 0
    (an indented ``# fail 0`` inside a TAP subtest does not count)."""

    assert node_tests_failure(0, output) == "1 failed and 0 cancelled node tests"
    assert node_tests_failure(1, output) == "node --test exited with 1"


@pytest.mark.parametrize(
    "output",
    ["", "\u2714 the notifier\n", "# tests 0\n# pass 0\n# fail 0\n# cancelled 0\n"],
    ids=["empty", "no-summary", "no-tests"],
)
def test_node_summary_without_passing_tests_fails(output: str) -> None:
    """An output with no summary (a third shape) or no test is not a pass."""

    assert node_tests_failure(0, output) is not None


def test_native_bridge_last_minute_rule_is_the_servers() -> None:
    """The bridge's default rule is the one the server tells (and sends in its heartbeat)."""

    source = (ROOT / "src" / "voiney_lab" / "static" / "native-bridge.js").read_text(encoding="utf-8")
    found = re.search(r"LAST_MINUTE_RULE = Object\.freeze\(\{ fromSeconds: (\d+), seconds: (\d+) \}\)", source)
    assert found is not None
    assert int(found.group(1)) == curated_protocol.TIMER_LAST_MINUTE_FROM_SECONDS
    assert int(found.group(2)) == curated_protocol.TIMER_LAST_MINUTE_SECONDS
