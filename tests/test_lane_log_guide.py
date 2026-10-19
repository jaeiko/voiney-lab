"""Lane LOG, decision 5: docs/PILOT_GUIDE.md says what is measured, where it is, and how it goes.

The guide's section 3-3 defines every column the export and the admin screen
show, names the file it is kept in and the delete tool that removes it, and
the draft notice (section 4) tells participants that usage metrics are kept.
These tests keep the guide from falling behind the code.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from voiney_lab.pilot_metrics import (
    ANALYSIS_COLUMNS,
    DAY_COLUMNS,
    EXPERIMENT_COLUMNS,
    PILOT_METRICS_FILENAME,
)

GUIDE = Path(__file__).resolve().parents[1] / "docs" / "PILOT_GUIDE.md"


def _section(text: str, start: str, end: str) -> str:
    return text.split(start, 1)[1].split(end, 1)[0]


class GuideTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = GUIDE.read_text(encoding="utf-8")
        self.metrics = _section(self.text, "### 3-3.", "## 4.")

    def test_every_column_is_defined(self) -> None:
        groups = set(re.findall(r"`([a-z]+)_\*`", self.metrics))
        missing = [
            column for column in dict.fromkeys(EXPERIMENT_COLUMNS + ANALYSIS_COLUMNS + DAY_COLUMNS)
            if f"`{column}`" not in self.metrics and column.split("_")[0] not in groups
        ]
        self.assertEqual(missing, [])

    def test_where_it_is_kept_and_how_it_is_read_and_deleted(self) -> None:
        for words in (
            PILOT_METRICS_FILENAME, "scripts/pilot_metrics.py", "/admin-metrics.html",
            "/api/admin/pilot-metrics", "VOINEY_LAB_ADMIN_TOKEN", "pilot.turn",
            "pilot.metrics.failed", "이번 실험에 도움이 됐나요?",
        ):
            self.assertIn(words, self.metrics, words)
        self.assertIn("삭제 도구", self.metrics)
        table = _section(self.text, "### 3-1.", "### 3-2.")
        self.assertIn(f"workspace/{PILOT_METRICS_FILENAME}", table)

    def test_the_draft_notice_tells_about_the_usage_metrics(self) -> None:
        notice = _section(self.text, "## 4.", "## 5.")
        self.assertIn("사용 지표", notice)
        self.assertIn("말씀하신", notice)


if __name__ == "__main__":
    unittest.main()
