import numpy as np
import pytest

from callpilot.audio.codecs import (
    Resampler,
    array_to_pcm16,
    mulaw_decode,
    mulaw_encode,
    pcm16_to_array,
    rms_dbfs,
)
from callpilot.audio.vad import EnergyVAD


def tone(freq: float, rate: int, seconds: float, amplitude: float = 0.5) -> np.ndarray:
    t = np.arange(int(rate * seconds)) / rate
    return (amplitude * 32767 * np.sin(2 * np.pi * freq * t)).astype(np.int16)


def dominant_frequency(samples: np.ndarray, rate: int) -> float:
    spectrum = np.abs(np.fft.rfft(samples.astype(np.float64)))
    return float(np.fft.rfftfreq(len(samples), 1 / rate)[int(np.argmax(spectrum))])


# mu-law ------------------------------------------------------------------------


def test_mulaw_known_values() -> None:
    assert mulaw_encode(np.array([0], dtype=np.int16)) == b"\xff"
    assert mulaw_decode(b"\xff")[0] == 0
    assert mulaw_decode(b"\x80")[0] == 32124
    assert mulaw_decode(b"\x00")[0] == -32124


def test_mulaw_round_trip_is_close() -> None:
    samples = tone(440, 8000, 0.1)
    decoded = mulaw_decode(mulaw_encode(samples))
    error = np.abs(decoded.astype(int) - samples.astype(int))
    # mu-law is lossy but its error scales with amplitude (about 3% at most).
    assert np.all(error <= np.maximum(16, np.abs(samples.astype(int)) * 0.035))


def test_mulaw_handles_full_scale_without_overflow() -> None:
    extremes = np.array([-32768, 32767], dtype=np.int16)
    decoded = mulaw_decode(mulaw_encode(extremes))
    assert decoded[0] < -30000
    assert decoded[1] > 30000


# Resampling ----------------------------------------------------------------------


@pytest.mark.parametrize(("src", "dst"), [(8000, 24000), (24000, 8000), (24000, 24000)])
def test_resampling_keeps_pitch_and_length(src: int, dst: int) -> None:
    out = Resampler(src, dst).process(tone(440, src, 0.5))
    assert len(out) == int(0.5 * dst)
    assert dominant_frequency(out, dst) == pytest.approx(440, abs=4)


def test_downsampling_filters_frequencies_above_nyquist() -> None:
    # 6 kHz cannot exist at 8 kHz; without filtering it would alias to 2 kHz.
    out = Resampler(24000, 8000).process(tone(6000, 24000, 0.5))
    assert rms_dbfs(out[200:]) < -40


def test_streaming_matches_one_shot() -> None:
    signal = tone(300, 8000, 0.3)
    one_shot = Resampler(8000, 24000).process(signal)
    streaming = Resampler(8000, 24000)
    chunks = [streaming.process(c) for c in np.array_split(signal, 17)]
    assert np.array_equal(np.concatenate(chunks), one_shot)


def test_streaming_downsample_matches_one_shot_with_odd_chunks() -> None:
    signal = tone(300, 24000, 0.3)
    one_shot = Resampler(24000, 8000).process(signal)
    streaming = Resampler(24000, 8000)
    chunks = [streaming.process(c) for c in np.array_split(signal, 13)]
    assert np.array_equal(np.concatenate(chunks), one_shot)


def test_non_integer_ratios_are_rejected() -> None:
    with pytest.raises(ValueError, match="integer"):
        Resampler(44100, 24000)


def test_pcm16_bytes_round_trip() -> None:
    samples = tone(200, 24000, 0.01)
    assert np.array_equal(pcm16_to_array(array_to_pcm16(samples)), samples)
    assert len(pcm16_to_array(b"\x01\x02\x03")) == 1  # odd trailing byte ignored


# VAD ------------------------------------------------------------------------------


def test_vad_timestamps_speech_and_ignores_silence() -> None:
    vad = EnergyVAD()
    silence = np.zeros(480, dtype=np.int16)  # 20 ms
    speech = tone(200, 24000, 0.02, amplitude=0.2)
    assert not vad.process(silence, now=1.0)
    assert vad.last_voiced_at is None
    assert not vad.process(speech, now=1.02)  # 20 ms is not enough yet
    assert not vad.process(speech, now=1.04)
    assert vad.process(speech, now=1.06)
    assert vad.process(speech, now=1.08)
    vad.process(silence, now=1.10)
    assert vad.last_voiced_at == 1.08


def test_vad_ignores_short_clicks() -> None:
    vad = EnergyVAD()
    click = tone(1000, 24000, 0.02, amplitude=0.9)
    for t in (1.0, 1.5, 2.0):
        vad.process(click, now=t)
        vad.process(np.zeros(480, dtype=np.int16), now=t + 0.02)
    assert vad.last_voiced_at is None
