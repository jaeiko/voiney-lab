"""Lane HELP, decision 3 (2026-10-24): the knowledge search behind a problem's help.

``knowledge`` holds the contract every source keeps -- ``KnowledgeHit``,
``KnowledgeContext``, ``KnowledgeSource`` and ``KnowledgeUnavailable`` (a
source that cannot be used names its setting, and no other source is tried in
its place) -- and the two sources this lane makes: the running protocol's own
sentences and the same account's earlier runs of it, both searched with SQLite
FTS5 inside the server process. Numbers keep their units in the terms, and a
Korean problem word meets the English a source writes through shared concept
terms. The public tier is lane KNOW's. Fictional protocol, no licensed PDF.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from tests.lane_help_support import NOTE_2, RESULT_3, STEP_3, TROUBLE, WARNING_1, gel_wash_fixture
from voiney_lab.experiment_reports import ExperimentReportStore
from voiney_lab.knowledge import (
    HistoryKnowledge,
    KnowledgeContext,
    KnowledgeHit,
    KnowledgeSource,
    KnowledgeUnavailable,
    ProtocolKnowledge,
    index_terms,
    search_tiers,
)


def _context(fixture, step_id: str | None, *, organization=None, account=None) -> KnowledgeContext:
    return KnowledgeContext(organization, account, fixture.protocol_id, fixture.revision_id, step_id, "ko")


class ProtocolSearchTests(unittest.TestCase):
    """Decision 3: the protocol tier, its terms and what it finds."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = gel_wash_fixture()
        cls.knowledge = ProtocolKnowledge(cls.fixture)

    def _texts(self, said: str, step_id: str) -> list[str]:
        return [hit.text for hit in self.knowledge.search_sync(
            said, limit=4, context=_context(self.fixture, step_id))]

    def test_values_keep_their_units_and_korean_meets_english(self) -> None:
        terms = index_terms("Wash with 500 µL at 37°C for 15 min, 0.5 mL")
        for term in ("v500ul", "v37degc", "v15min", "v0p5ml"):
            self.assertIn(term, terms)
        self.assertIn("cstain", index_terms("젤이 아직 파래"))
        self.assertIn("cstain", index_terms("until the gel piece is fully destained"))

    def test_the_repeat_condition_troubleshooting_line_and_note_are_found(self) -> None:
        found = self._texts("젤이 아직 파래", "step-3")
        self.assertEqual(found[0], STEP_3)
        self.assertIn(TROUBLE, found)
        self.assertIn(NOTE_2, found)

    def test_nothing_unrelated_is_said(self) -> None:
        self.assertEqual(self._texts("침전이 생겼어", "step-2"), [])
        self.assertNotIn(WARNING_1, self._texts("젤이 아직 파래", "step-3"))

    def test_a_question_with_no_thing_named_gets_the_step_own_lines(self) -> None:
        self.assertEqual(self._texts("왜 안 되지?", "step-3"), [RESULT_3])

    def test_a_hit_names_its_page_step_and_reading(self) -> None:
        (hit, *_rest) = self.knowledge.search_sync(
            "젤이 아직 파래", limit=4, context=_context(self.fixture, "step-3"))
        self.assertEqual(hit.tier, "protocol")
        self.assertEqual(hit.detail("page"), "1")
        self.assertEqual(hit.detail("step_label"), "3")
        self.assertTrue(hit.detail("reading").startswith("3단계:"))
        self.assertEqual(hit.locator, "p.1 · 3단계")

    def test_another_protocol_finds_nothing(self) -> None:
        other = KnowledgeContext(None, None, "another-protocol", None, "step-3")
        self.assertEqual(self.knowledge.search_sync("젤이 아직 파래", limit=4, context=other), [])

    def test_it_is_a_knowledge_source(self) -> None:
        self.assertIsInstance(self.knowledge, KnowledgeSource)
        self.assertEqual(self.knowledge.tier, "protocol")


class _Store:
    """An experiment record store with an earlier run already in it."""

    def __init__(self, directory: str, fixture) -> None:
        self.store = ExperimentReportStore(Path(directory) / "reports.sqlite")
        self.fixture = fixture

    def run(self, session: str, *, organization: str | None, owner: str | None,
            events: tuple[tuple[str, str, str], ...] = ()) -> str:
        report = self.store.open_report(
            session_id=session, protocol_id=self.fixture.protocol_id,
            protocol_title=self.fixture.title, protocol_revision=self.fixture.revision_id,
            protocol_sha256="a" * 64, readiness_status="ready", development_only=True,
            organization_id=organization, owner_principal_id=owner,
        )
        for index, (kind, label, words) in enumerate(events):
            step = next(item for item in self.fixture.steps if item.source_label == label)
            self.store.append_event(
                report["report_id"], event_key=f"{session}-{index}", event_type=kind,
                step_id=step.step_id, step_label=label, user_wording=words or None,
            )
        return report["report_id"]


