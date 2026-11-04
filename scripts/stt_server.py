#!/usr/bin/env python3
"""
A small speech-to-text server for the second pass (STT_SECOND_PASS=http://host:port), for a GPU machine in the lab.

It runs Cohere Transcribe and answers ``POST /transcribe`` (body: 16 kHz mono 16-bit
PCM; ``?language=en``) with ``{"text": ..., "seconds": ...}``, and ``GET /health``. Audio is never written to disk or
logged; only durations and timings are.

Two kinds of model folder:

- the full model (``CohereLabs/cohere-transcribe-03-2026`` from Hugging Face, gated: accept the terms first) on the
  GPU in bf16 with ``transformers`` >= 5.4 (``pip install "transformers>=5.4" torch librosa soundfile
  sentencepiece protobuf``). On Alien3's RTX 5090: 0.04 s for 3 s of speech, 0.08 s for 6 s, 4 GB of GPU memory.
- the sherpa-onnx int8 package (``pip install "sherpa-onnx==1.13.8+cuda13.cudnn9.onnxruntime1.28.2" numpy -f
  https://k2-fsa.github.io/sherpa/onnx/cuda.html``): 0.29 s for 3 s on the same GPU, as int8 leaves work on the CPU.

    python stt_server.py --model cohere-transcribe-03-2026 --port 11436

On Alien3 it binds 127.0.0.1 (the user-space tailscale forwards tailnet connections to localhost), as the private
Ollama does.
"""

import argparse
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


def handler(recognizer):
    class Handler(BaseHTTPRequestHandler):
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
                self.reply(200, {"ok": True, "model": "cohere-transcribe", "provider": recognizer.args["provider"]})
            else:
                self.reply(404, {"error": "not found"})

        def do_POST(self):
            url = urlparse(self.path)
            if url.path != "/transcribe":
                return self.reply(404, {"error": "not found"})
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_SECONDS * SAMPLE_RATE * 2 or length % 2:
                return self.reply(400, {"error": "body must be 16 kHz mono 16-bit PCM, up to 60 s"})
            pcm = self.rfile.read(length)
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

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", required=True, help="cohere-transcribe-03-2026 or sherpa-onnx-cohere-transcribe-*")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=11436)
    parser.add_argument("--provider", default="cuda", choices=["cuda", "cpu"])
    parser.add_argument("--threads", type=int, default=4)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    full = os.path.exists(os.path.join(os.path.expanduser(args.model), "model.safetensors"))
    recognizer = FullModel(args.model, args.provider) if full else Recognizer(args.model, args.provider, args.threads)
    server = ThreadingHTTPServer((args.host, args.port), handler(recognizer))
    log.info("listening on %s:%d", args.host, args.port)
    server.serve_forever()


if __name__ == "__main__":
    main()
