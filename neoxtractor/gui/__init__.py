"""GUI bootstrapper"""

import os

from PySide6 import QtWidgets

from neoxtractor.core.utils.logger import get_logger, enable_qt_log_handling
from neoxtractor.core.utils.app import get_application_path
from neoxtractor.gui.app_state import AppState
from neoxtractor.gui.config import ConfigVersionTooNew
from neoxtractor.gui.config_manager import ConfigManager
from neoxtractor.gui.fonts import load_font
from neoxtractor.gui.settings_manager import SettingsManager
from neoxtractor.gui.settings_migration import SettingsVersionTooNew
from neoxtractor.gui.theme import ThemeManager
from neoxtractor.gui.windows.main_window import MainWindow


def main(argv: list[str]) -> int:
    """Run NeoXtractor as a GUI application.

    *argv* is the subcommand's ``sys.argv``-style list handed over by the
    entry point (program name first, then the tokens after ``gui``); the
    GUI never reads ``sys.argv`` itself, so ``QApplication`` receives
    exactly this list and application options parsed upstream cannot
    collide with Qt's own.
    """

    get_logger().info("Starting NeoXtractor in GUI mode...")

    # Route Qt's own debug/warning output into the project log stream.
    enable_qt_log_handling()

    app = QtWidgets.QApplication(list(argv))

    fonts_dir = os.path.join(get_application_path(), "data", "fonts")

    # Fonts used by About window
    load_font(
        "NotoSansLatin_600_italic",
        os.path.join(fonts_dir, "noto-sans-latin-600-italic.ttf"),
    )
    load_font("Orbitron", os.path.join(fonts_dir, "orbitron-latin-400-normal.ttf"))

    # Fonts used by Code Editor
    load_font("SpaceMono", os.path.join(fonts_dir, "space-mono-latin-400-normal.ttf"))
    load_font(
        "SpaceMono_italic", os.path.join(fonts_dir, "space-mono-latin-400-italic.ttf")
    )

    get_logger().debug("Setup config manager...")
    config_manager = ConfigManager()
    get_logger().debug("Setup settings manager...")
    # A settings file stamped above this build's schema version is fatal:
    # rewriting it down-level would corrupt the newer format.
    try:
        settings_manager = SettingsManager()
    except SettingsVersionTooNew as exc:
        QtWidgets.QMessageBox.critical(None, "NeoXtractor Outdated", str(exc))
        get_logger().error("Startup aborted: %s", exc)
        return 1
    get_logger().debug("Setup theme manager...")
    theme_manager = ThemeManager()

    # Apply saved theme or default to system theme
    theme_manager.set_theme(settings_manager.get("appearance.theme", None))

    graphics_backend = settings_manager.get(
        "graphics.backend", QtWidgets.QRhiWidget.Api.Null.value
    )

    AppState.build(
        settings=settings_manager,
        configs=config_manager,
        theme=theme_manager,
        graphics_backend=graphics_backend,
    )

    # A seed demanding a newer config version is fatal -- this build cannot
    # speak it.
    seed_path = os.path.join(get_application_path(), "configs")
    try:
        seed_problems = config_manager.load_seeds(seed_path)
    except ConfigVersionTooNew as exc:
        QtWidgets.QMessageBox.critical(
            None, "NeoXtractor Outdated", str(exc)
        )
        get_logger().error("Startup aborted: %s", exc)
        return 1
    for problem in seed_problems:
        get_logger().warning("Skipping seed config: %s", problem)

    config_manager.load_user(settings_manager.get("gameconfigs", []))
    if config_manager.prune_redundant():
        settings_manager.set("gameconfigs", config_manager.to_user_dicts(), True)

    get_logger().debug("Showing main window.")
    main_window = MainWindow()
    main_window.resize(1000, 800)
    main_window.show()

    return app.exec()
