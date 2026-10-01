"""An entity answer comes from the active PDF's own statements, for every protocol.

``protocol_answer_envelope`` used to carry reagent explanations written for
one document, the Candidate A in-gel digestion protocol -- "Solution A는 25 mM
AMBIC 수용액 2 parts와 acetonitrile 1 part를 혼합한..." -- and a gate on the
protocol id kept them from other documents. That gate was on a name, not on
content: a fixture carrying Candidate A's id received the in-gel prose
whatever its PDF said, and in-gel itself received sentences its PDF does not
contain (AGENTS.md rule 1).

The prose and the gate are gone. Every protocol, in-gel included, is answered
with what its own statements say about the entity: the steps that name it,
the first of those statements (its reviewed translation where the fixture has
one), and a definition only where the PDF itself writes the long form.
These tests hold that on a fictional document that names in-gel reagents, so
the answer cannot depend on which protocol id it carries.

The fixtures here are fictional and built in memory, so these tests run with or
without the externally licensed source PDF (see the README's two baselines).
"""

from __future__ import annotations

import hashlib
import unittest

from voiney_lab import experiment_protocol as domain
from voiney_lab.curated_protocol import (
    CANDIDATE_A_PROTOCOL_ID,
    CuratedProtocolFixture,
    CuratedProtocolSession,
)
from voiney_lab.experiment_protocol_analysis import ProtocolAnalysisDraft
from voiney_lab.experiment_protocol_pdf import (
    ProtocolPdfExtraction,
    ProtocolPdfMetadata,
    ProtocolPdfPage,
)

OTHER_PROTOCOL_ID = "fictional-other-protocol-v1"

_FIRST = "1. Dissolve AMBIC in HPLC water and add 1 mL to the sample tube."
_SECOND = "2. Add acetonitrile and mix the sample for 30 seconds."
_RATIONALE = "This prepares the sample for mixing."
_WARNING = "Use a clean tube and wear gloves."
_PAGE_ONE = "\n".join(
    ["Fictional bench protocol.", _FIRST, _RATIONALE, _WARNING, _SECOND]
)
_PAGE_TWO = (
    "Abstract\nFictional protocol for gating tests.\n"
    "Protocol materials\nAMBIC and HPLC water\n"
    "Safety warnings\nUse a clean tube and wear gloves.\n"
    "Before start\nConfirm this is a non-operational fixture."
)

#: Phrases that appeared only in the removed Candidate A explanations.
CURATED_MARKERS = (
    "중탄산 암모늄",
    "Solution A",
    "고성능 액체 크로마토그래피",
)
#: The answer quotes the fixture's own first statement naming AMBIC.
SOURCE_MARKER = _FIRST


def _fixture(protocol_id: str) -> CuratedProtocolFixture:
    """One fictional two-step protocol whose text names in-gel reagents.

    Identical apart from ``protocol_id``, so a difference in the answer could
    only come from the protocol's identity -- and there must be none.
    """

    source_text = _PAGE_ONE + "\n" + _PAGE_TWO
    extraction = ProtocolPdfExtraction(
        original_filename="fictional-gating-protocol.pdf",
        byte_size=len(source_text.encode("utf-8")),
        sha256=hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
        media_type="application/pdf",
        page_count=2,
        encrypted=False,
        metadata=ProtocolPdfMetadata(
            title="Fictional gating protocol",
            author="Voice Workflow Agent",
            subject="Non-operational gating fixture",
            creator="voice-workflow-tests",
            producer="voice-workflow-tests",
            creation_date=None,
            modification_date=None,
        ),
        pages=(
            ProtocolPdfPage(1, _PAGE_ONE, False),
            ProtocolPdfPage(2, _PAGE_TWO, False),
        ),
    )
    on_one = lambda text: domain.SourceEvidence(1, text)  # noqa: E731
    on_two = lambda text: domain.SourceEvidence(2, text)  # noqa: E731
    protocol = domain.ExperimentProtocol(
        protocol_id=protocol_id,
        metadata=domain.ProtocolMetadata(
            pdf=extraction,
            title="Fictional gating protocol",
            original_language="en",
            version="1.0-test",
            source_status="fictional_non_operational",
            evidence=on_one("Fictional bench protocol."),
        ),
        materials=(
            domain.Material("m-ambic", "AMBIC", on_two("AMBIC and HPLC water")),
        ),
        equipment=(
            domain.Equipment("e-tube", "sample tube", on_one("sample tube")),
        ),
        before_start=(
            domain.BeforeStartPrerequisite(
                "bs-1",
                "Confirm this is a non-operational fixture.",
                on_two("Confirm this is a non-operational fixture."),
            ),
        ),
        sections=(
            domain.ProtocolSection(
                "s1",
                "Fictional bench protocol.",
                on_one("Fictional bench protocol."),
                steps=(
                    domain.ProtocolSourceStep(
                        "step-1",
                        "1",
                        _FIRST,
                        on_one(_FIRST),
                        notes=(
                            domain.SourceStatement("n1", _RATIONALE, on_one(_RATIONALE)),
                        ),
                        warnings=(
                            domain.SourceStatement("w1", _WARNING, on_one(_WARNING)),
                        ),
                        expected_results=(
                            domain.SourceStatement("r1", _RATIONALE, on_one(_RATIONALE)),
                        ),
                    ),
                    domain.ProtocolSourceStep("step-2", "2", _SECOND, on_one(_SECOND)),
                ),
            ),
        ),
    )
    domain.validate_protocol(protocol)
    draft = ProtocolAnalysisDraft(
        extraction=extraction,
        protocol=protocol,
        readiness=domain.assess_readiness(protocol),
        capability_policy=domain.P1_CAPABILITY_POLICY,
        analysis_schema_version=1,
        verified_evidence_count=10,
    )
    return CuratedProtocolFixture(
        draft=draft,
        status="fictional_non_operational",
        ordered_step_labels=("1", "2"),
        fixture_sha256=hashlib.sha256(protocol_id.encode("utf-8")).hexdigest(),
        revision_id="gating-fixture-v1",
        development_only=True,
        source_filename=extraction.original_filename,
    )


