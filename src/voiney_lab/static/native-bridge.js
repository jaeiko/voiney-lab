/*
 * VoineyLab native bridge (lane APP0).
 *
 * The store app (mobile/, Capacitor) wraps this same web screen and adds
 * device features only. This file is the screen's side of that: it turns the
 * step timer the server sent into device notifications (one minute before and
 * at the end) and keeps them in step with later server states.
 *
 * What it must not do: the server is the workflow and timer authority. A
 * device notification is a copy of the start and end times the server gave;
 * nothing here starts, stops, extends or completes a timer or a step. While the
 * connection is down no server state arrives, so nothing here changes either —
 * notifications already scheduled ring on the device's own clock. Only a later
 * server state (timer restarted, changed, stopped, experiment ended) cancels or
 * moves them.
 *
 * Not linked from index.html yet: the connection points are listed in
 * docs/APP_PLAN.md. The planning functions are pure and pinned by
 * tests/native_bridge/timer_alerts.test.js (node --test).
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.VoineyNativeBridge = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const WARNING_LEAD_MS = 60 * 1000;
  // A device clock this far from the server's is corrected for; below it the
  // difference is rounding (remaining_seconds is whole seconds) and latency.
  const CLOCK_SKEW_TOLERANCE_MS = 3000;
  // A pending notification whose time moved less than this is left alone, so
  // repeated states for one timer do not reschedule it every time.
  const RESCHEDULE_TOLERANCE_MS = 2000;
  const CHANNEL_ID = "voiney-timer";
  const EXTRA_TAG = "voiney-timer";

  function parseIso(value) {
    if (typeof value !== "string" || !value) return null;
    const ms = Date.parse(value);
    return Number.isFinite(ms) ? ms : null;
  }

  // FNV-1a, folded to a positive 31-bit integer: Android notification ids are
  // Java ints, and the same timer must map to the same id on every state.
  function alertId(key) {
    let hash = 0x811c9dc5;
    for (let i = 0; i < key.length; i += 1) {
      hash ^= key.charCodeAt(i);
      hash = Math.imul(hash, 0x01000193) >>> 0;
    }
    return (hash & 0x7fffffff) || 1;
  }

  function stepTimer(state) {
    if (!state || typeof state !== "object") return null;
    const timers = state.timers && typeof state.timers === "object" ? state.timers : null;
    const timer = (timers && timers.step) || state.timer || null;
    return timer && typeof timer === "object" ? timer : null;
  }

  // Device clock minus server clock, estimated from one running timer: the
  // server said `remaining_seconds` at about the moment the state arrived.
  function clockOffsetMs(timer, receivedAtMs) {
    if (!timer || timer.state !== "running") return 0;
    const deadline = parseIso(timer.deadline_at);
    const remaining = Number(timer.remaining_seconds);
    if (deadline === null || !Number.isFinite(remaining) || !Number.isFinite(receivedAtMs)) return 0;
    const offset = receivedAtMs + remaining * 1000 - deadline;
    return Math.abs(offset) > CLOCK_SKEW_TOLERANCE_MS ? Math.round(offset) : 0;
  }

  const BOUND_PREFIXES = { minimum: "최소 ", maximum: "최대 ", approximate: "약 " };

  function durationWords(timer) {
    const name = typeof timer.name === "string" ? timer.name.trim() : "";
    const prefix = BOUND_PREFIXES[timer.bound];
    if (name && prefix && name.startsWith(prefix)) return name.slice(prefix.length);
    if (name) return name;
    const seconds = Math.round(Number(timer.duration_seconds) || 0);
    if (seconds >= 3600 && seconds % 3600 === 0) return `${seconds / 3600}시간`;
    if (seconds >= 60 && seconds % 60 === 0) return `${seconds / 60}분`;
    return `${seconds}초`;
  }

  // The words follow the server's own end words (curated_protocol
  // _timer_end_words): a minimum's end is not the step's end, so it is never
  // said to be over.
  function alertText(timer, kind) {
    const label = typeof timer.step_label === "string" && timer.step_label ? `${timer.step_label}단계` : "타이머";
    const words = durationWords(timer);
    if (kind === "warning") {
      if (timer.bound === "minimum") return { title: `${label} · 1분 전`, body: `최소 시간 ${words}까지 1분 남았어요.` };
      if (timer.bound === "maximum") return { title: `${label} · 1분 전`, body: `최대 시간 ${words}까지 1분 남았어요.` };
      return { title: `${label} · 1분 전`, body: `타이머 ${words}, 1분 남았어요.` };
    }
    if (timer.bound === "minimum") return { title: `${label} · 최소 시간`, body: `최소 시간 ${words}이 지났어요. 앱에서 확인해 주세요.` };
    if (timer.bound === "maximum") return { title: `${label} · 최대 시간`, body: `최대 시간 ${words}이 됐어요. 앱에서 확인해 주세요.` };
    return { title: `${label} · 타이머 끝`, body: `타이머 ${words}이 끝났어요. 앱에서 확인해 주세요.` };
  }

  /*
   * The notifications a server state asks for, earliest first.
   *
   * state: the curated protocol state as the screen keeps it
   *   (attached, active, timers.step with state, started_at, deadline_at, ...).
   * options.nowMs: the device's now (default Date.now()).
   * options.offsetMs: device clock minus server clock (see clockOffsetMs).
   */
  function planTimerAlerts(state, options) {
    const opts = options || {};
    const nowMs = Number.isFinite(opts.nowMs) ? opts.nowMs : Date.now();
    const offsetMs = Number.isFinite(opts.offsetMs) ? opts.offsetMs : 0;
    if (!state || state.attached !== true || state.active !== true) return [];
    const timer = stepTimer(state);
    if (!timer || timer.state !== "running") return [];
    const deadline = parseIso(timer.deadline_at);
    const started = parseIso(timer.started_at);
    if (deadline === null || started === null || deadline <= started) return [];
    const base = `${timer.step_id || ""}|${timer.started_at}`;
    const alerts = [];
    const add = (kind, at) => {
      if (at <= nowMs) return;
      const key = `${base}|${kind}`;
      const text = alertText(timer, kind);
      alerts.push({
        id: alertId(key),
        key,
        kind,
        at,
        title: text.title,
        body: text.body,
        step_id: timer.step_id || null,
        deadline_at: timer.deadline_at,
      });
    };
    // A timer of a minute or less has no "one minute before".
    if (deadline - started > WARNING_LEAD_MS) add("warning", deadline - WARNING_LEAD_MS + offsetMs);
    add("end", deadline + offsetMs);
    return alerts.sort((a, b) => a.at - b.at);
  }

  /*
   * What to cancel and what to schedule so that `pending` (our own
   * notifications on the device: [{id, at}]) becomes `desired`.
   * A notification whose time moved is cancelled and scheduled again.
   */
  function diffAlerts(pending, desired, toleranceMs) {
    const tolerance = Number.isFinite(toleranceMs) ? toleranceMs : RESCHEDULE_TOLERANCE_MS;
    const wanted = new Map((desired || []).map((alert) => [alert.id, alert]));
    const have = new Map((pending || []).map((item) => [item.id, item]));
    const cancel = [];
    const schedule = [];
    for (const [id, item] of have) {
      const alert = wanted.get(id);
      if (!alert || !Number.isFinite(item.at) || Math.abs(item.at - alert.at) > tolerance) cancel.push(id);
    }
    for (const [id, alert] of wanted) {
      const item = have.get(id);
      if (!item || cancel.includes(id)) schedule.push(alert);
    }
    return { cancel, schedule };
  }

  function toCapacitorNotification(alert) {
    return {
      id: alert.id,
      title: alert.title,
      body: alert.body,
      channelId: CHANNEL_ID,
      schedule: { at: new Date(alert.at), allowWhileIdle: true },
      extra: { tag: EXTRA_TAG, key: alert.key, kind: alert.kind, at: alert.at },
    };
  }

  // Our notifications among everything pending; another feature's are never
  // touched.
  function ownPending(notifications) {
    const own = [];
    for (const item of notifications || []) {
      const extra = item && item.extra;
      if (!extra || extra.tag !== EXTRA_TAG) continue;
      own.push({ id: item.id, at: Number(extra.at) });
    }
    return own;
  }

  /*
   * notifier: { pending(): Promise<[{id, at}]>, cancel(ids), schedule(alerts) }.
   * Syncs run one after another, so two states arriving together cannot
   * interleave their cancels and schedules.
   */
  function createTimerAlerts(notifier, options) {
    const opts = options || {};
    const now = typeof opts.now === "function" ? opts.now : () => Date.now();
    let queue = Promise.resolve();
    const run = (task) => {
      const next = queue.then(task, task);
      queue = next.catch(() => {});
      return next;
    };
    return {
      // Call with every curated protocol state the server sends.
      sync(state, receivedAtMs) {
        return run(async () => {
          const received = Number.isFinite(receivedAtMs) ? receivedAtMs : now();
          const offsetMs = clockOffsetMs(stepTimer(state), received);
          const desired = planTimerAlerts(state, { nowMs: now(), offsetMs });
          const plan = diffAlerts(await notifier.pending(), desired);
          if (plan.cancel.length) await notifier.cancel(plan.cancel);
          if (plan.schedule.length) await notifier.schedule(plan.schedule);
          return plan;
        });
      },
      // The connection dropped: deliberately nothing. Scheduled notifications
      // are the server's times already and ring without the network.
      connectionLost() {
        return { cancel: [], schedule: [] };
      },
    };
  }

  function capacitorOf(win) {
    const cap = win && win.Capacitor;
    return cap && typeof cap.nativePromise === "function" ? cap : null;
  }

  function isNativeApp(win) {
    const cap = win && win.Capacitor;
    return !!(cap && typeof cap.isNativePlatform === "function" && cap.isNativePlatform());
  }

  // @capacitor/local-notifications through the bridge Capacitor injects, so
  // this file needs no bundler.
  function capacitorNotifier(cap) {
    const call = (method, options) => cap.nativePromise("LocalNotifications", method, options || {});
    return {
      async pending() {
        const result = await call("getPending");
        return ownPending(result && result.notifications);
      },
      cancel(ids) {
        return call("cancel", { notifications: ids.map((id) => ({ id })) });
      },
      schedule(alerts) {
        return call("schedule", { notifications: alerts.map(toCapacitorNotification) });
      },
    };
  }

  // Once per app start: notification permission, the high-importance channel
  // (Android), and whether exact alarms are allowed (Android 12+).
  async function prepareNotifications(cap) {
    const status = await cap.nativePromise("LocalNotifications", "requestPermissions", {});
    if (cap.getPlatform && cap.getPlatform() === "android") {
      await cap.nativePromise("LocalNotifications", "createChannel", {
        id: CHANNEL_ID,
        name: "타이머",
        description: "단계 타이머 1분 전과 끝",
        importance: 5,
        visibility: 1,
        vibration: true,
      });
    }
    let exact = null;
    try {
      exact = await cap.nativePromise("LocalNotifications", "checkExactNotificationSetting", {});
    } catch (_error) {
      exact = null;
    }
    return { display: status && status.display, exactAlarm: exact && exact.exact_alarm };
  }

  function keepAwake(cap, on) {
    return cap.nativePromise("KeepAwake", on ? "keepAwake" : "allowSleep", {});
  }

  function networkStatus(cap) {
    return cap.nativePromise("Network", "getStatus", {});
  }

  function onNetworkChange(cap, callback) {
    return cap.nativeCallback("Network", "addListener", { eventName: "networkStatusChange" }, callback);
  }

  return {
    WARNING_LEAD_MS,
    CHANNEL_ID,
    EXTRA_TAG,
    alertId,
    alertText,
    clockOffsetMs,
    planTimerAlerts,
    diffAlerts,
    toCapacitorNotification,
    ownPending,
    createTimerAlerts,
    capacitorOf,
    isNativeApp,
    capacitorNotifier,
    prepareNotifications,
    keepAwake,
    networkStatus,
    onNetworkChange,
  };
});
