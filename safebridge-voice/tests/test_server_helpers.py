import asyncio, unittest
from unittest.mock import patch
from collections import deque
from safebridge_voice.audio import FRAME_BYTES
from pathlib import Path
from safebridge_voice.server import ListenerSession, ServerConfig, frame_complete_audio, normalize_session_language, server_tool_context, validate_tts_pcm, voice_socket
from safebridge_voice.tools import ToolContext
from safebridge_voice.vad import EndpointDetector, TurnState

class FakeResponse:
    def __init__(self,content=b"",status=200,content_type="audio/pcm"):
        self.content=content; self.status_code=status; self.ok=200<=status<300
        self.headers={"content-type":content_type}; self.text=""
class Decisions:
    def __init__(self,values): self.values=deque(values)
    def __call__(self,frame): return self.values.popleft()
TURN=[False,True,True,True,True,False]+[True]*8+[False]*50
def frame(n=1): return bytes([n%256])*FRAME_BYTES

class ServerTests(unittest.TestCase):
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

if __name__=="__main__": unittest.main()
