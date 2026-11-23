"""Plain PCM WEM -> 16-bit PCM WAV.

WavConverter.cs only contains the float->PCM16 / RIFF writer used after
decoding an Ogg stream; Wwise PCM/ADPCM WEMs are not decoded by the C#
implementation. This module therefore handles the codecs that can be converted
losslessly by reinterpretation (PCM 8/16/24/32-bit and IEEE float) and raises
ValueError for ADPCM, which would need a real decoder.
"""

from __future__ import annotations

import struct

from .bitio import ParseErrorStr

PCM_TAGS = {
    0x0001: "pcm",    # PCM integer
    0x0003: "pcm",    # IEEE float
}
ADPCM_TAGS = {
    0x0002: "adpcm",  # Microsoft ADPCM
    0x0069: "adpcm",  # IMA ADPCM (Wwise)
}


def _read16(data: bytes, offset: int, little_endian: bool) -> int:
    return int.from_bytes(
        data[offset:offset + 2], "little" if little_endian else "big"
    )


def _read32(data: bytes, offset: int, little_endian: bool) -> int:
    return int.from_bytes(
        data[offset:offset + 4], "little" if little_endian else "big"
    )


def parse_pcm_wem(data: bytes) -> dict:
    """Parse a RIFF/RIFX PCM WEM header; returns a dict of format fields."""
    if not data or len(data) < 12:
        raise ParseErrorStr("missing RIFF")

    if data[0:4] == b"RIFF":
        little_endian = True
    elif data[0:4] == b"RIFX":
        little_endian = False
    else:
        raise ParseErrorStr("missing RIFF")

    riff_size = _read32(data, 4, little_endian) + 8
    if riff_size > len(data):
        raise ParseErrorStr("RIFF truncated")

    fmt_offset = -1
    data_offset = -1
    fmt_size = 0
    data_size = 0

    chunk_offset = 12
    while chunk_offset + 8 <= riff_size:
        fourcc = data[chunk_offset:chunk_offset + 4]
        chunk_size = _read32(data, chunk_offset + 4, little_endian)

        if fourcc == b"fmt ":
            fmt_offset = chunk_offset + 8
            fmt_size = chunk_size
        elif fourcc == b"data":
            data_offset = chunk_offset + 8
            data_size = chunk_size

        chunk_offset += 8 + chunk_size + (chunk_size & 1)

    if fmt_offset < 0 or data_offset < 0 or fmt_size < 16:
        raise ParseErrorStr("expected fmt, data chunks")

    format_tag = _read16(data, fmt_offset, little_endian)
    channels = _read16(data, fmt_offset + 2, little_endian)
    sample_rate = _read32(data, fmt_offset + 4, little_endian)
    block_align = _read16(data, fmt_offset + 12, little_endian)
    bits_per_sample = _read16(data, fmt_offset + 14, little_endian)

    if data_offset + data_size > len(data):
        raise ParseErrorStr("data chunk truncated")

    if format_tag in PCM_TAGS:
        codec = "pcm"
    elif format_tag in ADPCM_TAGS:
        codec = "adpcm"
    else:
        codec = "unknown"

    if codec == "pcm":
        bytes_per_sample = bits_per_sample // 8
        if bytes_per_sample <= 0:
            raise ParseErrorStr("invalid bits per sample")
        if block_align <= 0:
            block_align = channels * bytes_per_sample
        frames = data_size // (channels * bytes_per_sample)
    elif codec == "adpcm":
        bytes_per_sample = 0
        frames = _adpcm_frames(format_tag, channels, block_align, data_size)
    else:
        bytes_per_sample = 0
        frames = 0

    return {
        "format_tag": format_tag,
        "codec": codec,
        "channels": channels,
        "sample_rate": sample_rate,
        "block_align": block_align,
        "bits_per_sample": bits_per_sample,
        "bytes_per_sample": bytes_per_sample,
        "data_offset": data_offset,
        "data_size": data_size,
        "frames": frames,
        "little_endian": little_endian,
    }


