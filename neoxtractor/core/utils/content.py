"""Content-level helpers shared across layers (Qt-free).

:func:`is_binary` is a *content classification*, not a format: it answers
"does this payload look like text?", shared by the container layer (npk/wpk
entry flags) and the resource layer (unsupported sniffing).
"""

from __future__ import annotations

from neoxtractor.core.utils.filelike import FileLike, read_head

__all__ = ["is_binary", "head_is_binary"]

# Bytes inspected before the UTF-8 attempt; also the null scan width.
DEFAULT_SAMPLE = 2048

# A legal UTF-8 sequence is at most 4 bytes, so a mid-sequence cut leaves at
# most 3 stray trailing bytes: exact theoretical bound for the retry loop.
_MAX_CUT_RETRY = 3


def is_binary(
    data: bytes, *, truncated: bool = False, sample: int | None = None
) -> bool:
    """Classify payload bytes as binary (True) or text (False).

    ``sample`` bounds both the null scan and the UTF-8 attempt (default
    :data:`DEFAULT_SAMPLE`).  ``truncated`` says the payload may be only a
    *window* of a larger one: on such a window a failed strict decode trims
    the tail one byte at a time (up to :data:`_MAX_CUT_RETRY` times) before
    declaring binary, so a cut multi-byte character cannot misclassify real
    text.  Full payloads keep the strict answer.
    """
    n = DEFAULT_SAMPLE if sample is None else sample
    window = data[:n]
    if b"\x00" in window:
        return True
    # A sample window over longer data cuts sequences just like a truncated
    # payload does, so the rescue applies to either case.
    rescuable = truncated or len(data) > n
    try:
        window.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        if not rescuable:
            return True
        for _ in range(_MAX_CUT_RETRY):
            window = window[:-1]
            try:
                window.decode("utf-8", errors="strict")
                break
            except UnicodeDecodeError:
                continue
        else:
            return True
    return False


def head_is_binary(f: FileLike, window: int = 4096) -> bool:
    """Binary check over a bounded header window (probe-cheap variant).

    The caller's ``window`` governs the whole check: sample width and the
    cut-rescue decision both derive from it.
    """
    head = read_head(f, window)
    return is_binary(head, truncated=len(head) >= window, sample=window)
