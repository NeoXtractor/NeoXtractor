"""Combine several NeoX meshes into one GLB or PMX.

Each item is a dict: name, mesh (MeshData), an optional 4x4 placement matrix, and parts (one per sub-mesh:
material, texture, image, alpha mode, cutoff).
"""

from __future__ import annotations

import io
import json
import re
import struct
from pathlib import Path

import numpy as np

from core.mesh_loader.types import MeshData

MIRROR = np.diag([-1.0, 1.0, 1.0])          # NeoX (left-handed) -> glTF (right-handed)
ROOT_NAME = "root"


def _ranges(mesh) -> list[int]:
    faces = len(mesh.face)
    counts = [part[1] for part in mesh.mesh] if mesh.mesh else [faces]
    result, used = [], 0
    for count in counts:
        count = max(0, min(count, faces - used))
        result.append(count)
        used += count
    if used < faces:
        result[-1] += faces - used
    return result


def _bone_usage(mesh) -> dict[int, float]:
    """Total skin weight on each bone index of a mesh."""
    usage: dict[int, float] = {}
    for bones, weights in zip(mesh.vertex_bone or (), mesh.vertex_weight or ()):
        for bone, weight in zip(bones, weights):
            if weight > 0:
                usage[bone] = usage.get(bone, 0.0) + float(weight)
    return usage


