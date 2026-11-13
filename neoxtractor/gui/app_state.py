"""Typed process singletons for the GUI.

Settings, configs, and theme are reached through one strongly-typed object
built once by ``main()``.  The current archive and active game config are
NOT process state -- they live on the main window and are passed explicitly
to the widgets and models that need them, so a future multi-window build
needs no change here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from dataclasses import dataclass, field

if TYPE_CHECKING:
    from neoxtractor.gui.config_manager import ConfigManager
    from neoxtractor.gui.settings_manager import SettingsManager
    from neoxtractor.gui.theme.theme_manager import ThemeManager
    from neoxtractor.gui.widgets.managed_rhi_widget import ManagedRhiWidget


@dataclass
class AppState:
    """The application-wide singletons, constructed once by ``main()``."""

    settings: "SettingsManager"
    configs: "ConfigManager"
    theme: "ThemeManager"
    graphics_backend: int = 0
    managed_rhi_widgets: list["ManagedRhiWidget"] = field(default_factory=list)

    _instance: ClassVar["AppState | None"] = None

    @classmethod
    def build(
        cls,
        settings: "SettingsManager",
        configs: "ConfigManager",
        theme: "ThemeManager",
        graphics_backend: int,
    ) -> "AppState":
        """Construct the single instance (``main()`` calls this once)."""
        cls._instance = cls(
            settings=settings,
            configs=configs,
            theme=theme,
            graphics_backend=graphics_backend,
        )
        return cls._instance

    @classmethod
    def instance(cls) -> "AppState":
        if cls._instance is None:
            raise RuntimeError("AppState.build() must run before instance()")
        return cls._instance
