"""Theme management system for NeoXtractor."""

import json
import os
import re
from typing import Any, Dict, Optional, cast

from PySide6 import QtCore, QtWidgets

from neoxtractor.core.logger import get_logger
from neoxtractor.core.utils import get_application_path

SYSTEM_COLORS = {
    "palette": {
        "background": "#ffffff",
        "surface": "#f8f9fa",
        "surface_variant": "#e9ecef",
        "primary": "#1976d2",
        "primary_variant": "#1565c0",
        "secondary": "#424242",
        "accent": "#ff5722",
        "text": "#212121",
        "text_secondary": "#757575",
        "text_disabled": "#bdbdbd",
        "border": "#e0e0e0",
        "border_focus": "#1976d2",
        "hover": "#f5f5f5",
        "selected": "#e3f2fd",
        "pressed": "#bbdefb",
        "disabled": "#f5f5f5",
        "error": "#d32f2f",
        "warning": "#f57c00",
        "success": "#388e3c",
        "info": "#1976d2",
    }
}


class ThemeManager(QtCore.QObject):
    """Central theme manager for the application."""

    theme_changed = QtCore.Signal(str)

    def __init__(self, parent: Optional[QtCore.QObject] = None):
        super().__init__(parent)
        self._current_theme: str | None = None  # None = system theme
        self._app = cast(QtWidgets.QApplication, QtWidgets.QApplication.instance())
        self._logger = get_logger()

        # Path to themes directory
        self._themes_path = os.path.join(get_application_path(), "data", "themes")

        # Cache for loaded themes
        self._theme_cache: Dict[str, Dict[str, Any]] = {}

        self._flattened_colors: Dict[str, str] | None = None
        self._flattened_system_colors = self._flatten_colors(SYSTEM_COLORS)

        # Load theme definitions
        self._load_system_theme_override()
        self._load_theme_definitions()

        # Apply system theme in any cases (settings is not loaded at this point)
        self._apply_system_theme()

    def get_current_theme(self) -> str | None:
        """Get the currently active theme."""
        return self._current_theme

    def set_theme(self, theme: str | None) -> None:
        """Set the application theme.

        Args:
            theme: Theme name or None for system theme
        """
        if theme == self._current_theme:
            return

        theme_name = theme if theme is not None else "system"
        self._logger.info("Switching theme to: %s", theme_name)
        self._current_theme = theme

        if theme is None:
            self._apply_system_theme()
        else:
            self._apply_custom_theme(theme)

        self.theme_changed.emit(theme_name)

    def _load_system_theme_override(self):
        def_file = os.path.join(self._themes_path, "system", "definition.json")
        if not os.path.exists(def_file):
            self._logger.debug("No system theme definition found.")
            self._overrided_system_theme_colors = None
        else:
            with open(def_file, "r", encoding="utf-8") as f:
                definition = json.load(f)

            self._overrided_system_theme_colors = self._flatten_colors(definition)

            self._logger.debug("Loaded system theme definition from %s", def_file)

        style_file = os.path.join(self._themes_path, "system", "style.qss")
        if not os.path.exists(style_file):
            self._logger.debug("No system theme stylesheet found.")
            self._overrided_system_theme_stylesheet = ""
        else:
            with open(style_file, "r", encoding="utf-8") as f:
                stylesheet = f.read()
                self._overrided_system_theme_stylesheet = (
                    self._generate_stylesheet_from_template(
                        stylesheet,
                        SYSTEM_COLORS
                        if self._overrided_system_theme_colors is None
                        else self._overrided_system_theme_colors,
                    )
                )

            self._logger.debug("Loaded system theme stylesheet from %s", style_file)

    def _load_theme_definitions(self) -> None:
        """Load theme definitions from theme files."""
        if not os.path.exists(self._themes_path):
            self._logger.warning("Themes directory not found: %s", self._themes_path)
            return

        # Discover themes by scanning the themes directory
        try:
            for item in os.listdir(self._themes_path):
                if item == "system":
                    continue  # Skip the system theme directory
                theme_dir = os.path.join(self._themes_path, item)
                if os.path.isdir(theme_dir):
                    try:
                        self._load_theme_from_directory(item, theme_dir)
                    except Exception as e:
                        self._logger.error("Failed to load theme %s: %s", item, e)
        except OSError as e:
            self._logger.error("Error reading themes directory: %s", e)
            return

        self._logger.info("Loaded %d themes from files", len(self._theme_cache))

    def _load_theme_from_directory(self, theme_name: str, theme_dir: str) -> None:
        """Load a theme from its directory."""
        def_file = os.path.join(theme_dir, "definition.json")
        style_file = os.path.join(theme_dir, "style.qss")

        if not os.path.exists(def_file):
            raise FileNotFoundError(f"Definition file not found: {def_file}")

        if not os.path.exists(style_file):
            raise FileNotFoundError(f"Style file not found: {style_file}")

        # Load definition
        with open(def_file, "r", encoding="utf-8") as f:
            definition = json.load(f)

        # Load stylesheet template
        with open(style_file, "r", encoding="utf-8") as f:
            style_template = f.read()

        # Extract theme metadata
        theme_info = {
            "name": definition.get("name", theme_name),
            "base": definition.get("base", "custom"),
            "description": definition.get("description", ""),
            "colors": definition.get("colors", {}),
            "style_template": style_template,
        }

        self._theme_cache[theme_name] = theme_info

        self._logger.debug(
            "Loaded theme: %s - %s (%s)",
            theme_name,
            theme_info["name"],
            theme_info["base"],
        )

    def _apply_system_theme(self) -> None:
        """Apply system theme by resetting to default style."""
        if self._app and isinstance(self._app, QtWidgets.QApplication):
            self._app.setStyleSheet(self._overrided_system_theme_stylesheet)
            # Let Qt use the system's default style

    def _apply_custom_theme(self, theme: str) -> None:
        """Apply a custom theme using stylesheets."""
        if not self._app or not isinstance(self._app, QtWidgets.QApplication):
            return

        theme_data = self._theme_cache.get(theme)
        if not theme_data:
            self._logger.warning("No theme data found for theme: %s", theme)
            return

        colors = theme_data.get("colors", {})

        self._flattened_colors = self._flatten_colors(colors)
        style_template = theme_data.get("style_template", "")

        if not colors or not style_template:
            self._logger.warning("Incomplete theme data for theme: %s", theme)
            return

        stylesheet = self._generate_stylesheet_from_template(style_template, colors)
        self._app.setStyleSheet(stylesheet)

    def _flatten_colors(self, definition: Dict[str, Dict]) -> Dict[str, str]:
        """Flatten the color structure for easier access."""
        flattened: Dict[str, str] = {}

        def _recursive_flattener(definition: Dict[str, Dict], top: str):
            temp = {}
            for key in definition.keys():
                if isinstance(definition[key], str):
                    temp[top + "." + key] = definition[key]
                else:
                    temp.update(_recursive_flattener(definition[key], top + "." + key))
            return temp

        for key in definition.keys():
            flattened.update(_recursive_flattener(definition[key], key))

        return flattened

    def _generate_stylesheet_from_template(
        self, template: str, definition: Dict[str, Any]
    ) -> str:
        """Generate Qt stylesheet from template and colors."""
        try:
            # Flatten the color structure for template substitution
            flattened_colors = self.__generate_stylesheet_colors(definition)

            # Replace @variable syntax with actual color values
            # Sort by length (descending) to replace longer variable names first
            # This prevents partial matches (e.g., @disabled matching inside @text_disabled)
            result = template
            sorted_colors = sorted(
                flattened_colors.items(), key=lambda x: len(x[0]), reverse=True
            )

            for color_name, color_value in sorted_colors:
                # Use word boundaries to ensure exact matches only
                pattern = r"@" + re.escape(color_name) + r"(?![a-zA-Z0-9_])"
                result = re.sub(pattern, color_value, result)

            return result

        except Exception as e:
            self._logger.error("Error generating stylesheet: %s", e)
            return ""

    def __generate_stylesheet_colors(
        self, definition: Dict[str, Any]
    ) -> Dict[str, str]:
        """Generate the colors for the stylesheet."""
        flattened = {}

        if "palette" in definition:
            flattened.update(definition["palette"])

        if "custom" in definition:
            for category, category_colors in definition["custom"].items():
                if isinstance(category_colors, dict):
                    for key, value in category_colors.items():
                        flattened[f"{category}_{key}"] = value

        # Also add the raw colors if they're already flattened
        if all(isinstance(v, str) for v in definition.values()):
            flattened.update(definition)

        return flattened

    def get_color(self, color_path: str, default: str | None = None) -> str | None:
        """Get a color value from the current theme.

        Args:
            color_path: Dot-separated path to the color (e.g., 'palette.primary' or 'custom.editor.background')

        Returns:
            The color hex value or system theme fallback
        """
        # For system theme, return hardcoded values
        if self._flattened_colors is None or self._current_theme is None:
            return self._get_system_color(color_path, default)

        if color_path in self._flattened_colors.keys():
            return self._flattened_colors[color_path]
        return None

    def _get_system_color(
        self, color_path: str, default: str | None = None
    ) -> str | None:
        """Get hardcoded color values for system theme."""
        if (
            self._overrided_system_theme_colors
            and color_path in self._overrided_system_theme_colors
        ):
            # Use the system theme colors if available
            return self._overrided_system_theme_colors.get(color_path)
        return self._flattened_system_colors.get(color_path, default)

    def get_available_themes(self) -> Dict[str, Dict[str, str]]:
        """Get information about all available themes.

        Returns:
            Dictionary mapping theme names to their metadata
        """
        themes = {}
        for theme_name, theme_data in self._theme_cache.items():
            themes[theme_name] = {
                "name": theme_data.get("name", theme_name),
                "description": theme_data.get("description", ""),
                "base": theme_data.get("base", "custom"),
            }
        return themes

    def get_theme_info(self, theme_name: str) -> Dict[str, str]:
        """Get metadata for a specific theme.

        Args:
            theme_name: The theme identifier

        Returns:
            Dictionary with theme metadata or empty dict if not found
        """
        theme_data = self._theme_cache.get(theme_name)
        if not theme_data:
            return {}

        return {
            "name": theme_data.get("name", theme_name),
            "description": theme_data.get("description", ""),
            "base": theme_data.get("base", "custom"),
        }

    @staticmethod
    def instance() -> "ThemeManager":
        """Get the global theme manager instance."""
        app = QtWidgets.QApplication.instance()
        if not app:
            raise RuntimeError("No QApplication instance found")

        theme_manager = app.property("theme_manager")
        if not theme_manager:
            theme_manager = ThemeManager()
            app.setProperty("theme_manager", theme_manager)

        return theme_manager
