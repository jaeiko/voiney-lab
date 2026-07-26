import asyncio, json, unittest
from unittest.mock import patch
from collections import deque
from safebridge_voice.audio import FRAME_BYTES
from safebridge_voice.brain import (
    REPORT_CONFIRMATION_CLARIFICATION_TEXT,
    BrainResult,
    SentenceSegment,
)
from pathlib import Path
from safebridge_voice.language import Transcription
from safebridge_voice.emergency import ENGLISH_EMERGENCY_RESPONSE, KOREAN_EMERGENCY_RESPONSE
from safebridge_voice.server import ListenerSession, ServerConfig, frame_complete_audio, normalize_session_language, run_turn, server_tool_context, transcribe, validate_tts_pcm, voice_socket
from safebridge_voice.tools import ToolContext
from safebridge_voice.vad import EndpointDetector, TurnState

class FakeResponse:
    def __init__(self,content=b"",status=200,content_type="audio/pcm"):
        self.content=content; self.status_code=status; self.ok=200<=status<300
        self.headers={"content-type":content_type}; self.text=""
    def raise_for_status(self): pass
    def json(self): return {"text":"short test utterance","language":"Korean","duration":1.2}
class Decisions:
    def __init__(self,values): self.values=deque(values)
    def __call__(self,frame): return self.values.popleft()
TURN=[False,True,True,True,True,False]+[True]*8+[False]*50
def frame(n=1): return bytes([n%256])*FRAME_BYTES

