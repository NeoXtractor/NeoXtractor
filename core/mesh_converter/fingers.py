"""Five-finger hands for PMX export.

The game skins the ring and little fingers to the middle finger's bones. This splits those vertices into three
fingers, gives the ring and little fingers their own bones (薬指, 小指) and adds the third finger joint MMD
motions expect. Hands that don't split cleanly (mittens, claws) are left as they are.
"""

from __future__ import annotations

import copy

import numpy as np

SETTING = "pmx_export.five_fingers"
LABEL = "Five fingers (rig the ring and little fingers)"


def enabled(settings=None) -> bool:
    if settings is None:
        try:
            from PySide6 import QtWidgets

            app = QtWidgets.QApplication.instance()
            settings = app.property("settings_manager") if app is not None else None
        except Exception:
            settings = None
    try:
        return bool(settings.get(SETTING, True)) if settings is not None else True
    except Exception:
        return True


def _unit(v):
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-9 else v


class _Rig:
    """Mutable copy of the mesh's bones and weights."""

    def __init__(self, mesh):
        self.names = list(mesh.bone_name)
        self.parents = list(mesh.bone_parent)
        self.matrices = [np.asarray(m, float).copy() for m in mesh.bone_matrix]
        self.index = {n.lower(): i for i, n in enumerate(self.names)}
        self.positions = np.asarray(mesh.position, float)
        self.joints = [list(b) for b in mesh.vertex_bone]
        self.weights = [list(map(float, w)) for w in mesh.vertex_weight]
        self.added = 0

    def find(self, name):
        return self.index.get(name.lower())

    def pos(self, bone):
        return self.matrices[bone][3, :3].copy()

    def add_bone(self, name, parent, like, position):
        matrix = self.matrices[like].copy()
        matrix[3, :3] = position
        self.names.append(name)
        self.parents.append(parent)
        self.matrices.append(matrix)
        self.index[name.lower()] = len(self.names) - 1
        self.added += 1
        return len(self.names) - 1

    def weight_on(self, vertex, bone):
        return sum(w for b, w in zip(self.joints[vertex], self.weights[vertex]) if b == bone)

    def share(self, vertex, source, targets):
        """Hand fractions of the vertex's weight on `source` to other bones: targets = ((bone, fraction), ...)."""
        total = sum(f for _b, f in targets)
        if total <= 1e-6:
            return
        weight = self.weight_on(vertex, source)
        if weight <= 0:
            return
        joints = [b for b in self.joints[vertex]]
        weights = list(self.weights[vertex])
        for k, b in enumerate(joints):
            if b == source:
                weights[k] *= max(0.0, 1.0 - total)
        for bone, fraction in targets:
            if fraction > 0:
                joints.append(bone)
                weights.append(weight * fraction)
        self._store(vertex, joints, weights)

    def _store(self, vertex, joints, weights):
        merged: dict = {}
        for b, w in zip(joints, weights):
            merged[b] = merged.get(b, 0.0) + w
        pairs = sorted(((w, b) for b, w in merged.items() if w > 1e-6), reverse=True)[:4]
        total = sum(w for w, _b in pairs) or 1.0
        self.joints[vertex] = [b for _w, b in pairs]
        self.weights[vertex] = [w / total for w, _b in pairs]

    def move(self, vertex, source, target, fraction):
        """Move `fraction` of the vertex's weight on `source` to `target` (keeps at most 4 influences)."""
        if fraction <= 0:
            return
        joints, weights = self.joints[vertex], self.weights[vertex]
        moved = 0.0
        for k, (b, w) in enumerate(zip(joints, weights)):
            if b == source and w > 0:
                moved += w * fraction
                weights[k] = w * (1.0 - fraction)
        if moved <= 0:
            return
        for k, b in enumerate(joints):
            if b == target:
                weights[k] += moved
                break
        else:
            joints.append(target)
            weights.append(moved)
        pairs = sorted(((w, b) for b, w in zip(joints, weights) if w > 1e-6), reverse=True)[:4]
        total = sum(w for w, _b in pairs) or 1.0
        self.joints[vertex] = [b for _w, b in pairs]
        self.weights[vertex] = [w / total for w, _b in pairs]


