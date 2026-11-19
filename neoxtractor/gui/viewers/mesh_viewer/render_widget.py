"""Mesh Viewer Widget"""

from __future__ import annotations

import ctypes
import os
import sys
from typing import TYPE_CHECKING, cast, overload

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from neoxtractor.core import processors
from neoxtractor.core.utils.app import get_application_path
from neoxtractor.gui.renderers.mesh_renderer import MeshRenderer, ProcessedMeshData
from neoxtractor.gui.renderers.point_renderer import PointRenderer
from neoxtractor.gui.renderers.text_renderer import TextRenderer
from neoxtractor.gui.utils.rendering import grid
from neoxtractor.gui.widgets.managed_rhi_widget import ManagedRhiWidget

from .camera_controller import CameraController

if TYPE_CHECKING:
    # Canonical mesh IR is named through the public resource facade; runtime
    # dispatch here keys on bytes-like input, never on this type.
    from neoxtractor.core.resources.mesh import MeshData

# On-screen shortcut legend.  Second column is a plain label, or a
# (normal, alternative) pair toggled by the Alt/Ctrl modifier in render().
INSTRUCTIONS: list[tuple[str, str | tuple[str, str]]] = [
    ("Key F", "Focus Object"),
    ("M-Right", "Orbit"),
    ("M-Left", "Pan"),
    ("M-Middle", "Dolly"),
    ("W, A, S, D", "Move Camera"),
    ("Shift", "Sprint"),
    ("Key 1", ("Front View", "Back View")),
    ("Key 3", ("Right View", "Left View")),
    ("Key 7", ("Top View", "Bottom View")),
    ((sys.platform == "darwin" and "Option" or "Ctrl"), "Alternative Actions"),
]

GRID_COLOR = [0.3, 0.3, 0.3]
GRID_CELL_METERS = 1.525
GRID_CELLS_PER_SIDE = 10

GRID_VERTEX_DATA = [
    float(coord)
    for grid_line in cast(
        np.ndarray,
        grid(
            GRID_CELL_METERS * GRID_CELLS_PER_SIDE / 2,
            GRID_CELLS_PER_SIDE + 1,
            cell=GRID_CELL_METERS,
        ),
    )
    for grid_vertex in grid_line
    for coord in [*grid_vertex, *GRID_COLOR]
]

REF_POINT_COLOR = [1.0, 0.0, 0.0]