def _adpcm_frames(format_tag: int, channels: int, block_align: int, data_size: int) -> int:
    if channels <= 0 or block_align <= 0:
        return 0
    if format_tag == 0x0069:  # IMA ADPCM
        samples_per_block = (block_align - 4 * channels) * 2 // channels + 1
    elif format_tag == 0x0002:  # MS ADPCM
        samples_per_block = 2 + (block_align - 7 * channels) * 2 // channels
    else:
        return 0
    if samples_per_block <= 0:
        return 0
    return (data_size // block_align) * samples_per_block


def _to_s16(data: bytes, bits: int, little_endian: bool) -> bytes:
    count = len(data) // (bits // 8)
    if count == 0:
        return b""

    if bits == 16:
        if little_endian:
            return data[:count * 2]
        values = struct.unpack(">%dh" % count, data[:count * 2])
        return struct.pack("<%dh" % count, *values)

    if bits == 8:
        # 8-bit WAV samples are unsigned
        pcm = [(value - 128) * 256 for value in data[:count]]
        return struct.pack("<%dh" % count, *pcm)

    if bits == 24:
        out = bytearray(count * 2)
        size = len(data)
        step = 3
        index = 0
        for i in range(count):
            offset = i * step
            if offset + step > size:
                break
            value = int.from_bytes(
                data[offset:offset + step],
                "little" if little_endian else "big",
                signed=True,
            )
            sample = value >> 8
            out[i * 2:i * 2 + 2] = struct.pack("<h", sample)
        return bytes(out)

    if bits == 32:
        values = struct.unpack(
            ("<%di" if little_endian else ">%di") % count, data[:count * 4]
        )
        pcm = [value >> 16 for value in values]
        return struct.pack("<%dh" % count, *pcm)

    raise ParseErrorStr("unsupported bits per sample")


def _to_s16_float(data: bytes, little_endian: bool) -> bytes:
    count = len(data) // 4
    if count == 0:
        return b""
    values = struct.unpack(
        ("<%df" if little_endian else ">%df") % count, data[:count * 4]
    )
    pcm = []
    for value in values:
        sample = int(value * 32767.0)
        if sample > 32767:
            sample = 32767
        elif sample < -32768:
            sample = -32768
        pcm.append(sample)
    return struct.pack("<%dh" % count, *pcm)


def write_pcm_wav(pcm16: bytes, channels: int, sample_rate: int) -> bytes:
    """Canonical 44-byte-header 16-bit PCM WAV (port of WavConverter.WritePcmWav)."""
    bits_per_sample = 16
    block_align = channels * (bits_per_sample // 8)
    byte_rate = sample_rate * block_align

    header = bytearray(44)
    header[0:4] = b"RIFF"
    header[4:8] = (36 + len(pcm16)).to_bytes(4, "little")
    header[8:12] = b"WAVE"
    header[12:16] = b"fmt "
    header[16:20] = (16).to_bytes(4, "little")  # fmt chunk size (PCM)
    header[20:22] = (1).to_bytes(2, "little")   # audio format: PCM
    header[22:24] = channels.to_bytes(2, "little")
    header[24:28] = sample_rate.to_bytes(4, "little")
    header[28:32] = byte_rate.to_bytes(4, "little")
    header[32:34] = block_align.to_bytes(2, "little")
    header[34:36] = bits_per_sample.to_bytes(2, "little")
    header[36:40] = b"data"
    header[40:44] = len(pcm16).to_bytes(4, "little")

    return bytes(header) + pcm16


def to_wav(data: bytes) -> bytes:
    """Convert a PCM WEM to 16-bit PCM WAV bytes; ValueError for other codecs."""
    info = parse_pcm_wem(data)

    if info["codec"] == "adpcm":
        raise ValueError(
            "ADPCM WEM decoding is not supported (format tag 0x%04x)"
            % info["format_tag"]
        )
    if info["codec"] != "pcm":
        raise ValueError(
            "unsupported WEM codec (format tag 0x%04x)" % info["format_tag"]
        )

    start = info["data_offset"]
    end = start + info["data_size"]
    raw = data[start:end]

    if info["format_tag"] == 0x0003:
        if info["bits_per_sample"] != 32:
            raise ValueError("unsupported float WEM sample size")
        pcm16 = _to_s16_float(raw, info["little_endian"])
    else:
        pcm16 = _to_s16(raw, info["bits_per_sample"], info["little_endian"])

    return write_pcm_wav(pcm16, info["channels"], info["sample_rate"])
