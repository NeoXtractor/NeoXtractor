"""Provides Texture Viewer widget."""

from typing import cast

import numpy as np
from PIL import Image, ImageFile
from PySide6 import QtCore, QtGui, QtWidgets

from core.file import IFile
from core.images import convert_image, image_to_png_data
from gui.widgets.tab_window_ui.texture_viewer import setup_texture_viewer_tab_window
from gui.widgets.viewer import Viewer
from gui.windows.viewer_tab_window import ICustomTabWindow

QT_SUPPORTED_FORMATS = set(
    fmt.toStdString().lower() for fmt in QtGui.QImageReader.supportedImageFormats()
)


class ImageDecodeTaskSignals(QtCore.QObject):
    """Signals for the image decode task."""

    load_complete = QtCore.Signal(QtGui.QImage)
    load_failed = QtCore.Signal(Exception)


class ImageDecodeTask(QtCore.QRunnable):
    """A task to decode an image in a separate thread."""

    def __init__(self, data: bytes, extension: str):
        super().__init__()

        self.signals = ImageDecodeTaskSignals()

        self.cancelled = False

        self.data = data
        self.extension = extension

    @QtCore.Slot()
    def run(self):
        if self.extension in QT_SUPPORTED_FORMATS:
            # Use Qt's image reader for supported formats, PySide6 expects bytes for the format, while str is usable
            texture = QtGui.QImage.fromData(self.data, cast(bytes, self.extension))

        else:
            # Use custom conversion for unsupported formats
            try:
                texture = QtGui.QImage.fromData(
                    image_to_png_data(
                        cast(
                            Image.Image | ImageFile.ImageFile,
                            convert_image(self.data, self.extension),
                        )
                    )
                )
            except Exception as e:
                self.signals.load_failed.emit(str(e))
                return

        if self.cancelled:
            return

        if texture.isNull():
            self.signals.load_failed.emit(ValueError("Failed to load image data"))
            return

        self.signals.load_complete.emit(texture)


