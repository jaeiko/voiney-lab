"""What a student reads or hears uses the screen's plain words, not internal names.

The lane D screen cleanup replaced "리비전" with "버전" and "게이트" with "확인
조건" on the page, but the server's own sentences still said them, and two
blocked-step sentences named the development fixture "Candidate A". Those
sentences are in ``curated_protocol.py``, so a scan of its string literals
holds the wording for every branch, including blocked steps that a test
fixture cannot easily reach. Reviewer-only readiness messages
(``experiment_protocol.py``, ``protocol_catalog.py``) are not covered here.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from voiney_lab import curated_protocol
from voiney_lab.curated_protocol import CuratedProtocolSession
from voiney_lab.runtime_routing import route_curated_runtime_turn

from tests.protocol_vocabulary_support import miniprep_fixture

INTERNAL_WORDS = ("Candidate A", "후보 A", "게이트", "리비전")


def _plan(utterance: str):
    session = CuratedProtocolSession(miniprep_fixture())
    session.active = True
    return route_curated_runtime_turn(session, utterance, turn_id=1, language="ko").plan


class StudentFacingWordingTests(unittest.TestCase):
    def test_the_version_answer_says_version(self) -> None:
        plan = _plan("프로토콜 버전 알려줘")
        self.assertIn("실행 버전 fictional-miniprep-v1입니다.", plan.speech_text)
        self.assertIn("- 실행 버전: fictional-miniprep-v1", plan.display_text)
        for word in INTERNAL_WORDS:
            self.assertNotIn(word, plan.speech_text)
            self.assertNotIn(word, plan.display_text)

    def test_the_hypothetical_completion_answer_says_confirmation_conditions(self) -> None:
        plan = _plan("끝났다고 하면 어떻게 돼?")
        self.assertIn("원문 완료 확인 조건과 관찰 확인 조건을 먼저 검사합니다.", plan.speech_text)
        self.assertNotIn("게이트", plan.speech_text)
        self.assertFalse(plan.state_changed)

    def test_no_korean_sentence_in_the_module_uses_an_internal_word(self) -> None:
        source = Path(curated_protocol.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        docstrings = {
            id(node.body[0].value)
            for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(getattr(node.body[0], "value", None), ast.Constant)
        }
        found = [
            (node.lineno, node.value[:60])
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
            and any(word in node.value for word in INTERNAL_WORDS)
        ]
        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()
