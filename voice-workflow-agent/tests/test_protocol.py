import json, unittest
from voice_workflow_agent.protocol import ProtocolError, audio_segment_start, event, parse_control

class ProtocolTests(unittest.TestCase):
    def test_controls(self):
        canonical_start={
            "type":"session.start","mode":"cascade","language":"ko",
            "protocol_id":"candidate-a-curated-development-v1",
            "configuration_id":1,
        }
        self.assertEqual(parse_control(json.dumps(canonical_start)),canonical_start)
        recovery_start={
            **canonical_start,
            "experiment_session_id":"experiment-session-1",
            "experiment_session_version":4,
        }
        self.assertEqual(parse_control(json.dumps(recovery_start)),recovery_start)
        self.assertEqual(parse_control(json.dumps({
            "type":"session.start","pipeline":"cascade","language":"ko",
            "protocol_id":"candidate-a-curated-development-v1",
            "configuration_id":2,
        })),{
            "type":"session.start","mode":"cascade","language":"ko",
            "protocol_id":"candidate-a-curated-development-v1",
            "configuration_id":2,
        })
        self.assertEqual(parse_control("{\"type\":\"session.set_language\",\"language\":\"ko\"}"),
                         {"type":"session.set_language","language":"ko"})
        self.assertEqual(parse_control('{"type":"session.set_language_mode","mode":"auto"}'),
                         {"type":"session.set_language_mode","mode":"auto"})
        self.assertEqual(parse_control('{"type":"session.set_language_mode","mode":"manual","language":"en"}'),
                         {"type":"session.set_language_mode","mode":"manual","language":"en"})
        self.assertEqual(parse_control('{"type":"session.reset"}'),{"type":"session.reset"})
        self.assertEqual(
            parse_control(
                '{"type":"report.status.get","report_id":"sr-20260722-a1b2c3"}'),
            {"type":"report.status.get","report_id":"SR-20260722-A1B2C3"})
        self.assertEqual(parse_control("{\"type\":\"playback.ended\",\"turn_id\":7}"),{"type":"playback.ended","turn_id":7})
        for value in (None,0,-1,True,"1"):
            with self.assertRaises(ProtocolError): parse_control(json.dumps({"type":"playback.ended","turn_id":value}))
        constraints={
            "type":"client.audio_constraints",
            "requested":{
                "echoCancellation":True,"noiseSuppression":True,
                "autoGainControl":False,
            },
            "actual":{
                "echoCancellation":True,"noiseSuppression":None,
                "autoGainControl":False,
            },
        }
        self.assertEqual(parse_control(json.dumps(constraints)),constraints)
        for payload in (
            {"type":"session.start"},
            {"type":"session.start","pipeline":"direct"},
            {"type":"session.start","mode":"native","language":"ko","protocol_id":None,"configuration_id":1},
            {"type":"session.start","mode":"cascade","language":"ko",
             "configuration_id":1},
            {"type":"session.start","mode":"cascade","language":"ko",
             "protocol_id":None},
            {"type":"session.start","mode":"cascade","language":"ko",
             "protocol_id":" candidate-a-curated-development-v1 ",
             "configuration_id":1},
            {"type":"session.start","mode":"cascade","pipeline":"native",
             "language":"ko","protocol_id":None,"configuration_id":1},
            {**canonical_start,"experiment_session_id":"experiment-session-1"},
            {**canonical_start,"experiment_session_version":1},
            {**canonical_start,"experiment_session_id":"bad session","experiment_session_version":1},
            {**canonical_start,"experiment_session_id":"experiment-session-1","experiment_session_version":True},
            {"type":"session.set_language_mode","mode":"automatic"},
            {"type":"session.set_language_mode","mode":"manual"},
            {"type":"session.set_language_mode","mode":"auto","language":"en"},
            {"type":"native.playback.truncate","response_id":"r1",
             "item_id":"i1","audio_end_ms":True},
            {"type":"native.playback.ended","response_id":""},
            {"type":"native.playback.metrics","response_id":"r1",
             "provider_gap_count":0,"provider_gap_ms":0,
             "client_underrun_count":-1,"client_underrun_ms":0,
             "scheduled_chunks":1,"audio_context_state":"running"},
            {"type":"report.status.get","report_id":"not-a-report"},
            {"type":"report.status.get","report_id":7},
            {"type":"client.audio_constraints","requested":{},"actual":{}},
            {"type":"client.audio_constraints","requested":{
                "echoCancellation":True,"noiseSuppression":True,
                "autoGainControl":True,"extra":False},"actual":{
                "echoCancellation":True,"noiseSuppression":True,
                "autoGainControl":True}},
        ):
            with self.assertRaises(ProtocolError): parse_control(json.dumps(payload))
    def test_segment_contract(self):
        self.assertEqual(json.loads(audio_segment_start(4,2,3)),{"type":"audio.segment.start","turn_id":4,"segment_index":2,"frame_count":3,"sample_rate":16000,"encoding":"pcm_s16le","frame_ms":20})
        self.assertEqual(
            json.loads(audio_segment_start(4,2,3,generation=7))["generation"],
            7,
        )
        self.assertEqual(
            json.loads(audio_segment_start(4,2,3,generation=0))["generation"],
            0,
        )
        for generation in (-1,True,"7"):
            with self.assertRaises(ProtocolError):
                audio_segment_start(4,2,3,generation=generation)
    def test_event_compact(self):
        self.assertEqual(event("ready",sample_rate=16000),"{\"type\":\"ready\",\"sample_rate\":16000}")

if __name__=="__main__": unittest.main()
