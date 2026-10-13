"""Lane UI2, decision 5 (2026-10-13): VoineyLab as a home-screen web app.

The page links a web app manifest -- the name VoineyLab, its icons, the
start address, either orientation -- and the icons, and the app serves the
manifest under its registered type whatever the host's own type table says.
There is no service worker, so nothing is cached and nothing works offline.
A guide in the top bar says how to put the page on the home screen; where
the browser offers to install it, the guide shows one button for that
offer, and it goes away once the page runs from the home screen. Browsers
install a page only from a secure (https) address, which the guide says.
"""

from __future__ import annotations

import json
import struct
import unittest

from fastapi.testclient import TestClient

from tests.test_screen_cleanup import INDEX, run_page_script
from voiney_lab import server

STATIC = INDEX.parent


def _png_size(path) -> tuple[int, int]:
    head = path.read_bytes()[:24]
    assert head[:8] == b"\x89PNG\r\n\x1a\n", path
    return struct.unpack(">II", head[16:24])


class ManifestTests(unittest.TestCase):

    def test_the_manifest_names_voineylab_its_icons_start_and_orientation(self) -> None:
        manifest = json.loads((STATIC / "manifest.webmanifest").read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], "VoineyLab")
        self.assertEqual(manifest["short_name"], "VoineyLab")
        self.assertEqual(manifest["start_url"], "/")
        self.assertEqual(manifest["scope"], "/")
        self.assertEqual(manifest["display"], "standalone")
        self.assertEqual(manifest["orientation"], "any")
        self.assertEqual(manifest["lang"], "ko")
        icons = {(icon["src"], icon.get("purpose", "any")): icon for icon in manifest["icons"]}
        for (src, purpose), size in {("/icons/icon-192.png", "any"): 192, ("/icons/icon-512.png", "any"): 512, ("/icons/icon-maskable-512.png", "maskable"): 512}.items():
            self.assertIn((src, purpose), icons)
            self.assertEqual(icons[(src, purpose)]["sizes"], f"{size}x{size}")
            self.assertEqual(_png_size(STATIC / src.lstrip("/")), (size, size))
        self.assertEqual(_png_size(STATIC / "icons" / "apple-touch-icon.png"), (180, 180))
        self.assertTrue((STATIC / "icons" / "icon.svg").read_text(encoding="utf-8").startswith("<svg"))

    def test_the_page_links_the_manifest_and_the_icons_and_has_no_service_worker(self) -> None:
        html = INDEX.read_text(encoding="utf-8")
        head = html.split("</head>", 1)[0]
        for tag in ('<link rel="manifest" href="/manifest.webmanifest">', '<link rel="apple-touch-icon" href="/icons/apple-touch-icon.png">',
                    '<link rel="icon" href="/icons/icon.svg" type="image/svg+xml">', '<meta name="apple-mobile-web-app-title" content="VoineyLab">',
                    '<meta name="application-name" content="VoineyLab">', "<title>VoineyLab"):
            self.assertIn(tag, head)
        self.assertNotIn("serviceWorker", html)
        self.assertEqual([path.name for path in STATIC.rglob("*") if path.name in {"sw.js", "service-worker.js"}], [])


class ServedTests(unittest.TestCase):

    def test_the_app_serves_the_manifest_under_its_type_and_the_icons(self) -> None:
        client = TestClient(server.app)
        manifest = client.get("/manifest.webmanifest")
        self.assertEqual(manifest.status_code, 200)
        self.assertEqual(manifest.headers["content-type"].split(";")[0], "application/manifest+json")
        self.assertEqual(manifest.json()["name"], "VoineyLab")
        for path, kind in (("/icons/icon-192.png", "image/png"), ("/icons/icon-512.png", "image/png"), ("/icons/icon-maskable-512.png", "image/png"), ("/icons/apple-touch-icon.png", "image/png"), ("/icons/icon.svg", "image/svg+xml")):
            response = client.get(path)
            self.assertEqual(response.status_code, 200, path)
            self.assertEqual(response.headers["content-type"].split(";")[0], kind, path)
        self.assertIn('<link rel="manifest" href="/manifest.webmanifest">', client.get("/").text)


class InstallGuideTests(unittest.TestCase):

    def test_the_browser_offer_is_one_button_and_the_guide_leaves_once_installed(self) -> None:
        result = run_page_script(r"""
node("install-app").hidden=true;node("install-secure").hidden=true;
renderInstallGuide();
assert(node("install-guide").hidden===false&&node("install-app").hidden===true,"the guide or a button without an offer");
let prompted=0,prevented=0;
for(const handler of pageListeners.beforeinstallprompt||[])handler({preventDefault(){prevented++;},prompt:async()=>{prompted++;return{outcome:"accepted"};}});
assert(prevented===1&&node("install-app").hidden===false,"the browser's offer did not become the button");
node("install-app").dispatch("click");await new Promise(resolve=>setTimeout(resolve,0));
assert(prompted===1&&node("install-app").hidden===true,"the button did not use the offer once");
for(const handler of pageListeners.appinstalled||[])handler({});
globalThis.matchMedia=query=>({matches:query==="(display-mode: standalone)"});
renderInstallGuide();
assert(node("install-guide").hidden===true,"the guide stayed in the installed app");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_an_address_that_is_not_secure_is_said_to_be_so(self) -> None:
        result = run_page_script(r"""
globalThis.isSecureContext=false;renderInstallGuide();
assert(node("install-secure").hidden===false&&node("install-secure").textContent.includes("https"),node("install-secure").textContent);
globalThis.isSecureContext=true;renderInstallGuide();
assert(node("install-secure").hidden===true,"a secure address was called insecure");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
