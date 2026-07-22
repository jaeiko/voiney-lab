import asyncio, unittest
from unittest.mock import patch
from collections import deque
from safebridge_voice.audio import FRAME_BYTES
from safebridge_voice.server import ListenerSession, frame_complete_audio, validate_tts_pcm, voice_socket
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
        session=ListenerSession(); session.start(); session.active_turn_id=7; generation=session.generation; self.assertTrue(session.is_current(7,generation)); session.history.commit([{"role":"user","content":"x"}])
        session.stop(); self.assertEqual(len(session.history.messages()),1); self.assertFalse(session.is_current(7,generation))
        session.start(); self.assertEqual(len(session.history.messages()),1); self.assertTrue(session.active); session.stop()
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
