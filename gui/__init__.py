"""GUI bootstrapper"""

import os
import sys

from PySide6 import QtWidgets, QtCore
from core.utils import get_application_path
from core.logger import get_logger
from gui import addons
from gui.config_manager import ConfigManager
from gui.fonts import load_font
from gui.settings_manager import SettingsManager
from gui.theme import ThemeManager
from gui.utils.config import load_config_manager_from_settings, save_config_manager_to_settings
from gui.windows.main_window import MainWindow

def run():
    """Run NeoXtractor as a GUI application."""

    get_logger().info("Starting NeoXtractor in GUI mode...")
    addons.install_speedups()

    app = QtWidgets.QApplication(sys.argv)

    fonts_dir = os.path.join(get_application_path(), "data", "fonts")

    # Fonts used by About window
    load_font("NotoSansLatin_600_italic", os.path.join(fonts_dir, "noto-sans-latin-600-italic.ttf"))
    load_font("Orbitron", os.path.join(fonts_dir, "orbitron-latin-400-normal.ttf"))

    # Fonts used by Code Editor
    load_font("SpaceMono", os.path.join(fonts_dir, "space-mono-latin-400-normal.ttf"))
    load_font("SpaceMono_italic", os.path.join(fonts_dir, "space-mono-latin-400-italic.ttf"))

    first_run = not os.path.exists("settings.json")

    get_logger().debug("Setup config manager...")
    config_manager = ConfigManager()
    get_logger().debug("Setup settings manager...")
    settings_manager = SettingsManager()
    get_logger().debug("Setup theme manager...")
    theme_manager = ThemeManager()

    app.setProperty("settings_manager", settings_manager)
    app.setProperty("config_manager", config_manager)
    app.setProperty("theme_manager", theme_manager)

    # Apply saved theme or default to system theme
    theme_manager.set_theme(settings_manager.get("appearance.theme", None))

    app.setProperty("graphics_backend", settings_manager.get("graphics.backend", QtWidgets.QRhiWidget.Api.Null.value))

    # First run, setup default settings
    if first_run:
        get_logger().info("First run, setting up default settings.")
        config_manager.load_from_path(os.path.join(get_application_path(), "configs"))
        save_config_manager_to_settings(config_manager, settings_manager)
    else:
        load_config_manager_from_settings(settings_manager, config_manager)

    get_logger().debug("Showing main window.")
    main_window = MainWindow()
    main_window.resize(1000, 800)
    main_window.show()
    addons.install(app)

    app.exec()
