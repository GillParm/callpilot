"""A tiny energy-based voice activity detector.

It does not decide when the caller has finished talking (the provider's own
VAD does that). Its only job is to timestamp the caller's last voiced audio,
the same way for every provider, so time-to-first-audio is measured from the
moment the caller actually stopped speaking.
"""

from __future__ import annotations

from callpilot.audio.codecs import Int16Array, rms_dbfs


class EnergyVAD:
    def __init__(
        self,
        *,
        sample_rate: int = 24_000,
        threshold_dbfs: float = -42.0,
        min_speech_ms: float = 60.0,
    ) -> None:
        self._sample_rate = sample_rate
        self._threshold = threshold_dbfs
        self._min_speech_ms = min_speech_ms
        self._voiced_run_ms = 0.0
        #: Monotonic time of the most recent chunk that was part of speech.
        self.last_voiced_at: float | None = None

    def process(self, samples: Int16Array, now: float) -> bool:
        """Feed one chunk received at `now`. Returns True if it counts as speech."""
        duration_ms = 1000 * len(samples) / self._sample_rate
        if rms_dbfs(samples) < self._threshold:
            self._voiced_run_ms = 0.0
            return False
        self._voiced_run_ms += duration_ms
        if self._voiced_run_ms < self._min_speech_ms:
            return False  # a click or a pop, not speech
        self.last_voiced_at = now
        return True
