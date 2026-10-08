"""One explicit way for a test to assume the configured fixture may run.

Since STEP 23 the configured curated fixture is not executable merely because
it is configured.  A voice session may select it only when the catalog entry
materialized from that exact fixture is executable.  Under the MVP rule
(human decision of 2026-10-08, lane DI) that means its analysis carries no
execution blocker -- there is no approval, finding or activation to record
any more -- but it still needs a protocol store with the fixture
materialized in it, which a unit test of turn handling has no business
setting up.

So the tests that exercise what happens *behind* the rule -- session
durability, recovery, turn handling, reporting -- assert, for the duration of
one test and in the open, the answer a materialized fixture would give.
Nothing here changes a rule or a readiness verdict.  It does not step around
the endpoint-observation gate: that one is not readiness and is not
clearable by anybody but the experimenter, and tests about it must go
through it (see ``tests/test_repeat_until_declaration_properties.py``).

Tests that are *about* the rule must not use this.  See
``tests/test_lane_di_execution_rule.py``.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

import voiney_lab.server as server_module

RUNNABLE_STATE: dict[str, object] = {
    "available_for_execution": True,
    "blocked_reason": None,
    "execution_blockers": [],
    "execution_notices": [],
}


@contextmanager
def runnable_fixture_assumed():
    """Answer as though the fixture were materialized with no execution blocker."""

    with patch.object(
        server_module,
        "_candidate_fixture_execution_state",
        return_value=dict(RUNNABLE_STATE),
    ) as recorded:
        yield recorded
