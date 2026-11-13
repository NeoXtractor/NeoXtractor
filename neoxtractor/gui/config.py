"""Game config domain model.

A ``Config`` bundles the display name, the per-format read options, and the
signature-to-name map.  ``read_options`` is keyed by format name: one game
config can carry options for every container family it uses, so settings for
different families coexist as plain data.  The values
stay JSON-shaped field dicts -- the GUI never imports a format's options
class; the crossing to a concrete ``ReadOptions`` happens at open time
through :meth:`options_for`, which delegates to
:mod:`neoxtractor.core.packages.config_schema`.

Persistence (JSON files and dicts) lives here too.  Each config file
carries its own ``config_version`` (an independent space from the
settings.json ``schema_version``): a missing key reads as version 1, whose
flat ``read_options`` is normalized under ``"npk"``, and a version above
:data:`SUPPORTED_CONFIG_VERSION` refuses to load
(:class:`ConfigVersionTooNew`).  Unknown top-level keys are dropped with a
warning, so files written by older or hand-edited builds still load.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from neoxtractor.core.packages.base import ReadOptions
from neoxtractor.core.packages.config_schema import build_options
from neoxtractor.core.utils.logger import get_logger

SUPPORTED_CONFIG_VERSION = 3

# The fields a config dict may carry; anything else is dropped on load.
_KNOWN_FIELDS = frozenset(
    {"name", "read_options", "entry_signature_name_map", "config_version"}
)


class ConfigVersionTooNew(ValueError):
    """A config file requires a newer application version."""


def _is_per_format(read_options: dict[str, Any]) -> bool:
    """True when every value is a field dict (a format key), not a bare field.

    A legacy flat read_options (``{"decryption_key": ...}``) has scalar/None
    values, so it fails this test and is wrapped under npk -- the only format
    whose v1/v2 vocabulary was ever flat.
    """
    return bool(read_options) and all(
        isinstance(value, dict) for value in read_options.values()
    )


@dataclass
class Config:
    """Represents a game config."""

    name: str = "Default"
    read_options: dict[str, dict[str, Any]] = field(default_factory=dict)
    entry_signature_name_map: dict[str, str] = field(default_factory=dict)
    config_version: int = SUPPORTED_CONFIG_VERSION

    def __post_init__(self) -> None:
        # Accept the legacy flat shape and normalize it to per-format so an
        # old config file or settings entry loads without a crash.  An empty
        # dict is already-per-format (no options for any format), not a
        # legacy npk one, so it stays empty.
        if (
            isinstance(self.read_options, dict)
            and self.read_options
            and not _is_per_format(self.read_options)
        ):
            self.read_options = {"npk": dict(self.read_options)}

    # --- codec ---------------------------------------------------------------

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Config":
        """Build a Config from a JSON-ready dict.

        Lenient by design: unknown keys are dropped (logged), a missing
        ``config_version`` reads as version 1, and a flat ``read_options``
        is normalized by :meth:`__post_init__`.  Raises
        :class:`ConfigVersionTooNew` for a version this build cannot speak.
        The loaded object always carries :data:`SUPPORTED_CONFIG_VERSION`:
        re-saving upgrades in place (one-way).
        """
        version = data.get("config_version", 1)
        try:
            version = int(version)
        except (TypeError, ValueError):
            get_logger().warning(
                "config %r: bad config_version %r, treating as 1",
                data.get("name", "?"),
                version,
            )
            version = 1
        if version > SUPPORTED_CONFIG_VERSION:
            raise ConfigVersionTooNew(
                f"Config '{data.get('name', '?')}' requires config version "
                f"{version}; this build supports up to "
                f"{SUPPORTED_CONFIG_VERSION}. Update the application."
            )

        unknown = set(data) - _KNOWN_FIELDS
        if unknown:
            get_logger().warning(
                "config %r: ignoring unknown keys %s",
                data.get("name", "?"),
                ", ".join(sorted(unknown)),
            )

        return cls(
            name=data.get("name", "Default"),
            read_options=data.get("read_options", {}),
            entry_signature_name_map=data.get("entry_signature_name_map", {}),
            config_version=SUPPORTED_CONFIG_VERSION,
        )

    @classmethod
    def from_file(cls, path: str) -> "Config":
        """Build a Config from a JSON file path."""
        with open(path, "r", encoding="utf-8") as config_file:
            return cls.from_dict(json.load(config_file))

    def to_dict(self) -> dict[str, Any]:
        """A JSON-ready dict, carrying the current ``config_version``."""
        return asdict(self)

    # --- runtime -------------------------------------------------------------

    def options_for(self, fmt: str | None) -> ReadOptions | None:
        """Build the ``ReadOptions`` for one format from this config.

        The format's raw field dict is merged with the signature name map (as
        ``name_map``) so ``entry_signature_name_map`` reaches the decoder.
        ``None`` when the format is unknown (open_file then auto-detects).
        """
        if not fmt:
            return None
        raw = dict(self.read_options.get(fmt, {}))
        if self.entry_signature_name_map:
            raw["name_map"] = dict(self.entry_signature_name_map)
        return build_options(fmt, raw)
