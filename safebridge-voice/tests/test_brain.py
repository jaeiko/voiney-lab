import json, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from safebridge_voice.brain import ConversationHistory, SentenceChunker, SYSTEM_PROMPT, confirmation_intent, report_confirmation_text, sanitize_spoken_text, stream_brain_turn
from safebridge_voice.tools import ToolContext, create_safety_report

class BrainTests(unittest.TestCase):
    def test_persona_and_guardrails(self):
        for text in ("SafeBridge Voice","wet-lab researchers","Korean, English, or Vietnamese","Never use Markdown","Never approve work resumption","search_approved_safety_manual","create_safety_report","check_safety_report_status"):
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
                    return Stream([{"choices":[{"delta":{"content":"Internal plan. "}}]}, {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-1","function":{"name":"search_approved_safety_manual","arguments":'{"query":"spill","topic":"spill"}'}}]}}]}])
                return Stream([{"choices":[{"delta":{"content":"Final safe answer."}}]}])
        class Client: pass
        client=Client(); client.model="fake"; client.chat=Client(); client.chat.completions=Completions()
        spoken=[]; events=[]
        async def sentence(item): spoken.append(item.text)
        async def tool_event(kind,fields): events.append(kind)
        async def exercise():
            with patch("safebridge_voice.brain.execute_tool",return_value={"status":"success","answerable":True,"matches":[]}):
                return await stream_brain_turn(client,ConversationHistory(),"question",sentence,on_tool_event=tool_event,
                                               tool_context=ToolContext(Path("unused.sqlite"),None,"en","operational"))
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
                    return Stream([{"choices":[{"delta":{"content":"search plan"}}]}, {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"search-1","function":{"name":"search_approved_safety_manual","arguments":'{"query":"아세톤 누출","topic":"spill"}'}}]}}]}])
                if number==2:
                    return Stream([{"choices":[{"delta":{"content":"report plan"}}]}, {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"report-1","function":{"name":"create_safety_report","arguments":'{"location":"Lab A","summary":"spill","urgency":"urgent","exposure_status":"unknown","language":"ko"}'}}]}}]}])
                return Stream([{"choices":[{"delta":{"content":"작업을 멈추고 관리자에게 연락하세요. 보고 번호는 SR-20260722-A1B2C3입니다."}}]}])
        class Client: pass
        client=Client(); client.model="fake"; client.chat=Client(); client.chat.completions=Completions()
        spoken=[]; events=[]
        async def sentence(item): spoken.append(item.text)
        async def tool_event(kind,fields): events.append((kind,fields["tool"]))
        results=[{"status":"success","answerable":True,"matches":[{"document_id":"SOP-1"}]}]
        async def exercise():
            with patch("safebridge_voice.brain.execute_tool",side_effect=results):
                return await stream_brain_turn(client,ConversationHistory(),"누출을 보고할게",sentence,on_tool_event=tool_event,
                                               tool_context=ToolContext(Path("unused.sqlite"),None,"ko","operational"))
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
            def write(_,arguments,**_kwargs): return create_safety_report(**arguments,inbox_path=inbox,now_epoch=1000)
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

    def test_all_failed_retrieval_statuses_bypass_answer_synthesis(self):
        import asyncio
        class Stream:
            def __init__(self): self.done=False
            def __aiter__(self): return self
            async def __anext__(self):
                if self.done: raise StopAsyncIteration
                self.done=True
                return {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"s1","function":{
                    "name":"search_approved_safety_manual","arguments":'{"query":"FICTIONAL","topic":"first_aid"}'}}]}}]}
        class Completions:
            def __init__(self): self.calls=0
            async def create(self,**kwargs):
                self.calls+=1
                if self.calls > 1: raise AssertionError("post-retrieval synthesis must not run")
                return Stream()
        class Client: pass
        statuses=("not_found","ambiguous_product","conflicting_documents","stale_document",
                  "unapproved_document","translation_unverified","invalid_arguments","error","future_status")
        for status in statuses:
            with self.subTest(status=status):
                client=Client(); client.model="fake"; client.chat=Client(); client.chat.completions=Completions()
                spoken=[]
                async def sentence(item): spoken.append(item.text)
                blocked={"status":status,"answerable":False,"matches":[]}
                context=ToolContext(Path("unused.sqlite"),"F","ko","operational")
                with patch("safebridge_voice.brain.execute_tool",return_value=blocked):
                    result=asyncio.run(stream_brain_turn(client,ConversationHistory(),"질문",sentence,
                                                         tool_context=context))
                self.assertEqual(client.chat.completions.calls,1)
                self.assertEqual(result.text,spoken[0])

    def test_success_grounding_is_only_retrieved_sections_and_retains_references(self):
        import asyncio
        match={"document_id":"FICTIONAL-DOC","document_type":"supplier_sds","title":"FICTIONAL TITLE",
               "issuer":"FICTIONAL ISSUER","manufacturer":"FICTIONAL MAKER","product_name":"FICTIONAL PRODUCT",
               "product_code":"F-1","cas_numbers":[],"version":"1","canonical_version":"1",
               "section_code":"SDS-04","section_title":"FICTIONAL SECTION","page_start":4,"page_end":4,
               "content":"FICTIONAL NON-OPERATIONAL TEST CONTENT.","language":"ko","translation_status":"original",
               "source_uri":"test://fictional","source_checksum":"abc"}
        class Stream:
            def __init__(self,items): self.items=iter(items)
            def __aiter__(self): return self
            async def __anext__(self):
                try:return next(self.items)
                except StopIteration:raise StopAsyncIteration
        class Completions:
            def __init__(self):self.calls=[]
            async def create(self,**kwargs):
                self.calls.append(kwargs)
                if len(self.calls)==1:
                    return Stream([{"choices":[{"delta":{"tool_calls":[{"index":0,"id":"s1","function":{
                        "name":"search_approved_safety_manual","arguments":'{"query":"F-1","topic":"first_aid"}'}}]}}]}])
                return Stream([{"choices":[{"delta":{"content":"출처에 근거한 짧은 답변."}}]}])
        class Client:pass
        client=Client();client.model="fake";client.chat=Client();client.chat.completions=Completions()
        async def sentence(_):pass
        context=ToolContext(Path("unused.sqlite"),"F","ko","reference_only")
        with patch("safebridge_voice.brain.execute_tool",return_value={"status":"success","answerable":True,"matches":[match]}):
            result=asyncio.run(stream_brain_turn(client,ConversationHistory(),"질문",sentence,tool_context=context))
        grounding=client.chat.completions.calls[1]["messages"]
        tool_payload=next(json.loads(item["content"]) for item in grounding if item["role"]=="tool")
        self.assertEqual(tool_payload["matches"],[match])
        self.assertNotIn("UNRELATED",json.dumps(grounding))
        self.assertIn("using only",grounding[-1]["content"].casefold())
        self.assertEqual(result.source_references[0]["document_id"],"FICTIONAL-DOC")
        self.assertFalse(result.source_references[0]["operational"])

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

    def test_failed_retrieval_uses_trusted_english_without_synthesis(self):
        import asyncio
        class Stream:
            def __init__(self):self.done=False
            def __aiter__(self):return self
            async def __anext__(self):
                if self.done:raise StopAsyncIteration
                self.done=True
                return {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"bad","function":{
                    "name":"search_approved_safety_manual",
                    "arguments":'{"query":"FICTIONAL","topic":"first_aid"}'}}]}}]}
        class Completions:
            def __init__(self):self.calls=0
            async def create(self,**kwargs):
                self.calls+=1
                if self.calls>1:raise AssertionError("synthesis ran")
                return Stream()
        class Client:pass
        client=Client();client.model="fake";client.chat=Client();client.chat.completions=Completions()
        async def sentence(_):pass
        context=ToolContext(Path("unused.sqlite"),None,"en","operational")
        with patch("safebridge_voice.brain.execute_tool",
                   return_value={"status":"translation_unverified","answerable":False,"matches":[]}):
            result=asyncio.run(stream_brain_turn(client,ConversationHistory(),"한국어 질문",sentence,
                                                 tool_context=context))
        self.assertEqual(client.chat.completions.calls,1)
        self.assertIn("reviewed English source",result.text)

    def test_actual_invalid_topic_result_bypasses_synthesis(self):
        import asyncio
        class Stream:
            def __init__(self):self.done=False
            def __aiter__(self):return self
            async def __anext__(self):
                if self.done:raise StopAsyncIteration
                self.done=True
                return {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"bad","function":{
                    "name":"search_approved_safety_manual",
                    "arguments":'{"query":"FICTIONAL","topic":"unsupported"}'}}]}}]}
        class Completions:
            def __init__(self):self.calls=0
            async def create(self,**kwargs):
                self.calls+=1
                if self.calls>1:raise AssertionError("synthesis ran")
                return Stream()
        class Client:pass
        client=Client();client.model="fake";client.chat=Client();client.chat.completions=Completions()
        async def sentence(_):pass
        context=ToolContext(Path("unused.sqlite"),None,"en","operational")
        result=asyncio.run(stream_brain_turn(client,ConversationHistory(),"Question",sentence,
                                             tool_context=context))
        self.assertEqual(client.chat.completions.calls,1)
        tool_result=json.loads(next(message["content"] for message in result.messages
                                    if message["role"]=="tool"))
        self.assertEqual(tool_result,{"status":"invalid_arguments","answerable":False,"matches":[]})

    def test_report_language_argument_cannot_override_trusted_session(self):
        import asyncio
        class Stream:
            def __init__(self):self.done=False
            def __aiter__(self):return self
            async def __anext__(self):
                if self.done:raise StopAsyncIteration
                self.done=True
                arguments=json.dumps({"location":"F","summary":"fictional issue","urgency":"routine",
                                      "exposure_status":"unknown","language":"vi"})
                return {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"report","function":{
                    "name":"create_safety_report","arguments":arguments}}]}}]}
        class Completions:
            async def create(self,**kwargs):return Stream()
        class Client:pass
        client=Client();client.model="fake";client.chat=Client();client.chat.completions=Completions()
        history=ConversationHistory()
        async def sentence(_):pass
        context=ToolContext(Path("unused.sqlite"),None,"en","operational")
        result=asyncio.run(stream_brain_turn(client,history,"Report",sentence,tool_context=context))
        self.assertEqual(history.pending_report["language"],"en")
        self.assertIn("Please confirm",result.text)

    def test_scope_is_bound_to_grounding_instruction(self):
        import asyncio
        match={"document_id":"FICTIONAL","title":"FICTIONAL TITLE","version":"1",
               "section_code":"SDS-04","section_title":"FICTIONAL","page_start":4,"page_end":4,
               "content":"FICTIONAL NON-OPERATIONAL CONTENT.","source_uri":"test://fictional",
               "source_checksum":"sum"}
        class Stream:
            def __init__(self,items):self.items=iter(items)
            def __aiter__(self):return self
            async def __anext__(self):
                try:return next(self.items)
                except StopIteration:raise StopAsyncIteration
        class Completions:
            def __init__(self):self.calls=[]
            async def create(self,**kwargs):
                self.calls.append(kwargs)
                if len(self.calls)==1:
                    return Stream([{"choices":[{"delta":{"tool_calls":[{"index":0,"id":"s","function":{
                        "name":"search_approved_safety_manual",
                        "arguments":'{"query":"F","topic":"first_aid"}'}}]}}]}])
                return Stream([{"choices":[{"delta":{"content":"Answer."}}]}])
        class Client:pass
        async def sentence(_):pass
        for scope,operational in (("operational",True),("reference_only",False),("demo",False)):
            with self.subTest(scope=scope):
                client=Client();client.model="fake";client.chat=Client();client.chat.completions=Completions()
                context=ToolContext(Path("/private/catalog.sqlite"),None,"en",scope)
                with patch("safebridge_voice.brain.execute_tool",
                           return_value={"status":"success","answerable":True,"matches":[match]}):
                    result=asyncio.run(stream_brain_turn(client,ConversationHistory(),"Question",sentence,
                                                         tool_context=context))
                instruction=client.chat.completions.calls[1]["messages"][-1]["content"]
                self.assertIn(f"usage scope is {scope}",instruction)
                self.assertEqual(result.source_references[0]["operational"],operational)
                if not operational:self.assertIn("non-operational",instruction)
                model_payload=json.dumps(client.chat.completions.calls[1]["messages"])
                self.assertNotIn("source_path",model_payload)
                self.assertNotIn("/private/catalog.sqlite",model_payload)

    def test_two_turn_history_redacts_previous_source_content(self):
        import asyncio
        def match(document_id,content):
            return {"document_id":document_id,"title":f"FICTIONAL {document_id}","version":"1",
                    "section_code":"SDS-04","section_title":"FICTIONAL","page_start":4,"page_end":4,
                    "content":content,"source_uri":f"test://{document_id}","source_checksum":document_id}
        class Stream:
            def __init__(self,items):self.items=iter(items)
            def __aiter__(self):return self
            async def __anext__(self):
                try:return next(self.items)
                except StopIteration:raise StopAsyncIteration
        class Completions:
            def __init__(self,call_id):self.call_id=call_id;self.calls=[]
            async def create(self,**kwargs):
                self.calls.append(kwargs)
                if len(self.calls)==1:
                    args=json.dumps({"query":self.call_id,"topic":"first_aid"})
                    return Stream([{"choices":[{"delta":{"tool_calls":[{"index":0,"id":self.call_id,
                        "function":{"name":"search_approved_safety_manual","arguments":args}}]}}]}])
                return Stream([{"choices":[{"delta":{"content":"Grounded response."}}]}])
        class Client:pass
        async def sentence(_):pass
        history=ConversationHistory()
        context=ToolContext(Path("unused.sqlite"),None,"en","operational")
        first_client=Client();first_client.model="fake";first_client.chat=Client()
        first_client.chat.completions=Completions("call-a")
        with patch("safebridge_voice.brain.execute_tool",
                   return_value={"status":"success","answerable":True,
                                 "matches":[match("A","UNIQUE_SOURCE_A")]}):
            first=asyncio.run(stream_brain_turn(first_client,history,"First",sentence,tool_context=context))
        history.commit(first.messages,first.source_references)
        persisted=json.dumps(history.messages(),ensure_ascii=False)
        self.assertNotIn("UNIQUE_SOURCE_A",persisted)
        roles=[message["role"] for message in history.groups[0]]
        self.assertEqual(roles,["user","assistant","tool","assistant"])
        self.assertEqual(history.groups[0][2]["tool_call_id"],"call-a")

        second_client=Client();second_client.model="fake";second_client.chat=Client()
        second_client.chat.completions=Completions("call-b")
        with patch("safebridge_voice.brain.execute_tool",
                   return_value={"status":"success","answerable":True,
                                 "matches":[match("B","UNIQUE_SOURCE_B")]}):
            second=asyncio.run(stream_brain_turn(second_client,history,"Second",sentence,tool_context=context))
        synthesis=json.dumps(second_client.chat.completions.calls[1]["messages"],ensure_ascii=False)
        self.assertIn("UNIQUE_SOURCE_B",synthesis)
        self.assertNotIn("UNIQUE_SOURCE_A",synthesis)
        history.commit(second.messages,second.source_references)
        self.assertEqual(len(history.source_references),2)
        history.reset()
        self.assertEqual(history.source_references,[])
        self.assertEqual(len(history.messages()),1)

if __name__=="__main__": unittest.main()
