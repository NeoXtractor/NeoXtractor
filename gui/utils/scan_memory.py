"""Lets the Scene Viewer, Stages tab and exports hold memory without tripping the character-scan limit.

Wrap such work in begin() / end() on the GUI thread; whatever it keeps allocated is added to the limit.
"""

from __future__ import annotations

from PySide6 import QtCore

from core import process_memory

BASE_LIMIT = process_memory.SCAN_MEMORY_LIMIT
SETTLE_MS = 2000          # uploads to the GPU and frees land a little after a job ends

_state = {"allowance": 0, "active": 0, "start": None, "start_allowance": 0, "generation": 0}


def allowance() -> int:
    return _state["allowance"]


def _usage():
    try:
        return process_memory.private_memory_bytes()
    except Exception:
        return None


def _apply(start, start_allowance) -> None:
    now = _usage()
    if now is None or start is None:
        return
    _state["allowance"] = max(0, start_allowance + now - start)
    process_memory.SCAN_MEMORY_LIMIT = BASE_LIMIT + _state["allowance"]


def begin() -> None:
    """A job that may keep memory (a viewer load, a stage list, an export) is starting."""
    if _state["active"] == 0:
        _state["start"] = _usage()
        _state["start_allowance"] = _state["allowance"]
    _state["active"] += 1


def end() -> None:
    """The job finished: count what it left allocated, now and once more after things settle."""
    if _state["active"] == 0:
        return
    _state["active"] -= 1
    if _state["active"]:
        return
    start, start_allowance = _state["start"], _state["start_allowance"]
    _state["generation"] += 1
    generation = _state["generation"]
    _apply(start, start_allowance)

    def settle():
        if _state["active"] == 0 and _state["generation"] == generation:
            _apply(start, start_allowance)

    QtCore.QTimer.singleShot(SETTLE_MS, settle)
