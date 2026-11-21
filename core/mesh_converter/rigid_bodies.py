"""Body rigid bodies (剛体) for PMX export, sized to the model.

Capsules follow the main bones, with radii measured from the vertices each bone moves, so hair and skirts
don't fall through the body in MMD physics.
"""

from __future__ import annotations

import math

import numpy as np

SETTING = "pmx_export.rigid_bodies"
LABEL = "Body rigid bodies (collision for hair and cloth physics)"
GROUP = 0                                    # PMX group index 0 = "1" in PMX Editor
_MASK_BITS = 0xFFFF & ~(1 << GROUP)          # collides with everything except its own group
MASK = _MASK_BITS - 0x10000 if _MASK_BITS > 0x7FFF else _MASK_BITS     # the writer stores it as a signed short


STANDARD_HEIGHT = 17.4                       # a male character, head to toe, in PMX units


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


def _rotation_to(direction) -> tuple[float, float, float]:
    """Euler angles (x, y, z; PMX applies them as Y·X·Z) turning the capsule's +Y axis onto `direction`."""
    d = np.asarray(direction, float)
    d = d / max(np.linalg.norm(d), 1e-9)
    if d[1] < 0:
        d = -d                                   # a capsule is symmetric: keep the angles small
    rz = math.asin(max(-1.0, min(1.0, -d[0])))
    rx = math.atan2(d[2], d[1])
    return rx, 0.0, rz


def _vertices(model):
    """Positions [n, 3] and per-vertex dominant bone index of the PMX model's vertices."""
    positions = np.array([(v.position.x, v.position.y, v.position.z) for v in model.vertices], float)
    main = np.full(len(model.vertices), -1, int)
    for k, vertex in enumerate(model.vertices):
        deform = vertex.deform
        pairs = []
        for slot in range(4):
            bone = getattr(deform, f"index{slot}", None)
            weight = getattr(deform, f"weight{slot}", 1.0 if slot == 0 else 0.0)
            if bone is not None and weight is not None:
                pairs.append((weight, bone))
        if hasattr(deform, "index0") and not hasattr(deform, "weight0"):
            pairs = [(1.0, deform.index0)]
        if pairs:
            main[k] = max(pairs)[1]
    return positions, main


def _mirror_pairs(bodies, common, middle=0.0) -> None:
    """
    Left and right bodies (左x / 右x) the same size, at mirror-image places and angles, mirrored across the
    body's middle line x = middle.
    """
    by_name = {body.name: body for body in bodies}
    for name, left in by_name.items():
        if not name.startswith("左"):
            continue
        right = by_name.get("右" + name[1:])
        if right is None:
            continue
        size = [(a + b) / 2 for a, b in zip((left.shape_size.x, left.shape_size.y, left.shape_size.z),
                                             (right.shape_size.x, right.shape_size.y, right.shape_size.z))]
        lp, rp = left.shape_position, right.shape_position
        x = (abs(lp.x - middle) + abs(rp.x - middle)) / 2
        y, z = (lp.y + rp.y) / 2, (lp.z + rp.z) / 2
        lr, rr = left.shape_rotation, right.shape_rotation
        # mirroring across X keeps the X angle and flips the Y and Z angles
        rx = (lr.x + rr.x) / 2
        ry = (lr.y - rr.y) / 2
        rz = (lr.z - rr.z) / 2
        left_sign = 1.0 if lp.x >= middle else -1.0
        left.shape_size = common.Vector3(*size)
        right.shape_size = common.Vector3(*size)
        left.shape_position = common.Vector3(middle + left_sign * x, y, z)
        right.shape_position = common.Vector3(middle - left_sign * x, y, z)
        left.shape_rotation = common.Vector3(rx, ry, rz)
        right.shape_rotation = common.Vector3(rx, -ry, -rz)


