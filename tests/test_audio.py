"""
Tests for the audio helpers: PCM utilities, the energy endpointer and the
speech-to-text backends that can run without their models.
"""

import struct
from types import SimpleNamespace

import pytest

from src.audio import (
    EnergyVAD,
    Endpointer,
    FakeTranscriber,
    TranscriberUnavailable,
    duration,
    make_transcriber,
    resample,
    rms,
    silence,
    tone,
    wav_bytes,
)
from src.audio.stt import SherpaTranscriber, WhisperTranscriber


class TestPcm:
    def test_rms_and_duration(self):
        assert rms(silence(0.1)) == 0.0
        assert 5500 < rms(tone(0.1, amplitude=8000)) < 5800  # sine: amplitude / sqrt(2)
        assert duration(silence(0.5)) == 0.5
        assert rms(b"") == 0.0 and rms(b"\x01") == 0.0  # odd trailing byte ignored

    def test_resample(self):
        src = tone(0.1, sample_rate=48000)
        out = resample(src, 48000, 16000)
        assert len(out) == len(silence(0.1))
        assert abs(rms(out) - rms(src)) < 150
        assert resample(src, 16000, 16000) is src
        assert resample(b"", 48000, 16000) == b""
        assert len(resample(tone(0.1), 16000, 48000)) == len(silence(0.1, sample_rate=48000))

    def test_wav_container(self):
        data = wav_bytes(silence(0.01), 16000)
        assert data[:4] == b"RIFF" and data[8:12] == b"WAVE"
        assert struct.unpack("<I", data[24:28])[0] == 16000
        assert data[-320:] == silence(0.01)


class TestEnergyVAD:
    def test_threshold_follows_the_background(self):
        vad = EnergyVAD(min_rms=100, ratio=3.0, floor_alpha=0.5)
        background = tone(0.02, amplitude=212)  # rms ~150
        for _ in range(20):
            assert vad.is_speech(background) is False
        assert 140 < vad.noise_floor < 160
        assert vad.is_speech(tone(0.02, amplitude=424)) is False  # 300 rms: above min_rms, not 3x the floor
        assert 220 < vad.noise_floor < 230  # ...so it counts as background and pulls the floor up
        assert vad.is_speech(tone(0.02, amplitude=2000)) is True
        assert 220 < vad.noise_floor < 230  # speech frames do not raise the floor
        vad.reset()
        assert vad.noise_floor == 100


class TestEndpointer:
    def make(self, **kw):
        kw.setdefault("clock", lambda: 100.0)
        return Endpointer(**kw)

    def test_speech_between_silences_is_one_utterance(self):
        ep = self.make()
        utterances = ep.feed(silence(0.5) + tone(1.0) + silence(1.0))
        assert len(utterances) == 1
        utterance = utterances[0]
        # 300 ms pre-roll + 100 ms start + 900 ms more tone + 800 ms end silence
        assert utterance.duration == pytest.approx(2.1)
        assert duration(utterance.pcm) == pytest.approx(2.1)
        assert utterance.started_at == 100.0
        assert ep.utterances == 1 and ep.speaking is False and ep.dropped == 0

    def test_chunk_boundaries_do_not_matter(self):
        audio = silence(0.5) + tone(1.0) + silence(1.0)
        whole = self.make().feed(audio)
        ep = self.make()
        chunked = []
        for i in range(0, len(audio), 101):
            chunked.extend(ep.feed(audio[i : i + 101]))
        assert [u.duration for u in chunked] == [u.duration for u in whole]
        assert chunked[0].pcm == whole[0].pcm

    def test_speaking_flag_while_in_speech(self):
        ep = self.make()
        assert ep.feed(silence(0.5) + tone(0.5)) == []
        assert ep.speaking is True

    def test_short_blip_is_dropped(self):
        ep = self.make()
        assert ep.feed(silence(0.3) + tone(0.15) + silence(1.0)) == []
        assert ep.dropped == 1 and ep.utterances == 0

    def test_long_speech_is_split_at_max_length(self):
        ep = self.make(max_utterance_s=1.0)
        utterances = ep.feed(tone(2.5) + silence(1.0))
        assert len(utterances) == 3
        assert [u.duration for u in utterances] == [pytest.approx(1.0)] * 3
        assert ep.speaking is False

    def test_flush_ends_the_current_utterance(self):
        ep = self.make()
        assert ep.feed(silence(0.5) + tone(0.6)) == []
        utterance = ep.flush()
        assert utterance is not None and utterance.duration == pytest.approx(0.9)
        assert ep.speaking is False and ep.flush() is None
        assert self.make().flush() is None

    def test_flush_returns_an_utterance_finished_by_the_padded_frame(self):
        ep = self.make()
        assert ep.feed(silence(0.5) + tone(1.0) + silence(0.78) + b"\x00" * 10) == []
        utterance = ep.flush()
        assert utterance is not None and ep.utterances == 1 and ep.speaking is False

    def test_reset(self):
        ep = self.make()
        ep.feed(silence(0.5) + tone(0.5))
        ep.reset()
        assert ep.speaking is False and ep.feed(silence(0.1)) == []


