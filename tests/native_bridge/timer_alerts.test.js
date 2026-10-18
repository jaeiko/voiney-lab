// Lane APP0: the native bridge's notification schedule, pinned without a
// device. Run with `node --test tests/native_bridge/`; tests/test_native_bridge.py
// runs the same from pytest.
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const bridge = require(path.join(__dirname, "..", "..", "src", "voiney_lab", "static", "native-bridge.js"));

const T0 = Date.parse("2026-10-18T03:00:00+00:00");
const iso = (ms) => new Date(ms).toISOString().replace("Z", "+00:00");

function runningState(overrides, timerOverrides) {
  const started = T0;
  const duration = 15 * 60;
  const timer = {
    state: "running",
    duration_seconds: duration,
    remaining_seconds: duration,
    elapsed_seconds: 0,
    step_index: 3,
    step_id: "step-4",
    step_label: "4",
    started_at: iso(started),
    deadline_at: iso(started + duration * 1000),
    name: "15분",
    bound: "exact",
    ...(timerOverrides || {}),
  };
  return {
    attached: true,
    active: true,
    workflow_status: "active",
    timers: { experiment: { state: "running" }, step: timer, pause: { state: "running" } },
    timer,
    ...(overrides || {}),
  };
}

function fakeNotifier() {
  const pending = new Map();
  const calls = [];
  return {
    calls,
    pending: async () => [...pending.values()].map((a) => ({ id: a.id, at: a.at })),
    cancel: async (ids) => {
      calls.push(["cancel", ids]);
      ids.forEach((id) => pending.delete(id));
    },
    schedule: async (alerts) => {
      calls.push(["schedule", alerts.map((a) => a.id)]);
      alerts.forEach((a) => pending.set(a.id, a));
    },
    scheduled: () => [...pending.values()].sort((a, b) => a.at - b.at),
  };
}

test("a running timer gets one minute before and the end, at the server's times", () => {
  const alerts = bridge.planTimerAlerts(runningState(), { nowMs: T0 + 1000 });
  assert.deepEqual(alerts.map((a) => a.kind), ["warning", "end"]);
  assert.equal(alerts[0].at, T0 + 14 * 60 * 1000);
  assert.equal(alerts[1].at, T0 + 15 * 60 * 1000);
  assert.equal(alerts[1].deadline_at, iso(T0 + 15 * 60 * 1000));
  assert.equal(alerts[0].title, "4단계 · 1분 전");
  assert.equal(alerts[1].body, "타이머 15분이 끝났어요. 앱에서 확인해 주세요.");
});

test("ids are stable positive 31-bit integers, distinct for the two alerts", () => {
  const first = bridge.planTimerAlerts(runningState(), { nowMs: T0 });
  const again = bridge.planTimerAlerts(runningState(), { nowMs: T0 + 30 * 1000 });
  assert.deepEqual(first.map((a) => a.id), again.map((a) => a.id));
  assert.notEqual(first[0].id, first[1].id);
  for (const a of first) assert.ok(Number.isInteger(a.id) && a.id > 0 && a.id <= 0x7fffffff);
});

test("nothing is scheduled without a running step timer in an active experiment", () => {
  const cases = [
    null,
    { attached: false },
    runningState({ active: false }),
    runningState({}, { state: "not_started", deadline_at: null, started_at: undefined }),
    runningState({}, { state: "expired" }),
    runningState({}, { deadline_at: "not a time" }),
    runningState({}, { deadline_at: iso(T0 - 1000) }),
  ];
  for (const state of cases) assert.deepEqual(bridge.planTimerAlerts(state, { nowMs: T0 }), []);
});

test("a paused experiment keeps its step timer's notifications (pause does not stop the step timer)", () => {
  const alerts = bridge.planTimerAlerts(runningState({ workflow_status: "paused" }), { nowMs: T0 });
  assert.equal(alerts.length, 2);
});

