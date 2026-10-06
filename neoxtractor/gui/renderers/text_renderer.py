"""Text renderer for QRhiWidget"""

import ctypes
import os
from dataclasses import dataclass
from typing import cast

from PIL import Image, ImageDraw, ImageFont
from PySide6 import QtGui, QtWidgets

from neoxtractor.core.logger import get_logger
from neoxtractor.core.utils import get_application_path


@dataclass
class Character:
    """
    Represents a character in a font atlas with texture coordinates and metrics.
    A Character object stores the necessary information to render a single character
    from a font texture atlas, including its position in the texture, dimensions,
    and vertical positioning information.
    Args:
        tex_coords (tuple[int | float, int | float, int | float, int | float]):
            Texture coordinates in the format (x1, y1, x2, y2) defining the
            character's position in the texture atlas.
        size (tuple[int | float, int]):
            The width and height of the character in pixels as (width, height).
        ascent (int):
            The ascent value representing the distance from the baseline to the
            top of the character, used for proper vertical alignment.
    Attributes:
        tex_coords: Texture coordinates for the character in the atlas.
        size: Dimensions of the character.
        ascent: Vertical offset from baseline to character top.
    """

    def __init__(
        self,
        tex_coords: tuple[int | float, int | float, int | float, int | float],
        size: tuple[int | float, int],
        ascent: int,
    ):
        self.tex_coords = tex_coords
        self.size = size
        self.ascent = ascent


@dataclass
class QueuedTextRender:
    """
    Represents a queued text render request.
    Args:
        text (str): The text to render.
        position (tuple[int, int]): The position to render the text at (x, y).
        color (tuple[float, float, float, float]): The color of the text in RGBA format.
    Attributes:
        text: The text string to be rendered.
        position: The (x, y) coordinates where the text should be rendered.
        color: The RGBA color of the text.
    """

    text: str
    position: tuple[int, int]
    color: tuple[float, float, float, float] = (
        1.0,
        1.0,
        1.0,
        1.0,
    )  # Default white color
    scale: float = 1.0


