#!/usr/bin/env python3
"""
A small speech-to-text server for the second pass (STT_SECOND_PASS=http://host:port), for a GPU machine in the lab.

It runs Cohere Transcribe (the sherpa-onnx int8 package) and answers ``POST /transcribe`` (body: 16 kHz mono 16-bit
PCM; ``?language=en``) with ``{"text": ..., "seconds": ...}``, and ``GET /health``. Audio is never written to disk or
logged; only durations and timings are.

    pip install "sherpa-onnx==1.13.8+cuda13.cudnn9.onnxruntime1.28.2" numpy \\
        -f https://k2-fsa.github.io/sherpa/onnx/cuda.html
    python stt_server.py --model sherpa-onnx-cohere-transcribe-14-lang-int8-2026-04-01 --port 11436

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
import sherpa_onnx

SAMPLE_RATE = 16000
MAX_SECONDS = 60  # longer bodies are refused: an utterance is seconds, not minutes

log = logging.getLogger("stt_server")


class Recognizer:
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


def handler(recognizer: Recognizer):
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
    parser.add_argument("--model", required=True, help="the sherpa-onnx-cohere-transcribe-14-lang-int8-* folder")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=11436)
    parser.add_argument("--provider", default="cuda", choices=["cuda", "cpu"])
    parser.add_argument("--threads", type=int, default=4)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    recognizer = Recognizer(args.model, args.provider, args.threads)
    server = ThreadingHTTPServer((args.host, args.port), handler(recognizer))
    log.info("listening on %s:%d", args.host, args.port)
    server.serve_forever()


if __name__ == "__main__":
    main()
