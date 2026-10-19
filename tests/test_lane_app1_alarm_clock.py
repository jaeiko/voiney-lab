"""Lane APP1, decision 1 (2026-10-19): timer alerts on time, through Android's setAlarmClock.

The official notification plugin schedules with ``setExactAndAllowWhileIdle``,
which Doze holds to about once per nine minutes per app, so the end alert a
minute after the warning could come late. The app now carries a small
Capacitor plugin (``VoineyAlarm``) that uses ``AlarmManager.setAlarmClock`` --
"Alarms set with setAlarmClock() continue to fire normally" in Doze -- posts
the notification itself, and sets its alarms again after a reboot. The
bridge (``static/native-bridge.js``) uses it first and the official plugin
otherwise; ``tests/native_bridge/alarm_clock.test.js`` pins that choice.

These checks read the Android sources (no device, no build): the plugin is
registered, the receivers are declared, the plugin and the bridge agree on
names, and the boot rule (``TimerAlarmPolicy``, plain Java) is run with the
JDK when one is installed. Provider calls: none.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ANDROID_APP = ROOT / "mobile" / "android" / "app" / "src" / "main"
JAVA = ANDROID_APP / "java" / "com" / "voiney" / "lab"
ALARM = JAVA / "alarm"
BRIDGE = ROOT / "src" / "voiney_lab" / "static" / "native-bridge.js"
POLICY_CHECK = ROOT / "tests" / "native_android" / "TimerAlarmPolicyCheck.java"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _bridge_constant(name: str) -> str:
    found = re.search(rf'const {name} = "([^"]+)";', _read(BRIDGE))
    assert found is not None, name
    return found.group(1)


def test_the_plugin_is_registered_and_named_as_the_bridge_calls_it() -> None:
    plugin = _read(ALARM / "TimerAlarmPlugin.java")
    assert re.search(r'@CapacitorPlugin\(\s*name\s*=\s*"VoineyAlarm"', plugin)
    assert _bridge_constant("ALARM_PLUGIN") == "VoineyAlarm"
    for method in ("schedule", "cancel", "pending", "exactAlarmStatus", "openExactAlarmSettings"):
        assert re.search(rf"@PluginMethod\s+public void {method}\(PluginCall call\)", plugin), method
    activity = _read(JAVA / "MainActivity.java")
    assert "registerPlugin(TimerAlarmPlugin.class)" in activity
    # A plugin registered after super.onCreate is not seen by the bridge.
    assert activity.index("registerPlugin(TimerAlarmPlugin.class)") < activity.index("super.onCreate(")


def test_the_alarm_is_an_alarm_clock_and_posts_on_the_bridges_channel() -> None:
    alarms = _read(ALARM / "TimerAlarms.java")
    assert "setAlarmClock(" in alarms
    # The inexact fallback only when exact alarms are not allowed; never the
    # nine-minute-limited exact-while-idle call.
    assert "setExactAndAllowWhileIdle(" not in alarms
    assert "canScheduleExactAlarms()" in alarms
    found = re.search(r'static final String CHANNEL_ID = "([^"]+)";', alarms)
    assert found is not None and found.group(1) == _bridge_constant("CHANNEL_ID")


def test_the_manifest_declares_the_receivers_and_the_permissions() -> None:
    manifest = _read(ANDROID_APP / "AndroidManifest.xml")
    assert 'android:name=".alarm.TimerAlarmReceiver"' in manifest
    boot = re.search(
        r'<receiver[^>]*android:name="\.alarm\.TimerAlarmBootReceiver"[^>]*>(.*?)</receiver>', manifest, re.S)
    assert boot is not None
    for action in (
        "android.intent.action.BOOT_COMPLETED",
        "android.intent.action.MY_PACKAGE_REPLACED",
        "android.app.action.SCHEDULE_EXACT_ALARM_PERMISSION_STATE_CHANGED",
    ):
        assert action in boot.group(1), action
    for permission in ("RECEIVE_BOOT_COMPLETED", "SCHEDULE_EXACT_ALARM", "POST_NOTIFICATIONS"):
        assert f'android:name="android.permission.{permission}"' in manifest, permission
    # USE_EXACT_ALARM is for alarm or timer apps only (Play policy); the
    # person allows exact alarms instead.
    assert "android.permission.USE_EXACT_ALARM" not in manifest


def test_the_boot_rule_with_the_jdk() -> None:
    javac = shutil.which("javac")
    java = shutil.which("java")
    if javac is None or java is None:
        pytest.skip("no JDK on PATH; the rule is plain Java (TimerAlarmPolicy)")
    out = Path(subprocess.run(["mktemp", "-d"], capture_output=True, text=True, check=True).stdout.strip())
    try:
        compiled = subprocess.run(
            [javac, "-d", str(out), str(ALARM / "TimerAlarmPolicy.java"), str(POLICY_CHECK)],
            capture_output=True, text=True, timeout=120)
        assert compiled.returncode == 0, compiled.stderr
        ran = subprocess.run(
            [java, "-cp", str(out), "TimerAlarmPolicyCheck"], capture_output=True, text=True, timeout=60)
        assert ran.returncode == 0, ran.stdout + ran.stderr
        assert "ok" in ran.stdout
    finally:
        shutil.rmtree(out, ignore_errors=True)
