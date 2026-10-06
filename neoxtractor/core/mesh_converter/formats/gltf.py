"""glTF 2.0 Format Converter"""

import base64
import json
import struct
from shlex import join
from typing import Any

from neoxtractor.core.mesh_loader import MeshData

NAME = "glTF 2.0 (GLTF) Format"
EXTENSION = ".gltf"


def pack_float_array(data: list[float]) -> bytes:
    """Pack a list of floats into binary data using struct.pack"""
    return struct.pack(f"{len(data)}f", *data)


def pack_int_array(data: list[int]) -> bytes:
    """Pack a list of integers into binary data using struct.pack"""
    return struct.pack(f"{len(data)}I", *data)


def pack_ushort_array(data: list[int]) -> bytes:
    """Pack a list of unsigned short integers into binary data using struct.pack"""
    return struct.pack(f"{len(data)}H", *data)


def create_accessor(
    buffer_view_id: int,
    component_type: int,
    count: int,
    type_str: str,
    min_vals: list[int | float] | None = None,
    max_vals: list[int | float] | None = None,
) -> dict[str, Any]:
    """
    Create an accessor for the glTF structure following glTF guidelines
    """
    accessor = {
        "bufferView": buffer_view_id,
        "componentType": component_type,
        "count": count,
        "type": type_str,
    }

    if min_vals is not None:
        accessor["min"] = min_vals

    if max_vals is not None:
        accessor["max"] = max_vals

    return accessor


