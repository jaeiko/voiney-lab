"""Lane NAME (2026-10-25, a person's decision): the product is "Voiney Lab".

The company is Voiney and the product Voiney Lab -- two words, and "보이니 랩"
when it is said. The joined "VoineyLab" stays only where a space cannot go: a
code identifier, an address or a file name (``voiney_lab``, ``voiney-lab``,
``com.voiney.lab``, a user-agent token). So the screen's text, the web app
manifest and the documents people read -- README, AGENTS, ``.agent/`` and
``docs/`` outside the archives -- never write it joined as a word. In a
document, code spans and fenced blocks are code and are not read; on the
screen everything is read, a script's strings included. The spoken form is
read in the server's sentences too.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from tests.test_docs_consistency import FENCE, INLINE_CODE, ROOT, documents, repository_files

STATIC = ROOT / "src" / "voiney_lab" / "static"
SCREEN_SUFFIXES = {".html", ".js", ".css", ".svg", ".webmanifest"}
#: "VoineyLab" as a word: not a piece of an identifier, an address or a file name.
JOINED = re.compile(r"(?<![\w./@-])VoineyLab(?![\w/@-]|\.\w)")
JOINED_SPOKEN = re.compile("보이니랩")


def _screen_files() -> list[str]:
    return [
        path for path in repository_files()
        if path.startswith("src/voiney_lab/static/") and Path(path).suffix in SCREEN_SUFFIXES
    ]


def _read_documents() -> list[str]:
    return [path for path in documents() if not path.startswith("mobile/")]


def _server_sources() -> list[str]:
    return [path for path in repository_files() if path.startswith("src/voiney_lab/") and path.endswith(".py")]


def _blank(match: re.Match[str]) -> str:
    return re.sub(r"[^\n]", " ", match.group(0))


def _prose(text: str) -> str:
    """The document with its code blanked out, so line numbers stay."""

    return INLINE_CODE.sub(_blank, FENCE.sub(_blank, text))


def _places(path: str, text: str, pattern: re.Pattern[str]) -> list[str]:
    return [f"{path}:{text.count(chr(10), 0, match.start()) + 1}" for match in pattern.finditer(text)]


def test_the_screen_the_manifest_and_the_documents_write_voiney_lab_apart() -> None:
    screen, read, server = _screen_files(), _read_documents(), _server_sources()
    assert "src/voiney_lab/static/index.html" in screen and "src/voiney_lab/static/manifest.webmanifest" in screen
    assert {"README.md", "AGENTS.md", "docs/FEATURES.md", ".agent/architecture.md"} <= set(read)
    assert not [path for path in read if path.startswith(("docs/archive/", "docs/course-archive/"))]
    joined: list[str] = []
    for path in screen:
        text = (ROOT / path).read_text(encoding="utf-8")
        joined += _places(path, text, JOINED) + _places(path, text, JOINED_SPOKEN)
    for path in read:
        text = _prose((ROOT / path).read_text(encoding="utf-8"))
        joined += _places(path, text, JOINED) + _places(path, text, JOINED_SPOKEN)
    for path in server:
        joined += _places(path, (ROOT / path).read_text(encoding="utf-8"), JOINED_SPOKEN)
    assert joined == []
    manifest = json.loads((STATIC / "manifest.webmanifest").read_text(encoding="utf-8"))
    assert (manifest["name"], manifest["short_name"]) == ("Voiney Lab", "Voiney Lab")


def test_an_identifier_an_address_or_a_file_name_may_keep_the_name_joined() -> None:
    for kept in ("VoineyLabPublicKnowledge/0.1", "VoineyLab/0.1 (Voice Workflow Agent)", "https://x.test/VoineyLab", "VoineyLab.apk", "@VoineyLab"):
        assert not JOINED.search(kept), kept
    for said in ("VoineyLab 설치", "<title>VoineyLab · Lab Copilot</title>", "# 기능 설명 — VoineyLab", "VoineyLab.", "(VoineyLab)"):
        assert JOINED.search(said), said
    assert JOINED.search(_prose("see `VoineyLab` here")) is None
    assert JOINED.search(_prose("```\nDescription=VoineyLab pilot\n```\n")) is None
