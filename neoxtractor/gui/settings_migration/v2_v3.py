"""Migrate settings from schema v2 to v3.

v2 stored one game config's read options as a single flat dict (npk
vocabulary).  v3 keys them by format name so one config carries options for
every container family it uses.  The flat field set is wrapped under
``"npk"`` and the unused ``aes_key`` field is dropped;
``entry_signature_name_map`` is unchanged.
"""

from neoxtractor.core.utils.logger import get_logger


def _is_per_format(read_options: dict) -> bool:
    """True when every value is a field dict (already a v3 shape)."""
    return bool(read_options) and all(
        isinstance(value, dict) for value in read_options.values()
    )


def migrate(settings_dict: dict):
    """Migrate settings from version 2 to version 3.

    Args:
        settings_dict (dict): The dictionary containing settings to migrate.
    """
    if settings_dict.get("schema_version") != 2:
        get_logger().info("Settings is not v2, skipping migration.")
        return

    for game_config in settings_dict.get("gameconfigs", []):
        read_options = game_config.get("read_options")
        if not isinstance(read_options, dict) or not read_options:
            game_config["read_options"] = {}
            continue
        if _is_per_format(read_options):
            continue  # already wrapped (e.g. hand-edited v3)
        wrapped = {k: v for k, v in read_options.items() if k != "aes_key"}
        game_config["read_options"] = {"npk": wrapped}

    settings_dict["schema_version"] = 3
    get_logger().info("Migrated settings from v2 to v3.")