def merge(items: list[dict], separate_skeletons: bool = False):
    """
    Merge items into one MeshData (NeoX space) and a material list with one entry per
    merged sub-mesh. Bones are unified by name (character parts share one skeleton), or
    kept per item with separate_skeletons (placed objects that animate on their own).
    Several roots get a shared "root" bone. merged.joint_keys holds (item index or None, bone name).
    """
    bones: dict[tuple, list] = {}             # (group, name) -> [row-vector world matrix, parent key]
    order: list[tuple] = []
    usage: dict[tuple, float] = {}            # how much skin weight the chosen bind carries
    for number, item in enumerate(items):
        mesh = item["mesh"]
        if not mesh.has_bones:
            continue
        group = number if separate_skeletons else None
        place = np.asarray(item.get("matrix") if item.get("matrix") is not None else np.eye(4), float)
        weight = _bone_usage(mesh)
        for index, name in enumerate(mesh.bone_name):
            key = (group, name)
            if index >= len(mesh.bone_matrix):
                continue
            # Character parts all carry the full skeleton, but cloth / face bones can sit in a
            # slightly different place in each part: use the bind of the part that is skinned to it
            if key in bones:
                if weight.get(index, 0.0) > usage[key]:
                    bones[key][0] = np.asarray(mesh.bone_matrix[index], float) @ place.T
                    usage[key] = weight[index]
                continue
            parent = mesh.bone_parent[index] if index < len(mesh.bone_parent) else -1
            parent_key = (group, mesh.bone_name[parent]) if 0 <= parent < len(mesh.bone_name) else None
            bones[key] = [np.asarray(mesh.bone_matrix[index], float) @ place.T, parent_key]
            usage[key] = weight.get(index, 0.0)
            order.append(key)

    def display(key):
        group, name = key
        return name if group is None else f"{re.sub(r'[.](mesh|gim)$', '', items[group]['name'], flags=re.I)}/{name}"

    roots = [key for key in order if bones[key][1] not in bones]
    if len(roots) != 1 or any(not item["mesh"].has_bones for item in items):
        names = {display(key) for key in order}
        base_name = ROOT_NAME
        while base_name in names:
            base_name += "_"
        base = (None, base_name)
        bones[base] = [np.eye(4), None]
        order.insert(0, base)
        for key in roots:
            bones[key][1] = base
        root_index = 0
    else:
        root_index = order.index(roots[0])
    bone_index = {key: i for i, key in enumerate(order)}

    positions, normals, uvs, faces = [], [], [], []
    vertex_bones, vertex_weights, parts, materials = [], [], [], []
    offset = 0
    for number, item in enumerate(items):
        mesh = item["mesh"]
        group = number if separate_skeletons else None
        count = len(mesh.position)
        if not count:
            continue
        place = np.asarray(item.get("matrix") if item.get("matrix") is not None else np.eye(4), float)
        p = np.asarray(mesh.position, float).reshape(-1, 3)
        positions.append(p @ place[:3, :3].T + place[:3, 3])
        if mesh.has_normals:
            n = np.asarray(mesh.normal, float).reshape(-1, 3) @ np.linalg.inv(place[:3, :3])
            n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
            normals.append(n)
        else:
            normals.append(np.tile([0.0, 1.0, 0.0], (count, 1)))
        uvs.append(np.asarray(mesh.uv, float).reshape(-1, 2) if mesh.has_uvs else np.zeros((count, 2)))
        faces.append(np.asarray(mesh.face, np.int64).reshape(-1, 3) + offset)

        if mesh.has_bones and len(mesh.vertex_bone) == count:
            remap = np.array([bone_index.get((group, name), root_index) for name in mesh.bone_name] + [root_index])
            local = np.asarray([list(b)[:4] + [len(mesh.bone_name)] * (4 - len(b)) for b in mesh.vertex_bone], np.int64)
            local = np.where((local < 0) | (local >= len(mesh.bone_name)), len(mesh.bone_name), local)
            weights = np.asarray([list(w)[:4] + [0.0] * (4 - len(w)) for w in mesh.vertex_weight], float)
            vertex_bones.append(remap[local])
            vertex_weights.append(weights)
        else:
            vertex_bones.append(np.full((count, 4), root_index))
            vertex_weights.append(np.tile([1.0, 0.0, 0.0, 0.0], (count, 1)))

        item_parts = item.get("parts") or []
        ranges = _ranges(mesh)
        for number, face_count in enumerate(ranges):
            info = item_parts[number] if number < len(item_parts) else {}
            parts.append((count if number == 0 else 0, face_count, 0, 0))
            object_name = re.sub(r"\.(mesh|gim)$", "", item["name"], flags=re.I)
            materials.append({
                "name": info.get("material") or f"{item['name']}_{number}",
                "object": item["name"],
                "part_name": object_name if len(ranges) == 1 else f"{object_name}_{number}",
                "texture": info.get("texture"),
                "image": info.get("image"),
                "alpha": info.get("alpha", "OPAQUE"),
                "cutoff": info.get("cutoff", 0.5),
            })
        offset += count

    if not positions:
        raise ValueError("Nothing to export")
    weights = np.concatenate(vertex_weights)
    totals = weights.sum(axis=1, keepdims=True)
    weights = np.where(totals > 1e-8, weights / np.maximum(totals, 1e-8), [1.0, 0.0, 0.0, 0.0])
    joints = np.concatenate(vertex_bones)
    joints[:, 0] = np.where(totals[:, 0] > 1e-8, joints[:, 0], root_index)

    merged = MeshData(version=items[0]["mesh"].version)
    merged.position = [tuple(v) for v in np.concatenate(positions)]
    merged.normal = [tuple(v) for v in np.concatenate(normals)]
    merged.uv = [tuple(v) for v in np.concatenate(uvs)]
    merged.face = [tuple(int(i) for i in f) for f in np.concatenate(faces)]
    merged.bone_exist = 1
    merged.bone_name = [display(key) for key in order]
    merged.bone_parent = [bone_index[bones[key][1]] if bones[key][1] in bone_index else -1 for key in order]
    merged.bone_matrix = [bones[key][0] for key in order]
    merged.bone_count = len(order)
    merged.joint_keys = list(order)
    merged.vertex_bone = [list(map(int, j)) for j in joints]
    merged.vertex_weight = [list(map(float, w)) for w in weights]
    merged.mesh = parts
    return merged, materials


def _safe_name(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\s]+', "_", name).strip("._") or "texture"
    return name[:80]


