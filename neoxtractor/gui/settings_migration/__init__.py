"""Migrations module for settings.

Migrations form a chain keyed by the source schema version: each callable
turns version N into version N+1 and stamps the new number, so
:func:`run_migration` walks from the file's version up to the current one in
one pass.  A file with no ``schema_version`` is treated as v1.

A file stamped above :data:`CURRENT_SCHEMA_VERSION` was written by a newer
application; the chain has no step for it and its unknown keys cannot be
honoured, so :func:`run_migration` refuses it with
:class:`SettingsVersionTooNew` rather than silently down-grading it.
"""

from typing import Callable

from .v1_v2 import migrate as migrate_v1_to_v2
from .v2_v3 import migrate as migrate_v2_to_v3

MIGRATION_MAP: dict[int, Callable] = {
    1: migrate_v1_to_v2,
    2: migrate_v2_to_v3,
}

CURRENT_SCHEMA_VERSION = 3


class SettingsVersionTooNew(ValueError):
    """The settings file requires a newer application version."""


def run_migration(settings_dict: dict) -> bool:
    """
    Bring a settings dictionary up to the current schema version.

    Args:
        settings_dict (dict): The dictionary containing settings to migrate.

    Returns:
        bool: True when at least one migration step ran.

    Raises:
        SettingsVersionTooNew: when the file's ``schema_version`` is above
            :data:`CURRENT_SCHEMA_VERSION` -- this build cannot speak it.
    """

    version = settings_dict.setdefault("schema_version", 1)
    if version > CURRENT_SCHEMA_VERSION:
        raise SettingsVersionTooNew(
            f"Settings require schema version {version}; this build supports "
            f"up to {CURRENT_SCHEMA_VERSION}. Update the application."
        )
    migrated = False

    while version in MIGRATION_MAP:
        MIGRATION_MAP[version](settings_dict)
        version = settings_dict["schema_version"]
        migrated = True

    return migrated
