"""Resource protocol entry for native audio container files.

FORMAT  -> "audio"
probe   -> header-only check for a playable native container: Ogg
           (Vorbis/Opus), FLAC, MP3 (ID3v2 or frame sync), and M4A-brand
           ISO-BMFF audio.  Anything else scores 0; video MP4 stays with
           ``unsupported`` (only audio brands are claimed here).
read    -> ``(ext, data, AudioInfo)``: the bytes are already playable, so
           ``data`` is the payload unchanged -- the container extension
           decides ``ext``.  ``AudioInfo`` describes the stream; for MP3 and
           M4A the duration is not computed here (it needs a full frame or
           stts walk), so ``duration_samples`` is 0 and the player's own
           duration is the display source.

Header parsing is container-level only; codec bitstreams are never decoded.
Ogg helpers live in ``_ogg`` (shared with the WEM pipeline), the rest is a
few bounded struct reads implemented here.

File-like input contract (shared by probe/read): seekable binary stream or
plain bytes; ``probe`` reads only a header.  Stateless: all returns are
fresh objects.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Union

from neoxtractor.core.resources import _ogg
from neoxtractor.core.utils.filelike import read_all, read_head
from neoxtractor.core.utils.result import Category, ProbeResult

FORMAT = "audio"
CANONICAL_TYPE = "tuple[str, bytes, AudioInfo]"

# MPEG audio sample-rate tables by (MPEG version, layer): layer III/II/I.
_MPEG_RATES = {
    (1, 3): [44100, 48000, 32000],
    (1, 2): [44100, 48000, 32000],
    (1, 1): [44100, 48000, 32000],
    (2, 3): [22050, 24000, 16000],
    (2, 2): [22050, 24000, 16000],
    (2, 1): [22050, 24000, 16000],
}
# ISO-BMFF brands that mean "this is an audio-only M4A-family file".
_M4A_BRANDS = (b"M4A ", b"M4B ", b"M4P ", b"m4a ")
# Channels/channelconfig values that are not a plain speaker count.
_OPUS_PROJECTION = 255


@dataclass
class AudioInfo:
    """Descriptive metadata for one native audio payload.

    ``duration_samples`` is exact for Ogg (granule) and FLAC (STREAMINFO);
    0 means "not computed here" (MP3, M4A) -- callers display the player's
    duration in that case.  ``sample_rate`` is 0 only when the stream's
    header could not be parsed at all.
    """

    codec: str            # "vorbis" | "opus" | "flac" | "mp3" | entry fourcc
    channels: int
    sample_rate: int
    duration_samples: int

    @property
    def duration_seconds(self) -> float:
        if self.sample_rate <= 0:
            return 0.0
        return self.duration_samples / self.sample_rate


def _ogg_info(data: bytes) -> Optional[AudioInfo]:
    """Describe an Ogg stream: Vorbis or Opus, via the shared _ogg helpers.

    ``data`` may be a bounded header window (probe) or the whole stream
    (read): the identification headers sit in the first pages, so channels
    and rate always resolve, while the granule walk only reaches the true
    end when handed the full stream -- which is exactly the read path that
    reports duration.
    """
    if data[:4] != b"OggS":
        return None
    head = data[:64 * 1024]
    vorbis = _ogg.parse_vorbis_head(head)
    if vorbis is not None:
        channels, rate = vorbis
        return AudioInfo("vorbis", channels, rate, _ogg.last_granule(data))
    channels, pre_skip, _rate = _ogg.parse_opus_head(head)
    if channels not in (0, _OPUS_PROJECTION):
        granule = _ogg.last_granule(data)
        samples = max(0, granule - pre_skip)
        # Opus granules are 48 kHz; the identification rate is informational.
        return AudioInfo("opus", channels, 48000, samples)
    return None


def _flac_info(data: bytes) -> Optional[AudioInfo]:
    """Describe a native FLAC stream from its STREAMINFO block."""
    if data[:4] != b"fLaC" or len(data) < 4 + 4 + 34:
        return None
    # First metadata block: (last<<7 | type) byte then 3-byte length; STREAMINFO
    # is type 0 with a fixed 34-byte body.
    if (data[4] & 0x7F) != 0 or int.from_bytes(data[5:8], "big") != 34:
        return None
    si = data[8:8 + 34]
    # A single 64-bit field at offset 10 packs, MSB first: sample rate (20) |
    # channels-1 (3) | bits-per-sample-1 (5) | total samples (36).
    field = int.from_bytes(si[10:18], "big")
    rate = field >> 44
    channels = ((field >> 41) & 0x7) + 1
    total_samples = field & 0xFFFFFFFFF
    if rate == 0:
        return None
    return AudioInfo("flac", channels, rate, total_samples)


def _mp3_frame(data: bytes, pos: int) -> Optional[tuple[int, int]]:
    """Parse one MPEG audio frame header at *pos*: (channels, sample_rate)."""
    if pos + 4 > len(data):
        return None
    head = int.from_bytes(data[pos:pos + 4], "big")
    if head & 0xFFE00000 != 0xFFE00000:  # sync + version/layer bits set
        return None
    version = 1 if head & 0x00080000 else 2  # 1 = MPEG1, 0 = MPEG2/2.5
    layer_bits = (head >> 20) & 0x3
    if layer_bits == 0:  # reserved: not a layer I/II/III frame
        return None
    layer = {3: 1, 2: 2, 1: 3}[layer_bits]
    if version == 2 and layer != 3:
        return None  # MPEG2/2.5 carries no layer I/II in the wild here
    rate_idx = (head >> 10) & 0x3
    if rate_idx == 3:
        return None
    rates = _MPEG_RATES.get((version, layer))
    if rates is None:
        return None
    chan_mode = (head >> 6) & 0x3
    channels = 1 if chan_mode == 3 else 2
    return channels, rates[rate_idx]


def _syncsafe(data: bytes, off: int, count: int) -> int:
    """Decode an ID3v2 *count*-byte synchsafe integer (7 bits per byte)."""
    value = 0
    for i in range(count):
        value = (value << 7) | (data[off + i] & 0x7F)
    return value


def _mp3_info(data: bytes) -> Optional[AudioInfo]:
    """Describe an MP3 whose first frame sits at a known position.

    A frame header is read only where one is guaranteed to be: at offset 0
    for a raw stream, or just past the ID3v2 tag otherwise.  The header is
    never searched for -- scanning an arbitrary binary (a RIFF WEM) would
    hit false ``FF Ex`` syncs and mis-claim it.  Past the ID3v2 tag a short
    forward window tolerates tag padding only.
    """
    if data[:3] == b"ID3":
        if len(data) < 10:
            return None
        pos = 10 + _syncsafe(data, 6, 4)
        for frame_pos in range(pos, min(pos + 32, len(data) - 4)):
            info = _mp3_frame(data, frame_pos)
            if info is not None:
                return AudioInfo("mp3", info[0], info[1], 0)
        return None
    info = _mp3_frame(data, 0)
    if info is None:
        return None
    return AudioInfo("mp3", info[0], info[1], 0)


def _u32be(data: bytes, pos: int) -> int:
    return int.from_bytes(data[pos:pos + 4], "big")


def _walk_boxes(data: bytes, start: int, end: int):
    """Yield (type, payload_start, payload_end) for boxes in [start, end)."""
    pos = start
    while pos + 8 <= end:
        size = _u32be(data, pos)
        btype = data[pos + 4:pos + 8]
        body = pos + 8
        if size == 1:
            if pos + 16 > end:
                return
            size = int.from_bytes(data[pos + 8:pos + 16], "big")
            body = pos + 16
        if size < 8 or pos + size > end:
            return
        yield btype, body, pos + size
        pos += size


def _m4a_info(data: bytes) -> Optional[AudioInfo]:
    """Describe an M4A-brand ISO-BMFF file via its first audio sample entry.

    Every audio sample entry shares the AudioSampleEntry prefix, so one
    fixed-offset read covers AAC, ALAC, and friends; the entry fourcc names
    the codec.  moov may sit at either end of the file, so the box walk
    searches the whole buffer.  Duration is left to the player (0).
    """
    if data[4:8] != b"ftyp" or data[8:12] not in _M4A_BRANDS:
        return None
    for mtype, mstart, mend in _walk_boxes(data, 0, len(data)):
        if mtype != b"moov":
            continue
        for track, tstart, tend in _walk_boxes(data, mstart, mend):
            if track != b"trak":
                continue
            for min_, sstart, send in _walk_boxes(data, tstart, tend):
                if min_ != b"mdia":
                    continue
                for inf, istart, iend in _walk_boxes(data, sstart, send):
                    if inf != b"minf":
                        continue
                    for stbl, bstart, bend in _walk_boxes(data, istart, iend):
                        if stbl != b"stbl":
                            continue
                        for st, pstart, pend in _walk_boxes(data, bstart, bend):
                            if st != b"stsd":
                                continue
                            # stsd body: version/flags u32 + entry_count u32,
                            # then the first entry = size u32 + fourcc u32.
                            if pstart + 16 > pend:
                                continue
                            entry = data[pstart + 12:pstart + 16]
                            if entry in (b"mp4a", b"alac"):
                                # AudioSampleEntry, ISO/IEC 14496-14 layout
                                # (verified against ffprobe): from the entry's
                                # first byte -- 6 reserved + 2 data-ref + 2
                                # version + 6 reserved + 2 pre-defined, then
                                # channels u16 @+24 and sample rate u32(16.16)
                                # @+32.
                                base = pstart + 8
                                if base + 36 > pend:
                                    return None
                                channels = int.from_bytes(
                                    data[base + 24:base + 26], "big"
                                )
                                rate = _u32be(data, base + 32) >> 16
                                if channels == 0 or rate == 0:
                                    return None
                                codec = "aac" if entry == b"mp4a" else "alac"
                                return AudioInfo(codec, channels, rate, 0)
    return None


def _head_info(head: bytes) -> Optional[AudioInfo]:
    """Dispatch a header window to the container describer that fits it."""
    if head[:4] == b"OggS":
        return _ogg_info(head)
    if head[:4] == b"fLaC":
        return _flac_info(head)
    if head[:3] == b"ID3":
        return _mp3_info(head)
    if head[4:8] == b"ftyp":
        return _m4a_info(head)
    # A raw MP3 starts straight at a frame sync: only a 0xFF lead byte can
    # be one, so anything else (a RIFF WEM included) is not claimed here.
    if head[:1] == b"\xff":
        return _mp3_info(head)
    return None


_SNIFF_WINDOW = 64 * 1024


def probe(f) -> ProbeResult:
    """Probe *f* as a native audio container (header window only).

    A playable, describable container scores 100.  Ogg/FLAC/M4A need a
    parsed identity header; MP3 needs one valid frame after the tag.  A
    container we cannot describe scores 0 so the entry keeps whatever the
    other modules say about it (video mp4 belongs to ``unsupported``).
    """
    head = read_head(f, _SNIFF_WINDOW)
    info = _head_info(head)
    if info is None:
        return ProbeResult(0)
    return ProbeResult(100, _ext_for(head), Category.AUDIO)


def _ext_for(head: bytes) -> str:
    """Extension for a detection: the container's own, not the codec's."""
    if head[:4] == b"OggS":
        return "ogg"
    if head[:4] == b"fLaC":
        return "flac"
    if head[4:8] == b"ftyp":
        return "m4a"
    return "mp3"


def read(f) -> Union[None, "tuple[str, bytes, AudioInfo]"]:
    """Return ``(ext, data, AudioInfo)`` for a native audio payload.

    The payload is already playable, so the canonical object is the bytes
    themselves; ``read`` declines (None) when the header no longer matches
    what ``probe`` saw, letting the facade move to the next candidate.
    """
    data = read_all(f)
    info = _head_info(data)
    if info is None:
        return None
    return (_ext_for(data), data, info)
