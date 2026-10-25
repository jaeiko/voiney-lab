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

The logo files are the person's, kept unchanged in ``assets/brand/``; the
screen copies only what it uses into ``static/icons/``: the tab's icon, the
home screen's icons (the manifest's) and the ink logo, which heads the top
bar and the login page. Every icon the manifest lists and the pages link is
there, the size it says, and the same bytes as its original.
"""

from __future__ import annotations

import json
import re
import struct
from pathlib import Path

from tests.test_docs_consistency import FENCE, INLINE_CODE, ROOT, documents, repository_files

STATIC = ROOT / "src" / "voiney_lab" / "static"
BRAND = ROOT / "assets" / "brand"
PAGES = ("index.html", "admin-metrics.html")
#: A page's link to a file under /icons/ (an icon, the logo).
ICON_LINK = re.compile(r'(?:href|src)="/icons/([^"]+)"')
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


def _png_size(path: Path) -> tuple[int, int]:
    head = path.read_bytes()[:24]
    assert head[:8] == b"\x89PNG\r\n\x1a\n", path
    return struct.unpack(">II", head[16:24])


def test_every_icon_the_manifest_and_the_pages_name_is_the_brands_file_and_is_there() -> None:
    manifest = json.loads((STATIC / "manifest.webmanifest").read_text(encoding="utf-8"))
    assert manifest["icons"]
    for icon in manifest["icons"]:
        assert icon["src"].startswith("/icons/"), icon
        path = STATIC / icon["src"].lstrip("/")
        assert path.is_file(), icon["src"]
        if icon["sizes"] == "any":
            assert path.suffix == ".svg", icon
            continue
        width, height = (int(side) for side in icon["sizes"].split("x"))
        assert _png_size(path) == (width, height), icon
    assert "512x512" in {icon["sizes"] for icon in manifest["icons"]}
    linked = set()
    for page in PAGES:
        names = ICON_LINK.findall((STATIC / page).read_text(encoding="utf-8"))
        assert names, page
        linked.update(names)
    assert {"voiney-lab_favicon_32.png", "voiney-lab_app-icon_180.png", "voiney-lab_logo_ink.svg"} <= linked
    on_disk = {path.name for path in (STATIC / "icons").iterdir()}
    used = linked | {icon["src"].rsplit("/", 1)[1] for icon in manifest["icons"]}
    assert used <= on_disk
    assert on_disk <= used, f"icons nothing names: {sorted(on_disk - used)}"
    for name in sorted(on_disk):
        assert (STATIC / "icons" / name).read_bytes() == (BRAND / name).read_bytes(), name
    assert _png_size(STATIC / "icons" / "voiney-lab_favicon_32.png") == (32, 32)
    assert _png_size(STATIC / "icons" / "voiney-lab_app-icon_180.png") == (180, 180)


def test_the_logo_heads_the_top_bar_and_the_login_page() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    head = html.split("</head>", 1)[0]
    assert '<link rel="icon" href="/icons/voiney-lab_favicon_32.png" type="image/png" sizes="32x32">' in head
    assert '<link rel="apple-touch-icon" href="/icons/voiney-lab_app-icon_180.png">' in head
    topbar = html.split('<header class="topbar">', 1)[1].split("</header>", 1)[0]
    assert '<img class="brand-logo" src="/icons/voiney-lab_logo_ink.svg" alt="Voiney Lab"' in topbar
    assert "brand-mark" not in html
    wizard = html.split('<section id="start-wizard"', 1)[1].split("</section>", 1)[0]
    assert '<img class="wizard-logo" src="/icons/voiney-lab_logo_ink.svg" alt="Voiney Lab"' in wizard
    # Only the login's own page shows it; the sheet over the page does not.
    assert ".wizard-logo{display:none}" in head
    assert '.wizard-sheet[data-mode="page"] .wizard-logo{display:block;' in head
