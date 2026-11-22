"""HPMA skeletons (.skeleton) and animations (.rawanimation).

A .rawanimation is a set of NeoX chunks: RAWANIMA (skeleton path), HEAD (frame rate, duration), DATA and
NAME (track names). DATA is either an ACL 1.x compressed clip (most clips) or plain keyframes, described by a
small node tree in HEAD: times, positions, rotations (xyzw) and scales, each array 16-byte aligned.
"""

from __future__ import annotations

import struct

import numpy as np

ACL_TAG = 0xAC10AC10
# ACL 1.x bit rate index -> bits per component (18 = raw 32-bit floats)
BIT_RATE_BITS = [0, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 32]


def _chunks(data: bytes, start: int) -> dict[bytes, tuple[int, int]]:
    """Map of chunk tag -> (payload offset, payload size)."""
    found, pos = {}, start
    while pos + 8 <= len(data):
        tag, size = data[pos : pos + 4], struct.unpack_from("<I", data, pos + 4)[0]
        if not tag.isalpha():
            break
        found[tag] = (pos + 8, size)
        pos += 8 + size
    return found


def _names(data: bytes, offset: int) -> list[str]:
    count = struct.unpack_from("<I", data, offset)[0]
    names, pos = [], offset + 4
    for _ in range(count):
        length = struct.unpack_from("<I", data, pos)[0]
        names.append(data[pos + 4 : pos + 4 + length].decode("utf-8", "replace"))
        pos += 4 + length
    return names


class Skeleton:
    """Bone names, parents and bind-pose local transforms (position, quaternion xyzw, scale)."""

    def __init__(self, data: bytes):
        if data[:8] != b"SKELETON":
            raise ValueError("Not a NeoX skeleton")
        header = struct.unpack_from("<I", data, 20)[0]
        chunks = _chunks(data, header)
        body, size = chunks[b"DATA"]
        names = _names(data, chunks[b"NAME"][0])
        self.name, self.bone_names = names[0], names[1:]
        count = len(self.bone_names)

        # DATA holds bone ids (u32), parents (i16) and bind transforms (10 floats each),
        # every array starting 16-byte aligned from the start of the chunk
        def align(value):
            return (value + 15) & ~15

        parents_at = body + align(4 * count)
        transforms_at = body + align(align(4 * count) + 2 * count)
        if transforms_at + 40 * count > body + size:
            raise ValueError("Unexpected skeleton layout")
        self.parents = list(struct.unpack_from(f"<{count}h", data, parents_at))
        if self.parents[0] != -1 or any(not -1 <= p < count for p in self.parents):
            raise ValueError("Could not read the skeleton hierarchy")
        values = np.frombuffer(data, "<f4", count * 10, transforms_at).reshape(count, 10)
        self.positions = values[:, 0:3].astype(np.float64)
        self.rotations = values[:, 3:7].astype(np.float64)
        self.scales = values[:, 7:10].astype(np.float64)


class _Bits:
    """Big-endian bit stream, as ACL packs its samples."""

    def __init__(self, data: bytes, start: int):
        self.data, self.start = data, start

    def read(self, bit_offset: int, bits: int) -> int:
        byte, shift = self.start + bit_offset // 8, bit_offset % 8
        span = (shift + bits + 7) // 8
        value = int.from_bytes(self.data[byte : byte + span], "big")
        return (value >> (span * 8 - shift - bits)) & ((1 << bits) - 1)