test("a timer of a minute or less has only the end", () => {
  const state = runningState({}, { duration_seconds: 60, deadline_at: iso(T0 + 60 * 1000), name: "1분" });
  assert.deepEqual(bridge.planTimerAlerts(state, { nowMs: T0 }).map((a) => a.kind), ["end"]);
});

test("a time already passed is not scheduled", () => {
  const nowMs = T0 + 14 * 60 * 1000 + 5000;
  assert.deepEqual(bridge.planTimerAlerts(runningState(), { nowMs }).map((a) => a.kind), ["end"]);
  assert.deepEqual(bridge.planTimerAlerts(runningState(), { nowMs: T0 + 15 * 60 * 1000 }), []);
});

test("minimum and maximum timers are worded as the server words their end", () => {
  const minimum = runningState({}, { duration_seconds: 7200, deadline_at: iso(T0 + 7200 * 1000), name: "최소 2시간", bound: "minimum" });
  const [warnMin, endMin] = bridge.planTimerAlerts(minimum, { nowMs: T0 });
  assert.equal(warnMin.body, "최소 시간 2시간까지 1분 남았어요.");
  assert.equal(endMin.body, "최소 시간 2시간이 지났어요. 앱에서 확인해 주세요.");
  assert.ok(!/끝났/.test(endMin.body), "a minimum's end is not the step's end");
  const maximum = runningState({}, { name: "최대 15분", bound: "maximum" });
  assert.equal(bridge.planTimerAlerts(maximum, { nowMs: T0 })[1].body, "최대 시간 15분이 됐어요. 앱에서 확인해 주세요.");
});

test("a device clock off from the server's is corrected from remaining_seconds", () => {
  const state = runningState({}, { remaining_seconds: 600 });
  // The server said 600 s remain; it arrived when the device clock read
  // deadline - 600 s + 90 s, so the device runs 90 s ahead.
  const deadline = T0 + 15 * 60 * 1000;
  const received = deadline - 600 * 1000 + 90 * 1000;
  assert.equal(bridge.clockOffsetMs(state.timers.step, received), 90 * 1000);
  // Within rounding and latency it is left at zero.
  assert.equal(bridge.clockOffsetMs(state.timers.step, deadline - 600 * 1000 + 1500), 0);
  const alerts = bridge.planTimerAlerts(state, { nowMs: received, offsetMs: 90 * 1000 });
  assert.equal(alerts[1].at, deadline + 90 * 1000);
});

test("diff: the same plan twice changes nothing; a moved time is cancelled and scheduled again", () => {
  const plan = bridge.planTimerAlerts(runningState(), { nowMs: T0 });
  const pending = plan.map((a) => ({ id: a.id, at: a.at }));
  assert.deepEqual(bridge.diffAlerts(pending, plan), { cancel: [], schedule: [] });
  const jitter = pending.map((p) => ({ ...p, at: p.at + 1000 }));
  assert.deepEqual(bridge.diffAlerts(jitter, plan), { cancel: [], schedule: [] });
  const moved = bridge.planTimerAlerts(runningState({}, { deadline_at: iso(T0 + 20 * 60 * 1000) }), { nowMs: T0 });
  const diff = bridge.diffAlerts(pending, moved);
  assert.deepEqual(diff.cancel.sort(), pending.map((p) => p.id).sort());
  assert.deepEqual(diff.schedule.map((a) => a.at), [T0 + 19 * 60 * 1000, T0 + 20 * 60 * 1000]);
});

