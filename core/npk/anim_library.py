"""
Every animation in the game, grouped by the model it belongs to.

Animations live in <model folder>\\animations_<skeleton>\\<clip>.animation (+ .rawanimation, the data)
next to <model folder>\\<skeleton>.skeleton and the model's .gim files. The two player-character rigs
(model\\character\\male|female) are paired with the default character models instead of their 4-vertex
armature meshes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

B = "\\"
CHARACTER_MODELS = {
    "model\\character\\male": "model\\character\\male\\nanzhu001\\nanzhu001_fenli.gim",
    "model\\character\\female": "model\\character\\female\\nvzhu004\\nvzhu004_fenli.gim",
}
CATEGORY_NAMES = {
    "character": "Characters",
    "monster": "Creatures",
    "canon": "Story characters",
    "partner": "Partners",
    "items": "Items",
    "furniture": "Furniture",
    "levelsets": "Scenes",
    "original": "Original characters",
    "camera": "Cameras",
    "fx": "Effects",
    "levelsets_model": "Scene props",
    "sushe_jiaju": "Dorm furniture",
}


@dataclass
class Group:
    folder: str  # ...\animations_<skeleton>
    parent: str  # the model folder
    skeleton: str  # <parent>\<skeleton>.skeleton (may be missing)
    category: str
    clips: list[str] = field(default_factory=list)
    models: list[str] = field(default_factory=list)  # .gim files of the model folder
    default_model: str | None = None

    @property
    def label(self) -> str:
        if self.parent.lower() in CHARACTER_MODELS:
            return f"Player character ({self.parent.rsplit(B, 1)[-1]})"
        stem = self.folder.rsplit(B, 1)[-1][len("animations_") :]
        owner = self.parent.rsplit(B, 1)[-1]
        short = re.sub(r"_[0-9a-f]{6}$", "", stem)
        return owner if short.lower() == owner.lower() else f"{owner} / {short}"

    @property
    def is_player(self) -> bool:
        return self.parent.lower() in CHARACTER_MODELS


def category_of(parent: str) -> str:
    parts = parent.lower().split(B)
    if parts[:3] == ["model", "character", "appearance"]:
        return "Outfit & accessory animations"
    if parts[:2] == ["model", "character"]:
        return "Player character"
    for part in parts[:3]:
        if part in CATEGORY_NAMES:
            return CATEGORY_NAMES[part]
    return (
        parts[1].capitalize()
        if len(parts) > 1 and parts[0] in ("model", "res_dlc1_hogsmeade")
        else parts[0].capitalize()
    )


def scan(paths) -> list[Group]:
    """Groups for every animations_ folder in a list of game paths."""
    gims: dict[str, list[str]] = {}
    skeletons = set()
    groups: dict[str, Group] = {}
    clips: dict[str, set] = {}
    for path in paths:
        lower = path.lower()
        if lower.endswith(".gim"):
            gims.setdefault(lower.rsplit(B, 1)[0], []).append(path)
        elif lower.endswith(".skeleton"):
            skeletons.add(lower)
        elif lower.endswith((".animation", ".rawanimation")):
            folder, name = path.rsplit(B, 1)
            if not folder.rsplit(B, 1)[-1].lower().startswith("animations_") or B not in folder:
                continue
            key = folder.lower()
            if key not in groups:
                parent = folder.rsplit(B, 1)[0]
                stem = folder.rsplit(B, 1)[-1][len("animations_") :]
                groups[key] = Group(folder, parent, f"{parent}{B}{stem}.skeleton", category_of(parent))
                clips[key] = set()
            clips[key].add(name.rsplit(".", 1)[0])
    result = []
    for key, group in groups.items():
        group.clips = sorted(clips[key], key=str.lower)
        group.models = sorted(gims.get(group.parent.lower(), []), key=str.lower)
        group.default_model = pick_model(group)
        result.append(group)
    result.sort(key=lambda g: (g.category != "Player character", g.category, g.label.lower()))
    return result


def pick_model(group: Group) -> str | None:
    if group.parent.lower() in CHARACTER_MODELS:
        return CHARACTER_MODELS[group.parent.lower()]
    stem = group.folder.rsplit(B, 1)[-1][len("animations_") :].lower()
    short = re.sub(r"_[0-9a-f]{6}$", "", stem)
    by_name = {m.rsplit(B, 1)[-1][:-4].lower(): m for m in group.models}
    for candidate in (stem, short):
        if candidate in by_name:
            return by_name[candidate]
    for name, model in by_name.items():
        if name.startswith(short) or short.startswith(name):
            return model
    owner = group.parent.rsplit(B, 1)[-1].lower()
    return by_name.get(owner) or (group.models[0] if group.models else None)


def bone_key(name: str) -> str:
    return name.strip().replace(" ", "_").lower()


def read_animation_data(read, folder: str, name: str) -> tuple[str, bytes]:
    """
    (path, bytes) of a clip's .rawanimation: next to its .animation, or wherever the .animation's dependency
    points (some clips share data stored elsewhere). Raises FileNotFoundError when neither is there.
    """
    import xml.etree.ElementTree as ET
    from core.npk import neox_anim

    raw_path = f"{folder}{B}{name}.rawanimation"
    data = read(raw_path)
    if data is None:
        descriptor = read(f"{folder}{B}{name}.animation")
        if descriptor is not None:
            try:
                dependency = ET.fromstring(descriptor).find(".//Dependency/FileName")
                if dependency is not None and dependency.get("Value"):
                    raw_path = neox_anim._join(folder, dependency.get("Value"))
                    data = read(raw_path)
            except ET.ParseError:
                data = None
    if data is None:
        raise FileNotFoundError(f"{name}: animation data not found")
    return raw_path, data


def load_clip(read, group: Group, name: str):
    """
    A neox_anim.Clip for one clip of a group; read(path) returns a file's bytes or None. Both layouts are read (ACL
    and plain keyframes); a clip in neither raises ValueError("Unsupported animation ...").
    """
    from core.npk import neox_anim

    raw_path, data = read_animation_data(read, group.folder, name)
    animation = neox_anim.Animation(data)
    skeleton_data = None
    if animation.skeleton_path:
        skeleton_data = read(neox_anim._join(raw_path.rsplit(B, 1)[0], animation.skeleton_path))
    skeleton_data = skeleton_data or read(group.skeleton)
    if skeleton_data is None:
        raise FileNotFoundError(f"{name}: skeleton not found")
    return neox_anim.Clip(name, animation, neox_anim.Skeleton(skeleton_data))


def match_bones(clip, mesh):
    """Name the clip's bones like the mesh does (the skeleton writes 'bip001 head', meshes 'bip001_head')."""
    by_key = {bone_key(n): n for n in mesh.bone_name}
    clip.bone_names = [by_key.get(bone_key(n), n) for n in clip.skeleton.bone_names]
    return sum(bone_key(n) in by_key for n in clip.skeleton.bone_names)