class MeshRenderWidget(ManagedRhiWidget, CameraController):
    """A Qt widget for rendering 3D meshes with bones and normals visualization.
    This widget provides an interactive 3D mesh viewer using Qt's RHI (Render Hardware Interface)
    for cross-platform GPU rendering. It supports rendering meshes with optional bone structure
    visualization and surface normals display.
    Features:
        - 3D mesh rendering with solid or wireframe modes
        - Bone structure visualization with lines and points
        - Surface normals display as colored lines
        - Interactive camera controls (inherited from CameraController)
        - Grid and reference point display
        - Text overlay for keyboard shortcuts
        - Hardware-accelerated rendering via Qt RHI
    Attributes:
        draw_bones (bool): Whether to render bone structure visualization
        draw_normals (bool): Whether to render surface normals as lines
        wireframe_mode (bool): Whether to render meshes in wireframe mode
    The widget automatically handles GPU resource management, shader compilation,
    and render pipeline setup. Mesh data can be loaded from MeshData objects or
    raw bytes using the load_mesh() method.
    """

    def __init__(self, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)

        self.draw_text = True

        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)

        self._rhi: QtGui.QRhi | None = None

        self._colored_vertices_shaders: tuple[QtGui.QShader, QtGui.QShader] | None = (
            None
        )
        self._point_shaders: tuple[QtGui.QShader, QtGui.QShader] | None = None

        self._grid_pipeline: QtGui.QRhiGraphicsPipeline | None = None

        self._grid_vbuf: QtGui.QRhiBuffer | None = None
        self._mvp_ubuf: QtGui.QRhiBuffer | None = None
        self._mvp_srb: QtGui.QRhiShaderResourceBindings | None = None

        self._ref_point_renderer: PointRenderer = PointRenderer(
            self, cast(tuple[float, float, float], tuple(REF_POINT_COLOR))
        )
        self._mesh_renderer = MeshRenderer(self)

        self._text_renderer = TextRenderer(self, 14)

        self._ref_point_renderer.add_points([(0.0, 0.0, 0.0, 5.0)])

        self._alternative_actions = False

    @property
    def wireframe_mode(self) -> bool:
        """Get the current wireframe mode state."""
        return self._mesh_renderer.wireframe_mode

    @wireframe_mode.setter
    def wireframe_mode(self, value: bool):
        """Set the wireframe mode state."""
        self._mesh_renderer.wireframe_mode = value

    @property
    def draw_bones(self) -> bool:
        """Get whether bone visualization is enabled."""
        return self._mesh_renderer.draw_bones

    @draw_bones.setter
    def draw_bones(self, value: bool):
        """Set whether to draw bones in the mesh."""
        self._mesh_renderer.draw_bones = value

    @property
    def draw_normals(self) -> bool:
        """Get whether normals visualization is enabled."""
        return self._mesh_renderer.draw_normals

    @draw_normals.setter
    def draw_normals(self, value: bool):
        """Set whether to draw normals in the mesh."""
        self._mesh_renderer.draw_normals = value

    def initialize(self, cb: QtGui.QRhiCommandBuffer):
        if self._rhi != self.rhi() or self._rhi is None:  # type hint
            self._grid_pipeline = None
            self._text_renderer.releaseResources()
            self._rhi = self.rhi()

        shaders_path = os.path.join(get_application_path(), "data", "shaders")
        # Locals hold the narrowed non-None shaders: narrowing an Optional
        # field does not survive intervening method calls.
        cv_shaders = self._colored_vertices_shaders
        if cv_shaders is None:
            with open(
                os.path.join(shaders_path, "colored_vertices.vert.qsb"), "rb"
            ) as f:
                vsrc = QtGui.QShader.fromSerialized(f.read())
            with open(
                os.path.join(shaders_path, "colored_vertices.frag.qsb"), "rb"
            ) as f:
                fsrc = QtGui.QShader.fromSerialized(f.read())
            cv_shaders = (vsrc, fsrc)
            self._colored_vertices_shaders = cv_shaders

        pt_shaders = self._point_shaders
        if pt_shaders is None:
            with open(os.path.join(shaders_path, "point.vert.qsb"), "rb") as f:
                vsrc = QtGui.QShader.fromSerialized(f.read())
            with open(os.path.join(shaders_path, "point.frag.qsb"), "rb") as f:
                fsrc = QtGui.QShader.fromSerialized(f.read())
            pt_shaders = (vsrc, fsrc)
            self._point_shaders = pt_shaders

        if self._mvp_ubuf is None or self._mvp_srb is None:
            self._mvp_ubuf = self._rhi.newBuffer(
                QtGui.QRhiBuffer.Type.Dynamic,
                QtGui.QRhiBuffer.UsageFlag.UniformBuffer,
                16 * ctypes.sizeof(ctypes.c_float),
            )
            self._mvp_ubuf.create()

            self._mvp_srb = self._rhi.newShaderResourceBindings()
            self._mvp_srb.setBindings(
                [
                    QtGui.QRhiShaderResourceBinding.uniformBuffer(
                        0,
                        QtGui.QRhiShaderResourceBinding.StageFlag.VertexStage,
                        self._mvp_ubuf,
                    ),
                ]
            )
            self._mvp_srb.create()

        if self._grid_pipeline is None:
            self._grid_vbuf = self._rhi.newBuffer(
                QtGui.QRhiBuffer.Type.Immutable,
                QtGui.QRhiBuffer.UsageFlag.VertexBuffer,
                ctypes.sizeof(ctypes.c_float) * len(GRID_VERTEX_DATA),
            )
            self._grid_vbuf.create()

            self._grid_pipeline = self._rhi.newGraphicsPipeline()
            self._grid_pipeline.setShaderStages(
                [
                    QtGui.QRhiShaderStage(
                        QtGui.QRhiShaderStage.Type.Vertex,
                        cv_shaders[0],
                    ),
                    QtGui.QRhiShaderStage(
                        QtGui.QRhiShaderStage.Type.Fragment,
                        cv_shaders[1],
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
            self._grid_pipeline.setDepthTest(True)
            self._grid_pipeline.setDepthWrite(True)
            self._grid_pipeline.setVertexInputLayout(input_layout)
            self._grid_pipeline.setShaderResourceBindings(self._mvp_srb)
            self._grid_pipeline.setTopology(QtGui.QRhiGraphicsPipeline.Topology.Lines)
            self._grid_pipeline.setRenderPassDescriptor(
                self.renderTarget().renderPassDescriptor()
            )
            self._grid_pipeline.create()

            resource_updates = self._rhi.nextResourceUpdateBatch()
            arr = (ctypes.c_float * len(GRID_VERTEX_DATA))(*GRID_VERTEX_DATA)
            resource_updates.uploadStaticBuffer(self._grid_vbuf, cast(int, arr))
            cb.resourceUpdate(resource_updates)

        self._ref_point_renderer.initialize(cb, self._mvp_ubuf)
        self._mesh_renderer.initialize(
            cb,
            cv_shaders,
            pt_shaders,
            self._mvp_ubuf,
            self._mvp_srb,
        )
        self._text_renderer.initialize(cb)

        output_size = self.renderTarget().pixelSize()
        self.camera.set_aspect_ratio(output_size.width(), output_size.height())

    # PySide6 stub defect: the .pyi models QRhiWidget.render(cb) and the
    # inherited QWidget.render overload set under one name, so any render(cb)
    # override is flagged. Our signature is not wrong; not fixable from our side.
    def render(self, cb: QtGui.QRhiCommandBuffer):  # type: ignore[override]
        self._camera_update()

        if (
            self._rhi is None
            or self._mvp_ubuf is None
            or self._grid_pipeline is None
            or self._grid_vbuf is None
        ):
            return

        viewport_height = self.renderTarget().pixelSize().height()

        # Calculate starting Y position from bottom
        start_y = (
            viewport_height - (len(INSTRUCTIONS) * self._text_renderer.font_height) - 20
        )

        for i, (key, action) in enumerate(INSTRUCTIONS):
            if isinstance(action, tuple):
                if self._alternative_actions:
                    action = action[1]
                else:
                    action = action[0]
            y_pos = start_y + i * self._text_renderer.font_height
            self._text_renderer.render_text(
                f"{key}:", (20, y_pos), (0.5, 1.0, 1.0, 1.0)
            )
            self._text_renderer.render_text(action, (90, y_pos), (1.0, 1.0, 1.0, 1.0))

        if self._mesh_renderer.mesh_data is not None:
            mesh_info = [
                ("Version", self._mesh_renderer.mesh_data.raw_data.version),
                ("Type", self._mesh_renderer.mesh_data.raw_data.type),
                ("Bones", self._mesh_renderer.mesh_data.bone_count),
                ("Vertices", self._mesh_renderer.mesh_data.vertex_count),
                ("Triangles", self._mesh_renderer.mesh_data.triangle_count),
            ]

            for i, (label, value) in enumerate(mesh_info):
                y_pos = 20 + i * self._text_renderer.font_height
                self._text_renderer.render_text(
                    f"{label}:", (20, y_pos), (0.5, 1.0, 1.0, 1.0)
                )
                self._text_renderer.render_text(
                    str(value), (90, y_pos), (1.0, 1.0, 1.0, 1.0)
                )

        resource_updates = self._rhi.nextResourceUpdateBatch()

        # Update view-projection matrix from camera
        view_proj = self._rhi.clipSpaceCorrMatrix()
        view_proj = view_proj * self.camera.view_proj()

        vp_data = view_proj.data()
        arr = (ctypes.c_float * len(vp_data))(*vp_data)
        resource_updates.updateDynamicBuffer(
            self._mvp_ubuf, 0, ctypes.sizeof(arr), cast(int, arr)
        )

        self._ref_point_renderer.update_resources(resource_updates)
        self._mesh_renderer.update_resources(resource_updates, self.camera)
        if self.draw_text:
            self._text_renderer.update_resources(resource_updates)
        else:
            self._text_renderer.clear_queue()

        clr = QtGui.QColor.fromRgbF(0.23, 0.23, 0.23)
        cb.beginPass(
            self.renderTarget(),
            clr,
            QtGui.QRhiDepthStencilClearValue(1, 0),
            resource_updates,
        )

        viewport = QtGui.QRhiViewport(
            0,
            0,
            self.renderTarget().pixelSize().width(),
            self.renderTarget().pixelSize().height(),
        )

        cb.setGraphicsPipeline(self._grid_pipeline)
        cb.setViewport(viewport)
        cb.setShaderResources()
        cb.setVertexInput(0, [(self._grid_vbuf, 0)])
        cb.draw(
            len(GRID_VERTEX_DATA) // 6
        )  # 6 floats per vertex (3 for position, 3 for color)

        self._ref_point_renderer.render(cb)
        self._mesh_renderer.render(cb)
        self._text_renderer.render(cb)

        cb.endPass()

        self.update()

    def focus_mesh(self):
        """Focus the camera on the currently loaded mesh data.
        This method adjusts the camera position and orientation to ensure that the
        entire mesh fits within the view. It calculates the ideal distance based on
        the mesh size and camera field of view, and sets the camera to an orthogonal
        view from the front.
        If no mesh data is loaded, it resets the camera focus to the default position.
        Note:
            This method should be called after loading a mesh to ensure it is properly centered
            and visible in the viewer.
        """

        if self.mesh_data is not None:
            self.camera.focus(self.mesh_data.center.tolist())
            fov_radians = np.radians(self.camera.fov_y)

            # Ensure full object fits in view
            ideal_distance = self.mesh_data.size / np.sin(fov_radians / 2)

            # Adjust camera distance to ensure object fits in view
            self.camera.dist = max(
                self.camera.min_dist, min(ideal_distance, self.camera.max_dist)
            )
        else:
            self.camera.focus()
        # self.camera.orthogonal(OrthogonalDirection.FRONT)
        # <aex> this makes viewing experience hell, keep the original user camera viewpos

    def mousePressEvent(self, event: QtGui.QMouseEvent):
        super()._camera_mouse_pressed_event(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent):
        super()._camera_mouse_released_event(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent):
        super()._camera_mouse_moved_event(event)

    def wheelEvent(self, event: QtGui.QWheelEvent):
        super()._camera_wheel_event(event)

    def keyPressEvent(self, event: QtGui.QKeyEvent):
        if event.key() == QtCore.Qt.Key.Key_Control:
            self._alternative_actions = True
        elif event.key() == QtCore.Qt.Key.Key_F:
            self.focus_mesh()
        super()._camera_key_pressed_event(event)

    def keyReleaseEvent(self, event: QtGui.QKeyEvent):
        if event.key() == QtCore.Qt.Key.Key_Control:
            self._alternative_actions = False
        super()._camera_key_released_event(event)

    @property
    def mesh_data(self) -> ProcessedMeshData | None:
        """Get the currently loaded mesh data."""
        return self._mesh_renderer.mesh_data

    def unload_mesh(self):
        """
        Unloads the currently loaded mesh by destroying GPU buffers and clearing mesh data.
        This method safely cleans up mesh resources by:
        - Destroying the index buffer if it exists
        - Destroying the vertex buffer if it exists
        - Clearing the mesh data reference
        If no mesh is currently loaded, this method returns early without performing any operations.
        """
        self._mesh_renderer.mesh_data = None
        self._mesh_renderer._bone_points_renderer.clear_points()

    @overload
    def load_mesh(self, data: MeshData) -> None: ...

    @overload
    def load_mesh(self, data: bytes) -> None: ...

    def load_mesh(self, data: MeshData | bytes) -> None:
        """Load mesh data into the viewer widget.
        Args:
            data: Either a MeshData object or raw bytes containing mesh data.
                  Raw bytes are decoded through the processors facade
                  (``read(..., format="mesh")``); a MeshData is used directly.
        Raises:
            ValueError: If the provided bytes cannot be loaded as valid mesh data.
        Note:
            The loaded mesh data is stored internally as ProcessedMeshData for
            rendering purposes.
        """

        self.unload_mesh()

        # Dispatch on the bytes-like input (a reliable nominal type) rather
        # than testing ``isinstance(data, MeshData)`` -- that would drag the
        # private ``_mesh`` package into runtime.  Raw bytes are decoded
        # through the processors facade; an already-canonical MeshData is
        # used directly.
        if isinstance(data, (bytes, bytearray, memoryview)):
            result = processors.read(data, format="mesh")
            assert result is not None  # forced read() raises instead
            processed = ProcessedMeshData(result.data)
        else:
            processed = ProcessedMeshData(data)
        self.movement_factor = processed.size
        self._mesh_renderer.mesh_data = processed

        self.focus_mesh()
