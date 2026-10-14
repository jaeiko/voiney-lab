"""Lane RA, decision (g): the old xai/grok defaults are gone, and so is the general brain.

An enabled router or multi-brain whose provider and model nobody set fails
closed at start-up instead of reaching a provider no one chose; the general
brain of a session without a protocol (its persona prompt, its streamed
answer) is deleted -- a voice session always has a protocol. Nothing is live.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import patch

from tests.lane_ra_support import (
    AGITATION_STEPS,
    FIRST,
    ON,
    ROLE_ENV,
    FakeRoles,
    RouterFirstVoiceMixin,
    agitation_fixture,
    route,
    session_at,
    terminal,
)
from tests.protocol_vocabulary_support import SOURCE_PDF
from tests.router_fakes import FakeRouterClient, answer_call_reply, tool_reply
from voiney_lab.llm_router import LlmRouterSettings
from voiney_lab.multi_brain import MultiBrainSettings


class CleanupTests(unittest.TestCase):
    """Decision (g): no default provider or model; no general brain."""

    def test_an_enabled_router_with_no_model_or_provider_fails_closed(self) -> None:
        for env in (
            {"VOINEY_LAB_LLM_ROUTER_ENABLED": "true"},
            {"VOINEY_LAB_LLM_ROUTER_ENABLED": "true", "VOINEY_LAB_ROUTER_PROVIDER": "openai"},
            {"VOINEY_LAB_LLM_ROUTER_ENABLED": "true", "VOINEY_LAB_ROUTER_MODEL": "gpt-6-luna"},
            {"VOINEY_LAB_LLM_ROUTER_ENABLED": "true", "VOINEY_LAB_ROUTER_FIRST": "true"},
        ):
            with self.subTest(env=env):
                closed = LlmRouterSettings.from_environment(env)
                self.assertFalse(closed.enabled)
                self.assertFalse(closed.router_first)
                self.assertEqual(closed.unavailable_reason,
                                 "VOINEY_LAB_ROUTER_PROVIDER 와 VOINEY_LAB_ROUTER_MODEL 설정 필요")
                self.assertEqual(closed.public_capability()["status"], "disabled")
                self.assertIn("설정 필요", str(closed.public_capability()["reason"]))
                with self.assertRaises(ValueError):
                    closed.role_model
        off = LlmRouterSettings.from_environment({})
        self.assertFalse(off.enabled)
        self.assertIsNone(off.model)
        self.assertIsNone(off.unavailable_reason)
        self.assertIsNone(LlmRouterSettings().model)
    def test_the_multi_brain_has_no_default_model_either(self) -> None:
        self.assertIsNone(MultiBrainSettings(False).model)
        with patch.dict("os.environ", {"VOINEY_LAB_MULTI_BRAIN_ENABLED": "true"}, clear=True):
            closed = MultiBrainSettings.from_environment()
            self.assertFalse(closed.enabled)
            self.assertFalse(closed.source_brain_enabled)
            self.assertIsNone(closed.model)
            self.assertEqual(closed.public_capability()["status"], "disabled")
            self.assertIn("설정 필요", str(closed.public_capability()["reason"]))
        with patch.dict("os.environ", {
            "VOINEY_LAB_MULTI_BRAIN_ENABLED": "true", "VOINEY_LAB_ANSWER_PROVIDER": "google",
            "VOINEY_LAB_ANSWER_MODEL": "gemini-3.8-flash",
        }, clear=True):
            self.assertEqual(MultiBrainSettings.from_environment().model, "gemini-3.8-flash")
        with patch.dict("os.environ", {}, clear=True):
            self.assertFalse(MultiBrainSettings.from_environment().enabled)
    def test_the_general_brain_is_gone(self) -> None:
        import voiney_lab.brain as brain

        for name in ("stream_brain_turn", "_collect_stream", "SYSTEM_PROMPT", "BrainResult"):
            self.assertFalse(hasattr(brain, name), name)


if __name__ == "__main__":
    unittest.main()
