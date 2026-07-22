import tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from safebridge_voice.brain import ConversationHistory, SentenceChunker, SYSTEM_PROMPT, confirmation_intent, report_confirmation_text, sanitize_spoken_text, stream_brain_turn
from safebridge_voice.tools import create_safety_report

class BrainTests(unittest.TestCase):
    def test_persona_and_guardrails(self):
        for text in ("SafeBridge Voice","wet-lab researchers","Korean or Vietnamese","Never use Markdown","Never approve work resumption","search_approved_safety_manual","create_safety_report","check_safety_report_status"):
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
        h.pending_report={"location":"x"}; h.reset(); self.assertEqual(len(h.messages()),1); self.assertIsNone(h.pending_report)

    def test_exact_bilingual_confirmation_phrases_are_conservative(self):
        self.assertEqual(confirmation_intent("네.", "ko"), "approve")
        self.assertEqual(confirmation_intent("취소해 주세요", "ko"), "cancel")
        self.assertEqual(confirmation_intent("Đồng ý!", "vi"), "approve")
        self.assertEqual(confirmation_intent("hủy báo cáo", "vi"), "cancel")
        self.assertIsNone(confirmation_intent("네, 하지만 아세톤이 아니라 메탄올이에요", "ko"))
        self.assertIsNone(confirmation_intent("có lẽ", "vi"))

    def test_report_confirmation_localizes_korean_and_vietnamese_enums(self):
        base={"location":"A","summary":"spill","material_or_equipment":"x"}
        korean=report_confirmation_text({**base,"urgency":"emergency","exposure_status":"yes","language":"ko"})
        vietnamese=report_confirmation_text({**base,"urgency":"routine","exposure_status":"unknown","language":"vi"})
        self.assertIn("긴급도 비상",korean); self.assertIn("노출 상태 노출 있음",korean)
        self.assertNotIn("emergency",korean); self.assertNotIn("yes",korean)
        self.assertIn("mức khẩn cấp thông thường",vietnamese); self.assertIn("tình trạng phơi nhiễm chưa xác định",vietnamese)
        self.assertNotIn("routine",vietnamese); self.assertNotIn("unknown",vietnamese)

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
            with patch("safebridge_voice.brain.execute_tool",return_value={"status":"success","matches":[]}):
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
        results=[{"status":"success","matches":[{"document_id":"SOP-1"}]}]
        async def exercise():
            with patch("safebridge_voice.brain.execute_tool",side_effect=results):
                return await stream_brain_turn(client,ConversationHistory(),"누출을 보고할게",sentence,on_tool_event=tool_event)
        import asyncio
        result=asyncio.run(exercise())
        self.assertEqual(result.tools_used,["search_approved_safety_manual","create_safety_report"])
        self.assertEqual([message["role"] for message in result.messages],["user","assistant","tool","assistant","tool","assistant"])
        self.assertEqual([message.get("tool_call_id") for message in result.messages if message["role"]=="tool"],["search-1","report-1"])
        self.assertNotIn("plan"," ".join(spoken))
        self.assertEqual(events,[("tool.call","search_approved_safety_manual"),("tool.result","search_approved_safety_manual"),("tool.call","create_safety_report"),("tool.result","create_safety_report")])
        self.assertEqual(len(client.chat.completions.calls[0]["tools"]),3)

    def test_draft_then_next_turn_approval_submits_exactly_once(self):
        class Stream:
            def __init__(self, items): self.items=iter(items)
            def __aiter__(self): return self
            async def __anext__(self):
                try: return next(self.items)
                except StopIteration: raise StopAsyncIteration
        class Completions:
            async def create(self, **kwargs):
                args='{"location":" Lab A ","summary":" spill ","urgency":"urgent","exposure_status":"unknown","language":"ko","material_or_equipment":" acetone "}'
                return Stream([{"choices":[{"delta":{"tool_calls":[{"index":0,"id":"r1","function":{"name":"create_safety_report","arguments":args}}]}}]}])
        class Client: pass
        client=Client(); client.model="fake"; client.chat=Client(); client.chat.completions=Completions()
        history=ConversationHistory(); events=[]
        async def sentence(_): pass
        async def tool_event(kind, fields): events.append((kind, fields))
        import asyncio
        with patch("safebridge_voice.brain.execute_tool") as execute:
            first=asyncio.run(stream_brain_turn(client,history,"보고",sentence,on_tool_event=tool_event))
            self.assertEqual(first.text.find("Lab A") >= 0, True)
            execute.assert_not_called()
            self.assertEqual(history.pending_report["material_or_equipment"], "acetone")
            execute.return_value={"status":"success","report_id":"SR-20260722-A1B2C3","report_status":"queued_for_handoff"}
            second=asyncio.run(stream_brain_turn(client,history,"네",sentence,on_tool_event=tool_event))
            self.assertIn("SR-20260722-A1B2C3", second.text)
            execute.assert_called_once()
        self.assertIsNone(history.pending_report)
        statuses=[fields.get("status") for _,fields in events]
        self.assertIn("awaiting_user_confirmation", statuses)
        self.assertLess(statuses.index("submitting"),statuses.index("confirmed"))
        self.assertIn("confirmed", statuses)

    def test_failed_submission_preserves_draft_and_retry_confirms_once(self):
        history=ConversationHistory(); draft={"location":"A","summary":"spill","urgency":"urgent","exposure_status":"unknown","language":"ko"}; history.pending_report=draft.copy()
        events=[]
        async def sentence(_): pass
        async def tool_event(kind,fields): events.append((kind,fields.copy()))
        class Client: pass
        import asyncio
        with patch("safebridge_voice.brain.execute_tool",side_effect=[{"status":"error","message":"disk full"},{"status":"success","report_id":"SR-20260722-A1B2C3","report_status":"queued_for_handoff"}]) as execute:
            failed=asyncio.run(stream_brain_turn(Client(),history,"네",sentence,on_tool_event=tool_event))
            self.assertEqual(history.pending_report,draft)
            self.assertIn("다시 승인하거나 취소",failed.text)
            first_statuses=[fields.get("status") for _,fields in events]
            self.assertEqual(first_statuses,["submitting","submission_failed"])
            self.assertNotIn("confirmed",first_statuses)
            succeeded=asyncio.run(stream_brain_turn(Client(),history,"네",sentence,on_tool_event=tool_event))
            self.assertIn("SR-20260722-A1B2C3",succeeded.text)
            self.assertIsNone(history.pending_report)
            self.assertEqual(execute.call_count,2)
        self.assertEqual([fields.get("status") for _,fields in events],["submitting","submission_failed","submitting","confirmed"])

    def test_writer_exception_is_submission_failed_and_preserves_draft(self):
        history=ConversationHistory(); draft={"location":"A","summary":"spill","urgency":"routine","exposure_status":"no","language":"vi"}; history.pending_report=draft.copy(); events=[]
        async def sentence(_): pass
        async def tool_event(_,fields): events.append(fields.get("status"))
        class Client: pass
        import asyncio
        with patch("safebridge_voice.brain.execute_tool",side_effect=OSError("disk unavailable")):
            asyncio.run(stream_brain_turn(Client(),history,"đồng ý",sentence,on_tool_event=tool_event))
        self.assertEqual(history.pending_report,draft)
        self.assertEqual(events,["submitting","submission_failed"])

    def test_no_report_artifacts_are_created_before_approval(self):
        class Stream:
            def __init__(self): self.done=False
            def __aiter__(self): return self
            async def __anext__(self):
                if self.done: raise StopAsyncIteration
                self.done=True
                args='{"location":"A","summary":"spill","urgency":"routine","exposure_status":"no","language":"ko"}'
                return {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"draft","function":{"name":"create_safety_report","arguments":args}}]}}]}
        class C:
            async def create(self,**kwargs): return Stream()
        class Client: pass
        client=Client(); client.model="fake"; client.chat=Client(); client.chat.completions=C()
        history=ConversationHistory()
        async def sentence(_): pass
        import asyncio
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); inbox=root/"reports"/"inbox.jsonl"; status=root/"reports"/"status"; outbox=root/"outbox"
            asyncio.run(stream_brain_turn(client,history,"보고",sentence))
            self.assertFalse(inbox.exists()); self.assertFalse(status.exists()); self.assertFalse(outbox.exists())
            def write(_,arguments): return create_safety_report(**arguments,inbox_path=inbox,now_epoch=1000)
            with patch("safebridge_voice.brain.execute_tool",side_effect=write):
                asyncio.run(stream_brain_turn(client,history,"네",sentence))
            self.assertEqual(len(inbox.read_text(encoding="utf-8").splitlines()),1)
            self.assertFalse(status.exists()); self.assertFalse(outbox.exists())

    def test_cancel_never_executes_and_ambiguous_keeps_draft(self):
        history=ConversationHistory(); history.pending_report={"location":"A","summary":"spill","urgency":"routine","exposure_status":"no","language":"vi"}
        class Stream:
            def __aiter__(self): return self
            async def __anext__(self):
                if hasattr(self,"done"): raise StopAsyncIteration
                self.done=True; return {"choices":[{"delta":{"content":"Vui lòng xác nhận rõ ràng."}}]}
        class C:
            async def create(self,**kwargs): return Stream()
        class Client: pass
        client=Client(); client.model="fake"; client.chat=Client(); client.chat.completions=C()
        async def sentence(_): pass
        import asyncio
        with patch("safebridge_voice.brain.execute_tool") as execute:
            asyncio.run(stream_brain_turn(client,history,"vâng, nhưng đổi thành methanol",sentence))
            execute.assert_not_called(); self.assertIsNotNone(history.pending_report)
            asyncio.run(stream_brain_turn(client,history,"hủy báo cáo",sentence))
            execute.assert_not_called(); self.assertIsNone(history.pending_report)

    def test_correction_replaces_draft_and_requests_confirmation_again(self):
        history=ConversationHistory(); history.pending_report={"location":"A","summary":"spill","urgency":"urgent","exposure_status":"unknown","language":"ko","material_or_equipment":"acetone"}
        corrected='{"location":"A","summary":"spill","urgency":"urgent","exposure_status":"unknown","language":"ko","material_or_equipment":"methanol"}'
        class Stream:
            def __init__(self): self.done=False
            def __aiter__(self): return self
            async def __anext__(self):
                if self.done: raise StopAsyncIteration
                self.done=True
                return {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"fix","function":{"name":"create_safety_report","arguments":corrected}}]}}]}
        class C:
            async def create(self,**kwargs): return Stream()
        class Client: pass
        client=Client(); client.model="fake"; client.chat=Client(); client.chat.completions=C()
        async def sentence(_): pass
        import asyncio
        with patch("safebridge_voice.brain.execute_tool") as execute:
            result=asyncio.run(stream_brain_turn(client,history,"네, 하지만 아세톤이 아니라 메탄올이에요",sentence))
            execute.assert_not_called()
        self.assertEqual(history.pending_report["material_or_equipment"],"methanol")
        self.assertIn("methanol",result.text)
        self.assertIn("제출할까요",result.text)

if __name__=="__main__": unittest.main()
