"""One explicit way for a test to step around the development-activation wall.

Since STEP 23 the configured curated fixture is not executable merely because
it is configured.  A voice session may select it only when the catalog entry
materialized from that exact fixture is executable, which needs both a
recorded development activation and a readiness that a person has cleared.
The Candidate A in-gel fixture cannot satisfy the second half: two of its
blocking reasons are ``unsupported_repeat_until``, and no reviewer action
clears an unsupported capability.  So it is, correctly, not runnable.

That leaves the tests that exercise what happens *behind* the wall -- session
durability, recovery, turn handling, reporting -- with nothing to run.  They
step around the one gate in front of them, in the open, the way
``test_pdf_to_session_walkthrough`` already steps around the same wall to
diagnose the last two pipeline stages.  Nothing here changes a rule or a
readiness verdict; it only asserts, for the duration of one test, the answer
an activated protocol would have given.

Tests that are *about* the gate must not use this.  See
``tests/test_development_activation_gate.py``.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

import voice_workflow_agent.server as server_module

ACTIVATED_STATE: dict[str, object] = {
    "available_for_execution": True,
    "blocked_reason": None,
    "development_activation": {
        "activated": True,
        "actor_principal_id": "test-development-activator",
        "actor_role": "lab_admin",
        "recorded_at": "2026-09-05T00:00:00+00:00",
        "authority": "development_policy",
    },
    "approval": {
        "status": "development_only",
        "final_approval": False,
        "actor_principal_id": "test-development-activator",
        "actor_role": "lab_admin",
        "recorded_at": "2026-09-05T00:00:00+00:00",
        "authority": "development_policy",
    },
}


@contextmanager
def development_activation_recorded():
    """Answer as though a person had activated this fixture for development."""

    with patch.object(
        server_module,
        "_candidate_fixture_execution_state",
        return_value=dict(ACTIVATED_STATE),
    ) as recorded:
        yield recorded
