"""NewBasis (.nbs) container: VP9 video frames (first byte XOR 0xB0) + optional MP3.

Pure parser/writer, no external tools: playback and export both go through
``to_ivf()`` / ``to_mkv()`` without depending on ffmpeg.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field

from core.newbasis.mkv import build_mkv


@dataclass
class NbsAudio:
    channels: int
    sample_rate: int
    sample_count: int
    mp3: bytes

    @property
    def seconds(self) -> float:
        return self.sample_count / self.sample_rate if self.sample_rate else 0.0


@dataclass
class NbsVideo:
    width: int
    height: int
    fps: int
    frames: list[bytes]
    audio: NbsAudio | None = None
    meta: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def frame_count(self) -> int:
        return len(self.frames)

    @property
    def duration_ms(self) -> int:
        return int(self.frame_count * 1000 / (self.fps or 30))


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _vp9_keyframe_size(frame: bytes) -> tuple[int, int]:
    """Read width/height from a restored VP9 keyframe (profile 0 only)."""
    if (frame[0] >> 4) != 0x8 or frame[1:4] != b"\x49\x83\x42":
        raise ValueError("not a profile 0 VP9 keyframe")
    value = int.from_bytes(frame[4:9], "big")
    if (value >> 37) & 7 == 7:
        raise ValueError("RGB VP9 is not supported")
    return ((value >> 20) & 0xFFFF) + 1, ((value >> 4) & 0xFFFF) + 1


def _parse_audio(data: bytes, sections: dict[int, int]) -> tuple[NbsAudio | None, dict]:
    if 3 not in sections:
        return None, {}
    meta = {}
    if 21 in sections:
        pos = sections[21]
        meta = json.loads(data[pos + 4 : pos + 4 + _u32(data, pos)])
    start = sections[3]
    channels = data[start + 1]
    sample_rate = _u32(data, start + 2)
    sample_count = struct.unpack_from("<Q", data, start + 6)[0]
    length = _u32(data, start + 14)
    mp3 = data[start + 18 : start + 18 + length]
    if len(mp3) != length or mp3[:1] != b"\xff" or (mp3[1] & 0xE0) != 0xE0:
        raise ValueError("audio block is malformed (bad length or MP3 sync word)")
    return NbsAudio(channels, sample_rate, sample_count, mp3), meta


def parse(data: bytes) -> NbsVideo:
    """Parse a .nbs file. Raises ValueError when the container is malformed."""
    if len(data) < 64:
        raise ValueError("file too small to be a .nbs archive")
    section_count = data[2]
    if not 3 <= section_count <= 32 or 3 + 5 * section_count + 26 > len(data):
        raise ValueError("bad header, not a valid .nbs archive")
    sections = {
        data[3 + 5 * i]: _u32(data, 4 + 5 * i) for i in range(section_count)
    }
    if not {0, 1, 2} <= sections.keys() or sections[0] < 3 + 5 * section_count:
        raise ValueError("bad section table, not a valid .nbs archive")

    warnings: list[str] = []
    base = sections[0]
    clip_count = data[base]
    header_w, header_h, frame_count, fps, video_start = (
        _u32(data, base + 2 + 4 * i) for i in range(5)
    )
    fps = fps or 30
    if clip_count != 1:
        warnings.append(f"clip count is {clip_count} (samples use 1), treating as one sequence")
    if video_start != sections[2]:
        warnings.append(
            f"BaseInfo video offset {video_start} != section {sections[2]}, using section table"
        )

    key = sections[1]
    key_count = _u32(data, key)
    if not 0 < key_count <= 1_000_000 or key + 4 + 8 * key_count > len(data):
        raise ValueError("bad keyframe index, not a valid .nbs archive")
    index = [(_u32(data, key + 4 + 8 * i), _u32(data, key + 8 + 8 * i)) for i in range(key_count)]

    pos = sections[2]
    frames: list[bytes] = []
    starts = set()
    for i in range(frame_count):
        if pos + 4 > len(data):
            raise ValueError(f"frame {i}: data ends early")
        size = _u32(data, pos)
        end = pos + 4 + size
        if size == 0 or end > len(data):
            raise ValueError(f"frame {i}: bad size field ({size})")
        frame = bytearray(data[pos + 4 : end])
        frame[0] ^= 0xB0
        if frame[0] >> 6 != 2:
            raise ValueError(f"frame {i}: restored VP9 frame marker mismatch")
        frames.append(bytes(frame))
        starts.add(pos)
        pos = end

    # Frames may be followed by further sections (e.g. type 4); the video block
    # ends where the next section starts, not necessarily at section 20.
    following = [off for kind, off in sections.items() if kind != 2 and off > sections[2]]
    expected_end = min(following) if following else None
    if expected_end is not None and pos != expected_end:
        warnings.append(f"video data ends at {pos}, next section starts at {expected_end}")
    hit = sum(1 for _, offset in index if offset in starts)
    if hit != len(index):
        warnings.append(f"only {hit}/{len(index)} keyframe index entries point at frame starts")

    width, height = _vp9_keyframe_size(frames[0])
    if (width, height) != (header_w, header_h):
        warnings.append(
            f"header size {header_w}x{header_h} != bitstream {width}x{height}, using bitstream"
        )

    audio, meta = _parse_audio(data, sections)
    return NbsVideo(width, height, fps, frames, audio, meta, warnings)


def to_ivf(video: NbsVideo) -> bytes:
    """Serialise frames as an IVF stream (no audio; see ``NbsAudio.mp3``)."""
    header = b"DKIF" + struct.pack(
        "<HH4sHHIII", 0, 32, b"VP90", video.width, video.height, video.fps, 1, video.frame_count
    )
    out = bytearray(header + b"\x00" * 4)
    for i, frame in enumerate(video.frames):
        out += struct.pack("<IQ", len(frame), i)
        out += frame
    return bytes(out)


def to_mkv(video: NbsVideo) -> bytes:
    """Pack video frames and the optional MP3 track into one Matroska file."""
    audio = video.audio
    return build_mkv(
        video.frames,
        video.fps,
        video.width,
        video.height,
        audio.mp3 if audio else None,
        audio.channels if audio else 0,
        audio.sample_rate if audio else 0,
    )
