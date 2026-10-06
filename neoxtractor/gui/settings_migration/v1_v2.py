from neoxtractor.core.logger import get_logger


def migrate(settings_dict: dict):
    """
    Migrate settings from version 1 to version 2.

    Args:
        settings_dict (dict): The dictionary containing settings to migrate.
    """
    if "schema_version" in settings_dict:
        get_logger().info("Settings is not v1, skipping migration.")
        return

    # Update the schema version to 2
    settings_dict["schema_version"] = 2

    # Migrate gameconfigs to the new format
    for game_config in settings_dict.get("gameconfigs", []):
        decryption_key = game_config["decryption_key"]
        game_config["read_options"] = {"decryption_key": decryption_key}
        del game_config["decryption_key"]

    get_logger().info("Migrated settings from v1 to v2.")