test("sync follows the server: restart replaces, stop cancels, connection loss keeps", async () => {
  let now = T0 + 1000;
  const notifier = fakeNotifier();
  const alerts = bridge.createTimerAlerts(notifier, { now: () => now });

  await alerts.sync(runningState(), now);
  assert.deepEqual(notifier.scheduled().map((a) => a.kind), ["warning", "end"]);
  const firstIds = notifier.scheduled().map((a) => a.id);

  // The same state again (another turn's echo) is not rescheduled.
  notifier.calls.length = 0;
  await alerts.sync(runningState(), now);
  assert.deepEqual(notifier.calls, []);

  // The connection drops: nothing is cancelled.
  assert.deepEqual(alerts.connectionLost(), { cancel: [], schedule: [] });
  assert.deepEqual(notifier.scheduled().map((a) => a.id), firstIds);

  // The server restarts the timer later: the old pair goes, a new pair comes.
  now = T0 + 5 * 60 * 1000;
  const restarted = runningState({}, { started_at: iso(now), deadline_at: iso(now + 15 * 60 * 1000), remaining_seconds: 900 });
  await alerts.sync(restarted, now);
  const restartedIds = notifier.scheduled().map((a) => a.id);
  assert.equal(restartedIds.filter((id) => firstIds.includes(id)).length, 0);
  assert.equal(notifier.scheduled()[1].at, now + 15 * 60 * 1000);

  // The step moved on without a timer, or the experiment ended: all cancelled.
  await alerts.sync(runningState({}, { state: "not_started", started_at: null, deadline_at: null }), now);
  assert.deepEqual(notifier.scheduled(), []);
  await alerts.sync(restarted, now);
  await alerts.sync(runningState({ active: false }), now);
  assert.deepEqual(notifier.scheduled(), []);
});

test("syncs run one after another", async () => {
  const notifier = fakeNotifier();
  const slowPending = notifier.pending;
  notifier.pending = () => new Promise((resolve) => setTimeout(() => resolve(slowPending()), 20));
  const alerts = bridge.createTimerAlerts(notifier, { now: () => T0 });
  await Promise.all([alerts.sync(runningState(), T0), alerts.sync(runningState(), T0)]);
  assert.equal(notifier.calls.filter((c) => c[0] === "schedule").length, 1);
  assert.equal(notifier.scheduled().length, 2);
});

test("only our own pending notifications are read back; the Capacitor shape round-trips", () => {
  const [warning] = bridge.planTimerAlerts(runningState(), { nowMs: T0 });
  const native = bridge.toCapacitorNotification(warning);
  assert.equal(native.channelId, bridge.CHANNEL_ID);
  assert.equal(native.schedule.allowWhileIdle, true);
  assert.equal(native.schedule.at.getTime(), warning.at);
  // What the plugin hands back from getPending after a JSON round trip.
  const echoed = JSON.parse(JSON.stringify(native));
  const other = { id: 7, title: "x", body: "y", extra: { tag: "something-else" } };
  assert.deepEqual(bridge.ownPending([echoed, other, { id: 8 }]), [{ id: warning.id, at: warning.at }]);
});

test("the Capacitor notifier speaks to the injected bridge", async () => {
  const sent = [];
  const cap = {
    getPlatform: () => "android",
    nativePromise: async (plugin, method, options) => {
      sent.push([plugin, method, options]);
      if (method === "getPending") return { notifications: [] };
      if (method === "requestPermissions") return { display: "granted" };
      if (method === "checkExactNotificationSetting") return { exact_alarm: "denied" };
      return {};
    },
  };
  assert.equal(bridge.capacitorOf({ Capacitor: cap }), cap);
  assert.equal(bridge.capacitorOf({}), null);
  assert.deepEqual(await bridge.prepareNotifications(cap), { display: "granted", exactAlarm: "denied" });
  assert.ok(sent.some(([, method, options]) => method === "createChannel" && options.id === bridge.CHANNEL_ID && options.importance === 5));
  const alerts = bridge.createTimerAlerts(bridge.capacitorNotifier(cap), { now: () => T0 });
  await alerts.sync(runningState(), T0);
  const schedule = sent.find(([, method]) => method === "schedule");
  assert.equal(schedule[0], "LocalNotifications");
  assert.equal(schedule[2].notifications.length, 2);
});
