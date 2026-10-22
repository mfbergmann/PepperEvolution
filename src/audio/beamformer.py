"""
Beamforming over Pepper's four head microphones (issue #34).

Pepper's own fans make most of the noise its microphones hear, and that noise sits in the speech band, so filtering one
channel cannot remove it (neural denoisers made recognition worse, 2026-10-22). Four microphones can: the fan noise
reaches them with a fixed pattern, while a voice comes from one direction. This is a streaming MVDR beamformer:

- per frequency bin, the noise's spatial covariance is learned from frames without speech (slowly: the fans are
  steady), and the speech's from frames with speech (quickly: people move and the head turns);
- the voice's relative transfer function to the front microphone is the principal generalized eigenvector of the two
  (covariance whitening), so no microphone geometry is needed;
- the weights pass the voice as the front microphone hears it (distortionless) and minimise everything else.

Until enough speech has been heard, and for frames where the beamformer would cut the voice itself by more than
MAX_SUPPRESSION_DB, the front microphone passes through unchanged. Offline, on four-channel recordings from the robot
(open space and office, 2026-10-22), an oracle version gained 2-4 dB of speech above noise.
"""

from collections import deque
from typing import Deque, Optional

import numpy as np

CHANNELS = 4  # ALAudioDevice's order: left, right, front, rear
REFERENCE = 2  # the front microphone, the one used before beamforming
N_FFT = 512  # 32 ms at 16 kHz
HOP = 256
SPEECH_BAND = (300.0, 3400.0)
NOISE_MEMORY = 0.995  # per hop (16 ms): about 3 s, the fans are steady
SPEECH_MEMORY = 0.97  # about 0.5 s: a voice moves, and the head turns while tracking a face
ACTIVITY_HOPS = 8  # speech or noise is judged on the front microphone's energy over the last 128 ms: single hops
# of fan noise vary too much (quiet hops sat 1.4-4.3 dB above the floor, speech 2.1-9.8, robot 2026-10-22)
SPEECH_ABOVE_FLOOR_DB = 6.0  # this far above the noise floor counts as speech
NOISE_ABOVE_FLOOR_DB = 2.5  # and up to this, as noise
FLOOR_WINDOW = 375  # hops (6 s): the floor is the quietest activity level in this window (minimum statistics). A
# floor that crept upwards instead rose into continuous speech and taught the noise model the voice (offline)
NOISE_HANGOVER = 10  # hops (0.16 s) after a louder moment before the noise model learns again
MIN_SPEECH_FRAMES = 40  # about 0.6 s of speech before the weights are trusted
UPDATE_EVERY = 8  # hops between weight updates (128 ms)
WEIGHT_SMOOTHING = 0.6  # share of the old weights kept at each update
MIN_BIN_SNR = 1.5  # generalized eigenvalue below which a bin keeps the front microphone
LOADING = 1e-3  # diagonal loading, relative to the noise power
MAX_SUPPRESSION_DB = 12.0  # in a speech frame, never end up this much quieter than the front microphone


