"""Shared handling of the configured Wwise audio export format."""

from __future__ import annotations

import os

from PySide6 import QtWidgets

from core.wem import convert, probe
from core.wem.bnk import BnkContainer

AUDIO_EXTENSIONS = (".wem", ".bnk")
ORIGIN = "origin"


def get_export_target() -> str:
    """Return the configured Wwise audio export format ('origin'/'ogg')."""
    app = QtWidgets.QApplication.instance()
    manager = app.property("settings_manager") if app is not None else None
    value = manager.get("audio.wwise_export", ORIGIN) if manager else ORIGIN
    return value if value == "ogg" else ORIGIN


def is_audio_entry(filename: str) -> bool:
    return filename.lower().endswith(AUDIO_EXTENSIONS)


def _encode_wem(data: bytes, target: str) -> tuple[bytes, str]:
    """Encode one WEM, downgrading to WAV when the codec cannot carry the target."""
    if target == "ogg" and probe(data).codec != "vorbis":
        return convert(data, "wav"), "wav"
    return convert(data, target), target


def build_outputs(
    relative_path: str, data: bytes, target: str
) -> list[tuple[str, bytes]]:
    """Expand a WEM/BNK entry into ``(relative path, content)`` audio files."""
    stem, extension = os.path.splitext(relative_path)
    extension = extension.lower()

    if extension == ".wem":
        content, suffix = _encode_wem(data, target)
        return [(f"{stem}.{suffix}", content)]

    if extension != ".bnk":
        raise ValueError("not a WEM or BNK entry")

    outputs: list[tuple[str, bytes]] = []
    for wem_id, wem in BnkContainer(data).files:
        try:
            content, suffix = _encode_wem(wem, target)
        except ValueError:
            continue
        outputs.append((os.path.join(stem, f"{wem_id}.{suffix}"), content))

    if not outputs:
        raise ValueError("no convertible audio found in bank")
    return outputs
