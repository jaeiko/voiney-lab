"""Forward progress happens in one place, and that place checks the gates.

The advance used to be a bare ``current_index += 1`` in the middle of the turn
handler, while the predicates written to guard it -- ``may_begin_step`` and the
two it calls -- sat beside it as methods with no callers. A rule enforced
nowhere is a rule the model is trusted to keep, and the premise of this system
is that it must not have to be trusted.

So there is one door. This file holds it shut: the structural test reads the
module's syntax tree and fails if any other code moves the index forward, which
is the only kind of test that survives someone adding a shortcut later.

What the door does *not* do is decide what happens instead. A refusal advances
nothing and marks nothing complete, and it does not queue an action or replay
an earlier plan. The person's next words stay theirs.
"""

from __future__ import annotations

import ast
import pathlib
import unittest

import voice_workflow_agent.curated_protocol as module

SOURCE = pathlib.Path(module.__file__)


class OneDoorTests(unittest.TestCase):
    """2-2: no path moves the run forward except the control point."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        cls.control = next(
            node
            for node in ast.walk(cls.tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "advance_one_step"
        )

    def _index_writes(self):
        found = []
        for node in ast.walk(self.tree):
            targets = []
            if isinstance(node, ast.AugAssign):
                targets = [node.target]
            elif isinstance(node, ast.Assign):
                targets = list(node.targets)
            for target in targets:
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr == "current_index"
                ):
                    found.append(
                        (
                            node.lineno,
                            "augmented" if isinstance(node, ast.AugAssign)
                            else "plain",
                        )
                    )
        return found

    def test_every_forward_move_is_inside_the_control_point(self) -> None:
        outside = [
            line
            for line, kind in self._index_writes()
            if kind == "augmented"
            and not (self.control.lineno <= line <= self.control.end_lineno)
        ]
        self.assertEqual(
            outside,
            [],
            "current_index is advanced outside advance_one_step; every forward "
            "move must go through the one place that checks the gates",
        )

    def test_the_control_point_advances_exactly_once(self) -> None:
        inside = [
            line
            for line, kind in self._index_writes()
            if kind == "augmented"
            and self.control.lineno <= line <= self.control.end_lineno
        ]
        self.assertEqual(len(inside), 1)

    def test_the_remaining_writes_are_starts_and_restores(self) -> None:
        """A plain assignment sets a run's position, it does not advance it.

        Kept as its own assertion so that adding one is a deliberate act: if
        this count changes, someone introduced a new way to place the cursor
        and has to say which of the three it is -- start, reset, or recovery.
        """

        plain = [line for line, kind in self._index_writes() if kind == "plain"]
        self.assertEqual(len(plain), 6, sorted(plain))

    def test_the_control_point_consults_the_gate(self) -> None:
        """2-3: it is not a wrapper. It asks before it moves."""

        import inspect

        body = inspect.getsource(module.CuratedProtocolSession.advance_one_step)
        self.assertIn("may_begin_step", body)
        # And the gate is asked before the write, not after.
        # rindex on both: the docstring quotes the old bare "+= 1" it
        # replaced, so the first occurrence is prose, not code.
        self.assertLess(
            body.rindex("if not self.may_begin_step"),
            body.rindex("self.current_index += 1"),
        )


class ARefusalSaysWhyAndDoesNothingElseTests(unittest.TestCase):
    """2-5 and 2-6: one sentence, no state change, no forced next move."""

    def _session(self, *, unread=True):
        from tests.test_pdf_to_session_walkthrough import _pipeline
        from voice_workflow_agent.curated_protocol import (
            CuratedProtocolFixture,
            CuratedProtocolSession,
        )

        _extraction, _plan, _merged, draft = _pipeline()
        labels = tuple(
            step.source_label
            for section in draft.protocol.sections
            for step in section.steps
        )
        fixture = CuratedProtocolFixture(
            draft=draft,
            status="fictional_non_operational",
            ordered_step_labels=labels,
            fixture_sha256="0" * 64,
            revision_id="advance-control-test",
            development_only=True,
            source_pdf_path=(
                pathlib.Path(__file__).resolve().parents[1]
                / "data" / "runtime" / "candidate-a-source"
                / "in-gel-digestion.pdf"
            ),
            source_filename=draft.extraction.original_filename,
            unread_pages=(
                {
                    draft.protocol.sections[0].steps[1]
                    .evidence.source_page_number: ()
                }
                if unread
                else None
            ),
        )
        session = CuratedProtocolSession(fixture)
        session.active = True
        return session

    @classmethod
    def setUpClass(cls) -> None:
        source = (
            pathlib.Path(__file__).resolve().parents[1]
            / "data" / "runtime" / "candidate-a-source" / "in-gel-digestion.pdf"
        )
        if not source.is_file():
            raise unittest.SkipTest(f"{source} is not present.")

    def test_a_refused_advance_moves_nothing(self) -> None:
        session = self._session()
        # Park on the step before the unread page, so the next one is gated.
        target = next(
            index
            for index, step in enumerate(session.fixture.steps)
            if session.step_is_on_an_unread_page(index)
        )
        session.current_index = target - 1
        before = session.current_index
        refusal = session.advance_one_step()
        self.assertIsNotNone(refusal)
        self.assertEqual(session.current_index, before)

    def test_the_refusal_reaches_the_person_as_one_sentence(self) -> None:
        session = self._session()
        target = next(
            index
            for index, step in enumerate(session.fixture.steps)
            if session.step_is_on_an_unread_page(index)
        )
        session.current_index = target - 1
        refusal = session._peek_advance_refusal()
        sentence = session._advance_refusal_sentence(
            refusal, session.fixture.steps[session.current_index], "ko"
        )
        self.assertTrue(sentence)
        # Short: a reason and what is needed, not the rule set.
        self.assertLess(len(sentence), 160)
        self.assertIn("넘기지 않았습니다", sentence)

    def test_a_refusal_does_not_decide_what_happens_next(self) -> None:
        """2-6: the gate closes a door; it does not open a different one.

        Read as code -- the control point returns a code and writes nothing
        else. It queues no action, sets no pending transition, and replays no
        earlier plan, so the next thing the person says is still what decides.
        """

        import inspect

        body = inspect.getsource(
            module.CuratedProtocolSession.advance_one_step
        )
        for forbidden in (
            "_replay",
            "_pending",
            "requested_transition",
            "target_step",
            "self.active =",
            "_workflow_status",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, body)

    def test_an_allowed_advance_moves_exactly_one_step(self) -> None:
        session = self._session(unread=False)
        session.current_index = 0
        self.assertIsNone(session.advance_one_step())
        self.assertEqual(session.current_index, 1)

    def test_it_refuses_at_the_final_step_and_when_not_active(self) -> None:
        session = self._session(unread=False)
        session.current_index = len(session.fixture.steps) - 1
        self.assertEqual(
            session.advance_one_step(),
            session.ADVANCE_REFUSED_AT_FINAL_STEP,
        )
        session.current_index = 0
        session.active = False
        self.assertEqual(
            session.advance_one_step(),
            session.ADVANCE_REFUSED_NOT_ACTIVE,
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
