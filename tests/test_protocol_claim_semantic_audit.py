"""No private-use glyph is hardcoded in production, tooling or tests.

What is left of the claim semantic-audit tests: the audit and the claim DTOs
were deleted on 2026-10-13 (lane AN, decision 1; the git history keeps them).
This repository-wide guard never depended on them.
"""

from __future__ import annotations

import unittest
from pathlib import Path


class NoParserCorruptionInSourcesTests(unittest.TestCase):
    """A hardcoded private-use glyph can only have come from a broken parser.

    It matches text no document contains, so a pattern carrying one scores
    corrupted extraction instead of the source, and a replacement carrying one
    silently repairs corruption that should have failed closed.
    """

    def test_production_and_tooling_contain_no_private_use_glyph(self) -> None:
        offenders = []
        for root in (Path("src"), Path("scripts")):
            if not root.exists():
                continue
            for path in sorted(root.rglob("*.py")):
                text = path.read_text(encoding="utf-8")
                for number, line in enumerate(text.splitlines(), 1):
                    for character in line:
                        if 0xE000 <= ord(character) <= 0xF8FF:
                            offenders.append(
                                f"{path.as_posix()}:{number} "
                                f"U+{ord(character):04X}"
                            )
        self.assertEqual(
            offenders,
            [],
            "a private-use glyph is hardcoded in production or tooling; it can "
            "only have come from a broken extraction",
        )

    # Tests may embed a corrupted sample, but only to prove it is detected or
    # repaired.  Any other test file gaining one is a mistake.
    TEST_FILES_ALLOWED = {
        "tests/test_frontend.py",  # asserts the browser normalizer repairs it
    }

    def test_only_named_tests_embed_a_corrupted_sample(self) -> None:
        found = {
            path.as_posix()
            for path in sorted(Path("tests").rglob("*.py"))
            if any(
                0xE000 <= ord(character) <= 0xF8FF
                for character in path.read_text(encoding="utf-8")
            )
        }
        self.assertEqual(found, self.TEST_FILES_ALLOWED)


if __name__ == "__main__":
    unittest.main()
