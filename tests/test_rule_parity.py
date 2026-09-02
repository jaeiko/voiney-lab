"""Every enforced rule is declared, and every declared rule is enforced."""

from __future__ import annotations

import ast
import pathlib
import unittest

from voice_workflow_agent import protocol_claim_analysis
from voice_workflow_agent.protocol_claim_analysis import (
    CLAIM_ANALYSIS_SYSTEM_PROMPT,
    CLAIM_RESPONSE_SCHEMA,
    _STABLE_ID,
    _VALUE_UNITS,
    segment_carries_unit_bearing_value,
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


def _stated(phrase: str) -> bool:
    """Is this rule stated in the prompt, regardless of line wrapping?

    Parity is about what the prompt says, not how it is filled to 80 columns.
    Matching raw text made a reflow look like a deleted rule.
    """

    return " ".join(phrase.split()) in " ".join(
        CLAIM_ANALYSIS_SYSTEM_PROMPT.split()
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
        self.assertTrue(_stated(_STABLE_ID.pattern))
        self.assertTrue(_stated("must not hold a space"))

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
                self.assertTrue(_stated(phrase), f"prompt omits: {phrase}")

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
                self.assertTrue(_stated(phrase), f"prompt omits: {phrase}")


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


# Every reason code the validator can raise, mapped to where the provider is
# told about it. STEP 7 audited parity from a hand-written list of rules, so it
# could only find gaps in rules someone had already thought to list -- and it
# checked that a phrase was *present*, not that the phrase said what the server
# enforces. Both misses let declined_segment_states_a_value through. This table
# is checked against the codes derived from the module source, so a new rule
# cannot land without an entry, and a deleted one cannot leave a stale entry.
#
# PROMPT: the provider avoids it by following a stated instruction.
# SCHEMA: the response schema makes it unrepresentable.
# SERVER: a transport or identity fault the provider cannot cause or avoid by
#         instruction; nothing to declare.
_PROMPT, _SCHEMA, _SERVER = "PROMPT", "SCHEMA", "SERVER"

_RULE_DECLARATIONS: dict[str, tuple[str, tuple[str, ...]]] = {
    "chunk_identity_mismatch": (
        _PROMPT,
        ("Context pages are read-only continuity context",),
    ),
    "coverage_mismatch": (_SCHEMA, ()),
    "declination_malformed": (_SCHEMA, ()),
    "declined_segment_not_on_page": (
        _PROMPT,
        ("listed in that page's declined_evidence_segment_ids",),
    ),
    "declined_segment_states_a_value": (
        _PROMPT,
        (
            "decided by shape, not by meaning",
            "digit immediately followed by one of",
            "none of them may be declined",
        ),
    ),
    "duplicate_declined_segment": (
        _PROMPT,
        ("Never both, and never neither.",),
    ),
    "duplicate_evidence_item_identifier": (
        _PROMPT,
        ("Use stable identifiers across page boundaries",),
    ),
    "evidence_segment_range_invalid": (
        _PROMPT,
        ("adjacent evidence_segment_ids in source order",),
    ),
    "evidence_segment_unknown": (_SERVER, ()),
    "numbered_action_missing": (
        _PROMPT,
        ("Never omit or merge numbered source actions.",),
    ),
    "quote_not_found": (
        _PROMPT,
        ("Never return source_excerpt text.",),
    ),
    "unknown_evidence_handle": (
        _PROMPT,
        ("never calculate,\nderive, normalize, shorten, alter, or invent an identity",),
    ),
    "unsupported_claim_category": (_SCHEMA, ()),
    "unsupported_coverage_status": (_SCHEMA, ()),
    "unsupported_structure_marker": (_SCHEMA, ()),
}


def _enforced_reason_codes() -> set[str]:
    """Reason codes read out of the validator source, not out of memory."""

    source = (
        pathlib.Path(protocol_claim_analysis.__file__).read_text(encoding="utf-8")
    )
    codes: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.keyword)
            and node.arg == "reason_code"
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            codes.add(node.value.value)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "fail"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            codes.add(node.args[0].value)
    return codes


class EnforcedRuleParityTests(unittest.TestCase):
    def test_every_enforced_reason_code_is_accounted_for(self) -> None:
        self.assertEqual(_enforced_reason_codes(), set(_RULE_DECLARATIONS))

    def test_every_prompt_declared_rule_states_its_phrase(self) -> None:
        for code, (where, phrases) in sorted(_RULE_DECLARATIONS.items()):
            if where != _PROMPT:
                continue
            with self.subTest(reason_code=code):
                self.assertTrue(phrases, f"{code} claims PROMPT but lists none")
                for phrase in phrases:
                    self.assertTrue(_stated(phrase), f"prompt omits: {phrase}")

    def test_schema_declared_rules_name_no_prompt_phrase(self) -> None:
        for code, (where, phrases) in sorted(_RULE_DECLARATIONS.items()):
            if where == _PROMPT:
                continue
            with self.subTest(reason_code=code):
                self.assertEqual(phrases, ())


class ValueHonestyParityTests(unittest.TestCase):
    """The rule that failed twice in the whole-document run.

    It was stated in the prompt as "do not decline a segment that states a
    measured value" -- a semantic sentence -- while the server applied a
    syntactic test over a closed unit set. A model reading the prompt could not
    compute the server's answer, and the two diverged on exactly the segments
    that failed: a note reading "more than 2 h between procedures" states no
    value the operator produces, but matches digit-plus-unit.
    """

    def test_the_prompt_lists_every_unit_the_server_enforces(self) -> None:
        for unit in sorted(_VALUE_UNITS):
            with self.subTest(unit=unit):
                self.assertTrue(_stated(unit))

    def test_the_schema_lists_every_unit_the_server_enforces(self) -> None:
        description = CLAIM_RESPONSE_SCHEMA["properties"]["page_coverage"][
            "items"
        ]["properties"]["declined_evidence_segment_ids"]["description"]
        for unit in sorted(_VALUE_UNITS):
            with self.subTest(unit=unit):
                self.assertIn(unit, description)

    def test_the_prompt_no_longer_promises_a_semantic_test(self) -> None:
        self.assertFalse(_stated("states a measured value"))

    def test_the_declining_shapes_that_actually_failed_are_covered(self) -> None:
        """Each string is a segment the run declined and the server refused."""

        for text in (
            "with more than 2 h between procedures",
            "you run it within 2 h of the previous procedure",
            "20 g Na2SO3 4.0 mL alpha-amylase",
        ):
            with self.subTest(text=text):
                self.assertTrue(segment_carries_unit_bearing_value(text))

    def test_the_shapes_the_prompt_calls_exempt_really_are(self) -> None:
        for text in ("Catalog #I1149-5G", "224-1S", "13/40", "page 7"):
            with self.subTest(text=text):
                self.assertFalse(segment_carries_unit_bearing_value(text))


if __name__ == "__main__":
    unittest.main()
