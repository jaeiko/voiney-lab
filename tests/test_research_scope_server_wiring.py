"""The server judges a search query on the whole active protocol.

``plan_research_query`` can use an in-gel search label only where the active
protocol has the substance, and name the protocol's own materials otherwise,
but only when the caller passes ``CuratedProtocolSession.research_scope()``.
Both server call sites -- the related-question turn and the Source Brain's
research query -- now pass it.

These tests run the server's own code: a related question goes through
``run_turn``, and a Source Brain query through ``_queue_curated_research``.
They read the query where the server uses it, the web-reference search
(the approved lab-reference search that read it before was deleted on
2026-10-10, lane CL).
For in-gel the query is the same, character for character, as the one built
without the scope (which ``test_protocol_scoped_research_query`` shows is the
query sent before); for another protocol it names that protocol's material.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from voiney_lab import server as server_module
from voiney_lab.curated_protocol import CuratedProtocolAction, CuratedProtocolSession
from voiney_lab.external_references import ExternalReferenceSettings, plan_research_query
from voiney_lab.language import Transcription
from voiney_lab.multi_brain import SourceBrainOutput
from voiney_lab.runtime_routing import route_curated_runtime_turn
from voiney_lab.server import (
    ListenerSession,
    LockedSender,
    _queue_curated_research,
    run_turn,
)
from voiney_lab.tools import ToolContext
from voiney_lab.vad import TurnState

from tests.protocol_vocabulary_support import (
    SOURCE_PDF,
    in_gel_fixture,
    miniprep_fixture,
)

_SCOPE_KEYS = ("protocol_materials", "protocol_text")

IN_GEL_QUESTIONS = (
    "AMBIC가 뭐야?",
    "HPLC water가 뭐야?",
    "Solution A가 뭐야?",
    "Solution B 구성은 뭐야?",
    "acetonitrile이 뭐야?",
    "gel plug이 뭐야?",
    "DTT가 뭐야?",
    "iodoacetamide가 뭐야?",
    "trypsin이 뭐야?",
    "formic acid는 왜 넣어?",
    "원심분리기가 뭐야?",
)
IN_GEL_STEPS = (2, 8, 10, 21, 24)


class _Socket:
    def __init__(self) -> None:
        self.text: list[dict] = []

    async def send_text(self, value: str) -> None:
        self.text.append(json.loads(value))

    async def send_bytes(self, value: bytes) -> None:
        pass


def _listener(fixture, index: int) -> ListenerSession:
    context = ToolContext(Path("/unused/offline-catalog"), None, "ko", "test_only")
    workflow = CuratedProtocolSession(fixture)
    workflow.active = True
    workflow.current_index = index
    session = ListenerSession(tool_context=context, curated_protocol_session=workflow)
    session.active = True
    session.active_turn_id = 1
    session.next_turn_id = 2
    session.turn_generations[1] = session.generation
    session.accept_configuration(41, "cascade", "ko", fixture.protocol_id)
    session.detector.state = TurnState.PROCESSING
    # The web-reference search is where the query is read.
    session.external_reference_settings = ExternalReferenceSettings(
        True, ("pubchem.ncbi.nlm.nih.gov",), "offline-web", 2.0, 3, "candidate_a",
    )
    return session


class _Recorder:
    """Spy on plan_research_query and on the search that receives its query."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, str]] = []
        self.searched: list[str] = []

    def plan(self, question, **kwargs):
        query = plan_research_query(question, **kwargs)
        self.calls.append((question, kwargs, query))
        return query

    def web(self, *args):
        recorder = self

        class _Web:
            async def search(self, query, *, language, **kwargs):
                recorder.searched.append(query)
                return {"status": "no_results", "matches": [], "images": []}

        return _Web()


async def _immediate(function, *args, **kwargs):
    return function(*args, **kwargs)


async def _drain(session: ListenerSession) -> None:
    """Let the research worker finish, then drop the budget-notice timer."""

    for _ in range(2000):
        if not session.research_operations:
            break
        await asyncio.sleep(0.001)
    pending = [
        task for task in asyncio.all_tasks() if task is not asyncio.current_task()
    ]
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)


def _related_turn(session: ListenerSession, transcript: str) -> _Recorder:
    recorder = _Recorder()

    async def scenario() -> None:
        await run_turn(_Socket(), session, b"\0\0", 1, session.generation)
        await _drain(session)

    with patch(
        "voiney_lab.server.transcribe", return_value=Transcription(transcript, "ko"),
    ), patch(
        "voiney_lab.server.synthesize", return_value=b"\0\0",
    ), patch(
        "voiney_lab.server.plan_research_query", side_effect=recorder.plan,
    ), patch(
        "voiney_lab.server.XaiAuthoritativeWebSearch", recorder.web,
    ), patch(
        "voiney_lab.server.AsyncOpenAI", return_value=object(),
    ), patch(
        "voiney_lab.server.require_env", return_value="offline",
    ), patch(
        "voiney_lab.server.asyncio.to_thread", side_effect=_immediate,
    ):
        asyncio.run(scenario())
    return recorder


