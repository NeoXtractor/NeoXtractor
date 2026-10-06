"""Provides managed RHI widget."""

from PySide6 import QtWidgets

from neoxtractor.gui.settings_manager import SettingsManager


class ManagedRhiWidget(QtWidgets.QRhiWidget):
    """
    A managed QRhiWidget that automatically handles the RHI backend and MSAA settings.
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        application = QtWidgets.QApplication.instance()
        if application is None:
            # If no QApplication instance exists, we cannot proceed.
            return

        managed_rhi_widgets = application.property("managed_rhi_widgets")
        if managed_rhi_widgets is None:
            # If no managed RHI widgets list exists, create one.
            managed_rhi_widgets = []

        managed_rhi_widgets.append(self)
        application.setProperty("managed_rhi_widgets", managed_rhi_widgets)

        settings_manager: SettingsManager = application.property("settings_manager")
        if settings_manager is None:
            # If no settings manager is available, we cannot proceed.
            return

        backend = application.property("graphics_backend")
        if backend is None:
            # If no graphics backend is set, don't proceed.
            return

        if backend != QtWidgets.QRhiWidget.Api.Null.value:
            self.setApi(QtWidgets.QRhiWidget.Api(backend))

        msaa = settings_manager.get("graphics.msaa", 1)
        self.setSampleCount(msaa)
