"""Provides game config utilities."""

import json
from dataclasses import dataclass, field
from typing import Any

from core.npk.class_types import NPKReadOptions


@dataclass
class Config:
    """Represents a game config."""

    name: str = "Default"
    read_options: NPKReadOptions | None = None
    entry_signature_name_map: dict[str, str] = field(default_factory=dict)
    # Identity V special: merge base/hot IDX, expand banks, and label media
    # with Wwise event names.
    wwise_event_map: bool = False

    def __post_init__(self):
        """Post-initialization to ensure read_options is initialized."""
        if isinstance(self.read_options, dict):
            # Convert dict to NPKReadOptions if it is a dictionary
            self.read_options = NPKReadOptions(**self.read_options)

    @staticmethod
    def from_file(path: str) -> "Config":
        """Creates a config from file."""
        with open(path, "r", encoding="utf-8") as config_file:
            data: dict[str, Any] = json.load(config_file)
            return Config(**data)
