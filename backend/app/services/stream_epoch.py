"""Monotonic counter bumped when the broadcast pipeline resets (e.g. Icecast restart)."""

from __future__ import annotations

import threading

_lock = threading.Lock()
_epoch = 0


def get_stream_epoch() -> int:
    with _lock:
        return _epoch


def bump_stream_epoch() -> int:
    global _epoch
    with _lock:
        _epoch += 1
        return _epoch
