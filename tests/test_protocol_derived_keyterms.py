"""STT keyterms come from the active protocol, not from the in-gel document.

``stt_keyterms()`` used to send the same twenty in-gel terms to every
protocol's speech recognition. Now the terms are the active protocol's own:
its materials and equipment as its steps name them, the labelled reagents and
abbreviations its prose defines, and the in-gel candidates only where the
protocol's text contains them. In-gel therefore sends the same set as before;
a protocol without those words sends none of them.

The keyterms are read where the Cascade runtime reads them --
``cascade_transcription_context`` and the multipart the xAI request is built
from -- and the echo guard is run on what that boundary produced.
"""

from __future__ import annotations

import unittest

from voiney_lab.curated_protocol import (
    CuratedProtocolSession,
    ProtocolKnowledgeView,
    normalize_scientific_request,
)
from voiney_lab.language import Transcription, classify_input_event
from voiney_lab.runtime_routing import route_curated_runtime_turn
from voiney_lab.server import (
    ListenerSession,
    _stt_multipart,
    cascade_transcription_context,
)

from tests.protocol_vocabulary_support import (
    IN_GEL_TERMS,
    SOURCE_PDF,
    build_fixture,
    in_gel_fixture,
    miniprep_fixture,
)

CONTROL_TERMS = (
    "아니", "네", "현재 단계", "이번 단계", "완료", "완료했어",
    "시작", "다음 단계", "다시 알려줘",
)


def _step_tokens(label: str) -> tuple[str, ...]:
    return (f"{label}단계", f"{label} 단계", f"현재 {label}단계", f"이번 {label}단계")


def _boundary_keyterms(curated: CuratedProtocolSession) -> tuple[str, ...]:
    """The keyterms the Cascade runtime puts on the xAI request."""

    session = ListenerSession()
    session.start()
    session.curated_protocol_session = curated
    session.manual_language = "ko"
    session.accept_configuration(1, "cascade", "ko", curated.fixture.protocol_id)
    context = cascade_transcription_context(session, audio_origin="ordinary")
    _, _, bounded = _stt_multipart(b"\0\0", language="ko", keyterms=context.keyterms)
    return bounded


def _session_at(fixture, index: int, *, active: bool = True) -> CuratedProtocolSession:
    session = CuratedProtocolSession(fixture)
    session.active = active
    session.current_index = index
    return session


@unittest.skipUnless(
    SOURCE_PDF.is_file(),
    f"requires the externally licensed Candidate A source PDF at {SOURCE_PDF}",
)
class InGelKeytermTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = in_gel_fixture()

    def test_in_gel_sends_the_same_terms_as_the_old_literal_at_every_step(self) -> None:
        for active in (False, True):
            for index, step in enumerate(self.fixture.steps):
                for control in (False, True):
                    with self.subTest(active=active, step=step.source_label, control=control):
                        session = _session_at(self.fixture, index, active=active)
                        terms = session.stt_keyterms(include_control_terms=control)
                        before = tuple(dict.fromkeys((
                            *IN_GEL_TERMS,
                            *(_step_tokens(step.source_label) if active else ()),
                            *(CONTROL_TERMS if control else ()),
                        )))[:100]
                        self.assertEqual(set(terms), set(before))
                        self.assertEqual(len(terms), len(before))
                        # Only the protocol terms were reordered; the step and
                        # control phrases still follow them, in the same order.
                        self.assertEqual(terms[len(IN_GEL_TERMS):], before[len(IN_GEL_TERMS):])

    def test_the_boundary_sends_the_same_set(self) -> None:
        session = _session_at(self.fixture, 1)
        self.assertEqual(
            set(_boundary_keyterms(session)),
            {*IN_GEL_TERMS, *_step_tokens("2"), *CONTROL_TERMS},
        )

    def test_every_in_gel_term_is_spelled_as_the_in_gel_data_spells_it(self) -> None:
        protocol = self.fixture.draft.protocol
        view = ProtocolKnowledgeView.from_fixture(self.fixture)
        texts = [
            " ".join(text.split()) for text in (
                *(fact.text for index in range(len(self.fixture.steps))
                  for fact in self.fixture.facts_for_step(index)),
                *(section.title_source_text for section in protocol.sections),
                view.purpose.text,
            )
        ]
        for term in _session_at(self.fixture, 0, active=False).stt_keyterms():
            with self.subTest(term=term):
                self.assertTrue(any(term in text for text in texts))

    def test_the_terms_around_the_current_step_come_first(self) -> None:
        at_21 = _session_at(self.fixture, 20).stt_keyterms()
        self.assertEqual(at_21[0], "trypsin")
        at_2 = _session_at(self.fixture, 1).stt_keyterms()
        self.assertLess(at_2.index("Solution A"), at_2.index("trypsin"))
        self.assertNotEqual(at_2, at_21)

    def test_the_echo_guard_decides_as_before_on_in_gel(self) -> None:
        transcripts = (
            "AMBIC, DTT, trypsin, acetonitrile, SDS-PAGE",
            "HPLC water Solution A Solution B gel plug stained protein band",
            "Thermomixer rpm incubation keratin contamination Evotip",
            "AMBIC가 뭐야?",
            "현재 단계 완료했어",
            "2단계 완료",
            "네",
            "trypsin 넣었어",
        )
        session = _session_at(self.fixture, 1)
        now = _boundary_keyterms(session)
        before = tuple(dict.fromkeys((*IN_GEL_TERMS, *_step_tokens("2"), *CONTROL_TERMS)))
        for text in transcripts:
            with self.subTest(text=text):
                self.assertEqual(
                    classify_input_event(Transcription(text, "ko"), keyterms=now),
                    classify_input_event(Transcription(text, "ko"), keyterms=before),
                )
        self.assertEqual(
            classify_input_event(
                Transcription(transcripts[0], "ko"), keyterms=now
            ).reason,
            "keyterm_echo",
        )

    def test_in_gel_never_relied_on_the_empty_inventory_fallback(self) -> None:
        session = CuratedProtocolSession(self.fixture)
        for index in range(len(self.fixture.steps)):
            session.current_index = index
            with self.subTest(step=self.fixture.steps[index].source_label):
                self.assertTrue(session._entity_inventory())


