"""Minimal Matroska (``.mkv``) muxer: VP9 video plus an optional MP3 track.

Pure Python on purpose: the project must not depend on ffmpeg, and NBS already
stores raw VP9 frames and a ready-made MP3 stream, so both can be remuxed
without any re-encoding.
"""

from __future__ import annotations

import struct

_KEYFRAME_SYNC = b"\x49\x83\x42"

# MP3 Layer III tables, indexed by header version bits (3 = MPEG-1, 2 = MPEG-2).
_BITRATES = {
    3: (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0),
    2: (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0),
}
_SAMPLE_RATES = {
    3: (44100, 48000, 32000),
    2: (22050, 24000, 16000),
    0: (11025, 12000, 8000),  # MPEG-2.5
}


def _skip_id3v2(data: bytes) -> int:
    """Skip leading ID3v2 tags, which are not MPEG audio frames."""
    pos = 0
    while pos + 10 <= len(data) and data[pos : pos + 3] == b"ID3":
        size = (
            ((data[pos + 6] & 0x7F) << 21)
            | ((data[pos + 7] & 0x7F) << 14)
            | ((data[pos + 8] & 0x7F) << 7)
            | (data[pos + 9] & 0x7F)
        )
        pos += 10 + size
    return pos


def parse_mp3_frames(data: bytes) -> list[tuple[bytes, int, int]]:
    """Split an MP3 stream into Layer III frames as ``(frame, samples, rate)``."""
    frames: list[tuple[bytes, int, int]] = []
    pos = _skip_id3v2(data)
    while pos + 4 <= len(data):
        if data[pos] != 0xFF or (data[pos + 1] & 0xE0) != 0xE0:
            pos += 1
            continue
        version = (data[pos + 1] >> 3) & 3
        layer = (data[pos + 1] >> 1) & 3
        bitrate_index = (data[pos + 2] >> 4) & 0xF
        rate_index = (data[pos + 2] >> 2) & 3
        padding = (data[pos + 2] >> 1) & 1
        if layer != 1 or version == 1 or bitrate_index in (0, 15) or rate_index == 3:
            pos += 1
            continue
        bitrate = _BITRATES[version if version == 3 else 2][bitrate_index] * 1000
        rate = _SAMPLE_RATES[version][rate_index]
        samples = 1152 if version == 3 else 576
        size = (144 if version == 3 else 72) * bitrate // rate + padding
        if size < 4 or pos + size > len(data):
            break
        frames.append((data[pos : pos + size], samples, rate))
        pos += size
    return frames


def _size_vint(value: int) -> bytes:
    """Encode an EBML size as the shortest data size descriptor."""
    for length in range(1, 9):
        if value < (1 << (7 * length)) - 1:
            break
    else:
        raise ValueError("matroska element is too large")
    data = bytearray(length)
    for index in range(length - 1, 0, -1):
        data[index] = value & 0xFF
        value >>= 8
    data[0] = value | (1 << (8 - length))
    return bytes(data)


