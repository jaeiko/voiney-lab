// Lane APP1, decision 1: the timer alerts go to our own alarm-clock plugin
// (Android setAlarmClock, on time in Doze) when the app has it and exact
// alarms are allowed, and to the official notification plugin otherwise.
// Run with `node --test tests/native_bridge/*.test.js`; tests/test_native_bridge.py
// runs the same from pytest.
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const bridge = require(path.join(__dirname, "..", "..", "src", "voiney_lab", "static", "native-bridge.js"));

const T0 = Date.parse("2026-10-19T03:00:00+00:00");
const iso = (ms) => new Date(ms).toISOString().replace("Z", "+00:00");

function runningState(timerOverrides) {
  const timer = {
    state: "running",
    duration_seconds: 900,
    remaining_seconds: 900,
    step_id: "step-4",
    step_label: "4",
    started_at: iso(T0),
    deadline_at: iso(T0 + 900 * 1000),
    name: "15분",
    bound: "exact",
    ...(timerOverrides || {}),
  };
  return { attached: true, active: true, timers: { step: timer }, timer };
}

// The injected Capacitor bridge with both plugins as Android would answer.
function fakeCapacitor({ alarmPlugin = true, exact = "granted" } = {}) {
  const sent = [];
  const alarms = new Map();
  const notifications = new Map();
  const plugins = { LocalNotifications: {} };
  if (alarmPlugin) plugins.VoineyAlarm = {};
  const cap = {
    sent,
    alarms,
    notifications,
    Plugins: plugins,
    getPlatform: () => "android",
    isPluginAvailable: (name) => Object.prototype.hasOwnProperty.call(plugins, name),
    nativePromise: async (plugin, method, options) => {
      sent.push([plugin, method, options]);
      if (!cap.isPluginAvailable(plugin)) throw new Error(`"${plugin}" plugin is not implemented on android`);
      if (plugin === "VoineyAlarm") {
        if (method === "exactAlarmStatus") return { exact_alarm: exact };
        if (method === "pending") return { alarms: [...alarms.values()] };
        if (method === "schedule") {
          options.alarms.forEach((a) => alarms.set(a.id, { id: a.id, at: a.at, kind: a.kind, key: a.key, armed: true }));
          return { scheduled: options.alarms.length, exact: exact === "granted" };
        }
        if (method === "cancel") {
          options.ids.forEach((id) => alarms.delete(id));
          return {};
        }
      }
      if (plugin === "LocalNotifications") {
        if (method === "getPending") return { notifications: [...notifications.values()] };
        if (method === "schedule") {
          options.notifications.forEach((n) => notifications.set(n.id, JSON.parse(JSON.stringify(n))));
          return {};
        }
        if (method === "cancel") {
          options.notifications.forEach((n) => notifications.delete(n.id));
          return {};
        }
        if (method === "requestPermissions") return { display: "granted" };
        if (method === "checkExactNotificationSetting") return { exact_alarm: exact };
      }
      return {};
    },
  };
  return cap;
}

test("with the alarm-clock plugin and exact alarms allowed, the alerts are alarm clocks", async () => {
  const cap = fakeCapacitor();
  const chosen = await bridge.chooseNotifier(cap);
  assert.equal(chosen.kind, "alarm_clock");
  assert.equal(chosen.exactAlarm, "granted");
  const alerts = bridge.createTimerAlerts(chosen.notifier, { now: () => T0 });
  await alerts.sync(runningState(), T0);
  const scheduled = cap.sent.filter(([, method]) => method === "schedule");
  assert.equal(scheduled.length, 1);
  assert.equal(scheduled[0][0], "VoineyAlarm");
  const sentAlarms = scheduled[0][2].alarms;
  assert.deepEqual(sentAlarms.map((a) => [a.kind, a.at]), [["warning", T0 + 14 * 60 * 1000], ["end", T0 + 15 * 60 * 1000]]);
  // Exactly what the plugin needs to post the notification on its own,
  // with the app closed: the words, the channel, our key.
  for (const alarm of sentAlarms) {
    assert.deepEqual(Object.keys(alarm).sort(), ["at", "body", "channelId", "id", "key", "kind", "title"]);
    assert.equal(alarm.channelId, bridge.CHANNEL_ID);
    assert.ok(Number.isInteger(alarm.id) && alarm.id > 0);
  }
  assert.equal(cap.notifications.size, 0, "nothing goes to the official plugin");
  // The same state again changes nothing.
  cap.sent.length = 0;
  await alerts.sync(runningState(), T0);
  assert.deepEqual(cap.sent.filter(([, method]) => method === "schedule" || method === "cancel"), []);
});

test("without the alarm-clock plugin (iOS, the web, an older app) the official plugin is used", async () => {
  const cap = fakeCapacitor({ alarmPlugin: false });
  const chosen = await bridge.chooseNotifier(cap);
  assert.equal(chosen.kind, "local_notifications");
  const alerts = bridge.createTimerAlerts(chosen.notifier, { now: () => T0 });
  await alerts.sync(runningState(), T0);
  assert.equal(cap.notifications.size, 2);
  assert.ok(!cap.sent.some(([plugin]) => plugin === "VoineyAlarm"), "the missing plugin is never called");
});

