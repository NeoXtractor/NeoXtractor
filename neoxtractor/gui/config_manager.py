"""Provides game config manager."""

import os
from neoxtractor.core.config import Config

class ConfigManager:
    """Game config manager."""

    def __init__(self):
        self.configs: list[Config] = []
        self._name_dict: dict[str, Config] = {}

    def add_config(self, cfg: Config):
        """Add a game config to this manager."""
        if cfg.name in self._name_dict:
            raise ValueError("Config is already in Config Manager.")
        self.configs.append(cfg)
        self._name_dict[cfg.name] = cfg

    def add_configs(self, cfgs: list[Config]):
        """Add multiple game configs to this manager."""
        for cfg in cfgs:
            self.add_config(cfg)

    def remove_config(self, cfg: Config | str):
        """Remove a game config by name or instance."""
        if isinstance(cfg, str):
            cfg = self._name_dict[cfg]
        self.configs.remove(cfg)
        self._name_dict.pop(cfg.name)

    def clear(self):
        """Clear all configs."""
        self.configs.clear()
        self._name_dict.clear()

    def get_config(self, name: str) -> Config | None:
        """Get a config by name."""
        return self._name_dict.get(name)

    def get_config_index(self, cfg: Config | str) -> int | None:
        """Get the index of a config by name or instance."""
        if isinstance(cfg, str):
            ncfg = self._name_dict.get(cfg)
            if ncfg is None:
                return None
            cfg = ncfg
        try:
            return self.configs.index(cfg)
        except ValueError:
            return None

    def update_config(self, index: int, new_cfg: Config):
        """Update an existing config by index."""
        if index < 0 or index >= len(self.configs):
            raise IndexError("Config index out of range.")

        old_cfg = self.configs[index]
        self.configs[index] = new_cfg

        # Update the name dictionary
        self._name_dict.pop(old_cfg.name)
        self._name_dict[new_cfg.name] = new_cfg

    def load_from_path(self, path = "configs", overwrite = False):
        """Load all configs in path to this manager."""
        for cfg_file in os.listdir(path):
            cfg_path = os.path.join(path, cfg_file)
            cfg = Config.from_file(cfg_path)
            if overwrite and cfg.name in self._name_dict:
                self.remove_config(cfg.name)
            self.add_config(cfg)
