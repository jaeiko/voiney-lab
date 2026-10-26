"""Lane TIDY (2026-10-25): the product name and the screen's one-line intro.

Item 1. Lane NAME wrote the product as "Voiney Lab" on the screen and in the
documents; the files outside its list kept the joined "VoineyLab" as a word
(lane NAME report, 2-2): the settings example's first line and the tool that
writes it, the package descriptions, the store app's README, its two local
pages and two Android comments, and one comment in ``server.py``. A
user-agent token and every identifier keep the name joined.

Item 2 (a person's decision 2026-10-25): the screen does not use the English
subtitle "Lab Copilot". The window's title is the name alone and the line
under the logo is the new screen's one-line intro (``docs/COPY_DECK.md``
1.1, ``web/src/copy/ko.ts`` ``tagline``).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from tests.test_docs_consistency import ROOT, repository_files
from tests.test_lane_name_product_name import JOINED

#: The files lane NAME left (its report, 2-2), each read whole: a comment,
#: a description or a page's words are all read by people.
LEFT_BY_LANE_NAME = (
    ".env.example",
    "scripts/migrate_env.py",
    "pyproject.toml",
    "package.json",
    "mobile/package.json",
    "mobile/README.md",
    "mobile/www-src/offline.html",
    "mobile/www-src/device-check.html",
    "mobile/android/app/src/main/AndroidManifest.xml",
    "mobile/android/app/src/main/res/drawable/ic_stat_voiney_mic.xml",
)
LISTENING_SERVICE = "mobile/android/app/src/main/java/com/voiney/lab/mic/ListeningService.java"
INDEX = ROOT / "src" / "voiney_lab" / "static" / "index.html"
TAGLINE = "장갑을 낀 채 말로 진행하는 실험 안내"


def _places(path: str) -> list[str]:
    text = (ROOT / path).read_text(encoding="utf-8")
    return [f"{path}:{text.count(chr(10), 0, match.start()) + 1}"
            for match in JOINED.finditer(text)]


def test_the_files_lane_name_left_and_the_server_write_voiney_lab_apart() -> None:
    files = set(repository_files())
    assert set(LEFT_BY_LANE_NAME) <= files
    server = [path for path in files if path.startswith("src/voiney_lab/") and path.endswith(".py")]
    assert "src/voiney_lab/server.py" in server
    joined = [place for path in (*LEFT_BY_LANE_NAME, *sorted(server)) for place in _places(path)]
    assert joined == []
    # A user-agent token is an identifier and keeps the name joined.
    assert '"VoineyLab/0.1 (' in (ROOT / "src/voiney_lab/web_explanations.py").read_text(encoding="utf-8")


def test_the_settings_example_opens_with_the_tools_header() -> None:
    tool = ast.parse((ROOT / "scripts/migrate_env.py").read_text(encoding="utf-8"))
    (header,) = [ast.literal_eval(node.value) for node in tool.body if isinstance(node, ast.Assign)
                 and [getattr(target, "id", None) for target in node.targets] == ["HEADER"]]
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert example.startswith(header)
    assert example.startswith("## Voiney Lab 설정. ")


def test_the_device_check_page_quotes_the_notification_line_as_the_app_shows_it() -> None:
    service = (ROOT / LISTENING_SERVICE).read_text(encoding="utf-8")
    title = re.search(r'\.setContentTitle\("([^"]+)"\)', service)
    assert title is not None and title[1] == "Voiney Lab 이 듣는 중"
    page = (ROOT / "mobile/www-src/device-check.html").read_text(encoding="utf-8")
    assert f'(알림 줄 "{title[1]} · 끄기")' in page
    for name in ("offline.html", "device-check.html"):
        head = (ROOT / "mobile/www-src" / name).read_text(encoding="utf-8").split("</title>", 1)[0]
        assert head.endswith(" · Voiney Lab"), name


def test_the_window_title_is_the_name_and_the_logo_line_is_the_copy_decks_intro() -> None:
    html = INDEX.read_text(encoding="utf-8")
    assert "<title>Voiney Lab</title>" in html
    assert "Lab Copilot" not in html and "지능형 실험 보조" not in html
    topbar = html.split('<header class="topbar">', 1)[1].split("</header>", 1)[0]
    assert f"<small>{TAGLINE}</small>" in topbar
    # The same words as the new screen's and the copy deck's.
    assert f'tagline: "{TAGLINE}"' in (ROOT / "web/src/copy/ko.ts").read_text(encoding="utf-8")
    deck = (ROOT / "docs/COPY_DECK.md").read_text(encoding="utf-8")
    row = next(line for line in deck.splitlines() if "지능형 실험 보조" in line)
    assert f"`{TAGLINE}`" in row