class OtherProtocolKeytermTests(unittest.TestCase):
    def test_a_protocol_without_in_gel_words_gets_none_of_them(self) -> None:
        fixture = miniprep_fixture()
        in_gel = {term.casefold() for term in IN_GEL_TERMS}
        for index, step in enumerate(fixture.steps):
            with self.subTest(step=step.source_label):
                terms = _boundary_keyterms(_session_at(fixture, index))
                self.assertFalse(in_gel & {term.casefold() for term in terms})
                for own in (
                    "Tris-HCl buffer", "lysozyme", "ethanol", "microcentrifuge",
                    "Buffer 1", "PBS", "RT-PCR",
                ):
                    self.assertIn(own, terms)
                for token in (*_step_tokens(step.source_label), *CONTROL_TERMS):
                    self.assertIn(token, terms)

    def test_a_material_row_is_sent_as_the_steps_name_it(self) -> None:
        terms = _session_at(miniprep_fixture(), 0).stt_keyterms()
        # Not "Tris-HCl buffer Sigma-Aldrich Catalog #T1503", and the
        # equipment row loses its BRAND line and model number.
        self.assertIn("Tris-HCl buffer", terms)
        self.assertIn("microcentrifuge", terms)
        self.assertFalse(any("Catalog" in term or "Eppendorf" in term for term in terms))

    def test_an_in_gel_word_is_sent_only_where_the_protocol_uses_it(self) -> None:
        fixture = build_fixture(
            protocol_id="fictional-digest",
            title="Fictional overnight digest",
            steps=(
                "1 Dissolve the sample in 100 µL water.",
                "2 Digest the sample with trypsin overnight.",
            ),
        )
        terms = _session_at(fixture, 0).stt_keyterms()
        self.assertIn("trypsin", terms)
        others = {term.casefold() for term in IN_GEL_TERMS} - {"trypsin"}
        self.assertFalse(others & {term.casefold() for term in terms})

    def test_the_cap_keeps_the_step_and_control_terms_whole(self) -> None:
        long_name = (
            "Exceptionally long imaginary compound designation that keeps "
            "going well beyond fifty characters Catalog #X1"
        )
        fixture = build_fixture(
            protocol_id="fictional-many-reagents",
            title="Fictional reagent inventory",
            steps=("1 Label every reagent tube.", "2 Store the tubes cold."),
            materials=(
                long_name,
                *(f"Compound {index:03d} Sigma-Aldrich Catalog #C{index}" for index in range(130)),
            ),
        )
        session = _session_at(fixture, 1)
        terms = _boundary_keyterms(session)
        self.assertEqual(terms, session.stt_keyterms(include_control_terms=True))
        self.assertEqual(len(terms), 100)
        self.assertTrue(all(1 <= len(term) <= 50 for term in terms))
        for token in (*_step_tokens("2"), *CONTROL_TERMS):
            self.assertIn(token, terms)
        # A row the steps never name keeps its words, cut to fit.
        self.assertIn("Exceptionally long imaginary compound designation", terms)

    def test_the_echo_guard_follows_what_was_sent(self) -> None:
        terms = _boundary_keyterms(_session_at(miniprep_fixture(), 2))
        own_dump = Transcription("Tris-HCl buffer, lysozyme, ethanol, microcentrifuge, PBS", "ko")
        in_gel_dump = Transcription("AMBIC, DTT, trypsin, acetonitrile, SDS-PAGE", "ko")
        self.assertEqual(classify_input_event(own_dump, keyterms=terms).reason, "keyterm_echo")
        # In-gel terms were never put on this protocol's request, so a run of
        # them is not an echo of it.
        self.assertTrue(classify_input_event(in_gel_dump, keyterms=terms).accepted)


class EmptyInventoryTests(unittest.TestCase):
    def test_an_empty_inventory_repairs_nothing_toward_in_gel(self) -> None:
        normalized, entities, note, corrections = normalize_scientific_request(
            "trypsun이 뭐야?"
        )
        self.assertIn("trypsun", normalized)
        self.assertEqual(entities, ())
        self.assertIsNone(note)
        self.assertEqual(corrections, ())

    def test_a_supplied_inventory_still_repairs(self) -> None:
        normalized, entities, _, corrections = normalize_scientific_request(
            "trypsun이 뭐야?", entity_inventory=("trypsin",)
        )
        self.assertIn("trypsin", normalized)
        self.assertEqual(entities, ("trypsin",))
        self.assertIn(("trypsun", "TRYPSIN"), corrections)

    def test_a_protocol_without_trypsin_does_not_hear_it_in_a_near_miss(self) -> None:
        session = _session_at(miniprep_fixture(), 1)
        plan = route_curated_runtime_turn(
            session, "trypsun이 뭐야?", turn_id=1, language="ko"
        ).plan
        self.assertNotIn("trypsin", plan.requested_entities)
        self.assertNotIn("trypsin", str(plan.transcript_correction_note or "").casefold())


if __name__ == "__main__":
    unittest.main()