def _kmeans_1d(values, centres, rounds=25):
    centres = np.array(centres, float)
    for _ in range(rounds):
        labels = np.argmin(np.abs(values[:, None] - centres[None, :]), axis=1)
        updated = np.array(
            [values[labels == k].mean() if np.any(labels == k) else centres[k] for k in range(len(centres))]
        )
        if np.allclose(updated, centres):
            break
        centres = updated
    labels = np.argmin(np.abs(values[:, None] - centres[None, :]), axis=1)
    return centres, labels


def _smooth(x):
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def _split_hand(rig: _Rig, side: str, report) -> bool:
    prefix = f"bip001_{side}_finger"
    index1, middle1, middle2 = rig.find(prefix + "1"), rig.find(prefix + "2"), rig.find(prefix + "21")
    if None in (index1, middle1, middle2) or rig.find(prefix + "3") is not None:
        return False
    on_middle = [v for v in range(len(rig.joints)) if rig.weight_on(v, middle1) + rig.weight_on(v, middle2) > 0.0]
    if len(on_middle) < 30:
        return False
    base, tip_bone = rig.pos(middle1), rig.pos(middle2)
    along = _unit(tip_bone - base)  # down the finger
    across = rig.pos(index1) - base
    across = _unit(across - along * float(across @ along))  # towards the index finger
    spacing = abs(float((rig.pos(index1) - base) @ across)) or 0.1
    points = rig.positions[on_middle]
    t = (points - base) @ across
    s = (points - base) @ along
    dominant = np.array([rig.weight_on(v, middle1) + rig.weight_on(v, middle2) for v in on_middle]) >= 0.5
    # past the knuckle the three fingers stand apart; nearer the palm they merge
    reach = float(s[dominant].max()) if np.any(dominant) else float(s.max())
    apart = dominant & (s > 0.25 * reach)
    if int(np.sum(apart)) < 30:
        apart = dominant
    sample = t[apart]
    centres, _labels = _kmeans_1d(sample, np.percentile(sample, [85, 50, 15]))
    order = np.argsort(-centres)  # middle (nearest the index), ring, little
    centres = centres[order]
    counts = [int(np.sum(np.argmin(np.abs(sample[:, None] - centres[None, :]), axis=1) == k)) for k in range(3)]
    gaps = -np.diff(centres)
    if min(counts) < 8 or gaps.min() < 0.35 * spacing:
        report.append(f"{side} hand: the ring and little fingers could not be told apart; kept as rigged")
        return False
    bound_ring, bound_little = (centres[0] + centres[1]) / 2, (centres[1] + centres[2]) / 2
    band = 0.18 * float(gaps.min())
    tips = []
    for k in range(3):
        mask = np.abs(t - centres[k]) < 0.5 * float(gaps.min())
        tips.append(float(s[mask].max()) if np.any(mask) else float(s.max()))
    middle_tip = tips[0] or 1e-6
    joint_s = float((tip_bone - base) @ along)
    gap = float(gaps.min())

    def centre(k, at, fallback):
        """The middle of finger k's cross-section at `at` along the finger (its own vertices there)."""
        for width in (0.06, 0.1, 0.16):
            mask = dominant & (np.abs(t - centres[k]) < 0.45 * gap) & (np.abs(s - at) < width * reach)
            if int(np.sum(mask)) >= 4:
                found = points[mask].mean(axis=0)
                # keep the joint's height along the finger; only its place across and in depth comes from the mesh
                return found + along * float(at - (found - base) @ along)
        return fallback

    bones = {}
    # Each finger's joints in the middle of its own vertices: the game's middle-finger bone sits at the centre of
    # the three-finger bundle, so the middle finger moves over to its own finger as well
    for k, number in ((1, "3"), (2, "4")):
        shift = across * float(centres[k])
        shorter = tips[k] - middle_tip  # < 0: a shorter finger, its joints sit a little higher
        knuckle = centre(k, 0.25 * shorter, base + shift + along * (0.25 * shorter))
        joint = centre(k, joint_s + 0.6 * shorter, tip_bone + shift + along * (0.6 * shorter))
        first = rig.add_bone(prefix + number, rig.parents[middle1], middle1, knuckle)
        second = rig.add_bone(prefix + number + "1", first, middle2, joint)
        bones[k] = (first, second)
    rig.matrices[middle1][3, :3] = centre(0, 0.0, base + across * float(centres[0]))
    rig.matrices[middle2][3, :3] = centre(0, joint_s, tip_bone + across * float(centres[0]))
    for v, tv, sv in zip(on_middle, t, s):
        # How much this vertex belongs to each of the last three fingers. Near the palm they blend; and further each
        # vertex belongs to one finger only.
        width = band if sv < 0.25 * reach else 0.02 * gap
        past_ring = _smooth((bound_ring - tv) / (2 * width) + 0.5)
        little = min(past_ring, _smooth((bound_little - tv) / (2 * width) + 0.5))
        ring = past_ring - little
        rig.share(v, middle1, ((bones[1][0], ring), (bones[2][0], little)))
        rig.share(v, middle2, ((bones[1][1], ring), (bones[2][1], little)))
    report.append(f"{side} hand: ring and little fingers rigged from the middle finger's weights")
    return True


