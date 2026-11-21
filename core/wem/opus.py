"""Wwise Opus WEM handling (port of WwiseRiffOpus.cs).

Wwise Opus WEMs already embed a complete, standards-compliant Ogg Opus
bitstream in the RIFF 'data' chunk, so conversion is a pure extraction.
"""

from __future__ import annotations

from .bitio import ParseErrorStr


def _read16(data: bytes, offset: int, little_endian: bool) -> int:
    return int.from_bytes(
        data[offset:offset + 2], "little" if little_endian else "big"
    )


def _read32(data: bytes, offset: int, little_endian: bool) -> int:
    return int.from_bytes(
        data[offset:offset + 4], "little" if little_endian else "big"
    )


def _contains_opus_head(data: bytes, offset: int = 0, count: int = -1) -> bool:
    if count < 0:
        count = len(data) - offset
    end = min(len(data), offset + count)
    pos = offset + 27
    while pos + 8 <= end:
        if data[pos:pos + 8] == b"OpusHead":
            return True
        pos += 1
    return False


def is_opus_wem(data: bytes) -> bool:
    """Port of WwiseRiffOpus.IsOpusWem: data chunk carries an Ogg Opus stream."""
    try:
        if data is None or len(data) < 48:
            return False

        if data[0:4] == b"RIFF":
            little_endian = True
        elif data[0:4] == b"RIFX":
            little_endian = False
        else:
            return False

        riff_size = _read32(data, 4, little_endian) + 8
        if riff_size > len(data):
            return False

        chunk_offset = 12
        while chunk_offset + 8 <= riff_size:
            chunk_size = _read32(data, chunk_offset + 4, little_endian)
            fourcc = data[chunk_offset:chunk_offset + 4]

            if fourcc == b"data":
                payload = chunk_offset + 8
                if payload + 8 > len(data):
                    return False
                if data[payload:payload + 4] != b"OggS":
                    return False
                return _contains_opus_head(
                    data, payload, min(4096, riff_size - payload)
                )

            chunk_offset += 8 + chunk_size + (chunk_size & 1)
    except Exception:
        return False

    return False


class WwiseRiffOpus:
    def __init__(self, input_data: bytes, name: str = ""):
        self.name = name

        if not input_data or len(input_data) < 12:
            raise ParseErrorStr("missing RIFF")

        if input_data[0:4] == b"RIFF":
            self._little_endian = True
        elif input_data[0:4] == b"RIFX":
            self._little_endian = False
        else:
            raise ParseErrorStr("missing RIFF")

        riff_size = _read32(input_data, 4, self._little_endian) + 8
        if riff_size > len(input_data):
            raise ParseErrorStr("RIFF truncated")

        fmt_offset = -1
        data_offset = -1
        fmt_size = 0
        data_size = 0

        chunk_offset = 12
        while chunk_offset + 8 <= riff_size:
            fourcc = input_data[chunk_offset:chunk_offset + 4]
            chunk_size = _read32(input_data, chunk_offset + 4, self._little_endian)

            if fourcc == b"fmt ":
                fmt_offset = chunk_offset + 8
                fmt_size = chunk_size
            elif fourcc == b"data":
                data_offset = chunk_offset + 8
                data_size = chunk_size

            chunk_offset += 8 + chunk_size + (chunk_size & 1)

        if fmt_offset < 0 or data_offset < 0 or fmt_size < 16:
            raise ParseErrorStr("expected fmt, data chunks")

        self.channels = _read16(input_data, fmt_offset + 2, self._little_endian)
        self.sample_rate = _read32(input_data, fmt_offset + 4, self._little_endian)

        if data_offset + data_size > len(input_data):
            raise ParseErrorStr("data chunk truncated")
        if data_size < 27 + 8:
            raise ParseErrorStr("data chunk is not an Ogg Opus stream")

        if input_data[data_offset:data_offset + 4] != b"OggS":
            raise ParseErrorStr("data chunk is not an Ogg Opus stream")

        self.ogg_data = bytes(input_data[data_offset:data_offset + data_size])

        if not _contains_opus_head(self.ogg_data):
            raise ParseErrorStr("Ogg stream is missing OpusHead")

    def get_ogg_data(self) -> bytes:
        return self.ogg_data

    def generate_ogg(self) -> bytes:
        return self.ogg_data


def parse_opus_head(ogg_data: bytes) -> tuple[int, int, int]:
    """Return (channels, pre_skip, input_sample_rate) from a BOS page."""
    pos = ogg_data.find(b"OpusHead")
    if pos < 0 or pos + 19 > len(ogg_data):
        return 0, 0, 0
    channels = ogg_data[pos + 9]
    pre_skip = int.from_bytes(ogg_data[pos + 10:pos + 12], "little")
    input_rate = int.from_bytes(ogg_data[pos + 12:pos + 16], "little")
    return channels, pre_skip, input_rate


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
        if page_granule != 0xFFFFFFFFFFFFFFFF:
            granule = page_granule
        pos = page_end
    return granule
