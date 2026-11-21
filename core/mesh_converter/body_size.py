"""
Character body size: the game's "size 0" (as modelled) and "size 1" (the adult body, body_dress 240002 = plan1).

The adult body is a skeleton deform. Every outfit has <outfit>.sdm next to its .gim (JSON):
    {"plan_map": {"plan1": {"symmetry": true, "toe_ik": true, "root_height": 10.086,
        "skeleton_list": [["bip001 l thigh", 1.04, 1, 1, false], ["bip001 head", 0.92, 0.92, 0.92, true], ...]}}}
Each entry scales a bone in its own axes (x runs along the bone, so x lengthens a limb and moves the
bones below it); the last flag says whether the scale also applies to the bone's children (the head's
scale shrinks the face and hair with it). "symmetry" mirrors left entries to the right.

The character skeleton the meshes use is flat (every leg bone hangs off "lower", every arm bone off
"upper"), so the game deforms through a proper biped hierarchy instead, model/character/skeleton_deformer/
<sex>.skeleton (comm/body_type/config.json, hierarchy_skeleton_rules), and carries the result over by bone
name. toe_ik keeps the feet on the ground; root_height is the resulting hip height.
"""

from __future__ import annotations

import copy
import json

import numpy as np

ADULT_PLAN = "plan1"
DEFAULT_OUTFITS = {
    "male": "model\\character\\appearance\\boy\\high\\clothes\\male_clothes_01_chuangjue\\male_clothes_01_chuangjue.sdm",
    "female": "model\\character\\appearance\\girl\\high\\clothes\\female_clothes_01_chuangjue\\female_clothes_01_chuangjue.sdm",
}
DEFORMER_SKELETONS = {"male": "model\\character\\skeleton_deformer\\male.skeleton",
                      "female": "model\\character\\skeleton_deformer\\female.skeleton"}
GROUND_BONES = ("bip001_l_toe0", "bip001_r_toe0", "bip001_l_foot", "bip001_r_foot")
HIP_BONE = "bip001_pelvis"


def bone_key(name: str) -> str:
    return name.strip().replace(" ", "_").lower()


def sex_of(path: str) -> str | None:
    lower = path.lower()
    name = lower.rsplit("\\", 1)[-1]
    if "\\girl\\" in lower or name.startswith("female"):
        return "female"
    if "\\boy\\" in lower or name.startswith("male"):
        return "male"
    return None


def read_plan(read, sdm_path: str, plan: str = ADULT_PLAN) -> dict | None:
    data = read(sdm_path)
    if not data:
        return None
    try:
        plans = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data).get("plan_map", {})
    except ValueError:
        return None
    return plans.get(plan)


def plan_for(read, mesh_paths, report: list) -> tuple[dict | None, str | None, str]:
    """(plan, sex, where it came from): the first outfit's own adult plan, else the starter outfit's."""
    sexes = [s for s in (sex_of(p) for p in mesh_paths) if s]
    sex = sexes[0] if sexes else None
    for path in mesh_paths:
        if "\\clothes\\" in path.lower():
            plan = read_plan(read, path.rsplit(".", 1)[0] + ".sdm")
            if plan:
                return plan, sex_of(path) or sex, path.rsplit("\\", 1)[-1].rsplit(".", 1)[0]
    if sex is None:
        return None, None, ""
    plan = read_plan(read, DEFAULT_OUTFITS[sex])
    if plan:
        report.append("Adult size: no outfit with an adult plan in this export, so the starter outfit's "
                      "adult proportions are used.")
    return plan, sex, DEFAULT_OUTFITS[sex].rsplit("\\", 1)[-1][:-4]


