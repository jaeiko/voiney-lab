"""Every enforced rule is declared, and every declared rule is enforced."""

from __future__ import annotations

import unittest

from voice_workflow_agent.protocol_claim_analysis import (
    CLAIM_ANALYSIS_SYSTEM_PROMPT,
    CLAIM_RESPONSE_SCHEMA,
    _STABLE_ID,
)
from voice_workflow_agent.replay_turns import check

# Fields the server validates with _STABLE_ID. A rule enforced here but absent
# from the schema cannot be satisfied by a provider: it was exactly this gap on
# section_id that wasted an authorized call.
_SERVER_VALIDATED_IDENTIFIERS = {
    ("structure", "marker_id"),
    ("structure", "section_id"),
    ("claims", "claim_id"),
    ("claims", "section_id"),
    ("claims", "step_id"),
    ("claims", "target_claim_id"),
}

# Hazard wording must never enter the prompt: what counts as a hazard is the
# provider's judgement, and this contract constrains only position and format.
_FORBIDDEN_PROMPT_WORDS = (
    "danger",
    "corrosive",
    "hazardous",
    "toxic",
    "flammable",
    "caution",
    "irritant",
    "explosive",
    "protective equipment",
    "safety information",
)


def _field_schema(record: str, field: str) -> dict:
    return CLAIM_RESPONSE_SCHEMA["properties"][record]["items"]["properties"][
        field
    ]


def _declared_pattern(schema: dict) -> str | None:
    if "pattern" in schema:
        return schema["pattern"]
    for branch in schema.get("anyOf", ()):
        if branch.get("type") == "string" and "pattern" in branch:
            return branch["pattern"]
    return None


class IdentifierParityTests(unittest.TestCase):
    def test_every_server_validated_identifier_is_declared(self) -> None:
        for record, field in sorted(_SERVER_VALIDATED_IDENTIFIERS):
            with self.subTest(field=f"{record}.{field}"):
                pattern = _declared_pattern(_field_schema(record, field))
                self.assertEqual(
                    pattern,
                    _STABLE_ID.pattern,
                    f"{record}.{field} is enforced with _STABLE_ID but the "
                    "schema does not declare it",
                )

    def test_the_declared_pattern_rejects_a_prose_title(self) -> None:
        """The exact shape of the wasted call: a section's human title."""

        self.assertIsNone(_STABLE_ID.fullmatch("Lignin method in beakers"))
        self.assertIsNotNone(_STABLE_ID.fullmatch("section-lignin-in-beakers"))

    def test_the_prompt_states_the_identifier_charset(self) -> None:
        self.assertIn(_STABLE_ID.pattern, CLAIM_ANALYSIS_SYSTEM_PROMPT)
        self.assertIn("must not hold a space", CLAIM_ANALYSIS_SYSTEM_PROMPT)

    def test_a_free_text_label_is_not_forced_to_be_a_slug(self) -> None:
        """source_label is source text, not an identifier."""

        self.assertIsNone(_declared_pattern(_field_schema("claims", "source_label")))


class AttachmentRuleParityTests(unittest.TestCase):
    def test_the_prompt_states_the_positional_attachment_rule(self) -> None:
        for phrase in (
            "A claim attaches by position.",
            "must target that step's action claim",
            "leaves target_claim_id null",
            "applies to warning_hazard exactly as it does to a quantity",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, CLAIM_ANALYSIS_SYSTEM_PROMPT)

    def test_the_prompt_carries_no_hazard_vocabulary(self) -> None:
        lowered = CLAIM_ANALYSIS_SYSTEM_PROMPT.lower()
        self.assertEqual(
            [word for word in _FORBIDDEN_PROMPT_WORDS if word in lowered], []
        )

    def test_the_prompt_states_the_accounting_count_check(self) -> None:
        for phrase in (
            "the segments you cited plus the segments",
            "at least one letter or digit",
            "decline it if it holds no claim",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, CLAIM_ANALYSIS_SYSTEM_PROMPT)


class ReplayCheckTests(unittest.TestCase):
    """A verification step that cannot fail is not a verification step."""

    BASE = {
        "turn_id": 1,
        "transcript": "왜 해야 돼?",
        "normalized_text": "왜 해야 돼",
        "intent": "learning",
        "confidence": 1.0,
        "runtime_router": "curated_protocol",
        "action": "question",
        "intent_kind": "current_step_learning",
        "answer_origin": "approved_step_metadata",
        "state_mutation": False,
        "step_before": "1",
        "step_after": "1",
        "speech_text": "이 단계의 수행 목적은",
        "tools_used": [],
        "visual_intent": None,
    }

    def test_a_clean_replay_reports_no_problem(self) -> None:
        self.assertEqual(check([self.BASE], 1), [])

    def test_a_short_replay_is_reported(self) -> None:
        self.assertEqual(check([], 7), ["replayed 0 turns, expected 7"])

    def test_a_turn_with_no_route_is_reported(self) -> None:
        self.assertEqual(
            check([{**self.BASE, "runtime_router": ""}], 1),
            ["turn 1 produced no runtime_router"],
        )

    def test_a_mutation_that_did_not_move_the_step_is_reported(self) -> None:
        self.assertEqual(
            check([{**self.BASE, "state_mutation": True}], 1),
            [
                "turn 1 claimed a state mutation but the step did not move",
            ],
        )

    def test_a_silent_turn_is_reported(self) -> None:
        self.assertEqual(
            check([{**self.BASE, "speech_text": "   "}], 1),
            ["turn 1 produced no speech"],
        )

    def test_a_missing_field_is_reported(self) -> None:
        broken = {key: value for key, value in self.BASE.items() if key != "action"}
        self.assertEqual(check([broken], 1), ["turn 1 is missing ['action']"])

    def test_the_real_demo_replay_passes_its_own_check(self) -> None:
        from voice_workflow_agent.replay_turns import (
            DEFAULT_TURNS,
            parse_args,
            replay,
        )

        records = replay(parse_args([]))
        self.assertEqual(check(records, len(DEFAULT_TURNS)), [])
        self.assertEqual(len(records), len(DEFAULT_TURNS))


if __name__ == "__main__":
    unittest.main()
