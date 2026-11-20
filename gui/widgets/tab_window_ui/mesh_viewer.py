"""Code for viewer tab window customization."""

import os
from typing import TYPE_CHECKING, cast

from PySide6 import QtCore, QtWidgets

from core.mesh_converter import FORMATS, convert_mesh
from gui.widgets.managed_rhi_widget import ManagedRhiWidget

if TYPE_CHECKING:
    from gui.widgets.viewers.mesh_viewer.viewer_widget import MeshViewer
    from gui.windows.viewer_tab_window import ViewerTabWindow


def _save_as_format(viewer: "MeshViewer", target_format, file_path: str):
    """
    Save the current mesh in the specified format.

    Parameters:
    - viewer: The MeshViewer instance containing the mesh to save.
    - target_format: The format to save the mesh as.
    """
    mesh = viewer.render_widget.mesh_data
    if mesh is None:
        QtWidgets.QMessageBox.warning(
            viewer, "No Mesh Loaded", "Please load a mesh file before saving."
        )
        return False
    try:
        # Convert first so a failure never leaves an empty/partial file behind.
        data = convert_mesh(mesh.raw_data, target_format)
        with open(file_path, "wb") as f:
            f.write(data)
    except Exception as e:  # pylint: disable=broad-exception-caught
        QtWidgets.QMessageBox.critical(
            viewer,
            "Save Failed",
            f"Failed to save {os.path.basename(file_path)} as "
            f"{target_format.NAME}:\n{type(e).__name__}: {e}",
        )
        return False
    return True


def _save_current_as_format(window: "ViewerTabWindow", target_format):
    """
    Save the current mesh in the specified format.

    Parameters:
    - target_format: The format to save the mesh as.
    """
    viewer = cast("MeshViewer", window.tab_widget.currentWidget())
    if viewer is None:
        QtWidgets.QMessageBox.warning(
            window, "No File Opened", "Please open a mesh file before saving."
        )
        return
    file_dialog = QtWidgets.QFileDialog()
    file_path, _ = file_dialog.getSaveFileName(
        None,
        f"Save Mesh as {target_format.NAME}",
        "",
        f"{target_format.NAME} Files (*{target_format.EXTENSION})",
    )
    if file_path and _save_as_format(viewer, target_format, file_path):
        QtWidgets.QMessageBox.information(
            window,
            "Save Successful",
            f"Mesh saved successfully as {target_format.NAME}.",
        )


def _save_all_as_format(window: "ViewerTabWindow", target_format):
    """
    Save all currently opened meshes in the specified format.

    Parameters:
    - target_format: The format to save the meshes as.
    """
    file_count = window.tab_widget.count()
    if file_count == 0:
        QtWidgets.QMessageBox.warning(
            window, "No Files Opened", "Please open mesh files before saving all."
        )
        return
    save_directory = QtWidgets.QFileDialog.getExistingDirectory(
        None, f"Save All Meshes as {target_format.NAME}", ""
    )
    if not save_directory:
        return

    for i in range(window.tab_widget.count()):
        viewer = cast("MeshViewer", window.tab_widget.widget(i))
        if viewer is None or viewer.render_widget.mesh_data is None:
            continue
        file_path = os.path.join(
            save_directory,
            f"{os.path.splitext(window.tab_widget.tabText(i))[0]}{target_format.EXTENSION}",
        )
        _save_as_format(viewer, target_format, file_path)
    QtWidgets.QMessageBox.information(
        window,
        "Save All Successful",
        f"All meshes saved successfully as {target_format.NAME}.",
    )


class _EventFilter(QtCore.QObject):
    """Event filter for the mesh viewer tab window. Removes the surface type setter after shown."""

    def __init__(self, window: "ViewerTabWindow", setter: ManagedRhiWidget):
        super().__init__(window)
        self._window = window
        self._setter = setter

    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if event.type() == QtCore.QEvent.Type.Show:
            self._window.central_layout.removeWidget(self._setter)
            return True
        return False


def setup_mesh_viewer_tab_window(window: "ViewerTabWindow"):
    """Setup the mesh viewer tab window."""

    # Forces the tab window to use current graphics backend.
    surface_type_setter = ManagedRhiWidget()
    window.central_layout.addWidget(surface_type_setter)

    event_filter = _EventFilter(window, surface_type_setter)
    window.installEventFilter(event_filter)

    save_as_menu = window.menuBar().addMenu("Save As")

    for fmt in FORMATS:
        action = save_as_menu.addAction(fmt.NAME)
        action.triggered.connect(
            lambda _, fmt=fmt: _save_current_as_format(window, fmt)
        )

    save_all_as_menu = window.menuBar().addMenu("Save All As")
    for fmt in FORMATS:
        action = save_all_as_menu.addAction(fmt.NAME)
        action.triggered.connect(lambda _, fmt=fmt: _save_all_as_format(window, fmt))
