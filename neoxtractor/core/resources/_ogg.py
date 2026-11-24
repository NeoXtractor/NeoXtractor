"""Container-level Ogg stream readers, shared by WEM conversion and native Ogg.

Ogg is a framing container: pages carry a segment table and an absolute
granule position whose unit is defined by the codec inside.  This module
only *reads* streams that already exist (identify headers, walk pages);
writing Ogg pages is part of the WEM repacking pipeline and stays private
to ``_wem``.

Granule semantics per codec (RFC 7845 / Vorbis spec):

- Vorbis: samples per channel at 44100 or the stream's own rate -- the
  identification header carries the rate, so granule is the exact duration.
- Opus: 48000 Hz always, minus the pre-skip announced in OpusHead.

The page walk is best-effort by design: it stops at the first malformed
page and returns the last finite granule seen, which is exactly what a
duration guess needs and never raises on truncated payloads.
"""

from __future__ import annotations

# End-of-stream granule marker (all-ones i64) used by old muxers.
_LAST_GRANULE_MASK = 0xFFFFFFFFFFFFFFFF


def last_granule(ogg_data: bytes) -> int:
    """Best-effort final granule position by walking the Ogg pages."""
    pos = 0
    granule = 0
    data_len = len(ogg_data)
    while pos + 27 <= data_len:
        if ogg_data[pos:pos + 4] != b"OggS":
            break
        segments = ogg_data[pos + 26]
        if pos + 27 + segments > data_len:
            break
        payload = sum(ogg_data[pos + 27:pos + 27 + segments])
        page_end = pos + 27 + segments + payload
        if page_end > data_len:
            break
        page_granule = int.from_bytes(ogg_data[pos + 6:pos + 14], "little")
        if page_granule != _LAST_GRANULE_MASK:
            granule = page_granule
        pos = page_end
    return granule


def parse_opus_head(ogg_data: bytes) -> tuple[int, int, int]:
    """Return (channels, pre_skip, input_sample_rate) from a BOS page.

    ``input_sample_rate`` is informational only (Opus decodes at 48 kHz);
    zero channels means the head was not found or is truncated.
    """
    pos = ogg_data.find(b"OpusHead")
    if pos < 0 or pos + 19 > len(ogg_data):
        return 0, 0, 0
    channels = ogg_data[pos + 9]
    pre_skip = int.from_bytes(ogg_data[pos + 10:pos + 12], "little")
    input_rate = int.from_bytes(ogg_data[pos + 12:pos + 16], "little")
    return channels, pre_skip, input_rate


def parse_vorbis_head(ogg_data: bytes) -> tuple[int, int] | None:
    """Return (channels, sample_rate) from a Vorbis identification header.

    The header is packet type 1 (``\\x01`` + ``"vorbis"``) followed by
    version u32, channels u8, sample_rate u32.  ``None`` when no ident
    header is found in the stream's opening pages.
    """
    pos = ogg_data.find(b"\x01vorbis")
    if pos < 0 or pos + 16 > len(ogg_data):
        return None
    version = int.from_bytes(ogg_data[pos + 7:pos + 11], "little")
    if version != 0:
        return None
    channels = ogg_data[pos + 11]
    sample_rate = int.from_bytes(ogg_data[pos + 12:pos + 16], "little")
    if channels == 0 or sample_rate == 0:
        return None
    return channels, sample_rate
