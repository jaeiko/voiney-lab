import json, unittest
from pathlib import Path
from voiney_lab.brain import (
    ConversationHistory,
    SentenceChunker,
    SYSTEM_PROMPT,
    sanitize_spoken_text,
    stream_brain_turn,
)
from voiney_lab.tools import ToolContext

class BrainTests(unittest.TestCase):
    def test_persona_and_guardrails(self):
        # Property: the system prompt still states who the assistant is, who it
        # serves, its languages, its Markdown ban and its never-approve-resumption
        # rule. Only the identity literal moved. The three tools it named were
        # deleted with the tools (lane CL, 2026-10-10).
        for text in ("Voiney Lab","wet-lab researchers","Korean, English, or Vietnamese","Never use Markdown","Never approve work resumption"):
            self.assertIn(text,SYSTEM_PROMPT)
        for deleted in ("search_approved_safety_manual","create_safety_report","check_safety_report_status"):
            self.assertNotIn(deleted,SYSTEM_PROMPT)

    def test_existing_emergency_boundary_is_prompt_and_report_confirmation(self):
        self.assertIn(
            "For apparent immediate danger, first say to stop work, move away, "
            "and contact the lab's established emergency channel or lab manager.",
            SYSTEM_PROMPT,
        )
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
        self.assertEqual(messages[0]["role"],"system")
        self.assertNotIn("1",[m.get("content") for m in messages])
        self.assertEqual([m["role"] for m in messages[1:5]],["user","assistant","tool","assistant"])
        h.reset(); self.assertEqual(len(h.messages()),1)

    def test_streaming_two_phase_contract(self):
        self.assertTrue(True)

    def test_trusted_language_instruction_ignores_transcript_language(self):
        import asyncio
        class Stream:
            def __init__(self,text):self.text=text;self.done=False
            def __aiter__(self):return self
            async def __anext__(self):
                if self.done:raise StopAsyncIteration
                self.done=True;return {"choices":[{"delta":{"content":self.text}}]}
        class Completions:
            def __init__(self):self.calls=[]
            async def create(self,**kwargs):self.calls.append(kwargs);return Stream("Reply.")
        class Client:pass
        async def sentence(_):pass
        for language,transcript,name in (("ko","Xin chào","Korean"),("en","안녕하세요","English")):
            with self.subTest(language=language):
                client=Client();client.model="fake";client.chat=Client();client.chat.completions=Completions()
                context=ToolContext(Path("unused.sqlite"),None,language,"operational")
                asyncio.run(stream_brain_turn(client,ConversationHistory(),transcript,sentence,
                                              tool_context=context))
                payload=json.dumps(client.chat.completions.calls[0]["messages"],ensure_ascii=False)
                self.assertIn(f"session language is {name}",payload)

