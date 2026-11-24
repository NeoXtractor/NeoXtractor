"""Resource protocol entry for Wwise WEM audio files.

FORMAT  -> "wem"
probe   -> header-only check for a decodable WEM (RIFF/WAVE carrying Wwise
           Vorbis, embedded Ogg Opus, or PCM); returns an AVPROBE-style score
read    -> ``(ext, data, metadata)``: the entry repacked into a directly
           playable container (Vorbis WEM -> Ogg Vorbis, Opus WEM -> Ogg
           Opus, PCM WEM -> WAV) plus a :class:`WemAudioInfo` describing the
           stream.  No ``convert``/``TARGETS``: the repacked bytes are the
           final product, so viewers save ``data`` as-is.

Vorbis WEM payloads store stripped setup headers and rewritten packet
boundaries, so repacking needs the packed aoTuV codebook database shipped
in the resource root's ``data/codebooks`` -- raw streams are not decodable
by generic players.

File-like input contract (shared by probe/read): seekable binary stream or
plain bytes; ``probe`` reads only a header.  All codec work lives in the
private ``_wem`` package (a port of ww2ogg-cs).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Union

from neoxtractor.core.resources._wem import api as _api
from neoxtractor.core.utils.filelike import read_all, read_head
from neoxtractor.core.utils.result import Category, ProbeResult

FORMAT = "wem"
CANONICAL_TYPE = "tuple[str, bytes, WemAudioInfo]"


@dataclass
class WemAudioInfo:
    """Descriptive metadata for one decoded WEM, carried beside its bytes.

    ``duration_samples`` is the codec's own sample count (Vorbis: the exact
    per-channel count from the Wwise header; Opus: the last Ogg granule minus
    pre-skip; PCM: decoded frames), at ``sample_rate`` Hz.
    """

    codec: str            # "vorbis" | "opus" | "pcm"
    channels: int
    sample_rate: int
    duration_samples: int

    @property
    def duration_seconds(self) -> float:
        if self.sample_rate <= 0:
            return 0.0
        return self.duration_samples / self.sample_rate


def _head_codec(head: bytes) -> Optional[str]:
    """Classify a WEM from a header window only (no full-length check).

    Walks the RIFF chunk list as far as the window reaches and returns the
    codec whose payload is repackable: "vorbis" (format tag 0xFFFF), "opus"
    (standard Ogg stream in the data chunk), "pcm" (tag 0x0001), else None.
    Unlike the engine's own parsers, this never compares the declared RIFF
    size against the window length, so it works on a partial read.
    """
    if head[:4] not in (b"RIFF", b"RIFX") or head[8:12] != b"WAVE":
        return None
    le = head[:4] == b"RIFF"
    p = 12
    tag: Optional[int] = None
    while p + 8 <= len(head):
        cid = head[p:p + 4]
        csz = int.from_bytes(head[p + 4:p + 8], "little" if le else "big")
        body = p + 8
        if cid == b"fmt " and body + 2 <= len(head):
            tag = int.from_bytes(head[body:body + 2], "little" if le else "big")
        elif cid == b"data":
            if head[body:body + 4] == b"OggS":
                return "opus"
            break
        p = body + csz + (csz & 1)
    if tag == 0xFFFF:
        return "vorbis"
    if tag == 0x0001:
        return "pcm"
    return None


def _is_wave(head: bytes) -> bool:
    return head[:4] in (b"RIFF", b"RIFX") and head[8:12] == b"WAVE"


def probe(f) -> ProbeResult:
    """Probe *f* as a WEM (header window only).

    A RIFF/WAVE carrying a repackable codec (Vorbis / Opus / PCM) scores 100.
    A RIFF/WAVE whose codec this build cannot repack (XMA, ADPCM, an unknown
    tag) still claims the WEM identity at fallback strength (30) so the entry
    keeps its ``.wem`` name, but :func:`read` declines it and the scan groups
    it as unreadable.  Non-WAVE payloads score 0.
    """
    head = read_head(f, 4096)
    if not _is_wave(head):
        return ProbeResult(0)
    if _head_codec(head) is None:
        return ProbeResult(30, "wem", Category.AUDIO)
    return ProbeResult(100, "wem", Category.AUDIO)


def read(f) -> Union[None, "tuple[str, bytes, WemAudioInfo]"]:
    """Repack a WEM to playable bytes, or None when it is not ours.

    Returns ``(ext, data, WemAudioInfo)``: ext is "ogg" for Vorbis and Opus
    WEMs and "wav" for PCM; ``data`` is the complete repacked stream.
    """
    data = read_all(f)
    codec = _head_codec(data)
    if codec is None:
        return None
    info = _api.probe(data)
    if codec == "vorbis":
        return ("ogg", _api.to_ogg(data), _info(info, "vorbis"))
    if codec == "opus":
        return ("ogg", _api.to_opus(data), _info(info, "opus"))
    return ("wav", _api.to_wav(data), _info(info, "pcm"))


def _info(info: "_api.WemInfo", codec: str) -> WemAudioInfo:
    return WemAudioInfo(
        codec=codec,
        channels=info.channels,
        sample_rate=info.sample_rate,
        duration_samples=info.duration_samples,
    )
