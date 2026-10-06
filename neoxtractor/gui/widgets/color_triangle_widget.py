"""Provides a widget for graphics settings preview."""

import ctypes
import os
import time
from typing import cast

from PySide6 import QtGui, QtWidgets

from neoxtractor.core.utils import get_application_path
from neoxtractor.gui.renderers.text_renderer import TextRenderer
from neoxtractor.gui.widgets.managed_rhi_widget import ManagedRhiWidget

VERTEX_DATA = [
    # Vertex position  Colors
    0.0,
    0.5,
    1.0,
    0.0,
    0.0,
    -0.5,
    -0.5,
    0.0,
    1.0,
    0.0,
    0.5,
    -0.5,
    0.0,
    0.0,
    1.0,
]


class ColorTriangleWidget(ManagedRhiWidget):
    """
    A QRhiWidget that renders a rotating triangle with colors.
    This widget utilizes Qt's RHI (Rendering Hardware Interface) to render a 3D triangle
    with vertex colors. It sets up the necessary graphics pipeline, buffers, and shader
    resource bindings to perform hardware-accelerated rendering.
    The triangle rotates continuously around the Y axis.
    Requirements:
    - Shader files "color.vert.qsb" and "color.frag.qsb" must be available in "data/shaders"
    Methods:
            initialize(cb): Sets up the rendering resources when the RHI context is ready
            render(cb): Performs the actual rendering of the triangle each frame
    """

    def __init__(self, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)

        self._rhi: QtGui.QRhi | None = None
        self._pipeline: QtGui.QRhiGraphicsPipeline | None = None
        self._vbuf: QtGui.QRhiBuffer | None = None
        self._ubuf: QtGui.QRhiBuffer | None = None
        self._srb: QtGui.QRhiShaderResourceBindings | None = None
        self._view_proj: QtGui.QMatrix4x4 | None = None

        self._text_renderer = TextRenderer(self, 16)

        self._last_frame_time = time.time()
        self._rotation = 0

    def initialize(self, cb: QtGui.QRhiCommandBuffer):
        if self._rhi != self.rhi() or self._rhi is None:  # type hint
            self._pipeline = None
            self._rhi = self.rhi()

        if self._pipeline is None:
            self._vbuf = self._rhi.newBuffer(
                QtGui.QRhiBuffer.Type.Immutable,
                QtGui.QRhiBuffer.UsageFlag.VertexBuffer,
                ctypes.sizeof(ctypes.c_float) * len(VERTEX_DATA),
            )
            self._vbuf.create()

            self._ubuf = self._rhi.newBuffer(
                QtGui.QRhiBuffer.Type.Dynamic,
                QtGui.QRhiBuffer.UsageFlag.UniformBuffer,
                80,
            )
            self._ubuf.create()

            self._srb = self._rhi.newShaderResourceBindings()
            self._srb.setBindings(
                [
                    QtGui.QRhiShaderResourceBinding.uniformBuffer(
                        0,
                        QtGui.QRhiShaderResourceBinding.StageFlag.VertexStage
                        | QtGui.QRhiShaderResourceBinding.StageFlag.FragmentStage,
                        self._ubuf,
                    ),
                ]
            )
            self._srb.create()

            self._pipeline = self._rhi.newGraphicsPipeline()
            shaders_path = os.path.join(get_application_path(), "data", "shaders")
            with open(os.path.join(shaders_path, "color.vert.qsb"), "rb") as f:
                vsrc = f.read()
                vsrc = QtGui.QShader.fromSerialized(vsrc)
                with open(os.path.join(shaders_path, "color.frag.qsb"), "rb") as f:
                    fsrc = f.read()
                    fsrc = QtGui.QShader.fromSerialized(fsrc)

                    self._pipeline.setShaderStages(
                        [
                            QtGui.QRhiShaderStage(
                                QtGui.QRhiShaderStage.Type.Vertex, vsrc
                            ),
                            QtGui.QRhiShaderStage(
                                QtGui.QRhiShaderStage.Type.Fragment, fsrc
                            ),
                        ]
                    )
            input_layout = QtGui.QRhiVertexInputLayout()
            input_layout.setBindings(
                [
                    QtGui.QRhiVertexInputBinding(5 * ctypes.sizeof(ctypes.c_float)),
                ]
            )
            input_layout.setAttributes(
                [
                    QtGui.QRhiVertexInputAttribute(
                        0, 0, QtGui.QRhiVertexInputAttribute.Format.Float2, 0
                    ),
                    QtGui.QRhiVertexInputAttribute(
                        0,
                        1,
                        QtGui.QRhiVertexInputAttribute.Format.Float3,
                        2 * ctypes.sizeof(ctypes.c_float),
                    ),
                ]
            )
            self._pipeline.setVertexInputLayout(input_layout)
            self._pipeline.setShaderResourceBindings(self._srb)
            self._pipeline.setRenderPassDescriptor(
                self.renderTarget().renderPassDescriptor()
            )
            self._pipeline.create()

            resource_updates = self._rhi.nextResourceUpdateBatch()
            arr = (ctypes.c_float * len(VERTEX_DATA))(*VERTEX_DATA)
            resource_updates.uploadStaticBuffer(self._vbuf, cast(int, arr))
            cb.resourceUpdate(resource_updates)

        self._text_renderer.initialize(cb)

        output_size = self.renderTarget().pixelSize()
        self._view_proj = self._rhi.clipSpaceCorrMatrix()
        self._view_proj.perspective(
            45, output_size.width() / output_size.height(), 0.01, 1000
        )
        self._view_proj.translate(0, 0, -4)

    def render(self, cb: QtGui.QRhiCommandBuffer):
        if (
            self._rhi is None
            or self._view_proj is None
            or self._vbuf is None
            or self._ubuf is None
            or self._pipeline is None
        ):
            return

        self._text_renderer.render_text("Preview", (10, 10))

        now = time.time()
        delta = now - self._last_frame_time

        self._rotation += 1.5 * delta * 60
        if self._rotation >= 360:
            self._rotation -= 360

        self._last_frame_time = now

        resource_updates = self._rhi.nextResourceUpdateBatch()
        vp = QtGui.QMatrix4x4(self._view_proj)
        vp.rotate(self._rotation, 0, 1, 0)
        vp_data = vp.data()
        ubuf_data = list(vp_data) + [1]  # MVP and Opacity data
        arr = (ctypes.c_float * len(ubuf_data))(*ubuf_data)
        resource_updates.updateDynamicBuffer(
            self._ubuf, 0, ctypes.sizeof(arr), cast(int, arr)
        )

        self._text_renderer.update_resources(resource_updates)

        clr = QtGui.QColor.fromRgbF(0.4, 0.7, 0.0, 1.0)
        cb.beginPass(
            self.renderTarget(),
            clr,
            QtGui.QRhiDepthStencilClearValue(1, 0),
            resource_updates,
        )

        cb.setGraphicsPipeline(self._pipeline)
        cb.setViewport(
            QtGui.QRhiViewport(
                0,
                0,
                self.renderTarget().pixelSize().width(),
                self.renderTarget().pixelSize().height(),
            )
        )
        cb.setShaderResources()
        cb.setVertexInput(0, [(self._vbuf, 0)])
        cb.draw(3)

        self._text_renderer.render(cb)

        cb.endPass()

        self.update()
