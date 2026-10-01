"""In-gel search wording is used only where the protocol has the substance.

``plan_research_query`` searched every protocol's questions with the in-gel
document's labels ("Solution A ammonium bicarbonate acetonitrile") and ended
every query with "role in in-gel digestion". A label is now used only where
the active protocol has every substance it names; otherwise the query names
the protocol's own materials, and it says what the protocol is from its title.

The server passes the title, the step and the evidence. The in-gel tests
build those exactly as ``server.py`` does for a related question, so they
show the query the server sends today is unchanged.
"""

from __future__ import annotations

import unittest

from voiney_lab.curated_protocol import CuratedProtocolAction, CuratedProtocolSession
from voiney_lab.external_references import plan_research_query
from voiney_lab.runtime_routing import route_curated_runtime_turn

from tests.protocol_vocabulary_support import (
    MINIPREP_STEPS,
    SOURCE_PDF,
    build_fixture,
    in_gel_fixture,
    miniprep_fixture,
)

#: The labels and ending plan_research_query used for every protocol before.
_LABELS_BEFORE = {
    "ambic": "ammonium bicarbonate AMBIC",
    "hplc_water": "HPLC grade water",
    "solution_a": "Solution A ammonium bicarbonate acetonitrile",
    "solution_b": "Solution B ammonium bicarbonate",
    "acetonitrile": "acetonitrile",
    "gel_plug": "gel plug in-gel digestion",
    "stained_protein_band": "stained protein band SDS-PAGE gel",
    "dtt": "DTT dithiothreitol",
    "dithiothreitol": "DTT dithiothreitol",
    "iodoacetamide": "iodoacetamide",
    "trypsin": "trypsin protease digestion",
}

QUESTIONS = (
    "AMBIC가 뭐야?",
    "HPLC water가 뭐야?",
    "Solution A가 뭐야?",
    "Solution B 구성은 뭐야?",
    "acetonitrile이 뭐야?",
    "gel plug이 뭐야?",
    "stained protein band가 뭐야?",
    "DTT가 뭐야?",
    "iodoacetamide가 뭐야?",
    "trypsin이 뭐야?",
    "원심분리기가 뭐야?",
)


def _query_before(question: str, entities: tuple[str, ...]) -> str:
    entity = "; ".join(_LABELS_BEFORE.get(item, item) for item in entities) or "laboratory protocol"
    return f"{entity} role in in-gel digestion. Question: {question.strip()[:180]}"


def _server_arguments(session: CuratedProtocolSession, transcript: str, turn_id: int):
    """What server.py hands plan_research_query for a related question."""

    plan = route_curated_runtime_turn(
        session, transcript, turn_id=turn_id, language="ko"
    ).plan
    if plan.action is not CuratedProtocolAction.RELATED_QUESTION:
        return None
    step = session.fixture.steps[session.current_index]
    facts = plan.facts or session.related_facts(transcript)
    question = session.reference_query_for(transcript, plan)
    return question, dict(
        protocol_title=session.fixture.title,
        step_label=step.source_label,
        step_text=step.instruction_source_text,
        evidence_texts=tuple(fact.text for fact in facts),
        requested_entity=plan.requested_entity,
        requested_entities=plan.requested_entities,
        question_kind=plan.question_kind,
        question_dimensions=plan.unresolved_dimensions or plan.question_dimensions,
    )


@unittest.skipUnless(
    SOURCE_PDF.is_file(),
    f"requires the externally licensed Candidate A source PDF at {SOURCE_PDF}",
)
class InGelResearchQueryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = in_gel_fixture()

    def test_the_query_the_server_sends_is_unchanged_at_every_step(self) -> None:
        checked = 0
        for index, step in enumerate(self.fixture.steps):
            for turn_id, transcript in enumerate(QUESTIONS, 1):
                session = CuratedProtocolSession(self.fixture)
                session.active = True
                session.current_index = index
                built = _server_arguments(session, transcript, turn_id)
                if built is None:
                    continue
                question, arguments = built
                entities = arguments["requested_entities"] or (
                    (arguments["requested_entity"],) if arguments["requested_entity"] else ()
                )
                with self.subTest(step=step.source_label, transcript=transcript):
                    expected = _query_before(question, entities)
                    self.assertEqual(plan_research_query(question, **arguments), expected)
                    # Judged on the whole protocol, as research_scope() lets
                    # the server do, the entity questions read the same.
                    self.assertEqual(
                        plan_research_query(question, **arguments, **session.research_scope()),
                        expected,
                    )
                    checked += 1
        self.assertGreater(checked, 200)

    def test_in_gel_still_says_in_gel_digestion(self) -> None:
        query = plan_research_query(
            "AMBIC가 뭐야?", protocol_title=self.fixture.title, step_label="2",
            step_text="", evidence_texts=(), requested_entity=None,
            question_kind=None,
        )
        self.assertEqual(
            query, "laboratory protocol role in in-gel digestion. Question: AMBIC가 뭐야?"
        )


