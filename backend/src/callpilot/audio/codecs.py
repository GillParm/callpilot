"""G.711 mu-law and streaming resampling, in NumPy.

Phone audio from Twilio is 8 kHz mu-law; everything inside CallPilot is 16-bit
PCM at 24 kHz. `audioop` used to do this but was removed in Python 3.13, so
these are small, tested replacements.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

Int16Array = NDArray[np.int16]

_BIAS = 0x84
_CLIP = 32635


def _build_decode_table() -> Int16Array:
    codes = ~np.arange(256, dtype=np.int32) & 0xFF
    sign = codes & 0x80
    exponent = (codes >> 4) & 0x07
    mantissa = codes & 0x0F
    magnitude = (((mantissa << 3) + _BIAS) << exponent) - _BIAS
    return np.where(sign != 0, -magnitude, magnitude).astype(np.int16)


_DECODE = _build_decode_table()


def mulaw_decode(data: bytes) -> Int16Array:
    """mu-law bytes -> int16 samples."""
    return _DECODE[np.frombuffer(data, dtype=np.uint8)]


def mulaw_encode(samples: Int16Array) -> bytes:
    """int16 samples -> mu-law bytes."""
    pcm = samples.astype(np.int32)
    sign = np.where(pcm < 0, 0x80, 0)
    magnitude = np.minimum(np.abs(pcm), _CLIP) + _BIAS
    exponent = np.floor(np.log2(magnitude)).astype(np.int32) - 7
    exponent = np.clip(exponent, 0, 7)
    mantissa = (magnitude >> (exponent + 3)) & 0x0F
    encoded = ~(sign | (exponent << 4) | mantissa) & 0xFF
    return bytes(encoded.astype(np.uint8).tobytes())


def pcm16_to_array(data: bytes) -> Int16Array:
    usable = len(data) - len(data) % 2
    return np.frombuffer(data[:usable], dtype="<i2").astype(np.int16)


def array_to_pcm16(samples: Int16Array) -> bytes:
    return samples.astype("<i2").tobytes()


def _lowpass_taps(cutoff: float, taps: int = 63) -> NDArray[np.float64]:
    """Windowed-sinc low-pass FIR; `cutoff` is a fraction of the sample rate."""
    n = np.arange(taps) - (taps - 1) / 2
    kernel = np.sinc(2 * cutoff * n) * np.hamming(taps)
    result: NDArray[np.float64] = kernel / kernel.sum()
    return result


class Resampler:
    """Streaming resampler for integer ratios (e.g. 8 kHz <-> 24 kHz).

    Feed it chunks of any size; it keeps filter and phase state between calls
    so the output is the same as resampling the whole stream at once.
    Upsampling zero-stuffs and low-passes; downsampling low-passes then
    decimates, so neither direction aliases.
    """

    def __init__(self, src_rate: int, dst_rate: int) -> None:
        if src_rate == dst_rate:
            self._up, self._down = 1, 1
        elif dst_rate % src_rate == 0:
            self._up, self._down = dst_rate // src_rate, 1
        elif src_rate % dst_rate == 0:
            self._up, self._down = 1, src_rate // dst_rate
        else:
            raise ValueError(f"only integer ratios are supported: {src_rate} -> {dst_rate}")
        factor = max(self._up, self._down)
        self._taps = _lowpass_taps(0.45 / factor) if factor > 1 else None
        self._history = np.zeros(0 if self._taps is None else len(self._taps) - 1)
        self._phase = 0  # decimation offset carried across chunks

    def process(self, samples: Int16Array) -> Int16Array:
        if self._taps is None or len(samples) == 0:
            return samples.astype(np.int16)
        signal = samples.astype(np.float64)
        if self._up > 1:
            stuffed = np.zeros(len(signal) * self._up)
            stuffed[:: self._up] = signal * self._up  # keep the gain at 1
            signal = stuffed
        padded = np.concatenate([self._history, signal])
        filtered = np.convolve(padded, self._taps, mode="valid")
        self._history = padded[len(padded) - (len(self._taps) - 1) :]
        if self._down > 1:
            filtered = filtered[self._phase :: self._down]
            self._phase = (self._phase - len(signal)) % self._down
        return np.clip(np.round(filtered), -32768, 32767).astype(np.int16)


def rms_dbfs(samples: Int16Array) -> float:
    """Loudness of a chunk in dB relative to full scale (silence is -inf)."""
    if len(samples) == 0:
        return float("-inf")
    rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))
    return 20 * float(np.log10(rms / 32768)) if rms > 0 else float("-inf")
