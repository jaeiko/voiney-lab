import asyncio
import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from websockets.exceptions import ConnectionClosedError

from voice_workflow_agent.native_realtime import (
    MAX_RECONNECT_AUDIO_BYTES,
    NATIVE_SAMPLE_RATE,
    NativeRealtimeConfig,
    NativeRealtimeSession,
    realtime_tool_schemas,
    session_update_payload,
)
from voice_workflow_agent.emergency import KOREAN_EMERGENCY_RESPONSE
from voice_workflow_agent.procedure_definitions import (
    ProcedureDefinition,
    ProcedureStep,
    SourceReference,
)
from voice_workflow_agent.procedure_store import ProcedureStore
from voice_workflow_agent.procedures import ProcedureController as RealProcedureController
from voice_workflow_agent.server import ServerConfig, voice_socket
from voice_workflow_agent.tools import (
    CHECK_REPORT_TOOL_NAME,
    COMPLETE_CURRENT_STEP_TOOL_NAME,
    CREATE_REPORT_TOOL_NAME,
    GET_CURRENT_STEP_TOOL_NAME,
    RECORD_STEP_OBSERVATION_TOOL_NAME,
    START_STEP_TIMER_TOOL_NAME,
    ToolContext,
    execute_tool,
)


class Sender:
    def __init__(self):
        self.events = []
        self.audio = []

    async def text(self, kind, **fields):
        self.events.append({"type": kind, **fields})

    async def native_audio(
        self, turn_id, response_id, item_id, pcm, *, sample_rate
    ):
        self.audio.append(
            {
                "turn_id": turn_id,
                "response_id": response_id,
                "item_id": item_id,
                "pcm": pcm,
                "sample_rate": sample_rate,
            }
        )


class Upstream:
    def __init__(self, events=(), *, hold=False):
        self.events = list(events)
        self.hold = hold
        self.sent = []
        self.closed = []
        self._queue = asyncio.Queue()
        for event in self.events:
            self._queue.put_nowait(event)
        if not hold:
            self._queue.put_nowait(None)

    def __aiter__(self):
        return self

    async def __anext__(self):
        value = await self._queue.get()
        if value is None:
            raise StopAsyncIteration
        return value

    async def send(self, value):
        self.sent.append(value)

    async def close(self, code=1000, reason=""):
        self.closed.append((code, reason))
        if self.hold:
            self.hold = False
            self._queue.put_nowait(None)


class Clock:
    def __init__(self, value=0.0):
        self.value = value

    def __call__(self):
        return self.value


class ProcedureController:
    def __init__(self):
        self.definition = SimpleNamespace(
            steps=(SimpleNamespace(step_id="blue-card"),)
        )
        self.row = {
            "status": "active",
            "current_step_index": 0,
        }
        self.completions = 0

    def _attached(self):
        return self.definition, self.row

    def complete(self, expected_step_id):
        self.completions += 1
        self.row = {"status": "completed", "current_step_index": 1}
        return {
            "status": "success",
            "operation": "complete",
            "idempotent": False,
            "completed_step_id": expected_step_id,
            "completed": True,
            "state": {
                "attached": True,
                "procedure_id": "demo",
                "title": "Fictional demo",
                "version": "1",
                "status": "completed",
                "total_step_count": 1,
                "completed_step_count": 1,
                "current_step_number": None,
                "current_step_id": None,
                "current_step_title": None,
                "approved_current_instruction": None,
            },
        }

    def current(self):
        return {
            "status":"success",
            "operation":"read",
            "state":{
                "attached":True,
                "procedure_id":"demo",
                "title":"Fictional demo",
                "version":"1",
                "status":self.row["status"],
                "total_step_count":1,
                "completed_step_count":self.row["current_step_index"],
                "current_step_number":(
                    1 if self.row["status"]=="active" else None),
                "current_step_id":(
                    "blue-card" if self.row["status"]=="active" else None),
                "current_step_title":(
                    "Blue card" if self.row["status"]=="active" else None),
                "approved_current_instruction":(
                    "Observe the fictional blue card."
                    if self.row["status"]=="active" else None),
                "timer":{
                    "state":"elapsed",
                    "duration_seconds":10,
                    "remaining_seconds":0,
                },
            },
        }


class TwoStepProcedureController:
    def __init__(self):
        self.definition=SimpleNamespace(steps=(
            SimpleNamespace(step_id="blue-card"),
            SimpleNamespace(step_id="fixed-timer"),
        ))
        self.row={"status":"active","current_step_index":0}
        self.completions=0
        self.timer_starts=0

    def _attached(self):
        return self.definition,self.row

    def _state(self):
        index=self.row["current_step_index"]
        current=(
            self.definition.steps[index].step_id
            if index<len(self.definition.steps) else None
        )
        return {
            "attached":True,"procedure_id":"demo","title":"Fictional demo",
            "version":"1","status":self.row["status"],
            "total_step_count":2,"completed_step_count":index,
            "current_step_number":index+1 if current else None,
            "current_step_id":current,
            "current_step_title":"Fixed timer" if index==1 else "Blue card",
            "approved_current_instruction":"Use the fictional current step.",
            "timer":(
                {
                    "state":"not_started","duration_seconds":10,
                    "remaining_seconds":10,
                }
                if index==1 else None
            ),
        }

    def complete(self,expected_step_id):
        current=self._state()["current_step_id"]
        if expected_step_id!=current:
            return {"status":"error","code":"step_mismatch","state":self._state()}
        if current=="fixed-timer":
            return {
                "status":"error","code":"timer_not_started",
                "state":self._state(),
            }
        self.completions+=1
        self.row={"status":"active","current_step_index":1}
        return {
            "status":"success","operation":"complete","idempotent":False,
            "completed_step_id":"blue-card","completed":False,
            "state":self._state(),
        }

    def start_timer(self,expected_step_id):
        if expected_step_id!="fixed-timer":
            return {"status":"error","code":"step_mismatch","state":self._state()}
        self.timer_starts+=1
        state=self._state()
        state["timer"]={
            "state":"running","duration_seconds":10,"remaining_seconds":10,
        }
        return {
            "status":"success","operation":"start_timer","idempotent":False,
            "timer_step_id":"fixed-timer",
            "timer":{"duration_seconds":10},
            "state":state,
        }

    def current(self):
        return {"status":"success","operation":"read","state":self._state()}


def context(controller=None):
    return ToolContext(
        Path("/trusted/catalog.sqlite"),
        "FACILITY",
        "ko",
        "test_only",
        "ko",
        controller,
    )


def real_observation_workflow(directory, *, subjects=None, start=True):
    source=SourceReference("DEMO",1,1)
    schema=(
        {
            "type":"text",
            "required":True,
            "label":"가상 관찰값",
            "utterance_subjects":list(subjects),
        }
        if subjects is not None else None
    )
    definition=ProcedureDefinition(
        1,"native-observation-demo","FICTIONAL NON-OPERATIONAL Demo","1",
        "FACILITY","ko","approved","test_only",True,"demo-doc","1","ko",
        source,
        (
            ProcedureStep(
                "observe",1,"Observe","가상 관찰값을 말해 주세요.",
                "explicit_confirmation",source,observation_schema=schema),
        ),
    )
    store=ProcedureStore(Path(directory)/"procedure.sqlite")
    controller=RealProcedureController({definition.procedure_id:definition},store)
    if start:
        result=controller.start(
            definition.procedure_id,facility_id="FACILITY",
            language="ko",usage_scope="test_only")
        if result.get("status")!="success":
            raise AssertionError("failed to start test workflow")
    return store,controller


def config(**overrides):
    values = {
        "api_key": "test-key",
        "model": "grok-voice-latest",
        "voice": "eve",
        "reconnect_delays": (0.0, 0.0),
    }
    values.update(overrides)
    return NativeRealtimeConfig(**values)


async def prime(session, upstream):
    session._upstream = upstream
    session._ready.set()
    session._started.set()


def response_created(response_id):
    return json.dumps(
        {"type": "response.created", "response": {"id": response_id}}
    )


def audio_delta(response_id, pcm=b"\x01\x00" * 240):
    return json.dumps(
        {
            "type": "response.output_audio.delta",
            "response_id": response_id,
            "delta": base64.b64encode(pcm).decode(),
        }
    )