class HistorySearchTests(unittest.TestCase):
    """Decision 2 ② and 3: the same account's earlier runs, nobody else's."""

    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.fixture = gel_wash_fixture()
        self.records = _Store(directory.name, self.fixture)
        self.labels = {step.step_id: step.source_label for step in self.fixture.steps}

    def _history(self, report_id: str) -> HistoryKnowledge:
        return HistoryKnowledge(self.records.store, report_id, step_labels=self.labels)

    def test_an_earlier_run_at_the_same_step_with_what_followed(self) -> None:
        self.records.run("earlier", organization="lab-1", owner="alice", events=(
            ("anomaly", "2", "침전이 생겼어"), ("observation", "2", "세척을 한 번 더 했어"),
            ("step_completed", "2", ""), ("anomaly", "4", "젤이 안 말라"),
        ))
        current = self.records.run("now", organization="lab-1", owner="alice")
        history = self._history(current)
        self.assertEqual(history.scope(), ("lab-1", "alice"))
        hits = history.search_sync(
            "침전이 보여", limit=3,
            context=_context(self.fixture, "step-2", organization="lab-1", account="alice"))
        self.assertEqual([hit.text for hit in hits], ["침전이 생겼어"])
        after = [value for key, value in hits[0].details if key == "after"]
        self.assertEqual(after, ["관찰: 세척을 한 번 더 했어", "단계 완료"])
        self.assertEqual(hits[0].tier, "history")

    def test_another_account_or_a_lab_only_record_is_never_read(self) -> None:
        context = _context(self.fixture, "step-2", organization="lab-1", account="alice")
        self.records.run("bob-run", organization="lab-1", owner="bob", events=(
            ("anomaly", "2", "침전이 생겼어"),))
        self.records.run("lab-run", organization="lab-1", owner=None, events=(
            ("anomaly", "2", "침전이 생겼어"),))
        self.records.run("other-lab", organization="lab-2", owner="alice", events=(
            ("anomaly", "2", "침전이 생겼어"),))
        current = self.records.run("now", organization="lab-1", owner="alice")
        self.assertEqual(self._history(current).search_sync("침전이 생겼어", limit=3, context=context), [])
        # A lab-only record (no owner) has no history of its own either.
        lab_only = self.records.run("lab-now", organization="lab-1", owner=None)
        self.assertEqual(self._history(lab_only).search_sync(
            "침전이 생겼어", limit=3, context=_context(self.fixture, "step-2", organization="lab-1")), [])

    def test_a_context_naming_another_account_finds_nothing(self) -> None:
        self.records.run("alice-earlier", organization="lab-1", owner="alice", events=(
            ("anomaly", "2", "침전이 생겼어"),))
        later = self.records.run("alice-later", organization="lab-1", owner="alice")
        mine = _context(self.fixture, "step-2", organization="lab-1", account="alice")
        wrong = _context(self.fixture, "step-2", organization="lab-1", account="bob")
        self.assertEqual(len(self._history(later).search_sync("침전이 생겼어", limit=3, context=mine)), 1)
        self.assertEqual(self._history(later).search_sync("침전이 생겼어", limit=3, context=wrong), [])

    def test_a_problem_at_the_same_step_in_other_words_is_still_told(self) -> None:
        self.records.run("earlier", organization="lab-1", owner="alice", events=(
            ("anomaly", "3", "탈색이 덜 됐어"),))
        current = self.records.run("now", organization="lab-1", owner="alice")
        hits = self._history(current).search_sync(
            "거품이 생겼어", limit=3,
            context=_context(self.fixture, "step-3", organization="lab-1", account="alice"))
        self.assertEqual([hit.text for hit in hits], ["탈색이 덜 됐어"])
        self.assertEqual(hits[0].detail("matched_by"), "step")


class TierSearchTests(unittest.TestCase):
    """Decision 3: every tier at once; one that fails or is late is skipped, named."""

    def test_unavailable_failed_and_late_sources_are_skipped(self) -> None:
        class Source:
            def __init__(self, tier, *, raise_=None, delay=0.0, hits=()):
                self.tier, self.raise_, self.delay, self.hits = tier, raise_, delay, hits

            async def search(self, query, *, limit, context):
                await asyncio.sleep(self.delay)
                if self.raise_ is not None:
                    raise self.raise_
                return list(self.hits)

        hit = KnowledgeHit("protocol", "1단계", "line", "p.1")
        found = asyncio.run(search_tiers(
            [Source("protocol", hits=(hit,)),
             Source("public", raise_=KnowledgeUnavailable("VOINEY_LAB_PUBLIC_KNOWLEDGE_PROVIDER")),
             Source("history", raise_=RuntimeError("boom")),
             Source("lab", delay=0.5)],
            "q", context=KnowledgeContext(None, None, "p", None, None), timeout_seconds=0.05,
        ))
        self.assertEqual(found.hits, (hit,))
        self.assertEqual(found.unavailable, ("VOINEY_LAB_PUBLIC_KNOWLEDGE_PROVIDER",))
        self.assertIn("history:RuntimeError", found.failed)
        self.assertIn("lab:TimeoutError", found.failed)
        self.assertEqual(set(found.elapsed_ms), {"protocol", "public", "history", "lab"})


if __name__ == "__main__":
    unittest.main()