def _element(element_id: bytes | int, payload: bytes) -> bytes:
    if isinstance(element_id, int):
        element_id = element_id.to_bytes((element_id.bit_length() + 7) // 8, "big")
    return element_id + _size_vint(len(payload)) + payload


def _uint(value: int) -> bytes:
    return value.to_bytes(max(1, (value.bit_length() + 7) // 8), "big")


def _element_uint(element_id: bytes, value: int) -> bytes:
    return _element(element_id, _uint(value))


def _element_float(element_id: bytes, value: float) -> bytes:
    return _element(element_id, struct.pack(">d", value))


def _element_str(element_id: bytes, value: str) -> bytes:
    return _element(element_id, value.encode("ascii"))


def _track(
    number: int, track_type: int, codec_id: str, settings: bytes = b""
) -> bytes:
    payload = (
        _element_uint(0xD7, number)
        + _element_uint(0x73C5, number)  # TrackUID
        + _element_uint(0x83, track_type)
        + _element_str(0x86, codec_id)
        + settings
    )
    return _element(b"\xAE", payload)


def _block(track: int, relative: int, keyframe: bool, payload: bytes) -> bytes:
    flags = 0x80 if keyframe else 0x00
    return bytes([0x80 | track]) + struct.pack(">hB", relative, flags) + payload


def build_mkv(
    video_frames: list[bytes],
    fps: int,
    width: int,
    height: int,
    mp3: bytes | None = None,
    channels: int = 0,
    sample_rate: int = 0,
) -> bytes:
    """Remux VP9 frames and an optional MP3 stream into one Matroska file.

    Timestamps use a 1 ms timecode scale; clusters are cut every second so the
    signed 16-bit block offsets stay valid even for long clips.
    """
    if not video_frames:
        raise ValueError("cannot build a Matroska file without video frames")
    fps = fps or 30

    # (timecode, track, keyframe, payload); videos come first for equal times.
    items: list[tuple[int, int, bool, bytes]] = []
    for index, frame in enumerate(video_frames):
        items.append((index * 1000 // fps, 1, frame[1:4] == _KEYFRAME_SYNC, frame))

    audio_seconds = 0.0
    if mp3:
        samples_done = 0
        for frame, samples, frame_rate in parse_mp3_frames(mp3):
            items.append((samples_done * 1000 // frame_rate, 2, True, frame))
            samples_done += samples
        if sample_rate:
            audio_seconds = samples_done / sample_rate
    items.sort(key=lambda item: item[0])

    video_seconds = len(video_frames) / fps
    # Duration is expressed in TimecodeScale units (1 ms), not in seconds.
    duration_ms = max(video_seconds, audio_seconds) * 1000
    info = _element(
        b"\x15\x49\xA9\x66",
        _element_uint(0x2AD7B1, 1_000_000)  # TimecodeScale: 1 ms
        + _element_str(0x4D80, "NeoXtractor")
        + _element_str(0x5741, "NeoXtractor")
        + _element_float(0x4489, duration_ms),
    )

    video_settings = _element(
        b"\xE0", _element_uint(0xB0, width) + _element_uint(0xBA, height)
    )
    entries = _track(1, 1, "V_VP9", video_settings)
    if mp3:
        audio_settings = _element(
            b"\xE1",
            _element_float(0xB5, float(sample_rate))
            + _element_uint(0x9F, channels or 2),
        )
        entries += _track(2, 2, "A_MPEG/L3", audio_settings)
    tracks = _element(b"\x16\x54\xAE\x6B", entries)

    # Cluster grouping: at most one second (or 600 blocks) per cluster.
    clusters: list[tuple[int, list[tuple[int, int, bool, bytes]]]] = []
    cluster_start = None
    cluster_blocks: list[tuple[int, int, bool, bytes]] = []
    for timecode, track, keyframe, payload in items:
        if (
            cluster_start is None
            or timecode - cluster_start > 1000
            or len(cluster_blocks) >= 600
        ):
            if cluster_start is not None:
                clusters.append((cluster_start, cluster_blocks))
            cluster_start = timecode
            cluster_blocks = []
        cluster_blocks.append((track, timecode - cluster_start, keyframe, payload))
    if cluster_start is not None:
        clusters.append((cluster_start, cluster_blocks))

    segment = bytearray(info + tracks)
    cue_points: list[tuple[int, int]] = []
    for start, blocks in clusters:
        cue_points.append((start, len(segment)))
        body = bytearray(_element_uint(0xE7, start))
        for track, relative, keyframe, payload in blocks:
            body += _element(b"\xA3", _block(track, relative, keyframe, payload))
        segment += _element(b"\x1F\x43\xB6\x75", bytes(body))

    cues = bytearray()
    for cue_time, position in cue_points:
        cues += _element(
            b"\xBB",
            _element_uint(0xB3, cue_time)
            + _element(
                b"\xB7",
                _element_uint(0xF7, 1)
                + _element_uint(0xF1, position),
            ),
        )
    segment += _element(b"\x1C\x53\xBB\x6B", bytes(cues))

    ebml = _element(
        b"\x1A\x45\xDF\xA3",
        _element_uint(0x4286, 1)  # EBMLVersion
        + _element_uint(0x42F7, 1)  # EBMLReadVersion
        + _element_uint(0x42F2, 4)  # EBMLMaxIDLength
        + _element_uint(0x42F3, 8)  # EBMLMaxSizeLength
        + _element_str(0x4282, "matroska")
        + _element_uint(0x4287, 4)  # DocTypeVersion
        + _element_uint(0x4285, 2),  # DocTypeReadVersion
    )
    return ebml + _element(b"\x18\x53\x80\x67", bytes(segment))