class NativeConfigurationTests(unittest.TestCase):
    def test_realtime_schema_is_flat_strict_and_uses_correlated_audio(self):
        schemas = realtime_tool_schemas()
        self.assertEqual(len(schemas), 9)
        self.assertTrue(all(item["type"] == "function" for item in schemas))
        self.assertTrue(all("function" not in item for item in schemas))
        self.assertTrue(
            all(
                item["parameters"]["additionalProperties"] is False
                for item in schemas
            )
        )
        payload = session_update_payload(
            config(), context(), language_mode="auto", manual_language=None
        )
        session = payload["session"]
        self.assertEqual(
            session["audio"]["input"],
            {
                "format": {"type": "audio/pcm", "rate": 24000},
                "transport": "binary",
                "transcription": {
                    "model": "grok-transcribe",
                    "keyterms": ["Voice Workflow Agent", "MSDS", "SDS", "안전담당자"],
                },
            },
        )
        self.assertEqual(session["audio"]["output"]["transport"], "json")
        self.assertEqual(session["turn_detection"]["type"], "server_vad")
        self.assertEqual(session["turn_detection"]["threshold"], 0.6)
        self.assertTrue(session["resumption"]["enabled"])

    def test_environment_vad_threshold_is_tunable_and_bounded(self):
        with patch.dict(
            os.environ,
            {"XAI_API_KEY": "test-key", "XAI_REALTIME_VAD_THRESHOLD": "0.45"},
            clear=True,
        ):
            self.assertEqual(
                NativeRealtimeConfig.from_environment().vad_threshold, 0.45
            )
        for value in ("not-a-number", "0.09", "0.91"):
            with self.subTest(value=value), patch.dict(
                os.environ,
                {
                    "XAI_API_KEY": "test-key",
                    "XAI_REALTIME_VAD_THRESHOLD": value,
                },
                clear=True,
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "native voice configuration is invalid"
                ):
                    NativeRealtimeConfig.from_environment()

    def test_connection_url_pins_model_and_scopes_resumption(self):
        value = config().connection_url()
        self.assertIn("model=grok-voice-latest", value)
        self.assertNotIn("conversation_id", value)
        resumed = config().connection_url("conversation-1")
        self.assertIn("conversation_id=conversation-1", resumed)


class NativeLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.sender = Sender()
        self.upstream = Upstream(hold=True)
        self.clock = Clock(10.0)
        self.session = NativeRealtimeSession(
            self.sender, context(), config(), clock=self.clock
        )
        await prime(self.session, self.upstream)

    async def asyncTearDown(self):
        await self.session.stop()

    async def test_preflight_gates_audio_and_transcript_until_final_transcript(self):
        await self.session.handle_upstream_message(
            json.dumps({"type": "input_audio_buffer.speech_started"})
        )
        self.clock.value = 11.0
        await self.session.handle_upstream_message(
            json.dumps({"type": "input_audio_buffer.speech_stopped"})
        )
        await self.session.handle_upstream_message(response_created("r1"))
        await self.session.handle_upstream_message(
            json.dumps(
                {
                    "type": "response.output_item.added",
                    "response_id": "r1",
                    "item": {"id": "i1"},
                }
            )
        )
        await self.session.handle_upstream_message(audio_delta("r1"))
        await self.session.handle_upstream_message(
            json.dumps(
                {
                    "type": "response.output_audio_transcript.delta",
                    "response_id": "r1",
                    "delta": "안전한 답변",
                }
            )
        )
        self.assertEqual(self.sender.audio, [])
        self.assertFalse(
            any(event["type"] == "reply.delta" for event in self.sender.events)
        )
        self.clock.value = 11.2
        await self.session.handle_upstream_message(
            json.dumps(
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": "승인된 정보를 알려 주세요",
                }
            )
        )
        self.assertEqual(len(self.sender.audio), 1)
        self.assertEqual(self.sender.audio[0]["response_id"], "r1")
        self.assertTrue(
            any(event["type"] == "reply.delta" for event in self.sender.events)
        )
        self.clock.value = 12.0
        await self.session.handle_upstream_message(
            json.dumps({"type": "response.done", "response": {"id": "r1"}})
        )
        done = next(
            event for event in self.sender.events if event["type"] == "turn.done"
        )
        self.assertEqual(done["pipeline"], "native")
        self.assertEqual(done["route"], "brain")

    async def test_nonlexical_transcript_cancels_preflight_without_a_turn(self):
        await self.session.handle_upstream_message(
            json.dumps({"type": "input_audio_buffer.speech_started"})
        )
        await self.session.handle_upstream_message(
            json.dumps({"type": "input_audio_buffer.speech_stopped"})
        )
        await self.session.handle_upstream_message(json.dumps({
            "type": "conversation.item.input_audio_transcription.completed",
            "transcript": "[Coughing]",
        }))
        await self.session.handle_upstream_message(response_created("noise-r1"))
        event_types = [event["type"] for event in self.sender.events]
        self.assertIn("speech.rejected", event_types)
        self.assertNotIn("transcript", event_types)
        self.assertNotIn("reply.delta", event_types)
        self.assertNotIn("turn.done", event_types)
        sent = [json.loads(item) for item in self.upstream.sent]
        self.assertEqual(
            sum(item.get("type") == "response.cancel" for item in sent), 1
        )

    async def test_initial_ready_is_distinct_from_later_configuration_updates(self):
        self.session._awaiting_initial_session_update = True
        self.session._connection_is_reconnect = False
        await self.session.handle_upstream_message(
            json.dumps({"type": "session.updated"})
        )
        await self.session.handle_upstream_message(
            json.dumps({"type": "session.updated"})
        )
        ready = [
            event for event in self.sender.events if event["type"] == "native.ready"
        ]
        self.assertEqual(len(ready), 1)
        self.assertFalse(ready[0]["reconnected"])
        self.assertEqual(
            [
                event["type"]
                for event in self.sender.events
                if event["type"] == "native.configuration.updated"
            ],
            ["native.configuration.updated"],
        )

    async def test_audio_delivery_is_acknowledged_once(self):
        await self.session.send_audio(b"\x01\x00" * 2400)
        await self.session.send_audio(b"\x01\x00" * 2400)
        self.assertEqual(self.upstream.sent, [b"\x01\x00" * 2400] * 2)
        started = [
            event
            for event in self.sender.events
            if event["type"] == "native.input.started"
        ]
        self.assertEqual(len(started), 1)
        self.assertEqual(started[0]["sample_rate"], 24_000)

    async def test_audio_send_racing_with_upstream_close_is_buffered(self):
        class ClosingUpstream(Upstream):
            async def send(self, value):
                raise ConnectionClosedError(None, None)

        self.session._upstream = ClosingUpstream(hold=True)
        chunk = b"\x01\x00" * 2400
        await self.session.send_audio(chunk)
        self.assertFalse(self.session.ready)
        self.assertEqual(list(self.session._reconnect_audio), [chunk])

    async def test_first_speech_is_not_mislabeled_as_barge_in(self):
        await self.session.handle_upstream_message(
            json.dumps({"type": "input_audio_buffer.speech_started"})
        )
        self.assertFalse(
            any(
                event["type"] == "native.playback.clear"
                for event in self.sender.events
            )
        )
        state = [
            event
            for event in self.sender.events
            if event["type"] == "native.state"
        ][-1]
        self.assertEqual(state["state"], "LISTENING")

    async def test_barge_in_discards_old_audio_and_truncates_to_played_duration(self):
        self.session.turn_id = 1
        self.session.transcript_finalized = True
        await self.session.handle_upstream_message(response_created("old"))
        await self.session.handle_upstream_message(
            json.dumps(
                {
                    "type": "response.output_item.added",
                    "response_id": "old",
                    "item": {"id": "item-old"},
                }
            )
        )
        await self.session.handle_upstream_message(audio_delta("old"))
        await self.session.handle_upstream_message(json.dumps({
            "type": "response.output_audio_transcript.delta",
            "response_id": "old", "delta": "이미 받은 답변",
        }))
        self.assertEqual(len(self.sender.audio), 1)
        await self.session.handle_upstream_message(
            json.dumps({"type": "input_audio_buffer.speech_started"})
        )
        await self.session.handle_upstream_message(audio_delta("old"))
        self.assertEqual(len(self.sender.audio), 1)
        clear = [
            event
            for event in self.sender.events
            if event["type"] == "native.playback.clear"
        ][-1]
        self.assertEqual(
            (clear["response_id"], clear["item_id"],clear["turn_id"]),
            ("old", "item-old",1),
        )
        self.assertEqual(clear["received_text_chars"], len("이미 받은 답변"))
        old_reply = next(
            event for event in self.sender.events
            if event["type"] == "reply.delta" and event["response_id"] == "old"
        )
        self.assertEqual(old_reply["turn_id"], 1)
        await self.session.truncate_playback("old", "item-old", 9999)
        truncate = json.loads(self.upstream.sent[-1])
        self.assertEqual(truncate["type"], "conversation.item.truncate")
        self.assertLessEqual(truncate["audio_end_ms"], 10)

        await self.session.handle_upstream_message(
            json.dumps(
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": "최신 질문입니다",
                }
            )
        )
        await self.session.handle_upstream_message(response_created("new"))
        await self.session.handle_upstream_message(audio_delta("new"))
        self.assertEqual(len(self.sender.audio), 2)
        self.assertEqual(self.sender.audio[-1]["response_id"], "new")

    async def test_barge_in_clears_audio_after_response_generation_is_done(self):
        self.session.turn_id = 1
        self.session.transcript_finalized = True
        await self.session.handle_upstream_message(response_created("old"))
        await self.session.handle_upstream_message(
            json.dumps(
                {
                    "type": "response.output_item.added",
                    "response_id": "old",
                    "item": {"id": "item-old"},
                }
            )
        )
        await self.session.handle_upstream_message(audio_delta("old"))
        await self.session.handle_upstream_message(
            json.dumps({"type": "response.done", "response": {"id": "old"}})
        )
        self.assertIsNone(self.session.active_response_id)
        self.assertEqual(self.session.playback_response_id, "old")

        await self.session.handle_upstream_message(
            json.dumps({"type": "input_audio_buffer.speech_started"})
        )

        clear = [
            event
            for event in self.sender.events
            if event["type"] == "native.playback.clear"
        ][-1]
        self.assertEqual(clear["reason"], "barge_in")
        self.assertEqual(
            (clear["response_id"], clear["item_id"],clear["turn_id"]),
            ("old", "item-old",1),
        )
        self.assertIn("old", self.session.discarded_response_ids)
        self.assertIsNone(self.session.playback_response_id)

    async def test_playback_ack_prevents_completed_audio_from_false_barge_in(self):
        self.session.turn_id = 1
        self.session.transcript_finalized = True
        await self.session.handle_upstream_message(response_created("done"))
        await self.session.handle_upstream_message(audio_delta("done"))
        await self.session.handle_upstream_message(
            json.dumps({"type": "response.done", "response": {"id": "done"}})
        )
        await self.session.playback_ended("done")
        before = len(
            [
                event
                for event in self.sender.events
                if event["type"] == "native.playback.clear"
            ]
        )
        await self.session.handle_upstream_message(
            json.dumps({"type": "input_audio_buffer.speech_started"})
        )
        after = len(
            [
                event
                for event in self.sender.events
                if event["type"] == "native.playback.clear"
            ]
        )
        self.assertEqual(before, after)

    async def test_final_playback_completion_is_monotonic_once_and_turn_scoped(self):
        await self.session.handle_upstream_message(
            json.dumps({"type":"input_audio_buffer.speech_started"}))
        self.clock.value=11.0
        await self.session.handle_upstream_message(
            json.dumps({"type":"input_audio_buffer.speech_stopped"}))
        await self.session.handle_upstream_message(json.dumps({
            "type":"conversation.item.input_audio_transcription.completed",
            "transcript":"승인된 정보를 알려 주세요",
        }))
        await self.session.handle_upstream_message(response_created("final"))
        self.clock.value=11.2
        await self.session.handle_upstream_message(audio_delta("final"))
        self.clock.value=11.5
        await self.session.handle_upstream_message(json.dumps({
            "type":"response.done","response":{"id":"final"},
        }))
        done=next(
            event for event in self.sender.events
            if event["type"]=="turn.done")
        self.assertEqual(done["timings_ms"]["first_audio_ms"],200)
        self.assertEqual(done["timings_ms"]["total_ms"],500)

        self.clock.value=12.25
        completion=await self.session.playback_ended("final")
        self.assertEqual(completion,(1,1250))
        self.assertIsInstance(completion[1],int)
        self.assertIsNone(await self.session.playback_ended("final"))
        self.assertIsNone(await self.session.playback_ended("previous-turn"))

    async def test_barge_in_and_late_callback_do_not_complete_playback_metric(self):
        await self.session.handle_upstream_message(
            json.dumps({"type":"input_audio_buffer.speech_started"}))
        self.clock.value=11.0
        await self.session.handle_upstream_message(
            json.dumps({"type":"input_audio_buffer.speech_stopped"}))
        await self.session.handle_upstream_message(json.dumps({
            "type":"conversation.item.input_audio_transcription.completed",
            "transcript":"첫 번째 질문",
        }))
        await self.session.handle_upstream_message(response_created("interrupted"))
        await self.session.handle_upstream_message(audio_delta("interrupted"))
        await self.session.handle_upstream_message(json.dumps({
            "type":"response.done","response":{"id":"interrupted"},
        }))
        await self.session.handle_upstream_message(
            json.dumps({"type":"input_audio_buffer.speech_started"}))
        events_before=list(self.sender.events)
        self.assertIsNone(await self.session.playback_ended("interrupted"))
        self.assertEqual(self.sender.events,events_before)
        self.assertIn("interrupted",self.session.discarded_response_ids)

    async def test_late_response_is_cancelled_instead_of_attaching_to_new_turn(self):
        await self.session.handle_upstream_message(
            json.dumps({"type": "input_audio_buffer.speech_started"})
        )
        await self.session.handle_upstream_message(
            json.dumps({"type": "input_audio_buffer.speech_stopped"})
        )
        await self.session.handle_upstream_message(
            json.dumps({"type": "input_audio_buffer.speech_started"})
        )
        await self.session.handle_upstream_message(
            json.dumps({"type": "input_audio_buffer.speech_stopped"})
        )
        await self.session.handle_upstream_message(response_created("late-turn-one"))
        self.assertEqual(
            json.loads(self.upstream.sent[-1]),
            {"type": "response.cancel"},
        )
        self.assertFalse(
            any(
                event["type"] == "native.response.created"
                and event["response_id"] == "late-turn-one"
                for event in self.sender.events
            )
        )
        await self.session.handle_upstream_message(response_created("turn-two"))
        created = [
            event
            for event in self.sender.events
            if event["type"] == "native.response.created"
        ][-1]
        self.assertEqual(
            (created["turn_id"], created["response_id"]),
            (2, "turn-two"),
        )

    async def test_emergency_cancels_preflight_response_and_forces_exact_line(self):
        await self.session.handle_upstream_message(
            json.dumps({"type": "input_audio_buffer.speech_started"})
        )
        await self.session.handle_upstream_message(response_created("unsafe"))
        await self.session.handle_upstream_message(audio_delta("unsafe"))
        self.assertEqual(self.sender.audio, [])
        await self.session.handle_upstream_message(
            json.dumps(
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": "도와줘",
                }
            )
        )
        self.assertEqual(json.loads(self.upstream.sent[-1])["type"], "response.cancel")
        await self.session.handle_upstream_message(
            json.dumps({"type": "response.done", "response": {"id": "unsafe"}})
        )
        forced = json.loads(self.upstream.sent[-1])
        self.assertEqual(forced["type"], "conversation.item.create")
        self.assertEqual(forced["item"]["type"], "force_message")
        self.assertEqual(
            forced["item"]["content"][0]["text"],
            KOREAN_EMERGENCY_RESPONSE,
        )
        self.assertEqual(self.sender.audio, [])

    async def test_tool_is_exactly_once_and_continuation_is_single(self):
        self.session.turn_id = 1
        self.session.transcript_finalized = True
        self.session.latest_transcript = "보고 상태를 확인해 주세요"
        await self.session.handle_upstream_message(response_created("selection"))
        call = json.dumps(
            {
                "type": "response.function_call_arguments.done",
                "response_id": "selection",
                "call_id": "call-1",
                "name": CHECK_REPORT_TOOL_NAME,
                "arguments": '{"report_id":"SR-20260722-A1B2C3"}',
            }
        )
        with patch(
            "voice_workflow_agent.native_realtime.execute_tool",
            return_value={
                "status": "success",
                "report_id": "SR-20260722-A1B2C3",
                "report_status": "queued_for_handoff",
            },
        ) as execute, patch(
            "voice_workflow_agent.native_realtime.asyncio.to_thread",
            side_effect=lambda function, *args: function(*args),
        ):
            await self.session.handle_upstream_message(call)
            await self.session.handle_upstream_message(call)
            await self.session.handle_upstream_message(
                json.dumps(
                    {"type": "response.done", "response": {"id": "selection"}}
                )
            )
            sent_after_result = [
                json.loads(item)
                for item in self.upstream.sent
                if isinstance(item, str)
            ]
            self.assertEqual(
                [item["type"] for item in sent_after_result].count("response.create"),
                1,
            )
            await self.session.playback_ended("selection")
            await self.session.playback_ended("selection")
            self.assertFalse(
                self.session.responses["selection"].playback_completion_recorded)
        execute.assert_called_once()
        clear = next(
            event for event in self.sender.events
            if event["type"] == "native.playback.clear"
        )
        self.assertEqual(clear["reason"],"tool_validation")
        sent = [json.loads(item) for item in self.upstream.sent if isinstance(item, str)]
        self.assertEqual(
            [item["type"] for item in sent].count("conversation.item.create"), 1
        )
        self.assertEqual([item["type"] for item in sent].count("response.create"), 1)

    async def test_procedure_tool_discards_premature_success_audio_and_forces_error(self):
        self.session.turn_id=1
        self.session.transcript_finalized=True
        self.session.latest_transcript="고정 타이머를 시작해 줘"
        await self.session.handle_upstream_message(response_created("timer"))
        await self.session.handle_upstream_message(json.dumps({
            "type":"response.output_item.added",
            "response_id":"timer",
            "item":{"id":"spoken-before-tool"},
        }))
        await self.session.handle_upstream_message(audio_delta("timer"))
        self.assertTrue(self.sender.audio)

        with patch(
            "voice_workflow_agent.native_realtime.execute_tool",
            return_value={
                "status":"error",
                "code":"timer_not_configured",
                "state":{
                    "attached":True,
                    "status":"active",
                    "current_step_number":1,
                    "approved_current_instruction":"라벨을 말해 주세요.",
                },
            },
        ):
            await self.session.handle_upstream_message(json.dumps({
                "type":"response.function_call_arguments.done",
                "response_id":"timer",
                "call_id":"timer-call",
                "name":START_STEP_TIMER_TOOL_NAME,
                "arguments":'{"expected_step_id":"demo-label-check"}',
            }))
            await self.session.handle_upstream_message(json.dumps({
                "type":"response.output_audio_transcript.delta",
                "response_id":"timer",
                "delta":"타이머를 시작했습니다.",
            }))
            await self.session.handle_upstream_message(json.dumps({
                "type":"response.done",
                "response":{"id":"timer"},
            }))

        clear=next(
            event for event in self.sender.events
            if event["type"]=="native.playback.clear")
        self.assertEqual(clear["reason"],"tool_validation")
        self.assertFalse(any(
            event["type"]=="reply.delta"
            and "시작했습니다" in event.get("text","")
            for event in self.sender.events))
        sent=[
            json.loads(item) for item in self.upstream.sent
            if isinstance(item,str)]
        force=next(
            item for item in sent
            if item["type"]=="conversation.item.create"
            and item["item"]["type"]=="force_message")
        self.assertIn(
            "타이머가 없습니다",
            force["item"]["content"][0]["text"])

    async def test_pending_report_accepts_natural_approval_and_blocks_model(self):
        self.session.pending_report={
            "location":"제3 실험실 B 작업대",
            "summary":"가상 표시창이 빨간색임",
            "urgency":"urgent",
            "exposure_status":"no",
            "language":"ko",
        }
        self.session.turn_id=4
        with patch(
            "voice_workflow_agent.native_realtime.execute_tool",
            return_value={
                "status":"success",
                "report_id":"SR-20260726-C1D2E3",
                "report_status":"queued_for_handoff",
            },
        ) as execute:
            handled=await self.session._handle_server_owned_transcript(
                "네, 제출해줘.")
        self.assertTrue(handled)
        execute.assert_called_once()
        self.assertIsNone(self.session.pending_report)

    async def test_tool_execution_failure_is_sanitized_and_connection_survives(self):
        self.session.turn_id = 1
        self.session.transcript_finalized = True
        self.session.latest_transcript = "보고 상태를 확인해 주세요"
        await self.session.handle_upstream_message(response_created("failure"))
        call = json.dumps(
            {
                "type": "response.function_call_arguments.done",
                "response_id": "failure",
                "call_id": "failed-call",
                "name": CHECK_REPORT_TOOL_NAME,
                "arguments": '{"report_id":"SR-20260722-A1B2C3"}',
            }
        )
        with patch(
            "voice_workflow_agent.native_realtime.execute_tool",
            side_effect=RuntimeError("secret /tmp/private.sqlite SQL failure"),
        ), patch(
            "voice_workflow_agent.native_realtime.asyncio.to_thread",
            side_effect=lambda function, *args: function(*args),
        ):
            await self.session.handle_upstream_message(call)
            await self.session.handle_upstream_message(
                json.dumps(
                    {"type": "response.done", "response": {"id": "failure"}}
                )
            )
            await self.session.playback_ended("failure")
        sent = [json.loads(item) for item in self.upstream.sent if isinstance(item, str)]
        output = next(
            item
            for item in sent
            if item["type"] == "conversation.item.create"
            and item["item"]["type"] == "function_call_output"
        )
        self.assertEqual(
            json.loads(output["item"]["output"]),
            {"status": "error", "message": "tool execution failed"},
        )
        self.assertEqual([item["type"] for item in sent].count("response.create"), 1)
        self.assertFalse(
            any(event["type"] == "native.failure" for event in self.sender.events)
        )

    async def test_procedure_completion_uses_final_transcript_authorization(self):
        controller = ProcedureController()
        self.session.tool_context = context(controller)
        self.session.turn_id = 1
        self.session.transcript_finalized = True
        self.session.latest_transcript = "현재 단계로 완료했습니다"
        await self.session.handle_upstream_message(response_created("bad"))
        await self.session.handle_upstream_message(
            json.dumps(
                {
                    "type": "response.function_call_arguments.done",
                    "response_id": "bad",
                    "call_id": "bad-call",
                    "name": COMPLETE_CURRENT_STEP_TOOL_NAME,
                    "arguments": '{"expected_step_id":"blue-card"}',
                }
            )
        )
        await self.session.handle_upstream_message(
            json.dumps({"type": "response.done", "response": {"id": "bad"}})
        )
        self.assertEqual(controller.completions, 0)
        error = next(
            event
            for event in self.sender.events
            if event["type"] == "procedure.error"
        )
        self.assertEqual(error["code"], "explicit_confirmation_required")

        self.session.latest_transcript = "현재 단계를 완료했습니다"
        await self.session.handle_upstream_message(response_created("good"))
        await self.session.handle_upstream_message(
            json.dumps(
                {
                    "type": "response.function_call_arguments.done",
                    "response_id": "good",
                    "call_id": "good-call",
                    "name": COMPLETE_CURRENT_STEP_TOOL_NAME,
                    "arguments": '{"expected_step_id":"blue-card"}',
                }
            )
        )
        await self.session.handle_upstream_message(
            json.dumps({"type": "response.done", "response": {"id": "good"}})
        )
        self.assertEqual(controller.completions, 1)

    async def test_exact_completion_is_server_routed_and_late_tool_is_ignored(self):
        controller=ProcedureController()
        self.session.tool_context=context(controller)
        self.session.turn_id=4
        await self.session.handle_upstream_message(response_created("automatic"))
        await self.session.handle_upstream_message(json.dumps({
            "type":"conversation.item.input_audio_transcription.completed",
            "transcript":"현재 단계를 완료했습니다.",
        }))
        self.assertEqual(controller.completions,1)
        await self.session.handle_upstream_message(json.dumps({
            "type":"response.function_call_arguments.done",
            "response_id":"automatic",
            "call_id":"late-duplicate",
            "name":COMPLETE_CURRENT_STEP_TOOL_NAME,
            "arguments":'{"expected_step_id":"blue-card"}',
        }))
        await self.session.handle_upstream_message(json.dumps({
            "type":"response.done","response":{"id":"automatic"},
        }))
        self.assertEqual(controller.completions,1)
        self.assertEqual(
            [event["type"] for event in self.sender.events].count("tool.call"),1)
        self.assertEqual(
            [event["type"] for event in self.sender.events].count("tool.result"),1)
        self.assertEqual(
            [event["type"] for event in self.sender.events].count(
                "procedure.step_completed"),1)
        self.assertEqual(
            [event["type"] for event in self.sender.events].count(
                "procedure.completed"),1)
        self.assertEqual(
            [event["type"] for event in self.sender.events].count(
                "procedure.state"),1)
        sent=[json.loads(item) for item in self.upstream.sent
              if isinstance(item,str)]
        self.assertEqual(
            [item["type"] for item in sent].count("response.cancel"),1)
        force=[item for item in sent
               if item["type"]=="conversation.item.create"
               and item["item"]["type"]=="force_message"]
        self.assertEqual(len(force),1)

    async def test_replayed_final_transcript_cannot_complete_the_next_step(self):
        controller=TwoStepProcedureController()
        self.session.tool_context=context(controller)
        self.session.turn_id=4
        await self.session.handle_upstream_message(response_created("automatic"))
        completed=json.dumps({
            "type":"conversation.item.input_audio_transcription.completed",
            "transcript":"현재 단계를 완료했습니다.",
        })

        await self.session.handle_upstream_message(completed)
        await self.session.handle_upstream_message(completed)
        await self.session.handle_upstream_message(json.dumps({
            "type":"response.done","response":{"id":"automatic"},
        }))

        self.assertEqual(controller.completions,1)
        self.assertEqual(controller.row["current_step_index"],1)
        results=[
            event for event in self.sender.events
            if event["type"]=="tool.result"
        ]
        self.assertEqual(len(results),1)
        self.assertEqual(results[0]["status"],"success")
        self.assertNotIn("code",results[0])
        self.assertEqual(
            [event["type"] for event in self.sender.events].count(
                "procedure.step_completed"),
            1,
        )
        sent=[json.loads(item) for item in self.upstream.sent
              if isinstance(item,str)]
        force=[
            item for item in sent
            if item["type"]=="conversation.item.create"
            and item["item"]["type"]=="force_message"
        ]
        self.assertEqual(len(force),1)
        self.assertIn(
            "다음 단계로 이동했습니다",
            force[0]["item"]["content"][0]["text"],
        )

    async def test_missing_speech_started_cannot_double_execute_completion(self):
        controller=TwoStepProcedureController()
        self.session.tool_context=context(controller)
        self.session.turn_id=4
        await self.session.handle_upstream_message(json.dumps({
            "type":"input_audio_buffer.speech_stopped",
        }))
        await self.session.handle_upstream_message(response_created("automatic"))
        await self.session.handle_upstream_message(json.dumps({
            "type":"conversation.item.input_audio_transcription.updated",
            "transcript":"현재 단계를 완료했습니다",
        }))
        await self.session.handle_upstream_message(json.dumps({
            "type":"response.function_call_arguments.done",
            "response_id":"automatic",
            "call_id":"model-completion",
            "name":COMPLETE_CURRENT_STEP_TOOL_NAME,
            "arguments":'{"expected_step_id":"blue-card"}',
        }))
        await asyncio.sleep(0)
        self.assertEqual(controller.completions,0)

        await self.session.handle_upstream_message(json.dumps({
            "type":"conversation.item.input_audio_transcription.completed",
            "transcript":"현재 단계를 완료했습니다.",
        }))
        await self.session.handle_upstream_message(json.dumps({
            "type":"response.done","response":{"id":"automatic"},
        }))

        self.assertEqual(controller.completions,1)
        self.assertEqual(controller.row["current_step_index"],1)
        results=[
            event for event in self.sender.events
            if event["type"]=="tool.result"
        ]
        self.assertEqual(len(results),1)
        self.assertEqual(results[0]["status"],"success")
        self.assertNotIn("code",results[0])
        self.assertEqual(
            [event["type"] for event in self.sender.events].count(
                "procedure.step_completed"),
            1,
        )
        sent=[json.loads(item) for item in self.upstream.sent
              if isinstance(item,str)]
        self.assertTrue(any(
            item["type"]=="session.update" for item in sent
        ))

    async def test_exact_timer_start_uses_fresh_server_step_and_fences_model(self):
        controller=TwoStepProcedureController()
        controller.row={"status":"active","current_step_index":1}
        self.session.tool_context=context(controller)
        self.session.turn_id=5
        await self.session.handle_upstream_message(response_created("timer"))
        await self.session.handle_upstream_message(json.dumps({
            "type":"conversation.item.input_audio_transcription.completed",
            "transcript":"고정 타이머를 시작해줘.",
        }))
        await self.session.handle_upstream_message(json.dumps({
            "type":"response.function_call_arguments.done",
            "response_id":"timer",
            "call_id":"late-stale-timer",
            "name":START_STEP_TIMER_TOOL_NAME,
            "arguments":'{"expected_step_id":"blue-card"}',
        }))
        await self.session.handle_upstream_message(json.dumps({
            "type":"response.done","response":{"id":"timer"},
        }))

        self.assertEqual(controller.timer_starts,1)
        results=[
            event for event in self.sender.events
            if event["type"]=="tool.result"
        ]
        self.assertEqual(len(results),1)
        self.assertEqual(results[0]["tool"],START_STEP_TIMER_TOOL_NAME)
        self.assertEqual(results[0]["timer_step_id"],"fixed-timer")
        self.assertEqual(
            [event["type"] for event in self.sender.events].count(
                "procedure.timer_started"),
            1,
        )

    async def test_timer_status_question_reads_fresh_state_without_completion(self):
        controller=ProcedureController()
        self.session.tool_context=context(controller)
        self.session.turn_id=5
        await self.session.handle_upstream_message(response_created("status"))
        await self.session.handle_upstream_message(json.dumps({
            "type":"conversation.item.input_audio_transcription.completed",
            "transcript":"왜 0초인데 안 끝나요?",
        }))
        await self.session.handle_upstream_message(json.dumps({
            "type":"response.done","response":{"id":"status"},
        }))
        self.assertEqual(controller.completions,0)
        result=next(
            event for event in self.sender.events
            if event["type"]=="tool.result")
        self.assertEqual(result["tool"],"get_current_step")
        sent=[json.loads(item) for item in self.upstream.sent
              if isinstance(item,str)]
        force=next(
            item for item in sent
            if item["type"]=="conversation.item.create"
            and item["item"]["type"]=="force_message")
        self.assertIn(
            "현재 단계를 완료했습니다",
            force["item"]["content"][0]["text"])

    async def test_first_final_observation_is_server_owned_replay_safe_and_fenced(self):
        with tempfile.TemporaryDirectory() as directory:
            store,controller=real_observation_workflow(
                directory,
                subjects=(
                    "가상 표시창","가상 표시창 색상","가상 표시창 색깔",
                ),
            )
            try:
                self.session.tool_context=context(controller)
                self.session.turn_id=4
                await self.session.handle_upstream_message(
                    response_created("automatic-observation"))
                completed=json.dumps({
                    "type":"conversation.item.input_audio_transcription.completed",
                    "transcript":"가상 표시창은 빨간색이야.",
                })
                await self.session.handle_upstream_message(completed)
                await self.session.handle_upstream_message(completed)

                for call_id,name,arguments in (
                    ("late-lookup",GET_CURRENT_STEP_TOOL_NAME,"{}"),
                    (
                        "late-observation",
                        RECORD_STEP_OBSERVATION_TOOL_NAME,
                        '{"expected_step_id":"observe","value":"파란색"}',
                    ),
                ):
                    await self.session.handle_upstream_message(json.dumps({
                        "type":"response.function_call_arguments.done",
                        "response_id":"automatic-observation",
                        "call_id":call_id,
                        "name":name,
                        "arguments":arguments,
                    }))
                await self.session.handle_upstream_message(json.dumps({
                    "type":"response.done",
                    "response":{"id":"automatic-observation"},
                }))
                await self.session.handle_upstream_message(
                    response_created("forced-observation"))
                await self.session.handle_upstream_message(json.dumps({
                    "type":"response.done",
                    "response":{"id":"forced-observation"},
                }))

                observations=store.list_observations(
                    controller.attached_session_id,"observe")
                self.assertEqual(
                    [item["value"] for item in observations],["빨간색"])
                self.assertEqual(
                    controller.current()["state"]["current_step_id"],"observe")
                event_types=[event["type"] for event in self.sender.events]
                self.assertEqual(event_types.count("tool.call"),1)
                self.assertEqual(event_types.count("tool.result"),1)
                self.assertEqual(
                    event_types.count("procedure.observation_recorded"),1)
                self.assertEqual(event_types.count("procedure.state"),1)
                result=next(
                    event for event in self.sender.events
                    if event["type"]=="tool.result")
                self.assertEqual(
                    result["tool"],RECORD_STEP_OBSERVATION_TOOL_NAME)
                self.assertEqual(
                    result["observation"]["value"],"빨간색")
                done=next(
                    event for event in self.sender.events
                    if event["type"]=="turn.done")
                self.assertEqual(done["route"],"deterministic_procedure")
            finally:
                store.close()

    async def test_identifier_observation_preserves_every_character(self):
        with tempfile.TemporaryDirectory() as directory:
            store,controller=real_observation_workflow(
                directory,subjects=("가상 라벨",))
            try:
                self.session.tool_context=context(controller)
                self.session.turn_id=5
                await self.session.handle_upstream_message(
                    response_created("identifier-observation"))
                await self.session.handle_upstream_message(json.dumps({
                    "type":"conversation.item.input_audio_transcription.completed",
                    "transcript":"가상 라벨은 A-170이야.",
                }))
                observations=store.list_observations(
                    controller.attached_session_id,"observe")
                self.assertEqual(
                    [item["value"] for item in observations],["A-170"])
                result=next(
                    event for event in self.sender.events
                    if event["type"]=="tool.result")
                self.assertEqual(result["observation"]["value"],"A-170")
            finally:
                store.close()

    async def test_observation_route_rejects_unsafe_states_and_utterances(self):
        rejected=(
            "가상 표시창은 빨간색이야?",
            "가상 표시창은 빨간색인 것 같아.",
            "오늘 본 색은 빨간색이야.",
            "가상 표시창은 빨간색이야. 보고서를 만들어 주세요.",
        )
        for transcript in rejected:
            with self.subTest(transcript=transcript), \
                    tempfile.TemporaryDirectory() as directory:
                sender=Sender()
                upstream=Upstream(hold=True)
                store,controller=real_observation_workflow(
                    directory,subjects=("가상 표시창",))
                try:
                    session=NativeRealtimeSession(
                        sender,context(controller),config(),clock=self.clock)
                    await prime(session,upstream)
                    session.turn_id=1
                    await session.handle_upstream_message(json.dumps({
                        "type":"conversation.item.input_audio_transcription.completed",
                        "transcript":transcript,
                    }))
                    self.assertEqual(
                        store.list_observations(
                            controller.attached_session_id,"observe"),[])
                    self.assertFalse(any(
                        event["type"]=="tool.call"
                        and event.get("tool")==RECORD_STEP_OBSERVATION_TOOL_NAME
                        for event in sender.events))
                finally:
                    store.close()

        for state in ("no_active","no_schema","blocked","pending_report"):
            with self.subTest(state=state), \
                    tempfile.TemporaryDirectory() as directory:
                sender=Sender()
                upstream=Upstream(hold=True)
                store,controller=real_observation_workflow(
                    directory,
                    subjects=None if state=="no_schema"
                    else ("가상 표시창",),
                    start=state!="no_active",
                )
                try:
                    if state=="blocked":
                        controller.block_for_handoff(
                            "SR-20260728-A1B2C3","fictional handoff")
                    session=NativeRealtimeSession(
                        sender,context(controller),config(),clock=self.clock)
                    await prime(session,upstream)
                    session.turn_id=1
                    if state=="pending_report":
                        session.pending_report={
                            "location":"F","summary":"fictional",
                            "urgency":"routine","exposure_status":"no",
                            "language":"ko",
                        }
                    await session.handle_upstream_message(json.dumps({
                        "type":"conversation.item.input_audio_transcription.completed",
                        "transcript":"가상 표시창은 빨간색이야.",
                    }))
                    observations=(
                        store.list_observations(
                            controller.attached_session_id,"observe")
                        if controller.attached_session_id else []
                    )
                    self.assertEqual(observations,[])
                    self.assertFalse(any(
                        event["type"]=="tool.call"
                        and event.get("tool")==RECORD_STEP_OBSERVATION_TOOL_NAME
                        for event in sender.events))
                finally:
                    store.close()

    async def _assert_realtime_draft_approval(self, transcript):
        with tempfile.TemporaryDirectory() as directory:
            store,controller=real_observation_workflow(
                directory,subjects=("가상 표시창",))
            sender=Sender()
            upstream=Upstream(hold=True)
            session=NativeRealtimeSession(
                sender,context(controller),config(),clock=self.clock)
            await prime(session,upstream)
            try:
                await session.handle_upstream_message(json.dumps({
                    "type":"input_audio_buffer.speech_started",
                }))
                await session.handle_upstream_message(json.dumps({
                    "type":"input_audio_buffer.speech_stopped",
                }))
                await session.handle_upstream_message(
                    response_created("draft-selection"))
                await session.handle_upstream_message(json.dumps({
                    "type":(
                        "conversation.item."
                        "input_audio_transcription.completed"
                    ),
                    "transcript":(
                        "제3 실험실 B 작업대의 가상 표시창 이상을 "
                        "보고해 주세요"
                    ),
                }))
                await session.handle_upstream_message(json.dumps({
                    "type":"response.function_call_arguments.done",
                    "response_id":"draft-selection",
                    "call_id":"draft-call",
                    "name":CREATE_REPORT_TOOL_NAME,
                    "arguments":json.dumps({
                        "location":"제3 실험실 B 작업대",
                        "summary":"가상 표시창이 빨간색임",
                        "urgency":"urgent",
                        "exposure_status":"no",
                    },ensure_ascii=False),
                }))
                await session.handle_upstream_message(json.dumps({
                    "type":"response.done",
                    "response":{"id":"draft-selection"},
                }))
                draft=session.pending_report
                self.assertIsNotNone(draft)
                awaiting=next(
                    event for event in sender.events
                    if event["type"]=="tool.result"
                    and event.get("status")=="awaiting_user_confirmation")
                self.assertIs(awaiting["report"],draft)

                await session.handle_upstream_message(
                    response_created("draft-confirmation"))
                await session.handle_upstream_message(json.dumps({
                    "type":"response.done",
                    "response":{"id":"draft-confirmation"},
                }))
                await session.handle_upstream_message(json.dumps({
                    "type":"input_audio_buffer.speech_started",
                }))
                self.assertIs(session.pending_report,draft)
                await session.handle_upstream_message(json.dumps({
                    "type":"input_audio_buffer.speech_stopped",
                }))
                await session.handle_upstream_message(
                    response_created("approval-automatic"))
                approval_event_index=len(sender.events)

                report_result={
                    "status":"success",
                    "report_id":"SR-20260728-C1D2E3",
                    "report_status":"queued_for_handoff",
                }
                with patch(
                    "voice_workflow_agent.tools.create_safety_report",
                    return_value=report_result,
                ) as create, patch(
                    "voice_workflow_agent.native_realtime.execute_tool",
                    wraps=execute_tool,
                ) as dispatch:
                    completed=json.dumps({
                        "type":(
                            "conversation.item."
                            "input_audio_transcription.completed"
                        ),
                        "transcript":transcript,
                    })
                    await session.handle_upstream_message(completed)
                    await session.handle_upstream_message(completed)
                    await session.handle_upstream_message(json.dumps({
                        "type":"response.function_call_arguments.done",
                        "response_id":"approval-automatic",
                        "call_id":"late-report-duplicate",
                        "name":CREATE_REPORT_TOOL_NAME,
                        "arguments":json.dumps(draft,ensure_ascii=False),
                    }))
                dispatch.assert_called_once()
                self.assertIs(dispatch.call_args.args[1],draft)
                create.assert_called_once()
                workflow=create.call_args.kwargs["workflow_context"]
                self.assertEqual(workflow["procedure_id"],"native-observation-demo")
                self.assertEqual(workflow["step_id"],"observe")

                await session.handle_upstream_message(json.dumps({
                    "type":"response.done",
                    "response":{"id":"approval-automatic"},
                }))
                await session.handle_upstream_message(
                    response_created("approval-forced"))
                await session.handle_upstream_message(json.dumps({
                    "type":"response.done",
                    "response":{"id":"approval-forced"},
                }))

                self.assertIsNone(session.pending_report)
                approval_events=sender.events[approval_event_index:]
                event_types=[event["type"] for event in approval_events]
                self.assertEqual(event_types.count("tool.call"),1)
                self.assertEqual(event_types.count("tool.result"),1)
                self.assertEqual(
                    event_types.count("procedure.blocked_for_handoff"),1)
                self.assertEqual(event_types.count("procedure.state"),1)
                confirmed=next(
                    event for event in approval_events
                    if event["type"]=="tool.result")
                self.assertEqual(confirmed["status"],"confirmed")
                self.assertEqual(
                    confirmed["report_id"],"SR-20260728-C1D2E3")
                self.assertTrue(confirmed["procedure_blocked"])
                self.assertEqual(
                    confirmed["procedure_state"]["status"],
                    "blocked_for_handoff",
                )
                self.assertEqual(
                    controller.current()["state"]["status"],
                    "blocked_for_handoff",
                )
                done=next(
                    event for event in approval_events
                    if event["type"]=="turn.done")
                self.assertEqual(done["route"],"deterministic_report")
            finally:
                store.close()

    async def test_realtime_draft_approves_ne_submit_without_model(self):
        await self._assert_realtime_draft_approval("네, 제출해줘.")

    async def test_realtime_draft_approves_spaced_submit_without_model(self):
        await self._assert_realtime_draft_approval("네, 제출해 줘.")

    async def test_realtime_draft_approves_report_submit_without_model(self):
        await self._assert_realtime_draft_approval("보고서를 제출해 주세요.")

    async def test_realtime_draft_approves_safe_unicode_variants(self):
        await self._assert_realtime_draft_approval("네, 제출해\u200b줘．")

    async def test_pending_report_approval_is_server_owned_and_exactly_once(self):
        self.session.pending_report = {
            "location": "F",
            "summary": "fictional",
            "urgency": "routine",
            "exposure_status": "no",
            "language": "ko",
        }
        self.session.turn_id = 2
        self.session.transcript_finalized = False
        with patch(
            "voice_workflow_agent.native_realtime.execute_tool",
            return_value={
                "status": "success",
                "report_id": "SR-20260726-A1B2C3",
                "report_status": "queued_for_handoff",
            },
        ) as execute, patch(
            "voice_workflow_agent.native_realtime.asyncio.to_thread"
        ) as to_thread:
            await self.session.handle_upstream_message(
                json.dumps(
                    {
                        "type": "conversation.item.input_audio_transcription.completed",
                        "transcript": "보고서를 제출해 주세요",
                    }
                )
            )
            await self.session.handle_upstream_message(response_created("automatic"))
            await self.session.handle_upstream_message(
                json.dumps(
                    {"type": "response.done", "response": {"id": "automatic"}}
                )
            )
        execute.assert_called_once()
        to_thread.assert_not_called()
        self.assertIsNone(self.session.pending_report)
        sent = [json.loads(item) for item in self.upstream.sent if isinstance(item, str)]
        self.assertEqual(
            [item["type"] for item in sent].count("response.cancel"), 1
        )
        force = [
            item
            for item in sent
            if item["type"] == "conversation.item.create"
            and item["item"]["type"] == "force_message"
        ][-1]
        self.assertIn("SR-20260726-A1B2C3", force["item"]["content"][0]["text"])

    async def test_pending_report_submission_force_response_is_not_cancelled(self):
        self.session.pending_report = {
            "location": "F",
            "summary": "fictional",
            "urgency": "routine",
            "exposure_status": "no",
            "language": "ko",
        }
        self.session.turn_id = 2
        self.session.transcript_finalized = False
        with patch(
            "voice_workflow_agent.native_realtime.execute_tool",
            return_value={
                "status": "success",
                "report_id": "SR-20260726-A1B2C3",
                "report_status": "queued_for_handoff",
            },
        ):
            await self.session.handle_upstream_message(
                json.dumps(
                    {
                        "type": "conversation.item.input_audio_transcription.completed",
                        "transcript": "보고서를 제출해 주세요",
                    }
                )
            )
            await self.session.handle_upstream_message(response_created("automatic"))
            await self.session.handle_upstream_message(
                json.dumps(
                    {"type": "response.done", "response": {"id": "automatic"}}
                )
            )
            await self.session.handle_upstream_message(response_created("forced"))
            await self.session.handle_upstream_message(
                json.dumps(
                    {
                        "type": "response.output_audio_transcript.delta",
                        "response_id": "forced",
                        "delta": "보고서가 제출되었습니다.",
                    }
                )
            )
            await self.session.handle_upstream_message(
                json.dumps({"type": "response.done", "response": {"id": "forced"}})
            )

        sent = [json.loads(item) for item in self.upstream.sent if isinstance(item, str)]
        self.assertEqual(
            [item["type"] for item in sent].count("response.cancel"),
            1,
        )
        created = [
            event
            for event in self.sender.events
            if event["type"] == "native.response.created"
        ]
        self.assertEqual(created[-1]["response_id"], "forced")
        self.assertTrue(
            any(
                event["type"] == "reply.delta"
                and "제출되었습니다" in event.get("text", "")
                for event in self.sender.events
            )
        )
        done = [
            event for event in self.sender.events if event["type"] == "turn.done"
        ][-1]
        self.assertEqual(done["route"], "deterministic_report")

    async def test_recent_report_status_precedes_stale_draft_and_is_spoken(self):
        self.session.latest_report_id = "SR-20260726-A1B2C3"
        self.session.pending_report = {
            "location": "stale draft",
            "summary": "must not intercept status",
            "urgency": "routine",
            "exposure_status": "no",
            "language": "ko",
        }
        await self.session.handle_upstream_message(
            json.dumps({"type": "input_audio_buffer.speech_started"})
        )
        await self.session.handle_upstream_message(
            json.dumps({"type": "input_audio_buffer.speech_stopped"})
        )
        await self.session.handle_upstream_message(response_created("automatic"))

        result = {
            "status": "success",
            "report_id": "SR-20260726-A1B2C3",
            "report_status": "handoff_ready",
            "attempts": 1,
        }
        with patch(
            "voice_workflow_agent.native_realtime.asyncio.to_thread",
            return_value=result,
        ) as to_thread:
            await self.session.handle_upstream_message(
                json.dumps(
                    {
                        "type": "conversation.item.input_audio_transcription.completed",
                        "transcript": "방금 보고서의 관리자 임계 상태를 확인해 줘.",
                    }
                )
            )
        await self.session.handle_upstream_message(
            json.dumps({"type": "response.done", "response": {"id": "automatic"}})
        )
        await self.session.handle_upstream_message(response_created("status-forced"))
        await self.session.handle_upstream_message(
            json.dumps(
                {
                    "type": "response.output_audio_transcript.delta",
                    "response_id": "status-forced",
                    "delta": "관리자 인계문 준비가 완료되었습니다.",
                }
            )
        )
        await self.session.handle_upstream_message(
            json.dumps(
                {"type": "response.done", "response": {"id": "status-forced"}}
            )
        )

        to_thread.assert_awaited_once()
        args = to_thread.await_args.args
        self.assertEqual(args[1], CHECK_REPORT_TOOL_NAME)
        self.assertEqual(args[2], {"report_id": "SR-20260726-A1B2C3"})
        tool_result = [
            event
            for event in self.sender.events
            if event["type"] == "tool.result"
        ][-1]
        self.assertEqual(tool_result["tool"], CHECK_REPORT_TOOL_NAME)
        self.assertEqual(tool_result["report_status"], "handoff_ready")
        self.assertIsNotNone(self.session.pending_report)
        self.assertTrue(
            any(
                event["type"] == "reply.delta"
                and "준비가 완료" in event.get("text", "")
                for event in self.sender.events
            )
        )
        done = [
            event for event in self.sender.events if event["type"] == "turn.done"
        ][-1]
        self.assertEqual(done["route"], "deterministic_report")

    async def test_pending_report_approval_emits_linked_workflow_block(self):
        self.session.pending_report = {
            "location":"F","summary":"fictional red display",
            "urgency":"urgent","exposure_status":"unknown","language":"ko",
        }
        self.session.turn_id=3
        blocked_state={
            "attached":True,"procedure_id":"demo","title":"Fictional demo",
            "version":"1","status":"blocked_for_handoff",
            "total_step_count":1,"completed_step_count":0,
            "current_step_number":1,"current_step_id":"observe",
            "current_step_title":"Observe",
            "approved_current_instruction":"Observe the fictional display.",
            "handoff":{
                "report_id":"SR-20260726-B1C2D3",
                "blocked_step_id":"observe",
            },
        }
        with patch(
            "voice_workflow_agent.native_realtime.execute_tool",
            return_value={
                "status":"success","report_id":"SR-20260726-B1C2D3",
                "report_status":"queued_for_handoff",
                "procedure_state":blocked_state,
                "procedure_blocked":True,
            },
        ):
            await self.session.handle_upstream_message(json.dumps({
                "type":"conversation.item.input_audio_transcription.completed",
                "transcript":"보고서를 제출해 주세요",
            }))

        event_types=[event["type"] for event in self.sender.events]
        self.assertIn("procedure.blocked_for_handoff",event_types)
        self.assertIn("procedure.state",event_types)
        result=next(
            event for event in self.sender.events
            if event["type"]=="tool.result")
        self.assertTrue(result["procedure_blocked"])
        self.assertEqual(
            result["procedure_state"]["status"],"blocked_for_handoff")

    async def test_reconnect_audio_buffer_is_bounded(self):
        self.session._ready.clear()
        self.session._upstream = None
        chunk = b"\x00\x00" * 24_000
        await self.session.send_audio(chunk)
        await self.session.send_audio(chunk)
        await self.session.send_audio(chunk)
        self.assertLessEqual(
            self.session._reconnect_audio_bytes, MAX_RECONNECT_AUDIO_BYTES
        )
        self.assertTrue(
            any(
                event["type"] == "native.input.dropped"
                for event in self.sender.events
            )
        )


