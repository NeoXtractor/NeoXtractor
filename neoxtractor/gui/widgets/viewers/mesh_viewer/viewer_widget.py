"""Provides mesh viewer."""

from PySide6 import QtCore, QtWidgets

from neoxtractor.core.file import IFile
from neoxtractor.gui.widgets.tab_window_ui.mesh_viewer import setup_mesh_viewer_tab_window
from neoxtractor.gui.widgets.viewer import Viewer
from neoxtractor.gui.windows.viewer_tab_window import ICustomTabWindow

from .render_widget import MeshRenderWidget


class MeshViewer(Viewer, ICustomTabWindow):
    """
    A Qt widget that provides a 3D mesh viewer with interactive controls.
    This widget combines a mesh rendering area with control checkboxes for toggling
    various display options like wireframe mode, bone visibility, and normal vectors.
    Attributes:
        wireframe_checkbox (QtWidgets.QCheckBox): Checkbox to toggle wireframe rendering mode
        bone_checkbox (QtWidgets.QCheckBox): Checkbox to toggle bone display (enabled by default)
        normal_checkbox (QtWidgets.QCheckBox): Checkbox to toggle normal vector display
        render_widget (MeshRenderWidget): The OpenGL widget responsible for 3D mesh rendering
        load_mesh: Reference to the render widget's load_mesh method
        unload_mesh: Reference to the render widget's unload_mesh method
    Args:
        parent (QtWidgets.QWidget, optional): Parent widget. Defaults to None.
    The widget automatically connects checkbox state changes to the corresponding
    rendering options in the underlying MeshRenderWidget.
    """

    name = "Mesh Viewer"
    accepted_extensions = {"mesh"}
    setup_tab_window = setup_mesh_viewer_tab_window

    def __init__(self, parent=None):
        super().__init__(parent)

        self._file: IFile | None = None

        layout = QtWidgets.QVBoxLayout(self)

        control_layout = QtWidgets.QHBoxLayout()

        self.wireframe_checkbox = QtWidgets.QCheckBox("Wireframe Mode", self)

        def toggle_wireframe(state):
            self.render_widget.wireframe_mode = state == QtCore.Qt.CheckState.Checked

        self.wireframe_checkbox.checkStateChanged.connect(toggle_wireframe)
        control_layout.addWidget(self.wireframe_checkbox)

        self.bone_checkbox = QtWidgets.QCheckBox("Show Bones", self)
        self.bone_checkbox.setChecked(True)

        def toggle_bones(state):
            self.render_widget.draw_bones = state == QtCore.Qt.CheckState.Checked

        self.bone_checkbox.checkStateChanged.connect(toggle_bones)
        control_layout.addWidget(self.bone_checkbox)

        self.normal_checkbox = QtWidgets.QCheckBox("Show Normals", self)

        def toggle_normals(state):
            self.render_widget.draw_normals = state == QtCore.Qt.CheckState.Checked

        self.normal_checkbox.checkStateChanged.connect(toggle_normals)
        control_layout.addWidget(self.normal_checkbox)

        self.text_checkbox = QtWidgets.QCheckBox("Show Text", self)
        self.text_checkbox.setChecked(True)

        def toggle_text(state):
            self.render_widget.draw_text = state == QtCore.Qt.CheckState.Checked

        self.text_checkbox.checkStateChanged.connect(toggle_text)
        control_layout.addWidget(self.text_checkbox)

        layout.addLayout(control_layout)

        self.render_widget = MeshRenderWidget(self)
        layout.addWidget(self.render_widget)

    def set_file(self, file: IFile):
        self._file = file
        self.render_widget.load_mesh(file.data)

    def get_file(self) -> IFile | None:
        return self._file

    def unload_file(self):
        self._file = None
        self.render_widget.unload_mesh()
