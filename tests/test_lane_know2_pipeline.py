"""Lane KNOW2: rebuilding the public bundle from its raw cache (2026-10-26).

The clean-up of the bundle (person's decisions 3 and 4: OpenStax chapters,
broken pieces, the ILO cards) changes how the raw material is cut, not the
material. ``collect --offline`` rebuilds the bundle from the raw cache with no
request at all, so the change is measured on the same material. The raw
cache also has to survive a move: lane KNOW's cache was written in its
worktree and copied to the main checkout, and every item still named the old
absolute path, so nothing cached could be read there.

Fake-backed: the transport is lane KNOW's dictionary of canned replies.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.test_lane_know_pipeline import _collect_fixture, pk


def _records(runtime: Path) -> list[dict]:
    return [json.loads(line) for line in (runtime / "bundle" / "chunks.jsonl").read_text(encoding="utf-8").splitlines()]


class OfflineTests(unittest.TestCase):
    def _online(self, root: Path):
        sources, transport = _collect_fixture(root)
        http = pk.Http(sources.user_agent, transport=transport, sleep=lambda s: None)
        pk.collect(sources, runtime_dir=root / "runtime", http=http, log=lambda line: None)
        return sources, transport

    def test_an_offline_run_sends_nothing_and_rebuilds_the_same_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sources, transport = self._online(root)
            before = _records(root / "runtime")
            transport.calls.clear()
            manifest = pk.collect(sources, runtime_dir=root / "runtime", http=pk.OfflineHttp(), offline=True,
                                  log=lambda line: None)
            self.assertEqual(transport.calls, [])
            self.assertEqual(manifest["totals"]["requests"], 0)
            self.assertEqual([r["id"] for r in _records(root / "runtime")], [r["id"] for r in before])

    def test_a_moved_raw_cache_is_still_read(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sources, _transport = self._online(root)
            before = _records(root / "runtime")
            moved = root / "elsewhere"
            shutil.move(str(root / "runtime"), str(moved))
            manifest = pk.collect(sources, runtime_dir=moved, http=pk.OfflineHttp(), offline=True,
                                  log=lambda line: None)
            self.assertEqual(manifest["totals"]["failures"], 0)
            self.assertEqual([r["id"] for r in _records(moved)], [r["id"] for r in before])

    def test_the_command_line_takes_offline(self) -> None:
        args = pk.build_parser().parse_args(["collect", "--offline"])
        self.assertTrue(args.offline)


if __name__ == "__main__":
    unittest.main()
