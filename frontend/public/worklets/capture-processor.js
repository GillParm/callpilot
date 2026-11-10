// Microphone capture: resample whatever rate the AudioContext runs at down to
// 24 kHz, convert to 16-bit PCM and post 20 ms frames to the main thread, with
// the frame's loudness for the level meter and local voice detection.

const TARGET_RATE = 24000;
const FRAME_SAMPLES = 480; // 20 ms at 24 kHz

class CaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / TARGET_RATE;
    this.position = 0; // fractional read position into the input stream
    this.previous = 0; // last input sample of the previous block, for interpolation
    this.frame = new Int16Array(FRAME_SAMPLES);
    this.filled = 0;
    this.energy = 0;
  }

  process(inputs) {
    const input = inputs[0] && inputs[0][0];
    if (!input) return true;

    // Linear interpolation between input samples. `position` is relative to
    // the start of this block; -1 refers to the previous block's last sample.
    while (this.position < input.length) {
      const index = Math.floor(this.position);
      const frac = this.position - index;
      const a = index < 0 ? this.previous : input[index];
      const b = index + 1 < input.length ? input[index + 1] : input[index];
      const sample = Math.max(-1, Math.min(1, a + (b - a) * frac));
      this.frame[this.filled++] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
      this.energy += sample * sample;
      if (this.filled === FRAME_SAMPLES) this.flush();
      this.position += this.ratio;
    }
    this.position -= input.length;
    this.previous = input[input.length - 1];
    return true;
  }

  flush() {
    const rms = Math.sqrt(this.energy / FRAME_SAMPLES);
    const pcm = this.frame.buffer.slice(0);
    this.port.postMessage({ pcm, rms }, [pcm]);
    this.filled = 0;
    this.energy = 0;
  }
}

registerProcessor("capture-processor", CaptureProcessor);
