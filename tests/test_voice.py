"""
Tests for VoiceInput: the robot-microphone path, push-to-talk and callbacks.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from src.audio import Endpointer, FakeTranscriber, VoiceInput, silence, tone


class FakeSource:
    def __init__(self):
        self.callbacks = []
        self.connected = False
        self.started = False

    def on_audio(self, cb):
        self.callbacks.append(cb)

    async def start(self):
        self.started = True
        self.connected = True

    async def stop(self):
        self.connected = False

    async def push(self, pcm, chunk=3200):  # 100 ms chunks like the bridge (a bit smaller)
        for i in range(0, len(pcm), chunk):
            for cb in self.callbacks:
                await cb(pcm[i : i + chunk])


def fake_manager():
    manager = MagicMock()
    manager.busy = False
    manager.process_user_input = AsyncMock(return_value={"text": "ok", "spoken": []})
    manager.signal_state = AsyncMock()
    return manager


async def settle(voice, manager, attempts=200):
    for _ in range(attempts):
        if manager.process_user_input.await_count or voice.last_error or voice.ignored:
            break
        await asyncio.sleep(0)
    if voice._tasks:
        await asyncio.gather(*voice._tasks, return_exceptions=True)


def collector(payloads):
    async def cb(payload):
        payloads.append(payload)

    return cb


class TestRobotMicrophone:
    async def test_utterance_is_transcribed_and_sent_to_the_manager(self):
        manager, source, payloads = fake_manager(), FakeSource(), []
        voice = VoiceInput(
            manager, FakeTranscriber(["wave at me"]), source=source, endpointer=Endpointer(clock=lambda: 1.0)
        )
        voice.on_transcript(collector(payloads))
        await voice.start()
        assert source.started and voice.started
        await source.push(silence(0.5) + tone(1.0) + silence(1.0))
        await settle(voice, manager)
        manager.process_user_input.assert_awaited_once_with("wave at me", source="voice", client_id=None)
        assert voice.transcripts == 1 and voice.utterances == 1 and voice.last_transcript == "wave at me"
        manager.signal_state.assert_any_await("listening")
        assert payloads[-1] == {
            "text": "wave at me",
            "final": True,
            "source": "voice",
            "client_id": None,
            "delivered": True,
        }
        status = voice.status()
        assert status["enabled"] and status["backend"] == "fake" and status["source_connected"] is True
        assert status["streaming"] is False and status["robot_microphone"] is True
        await voice.stop()
        assert source.connected is False and voice.started is False

    async def test_empty_transcript_is_ignored(self):
        manager, source, payloads = fake_manager(), FakeSource(), []
        voice = VoiceInput(manager, FakeTranscriber([]), source=source)
        voice.on_transcript(collector(payloads))
        await voice.start()
        await source.push(silence(0.5) + tone(1.0) + silence(1.0))
        await settle(voice, manager)
        manager.process_user_input.assert_not_awaited()
        assert voice.ignored == 1 and voice.transcripts == 0
        assert payloads[-1]["ignored"] == "empty" and payloads[-1]["delivered"] is False
        assert manager.signal_state.await_args_list[-1].args == ("idle",)

    async def test_streaming_backend_partials_then_final(self):
        manager, source, payloads = fake_manager(), FakeSource(), []
        voice = VoiceInput(manager, FakeTranscriber(["look at me"], streaming=True, endpoint_after=1.0), source=source)
        voice.on_transcript(collector(payloads))
        await voice.start()
        await source.push(tone(1.2))
        await settle(voice, manager)
        manager.process_user_input.assert_awaited_once_with("look at me", source="voice", client_id=None)
        assert [p["final"] for p in payloads] == [False, True]
        assert payloads[0]["text"] == "look" and payloads[1]["delivered"] is True
        manager.signal_state.assert_any_await("listening")
        assert voice.status()["streaming"] is True

    async def test_streaming_finals_do_not_block_the_audio_loop(self):
        manager, source = fake_manager(), FakeSource()
        release = asyncio.Event()

        async def slow_turn(text, **kwargs):
            await release.wait()
            return {"text": "ok"}

        manager.process_user_input = AsyncMock(side_effect=slow_turn)
        voice = VoiceInput(manager, FakeTranscriber(["hi", "again"], streaming=True, endpoint_after=1.0), source=source)
        await voice.start()
        await asyncio.wait_for(source.push(tone(2.4)), timeout=1.0)  # two finals; neither waits for the manager
        assert len(voice._tasks) == 2
        await asyncio.sleep(0)  # let the spawned deliveries start (and block on the manager)
        assert manager.process_user_input.await_count == 2
        release.set()
        await settle(voice, manager)
        assert voice.utterances == 2

    async def test_transcription_error_is_contained(self):
        manager, source = fake_manager(), FakeSource()
        transcriber = FakeTranscriber(["x"])
        transcriber.transcribe = AsyncMock(side_effect=RuntimeError("model exploded"))
        voice = VoiceInput(manager, transcriber, source=source)
        await voice.start()
        await source.push(silence(0.5) + tone(1.0) + silence(1.0))
        await settle(voice, manager)
        manager.process_user_input.assert_not_awaited()
        assert voice.last_error == "model exploded"
        assert voice.status()["last_error"] == "model exploded"

    async def test_audio_error_does_not_kill_the_stream(self):
        manager, source = fake_manager(), FakeSource()
        transcriber = FakeTranscriber(streaming=True)
        transcriber.process = AsyncMock(side_effect=RuntimeError("boom"))
        voice = VoiceInput(manager, transcriber, source=source)
        await voice.start()
        await source.push(tone(0.2))  # must not raise
        assert voice.last_error == "boom"

    async def test_no_source_means_push_to_talk_only(self):
        voice = VoiceInput(fake_manager(), FakeTranscriber())
        await voice.start()
        assert voice.status()["robot_microphone"] is False and voice.status()["source_connected"] is False
        await voice.stop()


class TestPushToTalk:
    async def test_handle_utterance_returns_text_and_response(self):
        manager = fake_manager()
        voice = VoiceInput(manager, FakeTranscriber(["hello"]))
        result = await voice.handle_utterance(tone(0.5), client_id="abc")
        assert result == {"text": "hello", "response": {"text": "ok", "spoken": []}}
        manager.process_user_input.assert_awaited_once_with("hello", source="voice", client_id="abc")
        manager.signal_state.assert_any_await("listening")
        assert voice.utterances == 1 and voice.transcripts == 1

    async def test_other_sample_rates_are_resampled(self):
        transcriber = FakeTranscriber(["hi"])
        voice = VoiceInput(fake_manager(), transcriber)
        await voice.handle_utterance(tone(0.5, sample_rate=48000), sample_rate=48000)
        assert len(transcriber.received[0]) == 16000  # 0.5 s at 16 kHz, 2 bytes per sample

    async def test_utterances_can_be_recorded(self, tmp_path):
        voice = VoiceInput(fake_manager(), FakeTranscriber(["hi", "there"]), record_dir=str(tmp_path / "rec"))
        await voice.start()
        await voice.handle_utterance(tone(0.5))
        await voice.handle_utterance(tone(0.5))  # same second: must not overwrite
        files = list((tmp_path / "rec").glob("utterance_*.wav"))
        assert len(files) == 2 and files[0].read_bytes()[:4] == b"RIFF"

    async def test_load_is_separate_from_start(self):
        transcriber = FakeTranscriber(["hi"])
        voice = VoiceInput(fake_manager(), transcriber, source=FakeSource())
        await voice.load()
        assert transcriber.started and voice.started is False and voice.source.started is False
        await voice.start()
        assert voice.started and voice.source.started

    async def test_ignored_and_error_results(self):
        manager = fake_manager()
        voice = VoiceInput(manager, FakeTranscriber(["..."]))
        assert await voice.handle_utterance(tone(0.5)) == {"text": "...", "ignored": "empty"}
        manager.process_user_input.assert_not_awaited()
        broken = FakeTranscriber()
        broken.transcribe = AsyncMock(side_effect=RuntimeError("no model"))
        voice = VoiceInput(manager, broken)
        result = await voice.handle_utterance(tone(0.5))
        assert result["ignored"] == "error" and result["error"] == "no model"
