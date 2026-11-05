#!/usr/bin/env python3
"""
A small speech server for a GPU machine in the lab: the second pass (STT_SECOND_PASS=http://host:port) and speaker
embeddings for voice identification (VOICE_ID_URL=http://host:port).

It runs Cohere Transcribe and answers ``POST /transcribe`` (body: 16 kHz mono 16-bit
PCM; ``?language=en``) with ``{"text": ..., "seconds": ...}``, and ``GET /health``. With the speaker model file beside
this script (``--voice-model``), it also answers ``POST /embed`` (the same body; ``?spans=0-75200,0-24000,...``, sample
ranges of the clip) with ``{"model": ..., "dim": ..., "embeddings": base64 float32, one per span, "seconds": ...}``.

Nothing here keeps anything: audio and embeddings live for the length of a request, are never written to disk and are
never logged; only durations and timings are. It knows nobody: who is enrolled stays on the host, which does the
comparing.

Two kinds of model folder:

- the full model (``CohereLabs/cohere-transcribe-03-2026`` from Hugging Face, gated: accept the terms first) on the
  GPU in bf16 with ``transformers`` >= 5.4 (``pip install "transformers>=5.4" torch librosa soundfile
  sentencepiece protobuf``). On Alien3's RTX 5090: 0.04 s for 3 s of speech, 0.08 s for 6 s, 4 GB of GPU memory.
- the sherpa-onnx int8 package (``pip install "sherpa-onnx==1.13.8+cuda13.cudnn9.onnxruntime1.28.2" numpy -f
  https://k2-fsa.github.io/sherpa/onnx/cuda.html``): 0.29 s for 3 s on the same GPU, as int8 leaves work on the CPU.

    python stt_server.py --model cohere-transcribe-03-2026 --port 11436

The speaker model needs sherpa-onnx with CUDA next to the full model (the wheel above): it has no batch call, so spans
run one after another, about 10 ms each on the RTX 5090. Without the file or the package the
server still transcribes, and the host reads voices on its own CPU.

On Alien3 it binds 127.0.0.1 (the user-space tailscale forwards tailnet connections to localhost), as the private
Ollama does.
"""

import argparse
import base64
import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import numpy as np

SAMPLE_RATE = 16000
MAX_SECONDS = 60  # longer bodies are refused: an utterance is seconds, not minutes
MAX_SPANS = 16  # one clip and its windows
VOICE_MODEL = "3dspeaker_speech_eres2net_large_sv_zh-cn_3dspeaker_16k.onnx"  # the one src/identity/voice.py loads

log = logging.getLogger("stt_server")


class FullModel:
    """The Hugging Face model in bf16 on the GPU (transformers' native Cohere ASR support, no remote code)."""

    def __init__(self, model_dir: str, provider: str):
        import torch
        from transformers import AutoProcessor, CohereAsrForConditionalGeneration

        self.torch = torch
        self.device = "cuda" if provider == "cuda" else "cpu"
        self.dtype = torch.bfloat16 if self.device == "cuda" else torch.float32
        self.lock = threading.Lock()
        self.args = {"provider": f"{self.device} {str(self.dtype).replace('torch.', '')}"}
        started = time.monotonic()
        d = os.path.expanduser(model_dir)
        self.processor = AutoProcessor.from_pretrained(d)
        self.model = CohereAsrForConditionalGeneration.from_pretrained(d, dtype=self.dtype).to(self.device).eval()
        self.transcribe(b"\x00\x00" * SAMPLE_RATE, "en")  # the first call compiles kernels: not on a real utterance
        log.info("full model ready in %.1f s (%s)", time.monotonic() - started, self.args["provider"])

    def transcribe(self, pcm: bytes, language: str) -> str:
        x = np.frombuffer(pcm, np.int16).astype(np.float32) / 32768
        with self.lock, self.torch.inference_mode():
            inputs = self.processor(x, sampling_rate=SAMPLE_RATE, return_tensors="pt", language=language)
            inputs = inputs.to(self.device, dtype=self.dtype)
            out = self.model.generate(**inputs, max_new_tokens=256)
            text = self.processor.decode(out, skip_special_tokens=True)
        return " ".join((text[0] if isinstance(text, list) else text).split())


class Recognizer:
    """The sherpa-onnx int8 package."""

    def __init__(self, model_dir: str, provider: str, threads: int):
        d = os.path.expanduser(model_dir)
        self.lock = threading.Lock()  # one decode at a time on the GPU
        self.recognizers = {}
        self.args = dict(
            encoder=os.path.join(d, "encoder.int8.onnx"),
            decoder=os.path.join(d, "decoder.int8.onnx"),
            tokens=os.path.join(d, "tokens.txt"),
            num_threads=threads,
            provider=provider,
        )
        self.get("en")

    def get(self, language: str):
        import sherpa_onnx

        if language not in self.recognizers:
            started = time.monotonic()
            self.recognizers[language] = sherpa_onnx.OfflineRecognizer.from_cohere_transcribe(
                language=language, **self.args
            )
            log.info("model ready for %s in %.1f s (%s)", language, time.monotonic() - started, self.args["provider"])
        return self.recognizers[language]

    def transcribe(self, pcm: bytes, language: str) -> str:
        x = np.frombuffer(pcm, np.int16).astype(np.float32) / 32768
        with self.lock:
            r = self.get(language)
            s = r.create_stream()
            s.accept_waveform(SAMPLE_RATE, x)
            r.decode_stream(s)
            return " ".join(s.result.text.split())


