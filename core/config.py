"""Provides game config utilities."""

from dataclasses import dataclass, field
import json
from typing import Any

from core.npk.class_types import NPKReadOptions

@dataclass
class Config:
    """Represents a game config."""

    name: str = "Default"
    read_options: NPKReadOptions | None = None
    entry_signature_name_map: dict[str, str] = field(default_factory=dict)
    character_folder_roots: list[str] = field(
        default_factory=lambda: ["model\\canon", "model\\partner"]
    )

    def __post_init__(self):
        """Post-initialization to ensure read_options is initialized."""
        if isinstance(self.read_options, dict):
            # Convert dict to NPKReadOptions if it is a dictionary
            self.read_options = NPKReadOptions(**self.read_options)
        if self.character_folder_roots is None:
            self.character_folder_roots = []
        self.character_folder_roots = [
            str(root).strip() for root in self.character_folder_roots if str(root).strip()
        ]

    @staticmethod
    def from_file(path: str) -> 'Config':
        """Creates a config from file."""
        with open(path, "r", encoding="utf-8") as config_file:
            data: dict[str, Any] = json.load(config_file)
            return Config(**data)