class Animation:
    """A decoded clip: per-frame local position, rotation (xyzw) and scale for every bone."""

    def __init__(self, data: bytes):
        if data[:8] != b"RAWANIMA":
            raise ValueError("Not a NeoX animation")
        header = struct.unpack_from("<I", data, 20)[0]
        path_length = struct.unpack_from("<I", data, 40)[0]
        self.skeleton_path = data[44 : 44 + path_length].decode("utf-8", "replace")
        chunks = _chunks(data, header)
        self.track_names = _names(data, chunks[b"NAME"][0]) if b"NAME" in chunks else []
        acl = chunks[b"DATA"][0]
        if struct.unpack_from("<I", data, acl + 8)[0] != ACL_TAG and b"HEAD" in chunks:
            self._decode_keyframes(data, chunks[b"HEAD"][0], acl)
        else:
            self._decode(data, acl)

    def _decode_keyframes(self, data: bytes, head: int, start: int):
        """The plain keyframe layout (see the module docstring), resampled at the clip's frame rate."""
        rate, duration = struct.unpack_from("<ff", data, head)
        try:
            tree, _end = _read_node(data, head + 36)
        except (ValueError, struct.error, UnicodeDecodeError) as exc:
            raise ValueError(f"Unsupported animation layout ({exc})") from exc
        arrays, offset = [], 0
        for size, count in _leaves(tree):
            offset = (offset + 15) & ~15
            arrays.append((size, count, start + offset))
            offset += size * count
        if not arrays or arrays[0][0] != 4 or (len(arrays) - 1) % 6:
            raise ValueError("Unsupported animation layout")
        _size, joints, at = arrays[0]
        indices = np.frombuffer(data, "<u4", joints, at)
        tracks = (len(arrays) - 1) // 6
        rate = rate or 30.0
        frames = max(1, int(round(duration * rate)) + 1)
        times = np.minimum(np.arange(frames) / rate, duration)
        # Components with no keys stay NaN: Clip keeps the skeleton's rest pose for them
        self.positions = np.full((frames, tracks, 3), np.nan)
        self.rotations = np.full((frames, tracks, 4), np.nan)
        self.scales = np.full((frames, tracks, 3), np.nan)
        for track in range(tracks):
            group = arrays[1 + 6 * track : 7 + 6 * track]
            if tuple(size for size, _count, _at in group) != (4, 12, 4, 16, 4, 12):
                raise ValueError("Unsupported animation layout")
            for (_t, count, key_at), (width, _c, value_at), target in zip(
                group[0::2], group[1::2], (self.positions, self.rotations, self.scales)
            ):
                if not count:
                    continue
                keys = np.frombuffer(data, "<f4", count, key_at).astype(np.float64)
                values = np.frombuffer(data, "<f4", count * (width // 4), value_at).reshape(count, width // 4)
                target[:, track] = _resample(keys, values.astype(np.float64), times, width == 16)
        names = list(self.track_names)
        first = names[0] if names else ""
        self.track_names = [first] + [names[i] if i < len(names) else f"joint_{i}" for i in indices[:tracks]]
        self.bone_count, self.frame_count, self.frame_rate = tracks, frames, rate

    def _decode(self, data: bytes, acl: int):
        _size, _hash, tag = struct.unpack_from("<III", data, acl)
        if tag != ACL_TAG:
            raise ValueError("Unsupported animation compression")
        base = acl + 16  # ACL clip header
        (
            bones,
            segments,
            rot_fmt,
            trans_fmt,
            scale_fmt,
            clip_rr,
            seg_rr,
            has_scale,
            default_scale,
            _pad,
            samples,
            rate,
            seg_off,
            default_off,
            constant_off,
            constant_data_off,
            clip_range_off,
        ) = struct.unpack_from("<HHBBBBBBBBIfHHHHH", data, base)
        if rot_fmt != 4 or trans_fmt != 3 or (has_scale and scale_fmt != 3):
            raise ValueError(f"Unsupported ACL formats {rot_fmt}/{trans_fmt}/{scale_fmt}")
        self.bone_count, self.frame_count, self.frame_rate = bones, samples, rate
        channels = 3 if has_scale else 2
        tracks = bones * channels

        def bit(offset, index):
            word = struct.unpack_from("<I", data, base + offset + 4 * (index // 32))[0]
            return (word >> (31 - index % 32)) & 1

        default = [bit(default_off, i) for i in range(tracks)]
        constant = [bit(constant_off, i) for i in range(tracks)]
        animated = [i for i in range(tracks) if not default[i] and not constant[i]]

        values = np.zeros((samples, tracks, 3))
        for i in range(tracks):
            if default[i] and i % channels == 2:
                values[:, i] = 1.0 if default_scale else 0.0
        pos = base + constant_data_off
        for i in range(tracks):
            if constant[i] and not default[i]:
                values[:, i] = struct.unpack_from("<3f", data, pos)
                pos += 12

        # Range reduction is flagged per track type (1 rotation, 2 translation, 4 scale), for the
        # whole clip and per segment; tracks of an unflagged type have no range data at all
        def reduced(flags, i):
            return bool(flags & (1 << (i % channels)))

        clip_min, clip_extent = {}, {}
        pos = base + clip_range_off
        for i in animated:
            if reduced(clip_rr, i):
                clip_min[i] = np.array(struct.unpack_from("<3f", data, pos))
                clip_extent[i] = np.array(struct.unpack_from("<3f", data, pos + 12))
                pos += 24

        sample = 0
        for segment in range(segments):
            count, pose_bits, formats_off, range_off, track_off = struct.unpack_from(
                "<5I", data, base + seg_off + 20 * segment
            )
            formats = data[base + formats_off : base + formats_off + len(animated)]
            stream = _Bits(data, base + track_off)
            has_segment_ranges = range_off != 0xFFFFFFFF
            ranges = base + range_off if has_segment_ranges else 0
            for frame in range(count):
                bit_pos = frame * pose_bits
                range_pos = ranges
                for slot, i in enumerate(animated):
                    num_bits = BIT_RATE_BITS[formats[slot]]
                    segment_range = has_segment_ranges and reduced(seg_rr, i)
                    if num_bits == 0:  # constant within the segment: 3 x u16 in the range data
                        raw = struct.unpack_from(">3H", data, range_pos) if segment_range else (0, 0, 0)
                        value = np.array(raw) / 65535.0
                    elif num_bits == 32:  # raw floats, no range reduction
                        value = np.array(
                            [
                                struct.unpack(">f", stream.read(bit_pos + 32 * c, 32).to_bytes(4, "big"))[0]
                                for c in range(3)
                            ]
                        )
                        bit_pos += 96
                    else:
                        scale = (1 << num_bits) - 1
                        value = np.array([stream.read(bit_pos + num_bits * c, num_bits) for c in range(3)]) / scale
                        bit_pos += 3 * num_bits
                        if segment_range:
                            seg_min = np.frombuffer(data, np.uint8, 3, range_pos) / 255.0
                            seg_extent = np.frombuffer(data, np.uint8, 3, range_pos + 3) / 255.0
                            value = value * seg_extent + seg_min
                    if num_bits != 32 and i in clip_min:
                        value = value * clip_extent[i] + clip_min[i]
                    if segment_range:
                        range_pos += 6
                    values[sample + frame, i] = value
            sample += count

        self.positions = values[:, 1::channels, :]
        rotations_xyz = values[:, 0::channels, :]
        w = np.sqrt(np.clip(1.0 - (rotations_xyz**2).sum(-1, keepdims=True), 0.0, 1.0))
        self.rotations = np.concatenate([rotations_xyz, w], -1)
        for i in range(bones):  # default rotation tracks are identity
            if default[i * channels]:
                self.rotations[:, i] = (0.0, 0.0, 0.0, 1.0)
        self.scales = values[:, 2::channels, :] if has_scale else np.ones((samples, bones, 3))
        self.default_tracks, self.constant_tracks, self.animated_tracks = default, constant, animated

    @property
    def duration(self) -> float:
        return (self.frame_count - 1) / self.frame_rate if self.frame_rate else 0.0


def _read_string(data: bytes, pos: int) -> tuple[str, int]:
    length = struct.unpack_from("<I", data, pos)[0]
    if length > 256:
        raise ValueError(f"bad name length {length} at {pos}")
    return data[pos + 4 : pos + 4 + length].decode("ascii"), pos + 4 + length


def _read_node(data: bytes, pos: int):
    """One node of a keyframe clip's HEAD tree: SNode / DNode (named, with children), LNode (array), Empty."""
    kind, pos = _read_string(data, pos)
    if kind in ("SNode", "DNode"):
        name, pos = _read_string(data, pos)
        count = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        children = []
        for _ in range(count):
            child, pos = _read_node(data, pos)
            children.append(child)
        return (kind, name, children), pos
    if kind == "LNode":  # element size in bytes, element count
        return (kind,) + struct.unpack_from("<II", data, pos), pos + 8
    if kind == "Empty":
        return (kind,), pos
    raise ValueError(f"unknown node {kind!r}")


def _leaves(node):
    """(element size, count) of every array in the tree, in the order DATA stores them."""
    if node[0] == "LNode":
        yield node[1], node[2]
    elif node[0] in ("SNode", "DNode"):
        for child in node[2]:
            yield from _leaves(child)


def _resample(keys, values, times, rotation: bool):
    """Values at `times` from keys at their own times: linear, rotations along the shorter way (normalised)."""
    if len(keys) == 1:
        return np.repeat(values, len(times), 0)
    if not rotation:
        return np.stack([np.interp(times, keys, values[:, c]) for c in range(values.shape[1])], -1)
    after = np.clip(np.searchsorted(keys, times, side="right"), 1, len(keys) - 1)
    before = after - 1
    span = np.maximum(keys[after] - keys[before], 1e-9)
    blend = np.clip((times - keys[before]) / span, 0.0, 1.0)[:, None]
    q0, q1 = values[before], values[after]
    q1 = np.where(((q0 * q1).sum(-1) < 0)[:, None], -q1, q1)
    result = q0 * (1 - blend) + q1 * blend
    return result / np.maximum(np.linalg.norm(result, axis=-1, keepdims=True), 1e-12)


def quaternion_matrices(quaternions) -> np.ndarray:
    """(..., 4) xyzw quaternions -> (..., 3, 3) rotation matrices (column-vector convention)."""
    q = np.asarray(quaternions, float)
    q = q / np.maximum(np.linalg.norm(q, axis=-1, keepdims=True), 1e-12)
    x, y, z, w = q[..., 0], q[..., 1], q[..., 2], q[..., 3]
    return np.stack(
        [
            np.stack([1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)], -1),
            np.stack([2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)], -1),
            np.stack([2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)], -1),
        ],
        -2,
    )


def matrix_quaternion(matrix) -> np.ndarray:
    """3x3 rotation matrix -> xyzw quaternion."""
    m = np.asarray(matrix, float)
    trace = m[0, 0] + m[1, 1] + m[2, 2]
    if trace > 0:
        s = np.sqrt(trace + 1.0) * 2
        q = [(m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s, 0.25 * s]
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = np.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2
        q = [0.25 * s, (m[0, 1] + m[1, 0]) / s, (m[0, 2] + m[2, 0]) / s, (m[2, 1] - m[1, 2]) / s]
    elif m[1, 1] > m[2, 2]:
        s = np.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2
        q = [(m[0, 1] + m[1, 0]) / s, 0.25 * s, (m[1, 2] + m[2, 1]) / s, (m[0, 2] - m[2, 0]) / s]
    else:
        s = np.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2
        q = [(m[0, 2] + m[2, 0]) / s, (m[1, 2] + m[2, 1]) / s, 0.25 * s, (m[1, 0] - m[0, 1]) / s]
    q = np.array(q)
    return q / np.linalg.norm(q)


class Clip:
    """A decoded animation bound to its skeleton, sampled as a loop."""

    def __init__(self, name: str, animation: Animation, skeleton: Skeleton, loop: bool = True):
        self.name, self.animation, self.skeleton, self.loop = name, animation, skeleton, loop
        self.bone_names = skeleton.bone_names
        self.parents = skeleton.parents
        frames, bones = animation.frame_count, len(skeleton.bone_names)
        # Tracks follow the clip's own bone list (its NAME chunk), which can be a subset of the
        # skeleton in another order: character clips leave out bones they do not move. Bones
        # without a track keep the skeleton's rest pose.
        self.positions = np.repeat(skeleton.positions[None, :bones], frames, 0).astype(np.float64)
        self.rotations = np.repeat(skeleton.rotations[None, :bones], frames, 0).astype(np.float64)
        self.scales = np.repeat(skeleton.scales[None, :bones], frames, 0).astype(np.float64)
        names = list(animation.track_names)
        if len(names) == animation.bone_count + 1:  # the list may start with the clip / skeleton name
            names = names[1:]
        index = {name: i for i, name in enumerate(skeleton.bone_names)}
        if len(names) == animation.bone_count and any(name in index for name in names):
            targets = [(track, index[name]) for track, name in enumerate(names) if name in index]
        else:  # no usable names: tracks are the skeleton's bones in order
            targets = [(track, track) for track in range(min(animation.bone_count, bones))]
        for track, bone in targets:
            for own, theirs in (
                (self.positions, animation.positions),
                (self.rotations, animation.rotations),
                (self.scales, animation.scales),
            ):
                values = theirs[:, track]
                if not np.isnan(values).any():
                    own[:, bone] = values
                elif not np.isnan(values).all():  # partly keyed: rest pose where it has none
                    own[:, bone] = np.where(np.isnan(values), own[:, bone], values)
        self.mapped_tracks = len(targets)
        self.frame_count, self.frame_rate = frames, animation.frame_rate or 30.0
        self.duration = max(self.frame_count - 1, 1) / self.frame_rate

    def local_pose(self, time: float):
        """Interpolated local (position, rotation xyzw, scale) for every bone at a time in seconds."""
        frame = (time % self.duration if self.loop else min(max(time, 0.0), self.duration)) * self.frame_rate
        first = int(np.floor(frame))
        second = min(first + 1, self.frame_count - 1)
        first = min(first, self.frame_count - 1)
        blend = frame - first
        q0, q1 = self.rotations[first], self.rotations[second]
        q1 = np.where(((q0 * q1).sum(-1) < 0)[:, None], -q1, q1)  # shortest path
        rotation = q0 * (1 - blend) + q1 * blend
        rotation /= np.maximum(np.linalg.norm(rotation, axis=-1, keepdims=True), 1e-12)
        position = self.positions[first] * (1 - blend) + self.positions[second] * blend
        scale = self.scales[first] * (1 - blend) + self.scales[second] * blend
        return position, rotation, scale

    def local_matrices(self, time: float) -> np.ndarray:
        position, rotation, scale = self.local_pose(time)
        matrices = np.tile(np.eye(4), (len(position), 1, 1))
        matrices[:, :3, :3] = quaternion_matrices(rotation) * scale[:, None, :]
        matrices[:, :3, 3] = position
        return matrices

    def world_matrices(self, time: float, root=None) -> np.ndarray:
        """Column-vector model-space matrices of every skeleton bone (optionally under a placement)."""
        local = self.local_matrices(time)
        world = np.empty_like(local)
        base = np.eye(4) if root is None else np.asarray(root, float)
        for index, parent in enumerate(self.parents[: len(local)]):
            world[index] = (world[parent] if parent >= 0 else base) @ local[index]
        return world


def _join(folder: str, relative: str) -> str:
    parts = [p for p in folder.split("\\") if p]
    for piece in relative.replace("/", "\\").split("\\"):
        if piece == "..":
            if parts:
                parts.pop()
        elif piece and piece != ".":
            parts.append(piece)
    return "\\".join(parts)


def load_default_clip(read, model_path: str, report=None):
    """
    The auto-play animation of a model (from <model>.animconfig), or None.
    read(path) -> (bytes, actual path) | None, e.g. PackIndex.read.
    """
    import xml.etree.ElementTree as ET

    stem = model_path.rsplit(".", 1)[0]
    found = read(stem + ".animconfig")
    if found is None:
        return None
    folder = stem.rsplit("\\", 1)[0]
    try:
        config = ET.fromstring(found[0])
        default = config.find("DefaultAnimation")
        if default is None or default.get("Autoplay", "true").lower() != "true":
            return None
        wanted = default.get("Name")
        entry = next((a for a in config.iter("Animation") if a.get("Name") == wanted), None)
        if entry is None:
            return None
        animation_path = _join(folder, entry.get("FileName", ""))
        descriptor = read(animation_path)
        loop = True
        raw_path = animation_path.rsplit(".", 1)[0] + ".rawanimation"
        if descriptor is not None:
            meta = ET.fromstring(descriptor[0])
            dependency = meta.find(".//Dependency/FileName")
            if dependency is not None and dependency.get("Value"):
                raw_path = _join(animation_path.rsplit("\\", 1)[0], dependency.get("Value"))
            prop = meta.find("Property")
            if prop is not None:
                loop = prop.get("DefaultLoop", "true").lower() == "true"
        raw = read(raw_path)
        if raw is None:
            raise FileNotFoundError(raw_path)
        animation = Animation(raw[0])
        skeleton_path = (
            _join(raw_path.rsplit("\\", 1)[0], animation.skeleton_path)
            if animation.skeleton_path
            else stem + ".skeleton"
        )
        skeleton_data = read(skeleton_path) or read(stem + ".skeleton")
        if skeleton_data is None:
            raise FileNotFoundError(skeleton_path)
        return Clip(wanted, animation, Skeleton(skeleton_data[0]), loop)
    except Exception as exc:
        if report is not None:
            report.append(f"animation of {stem}: {exc}")
        return None