def convert(mesh: MeshData) -> bytes:
    """
    Convert mesh to glTF format.

    Parameters:
    - mesh: MeshData object containing bones, vertices, faces, etc.

    Returns:
    - bytes: glTF file content as bytes (JSON with embedded binary data)
    """

    # Initialize buffer data
    buffer_data = bytearray()

    # Process positions (3 components per vertex)
    position_data = []
    for pos in mesh.mesh.position:
        position_data.extend(pos)  # [x, y, z]
    position_bytes = pack_float_array(position_data)

    # Process normals (3 components per vertex)
    normal_data = []
    for norm in mesh.mesh.normal:
        normal_data.extend(norm)  # [nx, ny, nz]
    normal_bytes = pack_float_array(normal_data)

    # Process UVs (2 components per vertex)
    uv_data = []
    for uv in mesh.mesh.uv:
        uv_data.extend(uv)  # [u, v]
    uv_bytes = pack_float_array(uv_data)

    # Process faces (3 indices per triangle)
    face_data = []
    for face in mesh.mesh.face:
        face_data.extend(face)  # [i0, i1, i2]
    face_bytes = pack_int_array(face_data)

    # Calculate offsets for each buffer view
    position_offset = 0
    normal_offset = position_offset + len(position_bytes)
    uv_offset = normal_offset + len(normal_bytes)
    face_offset = uv_offset + len(uv_bytes)

    # Add all data to buffer
    buffer_data.extend(position_bytes)
    buffer_data.extend(normal_bytes)
    buffer_data.extend(uv_bytes)
    buffer_data.extend(face_bytes)

    # Create bufferViews for each data type
    buffer_views = [
        {"buffer": 0, "byteOffset": position_offset, "byteLength": len(position_bytes)},
        {"buffer": 0, "byteOffset": normal_offset, "byteLength": len(normal_bytes)},
        {"buffer": 0, "byteOffset": uv_offset, "byteLength": len(uv_bytes)},
        {"buffer": 0, "byteOffset": face_offset, "byteLength": len(face_bytes)},
    ]

    # Create accessors for each data type
    accessors = [
        # Position accessor
        create_accessor(
            buffer_view_id=0,
            component_type=5126,  # FLOAT
            count=mesh.vertex_count,
            type_str="VEC3",
            min_vals=[
                min(position_data[0::3]),
                min(position_data[1::3]),
                min(position_data[2::3]),
            ],
            max_vals=[
                max(position_data[0::3]),
                max(position_data[1::3]),
                max(position_data[2::3]),
            ],
        ),
        # Normal accessor
        create_accessor(
            buffer_view_id=1,
            component_type=5126,  # FLOAT
            count=mesh.vertex_count,
            type_str="VEC3",
            min_vals=[
                -1.0,
                -1.0,
                -1.0,
            ],
            max_vals=[
                1.0,
                1.0,
                1.0,
            ],
        ),
        # UV accessor
        create_accessor(
            buffer_view_id=2,
            component_type=5126,  # FLOAT
            count=mesh.uv_count,
            type_str="VEC2",
            min_vals=[
                0.0,
                0.0,
            ],
            max_vals=[
                1.0,
                1.0,
            ],
        ),
        # Face accessor (indices)
        create_accessor(
            buffer_view_id=3,
            component_type=5125,  # UNSIGNED_INT
            count=mesh.face_count * 3,  # 3 indices per face
            type_str="SCALAR",
        ),
    ]

    # Create glTF structure
    gltf_data = {
        "asset": {"version": "2.0", "generator": "Custom Mesh Exporter"},
        "scenes": [{"nodes": [0]}],
        "scene": 0,
        "nodes": [
            {
                "name": "NeoXMesh",
                "children": [1],
            },
            {
                "name": "ArmatureNode",
                "children": [2],
            },
            {
                "name": "Mesh",
                "mesh": 0,
            },
        ],
        "meshes": [
            {
                "name": "Mesh",
                "primitives": [
                    {
                        "attributes": {"POSITION": 0, "NORMAL": 1, "TEXCOORD_0": 2},
                        "indices": 3,
                        "mode": 4,  # TRIANGLES
                    }
                ],
            }
        ],
        "accessors": accessors,
        "bufferViews": buffer_views,
    }

    if mesh.has_bones:
        gltf_data["meshes"][0]["primitives"][0]["attributes"]["JOINTS_0"] = 4
        gltf_data["meshes"][0]["primitives"][0]["attributes"]["WEIGHTS_0"] = 5

        skins_node = [
            {
                "name": "ArmatureSkin",
                # "inverseBindMatrices": 6,
                "joints": [],  # Joint indices
                "skeleton": 0,  # Root node
            }
        ]

        # Create bone nodes
        bone_nodes = []
        root_node = 0

        for i, (parent_idx, bone_name) in enumerate(
            zip(mesh.bones.parents, mesh.bones.names)
        ):
            # Create bone node
            bone_node = {
                "name": bone_name,
                "matrix": [data for l in mesh.bones.matrix[i].T.tolist() for data in l],
            }

            # Add to bone nodes list
            bone_nodes.append(bone_node)

            # +3 to account for central, armature and mesh nodes
            i += 3

            # Set parent relationship
            if parent_idx == -1:
                gltf_data["nodes"][1]["children"].append(i)
                skins_node[0]["skeleton"] = i
                root_node = i
            else:
                # Add to parent's children
                if parent_idx < len(bone_nodes):
                    if "children" not in bone_nodes[parent_idx]:
                        bone_nodes[parent_idx]["children"] = []
                    bone_nodes[parent_idx]["children"].append(i)

            skins_node[0]["joints"].append(i)

        gltf_data["skins"] = skins_node
        gltf_data["nodes"][2]["skin"] = 0

        for bone_node in bone_nodes:
            gltf_data["nodes"].append(bone_node)

        # Process joint indices (assuming 4 joints per vertex for skinning)
        joint_indices_data: list[int] = []
        for joint_list in mesh.bones.joints:
            fixed_list = [
                root_node if joint == 255 or joint == 65535 else joint
                for joint in joint_list
            ]
            joint_indices_data.extend(fixed_list)
        joint_indices_bytes = pack_ushort_array(joint_indices_data)

        # Process weights (assuming 4 weights per vertex for skinning)
        weights_data: list[float] = []
        for weight_list in mesh.bones.weights:
            weights_data.extend(weight_list)
        weights_bytes = pack_float_array(weights_data)

        inverse_matrix_data: list[float] = []
        for matrix in mesh.bones.matrix:
            x = matrix.T.tolist()
            inverse_matrix_data.extend(x[0])
            inverse_matrix_data.extend(x[1])
            inverse_matrix_data.extend(x[2])
            inverse_matrix_data.extend(x[3])
        inverse_matrix_bytes = pack_float_array(inverse_matrix_data)

        # Calculate offsets for bone data
        joint_indices_offset = face_offset + len(face_bytes)
        weights_offset = joint_indices_offset + len(joint_indices_bytes)
        inverse_matrix_offset = weights_offset + len(weights_bytes)

        # Add bone data to buffer
        buffer_data.extend(joint_indices_bytes)
        buffer_data.extend(weights_bytes)
        buffer_data.extend(inverse_matrix_bytes)

        buffer_views.extend(
            [
                {
                    "buffer": 0,
                    "byteOffset": joint_indices_offset,
                    "byteLength": len(joint_indices_bytes),
                },
                {
                    "buffer": 0,
                    "byteOffset": weights_offset,
                    "byteLength": len(weights_bytes),
                },
                {
                    "buffer": 0,
                    "byteOffset": inverse_matrix_offset,
                    "byteLength": len(inverse_matrix_bytes),
                },
            ]
        )

        # Joints accessor (4 unsigned shorts per vertex)
        accessors.append(
            create_accessor(
                buffer_view_id=4,
                component_type=5123,  # UNSIGNED_SHORT
                count=mesh.mesh.vertexes,
                type_str="VEC4",
            )
        )
        # Weights accessor (4 floats per vertex)
        accessors.append(
            create_accessor(
                buffer_view_id=5,
                component_type=5126,  # FLOAT
                count=mesh.mesh.vertexes,
                type_str="VEC4",
            )
        )

        accessors.append(
            create_accessor(
                buffer_view_id=6,
                component_type=5126,  # FLOAT
                count=mesh.bones.count,
                type_str="MAT4",
            )
        )

    # Convert buffer data to base64 string
    buffer_data_b64 = base64.b64encode(buffer_data).decode("utf-8")

    gltf_data["buffers"] = [
        {
            "uri": f"data:application/octet-stream;base64,{buffer_data_b64}",
            "byteLength": len(buffer_data),
        }
    ]

    # Convert to JSON with proper separators
    gltf_json = json.dumps(gltf_data, separators=(",", ":"))

    return gltf_json.encode("utf-8")
