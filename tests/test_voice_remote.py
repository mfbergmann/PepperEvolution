"""
Voice embeddings from a GPU machine (VOICE_ID_URL): the client, the fallback to the CPU, and the server's /embed. Fake
embeddings only: no model, no real audio, no names of real people.
"""

import base64
import importlib.util
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import httpx
import numpy as np
import pytest

from src.identity import Enrolled, VoiceIdentifier, read_voice, unit
from src.identity.voice import RemoteSpeakerModel, clip_spans, samples_from
from src.memory import MemoryStore

ROOT = Path(__file__).resolve().parents[1]
A = unit([1.0, 0.0, 0.0, 0.0])
B = unit([0.0, 1.0, 0.0, 0.0])


class Embedding:
    """A fake of the server's speaker model, on both sides of the comparison."""

    name = "fake-voice"
    dim = 4

    def __init__(self):
        self.seen = []

    def embed(self, pcm, spans):
        self.seen.append((len(pcm), list(spans)))
        return np.stack([A for _ in spans], dtype=np.float32)


def server_module():
    spec = importlib.util.spec_from_file_location("stt_server", ROOT / "scripts" / "stt_server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def server():
    module = server_module()
    embedder = Embedding()
    recognizer = SimpleNamespace(args={"provider": "cpu"}, transcribe=lambda pcm, language: "words")
    http = ThreadingHTTPServer(("127.0.0.1", 0), module.handler(recognizer, embedder))
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{http.server_address[1]}", embedder
    http.shutdown()
    http.server_close()


class TestServer:
    def test_spans_are_checked(self):
        parse = server_module().parse_spans
        assert parse("0-100,10-20", 100) == [(0, 100), (10, 20)]
        for bad in ("", "x", "0-101", "20-10", "5", "-1-5", ",".join(["0-1"] * 17)):
            assert parse(bad, 100) is None

    def test_embed_answers_one_vector_per_span(self, server):
        url, embedder = server
        response = httpx.post(f"{url}/embed?spans=0-100,0-50", content=b"\x01\x00" * 100)
        data = response.json()
        vectors = np.frombuffer(base64.b64decode(data["embeddings"]), "<f4").reshape(2, 4)
        assert data["model"] == "fake-voice" and np.allclose(vectors[0], A)
        assert embedder.seen == [(200, [(0, 100), (0, 50)])]

    def test_bad_requests(self, server):
        url, _ = server
        assert httpx.post(f"{url}/embed?spans=0-500", content=b"\x01\x00" * 100).status_code == 400
        assert httpx.post(f"{url}/embed?spans=0-1", content=b"\x01").status_code == 400
        assert httpx.post(f"{url}/nothing", content=b"\x01\x00").status_code == 404

    def test_transcribing_still_works_and_health_names_the_model(self, server):
        url, _ = server
        with httpx.Client() as client:  # on one kept connection
            assert client.post(f"{url}/transcribe", content=b"\x01\x00" * 100).json()["text"] == "words"
            assert client.get(f"{url}/health").json()["voice_model"] == "fake-voice"

    def test_without_a_speaker_model_embed_says_so(self):
        module = server_module()
        recognizer = SimpleNamespace(args={"provider": "cpu"})
        http = ThreadingHTTPServer(("127.0.0.1", 0), module.handler(recognizer))
        threading.Thread(target=http.serve_forever, daemon=True).start()
        try:
            url = f"http://127.0.0.1:{http.server_address[1]}"
            assert httpx.post(f"{url}/embed?spans=0-1", content=b"\x01\x00").status_code == 503
            assert httpx.get(f"{url}/health").json()["voice_model"] is None
        finally:
            http.shutdown()
            http.server_close()

    def test_a_missing_model_file_leaves_transcription_alone(self, tmp_path):
        assert server_module().load_embedder(str(tmp_path / "none.onnx"), "cpu", 1) is None


def pcm_seconds(seconds):
    return (np.ones(int(seconds * 16000)) * 1000).astype(np.int16).tobytes()


class LocalModel:
    name = "fake-voice"

    def __init__(self):
        self.calls = 0

    def embed(self, samples):
        self.calls += 1
        return A


def voice_identifier(remote, clock=lambda: 0.0):
    store = MemoryStore(":memory:")
    person = store.add_person("Mira", '"yes" (voice)')
    store.add_identity(person.id, "voice", A.tobytes(), "fake-voice", 1, "she asked for it")
    return VoiceIdentifier(store, model=LocalModel(), remote=remote, clock=clock)


class TestRemoteVoices:
    def test_the_same_spans_either_way(self):
        samples = samples_from(pcm_seconds(3.0))
        enrolled = [Enrolled(1, "Mira", A)]
        asked = []

        def embed_spans(x, spans):
            asked.append(spans)
            return [A for _ in spans]

        one = read_voice(lambda x: A, samples, enrolled)
        two = read_voice(None, samples, enrolled, embed_spans=embed_spans)
        assert one == two and asked == [clip_spans(len(samples))]
        assert asked[0][0] == (0, len(samples)) and all(b - a == 24000 for a, b in asked[0][1:])

    def test_the_client_sends_the_exact_samples_and_checks_the_model(self):
        got = {}

        def answer(request):
            got["pcm"], got["spans"] = request.content, request.url.params["spans"]
            vectors = np.stack([A, B]).astype("<f4").tobytes()
            return httpx.Response(
                200,
                json={"model": "fake-voice", "dim": 4, "embeddings": base64.b64encode(vectors).decode(), "seconds": 0},
            )

        remote = RemoteSpeakerModel("http://gpu/", "fake-voice", transport=httpx.MockTransport(answer))
        pcm = pcm_seconds(0.1)
        vectors = remote.embed_spans(samples_from(pcm), [(0, 1600), (0, 800)])
        assert got["pcm"] == pcm and got["spans"] == "0-1600,0-800"
        assert np.allclose(vectors[1], B)

    def test_vectors_from_another_model_are_refused(self):
        def answer(request):
            return httpx.Response(200, json={"model": "other", "dim": 4, "embeddings": "", "seconds": 0})

        remote = RemoteSpeakerModel("http://gpu", "fake-voice", transport=httpx.MockTransport(answer))
        with pytest.raises(ValueError):
            remote.embed_spans(samples_from(pcm_seconds(0.1)), [(0, 1600)])

    def test_health_must_name_the_model(self):
        def answer(request):
            return httpx.Response(200, json={"voice_model": "other"})

        remote = RemoteSpeakerModel("http://gpu", "fake-voice", transport=httpx.MockTransport(answer))
        with pytest.raises(ValueError):
            remote.check()

    async def test_the_remote_is_used_when_it_answers(self):
        remote = SimpleNamespace(embed_spans=lambda x, spans: [A for _ in spans], close=lambda: None)
        voices = voice_identifier(remote)
        reading = await voices.read(pcm_seconds(3.0))
        assert reading.via == "remote" and reading.name == "Mira" and voices.model.calls == 0
        assert reading.record()["via"] == "remote"
        voices.close()

    async def test_a_failure_reads_on_the_cpu_then_stays_there_for_a_while(self):
        now = [100.0]
        tried = []

        def fail(x, spans):
            tried.append(now[0])
            raise httpx.ConnectError("down")

        voices = voice_identifier(SimpleNamespace(embed_spans=fail, close=lambda: None), clock=lambda: now[0])
        first = await voices.read(pcm_seconds(3.0))
        assert first.via == "local" and first.name == "Mira" and voices.model.calls > 0
        now[0] += 10.0
        second = await voices.read(pcm_seconds(3.0))
        assert second.via == "local" and len(tried) == 1  # no wait for a dead server
        now[0] += 25.0
        await voices.read(pcm_seconds(3.0))
        assert len(tried) == 2  # and it is tried again
        voices.close()

    async def test_a_short_clip_asks_nobody(self):
        tried = []
        voices = voice_identifier(SimpleNamespace(embed_spans=lambda x, s: tried.append(1), close=lambda: None))
        reading = await voices.read(pcm_seconds(1.0))
        assert reading.via is None and not tried
        voices.close()

    async def test_a_server_that_is_down_at_the_start_breaks_nothing(self):
        store = MemoryStore(":memory:")
        voices = VoiceIdentifier(store, model=LocalModel(), url="http://127.0.0.1:9")
        await voices.start()
        assert voices._clock() < voices._remote_off_until
        voices.close()