class NativeReconnectTests(unittest.IsolatedAsyncioTestCase):
    async def test_ten_normal_turns_and_five_barge_ins_share_one_epoch(self):
        sender = Sender()
        upstream = Upstream((
            json.dumps({
                "type": "conversation.created",
                "conversation": {"id": "conversation-persistent"},
            }),
            json.dumps({"type": "session.updated"}),
        ), hold=True)
        connections = 0
        async def connector(_url, _headers):
            nonlocal connections
            connections += 1
            return upstream
        session = NativeRealtimeSession(
            sender, context(), config(), connector=connector,
            application_session_id="session-persistent-test",
        )
        await session.start()
        try:
            for index in range(10):
                await session.handle_upstream_message(json.dumps({
                    "type": "input_audio_buffer.speech_started",
                }))
                await session.handle_upstream_message(json.dumps({
                    "type": "input_audio_buffer.speech_stopped",
                }))
                await session.handle_upstream_message(json.dumps({
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": f"일반 질문 {index + 1}입니다",
                }))
                response_id = f"normal-{index}"
                await session.handle_upstream_message(response_created(response_id))
                await session.handle_upstream_message(json.dumps({
                    "type": "response.done", "response": {"id": response_id},
                }))
            for index in range(5):
                await session.handle_upstream_message(json.dumps({
                    "type": "input_audio_buffer.speech_started",
                }))
                await session.handle_upstream_message(json.dumps({
                    "type": "input_audio_buffer.speech_stopped",
                }))
                await session.handle_upstream_message(json.dumps({
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": "길게 설명해 주세요",
                }))
                interrupted_id = f"interrupted-{index}"
                await session.handle_upstream_message(response_created(interrupted_id))
                await session.handle_upstream_message(json.dumps({
                    "type": "response.output_item.added",
                    "response_id": interrupted_id, "item": {"id": f"item-{index}"},
                }))
                await session.handle_upstream_message(audio_delta(interrupted_id))
                await session.handle_upstream_message(json.dumps({
                    "type": "input_audio_buffer.speech_started",
                }))
                await session.handle_upstream_message(json.dumps({
                    "type": "input_audio_buffer.speech_stopped",
                }))
                await session.handle_upstream_message(json.dumps({
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": "새 질문입니다",
                }))
                next_id = f"after-barge-{index}"
                await session.handle_upstream_message(response_created(next_id))
                await session.handle_upstream_message(json.dumps({
                    "type": "response.done", "response": {"id": next_id},
                }))
            self.assertEqual(connections, 1)
            self.assertEqual(session.connection_epoch, 1)
            connected = next(
                event for event in sender.events
                if event["type"] == "native.connection.state"
                and event["state"] == "connected"
            )
            self.assertEqual(len(connected["application_session_ref"]), 12)
            self.assertNotIn("session-persistent-test", str(connected))
            self.assertEqual(sum(
                event["type"] == "native.playback.clear"
                for event in sender.events
            ), 5)
            self.assertEqual(sum(
                event["type"] == "native.response.done"
                and not event.get("awaiting_tool_continuation")
                for event in sender.events
            ), 15)
            self.assertFalse(any(
                event["type"] == "native.connection.state"
                and event.get("state") == "reconnect_scheduled"
                for event in sender.events
            ))
        finally:
            await session.stop()

    async def test_unintended_disconnect_resumes_and_stop_cancels_reconnect(self):
        sender = Sender()
        first = Upstream(
            (
                json.dumps(
                    {
                        "type": "conversation.created",
                        "conversation": {"id": "conversation-1"},
                    }
                ),
                json.dumps({"type": "session.updated"}),
            )
        )
        second = Upstream(
            (
                json.dumps(
                    {
                        "type": "conversation.created",
                        "conversation": {"id": "conversation-1"},
                    }
                ),
                json.dumps({"type": "session.updated"}),
            ),
            hold=True,
        )
        connections = []

        async def connector(url, headers):
            connections.append((url, headers))
            return first if len(connections) == 1 else second

        async def no_sleep(_):
            await asyncio.sleep(0)

        session = NativeRealtimeSession(
            sender,
            context(),
            config(),
            connector=connector,
            sleep=no_sleep,
        )
        await session.start()
        for _ in range(20):
            if len(connections) >= 2 and any(
                event["type"] == "native.ready" and event["reconnected"]
                for event in sender.events
            ):
                break
            await asyncio.sleep(0)
        self.assertEqual(len(connections), 2)
        self.assertNotIn("conversation_id", connections[0][0])
        self.assertIn("conversation_id=conversation-1", connections[1][0])
        self.assertTrue(
            any(
                event["type"] == "native.state"
                and event["state"] == "RECONNECTING"
                for event in sender.events
            )
        )
        connection_events = [
            event for event in sender.events
            if event["type"] == "native.connection.state"
        ]
        self.assertEqual(
            [event["epoch_id"] for event in connection_events
             if event["state"] == "connected"],
            [1, 2],
        )
        self.assertEqual(
            next(event for event in connection_events
                 if event["state"] == "closed")["close_initiator"],
            "provider_stream_end",
        )
        await session.stop()
        self.assertIsNone(session.conversation_id)
        self.assertTrue(second.closed)

    async def test_watchdog_ignores_idle_and_completed_turn_but_closes_stalled_turn(self):
        sender = Sender()
        upstream = Upstream(hold=True)
        clock = Clock(0.0)
        session = NativeRealtimeSession(
            sender,
            context(),
            config(response_timeout_seconds=1.0),
            clock=clock,
        )
        session._upstream = upstream
        session._ready.set()

        calls = 0

        async def advancing_sleep(_):
            nonlocal calls
            calls += 1
            clock.value += 1.1
            if calls == 1:
                self.assertEqual(upstream.closed, [])
                session.turn_id = 1
                session._response_wait_started_at = clock()
                session.speech_stopped_at = clock()
                await session.handle_upstream_message(response_created("started"))
                await session.handle_upstream_message(
                    json.dumps(
                        {"type": "response.done", "response": {"id": "started"}}
                    )
                )
            elif calls == 2:
                self.assertEqual(upstream.closed, [])
                session.turn_id = 2
                session._response_wait_started_at = clock()
            await asyncio.sleep(0)

        session.sleep = advancing_sleep
        task = asyncio.create_task(session._watchdog_loop())
        for _ in range(10):
            if upstream.closed:
                break
            await asyncio.sleep(0)
        session.stop_requested = True
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(upstream.closed[0][0], 1011)


