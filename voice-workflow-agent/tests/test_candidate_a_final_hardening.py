"""Final real-voice regressions for Candidate A's source-safe Cascade path."""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from voice_workflow_agent.curated_protocol import (
    CuratedProtocolAction,
    CuratedProtocolSession,
    ProtocolKnowledgeView,
    load_curated_protocol_fixture,
    normalize_scientific_request,
)
from voice_workflow_agent.multi_brain import activation_for
from voice_workflow_agent.server import (
    SttDiagnosticSettings,
    _stt_multipart,
    persist_stt_diagnostic,
)


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/development_protocols"
SOURCE_PDF = Path("/home/student/protocol-test-files/in-gel-digestion.pdf")


class CandidateAFinalHardeningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = load_curated_protocol_fixture(
            DATA / "candidate_a_curated_analysis.json",
            DATA / "candidate_a_curated_analysis.provenance.json",
            SOURCE_PDF,
        )

    def session(self, step_label: str = "2") -> CuratedProtocolSession:
        session = CuratedProtocolSession(self.fixture)
        session.active = True
        session.current_index = next(
            index for index, step in enumerate(self.fixture.steps)
            if step.source_label == step_label
        )
        return session

    def test_protocol_wide_stt_terms_and_documented_multipart_order(self) -> None:
        terms = self.session().stt_keyterms()
        for expected in (
            "AMBIC", "ammonium bicarbonate", "HPLC water", "DTT",
            "iodoacetamide", "trypsin", "formic acid", "LC-MS",
            "SDS-PAGE", "gel plug", "stained protein band", "Thermomixer",
            "rpm", "keratin", "contamination", "Evotip", "완료",
        ):
            self.assertIn(expected, terms)
        parts, wav, bounded = _stt_multipart(
            b"\0\0", language="ko", keyterms=terms
        )
        self.assertEqual(parts[0], ("format", (None, "true")))
        self.assertEqual(parts[1], ("language", (None, "ko")))
        self.assertEqual(parts[-1][0], "file")
        self.assertEqual(tuple(item[1][1] for item in parts[2:-1]), bounded)
        self.assertTrue(wav.startswith(b"RIFF"))
        self.assertLessEqual(len(bounded), 100)

    def test_opt_in_stt_diagnostic_is_sanitized_and_bounded(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "data/runtime") as directory:
            settings = SttDiagnosticSettings(True, Path(directory), 2)
            for index in range(3):
                result = persist_stt_diagnostic(
                    b"\0\0",
                    {
                        "turn_id": index,
                        "raw_transcript": "AMBIC가 뭐야?",
                        "normalized_transcript": "ambic가 뭐야",
                        "keyterms": ["AMBIC"],
                        "secret": "must-not-survive",
                    },
                    identity=f"offline-{index}", settings=settings,
                )
                self.assertIsNotNone(result)
            records = sorted(Path(directory).glob("*.json"))
            self.assertEqual(len(records), 2)
            payload = json.loads(records[-1].read_text(encoding="utf-8"))
            self.assertNotIn("secret", payload)
            self.assertEqual(payload["raw_transcript"], "AMBIC가 뭐야?")
            self.assertTrue(payload["wav_sha256"])

    def test_protocol_knowledge_view_uses_exact_pdf_wide_evidence(self) -> None:
        view = ProtocolKnowledgeView.from_fixture(self.fixture)
        self.assertIn("in-gel digests", view.purpose.text)
        self.assertIn("Evotips", view.purpose.text)
        self.assertEqual(view.purpose.source_page, 2)
        self.assertGreater(len(view.materials), 5)
        self.assertGreaterEqual(len(view.equipment), 1)
        session = self.session()
        opening = session.state()
        plan = session.plan(
            "이 프로토콜의 목적이 뭐야?", turn_id=1, language="ko"
        )
        self.assertEqual(plan.action, CuratedProtocolAction.PROTOCOL_QUERY)
        self.assertIn("질량분석", plan.display_text)
        self.assertIn(view.purpose.text, plan.source_texts)
        self.assertEqual(session.state(), opening)

    def test_bounded_repair_never_rewrites_numbers_or_solution_labels(self) -> None:
        normalized, entities, _, corrections = normalize_scientific_request(
            "AM BIC와 HPLG water가 뭐야? 800 rpm, Solution A, 37도",
            entity_inventory=("ambic", "hplc water", "solution a"),
        )
        self.assertEqual(
            entities, ("ambic", "hplc_water", "rpm", "solution_a")
        )
        self.assertIn("800", normalized)
        self.assertIn("37", normalized)
        self.assertIn("solution a", normalized)
        self.assertIn(("am bic", "AMBIC"), corrections)
        self.assertIn(("hplg", "HPLC"), corrections)

    def test_corrupted_start_requests_confirmation_without_mutation(self) -> None:
        session = self.session("1")
        opening = session.state()
        plan = session.plan(
            "투 루를 시작해 줘", turn_id=1, language="ko"
        )
        self.assertEqual(
            plan.action, CuratedProtocolAction.TRANSCRIPT_UNRELIABLE
        )
        self.assertEqual(plan.intent_kind, "ambiguous_protocol_start")
        self.assertIn("프로토콜을 시작해 줘", plan.display_text)
        self.assertIsNotNone(plan.transcript_correction_note)
        self.assertEqual(session.state(), opening)

    def test_bounded_coreference_resolves_one_focus_and_asks_on_ambiguity(self) -> None:
        session = self.session()
        opening = session.state()
        first = session.plan("HPLC water가 뭐야?", turn_id=1, language="ko")
        follow = session.plan(
            "그거는 일반 물하고 뭐가 다른데?", turn_id=2, language="ko"
        )
        self.assertEqual(first.requested_entities, ("hplc_water",))
        self.assertEqual(follow.requested_entities, ("hplc_water",))
        self.assertEqual(follow.coreference_status, "resolved")
        self.assertIn("difference", follow.unresolved_dimensions)
        self.assertEqual(session.state(), opening)

        ambiguous = self.session()
        ambiguous.plan(
            "Solution A와 Solution B의 차이가 뭐야?", turn_id=1,
            language="ko",
        )
        clarification = ambiguous.plan(
            "그 용액은 왜 써?", turn_id=2, language="ko"
        )
        self.assertEqual(
            clarification.action, CuratedProtocolAction.CLARIFY_REFERENCE
        )
        self.assertIn("Solution A", clarification.display_text)
        self.assertIn("Solution B", clarification.display_text)

    def test_step_three_labels_source_approved_alternative(self) -> None:
        session = self.session("3")
        opening = session.state()
        plan = session.plan(
            "3단계를 좀 더 자세히 설명해 줘", turn_id=1, language="ko"
        )
        self.assertEqual(plan.action, CuratedProtocolAction.FULL_DETAIL)
        self.assertIn("원문이 허용한 대안", plan.display_text)
        self.assertIn("SOURCE_APPROVED_ALTERNATIVE", plan.source_plan_scopes)
        self.assertIn("500", plan.display_text)
        self.assertIn("1000", plan.display_text)
        self.assertIn("Thermomixer", plan.display_text)
        self.assertEqual(session.state(), opening)

    def test_source_observation_gates_steps_7_9_and_20(self) -> None:
        cases = (
            ("7", "젤이 완전히 탈색되어 투명해요", "아직 색이 남아 있어요"),
            ("9", "젤이 흰색으로 변했고 탈수됐어요", "아직 투명해요"),
            ("20", "젤이 흰색으로 변했고 탈수됐어요", "아직 투명해요"),
        )
        for label, positive, negative in cases:
            with self.subTest(step=label, observation="positive"):
                session = self.session(label)
                opening_index = session.current_index
                ask = session.plan(
                    "현재 단계를 완료했어요", turn_id=1, language="ko",
                    configuration_id=7, generation=11,
                )
                self.assertEqual(
                    ask.action, CuratedProtocolAction.CLARIFY_COMPLETION
                )
                self.assertIsNotNone(session.pending_observation_confirmation)
                accepted = session.plan(
                    positive, turn_id=2, language="ko",
                    configuration_id=7, generation=11,
                )
                self.assertTrue(accepted.state_changed)
                self.assertTrue(accepted.reported_observation)
                self.assertEqual(accepted.observation_predicate, "positive")
                self.assertEqual(session.current_index, opening_index + 1)
            with self.subTest(step=label, observation="negative"):
                session = self.session(label)
                opening_index = session.current_index
                session.plan(
                    "현재 단계를 완료했어요", turn_id=1, language="ko",
                    configuration_id=7, generation=11,
                )
                rejected = session.plan(
                    negative, turn_id=2, language="ko",
                    configuration_id=7, generation=11,
                )
                self.assertFalse(rejected.state_changed)
                self.assertTrue(rejected.reported_observation)
                self.assertEqual(rejected.observation_predicate, "negative")
                self.assertEqual(session.current_index, opening_index)
                if label == "7":
                    self.assertIn("2–7단계 반복", rejected.display_text)
                elif label == "9":
                    self.assertIn("8–9단계 반복", rejected.display_text)
                else:
                    self.assertIn("17–18단계 반복", rejected.display_text)
                    self.assertIn("미해결", rejected.display_text)

    def test_pending_completion_accepts_progression_only_while_owned(self) -> None:
        replies = (
            "다음 단계로 이동할게", "넘어갈게", "넘어가자", "진행해",
            "이동해 줘", "옮겨", "yes, move on", "let's continue",
            "proceed", "go to the next step",
        )
        for index, reply in enumerate(replies, 1):
            with self.subTest(reply=reply):
                language = "en" if re.search(r"[a-z]", reply) else "ko"
                session = self.session("2")
                opening = session.current_index
                session.plan(
                    "Guide me to the next step."
                    if language == "en" else "다음 단계로 안내해 줘.",
                    turn_id=1, language=language,
                    configuration_id=index, generation=4,
                )
                accepted = session.plan(
                    reply, turn_id=2, language=language,
                    configuration_id=index, generation=4,
                )
                self.assertTrue(accepted.state_changed)
                self.assertEqual(session.current_index, opening + 1)

                unowned = self.session("2")
                rejected = unowned.plan(
                    reply, turn_id=1, language=language,
                    configuration_id=index, generation=4,
                )
                self.assertFalse(rejected.state_changed)
                self.assertEqual(unowned.current_index, opening)

    def test_stale_observation_confirmation_never_advances(self) -> None:
        session = self.session("7")
        opening = session.current_index
        session.plan(
            "현재 단계를 완료했어요", turn_id=1, language="ko",
            configuration_id=7, generation=11,
        )
        stale = session.plan(
            "젤이 투명해요", turn_id=3, language="ko",
            configuration_id=7, generation=11,
        )
        self.assertFalse(stale.state_changed)
        self.assertEqual(session.current_index, opening)
        self.assertIsNone(session.pending_observation_confirmation)

    def test_multi_brain_activation_is_conditional(self) -> None:
        deterministic = activation_for(
            intent_kind="workflow_command", visual_requested=False,
            unresolved_dimensions=(),
        )
        local = activation_for(
            intent_kind="protocol_entity_question", visual_requested=False,
            unresolved_dimensions=(),
        )
        complex_visual = activation_for(
            intent_kind="protocol_entity_question", visual_requested=True,
            unresolved_dimensions=("difference",),
        )
        self.assertEqual(deterministic.roles, ())
        self.assertEqual(local.roles, ("answer",))
        self.assertEqual(
            complex_visual.roles, ("answer", "source", "visual")
        )


if __name__ == "__main__":
    unittest.main()
