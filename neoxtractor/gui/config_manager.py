"""Game config manager: a seed layer plus a user-override layer.

Configs come from two places.  The **seed layer** is the ``configs``
directory, re-read every launch so an application update ships corrected or
new game configs automatically.  The **user layer** is what the user adds,
edits, or imports; it persists in ``settings.json`` under ``gameconfigs``.
A user config whose name matches a seed overrides that seed for display and
use; deleting the override lets the seed reappear on the next load (a
natural "revert to default").

The manager keeps the two layers separate internally and exposes one merged
view (``configs``).  Whether a name is a seed or user-only is derived from
the seed layer on demand -- nothing records "which are built in", so a file
the user drops into ``configs`` is simply a seed.
"""

from __future__ import annotations

import os
from typing import Iterable

from neoxtractor.gui.config import Config, ConfigVersionTooNew


class ConfigManager:
    """Holds the seed and user config layers and their merged view."""

    def __init__(self) -> None:
        self._seeds: dict[str, Config] = {}
        self._user: dict[str, Config] = {}

    # --- merged view ---------------------------------------------------------

    @property
    def configs(self) -> list[Config]:
        """The merged list: every seed (user override wins), then user-only."""
        merged: list[Config] = []
        seen: set[str] = set()
        for name, seed in self._seeds.items():
            merged.append(self._user.get(name, seed))
            seen.add(name)
        for name, cfg in self._user.items():
            if name not in seen:
                merged.append(cfg)
        return merged

    def is_seed(self, name: str) -> bool:
        """True when *name* is provided by the seed directory (not deletable)."""
        return name in self._seeds

    def get_config(self, name: str) -> Config | None:
        """The effective config for *name* (user override, else seed)."""
        return self._user.get(name) or self._seeds.get(name)

    def get_config_index(self, cfg: Config | str) -> int | None:
        """Index of a config (by name or instance) in the merged list."""
        name = cfg if isinstance(cfg, str) else cfg.name
        for i, merged in enumerate(self.configs):
            if merged.name == name:
                return i
        return None

    # --- mutation (all land in the user layer) -------------------------------

    def add_config(self, cfg: Config) -> None:
        """Add or override a config in the user layer."""
        self._user[cfg.name] = cfg

    def add_configs(self, cfgs: Iterable[Config]) -> None:
        for cfg in cfgs:
            self.add_config(cfg)

    def update_config(self, index: int, new_cfg: Config) -> None:
        """Replace the config at *index*, writing through the user layer.

        The seed layer is never mutated; an edited seed simply gains a user
        override under the new name.
        """
        merged = self.configs
        if index < 0 or index >= len(merged):
            raise IndexError("Config index out of range.")
        old = merged[index]
        # Drop any user entry that no longer names this slot (rename/delete).
        if old.name in self._user and self._user[old.name] is old:
            del self._user[old.name]
        self._user[new_cfg.name] = new_cfg

    def remove_config(self, cfg: Config | str) -> None:
        """Delete the user entry for a name.  Seeds cannot be removed.

        Removing the override of an edited seed reveals the seed again.
        Raises ``KeyError`` when the name is a seed with no user override,
        or is unknown -- the caller (UI) must not offer this for seeds.
        """
        name = cfg if isinstance(cfg, str) else cfg.name
        if name in self._user:
            del self._user[name]
            return
        if name in self._seeds:
            raise KeyError(f"'{name}' is a built-in config and cannot be deleted.")
        raise KeyError(f"No such config: '{name}'.")

    def clear_user(self) -> None:
        """Drop all user configs (seeds stay; used when reloading settings)."""
        self._user.clear()

    def has_user_override(self, name: str) -> bool:
        """True when the user layer carries an entry for *name*."""
        return name in self._user

    def revert_to_seeds(self) -> int:
        """Drop every user override whose name also exists as a seed.

        Returns the number of overrides reverted.  User-only configs (no
        seed of that name) stay untouched.
        """
        names = [name for name in self._user if name in self._seeds]
        for name in names:
            del self._user[name]
        return len(names)

    def prune_redundant(self) -> bool:
        """Drop user entries identical to their seed.  True if any dropped.

        Removing such an entry changes nothing visible: the seed shows
        through.
        """
        changed = False
        for name in list(self._user):
            seed = self._seeds.get(name)
            if seed is not None and self._user[name].to_dict() == seed.to_dict():
                del self._user[name]
                changed = True
        return changed

    # --- loading -------------------------------------------------------------

    def load_seeds(self, path: str = "configs") -> list[str]:
        """Read the seed directory, replacing the seed layer wholesale.

        Non-JSON files are skipped.  Returns the names that failed to parse,
        so the caller can surface them; a broken seed never drops the others.
        A seed requiring a newer config version is fatal -- it re-raises
        :class:`~neoxtractor.gui.config.ConfigVersionTooNew` so the caller can
        stop startup and tell the user to update.
        """
        self._seeds.clear()
        problems: list[str] = []
        if not os.path.isdir(path):
            return problems
        for cfg_file in sorted(os.listdir(path)):
            if not cfg_file.lower().endswith(".json"):
                continue
            cfg_path = os.path.join(path, cfg_file)
            try:
                cfg = Config.from_file(cfg_path)
            except ConfigVersionTooNew:
                raise
            except Exception as exc:  # one broken seed must not drop the rest
                problems.append(f"{cfg_file}: {exc}")
                continue
            self._seeds[cfg.name] = cfg
        return problems

    def load_user(self, dicts: list[dict]) -> list[str]:
        """Replace the user layer from persisted dicts.  Returns bad entries."""
        self._user.clear()
        problems: list[str] = []
        for data in dicts:
            try:
                cfg = Config.from_dict(data)
            except Exception as exc:
                problems.append(f"{data.get('name', '?')}: {exc}")
                continue
            self._user[cfg.name] = cfg
        return problems

    # --- persistence (user layer only) ---------------------------------------

    def to_user_dicts(self) -> list[dict]:
        """The user-layer configs as JSON-ready dicts (seeds are not saved)."""
        return [cfg.to_dict() for cfg in self._user.values()]
