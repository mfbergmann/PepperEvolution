"""
Tests for the streaming beamformer (src/audio/beamformer.py, issue #34): a synthetic room with a voice arriving at
four microphones with different delays, and a fan noise that reaches them with a fixed, partly shared pattern.
"""

import numpy as np
import pytest

from src.audio.beamformer import HOP, REFERENCE, Beamformer, Downsampler

SR = 16000


def room(seconds_quiet=6.0, seconds_talk=6.0, seed=1):
    """(quiet then talking, 4 channels): speech-like bursts from one direction plus correlated fan noise."""
    rng = np.random.default_rng(seed)
    n_q, n_t = int(seconds_quiet * SR), int(seconds_talk * SR)
    n = n_q + n_t
    fan = rng.normal(size=(n, 2))
    mix = np.array([[1.0, 0.3], [0.8, 0.6], [0.5, 0.9], [0.9, 0.2]])  # each microphone hears the two fans differently
    noise = 0.05 * fan @ mix.T + 0.01 * rng.normal(size=(n, 4))
    t = np.arange(n_t) / SR
    voice = np.sin(2 * np.pi * 440 * t) + 0.6 * np.sin(2 * np.pi * 880 * t) + 0.4 * np.sin(2 * np.pi * 1500 * t)
    voice *= 0.1 * (np.sin(2 * np.pi * 2.5 * t) > -0.2)  # syllable-like bursts, in the speech band
    speech = np.zeros((n, 4))
    for c, delay in enumerate((0, 3, 1, 5)):  # samples of delay to each microphone
        speech[n_q + delay :, c] = voice[: n_t - delay]
    return noise + speech, noise, speech


def run(bf, x, chunk=1365):
    y = np.concatenate([bf.process(x[i : i + chunk]) for i in range(0, len(x), chunk)])
    return np.concatenate([y[HOP:], np.zeros(HOP)])[: len(x)]  # undo the one-hop delay


class TestBeamformer:
    def test_passes_the_front_microphone_through_before_it_has_heard_speech(self):
        x, _, _ = room(seconds_talk=0.0)
        y = run(Beamformer(), x)
        assert not Beamformer().adapted
        assert np.allclose(y[HOP : -2 * HOP], x[HOP : -2 * HOP, REFERENCE], atol=1e-9)

    def test_learns_to_lower_the_fan_noise_and_keep_the_voice(self):
        x, noise, speech = room()
        bf = Beamformer()
        run(bf, x)  # first pass: adapt
        y = run(bf, x)  # second pass: scored
        assert bf.adapted
        quiet = slice(2 * SR, 5 * SR)
        noise_drop = 10 * np.log10(np.mean(y[quiet] ** 2) / np.mean(x[quiet, REFERENCE] ** 2))
        assert noise_drop < -3.0
        talk = slice(8 * SR, 12 * SR)
        kept = 10 * np.log10(np.mean(y[talk] ** 2) / np.mean(x[talk, REFERENCE] ** 2))
        assert kept > -6.0  # the voice is not cancelled

    def test_silence_and_odd_chunk_sizes(self):
        bf = Beamformer()
        assert bf.process(np.zeros((100, 4))).size == 0  # less than a hop: kept for later
        out = bf.process(np.zeros((500, 4)))
        assert out.size == 2 * HOP and np.all(out == 0.0) and np.all(np.isfinite(out))

    def test_pcm_in_and_out(self):
        bf = Beamformer()
        pcm = (np.ones((HOP * 4, 4)) * 1000).astype("<i2").tobytes()
        out = bf.process_pcm(pcm)
        assert len(out) == HOP * 4 * 2

    def test_reset_forgets(self):
        x, _, _ = room()
        bf = Beamformer()
        run(bf, x)
        bf.reset()
        assert not bf.adapted and bf.speech_frames == 0 and np.allclose(bf.weights[:, REFERENCE], 1.0)


class TestDownsampler:
    @pytest.mark.parametrize("chunk", [4095, 1000, 7])
    def test_streams_48k_to_16k_without_gaps(self, chunk):
        t = np.arange(48000 * 2) / 48000
        x = np.stack([np.sin(2 * np.pi * 440 * t)] * 4, axis=1)
        d = Downsampler()
        y = np.concatenate([d.process(x[i : i + chunk]) for i in range(0, len(x), chunk)])
        assert abs(len(y) - len(x) // 3) <= 1
        ref = np.sin(2 * np.pi * 440 * (np.arange(len(y)) * 3 - Downsampler.TAPS // 2) / 48000)
        assert np.max(np.abs(y[100:-100, 0] - ref[100:-100])) < 0.02  # the tone survives, delayed by half the filter

    def test_removes_what_16k_cannot_hold(self):
        t = np.arange(48000) / 48000
        x = np.stack([np.sin(2 * np.pi * 12000 * t)] * 4, axis=1)  # above the new Nyquist (8 kHz)
        y = Downsampler().process(x)
        assert np.sqrt(np.mean(y[50:-50, 0] ** 2)) < 0.02