def _answer(protocol_id: str, prompt: str):
    session = CuratedProtocolSession(_fixture(protocol_id))
    session.active = True
    session.current_index = 0
    plan = session.plan(prompt, turn_id=1, language="ko")
    return plan, session.protocol_answer_envelope(plan, language="ko")


class CuratedExplanationGateTests(unittest.TestCase):
    """No protocol id selects prose; the PDF's statements are the answer."""

    def test_both_fixtures_actually_offer_the_entities(self):
        """Guard the guard: a gate proves nothing if the entity never arrives.

        If the inventory stopped resolving these names, every assertion below
        would pass for the wrong reason.
        """

        for protocol_id in (CANDIDATE_A_PROTOCOL_ID, OTHER_PROTOCOL_ID):
            with self.subTest(protocol_id=protocol_id):
                plan, _ = _answer(protocol_id, "AMBIC가 무엇인지 설명해줘.")
                self.assertIn("ambic", plan.requested_entities)

    def test_curated_explanations_do_not_reach_another_protocol(self):
        _, envelope = _answer(OTHER_PROTOCOL_ID, "AMBIC가 무엇인지 설명해줘.")
        for marker in CURATED_MARKERS:
            self.assertNotIn(marker, envelope.direct_answer)
            self.assertNotIn(marker, envelope.speech_summary)
        self.assertIn(SOURCE_MARKER, envelope.direct_answer)
        self.assertIn("1단계", envelope.direct_answer)

    def test_curated_explanations_serve_no_protocol_id(self):
        """Candidate A's id selects nothing: the same PDF gets the same answer."""

        prompt = "AMBIC가 무엇인지 설명해줘."
        _, mine = _answer(CANDIDATE_A_PROTOCOL_ID, prompt)
        _, theirs = _answer(OTHER_PROTOCOL_ID, prompt)
        for marker in (*CURATED_MARKERS, "중탄산"):
            self.assertNotIn(marker, mine.direct_answer)
            self.assertNotIn(marker, mine.speech_summary)
        self.assertEqual(mine.direct_answer, theirs.direct_answer)
        self.assertEqual(mine.speech_summary, theirs.speech_summary)
        self.assertIn(SOURCE_MARKER, mine.direct_answer)

    def test_protocol_relationship_sentence_is_the_shared_statement(self):
        """The AMBIC/HPLC-water relationship is the statement naming both, nothing more."""

        prompt = "AMBIC와 HPLC water가 무엇인지 설명해줘."
        _, mine = _answer(CANDIDATE_A_PROTOCOL_ID, prompt)
        _, theirs = _answer(OTHER_PROTOCOL_ID, prompt)
        for envelope in (mine, theirs):
            for marker in CURATED_MARKERS:
                self.assertNotIn(marker, envelope.direct_answer)
            self.assertIn("AMBIC", envelope.direct_answer)
            self.assertIn("HPLC water", envelope.direct_answer)
            self.assertIn(SOURCE_MARKER, envelope.direct_answer)
        self.assertEqual(mine.direct_answer, theirs.direct_answer)

    def test_gated_answer_is_well_formed_rather_than_empty(self):
        """Emptying the map must route to the fallback, not produce a blank turn.

        An empty answer would be a worse failure than a wrong one: the
        experimenter would hear nothing and learn nothing about why.
        """

        _, envelope = _answer(OTHER_PROTOCOL_ID, "AMBIC가 무엇인지 설명해줘.")
        self.assertTrue(envelope.direct_answer.strip())
        self.assertTrue(envelope.speech_summary.strip())
        self.assertTrue(envelope.entity_sections)
        for label, text in envelope.entity_sections:
            self.assertTrue(label.strip())
            self.assertTrue(text.strip())


if __name__ == "__main__":
    unittest.main()