class BodyDeformer:
    """One plan's deform, solved on the deformer skeleton; apply() carries it to any mesh of the character."""

    def __init__(self, plan: dict, deformer_skeleton):
        from core.mesh_converter.character_anim import rest_worlds  # pylint: disable=import-outside-toplevel
        self.plan = plan
        entries: dict[str, tuple[np.ndarray, bool]] = {}
        for name, sx, sy, sz, carry in plan.get("skeleton_list", []):
            key = bone_key(name)
            entries[key] = (np.array([sx, sy, sz], float), bool(carry))
            if plan.get("symmetry", True) and "_l_" in key:
                entries.setdefault(key.replace("_l_", "_r_"), (np.array([sx, sy, sz], float), bool(carry)))
        self.entries = entries
        worlds = rest_worlds(deformer_skeleton)
        names = [bone_key(n) for n in deformer_skeleton.bone_names]
        binds = {name: worlds[i].T.copy() for i, name in enumerate(names)}          # row-vector
        parents = {name: (names[p] if p >= 0 else None) for name, p in zip(names, deformer_skeleton.parents)}
        effective, carried, positions = {}, {}, {}
        for index, name in enumerate(names):            # skeleton files list parents before children
            parent = parents[name]
            own, carry = entries.get(name, (np.ones(3), False))
            inherited = carried.get(parent, np.ones(3))
            effective[name] = own * inherited
            carried[name] = inherited * own if carry else inherited
            if parent is None:
                positions[name] = binds[name][3, :3].copy()
                continue
            parent_bind = binds[parent]
            offset = (binds[name][3, :3] - parent_bind[3, :3]) @ np.linalg.inv(parent_bind[:3, :3])
            positions[name] = positions[parent] + (offset * effective[parent]) @ parent_bind[:3, :3]
        ground = [b for b in GROUND_BONES if b in positions]
        shift = (min(binds[b][3, 1] for b in ground) - min(positions[b][1] for b in ground)) if ground else 0.0
        self.shift = shift
        self.hip_height = (positions[HIP_BONE][1] + shift) if HIP_BONE in positions else None
        # World-space map per deformer bone: a rest-pose point bound to it -> its adult position
        self.maps: dict[str, np.ndarray] = {}
        for name in names:
            new_bind = binds[name].copy()
            new_bind[3, :3] = positions[name] + [0.0, shift, 0.0]
            stretch = np.diag(list(effective[name]) + [1.0])
            self.maps[name] = np.linalg.inv(binds[name]) @ stretch @ new_bind

    def bone_map(self, mesh, index: int, cache: dict) -> np.ndarray:
        """The map for one of a mesh's bones: its own, else its nearest deformed ancestor's."""
        if index in cache:
            return cache[index]
        name = bone_key(mesh.bone_name[index])
        result = self.maps.get(name)
        if result is None:
            parent = mesh.bone_parent[index] if index < len(mesh.bone_parent) else -1
            result = self.bone_map(mesh, parent, cache) if 0 <= parent < len(mesh.bone_name) and parent != index else np.eye(4)
        cache[index] = result
        return result

    def apply(self, mesh):
        """A copy of `mesh` in the adult body (vertices, normals and bind pose); the original is untouched."""
        if not mesh.bone_name or not mesh.bone_matrix or not mesh.vertex_bone:
            return mesh
        count = len(mesh.bone_name)
        cache: dict[int, np.ndarray] = {}
        skins = np.tile(np.eye(4), (count + 1, 1, 1))
        binds = []
        for index in range(count):
            mapping = self.bone_map(mesh, index, cache)
            skins[index] = mapping
            bind = np.asarray(mesh.bone_matrix[index], float) if index < len(mesh.bone_matrix) else np.eye(4)
            moved = bind @ mapping
            new_bind = bind.copy()
            new_bind[3, :3] = moved[3, :3]                       # bones move; their axes stay
            binds.append(new_bind.astype(np.float32))
        vertex_count = len(mesh.position)
        joints = np.full((vertex_count, 4), count, np.int64)
        weights = np.zeros((vertex_count, 4))
        for row, (b, w) in enumerate(zip(mesh.vertex_bone, mesh.vertex_weight)):
            b, w = list(b)[:4], list(w)[:4]
            joints[row, :len(b)] = b
            weights[row, :len(w)] = w
        joints = np.where((joints < 0) | (joints >= count), count, joints)
        blend = np.einsum("vk,vkij->vij", weights, skins[joints])
        total = weights.sum(1)
        blend[total <= 0] = np.eye(4)
        blend[total > 0] /= total[total > 0, None, None]
        positions = np.c_[np.asarray(mesh.position, float), np.ones(vertex_count)]
        shaped = copy.copy(mesh)
        shaped.position = [tuple(p) for p in np.einsum("vi,vij->vj", positions, blend)[:, :3]]
        if mesh.normal and len(mesh.normal) == vertex_count:
            normals = np.einsum("vi,vij->vj", np.asarray(mesh.normal, float),
                                np.linalg.inv(blend[:, :3, :3]).transpose(0, 2, 1))
            normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-9)
            shaped.normal = [tuple(n) for n in normals]
        shaped.bone_matrix = binds
        return shaped


def deformer_for(read, mesh_paths, report: list):
    """(BodyDeformer, source name) for a character made of these meshes, or (None, "")."""
    from core.npk import neox_anim  # pylint: disable=import-outside-toplevel
    plan, sex, source = plan_for(read, mesh_paths, report)
    if plan is None or sex is None:
        return None, ""
    data = read(DEFORMER_SKELETONS[sex])
    if not data:
        report.append("Adult size: the game's deformer skeleton is missing.")
        return None, ""
    return BodyDeformer(plan, neox_anim.Skeleton(data)), source


def adult_items(read, items, mesh_paths, report: list) -> list[dict]:
    """
    The export items with their meshes in the adult body: one deform for the whole character, from the
    outfit among mesh_paths (the game paths of the parts). Unchanged when no plan applies.
    """
    deformer, source = deformer_for(read, [path for path in mesh_paths if path], report)
    if deformer is None:
        report.append("Adult size: no adult body plan found for these parts; exported at their original size.")
        return list(items)
    note = f", hips at {deformer.hip_height:.2f}" if deformer.hip_height is not None else ""
    report.append(f"Body size II (grown-up) from {source}: {len(deformer.entries)} bone scales{note}.")
    return [dict(item, mesh=deformer.apply(item["mesh"])) for item in items]
