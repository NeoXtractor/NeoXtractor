"""Face shape variants, from the game's <face>_NN.mb files.

Each .mb holds new bind matrices for about 80 face bones (uint16 count, then count x [uint16 bone index,
4x4 float32 matrix]). Baking moves every vertex from the old bind to the new one, so the reshaped face still
poses and animates.
"""

from __future__ import annotations

import copy
import struct

import numpy as np

SKIP_PREFIXES = ("bip001_prop",)  # weapon/prop sockets: not face bones


def read_mb(data: bytes) -> dict[int, np.ndarray]:
    count = struct.unpack_from("<H", data, 0)[0]
    if len(data) < 2 + count * 66:
        raise ValueError("not a face shape file")
    shape = {}
    for number in range(count):
        offset = 2 + number * 66
        index = struct.unpack_from("<H", data, offset)[0]
        shape[index] = np.array(struct.unpack_from("<16f", data, offset + 2), float).reshape(4, 4)
    return shape


MATCH_LIMIT = 0.15  # median bone movement above which the indices clearly point at the wrong bones
SEARCH = 12  # how far a shape's bone numbers may have drifted from the mesh's (bones added later)


def _pose_difference(a: np.ndarray, b: np.ndarray) -> float:
    ra = a[:3, :3] / np.maximum(np.linalg.norm(a[:3, :3], axis=1, keepdims=True), 1e-9)
    rb = b[:3, :3] / np.maximum(np.linalg.norm(b[:3, :3], axis=1, keepdims=True), 1e-9)
    return float(np.linalg.norm(a[3, :3] - b[3, :3]) + 0.5 * np.abs(ra - rb).max())


def shape_by_name(shape: dict[int, np.ndarray], reference_mesh) -> dict[str, np.ndarray]:
    """
    Bone name -> new bind matrix. The indices are the face mesh's bone numbers when the shape was made;
    a few faces gained bones since (male_body_20 has 4 more, so its later numbers are off by 4), so when
    the numbers don't fit, each entry goes to the nearby-numbered bone whose pose it matches.
    """
    names = reference_mesh.bone_name
    binds = [np.asarray(m, float) for m in reference_mesh.bone_matrix]
    if not shape:
        raise ValueError("empty face shape")
    direct = [_pose_difference(m, binds[i]) for i, m in shape.items() if i < len(binds)]
    if max(shape) < len(names) and direct and float(np.median(direct)) < MATCH_LIMIT:
        mapping = {i: i for i in shape}
    else:
        mapping = {}
        for i, matrix in shape.items():
            candidates = range(max(0, i - SEARCH), min(len(binds), i + SEARCH + 1))
            best = min(candidates, key=lambda j: _pose_difference(matrix, binds[j]), default=None)
            if best is not None and _pose_difference(matrix, binds[best]) < 0.5:
                mapping[i] = best
        moved = [_pose_difference(shape[i], binds[j]) for i, j in mapping.items()]
        if not moved or float(np.median(moved)) >= MATCH_LIMIT:
            raise ValueError("the shape does not match this face's bones")
    result = {}
    for i, j in mapping.items():
        if names[j].lower().startswith(SKIP_PREFIXES):
            continue
        target = _as_model_space(shape[i], binds[j])
        if target is not None:
            # The game binds the face mesh in the .mb pose and poses the bones at their rest, so the skin moves
            # by inv(mb) @ rest. As the bone target apply_shape uses (skin = inv(rest) @ target): rest @ inv(mb) @
            # rest. Checked against the game's own preset portraits (image/face/faces/<face>_<shape>_192.png).
            result[names[j]] = binds[j] @ np.linalg.inv(target) @ binds[j]
    return result


def _as_model_space(matrix: np.ndarray, bind: np.ndarray):
    """
    Most entries are the bone's new pose in model space. Some are written as a change in the bone's own
    space instead (a small move / scale, far from where the bone is: "upper" as the identity, the mouth
    bones as "0.035 down, 0.72 wide"); those apply on top of the bind pose. Entries with absurd scales are
    left out.
    """
    scales = np.linalg.norm(matrix[:3, :3], axis=1)
    if scales.max() > 4.0 or scales.min() < 0.2:
        return None
    if np.linalg.norm(matrix[3, :3] - bind[3, :3]) <= 1.0:
        return matrix
    if np.linalg.norm(matrix[3, :3]) < 1.0:
        if np.allclose(matrix, np.eye(4), atol=1e-4):
            return None  # "no change"
        return matrix @ bind
    return None


