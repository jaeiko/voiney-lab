"""Lane TIDY, item 5: the documentation map names every document in docs/.

``docs/DOCUMENTATION_INDEX.md`` is where a reader finds what to read; a
document it does not link is found only by chance (lane PLAN's
``docs/ADAPTED_RUN_PLAN.md`` was missing). Archives are listed by folder; a
person's own untracked file is not a document of the repository.
"""

from __future__ import annotations

import re
import subprocess

from tests.test_docs_consistency import ROOT, USER_OWNED

INDEX = "docs/DOCUMENTATION_INDEX.md"


def test_the_documentation_map_links_every_document_in_docs() -> None:
    linked = set(re.findall(r"\]\(([^)#\s]+)", (ROOT / INDEX).read_text(encoding="utf-8")))
    tracked = subprocess.run(
        ["git", "ls-files", "--cached", "docs"], cwd=ROOT, capture_output=True, text=True,
        check=True).stdout.splitlines()
    documents = [
        path for path in tracked
        if re.fullmatch(r"docs/[^/]+\.md", path) and path != INDEX and path not in USER_OWNED
    ]
    assert "docs/ADAPTED_RUN_PLAN.md" in documents
    assert [path for path in documents if path[len("docs/"):] not in linked] == []
