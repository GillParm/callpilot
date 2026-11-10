// Wraps the two AudioWorklets: microphone capture and agent playback.

export interface PlaybackEvent {
  type: "started" | "progress" | "stopped";
  /** 24 kHz samples played since the last reset. */
  played: number;
}

export class MicrophoneBlockedError extends Error {}

export class AudioEngine {
  onFrame: (pcm: ArrayBuffer, rms: number) => void = () => {};
  onPlayback: (event: PlaybackEvent) => void = () => {};

  private context: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private playback: AudioWorkletNode | null = null;

  async start(): Promise<void> {
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          // Echo cancellation keeps the agent's own voice out of the mic, which
          // would otherwise trigger barge-in on itself.
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
    } catch (error) {
      if (error instanceof DOMException && error.name === "NotAllowedError") {
        throw new MicrophoneBlockedError("Microphone access is blocked.");
      }
      throw error;
    }

    const context = new AudioContext({ latencyHint: "interactive" });
    this.context = context;
    await Promise.all([
      context.audioWorklet.addModule("/worklets/capture-processor.js"),
      context.audioWorklet.addModule("/worklets/playback-processor.js"),
    ]);

    const capture = new AudioWorkletNode(context, "capture-processor");
    capture.port.onmessage = ({ data }) => this.onFrame(data.pcm, data.rms);
    // A worklet only runs while connected to the graph; route it into silence.
    const mute = context.createGain();
    mute.gain.value = 0;
    context.createMediaStreamSource(this.stream).connect(capture).connect(mute);
    mute.connect(context.destination);

    this.playback = new AudioWorkletNode(context, "playback-processor", {
      outputChannelCount: [1],
    });
    this.playback.port.onmessage = ({ data }) => this.onPlayback(data);
    this.playback.connect(context.destination);
    await context.resume();
  }

  play(pcm: ArrayBuffer): void {
    this.playback?.port.postMessage({ type: "push", pcm }, [pcm]);
  }

  /** Drop queued agent audio (barge-in). */
  clear(): void {
    this.playback?.port.postMessage({ type: "clear" });
  }

  /** Start counting played audio from zero (a new agent response). */
  resetProgress(): void {
    this.playback?.port.postMessage({ type: "reset" });
  }

  async stop(): Promise<void> {
    this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = null;
    this.playback = null;
    if (this.context && this.context.state !== "closed") await this.context.close();
    this.context = null;
  }
}
