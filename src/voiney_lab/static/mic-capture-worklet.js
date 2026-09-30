class VoiceWorkflowMicCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.batchFrames = Math.max(128, Math.round(sampleRate * 0.02));
    this.pending = new Float32Array(this.batchFrames);
    this.pendingLength = 0;
    this.active = true;
    this.port.onmessage = event => {
      if (event.data?.type === "stop") {
        this.active = false;
        this.pendingLength = 0;
      }
    };
  }

  process(inputs) {
    if (!this.active) return false;
    const input = inputs[0]?.[0];
    if (!input?.length) return true;
    let offset = 0;
    while (offset < input.length) {
      const count = Math.min(
        input.length - offset,
        this.batchFrames - this.pendingLength,
      );
      this.pending.set(input.subarray(offset, offset + count), this.pendingLength);
      this.pendingLength += count;
      offset += count;
      if (this.pendingLength === this.batchFrames) {
        const block = this.pending;
        this.port.postMessage(block.buffer, [block.buffer]);
        this.pending = new Float32Array(this.batchFrames);
        this.pendingLength = 0;
      }
    }
    return true;
  }
}

registerProcessor(
  "voice-workflow-mic-capture",
  VoiceWorkflowMicCaptureProcessor,
);
