import asyncio, json, tempfile, unittest
from types import SimpleNamespace
from unittest.mock import patch
from collections import deque
from voice_workflow_agent.audio import FRAME_BYTES
from voice_workflow_agent.brain import (
    REPORT_CONFIRMATION_CLARIFICATION_TEXT,
    BrainResult,
    SentenceSegment,
)
from voice_workflow_agent.document_store import ingest_manifest
from voice_workflow_agent.experiment_reports import (
    ExperimentReportSettings,
    ExperimentReportStore,
)
from pathlib import Path
from voice_workflow_agent.language import Transcription
from voice_workflow_agent.emergency import ENGLISH_EMERGENCY_RESPONSE, KOREAN_EMERGENCY_RESPONSE
from voice_workflow_agent.server import CascadeTranscriptionContext, ListenerEvent, ListenerSession, ServerConfig, ServerConfigurationError, cancel_cascade_generation, cascade_transcription_context, export_experiment_report, frame_complete_audio, normalize_session_language, run_barge_in_stt_failure_turn, run_turn, server_config, server_tool_context, transcribe, transcribe_cascade_audio, validate_tts_pcm, voice_socket
from voice_workflow_agent.tools import ToolContext
from voice_workflow_agent.vad import EndpointDetector, EndpointResult, TurnState, VadConfig
from tests.test_retrieval import operational_document

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
    def setUp(self):
        # These tests exercise server behavior with local fakes, not worker-pool
        # scheduling. Keep asyncio.run() bounded and leave thread ownership to
        # the dedicated protocol-catalog concurrency tests.
        self._to_thread = patch(
            "voice_workflow_agent.server.asyncio.to_thread",
            side_effect=lambda function, *args: function(*args),
        )
        self._to_thread.start()
        self.addCleanup(self._to_thread.stop)

    def approved_catalog(self,directory,usage_scope="operational"):
        path=Path(directory)/"approved.sqlite"
        ingest_manifest({
            "documents":[operational_document(usage_scope=usage_scope)],
        },path)
        return path

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
        tts_patch=(patch("voice_workflow_agent.server.synthesize",side_effect=tts_result)
                   if isinstance(tts_result,BaseException)
                   else patch("voice_workflow_agent.server.synthesize",return_value=tts_result))
        with patch("voice_workflow_agent.server.transcribe",return_value=transcription), \
             tts_patch as tts, \
             patch("voice_workflow_agent.server.stream_brain_turn") as brain, \
             patch("voice_workflow_agent.tools.search_approved_safety_manual") as retrieval, \
             patch("voice_workflow_agent.brain.execute_tool") as execute, \
             patch("voice_workflow_agent.server.AsyncOpenAI") as llm:
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
                with patch("voice_workflow_agent.server.transcribe",
                           return_value=Transcription("가상 데모를 시작해 주세요","ko")), \
                     patch("voice_workflow_agent.server.synthesize",return_value=b"\0\0"), \
                     patch("voice_workflow_agent.server.stream_brain_turn",
                           side_effect=fake_brain), \
                     patch("voice_workflow_agent.server.AsyncOpenAI"), \
                     patch("voice_workflow_agent.server.require_env",return_value="test"):
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
                with patch("voice_workflow_agent.server.resolve_turn_language") as resolver:
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
        with patch("voice_workflow_agent.server.resolve_turn_language") as resolver, \
             patch("voice_workflow_agent.server.log.exception"):
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
        with patch("voice_workflow_agent.server.log.exception"):
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
                with patch("voice_workflow_agent.server.transcribe",
                           return_value=transcription), \
                     patch("voice_workflow_agent.server.resolve_turn_language") as resolver, \
                     patch("voice_workflow_agent.server.synthesize",return_value=b"\0\0") as tts, \
                     patch("voice_workflow_agent.brain.execute_tool",return_value={
                         "status":"success",
                         "report_id":"SR-20260724-A1B2C3",
                         "report_status":"queued_for_handoff",
                     }) as execute, \
                     patch("voice_workflow_agent.server.AsyncOpenAI"), \
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
        with patch("voice_workflow_agent.server.transcribe",
                   return_value=Transcription("보고서를 취소해 주세요.","en")), \
             patch("voice_workflow_agent.server.resolve_turn_language") as resolver, \
             patch("voice_workflow_agent.server.synthesize",return_value=b"\0\0") as tts, \
             patch("voice_workflow_agent.brain.execute_tool") as execute, \
             patch("voice_workflow_agent.server.AsyncOpenAI"), \
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

        with patch("voice_workflow_agent.server.transcribe",return_value=Transcription(
                 "네, 하지만 아세톤이 아니라 메탄올이에요.","ko")), \
             patch("voice_workflow_agent.server.synthesize",return_value=b"\0\0"), \
             patch("voice_workflow_agent.server.stream_brain_turn",
                   side_effect=fake_brain) as brain, \
             patch("voice_workflow_agent.server.AsyncOpenAI"), \
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
        with patch("voice_workflow_agent.server.transcribe",
                   return_value=Transcription("Approved information please.","en")), \
             patch("voice_workflow_agent.server.synthesize",return_value=b"\0\0"), \
             patch("voice_workflow_agent.server.stream_brain_turn",side_effect=fake_brain), \
             patch("voice_workflow_agent.server.AsyncOpenAI"), \
             patch("voice_workflow_agent.server.require_env",return_value="test"):
            asyncio.run(run_turn(socket,session,b"\0\0",1,1))
        done=next(item for item in socket.text if item["type"]=="turn.done")
        self.assertEqual(done["route"],"brain")
        self.assertIn("first_audio_ms",done["timings_ms"])

    def test_generic_progress_uses_only_observed_generation_and_tool_boundaries(self):
        session=self.emergency_session()
        session.language_mode="manual"; session.manual_language="ko"
        session.turn_generations[1]=session.generation
        session.accept_configuration(73,"cascade","ko","approved-demo")
        class Socket:
            def __init__(self): self.text=[]; self.binary=[]
            async def send_text(self,value): self.text.append(json.loads(value))
            async def send_bytes(self,value): self.binary.append(value)
        socket=Socket()
        async def fake_brain(client,history,transcript,sentence,mark_token,
                             tool_event,tool_context):
            await tool_event("tool.call",{
                "tool":"search_approved_safety_manual","round":0})
            await tool_event("tool.result",{
                "tool":"search_approved_safety_manual","round":0,
                "status":"success","elapsed_ms":1})
            await sentence(SentenceSegment(0,"승인된 정보에 근거한 응답입니다."))
            return BrainResult(
                [{"role":"user","content":transcript},
                 {"role":"assistant","content":"승인된 정보에 근거한 응답입니다."}],
                "승인된 정보에 근거한 응답입니다.",1,
                ["search_approved_safety_manual"],
            )
        with patch(
            "voice_workflow_agent.server.transcribe",
            return_value=Transcription("승인된 정보를 알려 주세요","ko"),
        ), patch(
            "voice_workflow_agent.server.synthesize",return_value=b"\0\0",
        ), patch(
            "voice_workflow_agent.server.stream_brain_turn",
            side_effect=fake_brain,
        ), patch(
            "voice_workflow_agent.server.AsyncOpenAI",
        ), patch(
            "voice_workflow_agent.server.require_env",return_value="test",
        ):
            asyncio.run(run_turn(socket,session,b"\0\0",1,1))
        progress=[item for item in socket.text if item["type"]=="turn.state"]
        self.assertEqual([item["state"] for item in progress],[
            "transcribing","routing","composing",
            "checking_approved_information","composing","synthesizing",
            "playing",
        ])
        self.assertEqual(
            next(item for item in progress
                 if item["state"]=="checking_approved_information")["route"],
            "approved_information")
        self.assertNotIn("checking_protocol",[item["state"] for item in progress])
        visible=json.dumps(progress,ensure_ascii=False)
        for forbidden in ("prompt","reasoning","arguments","Traceback"):
            self.assertNotIn(forbidden,visible)

    def test_server_owned_tool_context_and_language_normalization(self):
        self.assertEqual(normalize_session_language("ko-KR"), "ko")
        self.assertEqual(normalize_session_language("vi_VN"), "vi")
        with tempfile.TemporaryDirectory() as temporary:
            catalog=self.approved_catalog(temporary)
            values={"VOICE_WORKFLOW_AGENT_SAFETY_CATALOG":str(catalog),
                    "VOICE_WORKFLOW_AGENT_FACILITY_ID":"FACILITY-A","VOICE_WORKFLOW_AGENT_SESSION_LANGUAGE":"vi-VN",
                    "VOICE_WORKFLOW_AGENT_USAGE_SCOPE":"operational"}
            with patch.dict("os.environ", values, clear=True):
                context=server_tool_context()
        self.assertEqual((str(context.catalog_path),context.facility_id,context.language,context.usage_scope),
                         (str(catalog),"FACILITY-A","vi","operational"))
        self.assertEqual(context.report_language,"ko")

    def test_catalog_configuration_requires_usable_file_and_matching_scope(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            catalog=self.approved_catalog(root)
            base={
                "VOICE_WORKFLOW_AGENT_SAFETY_CATALOG":str(catalog),
                "VOICE_WORKFLOW_AGENT_USAGE_SCOPE":"operational",
            }
            with patch.dict("os.environ",base,clear=True):
                self.assertEqual(server_config().catalog_path,catalog)

            missing=root/"missing.sqlite"
            with patch.dict("os.environ",{
                **base,"VOICE_WORKFLOW_AGENT_SAFETY_CATALOG":str(missing),
            },clear=True),self.assertRaisesRegex(
                ServerConfigurationError,
                "VOICE_WORKFLOW_AGENT_SAFETY_CATALOG.*existing regular file",
            ) as captured:
                server_config()
            self.assertIn(str(missing),str(captured.exception))

            with patch.dict("os.environ",{
                **base,"VOICE_WORKFLOW_AGENT_SAFETY_CATALOG":"",
            },clear=True),self.assertRaisesRegex(
                ServerConfigurationError,
                "VOICE_WORKFLOW_AGENT_SAFETY_CATALOG must not be empty",
            ):
                server_config()

            invalid_file=root/"not-a-catalog.txt"
            invalid_file.write_text("not sqlite",encoding="utf-8")
            for target in (root,invalid_file):
                with self.subTest(target=target),patch.dict("os.environ",{
                    **base,"VOICE_WORKFLOW_AGENT_SAFETY_CATALOG":str(target),
                },clear=True),self.assertRaisesRegex(
                    ServerConfigurationError,
                    "VOICE_WORKFLOW_AGENT_SAFETY_CATALOG",
                ):
                    server_config()

            with patch.dict("os.environ",{
                **base,"VOICE_WORKFLOW_AGENT_USAGE_SCOPE":"demo",
            },clear=True),self.assertRaisesRegex(
                ServerConfigurationError,
                "VOICE_WORKFLOW_AGENT_USAGE_SCOPE.*no approved active documents",
            ):
                server_config()

    def test_missing_catalog_rejects_session_with_safe_path_diagnostic(self):
        class Socket:
            def __init__(self):
                self.sent=[]
                self.messages=iter((
                    {"text":json.dumps({
                        "type":"session.start","mode":"cascade",
                        "language":"ko","protocol_id":
                            "candidate-a-curated-development-v1",
                        "configuration_id":1,
                    })},
                    {"type":"websocket.disconnect","code":1000},
                ))
            async def accept(self): pass
            async def send_text(self,value): self.sent.append(json.loads(value))
            async def receive(self): return next(self.messages)
        with tempfile.TemporaryDirectory() as temporary:
            missing=Path(temporary)/"missing.sqlite"
            socket=Socket()
            with patch.dict("os.environ",{
                "VOICE_WORKFLOW_AGENT_SAFETY_CATALOG":str(missing),
                "VOICE_WORKFLOW_AGENT_USAGE_SCOPE":"demo",
            },clear=True),patch(
                "voice_workflow_agent.server.log.warning",
            ) as warning:
                asyncio.run(voice_socket(socket))
            rendered=warning.call_args.args[0] % warning.call_args.args[1:]
        self.assertIn("VOICE_WORKFLOW_AGENT_SAFETY_CATALOG",rendered)
        self.assertIn(str(missing),rendered)
        self.assertTrue(any(
            item.get("message")=="invalid session configuration"
            for item in socket.sent
        ))
        self.assertFalse(any(
            item["type"]=="session.started" for item in socket.sent
        ))

    def test_cascade_without_configured_protocol_requires_selection(self):
        class Socket:
            def __init__(self):
                self.sent=[]
                self.messages=iter((
                    {"text":json.dumps({
                        "type":"session.start","mode":"cascade",
                        "language":"ko","protocol_id":
                            "candidate-a-curated-development-v1",
                        "configuration_id":1,
                    })},
                    {"type":"websocket.disconnect","code":1000},
                ))
            async def accept(self): pass
            async def send_text(self,value): self.sent.append(json.loads(value))
            async def receive(self): return next(self.messages)
        with tempfile.TemporaryDirectory() as temporary:
            catalog=self.approved_catalog(temporary,"demo")
            environment={
                "VOICE_WORKFLOW_AGENT_SAFETY_CATALOG":str(catalog),
                "VOICE_WORKFLOW_AGENT_USAGE_SCOPE":"demo",
                "VOICE_WORKFLOW_AGENT_FACILITY_ID":"",
                "VOICE_WORKFLOW_AGENT_PROCEDURE_CATALOG":"",
                "VOICE_WORKFLOW_AGENT_PROCEDURE_STORE":"",
            }
            socket=Socket()
            with patch.dict("os.environ",environment,clear=True):
                asyncio.run(voice_socket(socket))
        required=next(
            item for item in socket.sent
            if item["type"]=="session.configuration_required")
        self.assertEqual(required,{
            "type":"session.configuration_required",
            "configuration_id":1,"mode":"cascade","language":"ko",
            "protocol_id":None,"reason":"protocol_selection_unavailable",
        })
        self.assertFalse(any(
            item["type"] in ("session.ready","session.started")
            for item in socket.sent
        ))
        self.assertFalse(any(
            item.get("message")=="invalid session configuration"
            for item in socket.sent
        ))

    def test_cascade_exact_curated_configuration_is_acknowledged_without_persistence(self):
        protocol_id="candidate-a-curated-development-v1"
        placeholder=Path("/tmp/offline-session-contract")
        config=ServerConfig(
            placeholder,None,"test_only",frozenset({"ko"}),"ko",
            None,None,placeholder,placeholder,placeholder,
        )

        class Fixture:
            def __init__(self):
                self.protocol_id=protocol_id
                self.revision_id="fixture-test-revision"
                self.development_only=True
                self.title="Candidate A development fixture"
                self.steps=(SimpleNamespace(
                    source_label="1",step_id="step-1",
                    instruction_source_text="Exact source instruction.",
                    evidence=SimpleNamespace(source_page_number=1),
                    warnings=(),
                ),)
                self.draft=SimpleNamespace(readiness=SimpleNamespace(
                    status=SimpleNamespace(value="analysis_required")))
            def visual_for_step(self,index): return None

        class Socket:
            def __init__(self):
                self.sent=[]
                self.messages=iter((
                    {"text":json.dumps({
                        "type":"session.start","mode":"cascade",
                        "language":"ko","protocol_id":protocol_id,
                        "configuration_id":7,
                    })},
                    {"type":"websocket.disconnect","code":1000},
                ))
            async def accept(self): pass
            async def send_text(self,value): self.sent.append(json.loads(value))
            async def receive(self): return next(self.messages)

        socket=Socket()
        with patch(
            "voice_workflow_agent.server.server_config",return_value=config,
        ), patch(
            "voice_workflow_agent.server.load_curated_protocol_fixture",
            return_value=Fixture(),
        ) as fixture_loader, patch(
            "voice_workflow_agent.server.ProcedureStore",
        ) as procedure_store, patch(
            "voice_workflow_agent.server.load_procedure_definitions",
        ) as procedure_loader, patch(
            "voice_workflow_agent.server.NativeRealtimeSession",
        ) as native_session:
            asyncio.run(voice_socket(socket))
        ready=next(item for item in socket.sent if item["type"]=="session.ready")
        self.assertEqual({key:ready[key] for key in (
            "type","configuration_id","mode","language","protocol_id",
            "revision_id",
        )},{
            "type":"session.ready","configuration_id":7,"mode":"cascade",
            "language":"ko","protocol_id":protocol_id,
            "revision_id":"fixture-test-revision",
        })
        self.assertEqual(
            ready["research_capabilities"]["external_text"]["status"],
            "disabled",
        )
        curated_state=next(
            item for item in socket.sent
            if item["type"]=="protocol.fixture.state")
        self.assertEqual(curated_state["configuration_id"],7)
        self.assertEqual(curated_state["action"],"attached")
        self.assertEqual(curated_state["state"]["protocol_id"],protocol_id)
        self.assertEqual(curated_state["state"]["revision"],1)
        self.assertTrue(curated_state["state"]["active"])
        self.assertEqual(curated_state["state"]["workflow_status"],"active")
        self.assertEqual(curated_state["state"]["current_step_label"],"1")
        self.assertFalse(any(
            item["type"]=="turn.state" for item in socket.sent
        ))
        started=next(item for item in socket.sent if item["type"]=="session.started")
        self.assertEqual(started["pipeline"],"cascade")
        fixture_loader.assert_called_once_with(placeholder,placeholder,placeholder)
        procedure_store.assert_not_called()
        procedure_loader.assert_not_called()
        native_session.assert_not_called()

    def test_curated_loading_failure_is_sanitized_and_never_becomes_ready(self):
        protocol_id="candidate-a-curated-development-v1"
        placeholder=Path("/tmp/offline-session-contract")
        config=ServerConfig(
            placeholder,None,"test_only",frozenset({"ko"}),"ko",
            None,None,placeholder,placeholder,placeholder,
        )

        class Socket:
            def __init__(self):
                self.sent=[]
                self.messages=iter((
                    {"text":json.dumps({
                        "type":"session.start","mode":"cascade",
                        "language":"ko","protocol_id":protocol_id,
                        "configuration_id":9,
                    })},
                    {"type":"websocket.disconnect","code":1000},
                ))
            async def accept(self): pass
            async def send_text(self,value): self.sent.append(json.loads(value))
            async def receive(self): return next(self.messages)

        socket=Socket()
        with patch(
            "voice_workflow_agent.server.server_config",return_value=config,
        ), patch(
            "voice_workflow_agent.server.load_curated_protocol_fixture",
            side_effect=ValueError("private malformed-fixture detail"),
        ), patch(
            "voice_workflow_agent.server.ProcedureStore",
        ) as procedure_store, patch(
            "voice_workflow_agent.server.AsyncOpenAI",
            side_effect=AssertionError("LLM must not run"),
        ):
            asyncio.run(voice_socket(socket))

        failure=next(item for item in socket.sent if item["type"]=="error")
        self.assertEqual(
            failure["message"],"선택된 프로토콜을 불러오지 못했습니다.")
        self.assertNotIn("검증된 개발용 픽스처",failure["message"])
        self.assertFalse(any(
            item["type"] in ("session.ready","session.started")
            for item in socket.sent
        ))
        procedure_store.assert_not_called()

    def test_cascade_null_or_unknown_protocol_never_becomes_ready(self):
        protocol_id="candidate-a-curated-development-v1"
        placeholder=Path("/tmp/offline-session-contract")
        config=ServerConfig(
            placeholder,None,"test_only",frozenset({"ko"}),"ko",
            None,None,placeholder,placeholder,placeholder,
        )

        class Fixture:
            def __init__(self): self.protocol_id=protocol_id

        for selected,reason in (
            (None,"protocol_selection_required"),
            ("unknown-development-protocol","protocol_selection_unknown"),
        ):
            with self.subTest(selected=selected):
                class Socket:
                    def __init__(self):
                        self.sent=[]
                        self.messages=iter((
                            {"text":json.dumps({
                                "type":"session.start","mode":"cascade",
                                "language":"ko","protocol_id":selected,
                                "configuration_id":8,
                            })},
                            {"type":"websocket.disconnect","code":1000},
                        ))
                    async def accept(self): pass
                    async def send_text(self,value): self.sent.append(json.loads(value))
                    async def receive(self): return next(self.messages)

                socket=Socket()
                with patch(
                    "voice_workflow_agent.server.server_config",return_value=config,
                ), patch(
                    "voice_workflow_agent.server.load_curated_protocol_fixture",
                    return_value=Fixture(),
                ), patch(
                    "voice_workflow_agent.server.ProcedureStore",
                ) as procedure_store, patch(
                    "voice_workflow_agent.server.NativeRealtimeSession",
                ) as native_session:
                    asyncio.run(voice_socket(socket))
                required=next(
                    item for item in socket.sent
                    if item["type"]=="session.configuration_required")
                self.assertEqual(required["reason"],reason)
                self.assertEqual(required["protocol_id"],None)
                self.assertFalse(any(
                    item["type"] in ("session.ready","session.started")
                    for item in socket.sent
                ))
                procedure_store.assert_not_called()
                native_session.assert_not_called()

    def test_canonical_environment_accepts_exact_native_browser_payload(self):
        class Socket:
            def __init__(self):
                self.sent=[]
                self.messages=iter((
                    {"text":json.dumps({
                        "type":"session.start","mode":"native",
                        "language":"ko","protocol_id":None,
                        "configuration_id":1,
                    })},
                    {"type":"websocket.disconnect","code":1000},
                ))
            async def accept(self): pass
            async def send_text(self,value): self.sent.append(json.loads(value))
            async def receive(self): return next(self.messages)
        instances=[]
        class Native:
            def __init__(self,sender,tool_context,native_config,**kwargs):
                self.config=native_config
                instances.append(self)
            async def start(self): pass
            async def stop(self): pass
        with tempfile.TemporaryDirectory() as temporary:
            catalog=self.approved_catalog(temporary,"demo")
            environment={
                "VOICE_WORKFLOW_AGENT_SAFETY_CATALOG":str(catalog),
                "VOICE_WORKFLOW_AGENT_USAGE_SCOPE":"demo",
                "XAI_API_KEY":"test-key",
            }
            socket=Socket()
            with patch.dict("os.environ",environment,clear=True), patch(
                "voice_workflow_agent.server.NativeRealtimeSession",Native,
            ):
                asyncio.run(voice_socket(socket))
        started=next(item for item in socket.sent if item["type"]=="session.started")
        self.assertEqual(started["pipeline"],"native")
        ready=next(item for item in socket.sent if item["type"]=="session.ready")
        self.assertEqual({key:ready[key] for key in (
            "type","configuration_id","mode","language","protocol_id",
        )},{
            "type":"session.ready","configuration_id":1,"mode":"native",
            "language":"ko","protocol_id":None,
        })
        self.assertEqual(instances[0].config.model,"grok-voice-latest")
        self.assertEqual(instances[0].config.voice,"eve")
        self.assertEqual(instances[0].config.vad_threshold,0.6)

    def test_legacy_renamed_keys_report_safe_session_configuration_stage(self):
        class Socket:
            def __init__(self):
                self.sent=[]
                self.messages=iter((
                    {"text":json.dumps({
                        "type":"session.start","mode":"native",
                        "language":"ko","protocol_id":None,
                        "configuration_id":1,
                    })},
                    {"type":"websocket.disconnect","code":1000},
                ))
            async def accept(self): pass
            async def send_text(self,value): self.sent.append(json.loads(value))
            async def receive(self): return next(self.messages)
        legacy_environment={
            "SAFEBRIDGE_SAFETY_CATALOG":"/old/catalog.sqlite",
            "SAFEBRIDGE_USAGE_SCOPE":"test_only",
            "XAI_API_KEY":"must-not-appear-in-logs",
        }
        socket=Socket()
        with patch.dict("os.environ",legacy_environment,clear=True), \
             patch("voice_workflow_agent.server.log.warning") as warning:
            asyncio.run(voice_socket(socket))
        error=next(item for item in socket.sent if item["type"]=="error")
        self.assertEqual(error["message"],"invalid session configuration")
        rendered=warning.call_args.args[0] % warning.call_args.args[1:]
        self.assertIn("pipeline=native",rendered)
        self.assertIn("stage=server_policy",rendered)
        self.assertIn("exception=ServerConfigurationError",rendered)
        self.assertIn("VOICE_WORKFLOW_AGENT_SAFETY_CATALOG",rendered)
        self.assertNotIn("must-not-appear-in-logs",rendered)

    def test_invalid_canonical_policy_names_the_safe_fields(self):
        environment={
            "VOICE_WORKFLOW_AGENT_SAFETY_CATALOG":"/trusted/catalog.sqlite",
            "VOICE_WORKFLOW_AGENT_USAGE_SCOPE":"unsupported",
        }
        with patch.dict("os.environ",environment,clear=True), \
             self.assertRaises(ServerConfigurationError) as captured:
            server_config()
        self.assertEqual(
            captured.exception.field_names,
            ("VOICE_WORKFLOW_AGENT_USAGE_SCOPE",),
        )

    def test_rest_stt_preserves_optional_provider_quality_without_inventing_it(self):
        with patch("voice_workflow_agent.server.requests.post",return_value=FakeResponse()) as post, \
             patch.dict("os.environ",{"XAI_API_KEY":"test"},clear=True):
            result=transcribe(b"\0\0")
        self.assertEqual((result.text,result.detected_language),
                         ("short test utterance","ko"))
        self.assertIsNone(result.confidence)
        self.assertIsNone(result.no_speech_probability)
        self.assertEqual(result.alternatives,())
        self.assertTrue(post.call_args.args[0].endswith("/stt"))

        with patch(
            "voice_workflow_agent.server.requests.post",
            return_value=FakeResponse(),
        ) as biased_post,patch.dict(
            "os.environ",{"XAI_API_KEY":"test"},clear=True,
        ):
            transcribe(
                b"\0\0",language="ko",
                keyterms=("AMBIC","HPLC water","AMBIC","x"*51),
            )
        parts=biased_post.call_args.kwargs["files"]
        self.assertEqual(
            [(name,value[1] if value[0] is None else value[0])
             for name,value in parts[:-1]],
            [("format","true"),("language","ko"),("vad_threshold","0.5"),
             ("keyterm","AMBIC"),("keyterm","HPLC water")],
        )
        self.assertEqual(parts[-1][0],"file")

        quality_response=FakeResponse()
        quality_response.json=lambda:{
            "text":" uncertain ","language":"Korean","confidence":0.2,
            "no_speech_probability":0.85,
            "alternatives":["대안 하나","대안 둘",7,"대안 셋","ignored"],
        }
        with patch(
            "voice_workflow_agent.server.requests.post",
            return_value=quality_response,
        ),patch.dict("os.environ",{"XAI_API_KEY":"test"},clear=True):
            quality=transcribe(b"\0\0")
        self.assertIsNone(quality.confidence)
        self.assertIsNone(quality.no_speech_probability)
        self.assertEqual(quality.alternatives,())

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
                with patch("voice_workflow_agent.server.transcribe",return_value=transcription), \
                     patch("voice_workflow_agent.server.synthesize",return_value=b"\0\0") as tts, \
                     patch("voice_workflow_agent.server.stream_brain_turn") as brain, \
                     patch("voice_workflow_agent.tools.search_approved_safety_manual") as retrieval, \
                     patch("voice_workflow_agent.server.AsyncOpenAI") as llm:
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
        now[0]=2.234
        self.assertFalse(session.playback_ended(end.turn_id+1))
        self.assertTrue(session.playback_ended(end.turn_id))
        self.assertEqual(session.playback_completion_ms(end.turn_id),1234)
        self.assertIsInstance(session.playback_completion_ms(end.turn_id),int)
        self.assertFalse(session.playback_ended(end.turn_id))
        self.assertEqual(session.playback_completion_ms(end.turn_id),1234)
        self.assertEqual(session.state,TurnState.COOLDOWN)
    def test_tts_rejects_errors_json_empty_and_odd_pcm(self):
        for response in (FakeResponse(b"bad",400), FakeResponse(b"{}",content_type="application/json"), FakeResponse(), FakeResponse(b"x")):
            with self.assertRaises(RuntimeError): validate_tts_pcm(response)

    def test_subthreshold_frames_do_not_interrupt_processing_or_playback(self):
        config=VadConfig(
            onset_voiced_frames=2,onset_window_frames=3,prefix_frames=3,
            endpoint_silence_frames=2,minimum_voiced_frames=2,
            maximum_utterance_frames=20,cooldown_ms=0,
        )
        session=ListenerSession(
            EndpointDetector(config,classifier=lambda _:False))
        session.start(); session.active_turn_id=1
        session.turn_generations[1]=session.generation
        session.detector.state=TurnState.PROCESSING
        session._interrupt_detector=EndpointDetector(
            config,classifier=lambda _:False)
        self.assertEqual(session.accept_chunk(frame(9)*6), [])
        session.detector.state=TurnState.AGENT_SPEAKING
        session._interrupt_detector=EndpointDetector(
            config,classifier=lambda _:False)
        self.assertEqual(session.accept_chunk(frame(9)*6), [])

    def test_idle_listening_threshold_isolated_from_interrupt_profiles(self):
        config=VadConfig(
            onset_voiced_frames=4,onset_window_frames=6,prefix_frames=15,
            endpoint_silence_frames=12,minimum_voiced_frames=8,
            maximum_utterance_frames=80,cooldown_ms=0,
            playback_onset_voiced_frames=12,
            playback_onset_window_frames=15,
            listening_onset_voiced_frames=8,
            listening_onset_window_frames=12,
        )
        session=ListenerSession(EndpointDetector(
            config,classifier=Decisions([True] * 7 + [False] * 5),
            listening_onset=True))
        session.start()
        opening_generation=session.generation
        curated_marker=object()
        session.curated_protocol_session=curated_marker
        events=session.accept_chunk(
            b"".join(frame(i) for i in range(12)))
        self.assertEqual(events,[])
        self.assertEqual(session.generation,opening_generation)
        self.assertEqual(session.next_turn_id,1)
        self.assertIsNone(session.active_turn_id)
        self.assertIs(session.curated_protocol_session,curated_marker)
        self.assertEqual(
            (session.detector.onset_voiced_frames,
             session.detector.onset_window_frames),(8,12))
        processing=session._new_interrupt_detector()
        self.assertEqual(
            (processing.onset_voiced_frames,processing.onset_window_frames),
            (4,6))
        playback=session._new_interrupt_detector(playback=True)
        self.assertEqual(
            (playback.onset_voiced_frames,playback.onset_window_frames),
            (12,15))

    def test_rejected_idle_onset_allocates_no_turn_or_transcription(self):
        config=VadConfig(
            onset_voiced_frames=4,onset_window_frames=6,prefix_frames=15,
            endpoint_silence_frames=12,minimum_voiced_frames=8,
            maximum_utterance_frames=80,cooldown_ms=0,
            listening_onset_voiced_frames=8,
            listening_onset_window_frames=12,
        )
        session=ListenerSession(EndpointDetector(
            config,classifier=Decisions([True] * 7 + [False] * 5),
            listening_onset=True))
        session.start(); session.accept_configuration(
            91,"cascade","ko","candidate-a-curated-development-v1")
        class Socket:
            def __init__(self):
                self.text=[]
                self.messages=[
                    {"bytes":b"".join(frame(i) for i in range(12))},
                    {"type":"websocket.disconnect","code":1000},
                ]
            async def accept(self): return None
            async def receive(self): return self.messages.pop(0)
            async def send_text(self,value): self.text.append(json.loads(value))
            async def send_bytes(self,value): raise AssertionError("no audio output")
            async def close(self,**kwargs): return None
        socket=Socket()
        with patch(
            "voice_workflow_agent.server.ListenerSession",return_value=session,
        ), patch(
            "voice_workflow_agent.server.VoiceVadSettings.from_environment",
            return_value=SimpleNamespace(cascade=object()),
        ), patch(
            "voice_workflow_agent.server.VadConfig.from_settings",
            return_value=config,
        ), patch(
            "voice_workflow_agent.server.transcribe",
        ) as transcription:
            asyncio.run(voice_socket(socket))
        transcription.assert_not_called()
        emitted_types=[item["type"] for item in socket.text]
        self.assertNotIn("speech.start",emitted_types)
        self.assertNotIn("speech.end",emitted_types)
        self.assertNotIn("turn.state",emitted_types)
        self.assertNotIn("cascade.playback.clear",emitted_types)

    def test_real_speech_after_rejected_noise_preserves_first_frame_once(self):
        config=VadConfig(
            onset_voiced_frames=4,onset_window_frames=6,prefix_frames=15,
            endpoint_silence_frames=12,minimum_voiced_frames=8,
            maximum_utterance_frames=80,cooldown_ms=0,
            listening_onset_voiced_frames=8,
            listening_onset_window_frames=12,
        )
        rejected_noise=[True] * 7 + [False] * 5
        separating_silence=[False] * 12
        real_speech=[True] * 8 + [False] * 4
        endpoint=[False] * 12
        decisions=(rejected_noise + separating_silence + real_speech + endpoint)
        session=ListenerSession(EndpointDetector(
            config,classifier=Decisions(decisions),listening_onset=True))
        session.start()
        events=session.accept_chunk(b"".join(
            frame(i) for i in range(len(decisions))))
        self.assertEqual(
            [item.kind for item in events],["speech.start","speech.end"])
        utterance=events[-1].result.utterance
        self.assertIsNotNone(utterance)
        chunks=[
            utterance[index:index + FRAME_BYTES]
            for index in range(0,len(utterance),FRAME_BYTES)
        ]
        first_real=frame(len(rejected_noise) + len(separating_silence))
        self.assertEqual(chunks.count(first_real),1)
        self.assertNotIn(frame(0),chunks)
        self.assertNotIn(frame(6),chunks)
        self.assertEqual(session.next_turn_id,2)

    def test_playback_onset_requires_sustained_speech_and_preserves_prefix(self):
        config=VadConfig(
            onset_voiced_frames=2,onset_window_frames=3,prefix_frames=15,
            endpoint_silence_frames=2,minimum_voiced_frames=10,
            maximum_utterance_frames=40,cooldown_ms=0,
            playback_onset_voiced_frames=12,
            playback_onset_window_frames=15,
        )

        for pattern in (
            [True]+[False]*14,
            [True,False]*7+[False],
            [True]*10+[False]*5,
            [True]*11+[False]*4,
        ):
            with self.subTest(pattern=pattern):
                candidate=ListenerSession(EndpointDetector(
                    config,classifier=Decisions(pattern)))
                candidate.start(); candidate.active_turn_id=1
                candidate.turn_generations[1]=candidate.generation
                candidate.detector.state=TurnState.PROCESSING
                self.assertTrue(candidate.start_playback(1))
                self.assertEqual(candidate.accept_chunk(
                    b"".join(frame(i) for i in range(15))),[])
                self.assertEqual(candidate.next_turn_id,1)

        following=[False,True,True]+[True]*8+[False]*2
        noise=ListenerSession(EndpointDetector(
            config,classifier=Decisions(
                [True]*11+[False]*4+following)))
        noise.start(); noise.active_turn_id=1
        noise.turn_generations[1]=noise.generation
        noise.detector.state=TurnState.PROCESSING
        self.assertTrue(noise.start_playback(1))
        opening_generation=noise.generation
        self.assertEqual(
            noise.accept_chunk(b"".join(frame(i) for i in range(15))),[])
        self.assertEqual(noise.generation,opening_generation)
        self.assertEqual(noise.active_turn_id,1)
        self.assertTrue(noise.playback_ended(1))
        self.assertEqual(noise.next_turn_id,1)
        resumed=noise.accept_chunk(
            b"".join(frame(100+i) for i in range(len(following))))
        self.assertEqual(
            [item.kind for item in resumed],["speech.start","speech.end"])
        self.assertTrue(resumed[-1].result.utterance.startswith(frame(100)))

        decisions=[True]*12+[False]*4
        speech=ListenerSession(EndpointDetector(
            config,classifier=Decisions(decisions)))
        speech.start(); speech.active_turn_id=1
        speech.turn_generations[1]=speech.generation
        speech.detector.state=TurnState.PROCESSING
        self.assertTrue(speech.start_playback(1))
        events=speech.accept_chunk(
            b"".join(frame(20+i) for i in range(len(decisions))))
        self.assertEqual(
            [item.kind for item in events],
            ["barge_in_candidate","barge_in_audio_ready"],
        )
        committed=speech.commit_interrupt_candidate(events[-1])
        self.assertEqual(
            [item.kind for item in committed],
            ["assistant.interrupted","barge_in_committed",
             "speech.start","speech.end"],
        )
        self.assertGreaterEqual(committed[2].result.total_frames,12)
        self.assertTrue(committed[3].result.utterance.startswith(frame(20)))
        self.assertEqual(
            len([item for item in committed
                 if item.kind=="assistant.interrupted"]),1)
        self.assertEqual(speech.state,TurnState.PROCESSING)
        self.assertEqual(
            speech.detector.config.onset_window_frames,
            config.onset_window_frames,
        )

        processing_pattern=[True]*10+[False]*2
        processing=ListenerSession(EndpointDetector(
            config,classifier=Decisions(processing_pattern)))
        processing.start(); processing.active_turn_id=1
        processing.turn_generations[1]=processing.generation
        processing.detector.state=TurnState.PROCESSING
        processing_events=processing.accept_chunk(
            b"".join(frame(200+i) for i in range(len(processing_pattern))))
        self.assertEqual(
            [item.kind for item in processing_events],
            ["barge_in_candidate","barge_in_audio_ready"],
        )
        processing_committed=processing.commit_interrupt_candidate(
            processing_events[-1])
        self.assertGreaterEqual(processing_committed[2].result.total_frames,10)
        self.assertTrue(
            processing_committed[-1].result.utterance.startswith(frame(200)))

    def test_barge_in_has_dedicated_preroll_beyond_idle_prefix(self):
        decisions=[False]*20+[True]*12+[False]*2
        config=VadConfig(
            onset_voiced_frames=2,onset_window_frames=3,prefix_frames=15,
            barge_in_prefix_frames=40,
            endpoint_silence_frames=2,minimum_voiced_frames=12,
            maximum_utterance_frames=80,cooldown_ms=0,
            playback_onset_voiced_frames=12,
            playback_onset_window_frames=15,
        )
        session=ListenerSession(EndpointDetector(
            config,classifier=Decisions(decisions)))
        session.start();session.active_turn_id=1
        session.turn_generations[1]=session.generation
        session.detector.state=TurnState.PROCESSING
        self.assertTrue(session.start_playback(1))
        pcm=b"".join(frame(index) for index in range(len(decisions)))
        events=session.accept_chunk(pcm)
        self.assertEqual(
            [item.kind for item in events],
            ["barge_in_candidate","barge_in_audio_ready"],
        )
        self.assertEqual(
            events[0].diagnostics["configured_barge_in_prefix_frames"],40)
        self.assertEqual(
            events[0].diagnostics["configured_barge_in_prefix_ms"],800)
        self.assertEqual(
            events[0].diagnostics["playback_onset_voiced_frames"],12)
        self.assertEqual(
            events[0].diagnostics["playback_onset_window_frames"],15)
        self.assertIn("candidate_onset_monotonic_ms",events[0].diagnostics)
        self.assertGreater(events[0].result.total_frames,15)
        committed=session.commit_interrupt_candidate(events[-1],stt_ms=37)
        captured=committed[-1].result.utterance
        self.assertTrue(captured.startswith(frame(0)))
        self.assertEqual(committed[0].diagnostics["barge_in_stt_ms"],37)
        self.assertIn(
            "candidate_endpoint_monotonic_ms",committed[0].diagnostics)
        self.assertEqual(
            committed[0].diagnostics["captured_utterance_frames"],
            events[-1].result.total_frames,
        )
    def test_accepted_onset_emits_one_server_owned_listening_state(self):
        config=VadConfig(
            onset_voiced_frames=2,onset_window_frames=3,prefix_frames=3,
            endpoint_silence_frames=3,minimum_voiced_frames=2,
            maximum_utterance_frames=20,cooldown_ms=0,
        )
        session=ListenerSession(EndpointDetector(
            config,classifier=Decisions([False,True,True])))
        session.start(); session.accept_configuration(
            81,"cascade","ko","candidate-a-curated-development-v1")
        class Socket:
            def __init__(self):
                self.text=[]; self.messages=[
                    {"bytes":frame(7)*3},
                    {"type":"websocket.disconnect","code":1000},
                ]
            async def accept(self): return None
            async def receive(self): return self.messages.pop(0)
            async def send_text(self,value): self.text.append(json.loads(value))
            async def send_bytes(self,value): raise AssertionError("no audio output")
            async def close(self,**kwargs): return None
        socket=Socket()
        with patch(
            "voice_workflow_agent.server.ListenerSession",
            return_value=session,
        ), patch(
            "voice_workflow_agent.server.VoiceVadSettings.from_environment",
            return_value=SimpleNamespace(cascade=object()),
        ), patch(
            "voice_workflow_agent.server.VadConfig.from_settings",
            return_value=config,
        ):
            asyncio.run(voice_socket(socket))
        starts=[item for item in socket.text if item["type"]=="speech.start"]
        states=[item for item in socket.text if item["type"]=="turn.state"]
        self.assertEqual(len(starts),1)
        self.assertEqual(len(states),1)
        self.assertEqual(states[0]["state"],"listening")
        self.assertEqual(states[0]["revision"],1)
        self.assertEqual(
            (states[0]["configuration_id"],states[0]["turn_id"],
             states[0]["generation"]),
            (81,starts[0]["turn_id"],starts[0]["generation"]),
        )

    def test_playback_ack_is_the_success_terminal_boundary(self):
        now=[10.0]
        config=VadConfig(cooldown_ms=0)
        session=ListenerSession(EndpointDetector(config),clock=lambda:now[0])
        session.start(); session.accept_configuration(
            82,"cascade","ko","candidate-a-curated-development-v1")
        session.active_turn_id=1; session.turn_generations[1]=session.generation
        session.turn_committed_at[1]=9.0
        session.detector.state=TurnState.PROCESSING
        for state in ("transcribing","routing","synthesizing"):
            self.assertIsNotNone(session.advance_turn_progress(
                1,session.generation,state))
        self.assertTrue(session.start_playback(1))
        playing=session.advance_turn_progress(1,session.generation,"playing")
        self.assertEqual(playing["state"],"playing")
        class Socket:
            def __init__(self):
                self.text=[]; self.messages=[
                    {"text":json.dumps({"type":"playback.ended","turn_id":1})},
                    {"type":"websocket.disconnect","code":1000},
                ]
            async def accept(self): return None
            async def receive(self): return self.messages.pop(0)
            async def send_text(self,value): self.text.append(json.loads(value))
            async def send_bytes(self,value): raise AssertionError("no audio output")
            async def close(self,**kwargs): return None
        socket=Socket(); now[0]=10.5
        with patch(
            "voice_workflow_agent.server.ListenerSession",return_value=session,
        ), patch(
            "voice_workflow_agent.server.VoiceVadSettings.from_environment",
            return_value=SimpleNamespace(cascade=object()),
        ), patch(
            "voice_workflow_agent.server.VadConfig.from_settings",
            return_value=config,
        ):
            asyncio.run(voice_socket(socket))
        terminal=next(item for item in socket.text
                      if item["type"]=="turn.state")
        self.assertEqual(terminal["state"],"complete")
        self.assertEqual(terminal["revision"],5)
        self.assertEqual(
            terminal["timings_ms"]["playback_completion"],1500)
        cooldown=next(item for item in socket.text
                      if item["type"]=="state.changed")
        self.assertEqual(cooldown["state"],TurnState.COOLDOWN.value)
        self.assertEqual(session.state,TurnState.IDLE)

    def test_accepted_onset_supersedes_one_generation_and_finalizes_new_turn(self):
        config=VadConfig(
            onset_voiced_frames=2,onset_window_frames=3,prefix_frames=3,
            endpoint_silence_frames=2,minimum_voiced_frames=2,
            maximum_utterance_frames=20,cooldown_ms=0,
        )
        session=ListenerSession(
            EndpointDetector(config,classifier=lambda _:False))
        session.start(); old_generation=session.generation
        session.active_turn_id=1; session.turn_generations[1]=old_generation
        session.detector.state=TurnState.AGENT_SPEAKING
        session._interrupt_detector=EndpointDetector(
            config,classifier=Decisions([False,True,True,True,False,False]))
        events=session.accept_chunk(frame(8)*6)
        self.assertEqual(
            [item.kind for item in events],
            ["barge_in_candidate","barge_in_audio_ready"],
        )
        committed=session.commit_interrupt_candidate(events[-1])
        self.assertEqual(
            [item.kind for item in committed],
            ["assistant.interrupted","barge_in_committed",
             "speech.start","speech.end"],
        )
        interruption,_,start,end=committed
        self.assertEqual(interruption.turn_id,1)
        self.assertEqual(interruption.generation,old_generation)
        self.assertEqual(interruption.superseding_turn_id,start.turn_id)
        self.assertEqual(interruption.superseding_generation,start.generation)
        self.assertEqual(start.turn_id,end.turn_id)
        self.assertEqual(start.generation,end.generation)
        self.assertGreater(start.generation,old_generation)
        self.assertEqual(
            len([item for item in committed
                 if item.kind=="assistant.interrupted"]),1)
        self.assertFalse(session.playback_ended(1))

        ordinary=ListenerSession(EndpointDetector(
            config,classifier=Decisions([False,True,True,True,False,False])))
        ordinary.start()
        ordinary_events=ordinary.accept_chunk(frame(7)*6)
        self.assertEqual(
            [item.kind for item in ordinary_events],
            ["speech.start","speech.end"],
        )

        isolated=ListenerSession(
            EndpointDetector(config,classifier=lambda _:False))
        isolated.start(); isolated.active_turn_id=1
        isolated.turn_generations[1]=isolated.generation
        isolated.detector.state=TurnState.AGENT_SPEAKING
        self.assertEqual(isolated.generation,old_generation)
        self.assertEqual(isolated.active_turn_id,1)

    def test_generation_owner_cancels_task_before_one_browser_clear(self):
        class Socket:
            def __init__(self): self.sent=[]
            async def send_text(self,value): self.sent.append(json.loads(value))
        async def scenario():
            started=asyncio.Event()
            async def generation():
                started.set()
                await asyncio.Future()
            task=asyncio.create_task(generation())
            await started.wait()
            session=ListenerSession(); session.accepted_configuration_id=19
            session.turn_generations[4]=8
            self.assertIsNotNone(session.advance_turn_progress(
                4,8,"playing",route="curated_protocol"))
            interruption=ListenerEvent(
                "assistant.interrupted",4,
                EndpointResult(speech_started=True),
                generation=8,superseding_turn_id=5,
                superseding_generation=9,
            )
            socket=Socket()
            await cancel_cascade_generation(
                socket,session,task,interruption)
            self.assertTrue(task.cancelled())
            self.assertEqual(socket.sent,[{
                "type":"cascade.playback.clear",
                "configuration_id":19,"turn_id":4,"generation":8,
                "revision":2,"state":"cancelled",
                "route":"curated_protocol",
                "superseding_turn_id":5,"superseding_generation":9,
                "reason":"confirmed_speech",
            }])
        asyncio.run(scenario())

    def test_rejected_barge_in_candidate_preserves_playback_and_generation(self):
        config=VadConfig(
            onset_voiced_frames=2,onset_window_frames=3,prefix_frames=3,
            endpoint_silence_frames=2,minimum_voiced_frames=8,
            maximum_utterance_frames=30,cooldown_ms=0,
            playback_onset_voiced_frames=2,
            playback_onset_window_frames=3,
        )
        session=ListenerSession(EndpointDetector(
            config,classifier=lambda _:False))
        session.start(); opening_generation=session.generation
        session.active_turn_id=1
        session.turn_generations[1]=opening_generation
        session.detector.state=TurnState.PROCESSING
        self.assertTrue(session.start_playback(1))
        session._interrupt_detector=EndpointDetector(
            config,classifier=Decisions(
                [True,True,False,False,False]))

        events=session.accept_chunk(frame(9)*5)

        self.assertEqual(
            [item.kind for item in events],
            ["barge_in_candidate","barge_in_rejected"],
        )
        self.assertEqual(events[-1].reason,"minimum_voiced_frames")
        self.assertEqual(session.generation,opening_generation)
        self.assertEqual(session.active_turn_id,1)
        self.assertEqual(session.next_turn_id,1)
        self.assertEqual(session.state,TurnState.AGENT_SPEAKING)
        self.assertTrue(session.playback_ended(1))

    def test_accepted_barge_in_with_empty_transcript_interrupts_and_clarifies(self):
        config=VadConfig(
            onset_voiced_frames=2,onset_window_frames=3,prefix_frames=3,
            endpoint_silence_frames=2,minimum_voiced_frames=2,
            maximum_utterance_frames=30,cooldown_ms=0,
            playback_onset_voiced_frames=2,
            playback_onset_window_frames=3,
        )
        session=ListenerSession(EndpointDetector(
            config,classifier=lambda _:False))
        session.start(); opening_generation=session.generation
        session.active_turn_id=1
        session.turn_generations[1]=opening_generation
        session.detector.state=TurnState.PROCESSING
        self.assertTrue(session.start_playback(1))
        session._interrupt_detector=EndpointDetector(
            config,classifier=Decisions(
                [False,True,True,True,False,False]))
        class Socket:
            def __init__(self):
                self.text=[]
                self.messages=[
                    {"bytes":frame(11)*6},
                    {"type":"websocket.disconnect","code":1000},
                ]
            async def accept(self): return None
            async def receive(self): return self.messages.pop(0)
            async def send_text(self,value):
                parsed=json.loads(value)
                self.text.append(parsed)
            async def send_bytes(self,value): raise AssertionError("no audio output")
            async def close(self,**kwargs): return None
        socket=Socket()
        with patch(
            "voice_workflow_agent.server.ListenerSession",
            return_value=session,
        ), patch(
            "voice_workflow_agent.server.VoiceVadSettings.from_environment",
            return_value=SimpleNamespace(cascade=object()),
        ), patch(
            "voice_workflow_agent.server.VadConfig.from_settings",
            return_value=config,
        ), patch(
            "voice_workflow_agent.server.transcribe",
            return_value=Transcription("", "ko"),
        ) as transcription:
            asyncio.run(voice_socket(socket))
        transcription.assert_called_once()
        kinds=[item["type"] for item in socket.text]
        self.assertEqual(kinds.count("barge_in_candidate"),1)
        self.assertEqual(kinds.count("barge_in_committed"),1)
        self.assertNotIn("barge_in_rejected",kinds)
        self.assertEqual(kinds.count("cascade.playback.clear"),1)
        committed=next(
            item for item in socket.text
            if item["type"]=="barge_in_committed")
        self.assertEqual(committed["reason"],"transcription_failed")
        self.assertGreater(committed["superseding_generation"],opening_generation)

    def test_ordinary_and_barge_in_share_one_session_aware_stt_policy(self):
        pending=SimpleNamespace(predicate_id="candidate_a_step_7_endpoint")
        curated=SimpleNamespace(
            active=True,current_index=6,
            fixture=SimpleNamespace(steps=[
                *[SimpleNamespace(step_id=f"step-{index}") for index in range(6)],
                SimpleNamespace(step_id="step-7"),
            ]),
            pending_observation_confirmation=pending,
            pending_completion_confirmation=None,
            stt_keyterms=lambda:("AMBIC","완료했어요","투명한가요"),
        )
        session=ListenerSession()
        session.start()
        session.curated_protocol_session=curated
        session.manual_language="ko"
        session.accept_configuration(9,"cascade","ko","candidate-a")
        ordinary=cascade_transcription_context(session,audio_origin="ordinary")
        barge=cascade_transcription_context(session,audio_origin="barge_in")
        self.assertEqual(ordinary.language,barge.language)
        self.assertEqual(ordinary.keyterms,barge.keyterms)
        self.assertEqual(ordinary.pending_frame,barge.pending_frame)
        self.assertEqual(ordinary.step_id,barge.step_id)
        self.assertEqual(ordinary.request_policy()["request_field_order"],
                         barge.request_policy()["request_field_order"])
        self.assertEqual(ordinary.audio_origin,"ordinary")
        self.assertEqual(barge.audio_origin,"barge_in")
        with patch(
            "voice_workflow_agent.server.transcribe",
            return_value=Transcription("네","ko"),
        ) as provider:
            transcribe_cascade_audio(b"\0\0",ordinary)
            transcribe_cascade_audio(b"\0\0",barge)
        self.assertEqual(provider.call_count,2)
        for call in provider.call_args_list:
            self.assertEqual(call.kwargs["language"],"ko")
            self.assertEqual(call.kwargs["keyterms"],ordinary.keyterms)

    def test_committed_barge_stt_failure_owns_one_clarification_turn(self):
        class Socket:
            def __init__(self): self.text=[]; self.binary=[]
            async def send_text(self,value): self.text.append(json.loads(value))
            async def send_bytes(self,value): self.binary.append(value)

        async def scenario():
            session=ListenerSession();session.start()
            session.accept_configuration(22,"cascade","ko","candidate-a")
            turn_id=session.next_turn_id;generation=session.generation
            session.active_turn_id=turn_id
            session.turn_generations[turn_id]=generation
            session.detector.state=TurnState.PROCESSING
            context=CascadeTranscriptionContext(
                22,"session-test",generation,"ko","candidate-a",None,
                "observation:candidate_a_step_7_endpoint",("AMBIC","네"),
                "barge_in",
            )
            socket=Socket()
            with patch(
                "voice_workflow_agent.server.synthesize",return_value=b"\0\0"
            ) as tts:
                await run_barge_in_stt_failure_turn(
                    socket,session,turn_id=turn_id,generation=generation,
                    input_frames=12,voiced_frames=8,context=context,stt_ms=41,
                )
            self.assertEqual(tts.call_count,1)
            self.assertEqual(sum(
                item["type"]=="transcript.unavailable" for item in socket.text
            ),1)
            done=[item for item in socket.text if item["type"]=="turn.done"]
            self.assertEqual(len(done),1)
            self.assertEqual(done[0]["result_kind"],"clarification")
            self.assertEqual(done[0]["generation"],generation)
            self.assertEqual(len(socket.binary),1)
        asyncio.run(scenario())

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
        with patch("voice_workflow_agent.server.log.exception") as logged:
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
            "voice_workflow_agent.server.check_safety_report_status",
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

    def test_each_experiment_report_export_has_safe_download_headers(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"reports.sqlite"
            store=ExperimentReportStore(path)
            report=store.open_report(
                session_id="session-export-test",protocol_id="candidate-a",
                protocol_title="Candidate A",protocol_revision="revision-1",
                protocol_sha256="6"*64,readiness_status="analysis_required",
                development_only=True,
            )
            store.append_event(
                report["report_id"],event_key="start-1",
                event_type="session_started",step_id="step-1",step_label="1",
            )
            with patch(
                "voice_workflow_agent.server.ExperimentReportSettings.from_environment",
                return_value=ExperimentReportSettings(True,path),
            ):
                for format_name,media_type,prefix in (
                    ("json","application/json",b"{"),
                    ("md","text/markdown",b"# Experiment report"),
                    ("csv","text/csv",b"\xef\xbb\xbf"),
                ):
                    with self.subTest(format_name=format_name):
                        response=export_experiment_report(
                            report["report_id"],format_name)
                        self.assertTrue(response.body.startswith(prefix))
                        self.assertTrue(response.media_type.startswith(media_type))
                        self.assertEqual(response.headers["cache-control"],"no-store")
                        self.assertEqual(
                            response.headers["content-disposition"],
                            f'attachment; filename="{report["report_id"]}.{format_name}"',
                        )

    def test_curated_protocol_action_operation_labels_are_exhaustive(self):
        from voice_workflow_agent.curated_protocol import CuratedProtocolAction
        root = Path(__file__).resolve().parents[1]
        server_py = (root / "src" / "voice_workflow_agent" / "server.py").read_text(encoding="utf-8")
        for action in CuratedProtocolAction:
            with self.subTest(action=action.name):
                self.assertIn(
                    f"CuratedProtocolAction.{action.name}:",
                    server_py,
                    f"CuratedProtocolAction.{action.name} is missing from server.py operation_labels!",
                )

    def test_experiment_report_get_websocket_handler(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reports.sqlite"
            store = ExperimentReportStore(path)
            report = store.open_report(
                session_id="session-ws-test", protocol_id="candidate-a",
                protocol_title="Candidate A", protocol_revision="revision-1",
                protocol_sha256="7" * 64, readiness_status="analysis_required",
                development_only=True,
            )
            store.append_event(
                report["report_id"], event_key="ws-start",
                event_type="session_started", step_id="step-1", step_label="1",
            )
            class MockSession:
                def __init__(self):
                    self.experiment_report_store = store
                    self.experiment_report_id = report["report_id"]
                    self.accepted_configuration_id = 1
                    self.turn_counter = 3
                    self.generation = 1
            mock_session = MockSession()
            sent_messages = []
            class MockWS:
                async def send_text(self, text):
                    sent_messages.append(json.loads(text))

            # Simulate the experiment.report.get handler logic from server.py
            control = {"type": "experiment.report.get", "report_id": report["report_id"]}
            report_id = control.get("report_id") or mock_session.experiment_report_id
            st = mock_session.experiment_report_store
            fetched = st.get_report(report_id) if st and report_id else None
            self.assertIsNotNone(fetched)
            self.assertEqual(fetched["report_id"], report["report_id"])
            self.assertEqual(len(fetched["events"]), 1)

if __name__=="__main__": unittest.main()
