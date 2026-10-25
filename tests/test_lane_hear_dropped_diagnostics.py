"""Lane HEAR (2026-10-25): the opt-in STT diagnostic recording keeps dropped turns too.

The person's test: 80 of 98 transcriptions came back empty, and the
recording (``VOINEY_LAB_STT_DIAGNOSTICS_ENABLED``) kept only turns that
produced words -- so the sound behind the main problem was never kept. With
the recording on, a turn dropped for an empty transcript or as
``language_unclear`` is kept as well, its ``endpoint_reason`` naming the
drop. With it off (the default) nothing is kept, as before.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from tests.test_lane_hear_drops import DropHarness, utterance


class DroppedDiagnosticsTests(DropHarness):
    def run_with(self, heard: str, *, enabled: bool):
        session, clock = self.listener()
        kept: list[dict] = []

        def persist(pcm, metadata, *, identity, settings=None):
            kept.append({"identity": identity, **metadata, "bytes": len(pcm)})
            return None

        environment = {"VOINEY_LAB_STT_DIAGNOSTICS_ENABLED": "true" if enabled else "false"}
        with patch.dict("os.environ", environment), patch(
            "voiney_lab.server.persist_stt_diagnostic", side_effect=persist,
        ):
            self.run_socket(session, clock, [{"bytes": utterance(0.1)}, {"sleep": 0.3}], heard=lambda pcm: heard)
        return kept

    def test_an_empty_transcript_is_kept_when_the_recording_is_on(self) -> None:
        kept = self.run_with("", enabled=True)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["endpoint_reason"], "dropped_empty_transcript")
        self.assertEqual(kept[0]["raw_transcript"], "")
        self.assertGreater(kept[0]["bytes"], 0)
        self.assertNotIn("transcript", {k for k in kept[0] if k.endswith("transcript") and k != "raw_transcript"})

    def test_nothing_is_kept_when_the_recording_is_off(self) -> None:
        self.assertEqual(self.run_with("", enabled=False), [])


if __name__ == "__main__":
    unittest.main()
