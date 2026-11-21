"""
Viewer utility functions for viewers.

Modified from NeoXtractor (AGPL-3.0) in 2026 by this patch set: see NOTICE.md.
"""

from gui.widgets.viewer import Viewer
from gui.widgets.viewers.audio_viewer import AudioViewer
from gui.widgets.viewers.bnk_viewer import BnkViewer
from gui.widgets.viewers.cocos_viewer.cocos_viewer import CocosViewer
from gui.widgets.viewers.code_editor import CodeEditor
from gui.widgets.viewers.hex_viewer import HexViewer
from gui.widgets.viewers.mesh_viewer.viewer_widget import MeshViewer
from gui.widgets.viewers.pyc_viewer import PycViewer
from gui.widgets.viewers.texture_viewer import TextureViewer

ALL_VIEWERS: list[type[Viewer]] = [
    HexViewer,
    CodeEditor,
    MeshViewer,
    TextureViewer,
    BnkViewer,
    AudioViewer,
    CocosViewer,
    PycViewer,
]


def find_best_viewer(extension: str, is_text: bool = False) -> type[Viewer]:
    """
    The viewer for a file extension: the first one that lists it, else the code editor for text and the
    hex viewer for anything else.
    """
    normalized_extension = str(extension or "").lower().lstrip(".")
    if normalized_extension == "mesh":
        return MeshViewer
    for previewer in ALL_VIEWERS:
        if not hasattr(previewer, "accepted_extensions"):
            continue
        accepted_extensions = {str(value).lower().lstrip(".") for value in getattr(previewer, "accepted_extensions")}
        if normalized_extension in accepted_extensions:
            return previewer
    if is_text:
        return CodeEditor
    return HexViewer
