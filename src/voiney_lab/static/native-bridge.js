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
 * Lane APP1 added the app's own plugins behind the same rules: the alarm
 * clock that rings on time in Doze (VoineyAlarm), listening with the screen
 * off (VoineyMicrophone), and the app's login (VoineySessionStore, the bearer
 * and the socket's first message, the page's fetch in the app).
 *
 * Not linked from index.html yet: the connection points are listed in
 * docs/APP_PLAN.md 9.2. The planning functions are pure and pinned by
 * tests/native_bridge/*.test.js (node --test).
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

  /*
   * Lane APP1, decision 2: listening with the screen off.
   *
   * Android lets a background app use the microphone only from a
   * "microphone" foreground service started while the app is visible. The
   * app's VoineyMicrophone plugin (mobile/android/.../mic/) runs one, with
   * the notification line "VoineyLab 이 듣는 중 · 끄기", records 16 kHz mono
   * PCM16 and hands it to the page in batches (base64, 100 ms each). Here the
   * batches become the 20 ms frames the web screen's worklet sends on /ws
   * today (voiney_lab.audio: SAMPLE_RATE, FRAME_MS, FRAME_BYTES), and the
   * page sends them on its one socket -- a second socket would be a second
   * server session. Nothing here touches the experiment's state: turning
   * listening off only stops the sound.
   */
  const MIC_PLUGIN = "VoineyMicrophone";
  const MIC_FORMAT = Object.freeze({ sampleRate: 16000, frameMs: 20, frameBytes: 640 });

  const MIC_WORDS = {
    listening: "VoineyLab 이 듣는 중 — 화면을 꺼도 계속 들어요. 알림 줄의 '끄기'로 멈출 수 있어요.",
    user_stopped: "알림 줄의 '끄기'로 듣기를 껐어요. 다시 들으려면 화면에서 듣기를 켜 주세요.",
    stopped: "듣기를 멈췄어요.",
    permission_denied: "마이크 권한이 거절됐어요. 설정 → 앱 → VoineyLab → 권한에서 마이크를 허용해 주세요.",
    not_visible: "앱 화면이 보일 때만 듣기를 시작할 수 있어요. 화면을 켜고 다시 시작해 주세요.",
    mic_unavailable: "마이크를 열지 못했어요. 다른 앱이 마이크를 쓰고 있는지 확인해 주세요.",
    unavailable: "이 앱에는 화면이 꺼져도 듣는 기능이 없어요. 화면을 켜 둔 채로 들어요.",
  };

  function micStateWords(reason) {
    return MIC_WORDS[reason] || MIC_WORDS.stopped;
  }

  function micState(reason) {
    const listening = reason === "listening";
    return { state: listening ? "listening" : "off", listening, reason, words: micStateWords(reason) };
  }

  function base64ToBytes(text) {
    if (typeof text !== "string" || !text) return new Uint8Array(0);
    if (typeof atob === "function") {
      const binary = atob(text);
      const bytes = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
      return bytes;
    }
    return Uint8Array.from(Buffer.from(text, "base64"));
  }

  // Cuts byte batches into whole frames (ArrayBuffer each, as the worklet
  // path sends); a partial frame waits for the next batch.
  function createFrameSplitter(frameBytes) {
    const size = Number.isInteger(frameBytes) && frameBytes > 0 ? frameBytes : MIC_FORMAT.frameBytes;
    let carry = new Uint8Array(0);
    return {
      push(bytes) {
        const joined = new Uint8Array(carry.length + bytes.length);
        joined.set(carry, 0);
        joined.set(bytes, carry.length);
        const frames = [];
        let offset = 0;
        for (; offset + size <= joined.length; offset += size) frames.push(joined.slice(offset, offset + size).buffer);
        carry = joined.slice(offset);
        return frames;
      },
      held() {
        return carry.length;
      },
      reset() {
        carry = new Uint8Array(0);
      },
    };
  }

  function nativeMicrophoneAvailable(cap) {
    return pluginAvailable(cap, MIC_PLUGIN);
  }

  /*
   * handlers.onFrame(ArrayBuffer): one 640-byte frame -- send it as the
   *   worklet's frames are sent (session active, socket open).
   * handlers.onState({state, listening, reason, words}): listening began or
   *   ended (the notification's 끄기, a refused permission, the microphone
   *   lost) -- show the words; the page's own microphone stays off while
   *   the native one is used.
   * start() never throws for a refusal; it answers {listening:false, reason, words}.
   */
  function createNativeMicrophone(cap, handlers) {
    const onFrame = handlers && typeof handlers.onFrame === "function" ? handlers.onFrame : () => {};
    const onState = handlers && typeof handlers.onState === "function" ? handlers.onState : () => {};
    const splitter = createFrameSplitter(MIC_FORMAT.frameBytes);
    let listeners = [];
    let lastSequence = null;
    const counts = { batches: 0, frames: 0, missedBatches: 0 };

    const removeListeners = async () => {
      const current = listeners;
      listeners = [];
      // Android's injected addListener answers the handle; @capacitor/core's, a promise of it.
      await Promise.all(current.map((handle) => Promise.resolve(handle).then((h) => h && h.remove && h.remove()).catch(() => {})));
    };
    const tell = (reason) => {
      const state = micState(reason);
      onState(state);
      return state;
    };
    const onBatch = (event) => {
      if (!listeners.length) return;
      const sequence = Number(event && event.sequence);
      if (Number.isFinite(sequence)) {
        if (lastSequence !== null && sequence > lastSequence + 1) counts.missedBatches += sequence - lastSequence - 1;
        lastSequence = sequence;
      }
      counts.batches += 1;
      for (const frame of splitter.push(base64ToBytes(event && event.data))) {
        counts.frames += 1;
        onFrame(frame);
      }
    };
    const onNativeState = (event) => {
      const reason = (event && event.reason) || "stopped";
      if (event && event.state === "listening") return;
      void removeListeners();
      splitter.reset();
      tell(reason);
    };

    return {
      async start() {
        if (!nativeMicrophoneAvailable(cap)) return micState("unavailable");
        await removeListeners();
        splitter.reset();
        lastSequence = null;
        listeners = [cap.addListener(MIC_PLUGIN, "frames", onBatch), cap.addListener(MIC_PLUGIN, "state", onNativeState)];
        try {
          await cap.nativePromise(MIC_PLUGIN, "start", { sampleRate: MIC_FORMAT.sampleRate, frameMs: MIC_FORMAT.frameMs });
        } catch (error) {
          await removeListeners();
          const code = error && typeof error.code === "string" && MIC_WORDS[error.code] ? error.code : "mic_unavailable";
          return tell(code);
        }
        return tell("listening");
      },
      async stop() {
        await removeListeners();
        splitter.reset();
        if (!nativeMicrophoneAvailable(cap)) return micState("stopped");
        try {
          await cap.nativePromise(MIC_PLUGIN, "stop", {});
        } catch (_error) {
          // Already stopped.
        }
        return micState("stopped");
      },
      async status() {
        if (!nativeMicrophoneAvailable(cap)) return { listening: false, permission: null };
        return cap.nativePromise(MIC_PLUGIN, "status", {});
      },
      // The system page where a refused microphone permission is allowed.
      openAppSettings() {
        return cap.nativePromise(MIC_PLUGIN, "openAppSettings", {});
      },
      stats() {
        return { ...counts };
      },
    };
  }

  /*
   * Lane APP1, decision 3: logging in from the bundled screen.
   *
   * The bundled screen runs at the app's origin, where the server's login
   * cookie is never sent. For an origin the server lists
   * (VOINEY_LAB_APP_ORIGINS) the login answer carries the session token
   * instead; it is kept only in the phone's secure store (the VoineySessionStore
   * plugin: Android Keystore) and in this closure, and sent as
   * `Authorization: Bearer` on /api and as the first message on /ws -- never
   * in a URL, never logged, never returned to the caller.
   */
  const SESSION_PLUGIN = "VoineySessionStore";
  const SOCKET_AUTH_TYPE = "client.auth";
  const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]"]);

  // "https://host[:port]" of a server address, or null. Plain http only for
  // this computer (development); the microphone needs a secure page anyway.
  function serverBase(value) {
    if (typeof value !== "string" || !value.trim()) return null;
    let url;
    try {
      url = new URL(value.trim());
    } catch (_error) {
      return null;
    }
    if (url.username || url.password) return null;
    if (url.protocol === "https:" || (url.protocol === "http:" && LOCAL_HOSTS.has(url.hostname))) {
      return `${url.protocol}//${url.host}`;
    }
    return null;
  }

  function apiUrl(base, path) {
    if (typeof path !== "string" || !/^\/(api\/|readyz$|healthz$)/.test(path)) throw new Error("only the server's /api paths");
    return `${base}${path}`;
  }

  function socketUrl(base) {
    return `${base.replace(/^http/, "ws")}/ws`;
  }

  function authHeaders(token, headers) {
    // A Headers (or Map) keeps its entries; a plain object is copied.
    const plain = headers && typeof headers.entries === "function" && !Array.isArray(headers) ? Object.fromEntries(headers.entries()) : headers;
    const copy = { ...(plain || {}) };
    if (token) copy.Authorization = `Bearer ${token}`;
    return copy;
  }

  function socketAuthMessage(token) {
    return token ? JSON.stringify({ type: SOCKET_AUTH_TYPE, token }) : null;
  }

  /*
   * options.serverUrl: the server's address (https). options.fetch: the
   * page's fetch (tests give their own).
   * -> { load, restore, login, logout, fetch, socketUrl, socketHello }
   */
  function createAppSession(cap, options) {
    const opts = options || {};
    const base = serverBase(opts.serverUrl);
    if (!base) throw new Error("the server address must be https://");
    const fetchImpl = typeof opts.fetch === "function" ? opts.fetch : (...args) => fetch(...args);
    const store = pluginAvailable(cap, SESSION_PLUGIN);
    let token = null;
    let mode = null;

    const forget = async () => {
      token = null;
      if (store) await cap.nativePromise(SESSION_PLUGIN, "clear", {}).catch(() => {});
    };
    const request = (path, init) => {
      const given = init || {};
      return fetchImpl(apiUrl(base, path), { ...given, credentials: "omit", headers: authHeaders(token, given.headers) });
    };
    const readJson = async (response) => {
      try {
        return await response.json();
      } catch (_error) {
        return null;
      }
    };

    // Login and signup: the server answers an app origin with the token in
    // the body; it goes to the secure store and this closure only.
    const signIn = async (path, payload) => {
      const response = await request(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const body = (await readJson(response)) || {};
      if (!response.ok) return { signedIn: false, status: response.status, reason: body.detail || "login_failed" };
      const given = body.app_session && typeof body.app_session.token === "string" ? body.app_session.token : null;
      if (!given) return { signedIn: false, status: response.status, reason: "app_origin_not_listed" };
      if (!store) return { signedIn: false, status: response.status, reason: "secure_store_unavailable" };
      await cap.nativePromise(SESSION_PLUGIN, "set", { token: given });
      token = given;
      mode = "accounts";
      return { signedIn: true, account: body.account || null };
    };

    return {
      // The stored token into memory (no network).
      async load() {
        if (!store) return false;
        const result = await cap.nativePromise(SESSION_PLUGIN, "get", {});
        token = result && typeof result.token === "string" && result.token ? result.token : null;
        return token !== null;
      },
      // Who the stored token signs in, asked of the server; a session the
      // server ended is forgotten here too.
      async restore() {
        await this.load();
        const response = await request("/api/auth/session");
        const body = (await readJson(response)) || {};
        mode = body.mode || null;
        if (body.signed_in !== true && token) await forget();
        return { signedIn: body.signed_in === true, mode, account: body.account || null };
      },
      login(name, password) {
        return signIn("/api/auth/login", { name, password });
      },
      // Joining through an invite link signs in at once, the same way.
      signup(invite, name, password) {
        return signIn("/api/auth/signup", { invite, name, password });
      },
      async logout() {
        if (token) await request("/api/auth/logout", { method: "POST" }).catch(() => null);
        await forget();
      },

      // fetch for the server's /api: the bearer, never cookies. A 401 means
      // the server ended the session; it is forgotten.
      async fetch(path, init) {
        const response = await request(path, init);
        if (response.status === 401 && token) await forget();
        return response;
      },
      socketUrl() {
        return socketUrl(base);
      },
      // The first message to send on /ws, or null (no token, or a server in
      // dev mode, which reads no token).
      async socketHello() {
        return mode === "dev" ? null : socketAuthMessage(token);
      },
    };
  }

  // For the screen bundled in the app: a fetch the page can use in place of
  // its own. Its server paths (/api/..., /readyz, /healthz -- written relative
  // today) go to the server through the session (bearer, no cookies);
  // anything else -- the bundled files -- goes to `baseFetch` untouched.
  function createAppFetch(session, baseFetch) {
    const local = typeof baseFetch === "function" ? baseFetch : (...args) => fetch(...args);
    return (input, init) => {
      if (typeof input === "string" && /^\/(api\/|readyz$|healthz$)/.test(input)) return session.fetch(input, init);
      return local(input, init);
    };
  }

  // An image or download the server serves under /api (figures, page images,
  // reports): an <img src> or <a href> cannot carry the bearer, so it is
  // fetched with it and shown from a blob: address (revoke it when done).
  async function resourceObjectUrl(session, path, urlApi) {
    const api = urlApi || (typeof URL !== "undefined" ? URL : null);
    const response = await session.fetch(path);
    if (!response.ok || !api || typeof api.createObjectURL !== "function") return null;
    return api.createObjectURL(await response.blob());
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
    MIC_PLUGIN,
    MIC_FORMAT,
    micStateWords,
    base64ToBytes,
    createFrameSplitter,
    nativeMicrophoneAvailable,
    createNativeMicrophone,
    SESSION_PLUGIN,
    serverBase,
    apiUrl,
    socketUrl,
    authHeaders,
    socketAuthMessage,
    createAppSession,
    createAppFetch,
    resourceObjectUrl,
    keepAwake,
    networkStatus,
    onNetworkChange,
  };
});
