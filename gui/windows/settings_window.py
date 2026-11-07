"""Provides a settings window for the application."""

import sys
from typing import TYPE_CHECKING, Any, cast

from PySide6 import QtGui, QtWidgets

from gui.settings_manager import SettingsManager
from gui.theme import ThemeManager
from gui.widgets.color_triangle_widget import ColorTriangleWidget
from gui.widgets.managed_rhi_widget import ManagedRhiWidget

if TYPE_CHECKING:
    from gui.windows.main_window import MainWindow


class SettingsWindow(QtWidgets.QDialog):
    """
    A window for application settings.
    """

    def __init__(
        self, settings_manager: SettingsManager, parent: QtWidgets.QWidget | None = None
    ):
        super().__init__(parent)

        self.setWindowTitle("Settings")
        self.setMinimumSize(800, 600)

        self._settings_manager = settings_manager

        self._pending_changes: dict[str, Any] = {}

        layout = QtWidgets.QVBoxLayout(self)

        layout.addWidget(self._create_appearance_settings())
        layout.addWidget(self._create_graphics_settings())
        layout.addWidget(self._create_audio_settings())
        layout.addWidget(self._create_video_settings())

        layout.addStretch()

        action_layout = QtWidgets.QHBoxLayout()
        action_layout.addStretch()

        self.apply_button = QtWidgets.QPushButton("Apply", self)
        self.apply_button.clicked.connect(self.save_settings)
        action_layout.addWidget(self.apply_button)

        self.save_button = QtWidgets.QPushButton("Save", self)
        self.save_button.setDefault(True)
        self.save_button.clicked.connect(lambda: (self.save_settings(), self.close()))
        action_layout.addWidget(self.save_button)

        layout.addLayout(action_layout)

    def _create_appearance_settings(self):
        """Create the appearance settings section."""
        appearance_group = QtWidgets.QGroupBox("Appearance Settings", self)
        appearance_layout = QtWidgets.QVBoxLayout(appearance_group)

        # Theme selection
        theme_layout = QtWidgets.QHBoxLayout()
        theme_layout.addWidget(QtWidgets.QLabel("Theme:", self))

        self.theme_combobox = QtWidgets.QComboBox(self)

        # Add system theme option
        self.theme_combobox.addItem("System", None)

        # Add available themes from theme manager
        try:
            theme_manager = ThemeManager.instance()
            available_themes = theme_manager.get_available_themes()

            for theme_id, theme_info in available_themes.items():
                display_name = theme_info.get("name", theme_id.title())
                self.theme_combobox.addItem(display_name, theme_id)

        except Exception as e:
            print(f"Error loading themes: {e}")

        def set_theme(theme_id):
            self._pending_changes["appearance.theme"] = theme_id

        self.theme_combobox.currentIndexChanged.connect(
            lambda idx: set_theme(self.theme_combobox.itemData(idx))
        )

        theme_layout.addWidget(self.theme_combobox)
        theme_layout.addStretch()

        appearance_layout.addLayout(theme_layout)

        # Theme description
        self.theme_description = QtWidgets.QLabel("", self)
        self.theme_description.setWordWrap(True)
        self.theme_description.setStyleSheet("color: gray; font-style: italic;")
        appearance_layout.addWidget(self.theme_description)

        # Update description when theme changes
        def update_theme_description():
            current_data = self.theme_combobox.currentData()
            if current_data is None:
                self.theme_description.setText("Use the system's default theme")
            else:
                try:
                    theme_manager = ThemeManager.instance()
                    theme_info = theme_manager.get_theme_info(current_data)
                    description = theme_info.get(
                        "description", "No description available"
                    )
                    self.theme_description.setText(description)
                except Exception:
                    self.theme_description.setText("")

        self.theme_combobox.currentIndexChanged.connect(update_theme_description)

        appearance_layout.addStretch()

        return appearance_group

    def _create_graphics_settings(self):
        """
        Create the graphics settings section.
        """
        graphics_group = QtWidgets.QGroupBox("Graphics Settings", self)
        graphics_layout = QtWidgets.QHBoxLayout(graphics_group)

        options_layout = QtWidgets.QVBoxLayout()

        options_layout.addStretch()

        self.backend_combobox = QtWidgets.QComboBox(self)

        self.backend_combobox.addItem("Auto", QtWidgets.QRhiWidget.Api.Null)

        self.backend_combobox.addItem("OpenGL", QtWidgets.QRhiWidget.Api.OpenGL)
        self.backend_combobox.addItem("Vulkan", QtWidgets.QRhiWidget.Api.Vulkan)

        if sys.platform == "win32":
            self.backend_combobox.addItem(
                "Direct3D 11", QtWidgets.QRhiWidget.Api.Direct3D11
            )
            self.backend_combobox.addItem(
                "Direct3D 12", QtWidgets.QRhiWidget.Api.Direct3D12
            )

        if sys.platform == "darwin":
            self.backend_combobox.addItem("Metal", QtWidgets.QRhiWidget.Api.Metal)

        def set_backend(api: QtWidgets.QRhiWidget.Api):
            self._pending_changes["graphics.backend"] = api.value

        self.backend_combobox.currentIndexChanged.connect(
            lambda idx: set_backend(self.backend_combobox.itemData(idx))
        )

        options_layout.addWidget(QtWidgets.QLabel("Graphics API:", self))
        options_layout.addWidget(self.backend_combobox)
        self.current_backend = QtWidgets.QLabel("", self)
        options_layout.addWidget(self.current_backend)
        options_layout.addWidget(
            QtWidgets.QLabel("To apply the API change, restart the application.", self)
        )
        options_layout.addStretch()

        options_layout.addWidget(QtWidgets.QLabel("MSAA:", self))
        self.msaa_combobox = QtWidgets.QComboBox(self)

        def set_msaa(value: int):
            self._pending_changes["graphics.msaa"] = value
            self.triangle_widget.setSampleCount(value)

        self.msaa_combobox.currentIndexChanged.connect(
            lambda idx: set_msaa(self.msaa_combobox.itemData(idx))
        )
        options_layout.addWidget(self.msaa_combobox)

        options_layout.addStretch()

        graphics_layout.addLayout(options_layout, stretch=3)

        preview_widget = QtWidgets.QWidget(self)
        preview_widget.setMinimumWidth(100)
        preview_widget.setMaximumWidth(300)
        preview_layout = QtWidgets.QVBoxLayout(preview_widget)
        self.triangle_widget = ColorTriangleWidget(preview_widget)
        preview_layout.addWidget(self.triangle_widget)

        graphics_layout.addWidget(preview_widget, stretch=1)

        return graphics_group

    def _create_audio_settings(self):
        """Create the audio export settings section."""
        audio_group = QtWidgets.QGroupBox("Audio Settings", self)
        audio_layout = QtWidgets.QVBoxLayout(audio_group)

        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Wwise audio export:", self))

        self.audio_export_combobox = QtWidgets.QComboBox(self)
        self.audio_export_combobox.addItem("Keep original (.wem)", "origin")
        self.audio_export_combobox.addItem("Ogg Vorbis (.ogg)", "ogg")
        self.audio_export_combobox.currentIndexChanged.connect(
            lambda idx: (
                self._pending_changes.__setitem__(
                    "audio.wwise_export", self.audio_export_combobox.itemData(idx)
                ),
                self._update_audio_export_hint(),
            )
        )
        row.addWidget(self.audio_export_combobox)
        row.addStretch()
        audio_layout.addLayout(row)

        description = QtWidgets.QLabel(
            "Keep original leaves .wem files untouched. Ogg transcodes every "
            "extracted WEM, including the audio embedded in .bnk files.",
            self,
        )
        description.setWordWrap(True)
        description.setStyleSheet("color: gray; font-style: italic;")
        audio_layout.addWidget(description)

        self.audio_export_warning = QtWidgets.QLabel(
            "Ogg export is slower: every WEM, including the audio inside .bnk "
            "files, is transcoded one by one.",
            self,
        )
        self.audio_export_warning.setWordWrap(True)
        self.audio_export_warning.setStyleSheet("color: #e6a23c;")
        audio_layout.addWidget(self.audio_export_warning)

        self.dedupe_media_checkbox = QtWidgets.QCheckBox(
            "List each audio file only once", self
        )
        self.dedupe_media_checkbox.setChecked(True)
        self.dedupe_media_checkbox.toggled.connect(
            lambda checked: self._pending_changes.__setitem__(
                "audio.wwise_dedupe_media", checked
            )
        )
        audio_layout.addWidget(self.dedupe_media_checkbox)

        dedupe_description = QtWidgets.QLabel(
            "On: audio repeated across several banks (or also stored as a "
            "standalone .wem) is listed once, so the export has no duplicates. "
            "Off: every reference is listed and exported separately. Takes "
            "effect the next time a package is opened.",
            self,
        )
        dedupe_description.setWordWrap(True)
        dedupe_description.setStyleSheet("color: gray; font-style: italic;")
        audio_layout.addWidget(dedupe_description)

        self._update_audio_export_hint()

        return audio_group

    def _update_audio_export_hint(self):
        """Show the slow-export warning only when Ogg transcoding is selected."""
        self.audio_export_warning.setVisible(
            self.audio_export_combobox.currentData() == "ogg"
        )

    def _create_video_settings(self):
        """Create the NBS video export settings section."""
        video_group = QtWidgets.QGroupBox("Video Settings", self)
        video_layout = QtWidgets.QVBoxLayout(video_group)

        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("NBS video export:", self))

        self.nbs_export_combobox = QtWidgets.QComboBox(self)
        self.nbs_export_combobox.addItem("Keep original (.nbs)", "origin")
        self.nbs_export_combobox.addItem("Matroska (.mkv)", "mkv")
        self.nbs_export_combobox.addItem("Raw IVF (.ivf)", "ivf")
        self.nbs_export_combobox.currentIndexChanged.connect(
            lambda idx: (
                self._pending_changes.__setitem__(
                    "video.nbs_export", self.nbs_export_combobox.itemData(idx)
                ),
                self._update_nbs_export_hint(),
            )
        )
        row.addWidget(self.nbs_export_combobox)
        row.addStretch()
        video_layout.addLayout(row)

        description = QtWidgets.QLabel(
            "Keep original stores the .nbs archive unchanged. Matroska packs the "
            "VP9 frames and the MP3 track into one playable file without re-encoding.",
            self,
        )
        description.setWordWrap(True)
        description.setStyleSheet("color: gray; font-style: italic;")
        video_layout.addWidget(description)

        self.nbs_export_warning = QtWidgets.QLabel(
            "IVF has no audio track, so exported videos will be silent. Choose "
            "Matroska (.mkv) to keep the sound.",
            self,
        )
        self.nbs_export_warning.setWordWrap(True)
        self.nbs_export_warning.setStyleSheet("color: #e6a23c;")
        video_layout.addWidget(self.nbs_export_warning)

        self._update_nbs_export_hint()

        return video_group

    def _update_nbs_export_hint(self):
        """Show the silent-export warning only when raw IVF is selected."""
        self.nbs_export_warning.setVisible(
            self.nbs_export_combobox.currentData() == "ivf"
        )

    def _load_settings(self):
        """
        Load settings from the settings manager.
        """
        # Load theme setting
        current_theme = self._settings_manager.get("appearance.theme", "system")
        if current_theme == "system":
            # Select system theme (None data)
            for i in range(self.theme_combobox.count()):
                if self.theme_combobox.itemData(i) is None:
                    self.theme_combobox.setCurrentIndex(i)
                    break
        else:
            # Select specific theme
            theme_index = self.theme_combobox.findData(current_theme)
            if theme_index != -1:
                self.theme_combobox.setCurrentIndex(theme_index)

        # Trigger theme description update
        self.theme_combobox.currentIndexChanged.emit(self.theme_combobox.currentIndex())

        # Load graphics settings
        backend = self._settings_manager.get(
            "graphics.backend", QtWidgets.QRhiWidget.Api.Null.value
        )
        index = self.backend_combobox.findData(QtWidgets.QRhiWidget.Api(backend))
        if index != -1:
            self.backend_combobox.setCurrentIndex(index)
        current_api = self.triangle_widget.api()
        current_backend = current_api.name
        backend_combo_item = self.backend_combobox.findData(current_api)
        if backend_combo_item != -1:
            current_backend = self.backend_combobox.itemText(backend_combo_item)
        self.current_backend.setText(f"Current Backend: {current_backend}")

        for sample_count in self.triangle_widget.rhi().supportedSampleCounts():
            if sample_count == 1:
                self.msaa_combobox.addItem("No MSAA", 1)
            else:
                self.msaa_combobox.addItem(f"{sample_count}x MSAA", sample_count)

        msaa = self._settings_manager.get("graphics.msaa", 1)
        msaa_combo_item = self.msaa_combobox.findData(msaa)
        if msaa_combo_item != -1:
            self.msaa_combobox.setCurrentIndex(msaa_combo_item)

        audio_export = self._settings_manager.get("audio.wwise_export", "origin")
        audio_index = self.audio_export_combobox.findData(audio_export)
        if audio_index != -1:
            self.audio_export_combobox.setCurrentIndex(audio_index)
        self._update_audio_export_hint()

        self.dedupe_media_checkbox.setChecked(
            self._settings_manager.get("audio.wwise_dedupe_media", True)
        )

        nbs_export = self._settings_manager.get("video.nbs_export", "mkv")
        nbs_index = self.nbs_export_combobox.findData(nbs_export)
        if nbs_index != -1:
            self.nbs_export_combobox.setCurrentIndex(nbs_index)
        self._update_nbs_export_hint()

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        """
        Handle the show event to load settings.
        """
        super().showEvent(event)
        self._load_settings()

    def save_settings(self):
        """
        Save the settings to the settings manager.
        """
        for key, value in self._pending_changes.items():
            self._settings_manager.set(key, value)
        self._settings_manager.save_config()

        # Apply theme changes immediately
        if "appearance.theme" in self._pending_changes:
            try:
                theme_manager = ThemeManager.instance()
                theme_manager.set_theme(self._pending_changes["appearance.theme"])
            except Exception as e:
                print(f"Error applying theme: {e}")

        main_window = cast("MainWindow", self.parent())

        # Apply MSAA to all QRhiWidgets
        msaa = self._settings_manager.get("graphics.msaa", 1)
        rhi_widgets: list[ManagedRhiWidget] = main_window.app.property(
            "managed_rhi_widgets"
        )
        if rhi_widgets is None:
            # If no managed RHI widgets list exists, we cannot proceed.
            return
        for rhi_widget in rhi_widgets:
            rhi_widget.setSampleCount(msaa)
