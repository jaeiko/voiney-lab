"""One explicit way for a test to step around the development-activation wall.

Since STEP 23 the configured curated fixture is not executable merely because
it is configured.  A voice session may select it only when the catalog entry
materialized from that exact fixture is executable, which needs both a
recorded development activation and a readiness that a person has cleared.

Until the REPEAT_UNTIL declaration the Candidate A in-gel fixture could not
satisfy the second half at all: three of its blocking reasons were
``unsupported_repeat_until``, and no reviewer action clears an unsupported
capability.  That is no longer the wall.  Both reasons it has left --
``no_declared_safety_warnings`` and one ``unresolved_ambiguity`` -- are
reviewer-clearable, so the document is reachable through the audited route
(``test_pdf_to_session_walkthrough.test_resolving_the_ambiguities_narrows_the_wall``
walks it end to end).  What remains in front of these tests is the route
itself: a reviewer's acknowledgement, four resolutions, and a recorded
activation, none of which a unit test of turn handling has any business
performing.

So this still exists, for the same reason and with a smaller claim.  The
tests that exercise what happens *behind* the wall -- session durability,
recovery, turn handling, reporting -- step around the gate in front of them,
in the open.  Nothing here changes a rule or a readiness verdict; it only
asserts, for the duration of one test, the answer an activated protocol would
have given.  It does not step around the endpoint-observation gate: that one
is not readiness and is not clearable by anybody but the experimenter, and
tests about it must go through it (see
``tests/test_repeat_until_declaration_properties.py``).

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
