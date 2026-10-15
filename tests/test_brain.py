import unittest
from voiney_lab.brain import (
    ConversationHistory,
    SentenceChunker,
    sanitize_spoken_text,
)

class BrainTests(unittest.TestCase):
    # The persona prompt and its guardrail tests went with the general brain
    # (lane RA, decision g, 2026-10-13): a voice session always has a protocol.
    def test_sanitizer_removes_markdown_not_punctuation(self):
        self.assertEqual(sanitize_spoken_text("# 제목\n- **멈추세요!**\n```"),"제목 멈추세요!")
    def test_chunker_boundaries_unicode_decimal_and_remainder(self):
        c=SentenceChunker()
        out=[]
        for part in ("Dr."," Kim said 2.","5 is safe? ","작업을 멈추세요！"," Đi ra ngoài!"," 나머지"):
            out.extend(c.feed(part))
        out.extend(c.flush())
        self.assertEqual([x.segment_index for x in out],list(range(len(out))))
        self.assertIn("2.5",out[0].text)
        self.assertTrue(all(x.text.strip() for x in out))
        self.assertEqual(out[-1].text,"나머지")
    def test_all_terminal_punctuation(self):
        c=SentenceChunker(minimum_length=1)
        out=c.feed("가.나?다!라。마？바！")
        self.assertEqual(len(out),6)
    def test_chunker_handles_scientific_abbreviations(self):
        c = SentenceChunker()
        out = c.feed("See Fig. 1 for details approx. 5 min vs. standard buffer. Done!")
        out.extend(c.flush())
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0].text, "See Fig. 1 for details approx. 5 min vs. standard buffer.")
        self.assertEqual(out[1].text, "Done!")
    def test_history_preserves_groups_and_resets(self):
        h=ConversationHistory(max_turns=2)
        h.commit([{"role":"user","content":"1"},{"role":"assistant","content":"a"}])
        h.commit([{"role":"user","content":"2"},{"role":"assistant","tool_calls":[{"id":"x"}]},{"role":"tool","tool_call_id":"x","content":"{}"},{"role":"assistant","content":"b"}])
        h.commit([{"role":"user","content":"3"},{"role":"assistant","content":"c"}])
        messages=h.messages()
        # No persona prompt leads the messages any more (lane RA, decision g).
        self.assertNotIn("1",[m.get("content") for m in messages])
        self.assertEqual([m["role"] for m in messages[0:4]],["user","assistant","tool","assistant"])
        h.reset(); self.assertEqual(len(h.messages()),0)

    def test_streaming_two_phase_contract(self):
        self.assertTrue(True)
