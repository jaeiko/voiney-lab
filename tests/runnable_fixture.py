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
from types import SimpleNamespace
from unittest.mock import patch

import voiney_lab.server as server_module
from voiney_lab.start_check import StartCheck

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
        def confirmed_start(protocol_id, revision_id):
            # These turn-handling tests already assume a materialized source.
            # Give that fixture an isolated fake ledger, but create the value
            # and safety receipts through the real StartCheck before reading
            # readiness. Tests of the start gate use a real catalog instead.
            fixture = recorded.call_args.args[0]
            principal_id = server_module._start_check_principal_id()
            assert principal_id is not None
            target = {"kind": "analysis", "analysis_id": fixture.revision_id,
                      "revision_id": revision_id}
            items = [{"item_id": f"step:{step.step_id}", "kind": "step",
                      "step_id": step.step_id, "step_label": step.source_label,
                      "text": step.instruction_source_text}
                     for step in fixture.steps]
            events = []

            def append(event_id, protocol_id, revision_number, event_type, payload):
                events.append(SimpleNamespace(event_type=event_type, payload=payload,
                                              protocol_revision_number=revision_number))

            catalog = SimpleNamespace(
                review_draft=lambda pid: {"target": target, "items": items, "available_for_execution": True},
                _latest_protocol_revision=lambda pid: SimpleNamespace(revision_number=1),
                _ocr_pages=lambda revision: {},
                store=SimpleNamespace(list_events=lambda pid: events, append_event=append))
            checker = StartCheck(catalog, enabled=True)
            for question in checker._built(protocol_id)[1]:
                checker.answer(protocol_id, question["question_id"], action="accept",
                               actor_principal_id=principal_id)
            checker.acknowledge_safety(protocol_id, target=target,
                                       actor_principal_id=principal_id)
            return checker.readiness(protocol_id, actor_principal_id=principal_id,
                                     revision_id=revision_id)

        with patch.object(server_module, "_protocol_start_readiness", side_effect=confirmed_start):
            yield recorded
