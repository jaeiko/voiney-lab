"""The old screen's script as the browser runs it (lane RX2, 2026-10-25).

Since the person's decision 4 of 2026-10-25 the voice, socket, timer-clock
and screen-lock code lives in ``static/voice-core.js``, which ``index.html``
loads just before its own ``<script>``. A Node harness that runs the page's
script runs both, in that order, as one program -- classic scripts share
their top-level names, so this is what the browser does.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "voiney_lab" / "static"
INDEX = STATIC / "index.html"
VOICE_CORE = STATIC / "voice-core.js"


def inline_script(html: str) -> str:
    """The page's own ``<script>`` block."""
    return html.split("<script>", 1)[1].split("</script>", 1)[0]


def page_script(html: str | None = None) -> str:
    """voice-core.js and then the page's own script: what the page runs."""
    if html is None:
        html = INDEX.read_text(encoding="utf-8")
    return VOICE_CORE.read_text(encoding="utf-8") + "\n" + inline_script(html)
