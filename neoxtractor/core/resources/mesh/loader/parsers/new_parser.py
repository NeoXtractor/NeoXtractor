import io
from typing import Any, BinaryIO, Iterable

import numpy as np

from neoxtractor.core.binary_readers import (
    read_float,
    read_half_float,
    read_sint16,
    read_uint8,
    read_uint16,
    read_uint32,
)
from neoxtractor.core.logger import get_logger
from neoxtractor.core.resources.mesh.loader.types import BaseMeshParser, MeshData


def identify_mesh_type(
    size: int,
    vertex_count: int,
    face_count: int,
    meshes_inside_data: int,
    uv_total_data: int,
    bone_type: int,
    version: int,
) -> tuple[int, int]:
    # Smallest final size calculation for vertex bones and vertex weights, 4x8u + 4xfloat (20 bytes)

    test = 0
    # UV data: U, V, 2xfloat
    uv_total_data = uv_total_data * 8

    size = size - meshes_inside_data

    # Assume bone_type 1 or 4 has 20 bytes per vertex
    # vertex_bones 4*uint8 and vertex weights 4xfloat
    if bone_type == 1 or bone_type == 4:
        size = size - 20 * vertex_count

    match bone_type:
        case 0 | 1:
            test = size - vertex_count * 24 - face_count * 6 - uv_total_data - 2
            if not test or test == 32:
                return (1, 0)

            test = test - vertex_count * 12
            if not test or test == 32:
                return (1, 0)

            # vertex_bones is uint16
            test = test - vertex_count * 4
            if test > 0 and test < vertex_count - 1:
                return (2, 0)

            # bones has extra data + vertex is uint16
            test = test - vertex_count * 4
            if test > 0 and test < vertex_count - 1:
                return (3, 0)

            if version == 4:
                return (-1, 0)

                # Keep the rest of the code, for when we figure out how to unpack these

                # No bones
                test = size - vertex_count * 14 - face_count * 6 - (uv_total_data // 2)
                if test >= 0 and test < vertex_count - 1:
                    return (-1, 0)
                # Vertex is +1 because vertex_weight can be half_float
                test = (
                    size - vertex_count * 3 - face_count * 6 - (uv_total_data // 2) - 1
                )
                if not test or test == 32:
                    return (-1, 0)
                # UV data: U, V, 4 bytes each, (8 bytes total, for vertex_count and extra)
                test = size - vertex_count * 11 - face_count * 6 - uv_total_data - 1
                if not test:
                    return (-1, 0)

                # vertex positions (4*uint16?), normals (3*half_float) 14 bytes per vertex, vertex_weights (4xhalf_float), uv (2*half_float)
                test = (
                    size - vertex_count * 6 - face_count * 6 - uv_total_data // 2 - 32
                )
                if test >= 0 and test < vertex_count - 1:
                    return (-1, 0)

                test = size - vertex_count * 14 - face_count * 6 - uv_total_data
                # vertex positions are 8 bytes whole (the 3 coordinates, somehow), and normals are quantized ints (2 bytes each), 14 bytes per vertex, and 300 bytes of some bullshit data in the middle
                if test >= 0 and test < vertex_count - 1:
                    return (-1, 0)

        case 3 | 4:
            test = size - vertex_count * 12 - face_count * 6 - uv_total_data - 2
            if not test or test == 32:
                return (4, 0)

            # vertex_bones is uint16
            test = test - vertex_count * 4
            if not test or test == 32:
                return (5, 0)

            # _flag is 1
            test = test - vertex_count * 2
            if not test or test == 32:
                return (4, 0)

            # vertex_bones is uint16 AND _flag is 1
            test = test - vertex_count * 4
            if not test or test == 32:
                return (5, 0)

            if version == 7:
                # Vertex and normals are quantized, uint16
                test = size - vertex_count * 20 - face_count * 6 - uv_total_data // 2
                if test >= 0 and test < vertex_count - 1:
                    return (22, test)

                test = test - vertex_count * 4
                if test >= 0 and test < vertex_count - 1:
                    return (23, test)

                # Vertex and normals are quantized, uint16
                test = size - vertex_count * 20 - face_count * 6 - uv_total_data
                if test >= 0 and test < vertex_count - 1:
                    return (20, test)

                test = test - vertex_count * 4
                if test >= 0 and test < vertex_count - 1:
                    return (21, test)

            return (100, 0)

        case _:
            return (-98, 0)
    return (-99, 0)


# Try to identify if the following data is uint8 or uint16, if its 16 bits, return True, else False
def bones_is_16(f: BinaryIO, bones: int) -> bool:
    ret = f.tell()
    f.seek(bones, 1)
    count = 0
    while f.read(1) != b"\x00":
        count += 1
    if count == 0 or count > 32:
        # If im in the middle of the data and I'm on a 00 byte or I read more than 32 bytes of data, its 16 bits
        f.seek(ret, 0)
        return True
    while f.read(1) == b"\x00":
        count += 1

    # If after jumping the bone count, I read and the sum between the non-zero bytes and the zero bytes is 32, its 16 bits
    # (since bones names are stored in 32 bit chunks)
    # # If none of these conditions are met, its 8 bits because I jumped to the middle and its not bone name data
    f.seek(ret, 0)

    return count != 31


def dequant(value: int, min_float: float, max_float: float) -> float:
    normalized = (value + 32768) / 65535.0
    return min_float + (normalized * (max_float - min_float))


def read_dequant_16(f):
    return read_sint16(f)


def read_normal_16(f, min_float: float, max_float: float) -> float:
    return dequant(read_sint16(f), min_float, max_float)


class MeshParser0(BaseMeshParser):
    """Standard mesh parser for typical mesh formats with bone hierarchies."""

    def parse(self, data: bytes) -> MeshData:
        """Parse mesh."""

        f = io.BytesIO(data)

        raw_model = self._parse_mesh_testing(f)
        return self._standardize_mesh_data(raw_model)

    def _parse_mesh_testing(self, f: BinaryIO) -> dict[str, Iterable[Any] | int]:
        model: dict[str, Iterable[Any] | int] = {}
        model["mesh"] = {}
        model["bones"] = {}
        model["mesh"]["extra"] = []
        model["mesh"]["multiple_offsets"] = []
        MAX_PARENT_NODE = 255

        bone_parent_read = read_uint8
        vert_float_read = read_float
        vert_bone_read = read_uint8
        total_uv_data = 0
        meshes_inside = 1
        meshes_inside_data = 0

        _magic_number = read_uint32(f)
        model["version"] = read_uint16(f)
        read_uint16(f)  # always_0x0500
        model["bones"]["has_bones"] = read_uint16(f)
        read_uint16(f)  # always_0x0000
        get_logger().info(
            f"MESH: Version {model['version']} | Bone Type: {model['bones']['has_bones']}"
        )

        if model["bones"]["has_bones"] == 1 or model["bones"]["has_bones"] == 4:
            model["bones"]["count"] = bone_count = read_uint16(f)
            if model["version"] == 4 and bones_is_16(f, bone_count):
                bone_parent_read = read_uint16
                MAX_PARENT_NODE = 65535
            self._validate_bone_count(bone_count)

            model["bones"]["parent_connections"] = []
            for _ in range(bone_count):
                parent_node: int = bone_parent_read(f)
                model["bones"]["parent_connections"].append(
                    parent_node
                ) if parent_node != MAX_PARENT_NODE else model["bones"][
                    "parent_connections"
                ].append(-1)

            model["bones"]["names"] = []
            for _ in range(bone_count):
                bone_name = f.read(32)
                model["bones"]["names"].append(
                    bone_name.decode().replace("\0", "").replace(" ", "_")
                )

            bone_extra_info = read_uint8(f)
            if bone_extra_info:
                f.seek(28 * bone_count, 1)

            model["bones"]["matrix"] = []
            for _ in range(bone_count):
                matrix = [read_float(f) for _ in range(16)]
                matrix = np.array(matrix).reshape(4, 4)
                model["bones"]["matrix"].append(matrix)

            # creates a new "dummy_root" as a default bone for the rest of the starters to link to
            if model["bones"]["parent_connections"].count(-1) > 1:
                num = len(model["bones"]["parent_connections"])
                model["bones"]["parent_connections"] = list(
                    map(
                        lambda x: num if x == -1 else x,
                        model["bones"]["parent_connections"],
                    )
                )
                model["bones"]["parent_connections"].append(-1)
                model["bones"]["names"].append("dummy_root")
                model["bones"]["matrix"].append(np.identity(4))

            flag1 = read_uint8(f)
            if flag1 != 0:
                raise ValueError(
                    f"Unexpected _flag value {flag1} at position {hex(f.tell() - 1)}"
                )

        ending_address = read_uint32(f)
        f.seek(ending_address, 0)
        model["mesh"]["multiple"] = meshes_inside = read_uint16(f)
        for i in range(meshes_inside):
            model["mesh"]["multiple_offsets"].append(read_uint32(f))

        main_data_offset = model["mesh"]["multiple_offsets"].pop(0)

        # Temporary handling, calculate the size and just ignore the data
        if meshes_inside > 1:
            for i in model["mesh"]["multiple_offsets"]:
                f.seek(i, 0)
                small = f.tell()
                v = read_uint32(f)
                f.seek(v * 12, 1)
                v = read_uint32(f)
                f.seek(v * 2, 1)
                v = read_uint32(f)
                f.seek(v * 4, 1)
                meshes_inside_data += f.tell() - small + 1

        f.seek(main_data_offset)

        it = 0  # iterations
        while True:
            vertex_count = read_uint32(f)
            face_count = read_uint32(f)
            uv_layers = read_uint8(f)
            unknown = read_uint8(f)
            must_be_one = read_uint16(f)
            model["mesh"]["extra"].append(
                (vertex_count, face_count, uv_layers, unknown)
            )
            if must_be_one == 1:
                vertex_count = read_uint32(f)
                face_count = read_uint32(f)
                break
            else:
                it += 1
                f.seek(-2, 1)

        for vertex, _, uv, _ in model["mesh"]["extra"]:
            total_uv_data += vertex * uv

        # Temporary handling, remove if its problematic:
        total_uv_data = (
            vertex_count
            if vertex_count * 2 == total_uv_data and uv_layers == 1
            else total_uv_data
        )

        mesh_data_size = ending_address - f.tell()

        type, bytes_to_skip = identify_mesh_type(
            mesh_data_size,
            vertex_count,
            face_count,
            meshes_inside_data,
            total_uv_data,
            model["bones"]["has_bones"],
            model["version"],
        )

        get_logger().info(
            f"MESH: VERTS: {vertex_count} | FACES: {face_count} | UV_DATA: {total_uv_data} | UV_LAYERS: {'No' if uv_layers == 0 else uv_layers} | TYPE: {type}"
        )

        if meshes_inside_data != 0:
            get_logger().warning(
                f"MESH: This is a multiple mesh type ({meshes_inside} splits ~ {meshes_inside_data} extra bytes) - COMPATIBILITY NOT GURANTEED"
            )

        if type == 100:
            vert_float_read = read_half_float
            get_logger().warning(
                "MESH: This mesh has a non-standard UV count - UV data, bone joints and bone weights will be missing!"
            )
        elif type == -1:
            raise NotImplementedError("MESH: This mesh type is not yet implemented")

        elif type == 4 or type == 5:
            vert_float_read = read_half_float
        elif type == 5 or type == 2 or type == 3:
            vert_bone_read = read_uint16
        elif type == 20 or type == 21 or type == 22 or type == 23:
            f.seek(bytes_to_skip, 1)
            vert_float_read = read_dequant_16

        model["type"] = type
        model["mesh"]["data"] = (
            vertex_count,
            face_count,
            uv_layers,
        )

        model["mesh"]["position"] = []
        # vertex position
        if type == 20 or type == 21 or type == 22 or type == 23:
            for _ in range(vertex_count):
                x = dequant(read_sint16(f), -3800.0, 3800.0)
                y = dequant(read_sint16(f), -3500.0, 3500.0)
                z = dequant(read_sint16(f), -6000.0, 6000.0)
                model["mesh"]["position"].append((x, y, z))
        else:
            for _ in range(vertex_count):
                x = vert_float_read(f)
                y = vert_float_read(f)
                z = vert_float_read(f)
                model["mesh"]["position"].append((x, y, z))

        model["mesh"]["normal"] = []

        # vertex normal
        if type == 20 or type == 21 or type == 22 or type == 23:
            for _ in range(vertex_count):
                x = read_normal_16(f, -1.0, 1.0)
                y = read_normal_16(f, -1.0, 1.0)
                z = read_normal_16(f, -1.0, 1.0)
                model["mesh"]["normal"].append((x, y, z))
        else:
            for _ in range(vertex_count):
                x = vert_float_read(f)
                y = vert_float_read(f)
                z = vert_float_read(f)
                model["mesh"]["normal"].append((x, y, z))

        _flag = read_uint16(f)

        if _flag == 1 and (type == 4 or type == 5 or type == 7 or type == 100):
            f.seek(vertex_count * 6, 1)
        elif type == 20 or type == 21 or type == 22 or type == 23:
            f.seek(vertex_count * 8 - 2, 1)
        elif _flag == 1:
            f.seek(vertex_count * 12, 1)
        elif _flag > 1:
            f.seek(_flag * 4, 1)

        model["mesh"]["face"] = []
        # face index table
        for _ in range(face_count):
            v1 = read_uint16(f)
            v2 = read_uint16(f)
            v3 = read_uint16(f)
            model["mesh"]["face"].append((v1, v2, v3))

        if type == 21:
            f.seek(vertex_count * 4, 1)

        elif type == 23:
            total_uv_data = total_uv_data // 2

        model["mesh"]["uv"] = []
        if uv_layers and type != 100:
            for _ in range(total_uv_data):
                model["mesh"]["uv"].append((read_float(f), read_float(f)))
        else:
            for _ in range(vertex_count):
                model["mesh"]["uv"].append((0.0, 0.0))

        if (
            model["bones"]["has_bones"] == 1
            or model["bones"]["has_bones"] == 4
            and type != 100
        ):
            model["bones"]["joints"] = []
            for _ in range(vertex_count):
                model["bones"]["joints"].append([vert_bone_read(f) for _ in range(4)])

            model["bones"]["weights"] = []
            for _ in range(vertex_count):
                model["bones"]["weights"].append([read_float(f) for _ in range(4)])
        elif (
            model["bones"]["has_bones"] == 1
            or model["bones"]["has_bones"] == 4
            and type == 100
        ):
            model["bones"]["joints"] = []
            model["bones"]["weights"] = []
            for _ in range(vertex_count):
                model["bones"]["joints"].append((0, 0, 0, 0))
                model["bones"]["weights"].append((0.0, 0.0, 0.0, 0.0))

        if type == 20 or type == 21:
            x, y, z = zip(*model["mesh"]["position"])
            minx = min(x)
            miny = min(y)
            minz = min(z)
            maxx = max(x)
            maxy = max(y)
            maxz = max(z)
            for i in range(vertex_count):
                model["mesh"]["position"][i] = (
                    dequant(model["mesh"]["position"][i][0], minx, maxx),
                    dequant(model["mesh"]["position"][i][1], miny, maxy),
                    dequant(model["mesh"]["position"][i][2], minz, maxz),
                )

        return model
