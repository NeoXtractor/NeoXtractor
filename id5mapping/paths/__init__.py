"""Original vfs path restoration for unpacked archives (Wwise excluded)."""

from .path_restore import PathHashIndex, restore_archive_paths

__all__ = ["PathHashIndex", "restore_archive_paths"]
