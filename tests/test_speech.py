"""
Tests for sentence streaming and speech text cleanup.
"""

import asyncio


from src.ai.speech import SentenceSplitter, SpeechStreamer, clean_for_speech, strip_animation_tags


class TestSentenceSplitter:

    def test_splits_on_terminal_punctuation(self):
        s = SentenceSplitter()
        out = s.feed("Hello there! I am Pepper. How are you? ")
        assert out == ["Hello there!", "I am Pepper.", "How are you?"]
        assert s.flush() is None

    def test_streams_across_chunks(self):
        s = SentenceSplitter()
        got = []
        for chunk in ["Hel", "lo the", "re! I am", " Pepper. And", " you"]:
            got += s.feed(chunk)
        assert got == ["Hello there!", "I am Pepper."]
        assert s.flush() == "And you"

    def test_no_split_on_decimal(self):
        s = SentenceSplitter()
        assert s.feed("I can drive 1.5 metres. Then stop. ") == ["I can drive 1.5 metres.", "Then stop."]

    def test_splits_on_newline(self):
        s = SentenceSplitter()
        assert s.feed("First line\nSecond line\n") == ["First line", "Second line"]

    def test_closing_quote_after_punctuation(self):
        s = SentenceSplitter()
        assert s.feed('She said "hi." Then left. ') == ['She said "hi."', "Then left."]

    def test_short_fragment_glued_to_next(self):
        s = SentenceSplitter(min_chars=3)
        assert s.feed("A. Long sentence here. ") == ["A. Long sentence here."]


class TestCleanForSpeech:

    def test_strips_markdown_and_emoji(self):
        assert clean_for_speech("**Hello** _there_ 👋 `code` # Title") == "Hello there code Title"

    def test_links_and_bullets(self):
        assert clean_for_speech("- [Pepper](http://x) is here\n1. yes") == "Pepper is here\nyes"

    def test_keeps_animation_tags_for_robot(self):
        assert (
            clean_for_speech("^start(animations/Stand/Gestures/Hey_1) Hi!")
            == "^start(animations/Stand/Gestures/Hey_1) Hi!"
        )

    def test_strip_animation_tags_for_display(self):
        assert strip_animation_tags("^start(animations/Stand/Gestures/Hey_1) Hi! ^wait(x) Bye") == "Hi! Bye"


class TestSpeechStreamer:

    async def test_speaks_in_order_and_finishes(self):
        spoken = []

        async def speak(text):
            await asyncio.sleep(0.005)
            spoken.append(text)

        streamer = SpeechStreamer(speak)
        await streamer.on_text("One. Two. Thr")
        await streamer.on_text("ee")
        result = await streamer.finish()
        assert result == ["One.", "Two.", "Three"]
        assert spoken == result

    async def test_disabled_does_not_speak_but_reports_sentences(self):
        spoken = []
        seen = []

        async def speak(text):
            spoken.append(text)

        async def on_sentence(text):
            seen.append(text)

        streamer = SpeechStreamer(speak, enabled=False, on_sentence=on_sentence)
        await streamer.on_text("Hello. World.")
        await streamer.finish()
        assert spoken == [] and seen == ["Hello.", "World."]

    async def test_speech_errors_are_collected(self):
        async def speak(text):
            raise RuntimeError("tts down")

        streamer = SpeechStreamer(speak)
        await streamer.on_text("Hi there. ")
        await streamer.finish()
        assert streamer.errors == ["tts down"] and streamer.spoken == []

    async def test_finish_with_nothing(self):
        streamer = SpeechStreamer(lambda t: asyncio.sleep(0))
        assert await streamer.finish() == []