class OtherProtocolResearchQueryTests(unittest.TestCase):
    def _miniprep_query(self, question: str, *entities: str, **scope) -> str:
        return plan_research_query(
            question,
            protocol_title="Fictional plasmid miniprep",
            step_label="1",
            step_text=MINIPREP_STEPS[0],
            evidence_texts=(MINIPREP_STEPS[0],),
            requested_entity=entities[0] if entities else None,
            requested_entities=entities,
            question_kind="related_knowledge",
            **scope,
        )

    def test_a_protocol_without_the_substance_gets_no_in_gel_label(self) -> None:
        scope = CuratedProtocolSession(miniprep_fixture()).research_scope()
        for entity, label in _LABELS_BEFORE.items():
            with self.subTest(entity=entity):
                for arguments in ({}, scope):
                    query = self._miniprep_query("이건 뭐야?", entity, **arguments)
                    self.assertNotIn(label, query)
                    self.assertNotIn("in-gel", query)
                    self.assertTrue(query.startswith(
                        "laboratory protocol role in fictional plasmid miniprep."
                    ))

    def test_otherwise_the_query_names_the_protocols_own_materials(self) -> None:
        scope = CuratedProtocolSession(miniprep_fixture()).research_scope()
        named = self._miniprep_query("Tris-HCl buffer는 왜 넣어?", **scope)
        self.assertEqual(
            named,
            "Tris-HCl buffer role in fictional plasmid miniprep. "
            "Question: Tris-HCl buffer는 왜 넣어?",
        )
        # An in-gel entity carried over from earlier is replaced, not kept.
        carried = self._miniprep_query("lysozyme 대신 AMBIC 써도 돼?", "ambic", **scope)
        self.assertTrue(carried.startswith("lysozyme role in fictional plasmid miniprep."))

    def test_a_label_follows_its_own_substances(self) -> None:
        fixture = build_fixture(
            protocol_id="fictional-rinse",
            title="Fictional column rinse",
            steps=(
                "1 Rinse the column with 1 mL acetonitrile.",
                "2 Prepare Solution A by dissolving the salt in water.",
            ),
        )
        scope = CuratedProtocolSession(fixture).research_scope()

        def query(entity: str) -> str:
            return plan_research_query(
                "이건 뭐야?", protocol_title=fixture.title, step_label="1",
                step_text="", evidence_texts=(), requested_entity=entity,
                question_kind=None, **scope,
            )

        self.assertTrue(query("acetonitrile").startswith(
            "acetonitrile role in fictional column rinse."
        ))
        # This Solution A is not in-gel's AMBIC/acetonitrile mix.
        self.assertNotIn("ammonium bicarbonate", query("solution_a"))
        # General lab vocabulary is searched as it always was.
        self.assertTrue(query("centrifuge").startswith("centrifuge role in"))

    def test_the_context_comes_from_the_title(self) -> None:
        def context(title: str) -> str:
            return plan_research_query(
                "q", protocol_title=title, step_label="1", step_text="",
                evidence_texts=(), requested_entity=None, question_kind=None,
            )

        self.assertEqual(context("Bradford protein assay"), "laboratory protocol role in bradford protein assay. Question: q")
        self.assertEqual(context("PCR protocol for colony screening"), "laboratory protocol role in PCR. Question: q")
        self.assertEqual(context(""), "laboratory protocol role in a laboratory protocol. Question: q")


if __name__ == "__main__":
    unittest.main()
