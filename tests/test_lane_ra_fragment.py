"""Lane RA, decision (d): a fragment that ends in an unfinished Korean ending is
held for the words that follow within two seconds and joined with them, as one
turn (text level; the sound-level endpoint is lane SP2's). Nothing is live.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import patch

from tests.lane_ra_support import (
    AGITATION_STEPS,
    FIRST,
    ON,
    ROLE_ENV,
    FakeRoles,
    RouterFirstVoiceMixin,
    agitation_fixture,
    route,
    session_at,
    terminal,
)
from tests.protocol_vocabulary_support import SOURCE_PDF
from tests.router_fakes import FakeRouterClient, answer_call_reply, tool_reply
from voiney_lab.llm_router import FRAGMENT_JOIN_SECONDS, incomplete_fragment


class FragmentTests(unittest.TestCase):
    """Decision (d): the text-level joining of a fragment cut by the endpoint."""

    def test_unfinished_korean_endings(self) -> None:
        self.assertEqual(FRAGMENT_JOIN_SECONDS, 2.0)
        for said in ("어 그러니까 음 지금 이 단계에서 쓰는", "젤 플러그를 37.2도에서 790 rpm 으로",
                     "이 단계 하고 나서", "용액 B를", "그러니까 음", "다음 단계는"):
            with self.subTest(said):
                self.assertTrue(incomplete_fragment(said))
        for said in ("용액 B 가 뭔지 설명해 줘", "젤 플러그를 37.2도에서 790 rpm 으로 교반했어",
                     "다음", "잠깐", "온도는 37도", "15분 돌렸어", "1단계 완료했어", "네", "아니 아직"):
            with self.subTest(said):
                self.assertFalse(incomplete_fragment(said))


@unittest.skipUnless(
    SOURCE_PDF.is_file(),
    f"requires the externally licensed Candidate A source PDF at {SOURCE_PDF}",
)
class FragmentVoiceTests(RouterFirstVoiceMixin, unittest.TestCase):
    """Over the WebSocket: held, joined, or processed alone."""

    def test_a_fragment_is_joined_with_the_words_that_follow(self) -> None:
        import voiney_lab.server as server_mod

        async def steps(socket, listener, say, seen):
            await say(1, "프로토콜 시작해줘")
            listener.turn_committed_at[2] = listener.clock()
            # The fragment's turn waits for the next words; they come, and
            # supersede it, before the window closes.
            listener.active_turn_id = 2
            listener.detector.state = server_mod.TurnState.PROCESSING
            fragment = asyncio.create_task(server_mod.run_turn(
                socket, listener, b"\0\0", 2, listener.generation,
                accepted_transcription=server_mod.Transcription("어 그러니까 음 지금 이 단계에서 쓰는", "ko"),
                accepted_stt_ms=1,
            ))
            await asyncio.sleep(0.05)
            seen["held"] = listener.held_fragment()
            listener.turn_committed_at[3] = listener.clock()
            listener.active_turn_id = 3
            fragment.cancel()
            try:
                await fragment
            except asyncio.CancelledError:
                pass
            await say(3, "용액 A 가 뭔지 설명해 줘")
            seen["held_after"] = listener.held_fragment()

        socket, seen, client = self._run(steps, script=(answer_call_reply(
            "Solution A는 25mM AMBIC과 acetonitrile을 2:1로 섞은 세척 용액이에요.",
            evidence_ids=("S2.current_step",),
        ),))
        self.assertEqual(seen["held"]["text"], "어 그러니까 음 지금 이 단계에서 쓰는")
        self.assertEqual(socket.for_turn(2, "reply.delta"), [])
        joined = socket.for_turn(3, "turn.fragment_joined")
        self.assertEqual(len(joined), 1)
        self.assertEqual(joined[0]["text"], "어 그러니까 음 지금 이 단계에서 쓰는 용액 A 가 뭔지 설명해 줘")
        interpreted = socket.for_turn(3, "turn.interpretation")[-1]
        self.assertEqual(interpreted["raw_transcript"], "어 그러니까 음 지금 이 단계에서 쓰는 용액 A 가 뭔지 설명해 줘")
        self.assertEqual(len(socket.for_turn(3, "reply.delta")), 1)
        self.assertIsNone(seen["held_after"])
    def test_a_fragment_nobody_continues_is_processed_alone(self) -> None:
        import voiney_lab.server as server_mod

        async def steps(socket, listener, say, seen):
            await say(1, "프로토콜 시작해줘")
            with patch.object(server_mod, "FRAGMENT_HOLD_SECONDS", 0.1):
                await say(2, "어 그러니까 음 지금 이 단계에서 쓰는")

        socket, seen, client = self._run(steps, script=(answer_call_reply(
            "PDF에서 확인할 수 없어요.", source_kind="none"),))
        self.assertEqual(len(socket.for_turn(2, "reply.delta")), 1)
        self.assertEqual(client.requests[-1]["messages"][-1]["content"], "어 그러니까 음 지금 이 단계에서 쓰는")


if __name__ == "__main__":
    unittest.main()
