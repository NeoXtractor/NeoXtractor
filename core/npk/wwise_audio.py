"""
Sounds in the main window's file list: loose .wem / .ogg / .wav files join the sound banks' category, which is shown
as "Audio". Playing and converting the sounds is NeoXtractor's own code (core.wem and its audio and bank viewers).
"""

from __future__ import annotations


# -- the Audio category in the main window ---------------------------------------------------------------
AUDIO_EXTENSIONS = ("wem", "ogg", "wav", "bnk")
AUDIO_LABEL = "Audio"


def install_detection() -> None:
    """Loose .wem / .ogg / .wav files share the sound banks' category (shown as "Audio")."""
    import importlib  # pylint: disable=import-outside-toplevel
    from core.npk import detection  # pylint: disable=import-outside-toplevel
    from core.npk.enums import NPKEntryFileCategories  # pylint: disable=import-outside-toplevel
    original = getattr(detection.get_file_category, "_original", detection.get_file_category)

    def get_file_category(extension):
        if str(extension or "").lower().lstrip(".") in AUDIO_EXTENSIONS:
            return NPKEntryFileCategories.BANK
        return original(extension)

    get_file_category._original = original
    for name in ("core.npk.detection", "core.npk.npk_file", "core.npk.related_archives", "core.wpk.wpk_file"):
        try:
            module = importlib.import_module(name)
        except Exception:  # pylint: disable=broad-except
            continue
        if hasattr(module, "get_file_category"):
            module.get_file_category = get_file_category


def install_category(app) -> None:
    """Name the sound bank category "Audio" in each main window's category list."""
    from core.npk.enums import NPKEntryFileCategories  # pylint: disable=import-outside-toplevel
    for window in app.topLevelWidgets():
        combo = getattr(window, "entry_category_filter_combobox", None)
        if combo is None:
            continue
        row = combo.findData(NPKEntryFileCategories.BANK)
        if row < 0:
            row = combo.findText(NPKEntryFileCategories.BANK.value)
        if row >= 0:
            combo.setItemText(row, AUDIO_LABEL)
            combo.setItemData(row, "Sound banks (.bnk) and sounds (.wem, .ogg, .wav)",
                              0x03)  # Qt.ToolTipRole
