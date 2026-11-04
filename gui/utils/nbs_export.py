"""Shared handling of the configured NBS export format."""

from __future__ import annotations

from PySide6 import QtWidgets

MKV = "mkv"
IVF = "ivf"
ORIGIN = "origin"


def get_export_target() -> str:
    """Return the configured NBS export format ('origin', 'mkv' or 'ivf')."""
    app = QtWidgets.QApplication.instance()
    manager = app.property("settings_manager") if app is not None else None
    value = manager.get("video.nbs_export", MKV) if manager else MKV
    return value if value in (MKV, IVF, ORIGIN) else MKV
