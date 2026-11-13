"""Provides a mesh renderer."""

from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

import numpy as np
from PySide6 import QtGui, QtWidgets

from neoxtractor.core.utils.app import get_application_path
from neoxtractor.gui.renderers.point_renderer import PointRenderer
from neoxtractor.gui.utils.rendering import is_d3d, static_uniform_buffer_type

if TYPE_CHECKING:
    # Type-only, named through the public resource facade, not the private
    # ``_mesh`` package.
    from neoxtractor.core.resources.mesh import MeshData
    # Type-only: avoids the renderers <-> viewers import cycle.
    from neoxtractor.gui.viewers.mesh_viewer.camera import Camera

MESH_COLOR = [0.8, 0.8, 0.8]

BONE_COLOR = [1.0, 0.8, 0.3]

NORMALS_COLOR = [0.8, 0.1, 0.3]


@dataclass
class ProcessedMeshData:
    """
    Data structure to hold processed mesh data.
    """

    raw_data: MeshData
    position: np.ndarray
    normal: np.ndarray

    vertices: np.ndarray  # Combined position and normals
    indices: np.ndarray
    wireframe_indices: np.ndarray

    def __init__(self, raw_data: MeshData):
        self.raw_data = raw_data

        # Process mesh data
        pos = np.array(raw_data.mesh.position)
        pos[:, 0] = -pos[:, 0]  # Flip X-axis
        norm = np.array(raw_data.mesh.normal)
        norm[:, 0] = -norm[:, 0]  # Flip X-axis for normals as well

        # Combine position and normals into a single array
        self.vertices = np.hstack((pos, norm))

        # Reorder indices
        self.indices = np.array(raw_data.mesh.face)[:, [1, 0, 2]]
        self.wireframe_indices = self._generate_wireframe_indices(self.indices)

        # Calculate normal lines
        normal_line_vertices = np.empty((pos.shape[0] * 2, 3), dtype="f4")
        normal_line_vertices[0::2] = pos
        normal_line_vertices[1::2] = pos + norm * 0.1

        self.normal_lines = normal_line_vertices

        # Calculate center and size
        # <aexadev>Axis aligned bounding box to determine model size
        self.aabb_min = pos.min(axis=0)
        self.aabb_max = pos.max(axis=0)

        self.center = (self.aabb_min + self.aabb_max) * 0.5
        self.extents = self.aabb_max - self.aabb_min

        self.radius = np.linalg.norm(self.extents) * 0.5
        self.size = self.extents.max()

        # Calculate each bone's position and connections
        bone_positions = []
        bone_lines = []

        for i, parent in enumerate(raw_data.bones.parents):
            # Apply the flip to the bone's matrix
            matrix = raw_data.bones.matrix[i]
            pos = np.asarray(matrix.T)[:3, 3].copy()
            pos[0] = -pos[0]  # Flip X-axis for bone positions
            bone_positions.append(pos)

            # Only create a line if the bone has a parent
            if parent != -1:
                parent_matrix = raw_data.bones.matrix[parent]
                parent_pos = np.asarray(parent_matrix.T)[:3, 3].copy()
                parent_pos[0] = -parent_pos[0]
                bone_lines.extend([pos, parent_pos])

        self.bone_positions = bone_positions
        self.bone_lines = bone_lines

    def _generate_wireframe_indices(self, triangle_indices: np.ndarray) -> np.ndarray:
        """Generate edge indices for wireframe rendering from triangle indices."""
        edges = set()

        for triangle in triangle_indices:
            # Add the three edges of each triangle
            edges.add(tuple(sorted([triangle[0], triangle[1]])))
            edges.add(tuple(sorted([triangle[1], triangle[2]])))
            edges.add(tuple(sorted([triangle[2], triangle[0]])))

        # Convert to numpy array
        return np.array(list(edges), dtype=np.uint32).flatten()

    @property
    def vertex_count(self) -> int:
        """Returns the total number of vertices in the mesh."""
        return len(self.raw_data.mesh.position)

    @property
    def face_count(self) -> int:
        """Returns the total number of faces (triangles) in the mesh."""
        return len(self.raw_data.mesh.face)

    @property
    def triangle_count(self) -> int:
        """Returns the total number of triangles in the mesh (same as face_count)."""
        return self.face_count

    @property
    def edge_count(self) -> int:
        """Returns the total number of unique edges in the wireframe."""
        return len(self.wireframe_indices) // 2

    @property
    def bone_count(self) -> int:
        """Returns the total number of bones in the mesh."""
        return len(self.raw_data.bones.parents)


