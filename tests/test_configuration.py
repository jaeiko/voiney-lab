import os
import tempfile
import unittest
from unittest.mock import patch

from voice_workflow_agent import server
from voice_workflow_agent.configuration import (
    CascadeVadSettings,
    ConfigurationError,
    VoiceVadSettings,
    milliseconds_to_frames,
)
from voice_workflow_agent.tools import ToolContext
from voice_workflow_agent.vad import VadConfig
from voice_workflow_agent.semantic_intent import SemanticIntentSettings
from pathlib import Path


class VadConfigurationTests(unittest.TestCase):
    def test_defaults_preserve_existing_cascade_behavior(self):
        settings=VoiceVadSettings.from_environment({})
        self.assertEqual(settings.cascade,CascadeVadSettings())
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

    def test_every_cascade_setting_can_be_overridden(self):
        environment={
            "CASCADE_VAD_MODE":"2",
            "CASCADE_VAD_ONSET_VOICED_FRAMES":"5",
            "CASCADE_VAD_ONSET_WINDOW_FRAMES":"8",
            "CASCADE_VAD_PREFIX_MS":"321",
            "CASCADE_BARGE_IN_PREFIX_MS":"860",
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
        }
        settings=VoiceVadSettings.from_environment(environment)
        self.assertEqual(
            settings.cascade,
            CascadeVadSettings(
                2,5,8,321,860,777,281,16001,450,11,14,9,13,7,11))
        config=VadConfig.from_settings(settings.cascade)
        self.assertEqual(
            (
                config.prefix_frames,
                config.barge_in_prefix_frames,
                config.endpoint_silence_frames,
                config.minimum_voiced_frames,
                config.maximum_utterance_frames,
            ),
            (17,43,39,15,801),
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
            ("CASCADE_BARGE_IN_PREFIX_MS","x","must be an integer"),
        )
        for name,value,message in cases:
            with self.subTest(name=name),self.assertRaisesRegex(
                ConfigurationError,f"{name} {message}"
            ):
                VoiceVadSettings.from_environment({name:value})

    def test_ranges_and_onset_relationship_are_validated(self):
        cases=(
            ({"CASCADE_VAD_MODE":"4"},"CASCADE_VAD_MODE"),
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
        self.assertNotIn("API_KEY",messages[0])


class DeploymentConfigurationTests(unittest.TestCase):
    def test_process_environment_precedes_local_development_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            environment_file=Path(temporary)/".env"
            environment_file.write_text(
                "VOICE_WORKFLOW_AGENT_SEMANTIC_INTENT_ENABLED=true\n",
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"VOICE_WORKFLOW_AGENT_SEMANTIC_INTENT_ENABLED":"false"},
                clear=True,
            ):
                server._load_project_environment(environment_file)
                self.assertFalse(
                    SemanticIntentSettings.from_environment().enabled
                )

    def test_local_development_environment_precedes_documented_default(self):
        with tempfile.TemporaryDirectory() as temporary:
            environment_file=Path(temporary)/".env"
            environment_file.write_text(
                "VOICE_WORKFLOW_AGENT_SEMANTIC_INTENT_ENABLED=true\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ,{},clear=True):
                server._load_project_environment(environment_file)
                self.assertTrue(
                    SemanticIntentSettings.from_environment().enabled
                )

    def test_protocol_analysis_model_is_deployment_supplied_grok_4_6(self):
        with patch.dict(
            os.environ,
            {
                "XAI_API_KEY":"fake-key",
                "PROTOCOL_ANALYSIS_MODEL":"grok-4.6",
            },
            clear=True,
        ),patch.object(server,"OpenAI") as client:
            model=server._protocol_analysis_model()

        self.assertEqual(model.model,"grok-4.6")
        client.assert_called_once()


if __name__=="__main__":
    unittest.main()
