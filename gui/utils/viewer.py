"""Viewer utility functions for viewers."""

from gui.widgets.viewer import Viewer
from gui.widgets.viewers.audio_viewer import AudioViewer
from gui.widgets.viewers.bnk_viewer import BnkViewer
from gui.widgets.viewers.cocos_viewer.cocos_viewer import CocosViewer
from gui.widgets.viewers.code_editor import CodeEditor
from gui.widgets.viewers.hex_viewer import HexViewer
from gui.widgets.viewers.mesh_viewer.viewer_widget import MeshViewer
from gui.widgets.viewers.nbs_viewer import NbsViewer
from gui.widgets.viewers.pyc_viewer import PycViewer
from gui.widgets.viewers.texture_viewer import TextureViewer

ALL_VIEWERS: list[type[Viewer]] = [
    HexViewer,
    CodeEditor,
    TextureViewer,
    MeshViewer,
    BnkViewer,
    AudioViewer,
    NbsViewer,
    CocosViewer,
    PycViewer,
]


def find_best_viewer(extension: str, is_text=False) -> type[Viewer]:
    """
    Finds and selects the most appropriate previewer widget for the given NPK entry.
    This method iterates through available previewers and selects one based on the file extension
    of the NPK entry. If a previewer declares compatibility with the entry's extension through
    its 'accepted_extensions' attribute, that previewer is selected. If no specialized previewer
    is found, it defaults to a code editor for text files or a hex viewer for binary files.
    Args:
        extension (str): The file extension to find a previewer for
        is_text (bool): Whether the file is a text file or not
    Returns:
        Type[QtWidgets.QWidget]: The class of the selected previewer widget
    """

    for previewer in ALL_VIEWERS:
        if hasattr(previewer, "accepted_extensions"):
            if extension in getattr(previewer, "accepted_extensions"):
                return previewer

    return CodeEditor if is_text else HexViewer