class NativeServerRoutingTests(unittest.TestCase):
    def test_real_websocket_routes_native_audio_controls_and_stop(self):
        class WebSocket:
            def __init__(self):
                self.sent = []
                self.messages = iter(
                    (
                        {
                            "text": (
                                '{"type":"session.start","mode":"native",'
                                '"language":"ko","protocol_id":null,'
                                '"configuration_id":1}'
                            )
                        },
                        {"bytes": b"\x00\x00"},
                        {
                            "text": json.dumps(
                                {
                                    "type": "native.playback.truncate",
                                    "response_id": "r1",
                                    "item_id": "i1",
                                    "audio_end_ms": 10,
                                }
                            )
                        },
                        {
                            "text": json.dumps(
                                {
                                    "type": "native.playback.ended",
                                    "response_id": "r1",
                                }
                            )
                        },
                        {"text": '{"type":"session.reset"}'},
                        {"text": '{"type":"session.stop"}'},
                        {"type": "websocket.disconnect"},
                    )
                )

            async def accept(self):
                pass

            async def send_text(self, value):
                self.sent.append(json.loads(value))

            async def send_bytes(self, value):
                pass

            async def receive(self):
                return next(self.messages)

        instances = []

        class Native:
            def __init__(self, sender, tool_context, native_config, **kwargs):
                self.audio = []
                self.truncations = []
                self.playback_endings = []
                self.started = 0
                self.stopped = 0
                self.language_mode = kwargs["language_mode"]
                instances.append(self)

            async def start(self):
                self.started += 1

            async def stop(self):
                self.stopped += 1

            async def send_audio(self, value):
                self.audio.append(value)

            async def truncate_playback(self, response_id, item_id, audio_end_ms):
                self.truncations.append((response_id, item_id, audio_end_ms))

            async def playback_ended(self, response_id):
                self.playback_endings.append(response_id)

            async def update_language(self, *args, **kwargs):
                pass

        server_config = ServerConfig(
            Path("/trusted/catalog.sqlite"),
            "FACILITY",
            "test_only",
            frozenset({"ko"}),
            "ko",
        )
        socket = WebSocket()
        with patch(
            "voice_workflow_agent.server.server_config", return_value=server_config
        ), patch(
            "voice_workflow_agent.server.NativeRealtimeConfig.from_environment",
            return_value=config(),
        ), patch(
            "voice_workflow_agent.server.NativeRealtimeSession", Native
        ):
            asyncio.run(voice_socket(socket))
        self.assertEqual(len(instances), 2)
        self.assertEqual(instances[0].started, 1)
        self.assertEqual(instances[0].language_mode, "manual")
        self.assertEqual(instances[0].audio, [b"\x00\x00"])
        self.assertEqual(instances[0].truncations, [("r1", "i1", 10)])
        self.assertEqual(instances[0].playback_endings, ["r1"])
        self.assertGreaterEqual(instances[0].stopped, 1)
        self.assertEqual(instances[1].started, 1)
        self.assertGreaterEqual(instances[1].stopped, 1)
        started = next(
            event for event in socket.sent if event["type"] == "session.started"
        )
        self.assertEqual(started["pipeline"], "native")


if __name__ == "__main__":
    unittest.main()