def _png(image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def _texture_names(materials) -> dict[int, str]:
    """Unique file stem per distinct image."""
    names, used = {}, set()
    for material in materials:
        image = material.get("image")
        if image is None or id(image) in names:
            continue
        base = _safe_name(Path(str(material.get("texture") or material["name"])).stem)
        name, number = base, 1
        while name.lower() in used:
            number += 1
            name = f"{base}_{number}"
        used.add(name.lower())
        names[id(image)] = name
    return names


def _trs(matrix):
    """Column-vector 4x4 -> glTF translation, rotation (xyzw) and scale lists."""
    from core.npk.neox_anim import matrix_quaternion
    scale = np.linalg.norm(matrix[:3, :3], axis=0)
    rotation = matrix_quaternion(matrix[:3, :3] / np.maximum(scale, 1e-12))
    return matrix[:3, 3].tolist(), rotation.tolist(), scale.tolist()


def _mirror(matrix):
    """NeoX (left-handed) column-vector matrix -> glTF space: mirror X on both sides."""
    flip = np.diag([-1.0, 1.0, 1.0, 1.0])
    return flip @ matrix @ flip


def _add_animations(gltf, items, mesh, bind_worlds, first_joint, accessor):
    """
    One glTF animation holding every item's default loop. Joint poses come from the clip's
    skeleton (placed by the item's matrix) and are written relative to each joint's parent.
    """
    key_index = {key: i for i, key in enumerate(mesh.joint_keys)}
    channels, samplers = [], []
    for number, item in enumerate(items):
        clip = item.get("animation")
        if clip is None:
            continue
        joints = {name: key_index.get((number, name), key_index.get((None, name))) for name in clip.bone_names}
        joints = {name: joint for name, joint in joints.items() if joint is not None}
        if not joints:
            continue
        place = np.asarray(item.get("matrix") if item.get("matrix") is not None else np.eye(4), float)
        frames = clip.frame_count
        times = np.arange(frames, dtype=np.float32) / np.float32(clip.frame_rate)
        # Animated model-space pose of every skeleton bone, per frame (NeoX space, placed)
        poses = np.array([clip.world_matrices(t, place) for t in times])
        skeleton_index = {name: i for i, name in enumerate(clip.bone_names)}
        animated_worlds = {}
        for name, joint in joints.items():
            animated_worlds[joint] = np.array([_mirror(pose) for pose in poses[:, skeleton_index[name]]])
        input_accessor = accessor(times.reshape(-1, 1), 5126, "SCALAR", minmax=True)
        for name, joint in joints.items():
            parent = mesh.bone_parent[joint]
            if parent in animated_worlds:
                parents = animated_worlds[parent]
            else:                                    # parent is not animated by this clip: its bind pose
                static = bind_worlds[parent] if parent >= 0 else np.eye(4)
                parents = np.repeat(static[None], frames, 0)
            locals_ = np.linalg.inv(parents) @ animated_worlds[joint]
            trs = [_trs(m) for m in locals_]
            rotations = np.array([r for _t, r, _s in trs], np.float32)
            for k in range(1, len(rotations)):           # keep quaternions on one hemisphere for smooth blending
                if np.dot(rotations[k], rotations[k - 1]) < 0:
                    rotations[k] = -rotations[k]
            node = first_joint + joint
            for path, values, kind in (("translation", np.array([t for t, _r, _s in trs], np.float32), "VEC3"),
                                       ("rotation", rotations, "VEC4"),
                                       ("scale", np.array([s for _t, _r, s in trs], np.float32), "VEC3")):
                samplers.append({"input": input_accessor, "output": accessor(values, 5126, kind), "interpolation": "LINEAR"})
                channels.append({"sampler": len(samplers) - 1, "target": {"node": node, "path": path}})
    if channels:
        gltf["animations"] = [{"name": "default loops", "channels": channels, "samplers": samplers}]


def write_glb(items: list[dict], target, animations: bool = True) -> Path:
    """
    One skinned mesh with a material per part and embedded PNG textures. Items carrying an
    'animation' (neox_anim.Clip) get their own joints and a looping glTF animation.
    """
    animated = animations and any(item.get("animation") is not None for item in items)
    # Several animated objects each keep their own skeleton (bones named object/bone); a single
    # model keeps plain bone names, so its animation retargets by name in Blender and similar tools
    mesh, materials = merge(items, separate_skeletons=animated and len(items) > 1)
    target = Path(target)
    gltf = {"asset": {"version": "2.0", "generator": "NeoXtractor HPMA model export"},
            "scene": 0, "scenes": [{"name": target.stem, "nodes": []}], "nodes": [], "meshes": [],
            "materials": [], "textures": [], "images": [], "samplers": [{"wrapS": 10497, "wrapT": 10497}],
            "accessors": [], "bufferViews": [], "skins": []}
    binary = bytearray()

    def view(data: bytes, target_kind=None):
        while len(binary) % 4:
            binary.append(0)
        entry = {"buffer": 0, "byteOffset": len(binary), "byteLength": len(data)}
        if target_kind:
            entry["target"] = target_kind
        binary.extend(data)
        gltf["bufferViews"].append(entry)
        return len(gltf["bufferViews"]) - 1

    def accessor(array, component, kind, target_kind=None, minmax=False):
        array = np.ascontiguousarray(array)
        result = {"bufferView": view(array.tobytes(), target_kind), "componentType": component,
                  "count": int(array.shape[0]), "type": kind}
        if minmax:
            result["min"] = array.min(axis=0).astype(float).tolist()
            result["max"] = array.max(axis=0).astype(float).tolist()
        gltf["accessors"].append(result)
        return len(gltf["accessors"]) - 1

    positions = (np.asarray(mesh.position, float) @ MIRROR.T).astype(np.float32)
    normals = (np.asarray(mesh.normal, float) @ MIRROR.T).astype(np.float32)
    uvs = np.asarray(mesh.uv, np.float32)
    faces = np.asarray(mesh.face, np.uint32)[:, [0, 2, 1]]
    attributes = {
        "POSITION": accessor(positions, 5126, "VEC3", 34962, True),
        "NORMAL": accessor(normals, 5126, "VEC3", 34962),
        "TEXCOORD_0": accessor(uvs, 5126, "VEC2", 34962),
        "JOINTS_0": accessor(np.asarray(mesh.vertex_bone, np.uint16), 5123, "VEC4", 34962),
        "WEIGHTS_0": accessor(np.asarray(mesh.vertex_weight, np.float32), 5126, "VEC4", 34962),
    }

    names = _texture_names(materials)
    image_index = {}
    for material in materials:
        image = material.get("image")
        gl_material = {"name": material["name"], "doubleSided": True,
                       "pbrMetallicRoughness": {"metallicFactor": 0.0, "roughnessFactor": 1.0}}
        if image is not None:
            if id(image) not in image_index:
                gltf["images"].append({"name": names[id(image)], "mimeType": "image/png",
                                       "bufferView": view(_png(image))})
                gltf["textures"].append({"source": len(gltf["images"]) - 1, "sampler": 0})
                image_index[id(image)] = len(gltf["textures"]) - 1
            gl_material["pbrMetallicRoughness"]["baseColorTexture"] = {"index": image_index[id(image)]}
            if material["alpha"] != "OPAQUE":
                gl_material["alphaMode"] = material["alpha"]
            if material["alpha"] == "MASK":
                gl_material["alphaCutoff"] = float(material["cutoff"])
        gltf["materials"].append(gl_material)

    primitives, start = [], 0
    for number, part in enumerate(mesh.mesh):
        chunk = faces[start:start + part[1]]
        start += part[1]
        if len(chunk):
            primitives.append({"attributes": attributes, "material": number,
                               "indices": accessor(chunk.reshape(-1), 5125, "SCALAR", 34963)})
    gltf["meshes"].append({"name": target.stem, "primitives": primitives})

    # Skeleton: NeoX bind matrices are row-vector model-space transforms
    worlds = []
    for matrix in mesh.bone_matrix:
        column = np.asarray(matrix, float).T
        mirrored = np.eye(4)
        mirrored[:3, :3] = MIRROR @ column[:3, :3] @ MIRROR
        mirrored[:3, 3] = MIRROR @ column[:3, 3]
        worlds.append(mirrored)
    first_joint = len(gltf["nodes"]) + 1
    gltf["nodes"].append({"name": target.stem, "mesh": 0, "skin": 0})
    for index, name in enumerate(mesh.bone_name):
        parent = mesh.bone_parent[index]
        local = np.linalg.inv(worlds[parent]) @ worlds[index] if parent >= 0 else worlds[index]
        translation, rotation, scale = _trs(local)       # TRS, since animated nodes may not use "matrix"
        gltf["nodes"].append({"name": name, "translation": translation, "rotation": rotation, "scale": scale})
    for index, parent in enumerate(mesh.bone_parent):
        if parent >= 0:
            gltf["nodes"][first_joint + parent].setdefault("children", []).append(first_joint + index)
    inverse_binds = np.array([np.linalg.inv(world).T.reshape(-1) for world in worlds], np.float32)
    roots = [first_joint + i for i, parent in enumerate(mesh.bone_parent) if parent < 0]
    gltf["skins"].append({"joints": list(range(first_joint, first_joint + len(worlds))),
                          "inverseBindMatrices": accessor(inverse_binds, 5126, "MAT4"),
                          "skeleton": roots[0]})
    gltf["scenes"][0]["nodes"] = [0] + roots

    if animated:
        _add_animations(gltf, items, mesh, worlds, first_joint, accessor)

    for key in ("images", "textures"):
        if not gltf[key]:
            del gltf[key]
    if "textures" not in gltf:
        del gltf["samplers"]
    while len(binary) % 4:
        binary.append(0)
    gltf["buffers"] = [{"byteLength": len(binary)}]
    document = json.dumps(gltf, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    document += b" " * (-len(document) % 4)
    total = 12 + 8 + len(document) + 8 + len(binary)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "wb") as stream:
        stream.write(struct.pack("<4sII", b"glTF", 2, total))
        stream.write(struct.pack("<I4s", len(document), b"JSON") + document)
        stream.write(struct.pack("<I4s", len(binary), b"BIN\0") + bytes(binary))
    return target


def write_pmx(items: list[dict], target, bone_names: list[str] | None = None) -> Path:
    """
    PMX through the app's converter, with materials named per part and PNG textures beside it.
    bone_names optionally renames the merged bones (e.g. to fit a VMD's 15-byte names).
    """
    from core.mesh_converter.formats import pmx as pmx_format
    mesh, materials = merge(items)
    if bone_names is not None:
        mesh.bone_source_names = list(mesh.bone_name)      # the HPMA names stay as English names
        mesh.bone_name = list(bone_names)
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    names = _texture_names(materials)
    folder = target.parent / f"{target.stem}_textures"
    written = {}
    pmx_materials = []
    for material in materials:
        image = material.get("image")
        texture = None
        if image is not None:
            if id(image) not in written:
                folder.mkdir(exist_ok=True)
                image.save(folder / f"{names[id(image)]}.png")
                written[id(image)] = f"{folder.name}\\{names[id(image)]}.png"
            texture = written[id(image)]
        # Named like the object in NeoXtractor; the game's material name goes in the English name
        pmx_materials.append({"name": material["part_name"], "english_name": material["name"], "texture": texture})
    target.write_bytes(pmx_format.convert(mesh, materials=pmx_materials))
    return target


def write_obj(items: list[dict], target) -> Path:
    """
    Static OBJ + MTL (no rig), one group and material per part, PNG textures in <stem>_textures.
    Same space as the GLB: NeoX mirrored on X, so it opens upright in Blender and other tools.
    """
    mesh, materials = merge(items)
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    names = _texture_names(materials)
    folder = target.parent / f"{target.stem}_textures"
    positions = np.asarray(mesh.position, float) @ MIRROR.T
    normals = np.asarray(mesh.normal, float) @ MIRROR.T
    uvs = np.asarray(mesh.uv, float)
    faces = np.asarray(mesh.face, np.int64)[:, [0, 2, 1]] + 1          # mirrored, so flip the winding
    lines = [f"# NeoXtractor HPMA model export", f"mtllib {target.stem}.mtl"]
    lines += [f"v {x:.6f} {y:.6f} {z:.6f}" for x, y, z in positions]
    lines += [f"vt {u:.6f} {1.0 - v:.6f}" for u, v in uvs]              # OBJ's V runs bottom-up
    lines += [f"vn {x:.6f} {y:.6f} {z:.6f}" for x, y, z in normals]
    mtl, written, used = [f"# {target.stem}"], {}, set()
    start = 0
    for material, (_vertices, count, _a, _b) in zip(materials, mesh.mesh):
        name = _safe_name(material["part_name"])
        while name in used:
            name += "_"
        used.add(name)
        lines += [f"g {name}", f"usemtl {name}"]
        lines += [f"f {a}/{a}/{a} {b}/{b}/{b} {c}/{c}/{c}" for a, b, c in faces[start:start + count]]
        start += count
        mtl += [f"newmtl {name}", "Ka 1 1 1", "Kd 1 1 1", "Ks 0 0 0", "Ns 50", "illum 1"]
        image = material.get("image")
        if image is not None:
            if id(image) not in written:
                folder.mkdir(exist_ok=True)
                image.save(folder / f"{names[id(image)]}.png")
                written[id(image)] = f"{folder.name}/{names[id(image)]}.png"
            mtl.append(f"map_Kd {written[id(image)]}")
            if material.get("alpha") in ("BLEND", "MASK"):
                mtl.append(f"map_d {written[id(image)]}")
    (target.parent / f"{target.stem}.mtl").write_text("\n".join(mtl) + "\n", encoding="utf-8")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target