def _split_tip(rig: _Rig, side: str, base_name: str, end_name: str, new_name: str) -> bool:
    """Split a finger's end segment (end_name) at its middle into end_name + new_name."""
    prefix = f"bip001_{side}_finger"
    base, end = rig.find(prefix + base_name), rig.find(prefix + end_name)
    if base is None or end is None or rig.find(prefix + new_name) is not None:
        return False
    on_end = [v for v in range(len(rig.joints)) if rig.weight_on(v, end) > 0.0]
    if len(on_end) < 6:
        return False
    start = rig.pos(end)
    along = _unit(start - rig.pos(base))
    s = (rig.positions[on_end] - start) @ along
    dominant = np.array([rig.weight_on(v, end) for v in on_end]) >= 0.5
    length = float(s[dominant].max()) if np.any(dominant) else float(s.max())
    if length <= 1e-4:
        return False
    split = 0.5 * length
    place = start + along * split
    near = np.array(dominant) & (np.abs(s - split) < 0.15 * length)
    if int(np.sum(near)) >= 4:  # in the middle of the finger there, not on its surface
        found = rig.positions[on_end][near].mean(axis=0)
        place = found + along * float(split - (found - start) @ along)
    new = rig.add_bone(prefix + new_name, end, end, place)
    for v, sv in zip(on_end, s):
        rig.move(v, end, new, _smooth((sv - (split - 0.15 * length)) / (0.3 * length)))
    return True


def five_fingers(mesh, report=None):
    """A copy of `mesh` with five rigged fingers per hand (or `mesh` itself when nothing changes)."""
    report = report if report is not None else []
    if not getattr(mesh, "bone_name", None) or not getattr(mesh, "vertex_bone", None) or not mesh.bone_matrix:
        return mesh
    rig = _Rig(mesh)
    for side in ("l", "r"):
        _split_hand(rig, side, report)
        for base_name, end_name, new_name in (
            ("0", "01", "02"),
            ("1", "11", "12"),
            ("2", "21", "22"),
            ("3", "31", "32"),
            ("4", "41", "42"),
        ):
            _split_tip(rig, side, base_name, end_name, new_name)
    if not rig.added:
        return mesh
    shaped = copy.copy(mesh)
    shaped.bone_name = rig.names
    shaped.bone_parent = rig.parents
    shaped.bone_matrix = [m.astype(np.float32) for m in rig.matrices]
    shaped.vertex_bone = rig.joints
    shaped.vertex_weight = rig.weights
    sources = getattr(mesh, "bone_source_names", None)
    if sources:
        shaped.bone_source_names = list(sources) + rig.names[len(sources) :]
    return shaped


def copy_finger_keys(names: list[str], keys):
    """
    VMD (names, keys): the ring and little fingers move with the middle finger (the game animates only that
    one), keyed under 薬指１/２ and 小指１/２ (names added when the animated skeleton has no such bones).
    """
    if not enabled():
        return names, keys
    names = list(names)
    position = {name: i for i, name in enumerate(names)}
    extra = []
    for side in ("左", "右"):
        for number in ("１", "２"):
            source = position.get(f"{side}中指{number}")
            if source is None:
                continue
            targets = []
            for finger in ("薬指", "小指"):
                name = f"{side}{finger}{number}"
                if name not in position:
                    position[name] = len(names)
                    names.append(name)
                targets.append(position[name])
            source_keys = [key for key in keys if key[0] == source]
            if any(key[0] in targets for key in keys):
                continue  # the motion already moves them
            for _bone, frame, offset, rotation in source_keys:
                extra.extend((target, frame, offset, rotation) for target in targets)
    return names, list(keys) + extra
