"""
Continuous perception on the host (docs/ARCHITECTURE.md, layer 2): camera judgements from a local vision model.
"""

from .vision import FrameWatcher

__all__ = ["FrameWatcher"]
