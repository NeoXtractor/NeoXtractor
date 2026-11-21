"""PMX Format Converter"""

import io
from typing import cast

from pymeshio import pmx, common
import pymeshio.pmx.writer

from core.mesh_loader import MeshData

NAME = "Polygon Model eXtended (PMX) Format"
EXTENSION = ".pmx"

def convert(mesh: MeshData) -> bytes:
    """
    Convert mesh to PMX format.
    
    Parameters:
    - mesh: MeshData object containing bones, vertices, faces, etc.
    
    Returns:
    - bytes: PMX file content as bytes
    """
    pmx_model = pmx.Model()
    pmx_model.display_slots.append(pmx.DisplaySlot('表情', 'Exp', 1, None))
    pmx_model.english_name = 'Empty model'
    pmx_model.comment = 'NeoX Model Converterで生成'
    pmx_model.english_comment = 'Created by NeoX Model Converter.'

    # Build bone hierarchy if bones exist
    if mesh.has_bones:
        parent_child_dict = {}
        old2new = {}
        index_pool = [-1]
        bone_pool: list[pmx.Bone] = []

        # Build parent-child relationships
        for i, p in enumerate(mesh.bone_parent):
            if p not in parent_child_dict:
                parent_child_dict[p] = []
            parent_child_dict[p].append(i)

        def build_joint(index, parent_index):
            matrix = mesh.bone_matrix[index]
            # Extract translation from matrix for PMX
            x, y, z = matrix[3, 0], matrix[3, 1], matrix[3, 2]
            bone_pool.append(pmx.Bone(
                name=mesh.bone_name[index],
                english_name=mesh.bone_name[index],
                position=common.Vector3(x, y, z),
                parent_index=parent_index,
                layer=0,
                flag=0
            ))
            bone_pool[-1].setFlag(pmx.BONEFLAG_CAN_ROTATE, True)
            bone_pool[-1].setFlag(pmx.BONEFLAG_IS_VISIBLE, True)
            bone_pool[-1].setFlag(pmx.BONEFLAG_CAN_MANIPULATE, True)

        def deep_first_search(index, index_pool, parent_index):
            index_pool[0] += 1
            current_node_index = index_pool[0]
            old2new[index] = current_node_index
            build_joint(index, parent_index)
            if index in parent_child_dict:
                for child in parent_child_dict[index]:
                    deep_first_search(child, index_pool, current_node_index)

        # Find root bone and build hierarchy
        try:
            root_index = mesh.bone_parent.index(-1)
            deep_first_search(root_index, index_pool, -1)
        except ValueError:
            # No root bone found, create default structure
            for i in range(len(mesh.bone_name)):
                old2new[i] = i
                build_joint(i, mesh.bone_parent[i] if mesh.bone_parent[i] != -1 else -1)

        pmx_model.bones = bone_pool
    else:
        # Create a default root bone
        root_bone = pmx.Bone(
            name="root",
            english_name="root",
            position=common.Vector3(0, 0, 0),
            parent_index=-1,
            layer=0,
            flag=0
        )
        root_bone.setFlag(pmx.BONEFLAG_CAN_ROTATE, True)
        root_bone.setFlag(pmx.BONEFLAG_IS_VISIBLE, True)
        root_bone.setFlag(pmx.BONEFLAG_CAN_MANIPULATE, True)
        pmx_model.bones = [root_bone]
        old2new = {0: 0}

    for i, position in enumerate(mesh.position):
        x, y, z = position
        nx, ny, nz = mesh.normal[i] if mesh.has_normals else (0.0, 0.0, 1.0)
        u, v = mesh.uv[i] if mesh.has_uvs else (0.0, 0.0)

        if mesh.has_bones and i < len(mesh.vertex_bone):
            # Map old bone indices to new ones
            vertex_joint_index = []
            for joint_idx in mesh.vertex_bone[i]:
                if joint_idx in old2new:
                    vertex_joint_index.append(old2new[joint_idx])
                else:
                    vertex_joint_index.append(0)  # Default to root bone

            # Ensure we have 4 bone indices and weights
            while len(vertex_joint_index) < 4:
                vertex_joint_index.append(0)
            vertex_joint_index = vertex_joint_index[:4]

            vertex_weights = mesh.vertex_weight[i] if i < len(mesh.vertex_weight) else [1.0, 0.0, 0.0, 0.0]
            while len(vertex_weights) < 4:
                vertex_weights.append(0.0)
            vertex_weights = vertex_weights[:4]

            vertex = pmx.Vertex(
                common.Vector3(cast(int, x), cast(int, y), cast(int, z)),
                common.Vector3(cast(int, nx), cast(int, ny), cast(int, nz)),
                common.Vector2(cast(int, u), cast(int, v)),
                pmx.Bdef4(*vertex_joint_index, *vertex_weights),
                0.0
            )
        else:
            # No bone data - assign to root bone
            vertex = pmx.Vertex(
                common.Vector3(cast(int, x), cast(int, y), cast(int, z)),
                common.Vector3(cast(int, nx), cast(int, ny), cast(int, nz)),
                common.Vector2(cast(int, u), cast(int, v)),
                pmx.Bdef1(0),
                0.0
            )
        pmx_model.vertices.append(vertex)

    # Add faces
    for face in mesh.face:
        pmx_model.indices.extend(face)

    # Create materials based on mesh data or default
    if mesh.mesh:
        for i, (_mesh_vertex_count, mesh_face_count, _, _) in enumerate(mesh.mesh):
            material = pmx.Material(
                name=f'Mat{i}',
                english_name=f'material{i}',
                diffuse_color=common.RGB(1, 1, 1),
                alpha=1.0,
                specular_factor=1,
                specular_color=common.RGB(1, 1, 1),
                ambient_color=common.RGB(0, 0, 0),
                flag=0,
                edge_color=common.RGBA(0, 0, 0, 1),
                edge_size=0,
                texture_index=-1,
                sphere_texture_index=-1,
                sphere_mode=pmx.MATERIALSPHERE_NONE,
                toon_sharing_flag=1,
                toon_texture_index=0,
                comment="Auto-Generated Material",
                vertex_count=mesh_face_count * 3
            )
            pmx_model.materials.append(material)
    else:
        # Default single material
        material = pmx.Material(
            name='Material',
            english_name='Material',
            diffuse_color=common.RGB(1, 1, 1),
            alpha=1.0,
            specular_factor=1,
            specular_color=common.RGB(1, 1, 1),
            ambient_color=common.RGB(0, 0, 0),
            flag=0,
            edge_color=common.RGBA(0, 0, 0, 1),
            edge_size=0,
            texture_index=-1,
            sphere_texture_index=-1,
            sphere_mode=pmx.MATERIALSPHERE_NONE,
            toon_sharing_flag=1,
            toon_texture_index=0,
            comment="Auto-Generated Material",
            vertex_count=len(mesh.face) * 3
        )
        pmx_model.materials.append(material)

    # Write to bytes buffer
    buffer = io.BytesIO()
    pymeshio.pmx.writer.write(buffer, pmx_model)
    return buffer.getvalue()
