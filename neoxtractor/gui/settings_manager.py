"""Provides a SettingsManager class to manage GUI mode settings."""

import json
import os
from typing import Any

from neoxtractor.core.utils.logger import get_logger
from neoxtractor.gui.settings_migration import CURRENT_SCHEMA_VERSION, run_migration

class SettingsManager:
    """
    A manager for application settings that handles loading, saving, and accessing
    configuration values.
    This class provides functionality to manage application settings through a JSON file.
    Settings can be accessed and modified using dot notation for nested configurations.
    Attributes:
        settings (dict): Dictionary containing the application settings.
    Args:
        path (str, optional): Path to the JSON configuration file. Defaults to "settings.json".
    Examples:
        >>> settings = SettingsManager("app_settings.json")
        >>> settings.load_config()  # Load existing settings or initialize empty
        >>> theme = settings.get("appearance.theme", "light")  # Get with default
        >>> settings.set("appearance.theme", "dark")  # Set nested value
        >>> settings.save_config()  # Save changes to file
    Notes:
        - The class handles common file operation errors and provides fallbacks.
        - Settings are stored in a nested dictionary structure.
        - Accessing non-existent settings with get() returns the provided default value.
        - Setting values with dot notation automatically creates necessary nested structures.
    """
    settings: dict[str, Any] = {}

    def __init__(self, path: str = "settings.json"):
        self._path = path
        self.load_config()

    @property
    def path(self):
        """Get the path to the config file."""
        return self._path

    def load_config(self):
        """Load settings from the config file."""
        try:
            if os.path.exists(self._path):
                with open(self._path, 'r', encoding='utf-8') as f:
                    json_dict = json.load(f)
                migrated = run_migration(json_dict)
                self.settings = json_dict
                if migrated:
                    get_logger().info("Settings migrated to the latest schema version.")
                    self.save_config()  # Save migrated settings
            else:
                self.settings = {}
            return True
        except (json.JSONDecodeError, FileNotFoundError, PermissionError) as e:
            get_logger().error("Error loading config: %s", e)
            self.settings = {}
            return False

    def save_config(self):
        """Save current settings to the config file."""
        try:
            with open(self._path, 'w', encoding='utf-8') as f:
                json_dict = {**self.settings}  # Create a copy to avoid modifying the original
                json_dict["schema_version"] = CURRENT_SCHEMA_VERSION
                json.dump(json_dict, f, indent=4)
            return True
        except (FileNotFoundError, PermissionError, OSError) as e:
            get_logger().error("Error saving config: %s", e)
            return False

    def get(self, key: str, default: Any = None, save = False) -> Any:
        """
        Get a value from settings using dot notation.
        
        Example: 
            settings.get("appearance.theme")
        """
        parts = key.split('.')
        current = self.settings

        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return default

        if save:
            self.save_config()

        return current

    def set(self, key: str, value: Any, save = False) -> None:
        """
        Set a value in settings using dot notation.
        
        Example:
            settings.set("appearance.theme", "dark")
        """
        parts = key.split('.')
        current = self.settings

        # Navigate the dictionary structure
        for part in parts[:-1]:
            if part not in current or not isinstance(current[part], dict):
                current[part] = {}
            current = current[part]

        # Set the value at the final level
        current[parts[-1]] = value

        if save:
            self.save_config()

    def __getattr__(self, name: str) -> Any:
        """Get a setting using dot notation."""
        return self.get(name)

    def __setattr__(self, name: str, value: Any) -> None:
        """Set a setting using dot notation."""
        if name in ["settings", "_path"]:
            super().__setattr__(name, value)
        else:
            self.set(name, value)
