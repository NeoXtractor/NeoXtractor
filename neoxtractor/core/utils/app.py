"""Shared utility functions for the application."""

import os
import sys

import neoxtractor


def get_application_path():
    """Return the resource root holding ``data/`` and ``configs/``.

    Three launch layouts, one rule: the resource root is the directory
    that actually contains ``data/``, probed in the order bundle ->
    package -> repository.

    * PyInstaller (frozen): the extraction folder ``sys._MEIPASS``.
    * Installed package: the ``neoxtractor/`` package directory itself.
    * Development checkout: the repository root, one level above the
      package directory.

    The result depends only on the on-disk layout, never on how the
    process was invoked.
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return sys._MEIPASS
    package_dir = os.path.dirname(os.path.abspath(neoxtractor.__file__))
    if os.path.isdir(os.path.join(package_dir, "data")):
        return package_dir
    repo_root = os.path.dirname(package_dir)
    if os.path.isdir(os.path.join(repo_root, "data")):
        return repo_root
    raise RuntimeError(
        "cannot locate the NeoXtractor resource root: no 'data' directory "
        f"inside {package_dir} or {repo_root}"
    )


def format_bytes(num_bytes: int) -> str:
    """
    Format bytes into a human-readable string with appropriate unit.

    :param num_bytes: Number of bytes to format
    :return: Formatted string (e.g., "1.23 KB", "45.6 MB")
    """
    if num_bytes < 0:
        return "0 B"

    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    size = float(num_bytes)
    unit_index = 0

    while size >= 1024.0 and unit_index < len(units) - 1:
        size /= 1024.0
        unit_index += 1

    if unit_index == 0:
        return f"{int(size)} {units[unit_index]}"
    else:
        return f"{size:.2f} {units[unit_index]}"