class MeshRenderer:
    """
    A Qt RHI-based mesh renderer for 3D graphics rendering.
    This class provides comprehensive mesh rendering capabilities using Qt's Rendering Hardware
    Interface (RHI), supporting various rendering modes and visualization features for 3D meshes.
    Features:
        - Solid and wireframe mesh rendering
        - Bone structure visualization with lines and points
        - Vertex normal vector display for debugging
        - GPU-based rendering using modern graphics pipelines
        - Dynamic resource management with automatic cleanup
    Rendering Modes:
        - Solid rendering: Standard mesh rendering with lighting
        - Wireframe rendering: Edge-only visualization
        - Bone visualization: Skeletal structure display
        - Normal visualization: Vertex normal vector display
    GPU Resources:
        The renderer manages various GPU resources including:
        - Graphics pipelines for different rendering modes
        - Vertex and index buffers for geometry data
        - Uniform buffers for transformation matrices and material properties
        - Shader resource bindings for pipeline configuration
    Usage:
        renderer = MeshRenderer(rhi_widget)
        renderer.initialize(cb, vertex_shaders, point_shaders, mvp_buffer, mvp_bindings)
        renderer.mesh_data = processed_mesh_data
        renderer.update_resources(resource_updates, camera)
        renderer.render(command_buffer)
    Attributes:
        wireframe_mode (bool): Enable wireframe rendering mode
        draw_bones (bool): Enable bone structure visualization
        draw_normals (bool): Enable vertex normal visualization
        mesh_data (ProcessedMeshData | None): Current mesh data for rendering
        This renderer requires a valid Qt RHI context and properly initialized shaders
        before use. All GPU resources are automatically managed and cleaned up when
        mesh data is changed or the renderer is destroyed.
    """

    def __init__(self, rhi_widget: QtWidgets.QRhiWidget):
        self.wireframe_mode = False
        self.draw_bones = True
        self.draw_normals = False

        self._rhi_widget = rhi_widget

        self._rhi: QtGui.QRhi | None = None

        self._mesh_data: ProcessedMeshData | None = None

        self.colored_vertices_shaders: tuple[QtGui.QShader, QtGui.QShader] | None = None
        self.point_shaders: tuple[QtGui.QShader, QtGui.QShader] | None = None
        self._mesh_shaders: tuple[QtGui.QShader, QtGui.QShader] | None = None

        self._mesh_pipeline: QtGui.QRhiGraphicsPipeline | None = None
        self._mesh_wireframe_pipeline: QtGui.QRhiGraphicsPipeline | None = None
        self._vertex_line_pipeline: QtGui.QRhiGraphicsPipeline | None = None

        self.mvp_ubuf: QtGui.QRhiBuffer | None = None
        self.mvp_srb: QtGui.QRhiShaderResourceBindings | None = None

        self._mesh_vbuf: QtGui.QRhiBuffer | None = None
        self._mesh_ibuf: QtGui.QRhiBuffer | None = None
        self._mesh_wireframe_ibuf: QtGui.QRhiBuffer | None = None
        self._mesh_vert_ubuf: QtGui.QRhiBuffer | None = None
        self._mesh_frag_ubuf: QtGui.QRhiBuffer | None = None
        self._mesh_srb: QtGui.QRhiShaderResourceBindings | None = None

        self._bone_lines_vbuf: QtGui.QRhiBuffer | None = None

        self._normals_vbuf: QtGui.QRhiBuffer | None = None

        self._bone_points_renderer = PointRenderer(
            rhi_widget, cast(tuple[float, float, float], tuple(BONE_COLOR))
        )

    @property
    def mesh_data(self) -> ProcessedMeshData | None:
        """
        Returns the currently loaded mesh data.
        """
        return getattr(self, "_mesh_data", None)

    @mesh_data.setter
    def mesh_data(self, value: ProcessedMeshData | None):
        """
        Assigning None destroys the mesh and overlay GPU buffers.
        Sets the mesh data to be rendered.
        """
        self._mesh_data = value
        if value is None:
            if self._mesh_vbuf is not None:
                self._mesh_vbuf.destroy()
                self._mesh_vbuf = None
            if self._mesh_ibuf is not None:
                self._mesh_ibuf.destroy()
                self._mesh_ibuf = None
            if self._mesh_wireframe_ibuf is not None:
                self._mesh_wireframe_ibuf.destroy()
                self._mesh_wireframe_ibuf = None
            if self._bone_lines_vbuf is not None:
                self._bone_lines_vbuf.destroy()
                self._bone_lines_vbuf = None
            if self._normals_vbuf is not None:
                self._normals_vbuf.destroy()
                self._normals_vbuf = None
        else:
            self._bone_points_renderer.add_points(value.bone_positions, 5.0)

    def initialize(
        self,
        cb: QtGui.QRhiCommandBuffer,
        colored_vertices_shaders: tuple[QtGui.QShader, QtGui.QShader],
        point_shaders: tuple[QtGui.QShader, QtGui.QShader],
        mvp_ubuf: QtGui.QRhiBuffer,
        mvp_srb: QtGui.QRhiShaderResourceBindings,
    ):
        """
        Initialize the mesh renderer with graphics pipelines and resources.
        Sets up the rendering pipelines for mesh rendering, wireframe rendering,
        vertex line rendering, and bone point rendering. Creates necessary buffers,
        shader resource bindings, and uploads initial data to the GPU.
        Args:
            cb (QtGui.QRhiCommandBuffer): Command buffer for resource operations
            colored_vertices_shaders (tuple[QtGui.QShader, QtGui.QShader]):
                Vertex and fragment shaders for colored vertex rendering
            point_shaders (tuple[QtGui.QShader, QtGui.QShader]):
                Vertex and fragment shaders for point rendering
            mvp_ubuf (QtGui.QRhiBuffer): Model-view-projection uniform buffer
            mvp_srb (QtGui.QRhiShaderResourceBindings):
                Shader resource bindings for MVP matrix
        Note:
            This method should be called once during renderer setup. It creates
            multiple graphics pipelines:
            - Mesh pipeline for solid mesh rendering
            - Wireframe pipeline for mesh outline rendering
            - Vertex line pipeline for rendering vertex connections
            - Bone point pipeline for rendering bone positions
        """

        if self._rhi is None or self._rhi != self._rhi_widget.rhi():
            self.releaseResources()
            self._rhi = self._rhi_widget.rhi()

        shaders_path = os.path.join(get_application_path(), "data", "shaders")
        # Load through a local: the field is Optional, but the rest of this
        # method uses the narrowed non-None pair.
        mesh_shaders = self._mesh_shaders
        if mesh_shaders is None:
            with open(os.path.join(shaders_path, "mesh.vert.qsb"), "rb") as f:
                vsrc = QtGui.QShader.fromSerialized(f.read())
            with open(os.path.join(shaders_path, "mesh.frag.qsb"), "rb") as f:
                fsrc = QtGui.QShader.fromSerialized(f.read())
            mesh_shaders = (vsrc, fsrc)
            self._mesh_shaders = mesh_shaders

        if self._mesh_pipeline is None:
            self._mesh_vert_ubuf = self._rhi.newBuffer(
                QtGui.QRhiBuffer.Type.Dynamic,
                QtGui.QRhiBuffer.UsageFlag.UniformBuffer,
                2 * 16 * ctypes.sizeof(ctypes.c_float),
            )
            self._mesh_vert_ubuf.create()

            self._mesh_frag_ubuf = self._rhi.newBuffer(
                static_uniform_buffer_type(self._rhi_widget),
                QtGui.QRhiBuffer.UsageFlag.UniformBuffer,
                3 * ctypes.sizeof(ctypes.c_float),
            )
            self._mesh_frag_ubuf.create()

            self._mesh_srb = self._rhi.newShaderResourceBindings()
            self._mesh_srb.setBindings(
                [
                    QtGui.QRhiShaderResourceBinding.uniformBuffer(
                        0,
                        QtGui.QRhiShaderResourceBinding.StageFlag.VertexStage,
                        self._mesh_vert_ubuf,
                    ),
                    QtGui.QRhiShaderResourceBinding.uniformBuffer(
                        1,
                        QtGui.QRhiShaderResourceBinding.StageFlag.FragmentStage,
                        self._mesh_frag_ubuf,
                    ),
                ]
            )
            self._mesh_srb.create()

            self._mesh_pipeline = self._rhi.newGraphicsPipeline()
            self._mesh_pipeline.setShaderStages(
                [
                    QtGui.QRhiShaderStage(
                        QtGui.QRhiShaderStage.Type.Vertex, mesh_shaders[0]
                    ),
                    QtGui.QRhiShaderStage(
                        QtGui.QRhiShaderStage.Type.Fragment, mesh_shaders[1]
                    ),
                ]
            )
            input_layout = QtGui.QRhiVertexInputLayout()
            input_layout.setBindings(
                [
                    QtGui.QRhiVertexInputBinding(6 * ctypes.sizeof(ctypes.c_float)),
                ]
            )
            input_layout.setAttributes(
                [
                    QtGui.QRhiVertexInputAttribute(
                        0, 0, QtGui.QRhiVertexInputAttribute.Format.Float3, 0
                    ),
                    QtGui.QRhiVertexInputAttribute(
                        0,
                        1,
                        QtGui.QRhiVertexInputAttribute.Format.Float3,
                        3 * ctypes.sizeof(ctypes.c_float),
                    ),
                ]
            )
            self._mesh_pipeline.setDepthTest(True)
            self._mesh_pipeline.setDepthWrite(True)
            self._mesh_pipeline.setVertexInputLayout(input_layout)
            self._mesh_pipeline.setShaderResourceBindings(self._mesh_srb)
            self._mesh_pipeline.setRenderPassDescriptor(
                self._rhi_widget.renderTarget().renderPassDescriptor()
            )
            self._mesh_pipeline.create()

            self._mesh_wireframe_pipeline = self._rhi.newGraphicsPipeline()
            self._mesh_wireframe_pipeline.setShaderStages(
                [
                    QtGui.QRhiShaderStage(
                        QtGui.QRhiShaderStage.Type.Vertex, mesh_shaders[0]
                    ),
                    QtGui.QRhiShaderStage(
                        QtGui.QRhiShaderStage.Type.Fragment, mesh_shaders[1]
                    ),
                ]
            )
            input_layout = QtGui.QRhiVertexInputLayout()
            input_layout.setBindings(
                [
                    QtGui.QRhiVertexInputBinding(6 * ctypes.sizeof(ctypes.c_float)),
                ]
            )
            input_layout.setAttributes(
                [
                    QtGui.QRhiVertexInputAttribute(
                        0, 0, QtGui.QRhiVertexInputAttribute.Format.Float3, 0
                    ),
                    QtGui.QRhiVertexInputAttribute(
                        0,
                        1,
                        QtGui.QRhiVertexInputAttribute.Format.Float3,
                        3 * ctypes.sizeof(ctypes.c_float),
                    ),
                ]
            )
            self._mesh_wireframe_pipeline.setDepthTest(True)
            self._mesh_wireframe_pipeline.setDepthWrite(True)
            self._mesh_wireframe_pipeline.setVertexInputLayout(input_layout)
            self._mesh_wireframe_pipeline.setShaderResourceBindings(self._mesh_srb)
            self._mesh_wireframe_pipeline.setTopology(
                QtGui.QRhiGraphicsPipeline.Topology.Lines
            )
            self._mesh_wireframe_pipeline.setRenderPassDescriptor(
                self._rhi_widget.renderTarget().renderPassDescriptor()
            )
            self._mesh_wireframe_pipeline.create()

            # Direct3D must use dynamic uniform buffer
            if not is_d3d(self._rhi_widget):
                resource_updates = self._rhi.nextResourceUpdateBatch()
                arr = (ctypes.c_float * len(MESH_COLOR))(*MESH_COLOR)
                resource_updates.uploadStaticBuffer(
                    self._mesh_frag_ubuf, cast(int, arr)
                )
                cb.resourceUpdate(resource_updates)

        if self._vertex_line_pipeline is None:
            self._vertex_line_pipeline = self._rhi.newGraphicsPipeline()
            self._vertex_line_pipeline.setShaderStages(
                [
                    QtGui.QRhiShaderStage(
                        QtGui.QRhiShaderStage.Type.Vertex, colored_vertices_shaders[0]
                    ),
                    QtGui.QRhiShaderStage(
                        QtGui.QRhiShaderStage.Type.Fragment, colored_vertices_shaders[1]
                    ),
                ]
            )
            input_layout = QtGui.QRhiVertexInputLayout()
            input_layout.setBindings(
                [
                    QtGui.QRhiVertexInputBinding(6 * ctypes.sizeof(ctypes.c_float)),
                ]
            )
            input_layout.setAttributes(
                [
                    QtGui.QRhiVertexInputAttribute(
                        0, 0, QtGui.QRhiVertexInputAttribute.Format.Float3, 0
                    ),
                    QtGui.QRhiVertexInputAttribute(
                        0,
                        1,
                        QtGui.QRhiVertexInputAttribute.Format.Float3,
                        3 * ctypes.sizeof(ctypes.c_float),
                    ),
                ]
            )
            self._vertex_line_pipeline.setVertexInputLayout(input_layout)
            self._vertex_line_pipeline.setShaderResourceBindings(mvp_srb)
            self._vertex_line_pipeline.setTopology(
                QtGui.QRhiGraphicsPipeline.Topology.Lines
            )
            self._vertex_line_pipeline.setRenderPassDescriptor(
                self._rhi_widget.renderTarget().renderPassDescriptor()
            )
            self._vertex_line_pipeline.create()

        self._bone_points_renderer.initialize(cb, mvp_ubuf)

    def releaseResources(self):
        """
        Release all graphics pipeline resources held by the mesh renderer.
        This method cleans up GPU resources by setting all pipeline references to None,
        allowing them to be garbage collected. Should be called when the renderer is
        no longer needed or during application shutdown to prevent memory leaks.
        Note:
            After calling this method, the renderer will need to be reinitialized
            before it can be used again for rendering operations.
        """

        self._mesh_pipeline = None
        self._mesh_wireframe_pipeline = None
        self._vertex_line_pipeline = None

    def update_resources(
        self, resource_updates: QtGui.QRhiResourceUpdateBatch, camera: Camera
    ):
        """
        Updates GPU resources for mesh rendering including vertex/index buffers and uniform data.
        This method prepares and uploads mesh data to GPU buffers for rendering. It handles:
        - Uniform buffer updates with model-view and model-view-projection matrices
        - Creation and upload of vertex buffers for mesh geometry
        - Creation and upload of index buffers for face and wireframe rendering
        - Creation and upload of vertex buffers for bone visualization (lines and points)
        - Creation and upload of vertex buffers for normal vector visualization
        Args:
            resource_updates (QtGui.QRhiResourceUpdateBatch): Batch object for GPU resource updates
            camera (Camera): Camera object providing view and projection matrices
        Raises:
            RuntimeError: If RHI (Rendering Hardware Interface) is not initialized
        Notes:
            - Buffers are created only once and reused across frames
            - Mesh data must be available in self._mesh_data before calling this method
            - Bone and normal visualization data is optional and only processed if available
            - All vertex data is converted to float32 format for GPU compatibility
            - Index data is converted to uint32 format for GPU compatibility
        """

        if self._rhi is None:
            raise RuntimeError("RHI not initialized. Call initialize() first.")

        if self._mesh_data is not None and self._mesh_vert_ubuf is not None:
            mv = camera.view() * QtGui.QMatrix4x4()
            mvp = self._rhi.clipSpaceCorrMatrix() * camera.proj() * mv

            ubuf_data = mv.data() + mvp.data()
            ubuf_arr = (ctypes.c_float * len(ubuf_data))(*ubuf_data)
            resource_updates.updateDynamicBuffer(
                self._mesh_vert_ubuf,
                0,
                2 * 16 * ctypes.sizeof(ctypes.c_float),
                cast(int, ubuf_arr),
            )

            if self._mesh_vbuf is None:
                vbuf_data = self._mesh_data.vertices.flatten().astype("f4").tolist()

                # Create vertex and index buffers for the mesh
                self._mesh_vbuf = self._rhi.newBuffer(
                    QtGui.QRhiBuffer.Type.Immutable,
                    QtGui.QRhiBuffer.UsageFlag.VertexBuffer,
                    ctypes.sizeof(ctypes.c_float) * len(vbuf_data),
                )
                self._mesh_vbuf.create()

                vbuf_arr = (ctypes.c_float * len(vbuf_data))(*vbuf_data)
                resource_updates.uploadStaticBuffer(
                    self._mesh_vbuf, cast(int, vbuf_arr)
                )

            if self._mesh_ibuf is None:
                ibuf_data = self._mesh_data.indices.flatten().astype("uint32").tolist()

                self._mesh_ibuf = self._rhi.newBuffer(
                    QtGui.QRhiBuffer.Type.Immutable,
                    QtGui.QRhiBuffer.UsageFlag.IndexBuffer,
                    ctypes.sizeof(ctypes.c_uint) * len(ibuf_data),
                )
                self._mesh_ibuf.create()

                ibuf_arr = (ctypes.c_uint * len(ibuf_data))(*ibuf_data)

                resource_updates.uploadStaticBuffer(
                    self._mesh_ibuf, cast(int, ibuf_arr)
                )

            if self._mesh_wireframe_ibuf is None:
                wireframe_ibuf_data = (
                    self._mesh_data.wireframe_indices.flatten()
                    .astype("uint32")
                    .tolist()
                )

                self._mesh_wireframe_ibuf = self._rhi.newBuffer(
                    QtGui.QRhiBuffer.Type.Immutable,
                    QtGui.QRhiBuffer.UsageFlag.IndexBuffer,
                    ctypes.sizeof(ctypes.c_uint) * len(wireframe_ibuf_data),
                )
                self._mesh_wireframe_ibuf.create()

                wireframe_ibuf_arr = (ctypes.c_uint * len(wireframe_ibuf_data))(
                    *wireframe_ibuf_data
                )

                resource_updates.uploadStaticBuffer(
                    self._mesh_wireframe_ibuf, cast(int, wireframe_ibuf_arr)
                )

            if self._bone_lines_vbuf is None:
                # Create vertex buffer for bones
                if self._mesh_data.bone_lines:
                    bone_data = []
                    # Create bone vertex data with BONE_COLOR
                    bone_vertices = np.array(self._mesh_data.bone_lines).reshape(-1, 3)
                    bone_colors = np.tile(BONE_COLOR, (len(bone_vertices), 1))
                    bone_data = (
                        np.column_stack([bone_vertices, bone_colors]).flatten().tolist()
                    )

                    self._bone_lines_vbuf = self._rhi.newBuffer(
                        QtGui.QRhiBuffer.Type.Immutable,
                        QtGui.QRhiBuffer.UsageFlag.VertexBuffer,
                        ctypes.sizeof(ctypes.c_float) * len(bone_data),
                    )
                    self._bone_lines_vbuf.create()

                    bone_arr = (ctypes.c_float * len(bone_data))(*bone_data)
                    resource_updates.uploadStaticBuffer(
                        self._bone_lines_vbuf, cast(int, bone_arr)
                    )

            if self._normals_vbuf is None:
                # Create vertex buffer for normals
                normals_vertices = np.array(self._mesh_data.normal_lines).reshape(-1, 3)
                normals_color = np.tile(NORMALS_COLOR, (len(normals_vertices), 1))
                normals_data = (
                    np.column_stack([normals_vertices, normals_color])
                    .flatten()
                    .tolist()
                )
                self._normals_vbuf = self._rhi.newBuffer(
                    QtGui.QRhiBuffer.Type.Immutable,
                    QtGui.QRhiBuffer.UsageFlag.VertexBuffer,
                    ctypes.sizeof(ctypes.c_float) * len(normals_data),
                )
                self._normals_vbuf.create()

                normals_arr = (ctypes.c_float * len(normals_data))(*normals_data)
                resource_updates.uploadStaticBuffer(
                    self._normals_vbuf, cast(int, normals_arr)
                )

        # Direct3D must use dynamic uniform buffer
        if is_d3d(self._rhi_widget):
            if self._mesh_vbuf is not None and self._mesh_frag_ubuf is not None:
                arr = (ctypes.c_float * len(MESH_COLOR))(*MESH_COLOR)
                resource_updates.updateDynamicBuffer(
                    self._mesh_frag_ubuf, 0, ctypes.sizeof(arr), cast(int, arr)
                )

        self._bone_points_renderer.update_resources(resource_updates)

    def render(self, cb: QtGui.QRhiCommandBuffer):
        """
        Renders the mesh using the provided QRhi command buffer.
        This method handles rendering of the main mesh geometry in either solid or wireframe mode,
        optionally drawing bone structures and vertex normals for debugging/visualization purposes.
        Args:
            cb (QtGui.QRhiCommandBuffer): The QRhi command buffer used for recording rendering commands.
        Behavior:
            - Sets up viewport based on the render target's pixel size
            - Renders mesh in wireframe or solid mode based on wireframe_mode flag
            - Optionally renders bone lines and points if draw_bones is enabled
            - Optionally renders vertex normals if draw_normals is enabled
            - Each rendering pass configures the appropriate graphics pipeline, viewport, and vertex input
        Note:
            Rendering only occurs if mesh_data, mesh_pipeline, and mesh_wireframe_pipeline are all available.
            Bone and normal rendering require their respective pipelines and vertex buffers to be initialized.
        """

        if (
            self._mesh_data is None
            or self._mesh_pipeline is None
            or self._mesh_wireframe_pipeline is None
            or self._mesh_vbuf is None
            or self._normals_vbuf is None
        ):
            return

        viewport = QtGui.QRhiViewport(
            0,
            0,
            self._rhi_widget.renderTarget().pixelSize().width(),
            self._rhi_widget.renderTarget().pixelSize().height(),
        )

        if self.wireframe_mode:
            cb.setGraphicsPipeline(self._mesh_wireframe_pipeline)
        else:
            cb.setGraphicsPipeline(self._mesh_pipeline)
        cb.setViewport(viewport)
        cb.setShaderResources()
        if self.wireframe_mode:
            cb.setVertexInput(
                0,
                [(self._mesh_vbuf, 0)],
                self._mesh_wireframe_ibuf,
                0,
                QtGui.QRhiCommandBuffer.IndexFormat.IndexUInt32,
            )
            cb.drawIndexed(self._mesh_data.wireframe_indices.size)
        else:
            cb.setVertexInput(
                0,
                [(self._mesh_vbuf, 0)],
                self._mesh_ibuf,
                0,
                QtGui.QRhiCommandBuffer.IndexFormat.IndexUInt32,
            )
            cb.drawIndexed(self._mesh_data.indices.size)

        if self.draw_bones:
            if self._bone_lines_vbuf and self._vertex_line_pipeline is not None:
                cb.setGraphicsPipeline(self._vertex_line_pipeline)
                cb.setViewport(viewport)
                cb.setShaderResources()
                cb.setVertexInput(0, [(self._bone_lines_vbuf, 0)])
                cb.draw(len(self._mesh_data.bone_lines))

            self._bone_points_renderer.render(cb)

        if self.draw_normals and self._vertex_line_pipeline is not None:
            cb.setGraphicsPipeline(self._vertex_line_pipeline)
            cb.setViewport(viewport)
            cb.setShaderResources()
            cb.setVertexInput(0, [(self._normals_vbuf, 0)])
            cb.draw(len(self._mesh_data.normal_lines))
