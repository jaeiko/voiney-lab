"""The LLM router and the semantic-intent fallback are never both on (lane M1, decision 4).

One turn is decided along one line: the front rules, then the router, then
the server's validation. With the router off the rules decide and the
semantic fallback may propose for a catch-all. Both on would let two models
each read the same turn, so the server refuses to start and says why.
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

from voiney_lab.llm_router import TwoTurnDecidersError, refuse_two_turn_deciders

ROOT = Path(__file__).resolve().parents[1]
BOTH = {
    "VOINEY_LAB_LLM_ROUTER_ENABLED": "true",
    "VOINEY_LAB_SEMANTIC_INTENT_ENABLED": "true",
}


class OneTurnOnePathTests(unittest.TestCase):
    def test_both_on_is_refused_with_the_reason(self) -> None:
        with self.assertRaises(TwoTurnDecidersError) as caught:
            refuse_two_turn_deciders(BOTH)
        message = str(caught.exception)
        self.assertIn("VOINEY_LAB_LLM_ROUTER_ENABLED", message)
        self.assertIn("VOINEY_LAB_SEMANTIC_INTENT_ENABLED", message)
        self.assertIn("한 턴은 한 경로만 판단합니다", message)

    def test_either_alone_or_neither_starts(self) -> None:
        for environment in (
            {},
            {"VOINEY_LAB_LLM_ROUTER_ENABLED": "true"},
            {"VOINEY_LAB_SEMANTIC_INTENT_ENABLED": "true"},
            {**BOTH, "VOINEY_LAB_SEMANTIC_INTENT_ENABLED": "false"},
        ):
            with self.subTest(environment=environment):
                refuse_two_turn_deciders(environment)

    def test_the_server_does_not_start(self) -> None:
        environment = dict(os.environ)
        environment.update(BOTH)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        result = subprocess.run(
            [sys.executable, "-c", "import voiney_lab.server"],
            cwd=ROOT, env=environment, capture_output=True, text=True, timeout=120,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("TwoTurnDecidersError", result.stderr)
        self.assertIn("한 턴은 한 경로만 판단합니다", result.stderr)


if __name__ == "__main__":
    unittest.main()