def add_body_rigid_bodies(model, report=None) -> int:
    """Append bone-following body shapes to model.rigidbodies; returns how many were added."""
    from pymeshio import common, pmx
    report = report if report is not None else []
    bones = model.bones
    index = {bone.name: i for i, bone in enumerate(bones)}
    if "上半身" not in index or not model.vertices:
        return 0
    positions, main = _vertices(model)
    pos = {name: np.array([bones[i].position.x, bones[i].position.y, bones[i].position.z]) for name, i in index.items()}
    ys = positions[:, 1]
    height = float(ys.max() - ys.min()) or 10.0
    # The body's centre line: the middle of each left/right bone pair (single bones like 上半身 can sit slightly off it, so
    # they aren't used).
    pairs = [(p[0] + pos["右" + n[1:]][0]) / 2 for n, p in pos.items() if n.startswith("左") and "右" + n[1:] in pos]
    middle = float(np.median(pairs)) if pairs else float(pos["上半身"][0])

    # bones whose vertices count for a body bone (the D legs, twist and helper bones of the same part)
    def members(*names):
        found = {index[n] for n in names if n in index}
        return found

    def near_vertices(group, start, end, radius_cap):
        mask = np.isin(main, list(group)) if group else np.zeros(len(main), bool)
        if not np.any(mask):
            return None, None
        p = positions[mask]
        axis = end - start
        length = float(np.linalg.norm(axis))
        if length < 1e-6:
            return None, None
        unit = axis / length
        u = (p - start) @ unit / length
        radial = np.linalg.norm((p - start) - np.outer(u * length, unit), axis=1)
        inside = (u > 0.1) & (u < 0.9) & (radial < radius_cap)
        return (radial[inside] if np.any(inside) else radial[radial < radius_cap]), unit

    added = []

    def capsule(name, bone_name, start, end, group, radius_default, share=0.6, shrink=1.0):
        if bone_name not in index:
            return
        start, end = np.asarray(start, float), np.asarray(end, float)
        length = float(np.linalg.norm(end - start))
        if length < 1e-4:
            return
        radial, _unit = near_vertices(group, start, end, radius_cap=max(length, height * 0.08))
        radius = float(np.percentile(radial, share * 100)) if radial is not None and len(radial) > 5 else radius_default
        radius = max(height * 0.008, min(radius * shrink, length))
        cylinder = max(0.0, length - radius)
        rotation = _rotation_to(end - start)
        centre = (start + end) / 2
        added.append(pmx.RigidBody(name, name, index[bone_name], GROUP, MASK, pmx.SHAPE_CAPSULE,
                                   common.Vector3(radius, cylinder, 0.0), common.Vector3(*centre),
                                   common.Vector3(*rotation), 1.0, 0.5, 0.5, 0.0, 0.5, 0))

    def sideways(name, bone_name, top, bottom, group, fixed=None):
        """A torso piece: a capsule across the body (along X) as wide as the vertices between top and bottom."""
        if bone_name not in index or not group:
            return
        mask = np.isin(main, list(group))
        p = positions[mask]
        lo, hi = min(top[1], bottom[1]), max(top[1], bottom[1])
        p = p[(p[:, 1] >= lo) & (p[:, 1] <= hi)]
        if len(p) < 10:
            return
        x = np.percentile(p[:, 0], [8, 92])
        z = np.percentile(p[:, 2], [10, 90])
        radius = max(height * 0.01, float(z[1] - z[0]) / 2 * 0.9)
        # Centred on the body's centre line and only as wide as its narrower side, so a bag on one hip doesn't widen it.
        # Vertices that are all on one side keep their own centre.
        if x[0] < middle < x[1]:
            mid, half = middle, min(middle - float(x[0]), float(x[1]) - middle)
        else:
            mid, half = float(x[0] + x[1]) / 2, float(x[1] - x[0]) / 2
        x = np.array([mid - half, mid + half])
        width = 2 * half
        cylinder = max(0.0, width - 2 * radius)
        if fixed is not None:                            # a set size (radius, height), scaled to the model
            radius, cylinder = fixed
        centre = np.array([(x[0] + x[1]) / 2, (lo + hi) / 2, (z[0] + z[1]) / 2])
        added.append(pmx.RigidBody(name, name, index[bone_name], GROUP, MASK, pmx.SHAPE_CAPSULE,
                                   common.Vector3(radius, cylinder, 0.0), common.Vector3(*centre),
                                   common.Vector3(0.0, 0.0, math.pi / 2), 1.0, 0.5, 0.5, 0.0, 0.5, 0))

    # torso: 下半身 from the hips' top to the legs, 上半身 up to 上半身2, 上半身2 up to the neck
    upper, upper2, lower = pos["上半身"], pos.get("上半身2"), pos.get("下半身", pos["上半身"])
    neck = pos.get("首")
    legs = [pos[n] for n in ("左足", "右足") if n in pos]
    leg_y = float(np.mean([p[1] for p in legs])) if legs else lower[1] - height * 0.06
    lower_group = members("下半身", "腰")
    sideways("下半身", "下半身", (0, upper[1], 0), (0, min(leg_y, upper[1] - height * 0.03), 0), lower_group)
    if upper2 is not None:
        sideways("上半身", "上半身", (0, upper[1], 0), (0, upper2[1], 0), members("上半身"))
        if neck is not None:
            # the chest: radius 1, height 1 at the game's standard height (about 17.4 units), as tuned in PMX Editor
            chest = height / STANDARD_HEIGHT
            sideways("上半身2", "上半身2", (0, upper2[1], 0), (0, neck[1], 0), members("上半身2", "上半身3"),
                     fixed=(1.0 * chest, 1.0 * chest))
    # neck and head
    if neck is not None and "頭" in pos:
        capsule("首", "首", neck, pos["頭"], members("首"), height * 0.025)
        head_group = members("頭") | {i for i, bone in enumerate(bones) if 0 <= bone.parent_index < len(bones)
                                     and bones[bone.parent_index].name == "頭" and not any(
                                         k in bone.name.lower() for k in ("hair", "hat", "mao", "fa_", "toufa"))}
        head_points = positions[np.isin(main, list(head_group))] if head_group else np.zeros((0, 3))
        head_points = head_points[head_points[:, 1] > pos["頭"][1] - height * 0.02] if len(head_points) else head_points
        if len(head_points) > 20:
            top = float(np.percentile(head_points[:, 1], 98))
            bottom = pos["頭"][1]
            centre_xz = np.median(head_points[:, [0, 2]], axis=0)
            radius = float(np.percentile(np.linalg.norm(head_points[:, [0, 2]] - centre_xz, axis=1), 55))
            start = np.array([centre_xz[0], bottom + radius * 0.6, centre_xz[1]])
            end = np.array([centre_xz[0], max(top - radius, bottom + radius), centre_xz[1]])
            cylinder = max(0.0, float(end[1] - start[1]))
            centre = (start + end) / 2
            added.append(pmx.RigidBody("頭", "head", index["頭"], GROUP, MASK, pmx.SHAPE_CAPSULE,
                                       common.Vector3(radius, cylinder, 0.0), common.Vector3(*centre),
                                       common.Vector3(0.0, 0.0, 0.0), 1.0, 0.5, 0.5, 0.0, 0.5, 0))
    # arms, hands, legs, feet
    for s in ("左", "右"):
        def p(name):
            return pos.get(s + name)
        if p("肩") is not None and p("腕") is not None:
            capsule(s + "肩", s + "肩", p("肩"), p("腕"), members(s + "肩"), height * 0.03, shrink=0.8)
        if p("腕") is not None and p("ひじ") is not None:
            capsule(s + "腕", s + "腕", p("腕"), p("ひじ"), members(s + "腕", s + "腕捩"), height * 0.025)
        if p("ひじ") is not None and p("手首") is not None:
            capsule(s + "ひじ", s + "ひじ", p("ひじ"), p("手首"), members(s + "ひじ", s + "手捩"), height * 0.022)
        if p("手首") is not None and p("中指１") is not None:
            wrist, knuckle = p("手首"), p("中指１")
            capsule(s + "手首", s + "手首", wrist, wrist + (knuckle - wrist) * 1.6, members(s + "手首"),
                    height * 0.02, shrink=0.8)
        leg = s + "足D" if s + "足D" in index else s + "足"
        knee = s + "ひざD" if s + "ひざD" in index else s + "ひざ"
        ankle = s + "足首D" if s + "足首D" in index else s + "足首"
        if p("足") is not None and p("ひざ") is not None:
            capsule(s + "足", leg, p("足"), p("ひざ"), members(s + "足", s + "足D"), height * 0.035)
        if p("ひざ") is not None and p("足首") is not None:
            capsule(s + "ひざ", knee, p("ひざ"), p("足首"), members(s + "ひざ", s + "ひざD"), height * 0.03)
        toe = p("つま先") if p("つま先") is not None else p("足先EX")
        if p("足首") is not None and toe is not None:
            capsule(s + "足首", ankle, p("足首"), toe, members(s + "足首", s + "足首D", s + "足先EX"),
                    height * 0.02, shrink=0.8)
    _mirror_pairs(added, common, middle)
    model.rigidbodies.extend(added)
    if added:
        report.append(f"{len(added)} body rigid bodies")
    return len(added)
