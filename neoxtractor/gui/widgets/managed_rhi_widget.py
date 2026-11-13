"""Provides managed RHI widget."""

from PySide6 import QtWidgets

from neoxtractor.gui.app_state import AppState


class ManagedRhiWidget(QtWidgets.QRhiWidget):
    """
    A managed QRhiWidget that automatically handles the RHI backend and MSAA settings.
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        try:
            state = AppState.instance()
        except RuntimeError:
            # No AppState yet (headless tests): the widget stays unconfigured.
            return

        state.managed_rhi_widgets.append(self)

        backend = state.graphics_backend
        if backend != QtWidgets.QRhiWidget.Api.Null.value:
            self.setApi(QtWidgets.QRhiWidget.Api(backend))

        msaa = state.settings.get("graphics.msaa", 1)
        self.setSampleCount(msaa)