class ServerTests(unittest.TestCase):
    def emergency_session(self):
        session=ListenerSession(tool_context=ToolContext(
            Path("/trusted/catalog.sqlite"),None,"ko","operational"))
        session.active=True; session.language_mode="auto"
        session.active_turn_id=1; session.detector.state=TurnState.PROCESSING
        return session

    def run_emergency(self, transcription, session=None, tts_result=b"\0\0"):
        class Socket:
            def __init__(self): self.text=[]; self.binary=[]
            async def send_text(self,value): self.text.append(json.loads(value))
            async def send_bytes(self,value): self.binary.append(value)
        session=session or self.emergency_session()
        socket=Socket()
        tts_patch=(patch("safebridge_voice.server.synthesize",side_effect=tts_result)
                   if isinstance(tts_result,BaseException)
                   else patch("safebridge_voice.server.synthesize",return_value=tts_result))
        with patch("safebridge_voice.server.transcribe",return_value=transcription), \
             tts_patch as tts, \
             patch("safebridge_voice.server.stream_brain_turn") as brain, \
             patch("safebridge_voice.tools.search_approved_safety_manual") as retrieval, \
             patch("safebridge_voice.brain.execute_tool") as execute, \
             patch("safebridge_voice.server.AsyncOpenAI") as llm:
            asyncio.run(run_turn(socket,session,b"\0\0",1,1))
        return session,socket,tts,brain,retrieval,execute,llm

    def test_server_maps_procedure_results_to_canonical_event_sequences(self):
        class Socket:
            def __init__(self): self.text=[]; self.binary=[]
            async def send_text(self,value): self.text.append(json.loads(value))
            async def send_bytes(self,value): self.binary.append(value)
        state={"attached":True,"procedure_id":"fictional-color-card-demo-ko",
               "title":"FICTIONAL NON-OPERATIONAL 색상 카드 확인 데모","version":"1.0",
               "status":"active","total_step_count":3,"completed_step_count":0,
               "current_step_number":1,"current_step_id":"blue-card",
               "current_step_title":"파란색 가상 카드",
               "approved_current_instruction":"검토된 가상 지시문"}
        cases=(
            ({"status":"success","operation":"start","idempotent":False,
              "procedure_state":state},["procedure.started","procedure.state"]),
            ({"status":"success","operation":"read","idempotent":False,
              "procedure_state":state},["procedure.state"]),
            ({"status":"success","operation":"complete","idempotent":False,
              "completed_step_id":"blue-card","procedure_completed":False,
              "procedure_state":state},["procedure.step_completed","procedure.state"]),
            ({"status":"success","operation":"complete","idempotent":False,
              "completed_step_id":"green-card","procedure_completed":True,
              "procedure_state":{**state,"status":"completed","completed_step_count":3,
                  "current_step_number":None,"current_step_id":None,
                  "current_step_title":None,"approved_current_instruction":None}},
             ["procedure.step_completed","procedure.completed","procedure.state"]),
            ({"status":"invalid_arguments","code":"invalid_arguments"},
             ["procedure.error"]),
        )
        for fields,expected in cases:
            with self.subTest(expected=expected):
                socket=Socket()
                session=ListenerSession(tool_context=ToolContext(
                    Path("/trusted/catalog.sqlite"),None,"ko","test_only"))
                session.active=True;session.active_turn_id=1
                session.detector.state=TurnState.PROCESSING
                async def fake_brain(client,history,transcript,on_sentence,on_first_token,
                                     on_tool_event,tool_context):
                    await on_tool_event("tool.result",{"tool":"start_procedure",**fields})
                    await on_sentence(SentenceSegment(0,"가상 응답입니다."))
                    return BrainResult([],"가상 응답입니다.",0,["start_procedure"])
                with patch("safebridge_voice.server.transcribe",
                           return_value=Transcription("가상 데모를 시작해 주세요","ko")), \
                     patch("safebridge_voice.server.synthesize",return_value=b"\0\0"), \
                     patch("safebridge_voice.server.stream_brain_turn",
                           side_effect=fake_brain), \
                     patch("safebridge_voice.server.AsyncOpenAI"), \
                     patch("safebridge_voice.server.require_env",return_value="test"):
                    asyncio.run(run_turn(socket,session,b"\0\0",1,1))
                procedure_events=[
                    item["type"] for item in socket.text
                    if item["type"].startswith("procedure.")]
                self.assertEqual(procedure_events,expected)
                done=[item for item in socket.text if item["type"]=="turn.done"][0]
                self.assertEqual(done["route"],"brain")

    def test_emergency_precedes_language_resolution_and_uses_fixed_language(self):
        cases=(
            (Transcription("불이 났어요. 어떻게 해야 돼요?",None),"ko",KOREAN_EMERGENCY_RESPONSE),
            (Transcription("There is a fire, what should I do?",None),"en",ENGLISH_EMERGENCY_RESPONSE),
            (Transcription("도와줘!",None),"ko",KOREAN_EMERGENCY_RESPONSE),
            (Transcription("Emergency!",None),"en",ENGLISH_EMERGENCY_RESPONSE),
            (Transcription("도와줘!","ja"),"ko",KOREAN_EMERGENCY_RESPONSE),
            (Transcription("Emergency!","unsupported"),"en",ENGLISH_EMERGENCY_RESPONSE),
        )
        for transcription,language,response in cases:
            with self.subTest(transcription=transcription):
                with patch("safebridge_voice.server.resolve_turn_language") as resolver:
                    session,socket,tts,brain,retrieval,execute,llm=self.run_emergency(transcription)
                resolver.assert_not_called()
                self.assertEqual(tts.call_args.args,(response,language))
                for boundary in (brain,retrieval,execute,llm): boundary.assert_not_called()
                event_types=[item["type"] for item in socket.text]
                self.assertNotIn("session.language_confirmation_required",event_types)
                self.assertNotIn("session.turn_language_resolved",event_types)
                self.assertNotIn("tool.call",event_types)
                self.assertNotIn("tool.result",event_types)
                self.assertEqual([item["text"] for item in socket.text
                                  if item["type"]=="reply.delta"],[response])
                self.assertEqual(len(socket.binary),1)
                self.assertEqual([item["text"] for item in socket.text
                                  if item["type"]=="reply.complete"],[response])
                audio_complete=next(item for item in socket.text
                                    if item["type"]=="audio.complete")
                self.assertEqual(audio_complete["segment_count"],1)
                done=next(item for item in socket.text if item["type"]=="turn.done")
                self.assertEqual(done["route"],"deterministic_emergency")
                self.assertIn("first_audio_ms",done["timings_ms"])
                self.assertEqual(done["segment_count"],1)
                self.assertGreater(done["output_frames"],0)
                self.assertEqual(done["tools_used"],[])
                states=[item["state"] for item in socket.text
                        if item["type"]=="state.changed"]
                self.assertIn(TurnState.AGENT_SPEAKING.value,states)
                self.assertIsNone(session.last_confirmed_language)

    def test_emergency_preserves_pending_product_sources_and_report_fields(self):
        session=self.emergency_session()
        session.last_confirmed_language="ko"
        product_group=[{"role":"user","content":"Product ABC-7"},
                       {"role":"assistant","content":"Please provide its full label."}]
        session.history.commit(product_group,[{"document_id":"PRIVATE-REFERENCE"}])
        pending={"location":"Lab A","summary":"unchanged","urgency":"urgent",
                 "exposure_status":"unknown","language":"ko",
                 "material_or_equipment":"Product ABC-7"}
        session.history.pending_report=dict(pending)
        _,socket,_,brain,retrieval,execute,llm=self.run_emergency(
            Transcription("Emergency!",None),session)
        self.assertEqual(session.history.pending_report,pending)
        self.assertEqual(session.history.groups[0],product_group)
        self.assertEqual(session.history.source_references,[{"document_id":"PRIVATE-REFERENCE"}])
        self.assertEqual(session.last_confirmed_language,"ko")
        self.assertEqual(session.language_mode,"auto")
        visible=json.dumps(socket.text,ensure_ascii=False)
        self.assertNotIn("PRIVATE-REFERENCE",visible)
        self.assertNotIn("Product ABC-7",visible)
        self.assertNotIn("/trusted/catalog.sqlite",visible)
        self.assertNotIn("credential",visible.casefold())
        self.assertNotIn("database",visible.casefold())
        for boundary in (brain,retrieval,execute,llm): boundary.assert_not_called()

    def test_emergency_preserves_full_capacity_product_history(self):
        session=self.emergency_session()
        history=session.history
        product_group=[{"role":"user","content":"Product ABC-7 full label"},
                       {"role":"assistant","content":"Product ABC-7 identified."}]
        history.commit(product_group)
        for index in range(1,history.max_turns):
            history.commit([{"role":"user","content":f"context {index}"},
                            {"role":"assistant","content":f"answer {index}"}])
        before=[[dict(message) for message in group] for group in history.groups]
        self.assertEqual(len(before),history.max_turns)
        self.run_emergency(Transcription("Emergency!",None),session)
        self.assertEqual(history.groups,before)
        self.assertEqual(history.groups[0],product_group)

    def test_emergency_does_not_reset_manual_language_mode(self):
        session=self.emergency_session()
        session.language_mode="manual"; session.manual_language="ko"
        self.run_emergency(Transcription("Emergency!",None),session)
        self.assertEqual((session.language_mode,session.manual_language),("manual","ko"))
        self.assertEqual(session.tool_context.language,"ko")

    def test_emergency_sessions_are_isolated(self):
        korean=self.emergency_session()
        english=self.emergency_session()
        korean.history.pending_report={"language":"ko","location":"K"}
        english.history.pending_report={"language":"en","location":"E"}
        self.run_emergency(Transcription("도와줘!",None),korean)
        self.run_emergency(Transcription("Emergency!",None),english)
        self.assertEqual(korean.history.pending_report,{"language":"ko","location":"K"})
        self.assertEqual(english.history.pending_report,{"language":"en","location":"E"})
        self.assertEqual(korean.history.groups,[])
        self.assertEqual(english.history.groups,[])

    def test_emergency_tts_failure_keeps_fixed_text_response(self):
        with patch("safebridge_voice.server.resolve_turn_language") as resolver, \
             patch("safebridge_voice.server.log.exception"):
            session,socket,_,brain,retrieval,execute,llm=self.run_emergency(
                Transcription("Emergency!",None),
                tts_result=RuntimeError("synthetic TTS failure"))
        resolver.assert_not_called()
        for boundary in (brain,retrieval,execute,llm): boundary.assert_not_called()
        self.assertEqual([item["text"] for item in socket.text
                          if item["type"] in ("reply.delta","reply.complete")],
                         [ENGLISH_EMERGENCY_RESPONSE,ENGLISH_EMERGENCY_RESPONSE])
        self.assertEqual(socket.binary,[])
        audio_complete=next(item for item in socket.text if item["type"]=="audio.complete")
        self.assertEqual(audio_complete["segment_count"],0)
        done=next(item for item in socket.text if item["type"]=="turn.done")
        self.assertEqual(done["route"],"deterministic_emergency")
        self.assertNotIn("first_audio_ms",done["timings_ms"])
        self.assertEqual(done["segment_count"],0)
        self.assertEqual(done["output_frames"],0)
        self.assertEqual(done["tools_used"],[])
        self.assertEqual(session.state,TurnState.COOLDOWN)

    def test_language_clarification_tts_failure_has_no_first_audio_timing(self):
        with patch("safebridge_voice.server.log.exception"):
            session,socket,_,brain,retrieval,execute,llm=self.run_emergency(
                Transcription("Please show the approved procedure.",None),
                tts_result=RuntimeError("synthetic TTS failure"))
        for boundary in (brain,retrieval,execute,llm): boundary.assert_not_called()
        done=next(item for item in socket.text if item["type"]=="turn.done")
        self.assertEqual(done["route"],"language_clarification")
        self.assertNotIn("first_audio_ms",done["timings_ms"])
        self.assertEqual((done["segment_count"],done["output_frames"]),(0,0))
        self.assertEqual(socket.binary,[])
        self.assertEqual(session.state,TurnState.COOLDOWN)

    def test_pending_report_approval_precedes_language_resolution(self):
        class Socket:
            def __init__(self): self.text=[]; self.binary=[]
            async def send_text(self,value): self.text.append(json.loads(value))
            async def send_bytes(self,value): self.binary.append(value)

        pending={"location":"Lab A","summary":"spill","urgency":"urgent",
                 "exposure_status":"unknown","language":"ko"}
        cases=(
            Transcription("네, 지금 제출해 주세요.",None),
            Transcription("지금 작성한 보고 초안 제출해 주세요.","en"),
        )
        for transcription in cases:
            with self.subTest(transcription=transcription):
                session=ListenerSession(tool_context=ToolContext(
                    Path("/trusted/catalog.sqlite"),"FACILITY-A","en","operational"))
                session.active=True; session.language_mode="auto"
                session.active_turn_id=1; session.detector.state=TurnState.PROCESSING
                session.last_confirmed_language="en"
                session.history.pending_report=dict(pending)
                socket=Socket()
                with patch("safebridge_voice.server.transcribe",
                           return_value=transcription), \
                     patch("safebridge_voice.server.resolve_turn_language") as resolver, \
                     patch("safebridge_voice.server.synthesize",return_value=b"\0\0") as tts, \
                     patch("safebridge_voice.brain.execute_tool",return_value={
                         "status":"success",
                         "report_id":"SR-20260724-A1B2C3",
                         "report_status":"queued_for_handoff",
                     }) as execute, \
                     patch("safebridge_voice.server.AsyncOpenAI"), \
                     patch.dict("os.environ",{
                         "XAI_API_KEY":"test",
                         "CHAT_MODEL":"test",
                     },clear=False):
                    asyncio.run(run_turn(socket,session,b"\0\0",1,1))
                resolver.assert_not_called()
                execute.assert_called_once()
                name,arguments=execute.call_args.args
                context=execute.call_args.kwargs["context"]
                self.assertEqual(name,"create_safety_report")
                self.assertEqual(arguments,pending)
                self.assertEqual(
                    (str(context.catalog_path),context.facility_id,
                     context.language,context.usage_scope),
                    ("/trusted/catalog.sqlite","FACILITY-A","ko","operational"),
                )
                self.assertIsNone(session.history.pending_report)
                self.assertEqual(session.last_confirmed_language,"ko")
                self.assertEqual(tts.call_args.args[1],"ko")
                event_types=[item["type"] for item in socket.text]
                self.assertNotIn("session.language_confirmation_required",event_types)
                resolved=next(item for item in socket.text
                              if item["type"]=="session.turn_language_resolved")
                self.assertEqual(resolved["language"],"ko")
                statuses=[item.get("status") for item in socket.text
                          if item["type"] in ("tool.call","tool.result")]
                self.assertEqual(statuses,["submitting","confirmed"])
                done=next(item for item in socket.text if item["type"]=="turn.done")
                self.assertEqual(done["route"],"brain")
                self.assertEqual(done["tools_used"],["create_safety_report"])

    def test_pending_report_cancellation_precedes_language_resolution(self):
        class Socket:
            def __init__(self): self.text=[]; self.binary=[]
            async def send_text(self,value): self.text.append(json.loads(value))
            async def send_bytes(self,value): self.binary.append(value)

        pending={"location":"Lab A","summary":"spill","urgency":"routine",
                 "exposure_status":"no","language":"ko"}
        session=ListenerSession(tool_context=ToolContext(
            Path("/trusted/catalog.sqlite"),None,"en","operational"))
        session.active=True; session.language_mode="auto"
        session.active_turn_id=1; session.detector.state=TurnState.PROCESSING
        session.history.pending_report=dict(pending)
        socket=Socket()
        with patch("safebridge_voice.server.transcribe",
                   return_value=Transcription("보고서를 취소해 주세요.","en")), \
             patch("safebridge_voice.server.resolve_turn_language") as resolver, \
             patch("safebridge_voice.server.synthesize",return_value=b"\0\0") as tts, \
             patch("safebridge_voice.brain.execute_tool") as execute, \
             patch("safebridge_voice.server.AsyncOpenAI"), \
             patch.dict("os.environ",{
                 "XAI_API_KEY":"test",
                 "CHAT_MODEL":"test",
             },clear=False):
            asyncio.run(run_turn(socket,session,b"\0\0",1,1))
        resolver.assert_not_called()
        execute.assert_not_called()
        self.assertIsNone(session.history.pending_report)
        self.assertEqual(tts.call_args.args[1],"ko")
        result=next(item for item in socket.text if item["type"]=="tool.result")
        self.assertEqual(result["status"],"cancelled")
        done=next(item for item in socket.text if item["type"]=="turn.done")
        self.assertEqual(done["route"],"brain")
        self.assertEqual(done["tools_used"],[])

    def test_pending_report_correction_still_uses_language_resolution_and_brain(self):
        class Socket:
            def __init__(self): self.text=[]; self.binary=[]
            async def send_text(self,value): self.text.append(json.loads(value))
            async def send_bytes(self,value): self.binary.append(value)

        pending={"location":"Lab A","summary":"spill","urgency":"urgent",
                 "exposure_status":"unknown","language":"ko",
                 "material_or_equipment":"acetone"}
        session=ListenerSession(tool_context=ToolContext(
            Path("/trusted/catalog.sqlite"),None,"ko","operational"))
        session.active=True; session.language_mode="auto"
        session.active_turn_id=1; session.detector.state=TurnState.PROCESSING
        session.history.pending_report=dict(pending)
        socket=Socket()

        async def fake_brain(client,history,transcript,sentence,mark_token,tool_event,
                             tool_context):
            self.assertEqual(tool_context.language,"ko")
            await sentence(SentenceSegment(0,"수정 내용을 다시 확인하겠습니다."))
            return BrainResult(
                [{"role":"user","content":transcript},
                 {"role":"assistant","content":"수정 내용을 다시 확인하겠습니다."}],
                "수정 내용을 다시 확인하겠습니다.",None,[],
            )

        with patch("safebridge_voice.server.transcribe",return_value=Transcription(
                 "네, 하지만 아세톤이 아니라 메탄올이에요.","ko")), \
             patch("safebridge_voice.server.synthesize",return_value=b"\0\0"), \
             patch("safebridge_voice.server.stream_brain_turn",
                   side_effect=fake_brain) as brain, \
             patch("safebridge_voice.server.AsyncOpenAI"), \
             patch.dict("os.environ",{
                 "XAI_API_KEY":"test",
                 "CHAT_MODEL":"test",
             },clear=False):
            asyncio.run(run_turn(socket,session,b"\0\0",1,1))
        brain.assert_called_once()
        self.assertEqual(session.history.pending_report,pending)
        self.assertIn("session.turn_language_resolved",
                      [item["type"] for item in socket.text])

    def test_brain_turn_done_uses_server_authored_route(self):
        session=self.emergency_session()
        class Socket:
            def __init__(self): self.text=[]; self.binary=[]
            async def send_text(self,value): self.text.append(json.loads(value))
            async def send_bytes(self,value): self.binary.append(value)
        socket=Socket()
        async def fake_brain(client,history,transcript,sentence,mark_token,tool_event,
                             tool_context):
            mark_token()
            await sentence(SentenceSegment(0,"Approved answer."))
            return BrainResult(
                [{"role":"user","content":transcript},
                 {"role":"assistant","content":"Approved answer."}],
                "Approved answer.",None,[],
            )
        with patch("safebridge_voice.server.transcribe",
                   return_value=Transcription("Approved information please.","en")), \
             patch("safebridge_voice.server.synthesize",return_value=b"\0\0"), \
             patch("safebridge_voice.server.stream_brain_turn",side_effect=fake_brain), \
             patch("safebridge_voice.server.AsyncOpenAI"):
            asyncio.run(run_turn(socket,session,b"\0\0",1,1))
        done=next(item for item in socket.text if item["type"]=="turn.done")
        self.assertEqual(done["route"],"brain")
        self.assertIn("first_audio_ms",done["timings_ms"])

    def test_server_owned_tool_context_and_language_normalization(self):
        self.assertEqual(normalize_session_language("ko-KR"), "ko")
        self.assertEqual(normalize_session_language("vi_VN"), "vi")
        values={"SAFEBRIDGE_SAFETY_CATALOG":"/trusted/catalog.sqlite",
                "SAFEBRIDGE_FACILITY_ID":"FACILITY-A","SAFEBRIDGE_SESSION_LANGUAGE":"vi-VN",
                "SAFEBRIDGE_USAGE_SCOPE":"operational"}
        with patch.dict("os.environ", values, clear=True):
            context=server_tool_context()
        self.assertEqual((str(context.catalog_path),context.facility_id,context.language,context.usage_scope),
                         ("/trusted/catalog.sqlite","FACILITY-A","vi","operational"))
        self.assertEqual(context.report_language,"ko")

    def test_rest_stt_preserves_text_and_provider_language_only(self):
        with patch("safebridge_voice.server.requests.post",return_value=FakeResponse()) as post, \
             patch.dict("os.environ",{"XAI_API_KEY":"test"},clear=True):
            result=transcribe(b"\0\0")
        self.assertEqual((result.text,result.detected_language),
                         ("short test utterance","ko"))
        self.assertFalse(hasattr(result,"confidence"))
        self.assertTrue(post.call_args.args[0].endswith("/stt"))

    def test_sessions_have_independent_korean_and_english_contexts(self):
        config=ServerConfig(Path("/trusted/catalog.sqlite"),"F","operational",
                            frozenset({"ko","en","vi"}),"ko")
        korean=ListenerSession(tool_context=server_tool_context(config,"ko"))
        english=ListenerSession(tool_context=server_tool_context(config,"en"))
        self.assertEqual(korean.tool_context.language,"ko")
        self.assertEqual(english.tool_context.language,"en")
        english.set_tool_context(server_tool_context(config,"vi"))
        self.assertEqual(korean.tool_context.language,"ko")
        self.assertEqual(english.tool_context.language,"vi")

    def test_invalid_language_is_rejected_without_changing_context(self):
        config=ServerConfig(Path("/trusted/catalog.sqlite"),None,"operational",
                            frozenset({"ko","en"}),"ko")
        session=ListenerSession(tool_context=server_tool_context(config,"ko"))
        with self.assertRaises(ValueError):
            server_tool_context(config,"vi")
        self.assertEqual(session.tool_context.language,"ko")

    def test_language_change_clears_sensitive_session_state(self):
        session=ListenerSession(tool_context=ToolContext(Path("/trusted/catalog.sqlite"),None,"ko","operational"))
        session.active=True
        session.history.commit([{"role":"user","content":"old"}],[{"document_id":"OLD"}])
        session.history.pending_report={"language":"ko","location":"F"}
        session.set_tool_context(ToolContext(Path("/trusted/catalog.sqlite"),None,"en","operational"))
        self.assertEqual(session.tool_context.language,"en")
        self.assertEqual(len(session.history.messages()),1)
        self.assertEqual(session.history.source_references,[])
        self.assertIsNone(session.history.pending_report)

    def test_auto_switch_preserves_state_and_reset_clears_it(self):
        session=ListenerSession(tool_context=ToolContext(Path("/trusted/catalog.sqlite"),None,"ko","operational"))
        session.active=True
        session.history.commit([{"role":"user","content":"safe history"}],[{"document_id":"OLD"}])
        session.history.pending_report={"language":"ko","location":"F"}
        session.set_language_mode("auto")
        session.last_confirmed_language="en"
        self.assertEqual(len(session.history.messages()),2)
        self.assertIsNotNone(session.history.pending_report)
        session.reset_sensitive_state()
        self.assertEqual(len(session.history.messages()),1)
        self.assertEqual(session.history.source_references,[])
        self.assertIsNone(session.history.pending_report)
        self.assertIsNone(session.last_confirmed_language)

    def test_unresolved_server_turn_bypasses_brain_retrieval_and_report_mutation(self):
        class Socket:
            def __init__(self): self.text=[]; self.binary=[]
            async def send_text(self,value): self.text.append(json.loads(value))
            async def send_bytes(self,value): self.binary.append(value)
        cases=(
            Transcription("Please show the approved procedure.",None),
            Transcription("Please explain the emergency spill procedure.",None),
            Transcription("Please show the approved procedure.","ja"),
            Transcription("누출됐어요. How should I clean this spill?","en"),
            Transcription("Please show the approved procedure.","ko"),
            Transcription("Acetone","en"),
            Transcription("They",None),
            Transcription("Day",None),
            Transcription("ねえ","ja"),
            Transcription("내",None),
        )
        for transcription in cases:
            with self.subTest(transcription=transcription):
                session=ListenerSession(tool_context=ToolContext(
                    Path("/trusted/catalog.sqlite"),None,"ko","operational"))
                session.active=True; session.language_mode="auto"
                session.active_turn_id=1; session.detector.state=TurnState.PROCESSING
                pending={"location":"F","summary":"unchanged","urgency":"routine",
                         "exposure_status":"unknown","language":"ko"}
                references=[{"document_id":"UNCHANGED"}]
                session.history.pending_report=dict(pending)
                session.history.source_references=list(references)
                socket=Socket()
                with patch("safebridge_voice.server.transcribe",return_value=transcription), \
                     patch("safebridge_voice.server.synthesize",return_value=b"\0\0") as tts, \
                     patch("safebridge_voice.server.stream_brain_turn") as brain, \
                     patch("safebridge_voice.tools.search_approved_safety_manual") as retrieval, \
                     patch("safebridge_voice.server.AsyncOpenAI") as llm:
                    asyncio.run(run_turn(socket,session,b"\0\0",1,1))
                brain.assert_not_called(); retrieval.assert_not_called(); llm.assert_not_called()
                self.assertEqual(session.history.pending_report,pending)
                self.assertEqual(session.history.source_references,references)
                self.assertEqual(
                    tts.call_args.args[0],
                    REPORT_CONFIRMATION_CLARIFICATION_TEXT["ko"],
                )
                event_types=[item["type"] for item in socket.text]
                self.assertIn("session.language_confirmation_required",event_types)
                done=next(item for item in socket.text if item["type"]=="turn.done")
                self.assertEqual(done["route"],"language_clarification")
                self.assertIn("first_audio_ms",done["timings_ms"])
                self.assertEqual(
                    [item["text"] for item in socket.text if item["type"]=="reply.delta"],
                    [REPORT_CONFIRMATION_CLARIFICATION_TEXT["ko"]],
                )
                self.assertNotIn("tool.call",event_types)
                self.assertNotIn("tool.result",event_types)

    def test_missing_catalog_configuration_fails_closed(self):
        with patch.dict("os.environ", {}, clear=True), self.assertRaises(RuntimeError):
            server_tool_context()
    def test_pcm_validation_and_framing(self):
        self.assertEqual(validate_tts_pcm(FakeResponse(b"\\0\\0")),b"\\0\\0")
        self.assertEqual([len(x) for x in frame_complete_audio(b"x"*(FRAME_BYTES+2))],[FRAME_BYTES,FRAME_BYTES])
    def test_week3_boundaries_and_ack(self):
        now=[1.0]; session=ListenerSession(EndpointDetector(classifier=Decisions(TURN)),clock=lambda:now[0]); session.start()
        events=session.accept_chunk(b"".join(frame(i) for i in range(len(TURN))))
        end=next(x for x in events if x.kind=="speech.end")
        self.assertEqual(session.state,TurnState.PROCESSING)
        self.assertTrue(session.start_playback(end.turn_id))
        self.assertFalse(session.playback_ended(end.turn_id+1))
        self.assertTrue(session.playback_ended(end.turn_id))
        self.assertFalse(session.playback_ended(end.turn_id))
        self.assertEqual(session.state,TurnState.COOLDOWN)
    def test_tts_rejects_errors_json_empty_and_odd_pcm(self):
        for response in (FakeResponse(b"bad",400), FakeResponse(b"{}",content_type="application/json"), FakeResponse(), FakeResponse(b"x")):
            with self.assertRaises(RuntimeError): validate_tts_pcm(response)

    def test_frames_during_processing_and_agent_are_ignored(self):
        session=ListenerSession(EndpointDetector(classifier=Decisions(TURN))); session.start(); end=next(x for x in session.accept_chunk(b"".join(frame(i) for i in range(len(TURN)))) if x.kind=="speech.end"); self.assertEqual(session.accept_chunk(frame(9)*4), []); session.start_playback(end.turn_id); self.assertEqual(session.accept_chunk(frame(9)*4), [])

    def test_stop_clears_history(self):
        session=ListenerSession(); session.history.pending_report={"location":"before start"}; session.start(); self.assertIsNone(session.history.pending_report); session.active_turn_id=7; generation=session.generation; self.assertTrue(session.is_current(7,generation)); session.history.commit([{"role":"user","content":"x"}]); session.history.pending_report={"location":"before stop"}
        session.stop(); self.assertEqual(len(session.history.messages()),1); self.assertIsNone(session.history.pending_report); self.assertFalse(session.is_current(7,generation))
        session.history.pending_report={"location":"restart"}; session.start(); self.assertEqual(len(session.history.messages()),1); self.assertIsNone(session.history.pending_report); self.assertTrue(session.active); session.history.pending_report={"location":"second stop"}; session.stop(); self.assertIsNone(session.history.pending_report)
        generation=session.generation
        session.stop(); self.assertEqual(len(session.history.messages()),1)
        self.assertGreater(session.generation,generation)
        session.stop(); self.assertEqual(len(session.history.messages()),1)

    def test_disconnect_message_exits_without_second_receive_or_error_log(self):
        class Socket:
            def __init__(self): self.receives=0; self.sent=[]
            async def accept(self): pass
            async def send_text(self,value): self.sent.append(value)
            async def receive(self):
                self.receives+=1
                if self.receives>1: raise AssertionError("receive called after disconnect")
                return {"type":"websocket.disconnect","code":1000}
        socket=Socket()
        with patch("safebridge_voice.server.log.exception") as logged:
            asyncio.run(voice_socket(socket))
        self.assertEqual(socket.receives,1); logged.assert_not_called()

    def test_report_status_control_returns_worker_progress(self):
        class Socket:
            def __init__(self):
                self.sent=[]
                self.messages=iter((
                    {"text":json.dumps({
                        "type":"report.status.get",
                        "report_id":"SR-20260722-A1B2C3",
                    })},
                    {"type":"websocket.disconnect","code":1000},
                ))
            async def accept(self): pass
            async def send_text(self,value): self.sent.append(json.loads(value))
            async def receive(self): return next(self.messages)
        socket=Socket()
        with patch(
            "safebridge_voice.server.check_safety_report_status",
            return_value={
                "status":"success","report_id":"SR-20260722-A1B2C3",
                "report_status":"handoff_ready","attempts":1,
                "workflow":{"procedure_id":"fictional-demo","step_id":"observe"},
            },
        ):
            asyncio.run(voice_socket(socket))
        status=next(item for item in socket.sent if item["type"]=="report.status")
        self.assertEqual(status["report_status"],"handoff_ready")
        self.assertEqual(status["attempts"],1)
        self.assertEqual(status["workflow"]["step_id"],"observe")

if __name__=="__main__": unittest.main()
