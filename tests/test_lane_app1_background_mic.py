"""Lane APP1, decision 2 (2026-10-19): listening with the screen off.

Android does not let a background app use the microphone; a ``microphone``
foreground service started while the app is visible may keep recording.
The app's ``VoineyMicrophone`` plugin runs one (``ListeningService``) with
the notification line "Voiney Lab 이 듣는 중 · 끄기", records 16 kHz mono
PCM16 -- the format the web screen sends on ``/ws`` (``voiney_lab.audio``)
-- and hands it to the page, which sends it on its one socket. The bridge
side is pinned by ``tests/native_bridge/background_mic.test.js``.

These checks read the Android sources and the bridge (no device): the
format agrees in all three places, the service is declared with the
microphone type and its permissions, and the plugin is registered.
Provider calls: none.
"""

from __future__ import annotations

import re
from pathlib import Path

from voiney_lab import audio

ROOT = Path(__file__).resolve().parents[1]
ANDROID_APP = ROOT / "mobile" / "android" / "app" / "src" / "main"
JAVA = ANDROID_APP / "java" / "com" / "voiney" / "lab"
MIC = JAVA / "mic"
BRIDGE = ROOT / "src" / "voiney_lab" / "static" / "native-bridge.js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _java_int(source: str, name: str) -> int:
    found = re.search(rf"static final int {name} = (\d+);", source)
    assert found is not None, name
    return int(found.group(1))


def test_the_format_is_the_servers_in_the_bridge_and_in_the_service() -> None:
    found = re.search(
        r"MIC_FORMAT = Object\.freeze\(\{ sampleRate: (\d+), frameMs: (\d+), frameBytes: (\d+) \}\)", _read(BRIDGE))
    assert found is not None
    assert tuple(map(int, found.groups())) == (audio.SAMPLE_RATE, audio.FRAME_MS, audio.FRAME_BYTES)
    service = _read(MIC / "ListeningService.java")
    assert _java_int(service, "SAMPLE_RATE") == audio.SAMPLE_RATE
    assert _java_int(service, "FRAME_MS") == audio.FRAME_MS
    assert _java_int(service, "FRAME_BYTES") == audio.FRAME_BYTES
    assert audio.CHANNELS == 1 and audio.SAMPLE_WIDTH == 2
    assert "AudioFormat.CHANNEL_IN_MONO" in service
    assert "AudioFormat.ENCODING_PCM_16BIT" in service
    # The voice-call source: the device's echo cancellation and noise
    # suppression, as the web screen asks getUserMedia for them.
    assert "MediaRecorder.AudioSource.VOICE_COMMUNICATION" in service


def test_the_service_is_a_microphone_foreground_service_with_its_words() -> None:
    manifest = _read(ANDROID_APP / "AndroidManifest.xml")
    service = re.search(r'<service[^>]*android:name="\.mic\.ListeningService"[^>]*/?>', manifest, re.S)
    assert service is not None
    assert 'android:foregroundServiceType="microphone"' in service.group(0)
    assert 'android:exported="false"' in service.group(0)
    for permission in ("FOREGROUND_SERVICE", "FOREGROUND_SERVICE_MICROPHONE", "RECORD_AUDIO", "POST_NOTIFICATIONS"):
        assert f'android:name="android.permission.{permission}"' in manifest, permission
    source = _read(MIC / "ListeningService.java")
    assert "ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE" in source
    assert '"Voiney Lab 이 듣는 중"' in source
    assert '"끄기"' in source


def test_the_plugin_is_registered_named_as_the_bridge_calls_it_and_asks_for_the_microphone() -> None:
    plugin = _read(MIC / "BackgroundMicPlugin.java")
    assert re.search(r'@CapacitorPlugin\(\s*name\s*=\s*"VoineyMicrophone"', plugin)
    assert re.search(r'const MIC_PLUGIN = "VoineyMicrophone";', _read(BRIDGE))
    assert "Manifest.permission.RECORD_AUDIO" in plugin
    for method in ("start", "stop", "status", "openAppSettings"):
        assert re.search(rf"@PluginMethod\s+public void {method}\(PluginCall call\)", plugin), method
    # Started only while the app is on screen (Android refuses otherwise).
    assert "Lifecycle.State.STARTED" in plugin
    for reason in ("permission_denied", "not_visible"):
        assert f'"{reason}"' in plugin, reason
    activity = _read(JAVA / "MainActivity.java")
    assert "registerPlugin(BackgroundMicPlugin.class)" in activity
    assert activity.index("registerPlugin(BackgroundMicPlugin.class)") < activity.index("super.onCreate(")


def test_no_recording_without_a_page_to_send_it() -> None:
    """The service stops when the screen's activity goes away: nothing records with no socket to carry it."""

    plugin = _read(MIC / "BackgroundMicPlugin.java")
    destroy = re.search(r"protected void handleOnDestroy\(\)\s*\{(.*?)\n    \}", plugin, re.S)
    assert destroy is not None
    assert "ListeningService.requestStop(" in destroy.group(1)
