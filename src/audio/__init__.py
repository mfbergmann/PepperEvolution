"""
Voice input for PepperEvolution: PCM helpers, utterance detection,
speech-to-text backends and the orchestrator that feeds the AI manager.
"""

from .endpointer import EnergyVAD, Endpointer, Utterance
from .pcm import duration, resample, rms, silence, tone, wav_bytes
from .stt import (
    BACKENDS,
    FakeTranscriber,
    SherpaTranscriber,
    Transcriber,
    TranscriberUnavailable,
    Transcript,
    WhisperTranscriber,
    make_transcriber,
)
from .voice import VoiceInput

__all__ = [
    "BACKENDS",
    "EnergyVAD",
    "Endpointer",
    "FakeTranscriber",
    "SherpaTranscriber",
    "Transcriber",
    "TranscriberUnavailable",
    "Transcript",
    "Utterance",
    "VoiceInput",
    "WhisperTranscriber",
    "duration",
    "make_transcriber",
    "resample",
    "rms",
    "silence",
    "tone",
    "wav_bytes",
]
