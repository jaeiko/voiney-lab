"""Installed launcher resources and the manifests must use the product symbol."""

from __future__ import annotations

import json
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "mobile/android/app/src/main/res"
INK = (28, 28, 26, 255)
ANDROID = "{http://schemas.android.com/apk/res/android}"


@pytest.mark.parametrize("density,size", [("mdpi", 48), ("hdpi", 72), ("xhdpi", 96), ("xxhdpi", 144), ("xxxhdpi", 192)])
def test_android_launcher_layers_and_legacy_sizes(density: str, size: int) -> None:
    folder = RES / f"mipmap-{density}"
    for name in ("ic_launcher", "ic_launcher_round", "ic_launcher_foreground"):
        with Image.open(folder / f"{name}.png") as source:
            image = source.convert("RGBA")
        expected = size * 108 // 48 if name.endswith("foreground") else size
        assert image.size == (expected, expected)
        assert image.getpixel((0, 0)) == ((0, 0, 0, 0) if name.endswith("foreground") or name.endswith("round") else INK)
        colours = image.get_flattened_data()
        assert any(r == g == b == 255 and a == 255 for r, g, b, a in colours)
        if name.endswith("foreground"):
            for y in range(expected):
                for x in range(expected):
                    if image.getpixel((x, y))[3]:
                        assert (x - expected/2) ** 2 + (y - expected/2) ** 2 <= (expected * 33 / 108) ** 2


@pytest.mark.parametrize("manifest,base", [("src/voiney_lab/static/manifest.webmanifest", "src/voiney_lab/static"), ("web/public/manifest.webmanifest", "web/public")])
def test_manifests_have_a_separate_opaque_maskable_plate(manifest: str, base: str) -> None:
    data = json.loads((ROOT / manifest).read_text())
    assert data["name"] == data["short_name"] == "Voiney Lab"
    maskable = [icon for icon in data["icons"] if "maskable" in icon.get("purpose", "").split()]
    assert maskable
    for icon in maskable:
        assert icon["purpose"] == "maskable"
        path = ROOT / base / icon["src"].lstrip("/")
        with Image.open(path) as source:
            image = source.convert("RGBA")
        assert image.size == (512, 512)
        assert image.getchannel("A").getextrema() == (255, 255)
        assert image.getpixel((0, 0)) == INK
        # Everything except the full-bleed background fits the central 80% circle.
        for y in range(512):
            for x in range(512):
                if image.getpixel((x, y)) != INK:
                    assert (x - 256) ** 2 + (y - 256) ** 2 <= (512 * .4) ** 2


def test_android_splash_and_adaptive_resources_are_wired() -> None:
    for name in ("ic_launcher", "ic_launcher_round"):
        icon = ET.parse(RES / f"mipmap-anydpi-v26/{name}.xml").getroot()
        assert icon.find("background").get(ANDROID + "drawable") == "@color/ic_launcher_background"
        assert icon.find("foreground").get(ANDROID + "drawable") == "@mipmap/ic_launcher_foreground"
    colours = ET.parse(RES / "values/ic_launcher_background.xml").getroot()
    assert colours.find("color").text.upper() == "#1C1C1A"
    style = ET.parse(RES / "values/styles.xml").getroot().find("style[@name='AppTheme.NoActionBarLaunch']")
    items = {item.get("name"): item.text for item in style}
    assert items["windowSplashScreenBackground"] == "@color/ic_launcher_background"
    assert items["windowSplashScreenAnimatedIcon"] == "@drawable/ic_splash_logo"
    for path in RES.glob("drawable*/splash.png"):
        with Image.open(path) as source:
            assert source.convert("RGBA").getpixel((0, 0)) == INK, path


def test_notification_icons_are_white_product_vectors() -> None:
    paths = ET.parse(ROOT / "assets/brand/voiney-lab_symbol_white.svg").getroot().findall(".//{http://www.w3.org/2000/svg}path")
    for name in ("ic_stat_voiney_mic", "ic_stat_voiney_timer", "ic_stat_voiney"):
        vector = ET.parse(RES / f"drawable/{name}.xml").getroot()
        actual = vector.findall(".//path")
        assert [p.get(ANDROID + "pathData") for p in actual] == [p.get("d") for p in paths]
        assert all(p.get(ANDROID + "strokeColor") == "#FFFFFFFF" for p in actual)
        assert all(p.get(ANDROID + "fillColor") == "#00000000" for p in actual)


@pytest.mark.parametrize("prefix", ["", "/app"])
def test_served_manifest_resolves_the_real_maskable_file(prefix: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient
    from voiney_lab import server

    shutil.copytree(ROOT / "web/public", tmp_path / "build")
    (tmp_path / "build/index.html").write_text("<html>synthetic build</html>")
    monkeypatch.setattr(server, "NEW_UI_DIR", tmp_path / "build")
    with TestClient(server.app) as client:
        response = client.get(f"{prefix}/manifest.webmanifest")
        assert response.status_code == 200
        icon = next(icon for icon in response.json()["icons"] if icon["purpose"] == "maskable")
        url = icon["src"] if icon["src"].startswith("/") else f"{prefix}/{icon['src']}"
        served = client.get(url)
        assert served.status_code == 200
        assert served.content == (ROOT / "assets/brand/voiney-lab_maskable_512.png").read_bytes()
