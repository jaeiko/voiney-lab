// Lane APP1, decision 2: listening with the screen off. The app's
// VoineyMicrophone plugin records in an Android microphone foreground service
// and hands the page 16 kHz mono PCM16 in batches; the bridge cuts them into
// the 20 ms frames the web screen sends on /ws today (640 bytes each).
// Run with `node --test tests/native_bridge/*.test.js`; tests/test_native_bridge.py
// runs the same from pytest.
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const bridge = require(path.join(__dirname, "..", "..", "src", "voiney_lab", "static", "native-bridge.js"));

const toBase64 = (bytes) => Buffer.from(bytes).toString("base64");
const ramp = (length, start = 0) => Uint8Array.from({ length }, (_, i) => (start + i) % 256);

// The injected Capacitor bridge with the microphone plugin as Android answers.
function fakeCapacitor({ plugin = true, startError = null } = {}) {
  const sent = [];
  const listeners = new Map();
  let nextId = 1;
  const plugins = plugin ? { VoineyMicrophone: {} } : {};
  const cap = {
    sent,
    listeners,
    Plugins: plugins,
    getPlatform: () => "android",
    isPluginAvailable: (name) => Object.prototype.hasOwnProperty.call(plugins, name),
    nativePromise: async (name, method, options) => {
      sent.push([name, method, options]);
      if (!cap.isPluginAvailable(name)) throw new Error(`"${name}" plugin is not implemented on android`);
      if (method === "start") {
        if (startError) {
          const error = new Error(startError.message);
          error.code = startError.code;
          throw error;
        }
        return { listening: true };
      }
      if (method === "stop") return { listening: false };
      if (method === "status") return { listening: false, permission: "prompt" };
      return {};
    },
    addListener: (name, eventName, callback) => {
      const id = nextId++;
      listeners.set(id, { name, eventName, callback });
      return { remove: async () => listeners.delete(id) };
    },
    emit: (eventName, data) => {
      for (const item of listeners.values()) if (item.eventName === eventName) item.callback(data);
    },
  };
  return cap;
}

test("the format is the web screen's: 16 kHz, 20 ms, 640-byte frames", () => {
  assert.deepEqual({ ...bridge.MIC_FORMAT }, { sampleRate: 16000, frameMs: 20, frameBytes: 640 });
  assert.ok(Object.isFrozen(bridge.MIC_FORMAT));
});

test("batches are cut into whole frames and the rest waits for the next batch", () => {
  const splitter = bridge.createFrameSplitter(640);
  const five = splitter.push(ramp(3200));
  assert.equal(five.length, 5);
  for (const frame of five) {
    assert.ok(frame instanceof ArrayBuffer);
    assert.equal(frame.byteLength, 640);
  }
  assert.deepEqual([...new Uint8Array(five[1]).slice(0, 3)], [640 % 256, 641 % 256, 642 % 256]);
  // An odd batch: one frame now, 360 bytes held, completed by the next.
  const one = splitter.push(ramp(1000, 7));
  assert.equal(one.length, 1);
  assert.equal(splitter.held(), 360);
  const next = splitter.push(ramp(280, 99));
  assert.equal(next.length, 1);
  const joined = new Uint8Array(next[0]);
  assert.deepEqual([...joined.slice(0, 360)], [...ramp(1000, 7).slice(640)]);
  assert.deepEqual([...joined.slice(360)], [...ramp(280, 99)]);
  assert.equal(splitter.held(), 0);
  splitter.push(ramp(100));
  splitter.reset();
  assert.equal(splitter.held(), 0);
});

test("base64 from the plugin decodes to the same bytes", () => {
  const bytes = ramp(3200, 13);
  assert.deepEqual([...bridge.base64ToBytes(toBase64(bytes))], [...bytes]);
  assert.equal(bridge.base64ToBytes("").length, 0);
  assert.equal(bridge.base64ToBytes(null).length, 0);
});

