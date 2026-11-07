"""Locate where an IDX entry's payload really starts inside its resource.

NeoX ships (at least) two IDX generations whose trailing ``u16`` per record
means completely different things, and neither the record size nor the GUI
config tells them apart:

* 28-byte records (Onmyoji 2.8.x) -- the payload is stored bare and starts
  exactly at ``file_offset``. The trailing u16 is a per-entry tag (observed
  values 1..211), *not* a header length. Treating it as ``hdr_size`` shifts
  the window into the middle of the encrypted body, cuts the ``PC``/``AC``/
  ``XC`` stage-1 tag, and every entry silently falls through to
  ``get_ext()``'s ``"dat"`` fallback.
* 36-byte records (Identity V) -- the payload sits behind a 48-byte ``1DPW``
  wrapper whose own header repeats ``payload_size`` (u32 @0x20) and
  ``hdr_size`` (u16 @0x24), so the skip distance is self-describing.

The window is therefore resolved from the bytes, never from the record size.
"""

from __future__ import annotations

from typing import Callable, NamedTuple

EMBEDDED_MAGIC = b"1DPW"
STAGE1_TAGS = (b"PC", b"AC", b"XC")
MAX_WRAPPER_HEADER = 0x100
EMBEDDED_SIZE_OFF = 0x20
EMBEDDED_HDR_OFF = 0x24
MIN_EMBEDDED_HEADER_SIZE = 0x30


class PayloadWindow(NamedTuple):
    """The resolved payload plus how it was found."""

    payload: bytes
    hdr_size: int
    source: str  # "embedded" | "bare" | "record_hdr" | "fallback"


def resolve_payload_window(
    read: Callable[[int, int], bytes],
    file_offset: int,
    payload_size: int,
    hdr_size: int,
) -> PayloadWindow:
    """Return the payload window for one entry.

    ``read`` takes absolute offsets and a length, so callers can pass an
    mmap slice, a file-handle read or an in-memory buffer without copying.
    """

    def take(offset: int, length: int) -> bytes:
        if length <= 0:
            return b""
        return read(file_offset + offset, length)

    head = take(0, MIN_EMBEDDED_HEADER_SIZE)
    if head[:4] == EMBEDDED_MAGIC and len(head) >= MIN_EMBEDDED_HEADER_SIZE:
        inner_hdr = int.from_bytes(
            head[EMBEDDED_HDR_OFF : EMBEDDED_HDR_OFF + 2], "little"
        )
        inner_size = int.from_bytes(
            head[EMBEDDED_SIZE_OFF : EMBEDDED_SIZE_OFF + 4], "little"
        )
        if 8 <= inner_hdr <= MAX_WRAPPER_HEADER:
            size = inner_size or payload_size
            body = take(inner_hdr, size)
            if len(body) == size:
                return PayloadWindow(body, inner_hdr, "embedded")

    bare = take(0, payload_size)
    if bare[:2] in STAGE1_TAGS:
        return PayloadWindow(bare, 0, "bare")

    if hdr_size:
        shifted = take(hdr_size, payload_size)
        if shifted[:2] in STAGE1_TAGS:
            return PayloadWindow(shifted, hdr_size, "record_hdr")

    # Unknown layout: keep the raw window so detection/logging can report it
    # instead of silently skipping bytes.
    return PayloadWindow(bare, 0, "fallback")
