/*
 * VoineyLab native bridge (lane APP0).
 *
 * The store app (mobile/, Capacitor) wraps this same web screen and adds
 * device features only. This file is the screen's side of that: it turns the
 * step timer the server sent into device notifications (its last minute, by
 * the server's own rule, and its end) and keeps them in step with later server
 * states.
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

  // The server's rule for a timer's last minute (curated_protocol
  // TIMER_LAST_MINUTE_FROM_SECONDS / TIMER_LAST_MINUTE_SECONDS): a timer at
  // least this long is told its last minute. The server's heartbeat carries
  // it (timer_last_minute_from_seconds, timer_last_minute_seconds); these are
  // its values when no heartbeat has come yet.
  const LAST_MINUTE_RULE = Object.freeze({ fromSeconds: 300, seconds: 60 });
  // A device clock this far from the server's is corrected for; below it the
  // difference is rounding (remaining_seconds is whole seconds) and latency.
  const CLOCK_SKEW_TOLERANCE_MS = 3000;
  // A pending notification whose time moved less than this is left alone, so
  // repeated states for one timer do not reschedule it every time.
  const RESCHEDULE_TOLERANCE_MS = 2000;
  const CHANNEL_ID = "voiney-timer";
  const EXTRA_TAG = "voiney-timer";
  // Lane APP1, decision 1: the app's own alarm-clock plugin
  // (mobile/android/.../alarm/TimerAlarmPlugin.java) and the official one.
  const ALARM_PLUGIN = "VoineyAlarm";
  const NOTIFICATIONS_PLUGIN = "LocalNotifications";

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

  // The rule from a server.heartbeat message, or null when it has none.
  function lastMinuteRule(message) {
    if (!message || typeof message !== "object") return null;
    const fromSeconds = Number(message.timer_last_minute_from_seconds);
    const seconds = Number(message.timer_last_minute_seconds);
    if (!Number.isFinite(fromSeconds) || fromSeconds < 0 || !Number.isFinite(seconds) || seconds <= 0) return null;
    return { fromSeconds, seconds };
  }

  function validRule(rule) {
    return rule && Number.isFinite(rule.fromSeconds) && Number.isFinite(rule.seconds) && rule.seconds > 0 ? rule : LAST_MINUTE_RULE;
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
  function leadWords(seconds) {
    return seconds % 60 === 0 ? `${seconds / 60}분` : `${seconds}초`;
  }

  function alertText(timer, kind, leadSeconds) {
    const label = typeof timer.step_label === "string" && timer.step_label ? `${timer.step_label}단계` : "타이머";
    const words = durationWords(timer);
    if (kind === "warning") {
      const lead = leadWords(Number.isFinite(leadSeconds) ? leadSeconds : LAST_MINUTE_RULE.seconds);
      if (timer.bound === "minimum") return { title: `${label} · ${lead} 전`, body: `최소 시간 ${words}까지 ${lead} 남았어요.` };
      if (timer.bound === "maximum") return { title: `${label} · ${lead} 전`, body: `최대 시간 ${words}까지 ${lead} 남았어요.` };
      return { title: `${label} · ${lead} 전`, body: `타이머 ${words}, ${lead} 남았어요.` };
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
   * options.rule: the server's last-minute rule (default LAST_MINUTE_RULE).
   */
  function planTimerAlerts(state, options) {
    const opts = options || {};
    const nowMs = Number.isFinite(opts.nowMs) ? opts.nowMs : Date.now();
    const offsetMs = Number.isFinite(opts.offsetMs) ? opts.offsetMs : 0;
    const rule = validRule(opts.rule);
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
      const text = alertText(timer, kind, rule.seconds);
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
    // As the server tells it: only a timer at least rule.fromSeconds long has
    // a last minute.
    const given = Number(timer.duration_seconds);
    const durationSeconds = Number.isFinite(given) && given > 0 ? given : (deadline - started) / 1000;
    if (durationSeconds >= rule.fromSeconds) add("warning", deadline - rule.seconds * 1000 + offsetMs);
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
    let rule = validRule(opts.rule);
    let current = notifier;
    let queue = Promise.resolve();
    const run = (task) => {
      const next = queue.then(task, task);
      queue = next.catch(() => {});
      return next;
    };
    return {
      // Call with every curated protocol state the server sends.
      // syncOptions.offsetMs: device minus server clock when the page has
      // measured it (lane NET's voineyConnection.serverNow); otherwise it is
      // estimated from the timer's remaining_seconds.
      sync(state, receivedAtMs, syncOptions) {
        return run(async () => {
          const received = Number.isFinite(receivedAtMs) ? receivedAtMs : now();
          const given = syncOptions && syncOptions.offsetMs;
          const offsetMs = Number.isFinite(given) ? given : clockOffsetMs(stepTimer(state), received);
          const desired = planTimerAlerts(state, { nowMs: now(), offsetMs, rule });
          const plan = diffAlerts(await current.pending(), desired);
          if (plan.cancel.length) await current.cancel(plan.cancel);
          if (plan.schedule.length) await current.schedule(plan.schedule);
          return plan;
        });
      },
      // chooseNotifier gave another answer (exact alarms allowed or taken
      // back since the app started). Queued like a sync, so a sync running
      // now finishes with the notifier it began with; the next sync moves
      // what the old one still holds.
      setNotifier(next) {
        return run(async () => {
          current = next;
        });
      },
      // The server's heartbeat changed the last-minute rule; the next sync
      // uses it.
      setRule(next) {
        rule = validRule(next);
        return rule;
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

  // Whether the app carries a native plugin. Capacitor's Android bridge lists
  // every registered plugin in Capacitor.Plugins before the page runs.
  function pluginAvailable(cap, name) {
    if (!cap) return false;
    if (typeof cap.isPluginAvailable === "function") return !!cap.isPluginAvailable(name);
    return !!(cap.Plugins && Object.prototype.hasOwnProperty.call(cap.Plugins, name));
  }

  // @capacitor/local-notifications through the bridge Capacitor injects, so
  // this file needs no bundler.
  function capacitorNotifier(cap) {
    const call = (method, options) => cap.nativePromise(NOTIFICATIONS_PLUGIN, method, options || {});
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

  /*
   * Lane APP1, decision 1: Android's alarm clock (AlarmManager.setAlarmClock)
   * through the app's VoineyAlarm plugin. Doze lets an alarm clock fire on
   * time, where the official plugin's setExactAndAllowWhileIdle is held to
   * about once per nine minutes per app. The plugin keeps each alarm (with
   * its words) and posts the notification itself, so it rings with the app
   * closed and is set again after a reboot.
   */
  function toAlarmClock(alert) {
    return {
      id: alert.id,
      at: alert.at,
      title: alert.title,
      body: alert.body,
      kind: alert.kind,
      key: alert.key,
      channelId: CHANNEL_ID,
    };
  }

  // The plugin's pending list ([{id, at, armed, ...}]) as {id, at}. One the
  // system no longer holds (armed false: exact alarms were taken back and
  // given again) has no time, so the next sync cancels and sets it again.
  function armedAlarms(alarms) {
    const own = [];
    for (const item of alarms || []) {
      if (!item || !Number.isInteger(item.id)) continue;
      const at = Number(item.at);
      own.push({ id: item.id, at: item.armed === false || !Number.isFinite(at) ? NaN : at });
    }
    return own;
  }

  function alarmClockNotifier(cap) {
    const call = (method, options) => cap.nativePromise(ALARM_PLUGIN, method, options || {});
    return {
      async pending() {
        const result = await call("pending");
        return armedAlarms(result && result.alarms);
      },
      cancel(ids) {
        return call("cancel", { ids });
      },
      schedule(alerts) {
        return call("schedule", { alarms: alerts.map(toAlarmClock) });
      },
    };
  }

  // `primary` schedules. Whatever of ours `secondary` still holds is read
  // back without a time, so the next sync cancels it there (cancel goes to
  // both) and sets it on the primary: one alert, never two.
  function preferNotifier(primary, secondary) {
    return {
      async pending() {
        const [own, other] = await Promise.all([primary.pending(), secondary.pending().catch(() => [])]);
        return [...own, ...other.map((item) => ({ id: item.id, at: NaN }))];
      },
      async cancel(ids) {
        await Promise.all([primary.cancel(ids), Promise.resolve().then(() => secondary.cancel(ids)).catch(() => {})]);
      },
      schedule(alerts) {
        return primary.schedule(alerts);
      },
    };
  }

  /*
   * The notifier for this device: the alarm clock when the app has the plugin
   * and exact alarms are allowed, the official plugin otherwise (iOS, an
   * app built before lane APP1, exact alarms not allowed -- then the alerts
   * may be a few minutes late in Doze, which the screen should say).
   * Ask again when the app comes back to the front (the person may have
   * changed the setting) and hand the answer to createTimerAlerts().setNotifier.
   * -> { kind: "alarm_clock" | "local_notifications", exactAlarm, notifier }
   */
  async function chooseNotifier(cap) {
    const local = capacitorNotifier(cap);
    if (!pluginAvailable(cap, ALARM_PLUGIN)) {
      return { kind: "local_notifications", exactAlarm: null, notifier: local };
    }
    let exact = null;
    try {
      const status = await cap.nativePromise(ALARM_PLUGIN, "exactAlarmStatus", {});
      exact = (status && status.exact_alarm) || null;
    } catch (_error) {
      exact = null;
    }
    const alarm = alarmClockNotifier(cap);
    if (exact === "granted") return { kind: "alarm_clock", exactAlarm: exact, notifier: preferNotifier(alarm, local) };
    return { kind: "local_notifications", exactAlarm: exact, notifier: preferNotifier(local, alarm) };
  }

  async function exactAlarmStatus(cap) {
    const plugin = pluginAvailable(cap, ALARM_PLUGIN) ? ALARM_PLUGIN : NOTIFICATIONS_PLUGIN;
    const method = plugin === ALARM_PLUGIN ? "exactAlarmStatus" : "checkExactNotificationSetting";
    try {
      const status = await cap.nativePromise(plugin, method, {});
      return (status && status.exact_alarm) || null;
    } catch (_error) {
      return null;
    }
  }

  // Android 12+: the system page where the person allows exact alarms.
  async function openExactAlarmSettings(cap) {
    if (pluginAvailable(cap, ALARM_PLUGIN)) return cap.nativePromise(ALARM_PLUGIN, "openExactAlarmSettings", {});
    return cap.nativePromise(NOTIFICATIONS_PLUGIN, "changeExactNotificationSetting", {});
  }

  // Once per app start: notification permission, the high-importance channel
  // (Android), and whether exact alarms are allowed (Android 12+).
  async function prepareNotifications(cap) {
    const status = await cap.nativePromise(NOTIFICATIONS_PLUGIN, "requestPermissions", {});
    if (cap.getPlatform && cap.getPlatform() === "android") {
      await cap.nativePromise(NOTIFICATIONS_PLUGIN, "createChannel", {
        id: CHANNEL_ID,
        name: "타이머",
        description: "단계 타이머 1분 전과 끝",
        importance: 5,
        visibility: 1,
        vibration: true,
      });
    }
    return { display: status && status.display, exactAlarm: await exactAlarmStatus(cap) };
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
    LAST_MINUTE_RULE,
    lastMinuteRule,
    CHANNEL_ID,
    EXTRA_TAG,
    ALARM_PLUGIN,
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
    pluginAvailable,
    toAlarmClock,
    armedAlarms,
    alarmClockNotifier,
    preferNotifier,
    chooseNotifier,
    exactAlarmStatus,
    openExactAlarmSettings,
    prepareNotifications,
    keepAwake,
    networkStatus,
    onNetworkChange,
  };
});
