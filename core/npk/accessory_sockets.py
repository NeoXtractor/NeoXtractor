"""Accessories the game hangs on sockets instead of skinning them (earrings, hats, brooches, some glasses).

role_dress rows with bind_type 1 or 2 name their sockets, which live on the character rig as a bone plus a
MatrixToBone offset. Each copy is placed at local @ MatrixToBone @ bone_rest and parented to that bone.
"""

from __future__ import annotations

import copy
import ntpath
import threading
import xml.etree.ElementTree as ET

import numpy as np

RIG_GIMS = {"male": "model\\character\\male\\male_fenli.gim", "female": "model\\character\\female\\female_fenli.gim"}
_LOCK = threading.RLock()  # pieces load on several threads; the game files are read one at a time
SKELETONS = {
    "male": "model\\character\\male\\male_fenli_b72732.skeleton",
    "female": "model\\character\\female\\female_fenli_5653f7.skeleton",
}


def bone_key(name: str) -> str:
    return str(name).strip().replace(" ", "_").lower()


def for_library(library) -> "Sockets":
    """One shared Sockets per makeup library (created once, under the lock)."""
    with _LOCK:
        sockets = getattr(library, "_accessory_sockets", None)
        if sockets is None:
            sockets = library._accessory_sockets = Sockets(library.index, library.tables)
        return sockets


