import os
import unittest
from unittest.mock import patch

from voice_workflow_agent import server
from voice_workflow_agent.configuration import (
    CascadeVadSettings,
    ConfigurationError,
    NativeVadSettings,
    VoiceVadSettings,
    milliseconds_to_frames,
)
from voice_workflow_agent.native_realtime import (
    NativeRealtimeConfig,
    session_update_payload,
)
from voice_workflow_agent.tools import ToolContext
from voice_workflow_agent.vad import VadConfig
from pathlib import Path


class VadConfigurationTests(unittest.TestCase):
    def test_defaults_preserve_existing_cascade_and_native_behavior(self):
        settings=VoiceVadSettings.from_environment({})
        self.assertEqual(settings.cascade,CascadeVadSettings())
        self.assertEqual(settings.native,NativeVadSettings())
        self.assertEqual(
            VadConfig.from_settings(settings.cascade),
            VadConfig())
        self.assertEqual(
            (settings.cascade.playback_onset_voiced_frames,
             settings.cascade.playback_onset_window_frames),
            (12,15),
        )
        self.assertEqual(
            (settings.cascade.listening_onset_voiced_frames,
             settings.cascade.listening_onset_window_frames,
             settings.cascade.listening_resume_voiced_frames,
             settings.cascade.listening_resume_window_frames),
            (8,12,6,10),
        )

    def test_every_cascade_and_native_setting_can_be_overridden(self):
        environment={
            "CASCADE_VAD_MODE":"2",
            "CASCADE_VAD_ONSET_VOICED_FRAMES":"5",
            "CASCADE_VAD_ONSET_WINDOW_FRAMES":"8",
            "CASCADE_VAD_PREFIX_MS":"321",
            "CASCADE_VAD_ENDPOINT_SILENCE_MS":"777",
            "CASCADE_VAD_MIN_SPEECH_MS":"281",
            "CASCADE_VAD_MAX_UTTERANCE_MS":"16001",
            "CASCADE_VAD_COOLDOWN_MS":"450",
            "CASCADE_VAD_PLAYBACK_ONSET_VOICED_FRAMES":"11",
            "CASCADE_VAD_PLAYBACK_ONSET_WINDOW_FRAMES":"14",
            "CASCADE_VAD_LISTENING_ONSET_VOICED_FRAMES":"9",
            "CASCADE_VAD_LISTENING_ONSET_WINDOW_FRAMES":"13",
            "CASCADE_VAD_LISTENING_RESUME_VOICED_FRAMES":"7",
            "CASCADE_VAD_LISTENING_RESUME_WINDOW_FRAMES":"11",
            "XAI_REALTIME_VAD_THRESHOLD":"0.45",
            "NATIVE_VAD_PREFIX_PADDING_MS":"444",
            "XAI_REALTIME_SILENCE_DURATION_MS":"1200",
        }
        settings=VoiceVadSettings.from_environment(environment)
        self.assertEqual(
            settings.cascade,
            CascadeVadSettings(
                2,5,8,321,777,281,16001,450,11,14,9,13,7,11))
        self.assertEqual(
            settings.native,
            NativeVadSettings(0.45,444,1200))
        config=VadConfig.from_settings(settings.cascade)
        self.assertEqual(
            (
                config.prefix_frames,
                config.endpoint_silence_frames,
                config.minimum_voiced_frames,
                config.maximum_utterance_frames,
            ),
            (17,39,15,801),
        )
        self.assertEqual(
            (config.playback_onset_voiced_frames,
             config.playback_onset_window_frames),
            (11,14),
        )
        self.assertEqual(
            (config.listening_onset_voiced_frames,
             config.listening_onset_window_frames,
             config.listening_resume_voiced_frames,
             config.listening_resume_window_frames),
            (9,13,7,11),
        )

    def test_invalid_numeric_values_name_the_setting(self):
        cases=(
            ("CASCADE_VAD_MODE","not-an-integer","must be an integer"),
            ("CASCADE_VAD_PREFIX_MS","20.5","must be an integer"),
            ("XAI_REALTIME_VAD_THRESHOLD","not-a-float","must be a number"),
            ("NATIVE_VAD_PREFIX_PADDING_MS","x","must be an integer"),
            ("XAI_REALTIME_SILENCE_DURATION_MS","1.5","must be an integer"),
        )
        for name,value,message in cases:
            with self.subTest(name=name),self.assertRaisesRegex(
                ConfigurationError,f"{name} {message}"
            ):
                VoiceVadSettings.from_environment({name:value})

    def test_ranges_and_onset_relationship_are_validated(self):
        cases=(
            ({"CASCADE_VAD_MODE":"4"},"CASCADE_VAD_MODE"),
            ({"XAI_REALTIME_VAD_THRESHOLD":"0.91"},
             "XAI_REALTIME_VAD_THRESHOLD"),
            ({"XAI_REALTIME_SILENCE_DURATION_MS":"499"},
             "XAI_REALTIME_SILENCE_DURATION_MS"),
            ({"XAI_REALTIME_SILENCE_DURATION_MS":"3001"},
             "XAI_REALTIME_SILENCE_DURATION_MS"),
            (
                {
                    "CASCADE_VAD_ONSET_VOICED_FRAMES":"7",
                    "CASCADE_VAD_ONSET_WINDOW_FRAMES":"6",
                },
                "cannot exceed",
            ),
            (
                {
                    "CASCADE_VAD_PLAYBACK_ONSET_VOICED_FRAMES":"13",
                    "CASCADE_VAD_PLAYBACK_ONSET_WINDOW_FRAMES":"12",
                },
                "PLAYBACK_ONSET_VOICED_FRAMES cannot exceed",
            ),
            ({"CASCADE_VAD_PLAYBACK_ONSET_VOICED_FRAMES":"0"},
             "CASCADE_VAD_PLAYBACK_ONSET_VOICED_FRAMES"),
            ({"CASCADE_VAD_PLAYBACK_ONSET_WINDOW_FRAMES":"-1"},
             "CASCADE_VAD_PLAYBACK_ONSET_WINDOW_FRAMES"),
            ({"CASCADE_VAD_PLAYBACK_ONSET_WINDOW_FRAMES":"101"},
             "CASCADE_VAD_PLAYBACK_ONSET_WINDOW_FRAMES"),
            (
                {
                    "CASCADE_VAD_LISTENING_ONSET_VOICED_FRAMES":"13",
                    "CASCADE_VAD_LISTENING_ONSET_WINDOW_FRAMES":"12",
                },
                "LISTENING_ONSET_VOICED_FRAMES cannot exceed",
            ),
            (
                {
                    "CASCADE_VAD_LISTENING_RESUME_VOICED_FRAMES":"11",
                    "CASCADE_VAD_LISTENING_RESUME_WINDOW_FRAMES":"10",
                },
                "LISTENING_RESUME_VOICED_FRAMES cannot exceed",
            ),
            ({"CASCADE_VAD_LISTENING_ONSET_VOICED_FRAMES":"0"},
             "CASCADE_VAD_LISTENING_ONSET_VOICED_FRAMES"),
            ({"CASCADE_VAD_LISTENING_ONSET_WINDOW_FRAMES":"101"},
             "CASCADE_VAD_LISTENING_ONSET_WINDOW_FRAMES"),
            ({"CASCADE_VAD_LISTENING_RESUME_VOICED_FRAMES":"-1"},
             "CASCADE_VAD_LISTENING_RESUME_VOICED_FRAMES"),
            ({"CASCADE_VAD_LISTENING_RESUME_WINDOW_FRAMES":"101"},
             "CASCADE_VAD_LISTENING_RESUME_WINDOW_FRAMES"),
            (
                {
                    "CASCADE_VAD_MIN_SPEECH_MS":"2000",
                    "CASCADE_VAD_MAX_UTTERANCE_MS":"1000",
                },
                "cannot exceed",
            ),
        )
        for environment,message in cases:
            with self.subTest(environment=environment),self.assertRaisesRegex(
                ConfigurationError,message
            ):
                VoiceVadSettings.from_environment(environment)

    def test_milliseconds_round_up_to_twenty_ms_frames(self):
        self.assertEqual(
            [milliseconds_to_frames(value) for value in (1,20,21,300)],
            [1,1,2,15],
        )
        with self.assertRaises(ConfigurationError):
            milliseconds_to_frames(0)

    def test_native_environment_values_reach_session_payload(self):
        with patch.dict(os.environ,{
            "XAI_API_KEY":"test-key",
            "XAI_REALTIME_VAD_THRESHOLD":"0.55",
            "NATIVE_VAD_PREFIX_PADDING_MS":"500",
            "XAI_REALTIME_SILENCE_DURATION_MS":"1300",
        },clear=True):
            config=NativeRealtimeConfig.from_environment()
        payload=session_update_payload(
            config,
            ToolContext(
                Path("/trusted/catalog.sqlite"),
                "FACILITY","ko","test_only"),
            language_mode="manual",
            manual_language="ko",
        )
        self.assertEqual(payload["session"]["turn_detection"],{
            "type":"server_vad",
            "threshold":0.55,
            "silence_duration_ms":1300,
            "prefix_padding_ms":500,
            "idle_timeout_ms":None,
        })

    def test_native_silence_default_custom_and_bounds(self):
        self.assertEqual(
            NativeVadSettings.from_environment({}).silence_duration_ms,
            1600,
        )
        for value in (500,1600,2200,3000):
            with self.subTest(value=value):
                settings=NativeVadSettings.from_environment({
                    "XAI_REALTIME_SILENCE_DURATION_MS":str(value),
                })
                self.assertEqual(settings.silence_duration_ms,value)
        for value in ("not-an-integer","499","3001"):
            with self.subTest(value=value),self.assertRaisesRegex(
                ConfigurationError,"XAI_REALTIME_SILENCE_DURATION_MS",
            ):
                NativeVadSettings.from_environment({
                    "XAI_REALTIME_SILENCE_DURATION_MS":value,
                })


class VadStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_effective_non_secret_settings_are_logged_once_at_startup(self):
        with (
            patch.object(server,"start_moss_runtime_from_environment"),
            patch.object(server,"stop_moss_runtime"),
            patch.object(
                server.asyncio,
                "to_thread",
                side_effect=lambda function, *args: function(*args),
            ),
            patch.dict(os.environ,{},clear=True),
            self.assertLogs("voice_workflow_agent",level="INFO") as captured,
        ):
            async with server.lifespan(server.app):
                pass
        messages=[
            message for message in captured.output
            if "vad.configuration" in message
        ]
        self.assertEqual(len(messages),1)
        self.assertIn("cascade_mode=3",messages[0])
        self.assertIn("cascade_prefix_ms=300",messages[0])
        self.assertIn(
            "cascade_processing_onset_voiced_frames=4",messages[0])
        self.assertIn(
            "cascade_processing_onset_window_frames=6",messages[0])
        self.assertIn(
            "cascade_listening_onset_voiced_frames=8",messages[0])
        self.assertIn(
            "cascade_listening_onset_window_frames=12",messages[0])
        self.assertIn(
            "cascade_listening_resume_voiced_frames=6",messages[0])
        self.assertIn(
            "cascade_listening_resume_window_frames=10",messages[0])
        self.assertIn("cascade_playback_onset_voiced_frames=12",messages[0])
        self.assertIn("cascade_playback_onset_window_frames=15",messages[0])
        self.assertIn("native_threshold=0.6",messages[0])
        self.assertIn("native_silence_duration_ms=1600",messages[0])
        self.assertNotIn("API_KEY",messages[0])


if __name__=="__main__":
    unittest.main()
