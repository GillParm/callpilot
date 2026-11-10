// Agent audio playback: queue 24 kHz PCM16 chunks, resample to the context
// rate and play them back to back. `clear` drops everything queued (barge-in).
// Reports how much has been played so the server can trim the agent's turn to
// what the caller actually heard.

const SOURCE_RATE = 24000;
const REPORT_EVERY = 2400; // report progress every 100 ms of source audio

class PlaybackProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.step = SOURCE_RATE / sampleRate; // source samples per output sample
    this.queue = []; // Float32Array chunks at 24 kHz
    this.offset = 0; // fractional read position in queue[0]
    this.played = 0; // source samples played since the last reset
    this.sinceReport = 0;
    this.playing = false;
    this.port.onmessage = ({ data }) => {
      if (data.type === "push") {
        const pcm = new Int16Array(data.pcm);
        const chunk = new Float32Array(pcm.length);
        for (let i = 0; i < pcm.length; i++) chunk[i] = pcm[i] / 0x8000;
        this.queue.push(chunk);
      } else if (data.type === "clear") {
        this.queue = [];
        this.offset = 0;
        this.stop();
      } else if (data.type === "reset") {
        this.played = 0;
      }
    };
  }

  sampleAt(position) {
    // Read across chunk boundaries for interpolation.
    let index = position;
    for (const chunk of this.queue) {
      if (index < chunk.length) return chunk[index];
      index -= chunk.length;
    }
    return 0;
  }

  process(_inputs, outputs) {
    const output = outputs[0][0];
    let written = 0;
    while (written < output.length && this.queue.length > 0) {
      const head = this.queue[0];
      const index = Math.floor(this.offset);
      if (index >= head.length) {
        this.queue.shift();
        this.offset -= head.length;
        continue;
      }
      const frac = this.offset - index;
      const a = head[index];
      const b = this.sampleAt(index + 1);
      output[written++] = a + (b - a) * frac;
      const before = Math.floor(this.offset);
      this.offset += this.step;
      const advanced = Math.floor(this.offset) - before;
      this.played += advanced;
      this.sinceReport += advanced;
    }
    for (let i = written; i < output.length; i++) output[i] = 0;

    if (written > 0 && !this.playing) {
      this.playing = true;
      this.port.postMessage({ type: "started", played: this.played });
    }
    if (this.sinceReport >= REPORT_EVERY) {
      this.sinceReport = 0;
      this.port.postMessage({ type: "progress", played: this.played });
    }
    if (written === 0 && this.playing) this.stop();
    return true;
  }

  stop() {
    if (!this.playing) return;
    this.playing = false;
    this.port.postMessage({ type: "stopped", played: this.played });
  }
}

registerProcessor("playback-processor", PlaybackProcessor);
