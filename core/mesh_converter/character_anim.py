"""
Character animations (model\\character\\<sex>\\animations_<skeleton>\\*.rawanimation) as VMD or GLB.

Both genders share one skeleton each; its rest pose is the bind pose the character meshes (and the
PMX files exported from them) use, so a VMD made against it plays on those PMX models. The
skeleton names bones with spaces ("bip001 l foot"), the meshes with underscores ("bip001_l_foot"):
clips are renamed to the mesh spelling, then the VMD gets the same MMD names (bone_names) as the PMX.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from core.mesh_converter import bone_names, model_export, vmd_export
from core.npk import neox_anim

RIGS = {
    "male": {
        "skeleton": "model\\character\\male\\male_fenli_b72732.skeleton",
        "folder": "model\\character\\male\\animations_male_fenli_b72732",
        "armature": "model\\character\\male\\male_fenli.mesh",  # 4-vertex mesh carrying every bone
        "character": "model\\character\\male\\nanzhu001\\nanzhu001_fenli.mesh",
    },
    "female": {
        "skeleton": "model\\character\\female\\female_fenli_5653f7.skeleton",
        "folder": "model\\character\\female\\animations_female_fenli_5653f7",
        "armature": "model\\character\\female\\female_fenli.mesh",
        "character": "model\\character\\female\\nvzhu004\\nvzhu004_fenli.mesh",
    },
}


def mesh_bone_name(name: str) -> str:
    return name.strip().replace(" ", "_").lower()


def animation_names(paths, sex: str) -> list[str]:
    """Clip names (file stems) of a gender's animations, from a list of game paths."""
    folder = RIGS[sex]["folder"].lower() + "\\"
    return sorted(
        {
            p.rsplit("\\", 1)[-1][: -len(".rawanimation")]
            for p in paths
            if p.lower().startswith(folder) and p.lower().endswith(".rawanimation")
        }
    )


def load_skeleton(read, sex: str) -> neox_anim.Skeleton:
    data = read(RIGS[sex]["skeleton"])
    if data is None:
        raise FileNotFoundError(RIGS[sex]["skeleton"])
    return neox_anim.Skeleton(data)


def load_clip(read, sex: str, name: str, skeleton) -> neox_anim.Clip:
    """A clip with its bones renamed to the meshes' spelling."""
    data = read(f"{RIGS[sex]['folder']}\\{name}.rawanimation")
    if data is None:
        raise FileNotFoundError(name)
    clip = neox_anim.Clip(name, neox_anim.Animation(data), skeleton)
    clip.bone_names = [mesh_bone_name(n) for n in skeleton.bone_names]
    return clip


def rest_worlds(skeleton) -> np.ndarray:
    """Column-vector model-space rest matrices of every skeleton bone."""
    local = np.tile(np.eye(4), (len(skeleton.bone_names), 1, 1))
    local[:, :3, :3] = neox_anim.quaternion_matrices(skeleton.rotations) * skeleton.scales[:, None, :]
    local[:, :3, 3] = skeleton.positions
    world = np.empty_like(local)
    for index, parent in enumerate(skeleton.parents):
        world[index] = (world[parent] if parent >= 0 else np.eye(4)) @ local[index]
    return world


def vmd_bone_names(clip) -> list[str]:
    names = list(clip.bone_names)
    if bone_names.enabled():
        names = bone_names.translate(names)
    return vmd_export.short_bone_names(names)


def write_vmd(clip, skeleton, target) -> tuple[Path, bool]:
    """<target>.vmd for a clip; returns (path, whether the clip also scales bones, which VMD cannot hold)."""
    rig = SimpleNamespace(
        bone_name=list(clip.bone_names),
        bone_parent=list(skeleton.parents),
        bone_matrix=[world.T for world in rest_worlds(skeleton)],
    )  # row-vector, like meshes
    from core.mesh_converter import mmd_rig

    names = vmd_bone_names(clip)
    rigged = bone_names.enabled() and mmd_rig.enabled()
    if rigged:  # bake against the rigged PMX's chains
        rig.bone_parent = mmd_rig.vmd_parents(names, list(rig.bone_parent))
    keys, _frames = vmd_export.vmd_keyframes(rig, clip)
    if rigged:  # the PMX's IK legs: key the FK leg bones, IK off
        names = [mmd_rig.vmd_name(n) for n in names]
    path = vmd_export.write_vmd(target, Path(target).stem[:20], names, keys, ik_off=mmd_rig.IK_BONES if rigged else ())
    scaled = bool(np.abs(clip.scales - skeleton.scales[None, : len(clip.bone_names)]).max() > 1e-3)
    return path, scaled


def write_glb(read_model, clip, sex: str, target, with_character: bool) -> Path:
    """<target>.glb: the clip on the full armature (tiny placeholder mesh) or on the default character."""
    model = read_model(RIGS[sex]["character" if with_character else "armature"])
    if model is None:
        raise FileNotFoundError(RIGS[sex]["character" if with_character else "armature"])
    mesh, parts = model
    return model_export.write_glb([{"name": clip.name, "mesh": mesh, "parts": parts, "animation": clip}], target)


def safe_name(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\s]+', "_", name).strip("._") or "animation"
