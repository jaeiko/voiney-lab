"""Lane APP1, decision 3 (2026-10-19): the app keeps its login token only in the phone's secure store.

``VoineySessionStore`` (mobile/android/.../session/SessionStorePlugin.java)
encrypts the token with an AES-GCM key kept in the Android Keystore; the
app's preferences hold only the ciphertext. Capacitor's debug logging, which
prints every plugin call's options to the device log, is off, so neither the
token nor the microphone's sound reaches logcat. The bridge side is pinned by
``tests/native_bridge/app_session.test.js``. Provider calls: none.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JAVA = ROOT / "mobile" / "android" / "app" / "src" / "main" / "java" / "com" / "voiney" / "lab"
BRIDGE = ROOT / "src" / "voiney_lab" / "static" / "native-bridge.js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_the_token_is_sealed_by_a_keystore_key_and_never_logged() -> None:
    plugin = _read(JAVA / "session" / "SessionStorePlugin.java")
    assert re.search(r'@CapacitorPlugin\(name = "VoineySessionStore"\)', plugin)
    assert re.search(r'const SESSION_PLUGIN = "VoineySessionStore";', _read(BRIDGE))
    assert '"AndroidKeyStore"' in plugin
    assert '"AES/GCM/NoPadding"' in plugin
    assert "KeyProperties.BLOCK_MODE_GCM" in plugin
    for method in ("set", "get", "clear"):
        assert re.search(rf"@PluginMethod\s+public void {method}\(PluginCall call\)", plugin), method
    # Only the sealed form is written; nothing is logged.
    assert re.search(r"putString\(ENTRY, stored\)", plugin)
    assert "putString(ENTRY, token)" not in plugin
    assert not re.search(r"\bLog\.|Logger\.|System\.out", plugin)
    activity = _read(JAVA / "MainActivity.java")
    assert activity.index("registerPlugin(SessionStorePlugin.class)") < activity.index("super.onCreate(")


def test_capacitor_logging_is_off_in_every_build() -> None:
    config = _read(ROOT / "mobile" / "capacitor.config.js")
    assert re.search(r'^\s*loggingBehavior: "none",$', config, re.M)


def test_the_app_never_backs_its_preferences_up() -> None:
    manifest = _read(ROOT / "mobile" / "android" / "app" / "src" / "main" / "AndroidManifest.xml")
    assert 'android:allowBackup="false"' in manifest


def test_the_bridge_never_logs_or_puts_the_token_in_a_url() -> None:
    source = _read(BRIDGE)
    session = source[source.index("function createAppSession("):source.index("function keepAwake(")]
    assert "console." not in session
    assert "?token" not in session and "token=" not in session
