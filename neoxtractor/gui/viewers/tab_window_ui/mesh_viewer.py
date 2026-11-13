"""Code for viewer tab window customization."""

import os
from typing import TYPE_CHECKING, cast

from PySide6 import QtCore, QtWidgets

from neoxtractor.core import processors
from neoxtractor.gui.widgets.managed_rhi_widget import ManagedRhiWidget

if TYPE_CHECKING:
    from neoxtractor.core.utils.target import Target
    from neoxtractor.gui.viewers.mesh_viewer.viewer_widget import MeshViewer
    from neoxtractor.gui.windows.viewer_tab_window import ViewerTabWindow


def _target_label(target: "Target") -> str:
    """Human-readable menu/dialog label for a convert target."""
    return target.desc or target.name


def _save_as_format(viewer: "MeshViewer", target: "Target", file_path: str):
    """Save the viewer's mesh to file_path in the target format."""
    mesh = viewer.render_widget.mesh_data
    if mesh is None:
        QtWidgets.QMessageBox.warning(
            viewer, "No Mesh Loaded", "Please load a mesh file before saving."
        )
        return
    with open(file_path, "wb") as f:
        f.write(processors.convert(mesh.raw_data, target.name, format="mesh"))


def _save_current_as_format(window: "ViewerTabWindow", target: "Target"):
    """Save the current tab's mesh in the target format via a save dialog."""
    viewer = cast("MeshViewer", window.tab_widget.currentWidget())
    if viewer is None:
        QtWidgets.QMessageBox.warning(
            window, "No File Opened", "Please open a mesh file before saving."
        )
        return
    label = _target_label(target)
    file_dialog = QtWidgets.QFileDialog()
    file_path, _ = file_dialog.getSaveFileName(
        None,
        f"Save Mesh as {label}",
        "",
        f"{label} Files (*{target.ext})",
    )
    if file_path:
        _save_as_format(viewer, target, file_path)
        QtWidgets.QMessageBox.information(
            window,
            "Save Successful",
            f"Mesh saved successfully as {label}.",
        )


def _save_all_as_format(window: "ViewerTabWindow", target: "Target"):
    """Save every open mesh in the target format into a chosen directory."""
    file_count = window.tab_widget.count()
    if file_count == 0:
        QtWidgets.QMessageBox.warning(
            window, "No Files Opened", "Please open mesh files before saving all."
        )
        return
    label = _target_label(target)
    save_directory = QtWidgets.QFileDialog.getExistingDirectory(
        None, f"Save All Meshes as {label}", ""
    )
    if not save_directory:
        return

    for i in range(window.tab_widget.count()):
        viewer = cast("MeshViewer", window.tab_widget.widget(i))
        if viewer is None or viewer.render_widget.mesh_data is None:
            continue
        file_path = os.path.join(
            save_directory,
            f"{os.path.splitext(window.tab_widget.tabText(i))[0]}{target.ext}",
        )
        _save_as_format(viewer, target, file_path)
    QtWidgets.QMessageBox.information(
        window,
        "Save All Successful",
        f"All meshes saved successfully as {label}.",
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

    for target in processors.info("mesh").targets:
        action = save_as_menu.addAction(_target_label(target))
        action.triggered.connect(
            lambda _, target=target: _save_current_as_format(window, target)
        )

    save_all_as_menu = window.menuBar().addMenu("Save All As")
    for target in processors.info("mesh").targets:
        action = save_all_as_menu.addAction(_target_label(target))
        action.triggered.connect(
            lambda _, target=target: _save_all_as_format(window, target)
        )