def reference_mesh(meshes):
    """The mesh the .mb bone indices refer to: the one with the most bones (the face)."""
    meshes = [m for m in meshes if m is not None and m.bone_name and m.bone_matrix]
    return max(meshes, key=lambda m: len(m.bone_name)) if meshes else None


def mirror_normal_fix(mesh):
    """
    Returns a copy of a face mesh whose broken normals are taken from the other half. Faces are symmetric, so a
    normal that disagrees with its mirror twin's (mirrored), while the twin's fits the surface far better, is a
    flaw in the game's data: female_body_04 has a few on one side of the nose bridge, which shade it dark.
    """
    if not mesh.normal or len(mesh.normal) != len(mesh.position) or not mesh.face:
        return mesh
    positions = np.asarray(mesh.position, float)
    normals = np.asarray(mesh.normal, float)
    normals = normals / np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-9)
    faces = np.asarray(mesh.face, int).reshape(-1, 3)
    keys = np.round(positions, 3)
    welded = {}
    weld = np.array([welded.setdefault(tuple(k), len(welded)) for k in keys])
    surface = np.zeros((len(welded), 3))
    cross = np.cross(positions[faces[:, 1]] - positions[faces[:, 0]], positions[faces[:, 2]] - positions[faces[:, 0]])
    for corner in range(3):
        np.add.at(surface, weld[faces[:, corner]], cross)
    surface = surface[weld]
    surface /= np.maximum(np.linalg.norm(surface, axis=1, keepdims=True), 1e-9)
    groups: dict[tuple, list[int]] = {}
    for index, key in enumerate(map(tuple, keys)):
        groups.setdefault(key, []).append(index)
    # spots whose overlapping vertices all face the same way: only those serve as twins (at closed lips the
    # vertices of both lips share a spot and face opposite ways, on both halves alike)
    clean = {
        key: ids[0]
        for key, ids in groups.items()
        if all(np.dot(normals[ids[0]], normals[other]) > 0.87 for other in ids[1:])
    }
    fits = (normals * surface).sum(1)
    found: dict[int, int] = {}
    for index in range(len(positions)):
        if abs(positions[index, 0]) < 1e-3:
            continue
        twin = clean.get((-keys[index, 0] + 0.0, keys[index, 1], keys[index, 2]))
        if twin is None or fits[twin] < 0.87:  # the twin must fit its own surface
            continue
        mirrored = normals[twin] * (-1.0, 1.0, 1.0)
        off = fits[index] < 0.87 and np.dot(mirrored, normals[index]) < 0.87  # over 30 degrees out
        if off and np.dot(mirrored, surface[index]) > fits[index] + 0.1:
            found[index] = twin
    # a flaw is on one side only: where both halves would take the other's normal, the shape itself differs
    flawed = {
        index: twin for index, twin in found.items() if not any(other in found for other in groups[tuple(keys[twin])])
    }
    if not flawed:
        return mesh
    fixed = normals.copy()
    for index, twin in flawed.items():
        fixed[index] = normals[twin] * (-1.0, 1.0, 1.0)
    repaired = copy.copy(mesh)
    repaired.normal = [tuple(n) for n in fixed]
    return repaired