class Sockets:
    def __init__(self, index, tables):
        self.index = index
        self.tables = tables
        self._rows = None
        self._sockets: dict[str, dict] = {}
        self._rest: dict[str, dict] = {}
        self._offsets: dict | None = None
        self._meshes: dict[str, str] = {}

    def row(self, mesh_path: str):
        """The role_dress row of a piece (by its .gim), or None."""
        with _LOCK:
            return self._row(mesh_path)

    def _row(self, mesh_path: str):
        rows = self._all_rows(mesh_path)
        return rows[0] if rows else None

    def _all_rows(self, mesh_path: str) -> list:
        """Every role_dress row of a piece, in table order (a model can be listed with different sockets)."""
        if self._rows is None:
            self._rows = {}
            for row in self.tables.keyed_rows("role_dress").values():
                if isinstance(row, dict) and row.get("model"):
                    self._rows.setdefault(row["model"].replace("/", "\\").lower(), []).append(row)
        return self._rows.get(mesh_path.rsplit(".", 1)[0].lower() + ".gim", [])

    def sockets(self, sex: str) -> dict:
        with _LOCK:
            return self._socket_table(sex)

    def _socket_table(self, sex: str) -> dict:
        if sex not in self._sockets:
            result = {}
            found = self.index.read(RIG_GIMS[sex])
            if found is not None:
                sockets = ET.fromstring(found[0]).find("Sockets")
                for socket in (sockets if sockets is not None else []):
                    try:
                        matrix = np.array(
                            [float(v) for v in socket.get("MatrixToBone").strip("[]").split(",")]
                        ).reshape(4, 4)
                    except (AttributeError, ValueError):
                        continue
                    result[socket.get("Name")] = (bone_key(socket.get("BoneName") or "biped"), matrix)
            self._sockets[sex] = result
        return self._sockets[sex]

    def rest(self, sex: str) -> dict:
        """Bone name -> rest pose (row-vector world matrix) of the character skeleton."""
        with _LOCK:
            return self._rest_table(sex)

    def _rest_table(self, sex: str) -> dict:
        if sex not in self._rest:
            from core.mesh_converter.character_anim import rest_worlds
            from core.npk import neox_anim

            found = self.index.read(SKELETONS[sex])
            result = {}
            if found is not None:
                skeleton = neox_anim.Skeleton(found[0])
                for name, world in zip(skeleton.bone_names, rest_worlds(skeleton)):
                    result[bone_key(name)] = world.T.copy()
            self._rest[sex] = result
        return self._rest[sex]

    def attach(self, items, mesh_path: str, sex: str, report: list):
        """The piece's items placed on its sockets (unchanged when the game skins it to the character)."""
        with _LOCK:
            rows = [row for row in self._all_rows(mesh_path) if row.get("bind_type") in (1, 2)]
        if not rows:
            return items
        sockets = self.sockets(sex)
        rest = self.rest(sex)
        row = self._fitting_row(rows, items, sockets, rest)
        if row is None:
            return items  # modelled in character space already: leave it there
        names = [n for n in (row.get("bind_params") or [])[1:] if isinstance(n, str)]
        placed = []
        for socket_name in names:
            socket = sockets.get(socket_name)
            if socket is None or socket[0] not in rest:
                report.append(f"{ntpath.basename(mesh_path)}: socket {socket_name} not found")
                continue
            bone, offset = socket
            place = offset @ rest[bone]
            suffix = socket_name.rsplit("_", 1)[-1] if len(names) > 1 else ""
            for item in items:
                placed.append(
                    dict(
                        item,
                        name=f"{item['name']}_{suffix}" if suffix else item["name"],
                        mesh=_place(item["mesh"], place, bone, rest[bone], suffix or socket_name),
                        socket_param=int(row.get("socket_param") or 0),
                        socket_bone=bone,
                        socket_sex=sex,
                    )
                )
        return placed or items

    # A placed piece this far from its socket's bone (model units; a character is ~17 tall) is in the wrong
    # space: e.g. glasses modelled at head height but listed on a socket that adds the head's height again
    FAR_FROM_BONE = 4.0

    @staticmethod
    def _fitting_row(rows, items, sockets, rest):
        """
        Of the piece's socket rows, the one that puts it nearest its socket's bone (the game lists some
        models twice: yanjing for origin-modelled glasses, yanjing02 for ones modelled at head height). None
        when every row would carry it far away: the model is already in character space.
        """
        points = [
            np.asarray(item["mesh"].position, float)
            for item in items
            if item.get("mesh") is not None and item["mesh"].position
        ]
        if not points:
            return rows[0]
        centre = np.r_[np.concatenate(points).mean(0), 1.0]
        best, best_distance = None, None
        for row in rows:
            names = [n for n in (row.get("bind_params") or [])[1:] if isinstance(n, str)]
            socket = sockets.get(names[0]) if names else None
            if socket is None or socket[0] not in rest:
                continue
            bone, offset = socket
            placed = (centre @ offset @ rest[bone])[:3]
            distance = float(np.linalg.norm(placed - rest[bone][3, :3]))
            if best_distance is None or distance < best_distance:
                best, best_distance = row, distance
        if best is None:
            return rows[0]  # sockets unknown: keep the old behaviour (reported later)
        return best if best_distance <= Sockets.FAR_FROM_BONE else None

    def mesh_of(self, path: str) -> str:
        """The mesh a worn piece's path means (a .gim names its mesh), lower case."""
        with _LOCK:
            key = path.replace("/", "\\").lower()
            if key not in self._meshes:
                mesh = key
                if key.endswith(".gim"):
                    mesh = key[:-4] + ".mesh"
                    found = self.index.read(path.replace("/", "\\"))
                    if found is not None:
                        try:
                            attribute = ET.fromstring(found[0]).get("Mesh")
                            if attribute and attribute.lower() != "false":
                                mesh = attribute.replace("/", "\\").lower()
                        except ET.ParseError:
                            pass
                self._meshes[key] = mesh
            return self._meshes[key]

    def bind_offsets(self) -> dict:
        """
        Reads accessory offsets from the game's role_dress_bind_info table. Wearing some hairstyles (item_type 1) or
        outfits (item_type 2) shifts accessories with a matching socket_param, such as hats over big hair or brooches
        onto a coat. Offsets are in the socket bone's own axes, keyed by (socket_param, worn mesh). Colour variants
        share their piece's mesh, so they share its offsets.
        """
        with _LOCK:
            if self._offsets is None:
                self._offsets = {}
                try:
                    rows = self.tables.keyed_rows("role_dress_bind_info")
                    dresses = self.tables.keyed_rows("role_dress")
                except Exception:
                    rows, dresses = {}, {}
                for row in rows.values():
                    if not isinstance(row, dict) or not isinstance(row.get("offset"), (list, tuple)):
                        continue
                    dress = dresses.get(row.get("dress_id"))
                    if not isinstance(dress, dict) or not dress.get("model") or len(row["offset"]) != 3:
                        continue
                    key = (int(row.get("id") or 0), self.mesh_of(dress["model"]))
                    self._offsets.setdefault(key, np.asarray(row["offset"], float))
            return self._offsets