class TextRenderer:
    """
    A text renderer using Qt's RHI (Render Hardware Interface) for hardware-accelerated text rendering.
    This class provides efficient text rendering capabilities by creating a font atlas texture
    and using GPU-accelerated rendering through Qt's RHI abstraction layer. It supports
    queued text rendering with customizable colors, positions, and scaling.
    The renderer creates a font atlas from ASCII characters (32-127) using the Roboto font
    or system default fallback. Text is rendered as textured quads using vertex and index
    buffers for optimal GPU performance.
    Key Features:
    - Hardware-accelerated text rendering via Qt RHI
    - Font atlas generation for efficient character lookup
    - Batched text rendering with queue system
    - Customizable text color, position, and scaling
    - Alpha blending support for transparent text
    - Cross-platform compatibility through RHI abstraction
    Usage:
        1. Initialize the renderer with a QRhiWidget
        2. Call initialize() with a command buffer to set up GPU resources
        3. Queue text using renderText() method
        4. Call preparePass() to update GPU buffers before starting a render pass
        5. Call render() within a render pass to draw queued text
        6. Call releaseResources() for cleanup
        rhi_widget (QtWidgets.QRhiWidget): The RHI widget providing the rendering context
        font_size (int, optional): Font size for the atlas generation. Defaults to 12.
    Attributes:
        font_height (int): The total height of the font (ascent + descent)
        - Requires valid RHI context from QRhiWidget
        - Maximum of 1024 quads and 4096 characters can be rendered per frame
    """

    def __init__(self, rhi_widget: QtWidgets.QRhiWidget, font_size=12):
        self._rhi_widget = rhi_widget

        self._rhi: QtGui.QRhi | None = None
        self._pipeline: QtGui.QRhiGraphicsPipeline | None = None
        self._vbuf: QtGui.QRhiBuffer | None = None
        self._ibuf: QtGui.QRhiBuffer | None = None
        self._ubuf: QtGui.QRhiBuffer | None = None
        self._srb: QtGui.QRhiShaderResourceBindings | None = None

        self._text_queue: list[QueuedTextRender] = []
        self._char_count = 0

        self._create_atlas_texture(font_size)

    def _create_atlas_texture(self, font_size: int):
        atlas_size = 512
        atlas = Image.new("L", (atlas_size, atlas_size), 0)
        atlas_draw = ImageDraw.Draw(atlas)

        font: ImageFont.FreeTypeFont | ImageFont.ImageFont
        try:
            font = ImageFont.truetype(
                os.path.join(
                    get_application_path(), "data", "fonts", "Roboto-Regular.ttf"
                ),
                font_size,
            )
        except OSError as exc:
            get_logger().warning("Failed to load font, falling back to default")
            font = ImageFont.load_default()
            if not isinstance(font, ImageFont.FreeTypeFont):
                raise TypeError("Default font is not a FreeTypeFont") from exc

        # Create character map
        self._char_data: dict[str, Character] = {}
        cursor_x, cursor_y = 0, 0
        max_height = 0

        ascent, descent = font.getmetrics()
        self.font_height = total_height = ascent + descent

        for char_code in range(32, 128):
            char = chr(char_code)
            bbox = font.getbbox(char)
            char_width = bbox[2] - bbox[0]
            char_height = bbox[3] - bbox[1]

            if cursor_x + char_width >= atlas_size:
                cursor_x = 0
                cursor_y += total_height + 2
                max_height = 0

            max_height = max(max_height, char_height)

            # Draw character aligned to the baseline
            atlas_draw.text(
                (cursor_x, cursor_y + ascent), char, font=font, fill=255, anchor="ls"
            )

            self._char_data[char] = Character(
                (
                    cursor_x / atlas_size,
                    cursor_y / atlas_size,
                    (cursor_x + char_width) / atlas_size,
                    (cursor_y + total_height) / atlas_size,
                ),
                (char_width, total_height),
                ascent,
            )

            cursor_x += char_width + 2

        get_logger().debug(
            "Font atlas created with %d characters", len(self._char_data)
        )

        # Convert to QImage with proper format and stride
        self._image = QtGui.QImage(
            atlas.tobytes(),
            atlas.size[0],
            atlas.size[1],
            atlas.size[0],
            QtGui.QImage.Format.Format_Grayscale8,
        )

    def releaseResources(self):
        """
        Release all allocated graphics resources.
        This method properly destroys and cleans up all graphics pipeline resources
        including the rendering pipeline, shader resource binding, and various buffers
        (uniform, index, and vertex buffers). Each resource is checked for existence
        before destruction and set to None after cleanup to prevent double-destruction.
        This method should be called when the text renderer is no longer needed
        or during cleanup to prevent memory leaks.
        """

        if self._pipeline is not None:
            self._pipeline.destroy()
            self._pipeline = None
        if self._srb is not None:
            self._srb.destroy()
            self._srb = None
        if self._ubuf is not None:
            self._ubuf.destroy()
            self._ubuf = None
        if self._ibuf is not None:
            self._ibuf.destroy()
            self._ibuf = None
        if self._vbuf is not None:
            self._vbuf.destroy()
            self._vbuf = None

    def initialize(self, cb: QtGui.QRhiCommandBuffer):
        """
        Initialize the text renderer's graphics pipeline and resources.
        Sets up all necessary RHI (Render Hardware Interface) resources for text rendering,
        including textures, buffers, shaders, and the graphics pipeline. This method should
        be called once before any rendering operations.
        Args:
            cb (QtGui.QRhiCommandBuffer): The command buffer used for resource updates
        Returns:
            None
        Note:
            - Creates a texture from the font atlas image
            - Sets up vertex buffer for dynamic text geometry (4096 characters max)
            - Creates index buffer with quad indices pattern for efficient rendering
            - Initializes uniform buffer for projection matrix and text color
            - Configures shader resource bindings for texture sampling
            - Sets up graphics pipeline with vertex/fragment shaders
            - Enables alpha blending for proper text transparency
            - If pipeline already exists, returns early without re-initialization
        """

        if self._rhi is None or self._rhi != self._rhi_widget.rhi():
            self._rhi = self._rhi_widget.rhi()

        resource_updates = self._rhi.nextResourceUpdateBatch()

        # Create texture from atlas
        texture = self._rhi.newTexture(QtGui.QRhiTexture.Format.R8, self._image.size())
        texture.create()
        resource_updates.uploadTexture(texture, self._image)

        # Create vertex buffer for dynamic geometry
        self._vbuf = self._rhi.newBuffer(
            QtGui.QRhiBuffer.Type.Dynamic,
            QtGui.QRhiBuffer.UsageFlag.VertexBuffer,
            4096 * 4 * 4,
        )  # Enough space for many characters
        self._vbuf.create()

        # Create index buffer for rendering quads (0,1,2, 0,2,3)
        self._ibuf = self._rhi.newBuffer(
            QtGui.QRhiBuffer.Type.Immutable,
            QtGui.QRhiBuffer.UsageFlag.IndexBuffer,
            1024 * 6 * 2,
        )  # 6 indices per quad (uint16), 1024 quads max
        self._ibuf.create()

        # Fill index buffer with quad indices pattern (0,1,2, 0,2,3, ...)
        indices = []
        for i in range(1024):
            base = i * 4
            indices.extend([base, base + 1, base + 2, base, base + 2, base + 3])

        index_data = (ctypes.c_uint16 * len(indices))(*indices)
        resource_updates.uploadStaticBuffer(self._ibuf, cast(int, index_data))

        # Create uniform buffer for projection matrix and text color
        self._ubuf = self._rhi.newBuffer(
            QtGui.QRhiBuffer.Type.Dynamic, QtGui.QRhiBuffer.UsageFlag.UniformBuffer, 64
        )  # Matrix (64 bytes)
        self._ubuf.create()

        sampler = self._rhi.newSampler(
            QtGui.QRhiSampler.Filter.Nearest,
            QtGui.QRhiSampler.Filter.Nearest,
            QtGui.QRhiSampler.Filter.None_,
            QtGui.QRhiSampler.AddressMode.ClampToEdge,
            QtGui.QRhiSampler.AddressMode.ClampToEdge,
        )
        sampler.create()

        # Create shader resource bindings
        self._srb = self._rhi.newShaderResourceBindings()
        self._srb.setBindings(
            [
                QtGui.QRhiShaderResourceBinding.uniformBuffer(
                    0,
                    QtGui.QRhiShaderResourceBinding.StageFlag.VertexStage
                    | QtGui.QRhiShaderResourceBinding.StageFlag.FragmentStage,
                    self._ubuf,
                ),
                QtGui.QRhiShaderResourceBinding.sampledTexture(
                    1,
                    QtGui.QRhiShaderResourceBinding.StageFlag.FragmentStage,
                    texture,
                    sampler,
                ),
            ]
        )
        self._srb.create()

        # Create graphics pipeline
        self._pipeline = self._rhi.newGraphicsPipeline()

        with open(
            os.path.join(get_application_path(), "data", "shaders", "text.vert.qsb"),
            "rb",
        ) as f:
            vsrc = f.read()
            vsrc = QtGui.QShader.fromSerialized(vsrc)
            with open(
                os.path.join(
                    get_application_path(), "data", "shaders", "text.frag.qsb"
                ),
                "rb",
            ) as f:
                fsrc = f.read()
                fsrc = QtGui.QShader.fromSerialized(fsrc)

                self._pipeline.setShaderStages(
                    [
                        QtGui.QRhiShaderStage(QtGui.QRhiShaderStage.Type.Vertex, vsrc),
                        QtGui.QRhiShaderStage(
                            QtGui.QRhiShaderStage.Type.Fragment, fsrc
                        ),
                    ]
                )

        # Set up vertex input layout
        input_layout = QtGui.QRhiVertexInputLayout()
        input_layout.setBindings(
            [
                QtGui.QRhiVertexInputBinding(8 * ctypes.sizeof(ctypes.c_float))
                # 8 floats per vertex (pos.xy + tex.uv + color.rgba)
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
                    QtGui.QRhiVertexInputAttribute.Format.Float2,
                    2 * ctypes.sizeof(ctypes.c_float),
                ),
                QtGui.QRhiVertexInputAttribute(
                    0,
                    2,
                    QtGui.QRhiVertexInputAttribute.Format.Float4,
                    4 * ctypes.sizeof(ctypes.c_float),
                ),
            ]
        )

        self._pipeline.setVertexInputLayout(input_layout)
        self._pipeline.setShaderResourceBindings(self._srb)
        self._pipeline.setRenderPassDescriptor(
            self._rhi_widget.renderTarget().renderPassDescriptor()
        )

        # Set up blending for text rendering
        target_blend = QtGui.QRhiGraphicsPipeline.TargetBlend()
        # TargetBlend is not currently typed
        target_blend.enable = True  # type: ignore
        target_blend.srcColor = QtGui.QRhiGraphicsPipeline.BlendFactor.SrcAlpha  # type: ignore
        target_blend.dstColor = QtGui.QRhiGraphicsPipeline.BlendFactor.OneMinusSrcAlpha  # type: ignore
        target_blend.srcAlpha = QtGui.QRhiGraphicsPipeline.BlendFactor.One  # type: ignore
        target_blend.dstAlpha = QtGui.QRhiGraphicsPipeline.BlendFactor.OneMinusSrcAlpha  # type: ignore

        self._pipeline.setTargetBlends([target_blend])

        # Create the pipeline
        self._pipeline.create()

        cb.resourceUpdate(resource_updates)

    def update_resources(self, resource_updates: QtGui.QRhiResourceUpdateBatch):
        """
        Prepare a rendering pass by updating GPU resources with text geometry and uniforms.
        This method processes the queued text elements and prepares the necessary GPU buffers
        for rendering. It sets up the projection matrix, generates vertex data for each
        character quad, and updates the vertex and uniform buffers.
        Args:
            cb (QtGui.QRhiCommandBuffer): The RHI command buffer to record resource updates
        Returns:
            None: Returns early if required resources are not available or no valid characters to render
        """

        if (
            not self._rhi
            or not self._pipeline
            or not self._srb
            or not self._vbuf
            or not self._ibuf
            or not self._ubuf
        ):
            return

        projection = QtGui.QMatrix4x4()
        if self._rhi_widget.api() == QtWidgets.QRhiWidget.Api.Vulkan:
            # Vulkan needs Y-flip for orthographic projection
            projection.ortho(
                0,
                self._rhi_widget.renderTarget().pixelSize().width(),
                self._rhi_widget.renderTarget().pixelSize().height(),
                0,
                -1.0,
                1.0,
            )
        else:
            projection.ortho(
                0,
                self._rhi_widget.renderTarget().pixelSize().width(),
                0,
                self._rhi_widget.renderTarget().pixelSize().height(),
                -1.0,
                1.0,
            )

        # Convert matrix and color to array
        matrix_data = projection.data()

        uniform_array = (ctypes.c_float * len(matrix_data))(*matrix_data)

        resource_updates.updateDynamicBuffer(
            self._ubuf, 0, ctypes.sizeof(uniform_array), cast(int, uniform_array)
        )

        vertices = []
        char_count = 0

        viewport_height = self._rhi_widget.renderTarget().pixelSize().height()

        for queued_text in self._text_queue:
            text = queued_text.text
            position = queued_text.position
            color = queued_text.color
            scale = queued_text.scale

            color_data = list(color)
            cursor_x = position[0]

            for char in text:
                if char not in self._char_data:
                    continue

                char_info = self._char_data[char]
                w, h = char_info.size
                w, h = w * scale, h * scale
                tex_coords = char_info.tex_coords

                # Position character relative to baseline
                char_y = viewport_height - position[1] - (char_info.ascent * scale)

                # Add quad vertices (position + texcoord for each vertex)
                quad = (
                    [
                        # Bottom-left
                        cursor_x,
                        char_y + h,
                        tex_coords[0],
                        tex_coords[1],
                    ]
                    + color_data
                    + [
                        # Top-left
                        cursor_x,
                        char_y,
                        tex_coords[0],
                        tex_coords[3],
                    ]
                    + color_data
                    + [
                        # Top-right
                        cursor_x + w,
                        char_y,
                        tex_coords[2],
                        tex_coords[3],
                    ]
                    + color_data
                    + [
                        # Bottom-right
                        cursor_x + w,
                        char_y + h,
                        tex_coords[2],
                        tex_coords[1],
                    ]
                    + color_data
                )
                vertices.extend(quad)
                cursor_x += w
                char_count += 1

        self._text_queue.clear()

        # Skip if no valid characters
        if char_count == 0:
            return

        self._char_count = char_count

        vertex_array = (ctypes.c_float * len(vertices))(*vertices)

        # Update vertex buffer
        resource_updates.updateDynamicBuffer(
            self._vbuf, 0, ctypes.sizeof(vertex_array), cast(int, vertex_array)
        )

    def render(self, cb: QtGui.QRhiCommandBuffer):
        """
        Renders text using the Qt RHI (Rendering Hardware Interface) command buffer.
        This method sets up the graphics pipeline and renders text characters as indexed quads.
        Each character is rendered as a quad with 6 indices (2 triangles). The method handles
        the complete rendering pipeline setup including viewport, shader resources, and vertex input.
        Args:
            cb (QtGui.QRhiCommandBuffer): The RHI command buffer used for recording rendering commands.
        Returns:
            None
        Note:
            - Must be called inside render pass.
            - Returns early if pipeline, shader resource bindings, vertex buffer, or index buffer are not initialized
            - Returns early if no characters are queued for rendering (_char_count == 0)
        """

        if not self._pipeline or not self._srb or not self._vbuf or not self._ibuf:
            return

        if self._char_count == 0:
            return

        cb.setGraphicsPipeline(self._pipeline)
        cb.setViewport(
            QtGui.QRhiViewport(
                0,
                0,
                self._rhi_widget.renderTarget().pixelSize().width(),
                self._rhi_widget.renderTarget().pixelSize().height(),
            )
        )
        cb.setShaderResources()

        cb.setVertexInput(
            0,
            [(self._vbuf, 0)],
            self._ibuf,
            0,
            QtGui.QRhiCommandBuffer.IndexFormat.IndexUInt16,
        )

        # Draw text (6 indices per quad)
        cb.drawIndexed(self._char_count * 6)

        self._char_count = 0

    def render_text(
        self,
        text: str,
        position: tuple[int, int],
        color: tuple[float, float, float, float] = (1.0, 1.0, 1.0, 1.0),
        scale: float = 1.0,
    ):
        """
        Queue a text render request.

        :param text: The text to render.
        :param position: The (x, y) position to render the text at.
        :param color: The RGBA color of the text.
        :param scale: Scale factor for the text size.
        """
        self._text_queue.append(QueuedTextRender(text, position, color, scale))

    def clear_queue(self):
        """
        Clear the text render queue.
        This method empties the internal queue of text render requests,
        effectively discarding any queued text that has not yet been rendered.
        """
        self._text_queue.clear()
        self._char_count = 0