def _source_brain_research(
    session: ListenerSession, transcript: str, brain_query: str,
) -> _Recorder:
    """Queue research the way run_turn does once the Source Brain asked for it."""

    curated = session.curated_protocol_session
    plan = route_curated_runtime_turn(curated, transcript, turn_id=1, language="ko").plan
    step = curated.fixture.steps[curated.current_index]
    facts = tuple(plan.facts or curated.related_facts(transcript))
    context = {
        "query": transcript,
        "reference_query": "query built before the Source Brain answered",
        "step": step,
        "facts": facts,
        "force_external": False,
    }
    output = SourceBrainOutput(
        entities=plan.requested_entities,
        dimensions=plan.question_dimensions,
        scopes=("APPROVED_REFERENCE",),
        query=brain_query,
        needs_research=True,
    )
    recorder = _Recorder()

    async def scenario() -> None:
        session.begin_research(1, session.generation)
        await _queue_curated_research(
            session=session, sender=LockedSender(_Socket()), turn_id=1,
            generation=session.generation, endpoint=0.0, clock=session.clock,
            curated=curated, context=context, turn_language="ko",
            pre_transition_index=curated.current_index, plan=plan,
            source_output=output,
        )
        await _drain(session)

    with patch(
        "voiney_lab.server.plan_research_query", side_effect=recorder.plan,
    ), patch(
        "voiney_lab.server.XaiAuthoritativeWebSearch", recorder.web,
    ), patch(
        "voiney_lab.server.AsyncOpenAI", return_value=object(),
    ), patch(
        "voiney_lab.server.require_env", return_value="offline",
    ), patch(
        "voiney_lab.server.asyncio.to_thread", side_effect=_immediate,
    ):
        asyncio.run(scenario())
    return recorder


def _without_scope(kwargs: dict) -> dict:
    return {key: value for key, value in kwargs.items() if key not in _SCOPE_KEYS}


class ResearchScopeWiringTests(unittest.TestCase):
    def assert_scope_passed(self, recorder: _Recorder, session: ListenerSession) -> str:
        self.assertEqual(len(recorder.calls), 1)
        question, kwargs, query = recorder.calls[0]
        scope = session.curated_protocol_session.research_scope()
        for key in _SCOPE_KEYS:
            self.assertEqual(kwargs[key], scope[key])
        # The query the server built is the one the search received.
        self.assertEqual(recorder.searched, [query])
        return query

    @unittest.skipUnless(
        SOURCE_PDF.is_file(),
        f"requires the externally licensed Candidate A source PDF at {SOURCE_PDF}",
    )
    def test_in_gel_related_question_query_is_unchanged(self) -> None:
        fixture = in_gel_fixture()
        checked = 0
        for label in IN_GEL_STEPS:
            for transcript in IN_GEL_QUESTIONS:
                session = _listener(fixture, label - 1)
                probe = CuratedProtocolSession(fixture)
                probe.active = True
                probe.current_index = label - 1
                routed = route_curated_runtime_turn(
                    probe, transcript, turn_id=1, language="ko").plan
                if routed.action is not CuratedProtocolAction.RELATED_QUESTION:
                    continue
                with self.subTest(step=label, transcript=transcript):
                    recorder = _related_turn(session, transcript)
                    query = self.assert_scope_passed(recorder, session)
                    question, kwargs, _ = recorder.calls[0]
                    self.assertEqual(
                        query, plan_research_query(question, **_without_scope(kwargs)))
                    checked += 1
        self.assertGreater(checked, 40)

    @unittest.skipUnless(
        SOURCE_PDF.is_file(),
        f"requires the externally licensed Candidate A source PDF at {SOURCE_PDF}",
    )
    def test_in_gel_source_brain_query_is_unchanged(self) -> None:
        fixture = in_gel_fixture()
        for label, transcript, brain_query in (
            (2, "Solution A가 뭐야?", "Solution A composition"),
            (10, "DTT가 뭐야?", "DTT reducing agent role"),
            (21, "trypsin이 뭐야?", "trypsin digestion"),
        ):
            with self.subTest(step=label, transcript=transcript):
                session = _listener(fixture, label - 1)
                recorder = _source_brain_research(session, transcript, brain_query)
                query = self.assert_scope_passed(recorder, session)
                question, kwargs, _ = recorder.calls[0]
                self.assertEqual(question, brain_query)
                self.assertEqual(
                    query, plan_research_query(question, **_without_scope(kwargs)))
                self.assertIn("in-gel digestion", query)

    def test_another_protocol_related_question_names_its_material(self) -> None:
        session = _listener(miniprep_fixture(), 0)
        recorder = _related_turn(session, "Tris-HCl buffer는 왜 넣어?")
        self.assertEqual(len(recorder.searched), 1)
        query = recorder.searched[0]
        # Without the scope the server searched "laboratory protocol role in ...".
        self.assertTrue(
            query.startswith("Tris-HCl buffer role in fictional plasmid miniprep."),
            query,
        )
        self.assertNotIn("in-gel", query)
        self.assertEqual(self.assert_scope_passed(recorder, session), query)

    def test_another_protocol_source_brain_query_names_its_material(self) -> None:
        session = _listener(miniprep_fixture(), 0)
        recorder = _source_brain_research(
            session, "Tris-HCl buffer는 왜 넣어?", "Tris-HCl buffer purpose")
        self.assertEqual(len(recorder.searched), 1)
        query = recorder.searched[0]
        self.assertTrue(
            query.startswith("Tris-HCl buffer role in fictional plasmid miniprep."),
            query,
        )
        self.assertEqual(self.assert_scope_passed(recorder, session), query)

    def test_every_call_site_passes_the_scope(self) -> None:
        source = Path(server_module.__file__).read_text(encoding="utf-8")
        calls = source.count("plan_research_query(")
        self.assertEqual(calls, 2)
        self.assertEqual(source.count("**curated.research_scope()"), calls)


if __name__ == "__main__":
    unittest.main()
