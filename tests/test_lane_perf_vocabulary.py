"""Content caches and session preparation at the real voice boundary; no providers."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import threading
import unittest
from unittest.mock import patch

from tests.lane_n_support import VoiceNotesHarness
from tests.protocol_vocabulary_support import miniprep_fixture
from voiney_lab import curated_protocol as cp
from voiney_lab import protocol_translation as pt


class ContentCacheTests(unittest.TestCase):
    def test_vocabulary_reused_after_reloading_same_content(self) -> None:
        fixture = miniprep_fixture()
        first = cp.ProtocolVocabulary.from_fixture(fixture)
        self.assertIs(first, cp.ProtocolVocabulary.from_fixture(replace(fixture)))

    def test_translation_units_reused_after_reloading_same_content(self) -> None:
        fixture = miniprep_fixture()
        first = pt.translation_units(fixture)
        with patch.object(pt.ProtocolVocabulary, "from_fixture", side_effect=AssertionError("rebuilt")):
            self.assertIs(first, pt.translation_units(replace(fixture)))

    def test_changed_analysis_with_same_source_and_revision_does_not_reuse(self) -> None:
        fixture = miniprep_fixture()
        first = cp.ProtocolVocabulary.from_fixture(fixture)
        protocol = fixture.draft.protocol
        section = protocol.sections[0]
        step = replace(section.steps[0], instruction_source_text="1 Add perfmarker reagent.")
        changed = replace(fixture, draft=replace(fixture.draft, protocol=replace(
            protocol, sections=(replace(section, steps=(step, *section.steps[1:])),))))
        self.assertNotEqual(first, cp.ProtocolVocabulary.from_fixture(changed))
        self.assertNotEqual(pt.translation_units(fixture), pt.translation_units(changed))

    def test_glossary_change_does_not_reuse_old_hearing_names(self) -> None:
        fixture = miniprep_fixture()
        first = cp.ProtocolVocabulary.from_fixture(fixture)
        changed = replace(fixture, translation_glossary=(("lysozyme", "라이소자임"),))
        self.assertNotEqual(first.korean_names, cp.ProtocolVocabulary.from_fixture(changed).korean_names)


class PreparedVoiceTests(VoiceNotesHarness, unittest.TestCase):
    def test_first_vocabulary_is_prepared_off_loop_and_turn_only_reads_it(self) -> None:
        real_to_thread = asyncio.to_thread
        real_vocabulary = cp.ProtocolVocabulary.from_fixture
        loop_thread = threading.get_ident()
        computations: list[int] = []

        def compute(fixture):
            computations.append(threading.get_ident())
            self.assertNotEqual(threading.get_ident(), loop_thread, "vocabulary on event loop")
            return real_vocabulary(fixture)

        async def scenario(socket, listener, say):
            self.assertTrue(computations, "session did not prepare vocabulary")
            before = len(computations)
            await say(1, "시작할게요")
            await say(2, "다음")
            self.assertEqual(len(computations), before, "turn rebuilt vocabulary")
            self.assertTrue(socket.for_turn(2, "turn.done"))

        self.voice(
            scenario,
            patch("voiney_lab.server.asyncio.to_thread", side_effect=real_to_thread),
            patch.object(cp.ProtocolVocabulary, "from_fixture", side_effect=compute),
        )
