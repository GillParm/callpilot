"use client";

// Runs one call: opens the WebSocket, streams the mic, plays agent audio and
// feeds everything into the call reducer.

import { useCallback, useEffect, useReducer, useRef, useState } from "react";

import { AudioEngine, MicrophoneBlockedError } from "./audio-engine";
import { callReducer, initialCallState } from "./call-state";
import type { ClientMessage, Mode, ServerEvent } from "./protocol";

// Local voice detection, used for the caller lane of the timeline and for
// measuring latency as the caller perceives it (end of speech -> first sound).
const SPEECH_RMS = 0.02;
const SPEECH_MIN_MS = 60;
const SPEECH_HANGOVER_MS = 400;
const SAMPLES_PER_MS = 24;

function toWebSocketUrl(base: string, mode: Mode): string {
  const url = new URL("/ws/call", base);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.searchParams.set("mode", mode);
  return url.toString();
}

export function useCall(backendUrl: string) {
  const [state, dispatch] = useReducer(callReducer, initialCallState());
  const socketRef = useRef<WebSocket | null>(null);
  const engineRef = useRef<AudioEngine | null>(null);
  const startedAtRef = useRef(0);
  const [startedAt, setStartedAt] = useState<number | null>(null);
  /** Mic loudness 0..1, read by the level meter on animation frames. */
  const levelRef = useRef(0);

  const elapsed = () => performance.now() - startedAtRef.current;

  const send = useCallback((message: ClientMessage | ArrayBuffer) => {
    const socket = socketRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN) return;
    socket.send(message instanceof ArrayBuffer ? message : JSON.stringify(message));
  }, []);

  const teardown = useCallback(async () => {
    const engine = engineRef.current;
    engineRef.current = null;
    levelRef.current = 0;
    socketRef.current?.close();
    socketRef.current = null;
    await engine?.stop();
  }, []);

  const start = useCallback(
    async (mode: Mode) => {
      await teardown();
      dispatch({ type: "connect", mode });
      startedAtRef.current = performance.now();
      setStartedAt(startedAtRef.current);

      // Local speech tracking (voice mode).
      let voicedMs = 0;
      let speaking = false;
      let lastVoicedAt = -Infinity;
      let agentStoppedAt = -Infinity;
      let currentTurn = 0;

      if (mode === "voice") {
        const engine = new AudioEngine();
        engineRef.current = engine;
        engine.onFrame = (pcm, rms) => {
          levelRef.current = Math.min(1, rms * 6);
          const now = elapsed();
          if (rms >= SPEECH_RMS) {
            voicedMs += 20;
            if (voicedMs >= SPEECH_MIN_MS) {
              lastVoicedAt = now;
              if (!speaking) {
                speaking = true;
                dispatch({ type: "caller.speech", speaking: true, at: now - voicedMs });
              }
            }
          } else {
            voicedMs = 0;
            if (speaking && now - lastVoicedAt > SPEECH_HANGOVER_MS) {
              speaking = false;
              dispatch({ type: "caller.speech", speaking: false, at: lastVoicedAt });
            }
          }
          send(pcm);
        };
        engine.onPlayback = ({ type, played }) => {
          const now = elapsed();
          if (type === "started") {
            dispatch({ type: "agent.audio", playing: true, at: now });
            // Perceived latency: the caller's last word to the first sound they hear.
            if (lastVoicedAt > agentStoppedAt && currentTurn > 0) {
              const perceived = Math.round(now - lastVoicedAt);
              dispatch({ type: "perceived", turn: currentTurn, perceivedMs: perceived });
              send({ type: "client.latency", turn: currentTurn, perceived_ms: perceived });
            }
          } else if (type === "stopped") {
            agentStoppedAt = now;
            dispatch({ type: "agent.audio", playing: false, at: now });
          }
          send({ type: "playback", played_ms: Math.round(played / SAMPLES_PER_MS) });
        };
        try {
          await engine.start();
        } catch (error) {
          await teardown();
          dispatch({
            type: "failed",
            message:
              error instanceof MicrophoneBlockedError
                ? "Microphone access is blocked. Allow it in your browser's site settings, then call again."
                : "Couldn't start the microphone. Check that one is connected, then call again.",
          });
          return;
        }
      }

      const socket = new WebSocket(toWebSocketUrl(backendUrl, mode));
      socket.binaryType = "arraybuffer";
      socketRef.current = socket;

      socket.onmessage = ({ data }) => {
        if (data instanceof ArrayBuffer) {
          engineRef.current?.play(data);
          return;
        }
        const event = JSON.parse(data as string) as ServerEvent;
        if (event.type === "audio.clear") engineRef.current?.clear();
        if (event.type === "agent.response") engineRef.current?.resetProgress();
        if (event.type === "metrics.turn") currentTurn = event.turn;
        dispatch({ type: "server", event, at: elapsed() });
        if (event.type === "call.ended") void teardown();
      };
      socket.onerror = () => {
        dispatch({
          type: "failed",
          message: "Couldn't reach the call server. Check that the backend is running.",
        });
      };
      socket.onclose = () => {
        if (socketRef.current === socket) {
          dispatch({
            type: "server",
            event: { type: "call.ended", reason: "closed", outcome: "info" },
            at: elapsed(),
          });
          void teardown();
        }
      };
    },
    [backendUrl, send, teardown],
  );

  const hangUp = useCallback(() => {
    send({ type: "hangup" });
  }, [send]);

  const sendText = useCallback(
    (text: string) => {
      const trimmed = text.trim();
      if (trimmed) send({ type: "caller.text", text: trimmed });
    },
    [send],
  );

  useEffect(() => () => void teardown(), [teardown]);

  return { state, start, hangUp, sendText, levelRef, startedAt };
}
