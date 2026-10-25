"""Lane RX0 (2026-10-24): the new screen is served at /app/, and the setting
VOINEY_LAB_NEW_UI decides where / goes.

The build (web/, Vite) lands in src/voiney_lab/static/app/, which git
ignores; the server serves it at /app/ whether or not the setting is on.
With VOINEY_LAB_NEW_UI=true, / goes to /app/ (the query string kept, so an
invite link still opens the signup); with the default false, / is the
current screen as before. A missing build never breaks the server: /app/
says the screen is not built, and / stays the current screen even when the
setting is on. The current screen's files and tests are untouched.

These run against the real app with fastapi's TestClient; the build folder
is pointed at a temporary one so the tests do not depend on node.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from voiney_lab import server

STATIC = Path(server.__file__).with_name("static")


class _Build:
    """A temporary build folder: index.html and one hashed asset, as Vite writes them."""

    def __enter__(self) -> Path:
        self._dir = tempfile.TemporaryDirectory(prefix="rx0-app-")
        root = Path(self._dir.name)
        (root / "assets").mkdir()
        (root / "index.html").write_text(
            '<!doctype html><html lang="ko"><head><title>Voiney Lab</title>'
            '<script type="module" src="/app/assets/index-abc.js"></script></head>'
            '<body><div id="root"></div></body></html>',
            encoding="utf-8",
        )
        (root / "assets" / "index-abc.js").write_text("console.log('rx0')\n", encoding="utf-8")
        # The brand files and the manifest as Vite copies them from web/public/.
        (root / "brand").mkdir()
        (root / "brand" / "voiney-lab_favicon_32.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 16)
        (root / "manifest.webmanifest").write_text(
            '{"name": "Voiney Lab", "start_url": "/app/", "icons": [{"src": "brand/voiney-lab_app-icon_512.png"}]}',
            encoding="utf-8",
        )
        return root

    def __exit__(self, *exc) -> None:
        self._dir.cleanup()


def _client() -> TestClient:
    return TestClient(server.app)


class NewScreenServedTests(unittest.TestCase):
    def test_app_serves_the_built_index_and_its_assets(self) -> None:
        with _Build() as build, patch.object(server, "NEW_UI_DIR", build):
            client = _client()
            page = client.get("/app/")
            self.assertEqual(page.status_code, 200)
            self.assertTrue(page.headers["content-type"].startswith("text/html"))
            self.assertIn("/app/assets/index-abc.js", page.text)
            self.assertIn("no-cache", page.headers.get("cache-control", ""))
            self.assertEqual(client.get("/app/assets/index-abc.js").status_code, 200)

    def test_app_without_a_trailing_slash_goes_to_the_slash_and_keeps_the_query(self) -> None:
        with _Build() as build, patch.object(server, "NEW_UI_DIR", build):
            client = _client()
            answer = client.get("/app?invite=tok-1", follow_redirects=False)
            self.assertIn(answer.status_code, (301, 302, 307, 308))
            self.assertEqual(answer.headers["location"], "/app/?invite=tok-1")

    def test_a_missing_build_is_said_and_never_breaks_the_server(self) -> None:
        with tempfile.TemporaryDirectory() as empty, patch.object(server, "NEW_UI_DIR", Path(empty) / "app"):
            client = _client()
            answer = client.get("/app/")
            self.assertEqual(answer.status_code, 503)
            self.assertTrue(answer.headers["content-type"].startswith("text/html"))
            self.assertIn("아직 준비되지 않았어요", answer.text)
            self.assertIn('href="/index.html"', answer.text)
            self.assertEqual(client.get("/healthz").status_code, 200)

    def test_the_brand_files_and_the_manifest_are_served_from_the_build(self) -> None:
        with _Build() as build, patch.object(server, "NEW_UI_DIR", build):
            client = _client()
            icon = client.get("/app/brand/voiney-lab_favicon_32.png")
            self.assertEqual(icon.status_code, 200)
            self.assertEqual(icon.headers["content-type"], "image/png")
            self.assertIn("max-age=86400", icon.headers.get("cache-control", ""))
            self.assertNotIn("immutable", icon.headers.get("cache-control", ""))
            manifest = client.get("/app/manifest.webmanifest")
            self.assertEqual(manifest.status_code, 200)
            self.assertTrue(manifest.headers["content-type"].startswith("application/manifest+json"))
            self.assertEqual(manifest.json()["name"], "Voiney Lab")
            self.assertIn("no-cache", manifest.headers.get("cache-control", ""))

    def test_a_name_that_leaves_its_folder_or_is_missing_is_not_served(self) -> None:
        with _Build() as build, patch.object(server, "NEW_UI_DIR", build):
            client = _client()
            # ".." (encoded, so the client sends it as the one segment) resolves to the build folder itself.
            self.assertEqual(client.get("/app/brand/%2e%2e").status_code, 404)
            self.assertEqual(client.get("/app/assets/%2e%2e").status_code, 404)
            self.assertEqual(client.get("/app/brand/missing.png").status_code, 404)
            self.assertEqual(client.get("/app/brand/").status_code, 404)
            # The guard itself, for a name a client could not even send as one segment.
            for folder, name in (("brand", "../index.html"), ("assets", "../brand/voiney-lab_favicon_32.png"), ("", "../"), ("brand", "")):
                with self.assertRaises(server.HTTPException) as caught:
                    server._new_ui_file(folder, name)
                self.assertEqual(caught.exception.status_code, 404)

    def test_the_repository_holds_the_brand_files_the_screen_names(self) -> None:
        public = Path(server.__file__).resolve().parents[2] / "web" / "public"
        for name in (
            "voiney-lab_logo_ink.svg",
            "voiney-lab_logo_white.svg",
            "voiney-lab_symbol_ink.svg",
            "voiney-lab_symbol_white.svg",
            "voiney-lab_favicon_32.png",
            "voiney-lab_app-icon_180.png",
            "voiney-lab_app-icon_512.png",
        ):
            self.assertTrue((public / "brand" / name).is_file(), name)
        # No company logo (voiney_*) on the new screen: the product logo only.
        self.assertEqual([p.name for p in (public / "brand").iterdir() if not p.name.startswith("voiney-lab_")], [])
        manifest = json.loads((public / "manifest.webmanifest").read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], "Voiney Lab")
        self.assertEqual(manifest["start_url"], "/app/")
        for icon in manifest["icons"]:
            self.assertTrue((public / icon["src"]).is_file(), icon["src"])
        shell = (public.parent / "index.html").read_text(encoding="utf-8")
        self.assertIn('href="/brand/voiney-lab_favicon_32.png"', shell)
        self.assertIn('href="/manifest.webmanifest"', shell)

    def test_the_build_folder_is_the_static_app_folder_and_git_ignores_it(self) -> None:
        self.assertEqual(server.NEW_UI_DIR, STATIC / "app")
        ignored = (Path(server.__file__).resolve().parents[2] / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn("/src/voiney_lab/static/app/", ignored)


class RootSettingTests(unittest.TestCase):
    def test_by_default_the_root_is_the_current_screen(self) -> None:
        with _Build() as build, patch.object(server, "NEW_UI_DIR", build), patch.dict(os.environ, {"VOINEY_LAB_NEW_UI": ""}):
            client = _client()
            page = client.get("/", follow_redirects=False)
            self.assertEqual(page.status_code, 200)
            self.assertIn('<link rel="manifest" href="/manifest.webmanifest">', page.text)

    def test_with_the_setting_on_the_root_goes_to_the_new_screen_with_its_query(self) -> None:
        with _Build() as build, patch.object(server, "NEW_UI_DIR", build), patch.dict(os.environ, {"VOINEY_LAB_NEW_UI": "true"}):
            client = _client()
            answer = client.get("/", follow_redirects=False)
            self.assertEqual(answer.status_code, 302)
            self.assertEqual(answer.headers["location"], "/app/")
            invite = client.get("/?invite=tok-9", follow_redirects=False)
            self.assertEqual(invite.headers["location"], "/app/?invite=tok-9")
            # Followed, it is the built page.
            self.assertIn("/app/assets/index-abc.js", client.get("/").text)

    def test_with_the_setting_on_but_no_build_the_root_stays_the_current_screen(self) -> None:
        with tempfile.TemporaryDirectory() as empty, patch.object(server, "NEW_UI_DIR", Path(empty) / "app"), patch.dict(os.environ, {"VOINEY_LAB_NEW_UI": "true"}):
            client = _client()
            page = client.get("/", follow_redirects=False)
            self.assertEqual(page.status_code, 200)
            self.assertIn('<link rel="manifest" href="/manifest.webmanifest">', page.text)

    def test_a_misspelled_setting_is_refused_by_name(self) -> None:
        with _Build() as build, patch.object(server, "NEW_UI_DIR", build), patch.dict(os.environ, {"VOINEY_LAB_NEW_UI": "maybe"}):
            client = _client()
            answer = client.get("/", follow_redirects=False)
            self.assertEqual(answer.status_code, 503)
            self.assertEqual(answer.json()["fields"], ["VOINEY_LAB_NEW_UI"])

    def test_the_current_screens_other_files_are_served_as_before(self) -> None:
        with patch.dict(os.environ, {"VOINEY_LAB_NEW_UI": "true"}):
            client = _client()
            self.assertEqual(client.get("/index.html").status_code, 200)
            self.assertEqual(client.get("/app.css").status_code, 200)
            self.assertEqual(client.get("/connection.js").status_code, 200)


WEB = Path(server.__file__).resolve().parents[2] / "web"
PALETTE = {
    "#1c1c1a", "#55554f", "#ffffff", "#efefec", "#d4d4cf", "#76766f",
    "#bf2229", "#fbeaea", "#f2a900", "#7a4f00", "#fff4d6", "#5b3cc4", "#efebfb",
}


class DesignSystemRulesTests(unittest.TestCase):
    """The person's design system (2026-10-25; docs/UI_SPEC.md section 6), read off the stylesheets themselves."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tokens = (WEB / "src" / "styles" / "tokens.css").read_text(encoding="utf-8")
        cls.base = (WEB / "src" / "styles" / "base.css").read_text(encoding="utf-8")
        cls.css = cls.tokens + "\n" + cls.base

    def test_colour_is_ink_paper_and_the_three_state_families_only(self) -> None:
        colours = {match.lower() for match in re.findall(r"#[0-9a-fA-F]{3,8}\b", self.css)}
        self.assertEqual(colours - PALETTE, set())
        self.assertIsNone(re.search(r"--ok\b|--source\b|--ai\b|--public\b|:\s*(green|red|blue|purple)\b", self.css))

    def test_the_danger_colour_is_kept_for_stop_and_hazard(self) -> None:
        self.assertIn("--danger: #bf2229", self.tokens)
        self.assertNotIn("--danger", self.base)

    def test_corners_are_at_most_4px_and_never_a_pill(self) -> None:
        for value in re.findall(r"--radius[a-z-]*:\s*(\d+)px", self.tokens):
            self.assertLessEqual(int(value), 4)
        for value in re.findall(r"border-radius:\s*([^;]+);", self.base):
            self.assertRegex(value.strip(), r"^var\(--radius(-small)?\)$")

    def test_no_shadow(self) -> None:
        self.assertIsNone(re.search(r"box-shadow|text-shadow|drop-shadow", self.css))

    def test_pretendard_at_400_600_700_on_the_desk_scale(self) -> None:
        self.assertIn('"Pretendard Variable"', self.tokens)
        self.assertEqual({int(w) for w in re.findall(r"font-weight:\s*(\d+)", self.css)} - {400, 600, 700}, set())
        for pair in (
            r"--text-title: 1\.5rem;\s+--line-title: 2rem;",
            r"--text-subtitle: 1\.125rem;\s+--line-subtitle: 1\.625rem;",
            r"--text-body: 0\.9375rem;\s+--line-body: 1\.5rem;",
            r"--text-label: 0\.8125rem;\s+--line-label: 1\.125rem;",
        ):
            self.assertRegex(self.tokens, pair)

    def test_spacing_in_steps_of_4_borders_and_a_desk_target_of_40px_or_more(self) -> None:
        for value in re.findall(r"--space-\d+: (\d+)px", self.tokens):
            self.assertEqual(int(value) % 4, 0)
        self.assertGreaterEqual(int(re.search(r"--target: (\d+)px", self.tokens).group(1)), 40)
        self.assertRegex(self.tokens, r"--border: 1px;\s+--border-state: 2px;\s+--border-focus: 3px;")


if __name__ == "__main__":
    unittest.main()
