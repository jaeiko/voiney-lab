import asyncio
import base64
import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from websockets.exceptions import ConnectionClosedError

from safebridge_voice.native_realtime import (
    MAX_RECONNECT_AUDIO_BYTES,
    NATIVE_SAMPLE_RATE,
    NativeRealtimeConfig,
    NativeRealtimeSession,
    realtime_tool_schemas,
    session_update_payload,
)
from safebridge_voice.emergency import KOREAN_EMERGENCY_RESPONSE
from safebridge_voice.server import ServerConfig, voice_socket
from safebridge_voice.tools import (
    CHECK_REPORT_TOOL_NAME,
    COMPLETE_CURRENT_STEP_TOOL_NAME,
    CREATE_REPORT_TOOL_NAME,
    ToolContext,
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


def context(controller=None):
    return ToolContext(
        Path("/trusted/catalog.sqlite"),
        "FACILITY",
        "ko",
        "test_only",
        "ko",
        controller,
    )


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
        self.assertEqual(len(schemas), 6)
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
                    "keyterms": ["SafeBridge", "MSDS", "SDS", "안전담당자"],
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
        self.session.stop_requested = True

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
            (clear["response_id"], clear["item_id"]), ("old", "item-old")
        )
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
            (clear["response_id"], clear["item_id"]), ("old", "item-old")
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
            "safebridge_voice.native_realtime.execute_tool",
            return_value={
                "status": "success",
                "report_id": "SR-20260722-A1B2C3",
                "report_status": "queued_for_handoff",
            },
        ) as execute:
            await self.session.handle_upstream_message(call)
            await self.session.handle_upstream_message(call)
            await self.session.handle_upstream_message(
                json.dumps(
                    {"type": "response.done", "response": {"id": "selection"}}
                )
            )
            sent_before_playback = [
                json.loads(item)
                for item in self.upstream.sent
                if isinstance(item, str)
            ]
            self.assertNotIn(
                "response.create",
                [item["type"] for item in sent_before_playback],
            )
            await self.session.playback_ended("selection")
            await self.session.playback_ended("selection")
        execute.assert_called_once()
        sent = [json.loads(item) for item in self.upstream.sent if isinstance(item, str)]
        self.assertEqual(
            [item["type"] for item in sent].count("conversation.item.create"), 1
        )
        self.assertEqual([item["type"] for item in sent].count("response.create"), 1)

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
            "safebridge_voice.native_realtime.execute_tool",
            side_effect=RuntimeError("secret /tmp/private.sqlite SQL failure"),
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
            "safebridge_voice.native_realtime.execute_tool",
            return_value={
                "status": "success",
                "report_id": "SR-20260726-A1B2C3",
                "report_status": "queued_for_handoff",
            },
        ) as execute:
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
                                '{"type":"session.start","pipeline":"native"}'
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
            "safebridge_voice.server.server_config", return_value=server_config
        ), patch(
            "safebridge_voice.server.NativeRealtimeConfig.from_environment",
            return_value=config(),
        ), patch(
            "safebridge_voice.server.NativeRealtimeSession", Native
        ):
            asyncio.run(voice_socket(socket))
        self.assertEqual(len(instances), 2)
        self.assertEqual(instances[0].started, 1)
        self.assertEqual(instances[0].language_mode, "auto")
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
