"""Lane CF, decision 6 (2026-10-08): no "approved protocol" in the voice.

Lane DI removed the reviewer and the approval of protocols, so the
voice no longer says a protocol's evidence, requirement or completion check is
"승인된". Those sentences say "원문" or "프로토콜" instead, with the same
meaning. "승인" left in the voice names the organization's approved safety
documents (the SOP corpus, ``safety_pack.py`` and the approved-document
retrieval), which still exist, or the person's own confirmation of a report.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "src/voiney_lab"
REVIEW_SENSE = (
    "승인된 근거", "승인된 요구사항", "승인된 완료", "승인된 실험 프로토콜",
    "진입 승인", "승인 값", "승인할 수 없습니다",
)


class ApprovalWordingTests(unittest.TestCase):

    def test_no_voice_sentence_speaks_of_an_approved_protocol(self) -> None:
        for name in ("curated_protocol.py", "brain.py", "cascade_filler.py"):
            text = (SOURCE / name).read_text(encoding="utf-8")
            for phrase in REVIEW_SENSE:
                with self.subTest(file=name, phrase=phrase):
                    self.assertNotIn(phrase, text)

    def test_what_remains_names_the_safety_documents(self) -> None:
        text = (SOURCE / "curated_protocol.py").read_text(encoding="utf-8")
        left = re.findall(r"[^\"\n]{0,12}승인[^\"\n]{0,12}", text)
        for line in left:
            with self.subTest(line=line):
                self.assertRegex(line, r"승인\s*(?:자료|참고자료|되거나)|승인된 참고자료")


if __name__ == "__main__":
    unittest.main()