def apply_shape(mesh, bones: dict[str, np.ndarray]):
    """Returns a reshaped copy of the mesh; the original isn't changed."""
    if not mesh.bone_name or not mesh.bone_matrix or not mesh.vertex_bone:
        return mesh
    count = len(mesh.bone_name)
    skin = np.tile(np.eye(4), (count + 1, 1, 1))
    old_bind = [np.asarray(m, float) for m in mesh.bone_matrix]
    new_bind = list(old_bind)
    targets: dict[int, np.ndarray] = {}

    def target_of(index, depth=0):
        """The bone's new bind: its own from the shape, else carried along by its nearest moved ancestor
        (the game moves bones hierarchically: a face whose shape moves "upper" takes the head with it)."""
        if index in targets:
            return targets[index]
        found = bones.get(mesh.bone_name[index])
        if found is None and depth < 64:
            parent = mesh.bone_parent[index] if index < len(mesh.bone_parent) else -1
            if 0 <= parent < count and parent != index and parent < len(old_bind):
                parent_target = target_of(parent, depth + 1)
                if parent_target is not None:
                    found = old_bind[index] @ np.linalg.inv(old_bind[parent]) @ parent_target
        targets[index] = found
        return found

    changed = False
    for index in range(min(count, len(old_bind))):
        target = target_of(index)
        if target is None:
            continue
        skin[index] = np.linalg.inv(old_bind[index]) @ target
        new_bind[index] = target
        changed = True
    if not changed:
        return mesh
    vertex_count = len(mesh.position)
    joints = np.full((vertex_count, 4), count, np.int64)
    weights = np.zeros((vertex_count, 4))
    for row, (b, w) in enumerate(zip(mesh.vertex_bone, mesh.vertex_weight)):
        b, w = list(b)[:4], list(w)[:4]
        joints[row, : len(b)] = b
        weights[row, : len(w)] = w
    joints = np.where((joints < 0) | (joints >= count), count, joints)
    blend = np.einsum("vk,vkij->vij", weights, skin[joints])  # per-vertex 4×4
    total = weights.sum(1)
    blend[total <= 0] = np.eye(4)
    blend[total > 0] /= total[total > 0, None, None]
    positions = np.c_[np.asarray(mesh.position, float), np.ones(vertex_count)]
    moved = np.einsum("vi,vij->vj", positions, blend)[:, :3]
    shaped = copy.copy(mesh)
    shaped.position = [tuple(p) for p in moved]
    if mesh.normal and len(mesh.normal) == vertex_count:
        normals = np.einsum("vi,vij->vj", np.asarray(mesh.normal, float), blend[:, :3, :3])
        normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-9)
        shaped.normal = [tuple(n) for n in normals]
    shaped.bone_matrix = [m.astype(np.float32) for m in new_bind]
    # The bind the pieces were modelled in: a pose skins against it, so each bone's shape change rides along as a
    # local offset instead of being undone by the clip putting the bone back where the unshaped face has it
    shaped.skin_bind = skin_bind(mesh)
    return shaped


def skin_bind(mesh) -> list:
    """The bind a pose skins against: the unshaped one for a reshaped face, else the mesh's own."""
    return getattr(mesh, "skin_bind", None) or mesh.bone_matrix


EYE_BONES = ("e_lbone_000", "e_rbone_000")


def apply_shape_to_eyes(mesh, bones: dict[str, np.ndarray]):
    """
    Eye meshes (<sex>_eye_l / _eye_r / _eye_jyq) follow the shape rigidly: each eye moves and turns with
    its side's eye bone (e_lbone_000 / e_rbone_000, the bone the face's pupil sockets hang on), sized evenly
    instead of being stretched with the eyelid bones it is also weighted to.
    """
    if not mesh.bone_name or not mesh.bone_matrix or not mesh.position:
        return mesh
    names = list(mesh.bone_name)
    sides = []
    for bone in EYE_BONES:
        target = bones.get(bone)
        if target is None or bone not in names:
            continue
        bind = np.asarray(mesh.bone_matrix[names.index(bone)], float)
        # Moved and turned with the bone, scaled evenly by its smallest scale: the game's eye bone can be
        # stretched unevenly (female_body_04 shape 07: 1.46 x 1.46 x 1.24), which would push the eye through the lids
        scales = np.linalg.norm(target[:3, :3], axis=1, keepdims=True)
        keep = np.linalg.norm(bind[:3, :3], axis=1, keepdims=True)
        rigid = target.copy()
        rigid[:3, :3] = target[:3, :3] / np.maximum(scales, 1e-9) * keep * (float(scales.min()) / float(keep.min()))
        sides.append((bind[3, :3], np.linalg.inv(bind) @ rigid))
    if not sides:
        return apply_shape(mesh, bones)
    positions = np.c_[np.asarray(mesh.position, float), np.ones(len(mesh.position))]
    centres = np.array([centre for centre, _m in sides])
    nearest = np.argmin(np.linalg.norm(positions[:, None, :3] - centres[None], axis=2), axis=1)
    matrices = np.array([m for _c, m in sides])[nearest]
    shaped = copy.copy(mesh)
    shaped.position = [tuple(p) for p in np.einsum("vi,vij->vj", positions, matrices)[:, :3]]
    if mesh.normal and len(mesh.normal) == len(mesh.position):
        normals = np.einsum("vi,vij->vj", np.asarray(mesh.normal, float), matrices[:, :3, :3])
        normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-9)
        shaped.normal = [tuple(n) for n in normals]
    reshaped = apply_shape(mesh, bones)
    shaped.bone_matrix = reshaped.bone_matrix
    shaped.skin_bind = skin_bind(mesh)
    return shaped
