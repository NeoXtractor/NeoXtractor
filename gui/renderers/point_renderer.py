"""Provides a point renderer that compatible with both Vulkan/OpenGL and Direct3D."""

import os
import ctypes
from typing import cast

import numpy as np
from PySide6 import QtGui, QtWidgets

from core.utils import get_application_path
from gui.utils.rendering import is_d3d, static_uniform_buffer_type

class PointRenderer:
    """
    PointRenderer is a class responsible for rendering 3D points in a Qt RHI (QRhi) context,
    supporting both Direct3D and other graphics backends.
    Methods:
        __init__(rhi_widget, point_color): Initializes the PointRenderer with a QRhiWidget and optional point color.
        add_points(points, point_size=None): Adds points to the renderer, optionally specifying a point size.
        clear_points(): Removes all points from the renderer.
        initialize(cb, mvp_ubuf): Initializes the renderer's resources and pipeline.
        update_resources(resource_updates): Updates GPU resources with the latest point and color data.
        render(cb): Issues draw commands to render the points.
    Usage:
        - Create an instance of PointRenderer with a QRhiWidget.
        - Use add_points() to add points to be rendered.
        - Call initialize() before rendering.
        - Call update_resources() and render() within the rendering loop.
    """

    def __init__(self, rhi_widget: QtWidgets.QRhiWidget, point_color: tuple[float, float, float] = (1.0, 1.0, 1.0)):
        self._is_d3d: bool

        self._rhi_widget = rhi_widget
        self._point_color = point_color

        self._points = []
        self._new_points = []

        self._rhi: QtGui.QRhi | None = None

        self._pipeline: QtGui.QRhiGraphicsPipeline | None = None

        self._shaders: tuple[QtGui.QShader, QtGui.QShader] | None = None

        self._vert_ubuf: QtGui.QRhiBuffer | None = None
        self._frag_ubuf: QtGui.QRhiBuffer | None = None
        self._vbuf: QtGui.QRhiBuffer | None = None
        self._ibuf: QtGui.QRhiBuffer | None = None
        self._srb: QtGui.QRhiShaderResourceBindings | None = None

    def _generate_quad_indices(self, point_count: int) -> list[int]:
        """
        Generate indices for quad rendering ahead of time.
        Each point creates a quad with 2 triangles (6 indices).
        """
        indices = []
        for i in range(point_count):
            base_vertex = i * 4
            # Triangle 1: 0,1,2  Triangle 2: 0,2,3 (counter-clockwise)
            indices.extend([
                base_vertex + 0, base_vertex + 1, base_vertex + 2,
                base_vertex + 0, base_vertex + 2, base_vertex + 3
            ])
        return indices

    def _points_to_quads(self, points: list[tuple[int, int, int, int]], screen_width, screen_height):
        """
        Convert point data to quad vertices for Direct3D rendering.
        """
        pnts = np.array(points)
        num_points = len(pnts)

        # Convert pixel sizes to NDC coordinates
        min_screen_dim = min(screen_width, screen_height)
        ndc_sizes = pnts[:, 3] / min_screen_dim

        # Create quad vertices (4 vertices per point)
        vertices = np.zeros((num_points * 4, 6), dtype=np.float32)

        for i in range(num_points):
            point = pnts[i]
            pos = point[:3]
            size = ndc_sizes[i]

            offsets = np.array([[-1,-1],[1,-1],[1,1],[-1,1]], dtype=np.float32)
            for j in range(4):
                idx = i * 4 + j
                vertices[idx, :3]      = pos
                vertices[idx, 3]       = size
                vertices[idx, 4:6]     = offsets[j]

        return vertices

    def add_points(self, points, point_size: float | None = None):
        """
        Add points to the renderer.

        :param points: A list of points, where each point is a tuple of (x, y, z).
        :param point_size: The size of the points.
        """

        if self._new_points is None:
            # No new points currently, and we (may) have existing points
            self._new_points = []
            self._new_points.extend(self._points)

        if point_size is None:
            self._new_points.extend(points)
            return

        points_with_size = np.column_stack([points, np.full(len(points), point_size)])
        self._new_points.extend(points_with_size.tolist())

    def clear_points(self):
        """
        Clear all points from the renderer.
        """
        self._points = []
        self._new_points = []

    def initialize(self, cb: QtGui.QRhiCommandBuffer,
                   mvp_ubuf: QtGui.QRhiBuffer):
        """
        Initialize the renderer.

        :param cb: The command buffer to use for rendering.
        """

        if self._rhi is None or self._rhi != self._rhi_widget.rhi():
            self._rhi = self._rhi_widget.rhi()

        self._is_d3d = is_d3d(self._rhi_widget)

        if self._shaders is None:
            shaders_path = os.path.join(get_application_path(), "data", "shaders")
            shader_name = "point_d3d" if self._is_d3d else "point"
            with open(os.path.join(shaders_path, f"{shader_name}.vert.qsb"), "rb") as f:
                vsrc = f.read()
                with open(os.path.join(shaders_path, f"{shader_name}.frag.qsb"), "rb") as f:
                    fsrc = f.read()
                    self._shaders = (
                        QtGui.QShader.fromSerialized(vsrc),
                        QtGui.QShader.fromSerialized(fsrc)
                    )

        if self._pipeline is None:
            if self._is_d3d:
                self._vert_ubuf = self._rhi.newBuffer(QtGui.QRhiBuffer.Type.Dynamic,
                                                      QtGui.QRhiBuffer.UsageFlag.UniformBuffer,
                                                      1 * ctypes.sizeof(ctypes.c_float)
                                                      )
                self._vert_ubuf.create()

            self._frag_ubuf = self._rhi.newBuffer(static_uniform_buffer_type(self._rhi_widget),
                                                         QtGui.QRhiBuffer.UsageFlag.UniformBuffer,
                                                         3 * ctypes.sizeof(ctypes.c_float)
                                                     )
            self._frag_ubuf.create()

            self._srb = self._rhi.newShaderResourceBindings()
            bindings = [
                QtGui.QRhiShaderResourceBinding.uniformBuffer(0,
                                                            QtGui.QRhiShaderResourceBinding.StageFlag.VertexStage,
                                                            mvp_ubuf),
                QtGui.QRhiShaderResourceBinding.uniformBuffer(1,
                                                            QtGui.QRhiShaderResourceBinding.StageFlag.FragmentStage,
                                                            self._frag_ubuf),
            ]
            if self._is_d3d:
                bindings.append(
                    QtGui.QRhiShaderResourceBinding.uniformBuffer(2,
                                                                  QtGui.QRhiShaderResourceBinding.StageFlag.VertexStage,
                                                                  cast(QtGui.QRhiBuffer, self._vert_ubuf))
                )
            self._srb.setBindings(bindings)
            self._srb.create()

            self._pipeline = self._rhi.newGraphicsPipeline()
            self._pipeline.setShaderStages([
                QtGui.QRhiShaderStage(QtGui.QRhiShaderStage.Type.Vertex, self._shaders[0]),
                QtGui.QRhiShaderStage(QtGui.QRhiShaderStage.Type.Fragment, self._shaders[1])
            ])
            input_layout = QtGui.QRhiVertexInputLayout()
            input_layout.setBindings([
                QtGui.QRhiVertexInputBinding((6 if self._is_d3d else 4) * ctypes.sizeof(ctypes.c_float)),
            ])
            input_attributes = [
                QtGui.QRhiVertexInputAttribute(0, 0, QtGui.QRhiVertexInputAttribute.Format.Float3, 0),
                QtGui.QRhiVertexInputAttribute(0, 1, QtGui.QRhiVertexInputAttribute.Format.Float,
                                               3 * ctypes.sizeof(ctypes.c_float))
            ]
            if self._is_d3d:
                input_attributes.append(
                    QtGui.QRhiVertexInputAttribute(0, 2, QtGui.QRhiVertexInputAttribute.Format.Float2,
                                               4 * ctypes.sizeof(ctypes.c_float))
                )
            input_layout.setAttributes(input_attributes)
            self._pipeline.setVertexInputLayout(input_layout)
            self._pipeline.setShaderResourceBindings(self._srb)
            if not self._is_d3d:
                self._pipeline.setTopology(QtGui.QRhiGraphicsPipeline.Topology.Points)
            self._pipeline.setRenderPassDescriptor(self._rhi_widget.renderTarget().renderPassDescriptor())
            self._pipeline.create()

            # Direct 3D needs dynamic uniform buffer
            if not self._is_d3d:
                resource_updates = self._rhi.nextResourceUpdateBatch()
                arr = (ctypes.c_float * len(self._point_color))(*self._point_color)
                resource_updates.uploadStaticBuffer(self._frag_ubuf, cast(int, arr))
                cb.resourceUpdate(resource_updates)

    def update_resources(self, resource_updates: QtGui.QRhiResourceUpdateBatch):
        """
        Update the resources for the renderer.

        :param resource_updates: The resource update batch.
        """
        if self._rhi is None:
            return

        if self._is_d3d:
            if self._vert_ubuf is not None:
                viewport = self._rhi_widget.renderTarget().pixelSize()
                arr = (ctypes.c_float * 1)(viewport.width() / viewport.height())
                resource_updates.updateDynamicBuffer(self._vert_ubuf, 0, ctypes.sizeof(arr), cast(int, arr))

            if self._frag_ubuf is not None:
                arr = (ctypes.c_float * len(self._point_color))(*self._point_color)
                resource_updates.updateDynamicBuffer(self._frag_ubuf, 0, ctypes.sizeof(arr), cast(int, arr))

        if self._new_points is not None:
            for name in ("_vbuf", "_ibuf"):
                buffer = getattr(self, name)
                if buffer is not None:
                    buffer.destroy()
                    setattr(self, name, None)
            point_count = len(self._new_points)
            if point_count == 0:
                self._points = []
                self._new_points = None
                return

            self._vbuf = self._rhi.newBuffer(QtGui.QRhiBuffer.Type.Immutable,
                                             QtGui.QRhiBuffer.UsageFlag.VertexBuffer,
                                             (4 * 6 if self._is_d3d else 4) * point_count * \
                                                ctypes.sizeof(ctypes.c_float)
                                             )
            self._vbuf.create()

            if self._is_d3d:
                self._ibuf = self._rhi.newBuffer(QtGui.QRhiBuffer.Type.Immutable,
                                                QtGui.QRhiBuffer.UsageFlag.IndexBuffer,
                                                6 * point_count * ctypes.sizeof(ctypes.c_int))
                self._ibuf.create()

            vbuf_data = []
            if self._is_d3d:
                size = self._rhi_widget.renderTarget().pixelSize()
                (width, height) = (size.width(), size.height())
                vertices = self._points_to_quads(self._new_points, width, height)
                vbuf_data.extend(vertices.flatten().tolist())
            else:
                for point in self._new_points:
                    vbuf_data.extend(point)
            arr = (ctypes.c_float * len(vbuf_data))(*vbuf_data)
            resource_updates.uploadStaticBuffer(self._vbuf, cast(int, arr))

            if self._is_d3d:
                indices = self._generate_quad_indices(point_count)
                arr = (ctypes.c_int * len(indices))(*indices)
                resource_updates.uploadStaticBuffer(cast(QtGui.QRhiBuffer, self._ibuf), cast(int, arr))

            self._points = self._new_points
            self._new_points = None

    def render(self, cb: QtGui.QRhiCommandBuffer):
        """
        Renders points using the configured graphics pipeline and vertex buffer.
        Args:
            cb (QtGui.QRhiCommandBuffer): The command buffer used to record rendering commands.
        Returns:
            None
        This method sets up the graphics pipeline, viewport, and shader resources, then issues draw commands
        for rendering points. If running on Direct3D (`_is_d3d` is True), it uses indexed drawing; otherwise,
        it uses non-indexed drawing. Rendering is skipped if the pipeline or vertex buffer is not initialized.
        """

        if self._pipeline is None or self._vbuf is None or not self._points:
            return

        cb.setGraphicsPipeline(self._pipeline)
        cb.setViewport(QtGui.QRhiViewport(0, 0, self._rhi_widget.renderTarget().pixelSize().width(),
                                                    self._rhi_widget.renderTarget().pixelSize().height()))
        cb.setShaderResources()
        if self._is_d3d:
            cb.setVertexInput(0, [(self._vbuf, 0)], self._ibuf, 0, QtGui.QRhiCommandBuffer.IndexFormat.IndexUInt32)
            cb.drawIndexed(len(self._points) * 6)
        else:
            cb.setVertexInput(0, [(self._vbuf, 0)])
            cb.draw(len(self._points))
