"""Lane RA test support: the fictional agitation protocol, a router-first session,
the routed turn, the fake roles and the voice harness (tests/router_fakes.py models).

Shared by the lane RA test files (one per decision). Nothing here is live.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from tests.protocol_vocabulary_support import build_fixture, miniprep_fixture
from tests.router_fakes import FakeRouterClient
from tests.test_voice_pause_resume_persistence import VoiceSessionHarness
from voiney_lab.curated_protocol import CuratedProtocolSession
from voiney_lab.llm_router import LlmRouterSettings, route_turn_with_llm_router
from voiney_lab.multi_brain import BrainTerminal
from voiney_lab.runtime_routing import route_curated_runtime_turn

ROLE_ENV = {
    "VOINEY_LAB_LLM_ROUTER_ENABLED": "true",
    "VOINEY_LAB_ROUTER_PROVIDER": "openai",
    "VOINEY_LAB_ROUTER_MODEL": "gpt-6-luna",
}
ON = LlmRouterSettings(enabled=True, model="fake-router-model", provider="xai", timeout_seconds=1.0)
FIRST = LlmRouterSettings(
    enabled=True, model="fake-router-model", provider="xai", timeout_seconds=1.0, router_first=True,
)
AGITATION_STEPS = (
    "1 Wash the band with 500 µL of solution A. Incubate the gel plug for 15 min at 37°C "
    "with gentle agitation 800 rpm.",
    "2 Add 10 µL lysozyme and mix by inverting the tube five times.",
    "3 Elute the DNA with 50 µL water.",
)


def agitation_fixture():
    return build_fixture(
        protocol_id="fictional-agitation", title="Fictional agitation protocol",
        steps=AGITATION_STEPS, materials=("Lysozyme Thermo Fisher Catalog #89833",),
    )


def session_at(fixture=None, step_index: int | None = 3, *, router_first: bool = False):
    session = CuratedProtocolSession(fixture or miniprep_fixture())
    session.router_first = router_first
    session.activate_configured()
    if step_index is not None:
        session.plan("시작", turn_id=1, language="ko", configuration_id=1, generation=1)
        session.current_index = step_index
    return session


def route(session, said, client, *, turn_id=2, history=(), settings=FIRST, roles=None, progress=None):
    async def rules():
        return route_curated_runtime_turn(
            session, said, turn_id=turn_id, language="ko", configuration_id=1, generation=1,
        )

    return asyncio.run(route_turn_with_llm_router(
        session, said, turn_id=turn_id, language="ko", settings=settings,
        client_factory=lambda: client, rule_route=rules, history=history,
        configuration_id=1, generation=1, roles=roles, on_progress=progress,
    ))


class FakeRun:
    def __init__(self, outputs: dict, *, delay: float = 0.0):
        self.outputs = outputs
        self.delay = delay
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True

    async def terminal(self, role, *, timeout=None):
        if role not in self.outputs:
            return None
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.outputs[role]


class FakeRoles:
    def __init__(self, outputs: dict, *, delay: float = 0.0):
        self.outputs = outputs
        self.delay = delay
        self.started: list = []
        self.runs: list[FakeRun] = []

    def start(self, context, transcript):
        self.started.append((context, transcript))
        run = FakeRun(self.outputs, delay=self.delay)
        self.runs.append(run)
        return run


def terminal(role, output, status="success"):
    return BrainTerminal(role, status, 5, output)


class FakeAsyncOpenAI:
    client: FakeRouterClient | None = None

    def __new__(cls, *args, **kwargs):
        return cls.client



class RouterFirstVoiceMixin(VoiceSessionHarness):
    """One voice session with the router on, the switch on unless ``first=False``."""

    def _run(self, scenario_steps, script=(), *, first: bool = True):
        self.environment["VOINEY_LAB_LLM_ROUTER_ENABLED"] = "true"
        self.environment["VOINEY_LAB_ROUTER_PROVIDER"] = "xai"
        self.environment["VOINEY_LAB_ROUTER_MODEL"] = "fake-router-model"
        self.environment["XAI_API_KEY"] = "test-only-not-a-key"
        self.environment["VOINEY_LAB_TRANSLATION_PROVIDER"] = "anthropic"
        self.environment["ANTHROPIC_API_KEY"] = ""
        if first:
            self.environment["VOINEY_LAB_ROUTER_FIRST"] = "true"
        client = FakeRouterClient(*script)
        FakeAsyncOpenAI.client = client
        seen: dict = {}

        async def scenario(socket, listener, say):
            await scenario_steps(socket, listener, say, seen)
            seen["history"] = listener.history.router_history()

        socket, listener = self._session(
            scenario, patch("voiney_lab.server.AsyncOpenAI", FakeAsyncOpenAI),
        )
        return socket, seen, client