class Embedder:
    """The speaker embedding model on the GPU. The same file as the host's CPU copy, so the vectors are comparable with
    what is enrolled there (checked in docs/MEMORY.md, "Identity"); the host refuses any other model by its name."""

    def __init__(self, path: str, provider: str, threads: int):
        import sherpa_onnx

        self.lock = threading.Lock()  # one extractor, one compute at a time
        self.name = os.path.splitext(os.path.basename(path))[0]
        config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=path, num_threads=threads, provider=provider)
        self.extractor = sherpa_onnx.SpeakerEmbeddingExtractor(config)
        self.dim = self.extractor.dim
        started = time.monotonic()
        for seconds in (1.5, 5, 1.5, 12):  # the first calls at each length set up kernels: not on a real utterance
            self.embed(b"\x10\x00\xf0\xff" * int(seconds * SAMPLE_RATE / 2), [(0, int(seconds * SAMPLE_RATE))])
        log.info("speaker model ready in %.1f s (%s)", time.monotonic() - started, provider)

    def embed(self, pcm: bytes, spans) -> np.ndarray:
        x = np.frombuffer(pcm, np.int16).astype(np.float32) / 32768
        out = np.empty((len(spans), self.dim), dtype=np.float32)
        with self.lock:
            for i, (start, stop) in enumerate(spans):
                stream = self.extractor.create_stream()
                stream.accept_waveform(SAMPLE_RATE, x[start:stop])
                stream.input_finished()
                out[i] = self.extractor.compute(stream)
        return out


def parse_spans(text: str, samples: int):
    """``0-75200,0-24000`` into [(0, 75200), (0, 24000)], or None if it is not ranges inside the clip."""
    try:
        spans = [tuple(int(n) for n in part.split("-")) for part in text.split(",")]
    except ValueError:
        return None
    if not 0 < len(spans) <= MAX_SPANS or any(len(s) != 2 or not 0 <= s[0] < s[1] <= samples for s in spans):
        return None
    return spans


def handler(recognizer, embedder=None):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"  # keep the connection: a new one over the tailnet costs more than the model
        disable_nagle_algorithm = True  # the reply's headers and body go in two writes

        def log_message(self, *args):
            pass

        def reply(self, code: int, payload: dict):
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if urlparse(self.path).path == "/health":
                self.reply(
                    200,
                    {
                        "ok": True,
                        "model": "cohere-transcribe",
                        "provider": recognizer.args["provider"],
                        "voice_model": embedder.name if embedder is not None else None,
                    },
                )
            else:
                self.reply(404, {"error": "not found"})

        def do_POST(self):
            url = urlparse(self.path)
            if url.path not in ("/transcribe", "/embed"):
                self.close_connection = True  # the body was not read
                return self.reply(404, {"error": "not found"})
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_SECONDS * SAMPLE_RATE * 2 or length % 2:
                self.close_connection = True
                return self.reply(400, {"error": "body must be 16 kHz mono 16-bit PCM, up to 60 s"})
            pcm = self.rfile.read(length)
            if url.path == "/embed":
                return self.embed(url, pcm)
            language = (parse_qs(url.query).get("language") or ["en"])[0]
            started = time.monotonic()
            try:
                text = recognizer.transcribe(pcm, language)
            except Exception as exc:  # noqa: BLE001 - answer, never die
                log.warning("transcription failed: %s", exc)
                return self.reply(500, {"error": str(exc)})
            seconds = time.monotonic() - started
            log.info("%.1f s of audio in %.3f s", length / 2 / SAMPLE_RATE, seconds)
            self.reply(200, {"text": text, "seconds": round(seconds, 3)})

        def embed(self, url, pcm: bytes):
            if embedder is None:
                return self.reply(503, {"error": "no speaker model on this server"})
            spans = parse_spans((parse_qs(url.query).get("spans") or [""])[0], len(pcm) // 2)
            if spans is None:
                return self.reply(400, {"error": "spans must be sample ranges inside the clip: 0-24000,12000-36000"})
            started = time.monotonic()
            try:
                vectors = embedder.embed(pcm, spans)
            except Exception as exc:  # noqa: BLE001 - answer, never die
                log.warning("embedding failed: %s", type(exc).__name__)
                return self.reply(500, {"error": type(exc).__name__})
            seconds = time.monotonic() - started
            log.info("%.1f s of audio, %d embeddings in %.3f s", len(pcm) / 2 / SAMPLE_RATE, len(spans), seconds)
            self.reply(
                200,
                {
                    "model": embedder.name,
                    "dim": embedder.dim,
                    "embeddings": base64.b64encode(vectors.astype("<f4").tobytes()).decode("ascii"),
                    "seconds": round(seconds, 3),
                },
            )

    return Handler


def load_embedder(path: str, provider: str, threads: int):
    """The speaker model if it is here and loads. Transcription does not depend on it."""
    path = os.path.expanduser(path)
    if not os.path.exists(path):
        log.info("no speaker model at %s: /embed is off", path)
        return None
    try:
        return Embedder(path, provider, threads)
    except Exception as exc:  # noqa: BLE001
        log.warning("speaker model did not load (%s): /embed is off", exc)
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", required=True, help="cohere-transcribe-03-2026 or sherpa-onnx-cohere-transcribe-*")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=11436)
    parser.add_argument("--provider", default="cuda", choices=["cuda", "cpu"])
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument(
        "--voice-model",
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), VOICE_MODEL),
        help="speaker model file for /embed (default: beside this script); off if it is not there",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    full = os.path.exists(os.path.join(os.path.expanduser(args.model), "model.safetensors"))
    recognizer = FullModel(args.model, args.provider) if full else Recognizer(args.model, args.provider, args.threads)
    embedder = load_embedder(args.voice_model, args.provider, args.threads)
    server = ThreadingHTTPServer((args.host, args.port), handler(recognizer, embedder))
    log.info("listening on %s:%d", args.host, args.port)
    server.serve_forever()


if __name__ == "__main__":
    main()
