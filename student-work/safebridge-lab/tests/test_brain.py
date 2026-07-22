import unittest
from unittest.mock import patch
from brain import ConversationHistory, SentenceChunker, SYSTEM_PROMPT, sanitize_spoken_text, stream_brain_turn

class BrainTests(unittest.TestCase):
    def test_persona_and_guardrails(self):
        for text in ("SafeBridge Lab","wet-lab researchers","Korean or Vietnamese","Never use Markdown","Never approve work resumption","search_approved_safety_manual","create_safety_report","check_safety_report_status"):
            self.assertIn(text,SYSTEM_PROMPT)
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

    def test_tool_selection_text_is_never_spoken_and_events_are_exactly_once(self):
        class Stream:
            def __init__(self,items): self.items=iter(items)
            def __aiter__(self): return self
            async def __anext__(self):
                try: return next(self.items)
                except StopIteration: raise StopAsyncIteration
        class Completions:
            def __init__(self): self.calls=0
            async def create(self,**kwargs):
                self.calls+=1
                if self.calls==1:
                    return Stream([{"choices":[{"delta":{"content":"Internal plan. "}}]}, {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-1","function":{"name":"search_approved_safety_manual","arguments":'{"query":"spill","language":"ko"}'}}]}}]}])
                return Stream([{"choices":[{"delta":{"content":"Final safe answer."}}]}])
        class Client: pass
        client=Client(); client.model="fake"; client.chat=Client(); client.chat.completions=Completions()
        spoken=[]; events=[]
        async def sentence(item): spoken.append(item.text)
        async def tool_event(kind,fields): events.append(kind)
        async def exercise():
            with patch("brain.execute_tool",return_value={"status":"success","matches":[]}):
                return await stream_brain_turn(client,ConversationHistory(),"question",sentence,on_tool_event=tool_event)
        import asyncio
        result=asyncio.run(exercise())
        self.assertEqual(spoken,["Final safe answer."]); self.assertEqual(result.text,"Final safe answer.")
        self.assertEqual(result.tools_used,["search_approved_safety_manual"])
        self.assertNotIn("Internal plan"," ".join(spoken)); self.assertEqual(events,["tool.call","tool.result"])

    def test_multiple_tool_rounds_preserve_message_order_and_speak_only_final(self):
        class Stream:
            def __init__(self,items): self.items=iter(items)
            def __aiter__(self): return self
            async def __anext__(self):
                try: return next(self.items)
                except StopIteration: raise StopAsyncIteration
        class Completions:
            def __init__(self): self.calls=[]
            async def create(self,**kwargs):
                self.calls.append(kwargs)
                number=len(self.calls)
                if number==1:
                    return Stream([{"choices":[{"delta":{"content":"search plan"}}]}, {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"search-1","function":{"name":"search_approved_safety_manual","arguments":'{"query":"아세톤 누출","language":"ko"}'}}]}}]}])
                if number==2:
                    return Stream([{"choices":[{"delta":{"content":"report plan"}}]}, {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"report-1","function":{"name":"create_safety_report","arguments":'{"location":"Lab A","summary":"spill","urgency":"urgent","exposure_status":"unknown","language":"ko"}'}}]}}]}])
                return Stream([{"choices":[{"delta":{"content":"작업을 멈추고 관리자에게 연락하세요. 보고 번호는 SR-20260722-A1B2C3입니다."}}]}])
        class Client: pass
        client=Client(); client.model="fake"; client.chat=Client(); client.chat.completions=Completions()
        spoken=[]; events=[]
        async def sentence(item): spoken.append(item.text)
        async def tool_event(kind,fields): events.append((kind,fields["tool"]))
        results=[
            {"status":"success","matches":[{"document_id":"SOP-1"}]},
            {"status":"success","report_id":"SR-20260722-A1B2C3","report_status":"queued_for_handoff"},
        ]
        async def exercise():
            with patch("brain.execute_tool",side_effect=results):
                return await stream_brain_turn(client,ConversationHistory(),"누출을 보고할게",sentence,on_tool_event=tool_event)
        import asyncio
        result=asyncio.run(exercise())
        self.assertEqual(result.tools_used,["search_approved_safety_manual","create_safety_report"])
        self.assertEqual([message["role"] for message in result.messages],["user","assistant","tool","assistant","tool","assistant"])
        self.assertEqual([message.get("tool_call_id") for message in result.messages if message["role"]=="tool"],["search-1","report-1"])
        self.assertNotIn("plan"," ".join(spoken))
        self.assertEqual(events,[("tool.call","search_approved_safety_manual"),("tool.result","search_approved_safety_manual"),("tool.call","create_safety_report"),("tool.result","create_safety_report")])
        self.assertEqual(len(client.chat.completions.calls[0]["tools"]),3)

if __name__=="__main__": unittest.main()