class TestFakeTranscriber:
    async def test_batch(self):
        t = FakeTranscriber(["hello"])
        await t.start()
        result = await t.transcribe(tone(0.5))
        assert result.text == "hello" and result.final and result.duration == 0.5 and result.backend == "fake"
        assert (await t.transcribe(tone(0.1))).text == ""
        assert len(t.received) == 2

    async def test_streaming(self):
        t = FakeTranscriber(["hello there"], streaming=True, endpoint_after=1.0)
        assert await t.process(tone(0.4)) == []
        partial = await t.process(tone(0.2))
        assert len(partial) == 1 and partial[0].text == "hello" and partial[0].final is False
        final = await t.process(tone(0.5))
        assert len(final) == 1 and final[0].text == "hello there" and final[0].final is True
        assert duration(t.received[0]) == pytest.approx(1.1)
        assert await t.flush() == []


class TestFactory:
    def test_none_and_fake(self):
        assert make_transcriber("none") is None
        assert make_transcriber("") is None and make_transcriber("off") is None
        assert isinstance(make_transcriber("fake"), FakeTranscriber)
        with pytest.raises(ValueError):
            make_transcriber("bogus")

    def test_missing_packages_explain_what_to_install(self):
        for backend, module in (("whisper", "faster_whisper"), ("sherpa", "sherpa_onnx")):
            try:
                __import__(module)
            except ImportError:
                with pytest.raises(TranscriberUnavailable) as info:
                    make_transcriber(backend, model="x")
                assert "pip install" in str(info.value)


class TestSherpaFiles:
    def test_prefers_int8_models(self, tmp_path):
        (tmp_path / "encoder-epoch-99-avg-1.onnx").write_bytes(b"")
        (tmp_path / "encoder-epoch-99-avg-1.int8.onnx").write_bytes(b"")
        assert SherpaTranscriber._pick(str(tmp_path), "encoder").endswith("int8.onnx")
        with pytest.raises(TranscriberUnavailable):
            SherpaTranscriber._pick(str(tmp_path), "joiner")

    async def test_start_needs_a_model_directory(self, tmp_path):
        pytest.importorskip("sherpa_onnx")
        with pytest.raises(TranscriberUnavailable):
            await SherpaTranscriber("").start()
        with pytest.raises(TranscriberUnavailable):
            await SherpaTranscriber(str(tmp_path)).start()  # no tokens.txt


class TestWhisperFiltering:
    class Model:
        def __init__(self, segments):
            self.segments = segments
            self.calls = []

        def transcribe(self, audio, **kwargs):
            self.calls.append(kwargs)
            return iter(self.segments), SimpleNamespace(language="en")

    def test_unsure_segments_are_dropped(self):
        t = WhisperTranscriber(model="base", language="en")
        t._model = self.Model(
            [
                SimpleNamespace(text=" Hello Pepper. ", no_speech_prob=0.1, compression_ratio=1.2),
                SimpleNamespace(text=" Thank you for watching ", no_speech_prob=0.9, compression_ratio=1.0),
                SimpleNamespace(text=" la la la la la la ", no_speech_prob=0.1, compression_ratio=3.0),
                SimpleNamespace(text=" Wave. ", no_speech_prob=0.2, compression_ratio=1.1),
            ]
        )
        assert t._run(None) == "Hello Pepper. Wave."
        assert t._model.calls[0]["language"] == "en" and t._model.calls[0]["vad_filter"] is False

    async def test_transcribe_runs_the_model_off_the_loop(self):
        pytest.importorskip("numpy")
        t = WhisperTranscriber()
        t._model = self.Model([SimpleNamespace(text="hi", no_speech_prob=0.0, compression_ratio=1.0)])
        result = await t.transcribe(tone(0.5))
        assert result.text == "hi" and result.final and result.duration == 0.5 and result.backend == "whisper"
        await t.close()
