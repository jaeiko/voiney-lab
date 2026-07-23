import json, unittest
from safebridge_voice.protocol import ProtocolError, audio_segment_start, event, parse_control

class ProtocolTests(unittest.TestCase):
    def test_controls(self):
        self.assertEqual(parse_control("{\"type\":\"session.start\"}"),{"type":"session.start"})
        self.assertEqual(parse_control("{\"type\":\"session.start\",\"language\":\"en\"}"),
                         {"type":"session.start","language":"en"})
        self.assertEqual(parse_control("{\"type\":\"session.set_language\",\"language\":\"ko\"}"),
                         {"type":"session.set_language","language":"ko"})
        self.assertEqual(parse_control('{"type":"session.set_language_mode","mode":"auto"}'),
                         {"type":"session.set_language_mode","mode":"auto"})
        self.assertEqual(parse_control('{"type":"session.set_language_mode","mode":"manual","language":"en"}'),
                         {"type":"session.set_language_mode","mode":"manual","language":"en"})
        self.assertEqual(parse_control('{"type":"session.reset"}'),{"type":"session.reset"})
        self.assertEqual(parse_control("{\"type\":\"playback.ended\",\"turn_id\":7}"),{"type":"playback.ended","turn_id":7})
        for value in (None,0,-1,True,"1"):
            with self.assertRaises(ProtocolError): parse_control(json.dumps({"type":"playback.ended","turn_id":value}))
        for payload in (
            {"type":"session.set_language_mode","mode":"automatic"},
            {"type":"session.set_language_mode","mode":"manual"},
            {"type":"session.set_language_mode","mode":"auto","language":"en"},
        ):
            with self.assertRaises(ProtocolError): parse_control(json.dumps(payload))
    def test_segment_contract(self):
        self.assertEqual(json.loads(audio_segment_start(4,2,3)),{"type":"audio.segment.start","turn_id":4,"segment_index":2,"frame_count":3,"sample_rate":16000,"encoding":"pcm_s16le","frame_ms":20})
    def test_event_compact(self):
        self.assertEqual(event("ready",sample_rate=16000),"{\"type\":\"ready\",\"sample_rate\":16000}")

if __name__=="__main__": unittest.main()
