"""Lane KO2, decision 8 (2026-10-26): the router's answers speak the house Korean.

Lane SAY moved the translations and the server's own explanations to 해요체 and
short sentences; the router's answer prompt said only "one or two short
sentences". Its answer rules now ask for the Korean a lab member says at the
bench -- 해요체, short sentences, no 합니다체, "~하십시오" or "귀하" -- and say a
protocol's authors ("we") in the third person. Only the prompt's wording
changes: the checks on a written answer (``answer_checks``) are as before.
"""

from __future__ import annotations

import unittest

from voiney_lab import llm_router


class RouterStyleTests(unittest.TestCase):
    def test_the_answer_rules_ask_for_bench_korean(self) -> None:
        prompt = llm_router.ROUTER_SYSTEM_PROMPT
        for asked in ("해요체", "short sentences", "합니다체", "'~하십시오'", "'귀하'",
                      "'이 논문의 저자들은'", "never '우리' or '저희'"):
            with self.subTest(asked=asked):
                self.assertIn(asked, prompt)
        # The router-first prompt carries the same rules.
        self.assertTrue(llm_router.ROUTER_FIRST_SYSTEM_PROMPT.startswith(prompt))

    def test_the_old_rules_stay(self) -> None:
        prompt = llm_router.ROUTER_SYSTEM_PROMPT
        for kept in ("one or two short sentences, at most about 120 characters",
                     "PDF에서 확인할 수 없어요.", "Copy their numbers and units exactly"):
            with self.subTest(kept=kept):
                self.assertIn(kept, prompt)


if __name__ == "__main__":
    unittest.main()
