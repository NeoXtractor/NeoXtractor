"""Treat the base install and the launcher's update folder (Documents\\res ...) as one, newest first.

Replaces related_archives.resource_roots() and related_archive_paths(), so opening a pack and looking up
metadata see both.
"""

from __future__ import annotations

from pathlib import Path

PATCH_FOLDER = "Documents"


def _layers(game: Path) -> list[Path]:
    """Game roots holding res* folders, newest (the Documents patch layer) first."""
    game = Path(game)
    if game.name.lower() == PATCH_FOLDER.lower():
        return [game, game.parent]
    return [game / PATCH_FOLDER, game]


def install() -> None:
    from core.npk import related_archives as ra
    if getattr(ra, "_layered", False):
        return
    original_roots = ra.resource_roots

    def resource_roots(game):
        roots = []
        for layer in _layers(Path(game)):
            if layer.is_dir():
                roots += [root for root in original_roots(layer) if root not in roots]
        return roots or original_roots(game)

    def related_archive_paths(path):
        path = Path(path).resolve()
        location = ra.resource_location(path)
        if not location:
            return [path]
        root, relative = location[0], location[2]          # (game, its res folder, path inside it)
        result = []
        for res in resource_roots(root):
            candidate = (Path(res) / relative).resolve()
            if candidate.is_file() and candidate not in result:
                result.append(candidate)
        if path not in result:
            result.insert(0, path)
        return result

    ra.resource_roots = resource_roots
    ra.related_archive_paths = related_archive_paths
    ra._layered = True
    _raise_scan_limit()


# The character-scan safety limit (core.process_memory) was sized for one install: 768 MiB. Both
# installs together hold ~16,000 more character files, and loading + scanning them alone passes it,
# after which Characters folders stop opening. It is meant to catch a runaway scan, so give it room.
SCAN_LIMIT = 1536 * 1024 * 1024


def _raise_scan_limit() -> None:
    import sys
    from core import process_memory
    if process_memory.SCAN_MEMORY_LIMIT < SCAN_LIMIT:
        process_memory.SCAN_MEMORY_LIMIT = SCAN_LIMIT
    tracker = sys.modules.get("gui.utils.scan_memory")      # the Scene Viewer's allowance builds on top of it
    if tracker is not None and tracker.BASE_LIMIT < SCAN_LIMIT:
        tracker.BASE_LIMIT = SCAN_LIMIT
        process_memory.SCAN_MEMORY_LIMIT = SCAN_LIMIT + tracker.allowance()
