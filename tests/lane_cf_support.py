"""Shared helpers for the lane CF tests (field interviews of 2026-10-07/08).

Two fictional protocols, built from text alone so the tests run in both
pytest baselines:

* lane N's wash (``tests.lane_n_support``): six steps, a repeat-until of
  steps 2-4 stated at step 5, a 10-minute timer on step 2. The voice-session
  tests use it through ``VoiceNotesHarness`` (workspace and report store on);
* lane CB's headspace subset (``tests.lane_cb_support``): a fixed repeat of
  12-15 stated at 16, a person-decided repeat of 19-20 stated at 21, and the
  condition at 42 over the repeat of 36-41. The rule-level tests use it
  through ``Turns``.

``Session`` is ``Turns`` with the experimenter's settings applied the way the
server applies them when a session opens.
"""

from __future__ import annotations

from tests.lane_cb_support import (  # noqa: F401
    CONDITION_42,
    RANGE_21,
    Recorded,
    Turns,
    headspace_fixture,
    index_of,
)
from tests.lane_n_support import (  # noqa: F401
    PROTOCOL_ID as WASH_PROTOCOL_ID,
    VoiceNotesHarness,
    notes_fixture,
    shown,
)
from voiney_lab.curated_protocol import CuratedProtocolSession


class Session(Turns):
    """A headspace-shaped session (or a wash one) with chosen settings."""

    def open_with(
        self,
        step_index: int | None,
        *,
        fixture=None,
        confirm_mode: str = "readback",
        question_timing: str = "during",
    ) -> CuratedProtocolSession:
        """Open a session; ``step_index`` None leaves it before the start."""

        self.session = CuratedProtocolSession(fixture or headspace_fixture())
        self.session.apply_experimenter_settings(
            {"confirm_mode": confirm_mode, "question_timing": question_timing})
        self.session.activate_configured()
        self.turn_id = 0
        if step_index is not None:
            self.say("프로토콜 시작해줘")
            self.session.current_index = step_index
        return self.session

    def text(self, said: str) -> str:
        plan = self.say(said)
        return plan.display_text if plan is not None else ""


def wash_session(**settings) -> Session:
    turns = Session()
    turns.open_with(None, fixture=notes_fixture(), **settings)
    return turns


class RecordedSession(Session, Recorded):
    """``Session`` whose state changes and records go to an experiment report."""