test("exact alarms not allowed: the official plugin, and the alarm clocks we still hold are taken back", async () => {
  const cap = fakeCapacitor({ exact: "granted" });
  const first = await bridge.chooseNotifier(cap);
  const alerts = bridge.createTimerAlerts(first.notifier, { now: () => T0 });
  await alerts.sync(runningState(), T0);
  assert.equal(cap.alarms.size, 2);
  // The person turns exact alarms off; the next app start chooses again.
  const denied = await bridge.chooseNotifier(fakeCapacitorSharing(cap, "denied"));
  assert.equal(denied.kind, "local_notifications");
  assert.equal(denied.exactAlarm, "denied");
  await alerts.setNotifier(denied.notifier);
  await alerts.sync(runningState(), T0);
  assert.equal(cap.alarms.size, 0, "no double alert: the alarm clocks are cancelled");
  assert.equal(cap.notifications.size, 2);
  // Allowed again later: back to alarm clocks, the notifications are cancelled.
  const again = await bridge.chooseNotifier(fakeCapacitorSharing(cap, "granted"));
  assert.equal(again.kind, "alarm_clock");
  await alerts.setNotifier(again.notifier);
  await alerts.sync(runningState(), T0);
  assert.equal(cap.notifications.size, 0);
  assert.deepEqual([...cap.alarms.values()].map((a) => a.kind).sort(), ["end", "warning"]);
});

// The same device (same pending alarms and notifications) with another
// exact-alarm answer.
function fakeCapacitorSharing(cap, exact) {
  return {
    ...cap,
    nativePromise: async (plugin, method, options) => {
      if (plugin === "VoineyAlarm" && method === "exactAlarmStatus") return { exact_alarm: exact };
      return cap.nativePromise(plugin, method, options);
    },
  };
}

test("an alarm clock the system no longer holds is set again at the next sync", async () => {
  const cap = fakeCapacitor();
  const chosen = await bridge.chooseNotifier(cap);
  const alerts = bridge.createTimerAlerts(chosen.notifier, { now: () => T0 });
  await alerts.sync(runningState(), T0);
  // The plugin's store still lists them, but Android dropped them
  // (exact alarms were taken back and given again): armed is false.
  for (const alarm of cap.alarms.values()) alarm.armed = false;
  cap.sent.length = 0;
  await alerts.sync(runningState(), T0);
  const cancel = cap.sent.find(([plugin, method]) => plugin === "VoineyAlarm" && method === "cancel");
  const schedule = cap.sent.find(([plugin, method]) => plugin === "VoineyAlarm" && method === "schedule");
  assert.equal(cancel[2].ids.length, 2);
  assert.equal(schedule[2].alarms.length, 2);
});

test("the plugin's pending list is read as our own alarms only", () => {
  assert.deepEqual(
    bridge.armedAlarms([
      { id: 11, at: T0, armed: true, key: "a" },
      { id: 12, at: T0 + 1000, armed: false, key: "b" },
      { id: "x", at: T0 },
      null,
      { id: 13 },
    ]).map((item) => [item.id, Number.isNaN(item.at) ? "NaN" : item.at]),
    [[11, T0], [12, "NaN"], [13, "NaN"]],
  );
  assert.deepEqual(bridge.armedAlarms(undefined), []);
});

test("a stopped timer cancels the alarm clocks too, and the plugin never sees a time already gone", async () => {
  let now = T0;
  const cap = fakeCapacitor();
  const chosen = await bridge.chooseNotifier(cap);
  const alerts = bridge.createTimerAlerts(chosen.notifier, { now: () => now });
  await alerts.sync(runningState(), now);
  assert.equal(cap.alarms.size, 2);
  // Half a minute before the end the warning is gone; only the end is left
  // to schedule after a restart of the app.
  now = T0 + 14 * 60 * 1000 + 30 * 1000;
  cap.alarms.clear();
  await alerts.sync(runningState({ remaining_seconds: 30 }), now);
  assert.deepEqual([...cap.alarms.values()].map((a) => a.kind), ["end"]);
  await alerts.sync(runningState({ state: "not_started", started_at: null, deadline_at: null }), now);
  assert.equal(cap.alarms.size, 0);
});

test("the exact-alarm answer comes from the alarm plugin when the app has it", async () => {
  const cap = fakeCapacitor({ exact: "denied" });
  assert.deepEqual(await bridge.prepareNotifications(cap), { display: "granted", exactAlarm: "denied" });
  assert.ok(cap.sent.some(([plugin, method]) => plugin === "VoineyAlarm" && method === "exactAlarmStatus"));
  const without = fakeCapacitor({ alarmPlugin: false, exact: "granted" });
  assert.deepEqual(await bridge.prepareNotifications(without), { display: "granted", exactAlarm: "granted" });
  assert.ok(await bridge.openExactAlarmSettings(cap));
  assert.ok(cap.sent.some(([plugin, method]) => plugin === "VoineyAlarm" && method === "openExactAlarmSettings"));
  assert.ok(await bridge.openExactAlarmSettings(without));
  assert.ok(without.sent.some(([plugin, method]) => plugin === "LocalNotifications" && method === "changeExactNotificationSetting"));
});