class TextureViewer(Viewer, ICustomTabWindow):
    """A widget that displays a texture."""

    name = "Texture Viewer"
    accepted_extensions = QT_SUPPORTED_FORMATS | {
        "dds",
        "pvr",
        "ktx",
        "ktx_low",
        "astc",
        "cbk",
    }
    setup_tab_window = setup_texture_viewer_tab_window

    def __init__(self):
        super().__init__()

        self._file: IFile | None = None

        self._decode_task: ImageDecodeTask | None = None

        self._texture: QtGui.QImage | None = None
        self._processed_texture: QtGui.QImage | None = None

        main_layout = QtWidgets.QVBoxLayout(self)

        self._message_label = QtWidgets.QLabel("No image loaded")
        self._message_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._message_label.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        main_layout.addWidget(self._message_label)

        self._image_label = QtWidgets.QLabel(self)
        self._image_label.setAlignment(QtGui.Qt.AlignmentFlag.AlignCenter)
        self._image_label.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        self._image_label.setVisible(False)

        size_layout = QtWidgets.QHBoxLayout()
        size_layout.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)

        self.size_label = QtWidgets.QLabel(self)
        self.size_label.setAlignment(QtGui.Qt.AlignmentFlag.AlignCenter)
        self.size_label.setVisible(False)
        size_layout.addWidget(self.size_label)

        self.rendered_size_label = QtWidgets.QLabel(self)
        self.rendered_size_label.setAlignment(QtGui.Qt.AlignmentFlag.AlignCenter)
        self.rendered_size_label.setVisible(False)
        size_layout.addWidget(self.rendered_size_label)

        self.flip_tex = QtWidgets.QCheckBox("Flip Vertically")
        self.flip_tex.stateChanged.connect(
            lambda: (self._apply_modifiers(), self._display_image())
        )

        self.channel_r = QtWidgets.QCheckBox("R")
        self.channel_g = QtWidgets.QCheckBox("G")
        self.channel_b = QtWidgets.QCheckBox("B")
        self.channel_a = QtWidgets.QCheckBox("A")

        channels_layout = QtWidgets.QHBoxLayout()

        for channel in [self.channel_r, self.channel_g, self.channel_b, self.channel_a]:
            channel.setChecked(True)
            channel.stateChanged.connect(
                lambda: (self._apply_modifiers(), self._display_image())
            )
            channels_layout.addWidget(channel)

        channels_layout.addStretch()

        main_layout.addWidget(self._image_label)
        main_layout.addLayout(size_layout)
        main_layout.addWidget(self.flip_tex)
        main_layout.addLayout(channels_layout)

    def _display_image(self):
        """Display the (processed) image in the label."""
        if self._texture is None and self._processed_texture is None:
            return

        pixmap = QtGui.QPixmap.fromImage(
            cast(
                QtGui.QImage,
                self._processed_texture if self._processed_texture else self._texture,
            )
        ).scaled(self._image_label.size(), QtCore.Qt.AspectRatioMode.KeepAspectRatio)
        self._image_label.setPixmap(pixmap)
        if cast(QtGui.QImage, self._texture).size() != pixmap.size():
            self.rendered_size_label.setVisible(True)
            self.rendered_size_label.setText(
                f"(Rendered Size: {pixmap.width()} x {pixmap.height()})"
            )
        else:
            self.rendered_size_label.setVisible(False)

    def _apply_modifiers(self):
        """Apply any necessary modifiers to the image."""

        if self._texture is None:
            return

        image = self._texture.convertToFormat(QtGui.QImage.Format.Format_ARGB32).copy()

        a_modifier = int(self.channel_a.isChecked())
        r_modifier = int(self.channel_r.isChecked())
        g_modifier = int(self.channel_g.isChecked())
        b_modifier = int(self.channel_b.isChecked())

        ptr = image.bits()
        # For ARGB32, each pixel is 4 bytes (B, G, R, A)
        arr = np.frombuffer(ptr, dtype=np.uint8).reshape(
            image.height(), image.width(), 4
        )

        # Perform vectorized operations on the NumPy array
        # Indices for BGRA: Blue=0, Green=1, Red=2, Alpha=3
        if a_modifier == 0:
            arr[:, :, 3] = 255  # Set Alpha channel to 255
        if r_modifier == 0:
            arr[:, :, 2] = 0  # Set Red channel to 0
        if g_modifier == 0:
            arr[:, :, 1] = 0  # Set Green channel to 0
        if b_modifier == 0:
            arr[:, :, 0] = 0  # Set Blue channel to 0

        if self.flip_tex.isChecked():
            arr[:] = np.flipud(arr)

        self._processed_texture = image

        self._display_image()

    @property
    def texture(self) -> QtGui.QImage | None:
        """Get the current texture."""
        return self._texture

    @property
    def processed_texture(self) -> QtGui.QImage | None:
        """Get the processed texture."""
        return self._processed_texture

    def showEvent(self, event: QtGui.QShowEvent):
        """Handle show events."""
        super().showEvent(event)
        if self._texture is not None:
            self._display_image()

    def resizeEvent(self, event: QtGui.QResizeEvent):
        """Handle resize events."""
        super().resizeEvent(event)
        if self._texture is not None:
            self._display_image()

    def _on_load_complete(self, image: QtGui.QImage):
        self._message_label.setVisible(False)
        self._image_label.setVisible(True)
        self.size_label.setVisible(True)
        self.rendered_size_label.setVisible(True)

        self._texture = image

        self._apply_modifiers()
        self._display_image()

        self.size_label.setText(
            f"Size: {self._texture.width()} x {self._texture.height()}"
        )

    def _on_load_failed(self, error: Exception):
        self._message_label.setText(f"Failed to load image: {error}")

    def clear(self):
        """Clear the texture."""
        self._texture = None
        self._image_label.clear()

    def set_file(self, file: IFile):
        """Set the texture data and extension."""
        if file.extension.lower() not in self.accepted_extensions:
            raise ValueError(f"Unsupported image format: {file.extension}")

        self._file = file

        if self._decode_task is not None:
            self._decode_task.cancelled = True
            self._decode_task = None

        self._message_label.setText("Loading image...")
        self._message_label.setVisible(True)
        self._image_label.setVisible(False)
        self.size_label.setVisible(False)
        self.rendered_size_label.setVisible(False)

        self._decode_task = ImageDecodeTask(file.data, file.extension)
        self._decode_task.signals.load_complete.connect(self._on_load_complete)
        self._decode_task.signals.load_failed.connect(self._on_load_failed)

        QtCore.QThreadPool.globalInstance().start(self._decode_task)

    def get_file(self) -> IFile | None:
        return self._file

    def unload_file(self):
        self._file = None
        self.clear()
