"""
Export an animated HPMA object as an MMD pair: <name>.pmx (+ textures) and <name>.vmd with its loop.

The PMX comes from the regular converter, so it uses the same space: NeoX turned 180 degrees
around Y (MMD faces -Z). VMD keys are per bone: a rotation and a position offset, both in
the parent's frame relative to the rest pose, as MMD expects for bones without local axes.
"""

from __future__ import annotations

import re
import struct
from pathlib import Path

import numpy as np

from core.mesh_converter import bone_names, model_export
from core.npk.neox_anim import matrix_quaternion

VMD_FPS = 30.0
NAME_BYTES = 15                      # VMD bone names are 15 bytes of Shift-JIS
TURN = np.diag([-1.0, 1.0, -1.0, 1.0])  # 180 degrees around Y: NeoX -> PMX space (a rotation, not a mirror)
# The usual "linear" interpolation block for bone keyframes
LINEAR = bytes([20, 20, 0, 0, 20, 20, 20, 20, 107, 107, 107, 107, 107, 107, 107, 107,
                20, 20, 20, 20, 20, 20, 20, 107, 107, 107, 107, 107, 107, 107, 107, 0,
                20, 20, 20, 20, 20, 20, 107, 107, 107, 107, 107, 107, 107, 107, 0, 0,
                20, 20, 20, 20, 20, 107, 107, 107, 107, 107, 107, 107, 107, 0, 0, 0])


def _sjis(text: str, size: int) -> bytes:
    data = text.encode("shift_jis", "replace")[:size]
    return data + b"\0" * (size - len(data))


def fit_name(name: str) -> str:
    """
    A bone name within VMD's 15 bytes that depends only on the name (so a PMX and a VMD listing bones
    in different orders still agree): "bone_" dropped, then underscores, then a short checksum.
    """
    import zlib

    def size(text):
        return len(text.encode("shift_jis", "replace"))

    if size(name) <= NAME_BYTES:
        return name
    short = name[5:] if name.lower().startswith("bone_") else name
    if size(short) > NAME_BYTES:
        short = short.replace("_", "")
    if size(short) > NAME_BYTES:
        tag = f"{zlib.crc32(name.encode('utf-8')) & 0xFFFF:04x}"
        short = short.encode("shift_jis", "replace")[:NAME_BYTES - len(tag)].decode("shift_jis", "ignore") + tag
    return short


def short_bone_names(names: list[str]) -> list[str]:
    """Bone names that fit VMD's 15 bytes and stay unique (used in both the PMX and the VMD)."""
    result, used = [], set()
    for name in names:
        short = fit_name(name)
        number = 1
        while short in used:
            number += 1
            tail = f"~{number}"
            short = name.encode("shift_jis", "replace")[:NAME_BYTES - len(tail)].decode("shift_jis", "ignore") + tail
        used.add(short)
        result.append(short)
    return result


def vmd_keyframes(mesh, clip, frames: int | None = None):
    """
    Bone keyframes [(bone index, frame, position xyz, rotation xyzw)] for a merged single-object
    mesh (model_export.merge of one item at the origin) driven by a neox_anim.Clip.
    """
    count = len(mesh.bone_name)
    binds = np.array([TURN @ np.asarray(m, float).T @ TURN for m in mesh.bone_matrix])      # PMX space, column-vector
    bind_rot = binds[:, :3, :3] / np.maximum(np.linalg.norm(binds[:, :3, :3], axis=1, keepdims=True), 1e-12)
    parents = mesh.bone_parent
    skeleton = {name: i for i, name in enumerate(clip.bone_names)}
    mapped = [skeleton.get(name.split("/", 1)[-1], -1) for name in mesh.bone_name]
    frames = frames or max(1, int(round(clip.duration * VMD_FPS)) + 1)
    keys = []
    for frame in range(frames):
        world = clip.world_matrices(frame / VMD_FPS)
        poses = binds.copy()
        for bone, source in enumerate(mapped):
            if source >= 0:
                poses[bone] = TURN @ world[source] @ TURN
        rot = poses[:, :3, :3] / np.maximum(np.linalg.norm(poses[:, :3, :3], axis=1, keepdims=True), 1e-12)
        delta = rot @ np.transpose(bind_rot, (0, 2, 1))                                        # world-space rotation since rest
        for bone in range(count):
            if mapped[bone] < 0:
                continue
            parent = parents[bone]
            parent_delta = delta[parent] if parent >= 0 else np.eye(3)
            local = parent_delta.T @ delta[bone]
            if parent >= 0:
                offset = parent_delta.T @ (poses[bone][:3, 3] - poses[parent][:3, 3]) - (binds[bone][:3, 3] - binds[parent][:3, 3])
            else:
                offset = poses[bone][:3, 3] - binds[bone][:3, 3]
            keys.append((bone, frame, offset, matrix_quaternion(local)))
    return keys, frames


def write_vmd(path, model_name: str, bone_names: list[str], keys, ik_off=()) -> Path:
    """ik_off: IK bones switched off from frame 0 (motions baked from the game drive the legs themselves)."""
    path = Path(path)
    from core.mesh_converter import fingers
    bone_names, keys = fingers.copy_finger_keys(bone_names, keys)    # ring and little follow the middle finger
    out = bytearray(_sjis("Vocaloid Motion Data 0002", 30))
    out += _sjis(model_name, 20)
    out += struct.pack("<I", len(keys))
    for bone, frame, position, rotation in keys:
        out += _sjis(bone_names[bone], NAME_BYTES)
        out += struct.pack("<I3f4f", frame, *map(float, position), *map(float, rotation))
        out += LINEAR
    out += struct.pack("<4I", 0, 0, 0, 0)         # morphs, cameras, lights, self shadows
    if ik_off:
        out += struct.pack("<I", 1)                # one show/IK frame at 0: model shown, IK off
        out += struct.pack("<IBI", 0, 1, len(ik_off))
        for name in ik_off:
            out += _sjis(name, 20) + b"\x00"
    else:
        out += struct.pack("<I", 0)
    path.write_bytes(bytes(out))
    return path


def write_pmx_vmd(item: dict, folder) -> tuple[Path, Path]:
    """<folder>/<name>.pmx (+ <name>_textures) and <name>.vmd for one animated item, placed at the origin."""
    clip = item.get("animation")
    if clip is None:
        raise ValueError(f"{item['name']} has no animation")
    name = re.sub(r'[<>:"/\\|?*\s]+', "_", re.sub(r"\.(mesh|gim)$", "", item["name"], flags=re.I)) or "model"
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    single = dict(item, matrix=None)                 # MMD wants the model at the origin
    mesh, _materials = model_export.merge([single])
    # Translate first so the PMX and the VMD use the same (MMD) bone names
    names = short_bone_names(bone_names.translate(list(mesh.bone_name)) if bone_names.enabled() else list(mesh.bone_name))
    pmx_path = model_export.write_pmx([single], folder / f"{name}.pmx", bone_names=names)
    from core.mesh_converter import mmd_rig
    rigged = bone_names.enabled() and mmd_rig.enabled()
    if rigged:                                       # bake against the rigged PMX's chains
        mesh.bone_parent = mmd_rig.vmd_parents(names, list(mesh.bone_parent))
    keys, _frames = vmd_keyframes(mesh, clip)
    vmd_names = [mmd_rig.vmd_name(n) for n in names] if rigged else names
    vmd_path = write_vmd(folder / f"{name}.vmd", name, vmd_names, keys, ik_off=mmd_rig.IK_BONES if rigged else ())
    return pmx_path, vmd_path
