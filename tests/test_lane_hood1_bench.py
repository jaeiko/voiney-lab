"""HOOD1: hands-free display commands at the production turn boundary.

The display is a device preference.  Saying its two commands must pass
through the one deterministic turn line, leave every workflow checkpoint
alone, and reach the browser as a configuration-fenced server event.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.lane_cf_support import Session
from tests.protocol_vocabulary_support import miniprep_fixture
from tests.test_screen_cleanup import run_page_script
from voiney_lab.curated_protocol import (
    CuratedProtocolAction,
    CuratedProtocolSession,
    display_mode_request,
)
from voiney_lab.language import Transcription
from voiney_lab.multi_brain import MultiBrainSettings
from voiney_lab.server import ListenerSession, run_turn
from voiney_lab.tools import ToolContext
from voiney_lab.vad import TurnState


class _Socket:
    def __init__(self) -> None:
        self.text: list[dict] = []

    async def send_text(self, value: str) -> None:
        self.text.append(json.loads(value))

    async def send_bytes(self, _value: bytes) -> None:
        return None


def _listener() -> ListenerSession:
    fixture = miniprep_fixture()
    workflow = CuratedProtocolSession(fixture)
    workflow.active = True
    workflow.current_index = 1
    session = ListenerSession(
        tool_context=ToolContext(Path("/unused/offline-catalog"), None, "ko", "test_only"),
        curated_protocol_session=workflow,
        multi_brain_settings=MultiBrainSettings(False),
    )
    session.active = True
    session.accept_configuration(41, "cascade", "ko", fixture.protocol_id)
    return session


def _turn(session: ListenerSession, transcript: str, turn_id: int = 1) -> list[dict]:
    session.active_turn_id = turn_id
    session.next_turn_id = turn_id + 1
    session.turn_generations[turn_id] = session.generation
    session.detector.state = TurnState.PROCESSING
    socket = _Socket()

    async def immediate(function, *args, **kwargs):
        return function(*args, **kwargs)

    with patch(
        "voiney_lab.server.transcribe", return_value=Transcription(transcript, "ko"),
    ), patch(
        "voiney_lab.server.synthesize", return_value=b"\0\0",
    ), patch(
        "voiney_lab.server.asyncio.to_thread", side_effect=immediate,
    ), patch(
        "voiney_lab.server.AsyncOpenAI", return_value=SimpleNamespace(),
    ), patch(
        "voiney_lab.server.require_env", return_value="offline",
    ):
        asyncio.run(run_turn(socket, session, b"\0\0", turn_id, session.generation))
    return socket.text


def test_display_words_are_narrow_whole_commands() -> None:
    assert display_mode_request("화면 크게") == "far"
    assert display_mode_request("화면 크게 해 줘") == "far"
    assert display_mode_request("보통 화면") == "normal"
    assert display_mode_request("보통 화면으로 해 줘") == "normal"
    for words in ("화면 크게 해도 돼?", "보통 화면이 뭐야", "화면", "크게"):
        assert display_mode_request(words) is None


def test_display_command_changes_no_workflow_checkpoint() -> None:
    turns = Session()
    turns.open_with(1)
    turns.say("다 했어")
    pending = turns.session.confirm_question()
    before = turns.projection()

    plan = turns.say("화면 크게")

    assert plan.action is CuratedProtocolAction.DISPLAY_MODE
    assert plan.display_mode == "far"
    assert plan.state_changed is False
    assert plan.setting_change is None
    assert turns.session.confirm_question() == pending
    assert turns.projection() == before
    assert turns.session.last_front_rule == "display_mode"


def test_run_turn_sends_the_fenced_display_event_without_moving_the_run() -> None:
    session = _listener()
    workflow = session.curated_protocol_session
    assert workflow is not None
    before = (workflow.current_index, workflow.active, workflow.workflow_status, workflow.state()["revision"])

    events = _turn(session, "화면 크게")

    display = [event for event in events if event["type"] == "screen.display_mode"]
    assert len(display) == 1
    assert display[0]["mode"] == "far"
    assert display[0]["configuration_id"] == 41
    assert display[0]["turn_id"] == 1
    assert display[0]["generation"] == session.generation
    assert (workflow.current_index, workflow.active, workflow.workflow_status, workflow.state()["revision"]) == before
    operation = next(event for event in events if event["type"] == "server.operation")
    assert operation["operation"] == "display_mode_changed"


def test_voice_core_announces_a_real_microphone_change_once_during_a_run() -> None:
    result = run_page_script(r"""
sessionActive=true;socket=new WS();
let label="Bluetooth 이어셋",deviceId="headset-1";
const track={get label(){return label;},getSettings:()=>({deviceId,echoCancellation:true,noiseSuppression:true,autoGainControl:true})};
const stream={getAudioTracks:()=>[track],getTracks:()=>[track]};
const constraints={echoCancellation:true,noiseSuppression:true,autoGainControl:true};
reportMicSettings(stream,constraints);
assert(node("mic-device-status").textContent==="사용 중 · Bluetooth 이어셋",`initial: ${node("mic-device-status").textContent}`);
reportMicSettings(stream,constraints);
assert(node("mic-device-status").textContent==="사용 중 · Bluetooth 이어셋",`same device: ${node("mic-device-status").textContent}`);
label="목걸이형 마이크";deviceId="neck-2";
reportMicSettings(stream,constraints);
assert(node("mic-device-status").textContent==="마이크가 바뀌었어요 · 목걸이형 마이크 사용 중",`changed: ${node("mic-device-status").textContent}`);
reportMicSettings(stream,constraints);
assert(node("mic-device-status").textContent==="사용 중 · 목걸이형 마이크",`repeat: ${node("mic-device-status").textContent}`);
deviceId="neck-3";
reportMicSettings(stream,constraints);
assert(node("mic-device-status").textContent==="마이크가 바뀌었어요 · 목걸이형 마이크 사용 중",`same-label replacement: ${node("mic-device-status").textContent}`);
""")
    assert result.returncode == 0, result.stderr or result.stdout
