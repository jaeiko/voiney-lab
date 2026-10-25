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

import os
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


if __name__ == "__main__":
    unittest.main()