def apply_bind_offsets(library, items, worn_paths) -> list:
    """
    The items with every socketed hat / brooch moved by the game's offset for the worn hairstyle or outfit
    (worn_paths: their mesh or .gim paths). Items without an offset are returned as they are.
    """
    if not any(item.get("socket_param") for item in items):
        return items
    sockets = for_library(library)
    offsets = sockets.bind_offsets()
    worn = [sockets.mesh_of(path) for path in worn_paths if path]
    output = []
    for item in items:
        param = item.get("socket_param") or 0
        offset = next((offsets[(param, mesh)] for mesh in worn if (param, mesh) in offsets), None) if param else None
        rest = sockets.rest(item.get("socket_sex", "male")).get(item.get("socket_bone")) if offset is not None else None
        if offset is None or rest is None:
            output.append(item)
            continue
        shift = offset @ rest[:3, :3]  # bone axes -> character space (row vectors)
        output.append(dict(item, mesh=_shifted(item["mesh"], shift)))
    return output


def _shifted(mesh, shift: np.ndarray):
    """A copy of a placed piece moved by shift, its own bones with it (the socket's bone, last, stays)."""
    moved = copy.copy(mesh)
    moved.position = [tuple(p) for p in np.asarray(mesh.position, float) + shift]
    matrices = [np.asarray(m, float).copy() for m in (mesh.bone_matrix or [])]
    for matrix in matrices[:-1]:
        matrix[3, :3] += shift
    moved.bone_matrix = [m.astype(np.float32) for m in matrices]
    return moved


def _place(mesh, place: np.ndarray, bone: str, bone_rest: np.ndarray, tag: str):
    """A copy of the mesh moved by `place`, its own bones renamed and hung under the socket's bone."""
    shaped = copy.copy(mesh)
    count = len(mesh.position)
    positions = np.c_[np.asarray(mesh.position, float), np.ones(count)] @ place
    shaped.position = [tuple(p) for p in positions[:, :3]]
    linear = place[:3, :3]
    if mesh.normal and len(mesh.normal) == count:
        normals = np.asarray(mesh.normal, float) @ np.linalg.inv(linear).T
        normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-9)
        shaped.normal = [tuple(n) for n in normals]
    if np.linalg.det(linear) < 0:  # a mirrored socket (the right ear): keep faces outward
        shaped.face = [(a, c, b) for a, b, c in mesh.face]
    own = list(mesh.bone_name or [])
    names = [f"{tag}_{bone_key(n)}" for n in own] + [bone]
    parents = [(p if 0 <= p < len(own) else len(own)) for p in (mesh.bone_parent or [-1] * len(own))] + [-1]
    binds = [np.asarray(m, float) @ place for m in (mesh.bone_matrix or [])] + [bone_rest]
    shaped.bone_name = names
    shaped.bone_parent = parents
    shaped.bone_matrix = [m.astype(np.float32) for m in binds]
    if own and mesh.vertex_bone and len(mesh.vertex_bone) == count:
        shaped.vertex_bone = [tuple(b) for b in mesh.vertex_bone]
        shaped.vertex_weight = [tuple(w) for w in mesh.vertex_weight]
    else:  # an unskinned piece: all of it on the socket's bone
        shaped.vertex_bone = [(len(own), 0, 0, 0)] * count
        shaped.vertex_weight = [(1.0, 0.0, 0.0, 0.0)] * count
    try:
        shaped.bone_count = len(names)
        shaped.bone_exist = 1
    except AttributeError:
        pass
    return shaped


class AttachedClip:
    """
    A prop's own clip (the doll of idle_276_sylg, the rose sword of idle_298_fnlg) played on its socket: its bones,
    named as _place names them, move by the prop's clip under the character clip's socket bone.
    """

    def __init__(self, character, prop, bone: str, offset: np.ndarray, tag: str):
        self.character, self.prop = character, prop
        self.name = getattr(character, "name", "")
        self.socket = list(character.bone_names).index(bone)
        self.offset = np.asarray(offset, float).T  # row-vector -> column-vector
        own = len(prop.bone_names)
        self.bone_names = [f"{tag}_{bone_key(n)}" for n in prop.bone_names] + [bone]
        self.parents = [(p if 0 <= p < own else own) for p in prop.parents] + [-1]
        self.duration = character.duration
        self._last = None

    def world_matrices(self, time: float) -> np.ndarray:
        if self._last is not None and self._last[0] == time:
            return self._last[1]
        socket = self.character.world_matrices(time)[self.socket]
        world = np.concatenate([socket @ self.offset @ self.prop.world_matrices(time), socket[None]])
        self._last = (time, world)
        return world
