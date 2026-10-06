"""Migrations module for settings."""

from typing import Callable

from .v1_v2 import migrate as migrate_v1_to_v2

MIGRATION_MAP: dict[int, Callable] = {}

CURRENT_SCHEMA_VERSION = 2


def run_migration(settings_dict: dict) -> bool:
    """
    Run migration for settings from a dictionary.

    Args:
        settings_dict (dict): The dictionary containing settings to migrate.
    """

    migrated = False

    if "schema_version" not in settings_dict:
        # Schema v1
        migrate_v1_to_v2(settings_dict)
        migrated = True
    else:
        current_version = settings_dict["schema_version"]
        while current_version in MIGRATION_MAP:
            MIGRATION_MAP[current_version](settings_dict)
            current_version = settings_dict["schema_version"]
            migrated = True

    return migrated
