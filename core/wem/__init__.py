"""Wwise WEM audio conversion (pure stdlib port of ww2ogg-cs).

Public API:
    probe(data) -> WemInfo
    to_ogg(data, inline_codebooks=False, codebook_file=None) -> bytes
    to_opus(data) -> bytes
    to_wav(data) -> bytes
    convert(data, target) -> bytes
"""

from __future__ import annotations

from dataclasses import dataclass

from .bitio import ParseErrorStr
from .codebooks import DEFAULT_CODEBOOK_FILE
from .opus import WwiseRiffOpus, is_opus_wem, last_granule, parse_opus_head
from .pcm import parse_pcm_wem
from .pcm import to_wav as _pcm_to_wav
from .vorbis import WwiseRiffVorbis

__all__ = [
    "WemInfo",
    "probe",
    "to_ogg",
    "to_opus",
    "to_wav",
    "convert",
]


@dataclass
class WemInfo:
    codec: str
    channels: int
    sample_rate: int
    duration_samples: int
    is_bank: bool


def _riff_fmt(data: bytes) -> dict:
    """Read the fmt chunk fields shared by all WEM codecs."""
    if not data or len(data) < 12:
        raise ParseErrorStr("missing RIFF")
    if data[0:4] == b"RIFF":
        little_endian = True
    elif data[0:4] == b"RIFX":
        little_endian = False
    else:
        raise ParseErrorStr("missing RIFF")

    riff_size = int.from_bytes(
        data[4:8], "little" if little_endian else "big"
    ) + 8
    if riff_size > len(data):
        raise ParseErrorStr("RIFF truncated")

    fmt_offset = -1
    fmt_size = 0
    data_size = 0

    chunk_offset = 12
    while chunk_offset + 8 <= riff_size:
        chunk_size = int.from_bytes(
            data[chunk_offset + 4:chunk_offset + 8],
            "little" if little_endian else "big",
        )
        fourcc = data[chunk_offset:chunk_offset + 4]
        if fourcc == b"fmt ":
            fmt_offset = chunk_offset + 8
            fmt_size = chunk_size
        elif fourcc == b"data":
            data_size = chunk_size
        chunk_offset += 8 + chunk_size + (chunk_size & 1)

    if fmt_offset < 0 or fmt_size < 16:
        raise ParseErrorStr("expected fmt chunk")

    tag = int.from_bytes(
        data[fmt_offset:fmt_offset + 2], "little" if little_endian else "big"
    )
    channels = int.from_bytes(
        data[fmt_offset + 2:fmt_offset + 4], "little" if little_endian else "big"
    )
    sample_rate = int.from_bytes(
        data[fmt_offset + 4:fmt_offset + 8], "little" if little_endian else "big"
    )
    return {
        "format_tag": tag,
        "channels": channels,
        "sample_rate": sample_rate,
        "fmt_offset": fmt_offset,
        "data_size": data_size,
        "little_endian": little_endian,
    }


def probe(data: bytes) -> WemInfo:
    """Identify a WEM payload without converting it."""
    if data[0:4] == b"BKHD":
        return WemInfo("bank", 0, 0, 0, True)
    if data[0:4] == b"AKPK":
        return WemInfo("pck", 0, 0, 0, False)

    try:
        fmt = _riff_fmt(data)
    except Exception:
        return WemInfo("unknown", 0, 0, 0, False)

    tag = fmt["format_tag"]
    channels = fmt["channels"]
    sample_rate = fmt["sample_rate"]

    # Opus payloads are standard Ogg Opus streams inside a fake fmt chunk.
    if is_opus_wem(data):
        try:
            opus = WwiseRiffOpus(data)
            head_channels, pre_skip, _input_rate = parse_opus_head(opus.ogg_data)
            if head_channels:
                channels = head_channels
            if not sample_rate:
                sample_rate = 48000
            granule = last_granule(opus.ogg_data)
            duration = granule - pre_skip if granule > pre_skip else 0
            return WemInfo("opus", channels, sample_rate, duration, False)
        except Exception:
            return WemInfo("opus", channels, sample_rate, 0, False)

    if tag == 0xFFFF:
        # Vorbis WEM: the RIFF header carries the exact per-channel sample count.
        try:
            vorbis = WwiseRiffVorbis(data)
            return WemInfo(
                "vorbis",
                vorbis.channels,
                vorbis.sample_rate,
                vorbis.sample_count,
                False,
            )
        except Exception:
            return WemInfo("vorbis", channels, sample_rate, 0, False)

    try:
        info = parse_pcm_wem(data)
    except Exception:
        return WemInfo("unknown", channels, sample_rate, 0, False)

    return WemInfo(
        info["codec"],
        info["channels"],
        info["sample_rate"],
        info["frames"],
        False,
    )


def to_ogg(
    data: bytes,
    inline_codebooks: bool = False,
    codebook_file: str | None = None,
) -> bytes:
    """Vorbis WEM -> standard Ogg Vorbis bytes."""
    if data[0:4] == b"BKHD":
        raise ValueError("input is a BKHD bank container, not a WEM")
    if is_opus_wem(data):
        raise ValueError("input is an Opus WEM, use to_opus()")

    try:
        fmt = _riff_fmt(data)
    except Exception as exc:
        raise ValueError(str(exc)) from exc

    if fmt["format_tag"] != 0xFFFF:
        raise ValueError(
            "not a Vorbis WEM (format tag 0x%04x)" % fmt["format_tag"]
        )

    try:
        converter = WwiseRiffVorbis(
            data,
            codebook_file=codebook_file or DEFAULT_CODEBOOK_FILE,
            inline_codebooks=inline_codebooks,
        )
        return converter.generate_ogg()
    except Exception as exc:
        raise ValueError(str(exc)) from exc


def to_opus(data: bytes) -> bytes:
    """Opus WEM -> .opus (embedded Ogg Opus stream extracted unchanged)."""
    if not is_opus_wem(data):
        raise ValueError("input is not an Opus WEM")
    try:
        return WwiseRiffOpus(data).get_ogg_data()
    except Exception as exc:
        raise ValueError(str(exc)) from exc


def to_wav(data: bytes) -> bytes:
    """PCM WEM -> 16-bit PCM WAV bytes."""
    if data[0:4] == b"BKHD":
        raise ValueError("input is a BKHD bank container, not a WEM")

    try:
        fmt = _riff_fmt(data)
    except Exception as exc:
        raise ValueError(str(exc)) from exc

    if fmt["format_tag"] == 0xFFFF:
        raise ValueError("Vorbis WEM decoding is not supported")
    if is_opus_wem(data):
        raise ValueError("Opus WEM decoding is not supported")

    try:
        return _pcm_to_wav(data)
    except Exception as exc:
        raise ValueError(str(exc)) from exc


def convert(data: bytes, target: str) -> bytes:
    """Convert a WEM payload to the requested container."""
    target = target.lower().lstrip(".")
    if target == "ogg":
        return to_ogg(data)
    if target == "opus":
        return to_opus(data)
    if target == "wav":
        return to_wav(data)
    raise ValueError("unknown target %r (expected 'ogg', 'opus' or 'wav')" % target)