class Beamformer:
    """Streaming: feed float samples shaped (n, CHANNELS) of any length, get mono samples back (delayed by HOP)."""

    def __init__(self, sample_rate: int = 16000, channels: int = CHANNELS, reference: int = REFERENCE):
        self.sample_rate = sample_rate
        self.channels = channels
        self.reference = reference
        self.window = np.sqrt(np.hanning(N_FFT + 1)[:N_FFT])  # sqrt-Hann at 50 %: perfect reconstruction
        freqs = np.fft.rfftfreq(N_FFT, 1.0 / sample_rate)
        self.band = (freqs >= SPEECH_BAND[0]) & (freqs < SPEECH_BAND[1])
        self.bins = len(freqs)
        self.reset()

    def reset(self):
        """Forget everything (a new stream: the bridge reconnected, or the robot was restarted)."""
        eye = np.eye(self.channels, dtype=complex)
        self.noise_cov = np.tile(eye * 1e-9, (self.bins, 1, 1))
        self.speech_cov = np.tile(eye * 1e-9, (self.bins, 1, 1))
        self.weights = np.zeros((self.bins, self.channels), dtype=complex)
        self.weights[:, self.reference] = 1.0
        self.floor: Optional[float] = None
        self._energies: Deque[float] = deque(maxlen=FLOOR_WINDOW)
        self._recent: Deque[float] = deque(maxlen=ACTIVITY_HOPS)
        self._since_loud = NOISE_HANGOVER
        self.speech_frames = 0
        self.noise_frames = 0
        self.hops = 0
        self.adapted = False
        self._input = np.zeros((N_FFT, self.channels))
        self._filled = N_FFT - HOP  # the first frame needs only HOP new samples
        self._pending = np.zeros((0, self.channels))
        self._overlap = np.zeros(N_FFT)
        self.passthrough_frames = 0

    def process(self, samples: np.ndarray) -> np.ndarray:
        """Beamform a chunk; returns as many mono samples as whole hops were completed."""
        samples = np.asarray(samples, dtype=float).reshape(-1, self.channels)
        data = np.concatenate([self._pending, samples]) if len(self._pending) else samples
        out = []
        start = 0
        while len(data) - start >= HOP:
            self._input = np.roll(self._input, -HOP, axis=0)
            self._input[-HOP:] = data[start : start + HOP]
            start += HOP
            out.append(self._hop())
        self._pending = data[start:].copy()
        return np.concatenate(out) if out else np.zeros(0)

    def process_pcm(self, pcm: bytes) -> bytes:
        """Interleaved 16-bit PCM in, mono 16-bit PCM out."""
        x = np.frombuffer(pcm, dtype="<i2").astype(float) / 32768.0
        y = self.process(x[: len(x) // self.channels * self.channels])
        return (np.clip(y, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()

    # -- one hop --------------------------------------------------------------------------------------------------

    def _hop(self) -> np.ndarray:
        spectrum = np.fft.rfft(self._input * self.window[:, None], axis=0)  # bins x channels
        ref = spectrum[:, self.reference]
        energy = float(np.sum(np.abs(ref[self.band]) ** 2)) + 1e-20
        if self.hops >= 2:  # the first frames still hold the zeros the buffer started with
            self._recent.append(energy)
        activity = sum(self._recent) / len(self._recent) if self._recent else energy
        if len(self._recent) == ACTIVITY_HOPS:
            self._energies.append(activity)
        self.floor = min(self._energies) if self._energies else activity
        above = 10.0 * np.log10(activity / self.floor)
        outer = spectrum[:, :, None] * spectrum[:, None, :].conj()  # bins x C x C
        speech = above >= SPEECH_ABOVE_FLOOR_DB
        self._since_loud = 0 if above > NOISE_ABOVE_FLOOR_DB else self._since_loud + 1
        if self._since_loud >= NOISE_HANGOVER:
            self.noise_cov = NOISE_MEMORY * self.noise_cov + (1 - NOISE_MEMORY) * outer
            self.noise_frames += 1
        elif speech:
            self.speech_cov = SPEECH_MEMORY * self.speech_cov + (1 - SPEECH_MEMORY) * outer
            self.speech_frames += 1
        self.hops += 1
        if self.hops % UPDATE_EVERY == 0 and self.speech_frames >= MIN_SPEECH_FRAMES and self.noise_frames >= 30:
            self._update_weights()
        output = np.einsum("bc,bc->b", self.weights.conj(), spectrum)
        if speech and self.adapted:
            kept = float(np.sum(np.abs(output[self.band]) ** 2))
            if kept < energy * 10 ** (-MAX_SUPPRESSION_DB / 10.0):
                output = ref  # the beamformer is cutting the voice itself: trust the front microphone
                self.passthrough_frames += 1
        frame = np.fft.irfft(output, N_FFT) * self.window
        self._overlap += frame
        result = self._overlap[:HOP].copy()
        self._overlap = np.concatenate([self._overlap[HOP:], np.zeros(HOP)])
        return result

    def _update_weights(self):
        eye = np.eye(self.channels)
        noise_power = np.real(np.trace(self.noise_cov, axis1=1, axis2=2)) / self.channels
        noise = self.noise_cov + (LOADING * noise_power + 1e-12)[:, None, None] * eye
        try:
            whitened = np.linalg.solve(noise, self.speech_cov)
            values, vectors = np.linalg.eig(whitened)
        except np.linalg.LinAlgError:
            return
        best = np.argmax(values.real, axis=1)
        snr = values.real[np.arange(self.bins), best]
        principal = vectors[np.arange(self.bins), :, best]  # bins x C
        rtf = np.einsum("bij,bj->bi", noise, principal)
        anchor = rtf[:, self.reference]
        usable = (snr > MIN_BIN_SNR) & (np.abs(anchor) > 1e-12) & np.all(np.isfinite(rtf), axis=1)
        rtf = rtf / np.where(np.abs(anchor) > 1e-12, anchor, 1.0)[:, None]
        solved = np.linalg.solve(noise, rtf[:, :, None])[:, :, 0]  # R^-1 d
        norm = np.einsum("bc,bc->b", rtf.conj(), solved)
        new = solved / np.where(np.abs(norm) > 1e-20, norm, 1.0)[:, None]
        fallback = np.zeros_like(new)
        fallback[:, self.reference] = 1.0
        new = np.where((usable & np.all(np.isfinite(new), axis=1))[:, None], new, fallback)
        self.weights = WEIGHT_SMOOTHING * self.weights + (1 - WEIGHT_SMOOTHING) * new
        self.adapted = True


class Downsampler:
    """48 kHz to 16 kHz for interleaved multi-channel float samples, streaming (an older bridge under Python 3.13+,
    without audioop, sends the four microphones at 48 kHz)."""

    TAPS = 63

    def __init__(self, channels: int = CHANNELS, factor: int = 3):
        self.factor = factor
        n = np.arange(self.TAPS) - self.TAPS // 2
        self.kernel = np.sinc(n / factor) / factor * np.hamming(self.TAPS)  # low-pass just below the new Nyquist
        self.history = np.zeros((self.TAPS - 1, channels))
        self.phase = 0

    def process(self, samples: np.ndarray) -> np.ndarray:
        data = np.concatenate([self.history, samples])
        filtered = np.stack(
            [np.convolve(data[:, c], self.kernel, mode="valid") for c in range(data.shape[1])], axis=1
        )  # one output per input sample
        self.history = data[-(self.TAPS - 1) :]
        picked = filtered[self.phase :: self.factor]
        self.phase = (self.phase - len(filtered)) % self.factor
        return picked