test("start asks the plugin for the format, and every batch becomes frames in order", async () => {
  const cap = fakeCapacitor();
  const frames = [];
  const states = [];
  const mic = bridge.createNativeMicrophone(cap, { onFrame: (frame) => frames.push(frame), onState: (state) => states.push(state) });
  const started = await mic.start();
  assert.equal(started.listening, true);
  const call = cap.sent.find(([, method]) => method === "start");
  assert.deepEqual(call, ["VoineyMicrophone", "start", { sampleRate: 16000, frameMs: 20 }]);
  cap.emit("frames", { data: toBase64(ramp(3200)), sequence: 1 });
  cap.emit("frames", { data: toBase64(ramp(3200, 50)), sequence: 2 });
  assert.equal(frames.length, 10);
  assert.ok(frames.every((frame) => frame.byteLength === 640));
  assert.deepEqual([...new Uint8Array(frames[5]).slice(0, 2)], [50, 51]);
  // A batch lost on the way (the page was busy): counted, not hidden.
  cap.emit("frames", { data: toBase64(ramp(3200)), sequence: 4 });
  assert.deepEqual(mic.stats(), { batches: 3, frames: 15, missedBatches: 1 });
  assert.equal(states[0].state, "listening");
});

test("the notification's 끄기, and stop, tell the page; stop removes the listeners", async () => {
  const cap = fakeCapacitor();
  const states = [];
  const mic = bridge.createNativeMicrophone(cap, { onFrame: () => {}, onState: (state) => states.push(state) });
  await mic.start();
  cap.emit("state", { state: "off", reason: "user_stopped" });
  const last = states[states.length - 1];
  assert.equal(last.listening, false);
  assert.equal(last.reason, "user_stopped");
  assert.match(last.words, /끄기|껐어요/);
  await mic.stop();
  assert.ok(cap.sent.some(([, method]) => method === "stop"));
  assert.equal(cap.listeners.size, 0);
  // Frames arriving after stop go nowhere.
  const frames = [];
  const again = bridge.createNativeMicrophone(cap, { onFrame: (frame) => frames.push(frame) });
  await again.start();
  await again.stop();
  cap.emit("frames", { data: toBase64(ramp(640)), sequence: 1 });
  assert.equal(frames.length, 0);
});

test("a refused permission is an answer with words, not a thrown error", async () => {
  const cap = fakeCapacitor({ startError: { code: "permission_denied", message: "denied" } });
  const states = [];
  const mic = bridge.createNativeMicrophone(cap, { onFrame: () => {}, onState: (state) => states.push(state) });
  const started = await mic.start();
  assert.equal(started.listening, false);
  assert.equal(started.reason, "permission_denied");
  assert.match(started.words, /권한/);
  assert.equal(cap.listeners.size, 0, "nothing keeps listening after a refusal");
  assert.equal(states[states.length - 1].reason, "permission_denied");
  const hidden = bridge.createNativeMicrophone(fakeCapacitor({ startError: { code: "not_visible", message: "x" } }), {});
  assert.equal((await hidden.start()).reason, "not_visible");
});

test("without the plugin (iOS, the web) the page keeps its own microphone", async () => {
  const cap = fakeCapacitor({ plugin: false });
  const mic = bridge.createNativeMicrophone(cap, { onFrame: () => {} });
  const started = await mic.start();
  assert.deepEqual({ listening: started.listening, reason: started.reason }, { listening: false, reason: "unavailable" });
  assert.ok(!cap.sent.some(([name]) => name === "VoineyMicrophone"));
  assert.equal(bridge.nativeMicrophoneAvailable(cap), false);
  assert.equal(bridge.nativeMicrophoneAvailable(fakeCapacitor()), true);
});

test("every reason has words for the screen", () => {
  for (const reason of ["listening", "user_stopped", "stopped", "permission_denied", "not_visible", "mic_unavailable", "unavailable"]) {
    const words = bridge.micStateWords(reason);
    assert.equal(typeof words, "string");
    assert.ok(words.length > 5, reason);
  }
  assert.match(bridge.micStateWords("listening"), /Voiney Lab 이 듣는 중/);
  assert.equal(bridge.micStateWords("nonsense"), bridge.micStateWords("stopped"));
});
